"""Render page: the queue, its controls, and what CS2 is doing right now."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.errors import AppError
from ...core.models import Highlight, JobState, RenderJob
from ...render.job import state_color, state_label, summarise
from ...render.pipeline import PipelineReport, build_jobs
from ...render.queue import RenderQueue
from ...utils.filesystem import open_in_file_manager
from .. import theme
from ..state import AppState
from ..widgets.common import Badge, Card, ProgressRow, heading, label
from ..widgets.error_dialog import show_error
from ..workers import PreviewWorker, RenderWorker


class JobRow(Card):
    """One queued clip: state, progress, and the actions that apply to it."""

    def __init__(self, index: int, job: RenderJob, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=14)
        self.job = job
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(label(f"{index}.", "muted"))
        top.addWidget(Badge(job.highlight.kind.value, theme.kind_color(job.highlight.kind.value)))
        top.addWidget(label(job.highlight.title or job.label, "h3", wrap=True), 1)
        self.state_badge = Badge(state_label(job), state_color(job))
        top.addWidget(self.state_badge)
        self.body().addLayout(top)

        self.progress = ProgressRow("")
        self.progress.title_label.setVisible(False)
        self.body().addWidget(self.progress)

        self.detail = label(f"{job.highlight.player_name} · Round {job.highlight.round_number}", "faint", wrap=True)
        self.body().addWidget(self.detail)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.open_button = QPushButton("Open folder")
        self.open_button.setObjectName("ghost")
        self.open_button.setVisible(False)
        self.open_button.clicked.connect(lambda: open_in_file_manager(Path(self.job.output_path).parent))
        actions.addWidget(self.open_button)
        self.body().addLayout(actions)

    def update_view(self) -> None:
        self.state_badge.setText(state_label(self.job))
        self.state_badge.set_color(state_color(self.job))
        self.progress.set_progress(self.job.progress, detail=self.job.message)
        if self.job.state == JobState.DONE:
            self.open_button.setVisible(True)
            self.detail.setText(Path(self.job.output_path).name)
        elif self.job.state == JobState.FAILED and self.job.error:
            self.detail.setText(self.job.error.splitlines()[0])
            self.detail.setStyleSheet(f"color: {theme.DANGER};")


class RenderPage(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.jobs: list[RenderJob] = []
        self.rows: list[JobRow] = []
        self.worker: RenderWorker | None = None
        self.preview_worker: PreviewWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 18)
        layout.setSpacing(16)

        header = QHBoxLayout()
        header.addWidget(
            heading("Render queue", "One CS2 recording at a time — the game cannot be in two places at once.")
        )
        header.addStretch(1)
        self.start_button = QPushButton("Start rendering")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start)
        header.addWidget(self.start_button)
        layout.addLayout(header)

        layout.addWidget(self._controls_card())

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.rows_container = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(10)
        self.rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.rows_container)
        layout.addWidget(self.scroll, 1)

        self.empty_label = label("The queue is empty. Pick highlights and press Generate or AUTO CLIP.", "muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label)

        self.developer_card = self._developer_card()
        layout.addWidget(self.developer_card)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh_rows)
        self._timer.start()

    # -- widgets ---------------------------------------------------------
    def _controls_card(self) -> Card:
        card = Card()
        row = QHBoxLayout()
        row.setSpacing(8)
        self.summary_label = label("Nothing queued", "h3")
        row.addWidget(self.summary_label, 1)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("danger")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)

        self.skip_button = QPushButton("Skip")
        self.skip_button.setObjectName("ghost")
        self.skip_button.setEnabled(False)
        self.skip_button.clicked.connect(self.skip)

        self.retry_button = QPushButton("Retry failed")
        self.retry_button.setObjectName("ghost")
        self.retry_button.clicked.connect(self.retry_failed)

        self.clear_button = QPushButton("Clear")
        self.clear_button.setObjectName("ghost")
        self.clear_button.clicked.connect(self.clear)

        for button in (self.skip_button, self.cancel_button, self.retry_button, self.clear_button):
            row.addWidget(button)
        card.body().addLayout(row)

        self.notes_column = QVBoxLayout()
        self.notes_column.setSpacing(2)
        card.body().addLayout(self.notes_column)
        return card

    def _developer_card(self) -> Card:
        card = Card()
        card.body().addWidget(label("Developer mode", "h3"))
        self.developer_text = label("", "mono", wrap=True)
        card.body().addWidget(self.developer_text)
        card.setVisible(self.state.settings.ui.developer_mode)
        return card

    # -- queue management ------------------------------------------------
    def queue_highlights(self, highlights: Sequence[Highlight]) -> None:
        analysis = self.state.analysis
        if analysis is None or not highlights:
            return
        new_jobs = build_jobs(highlights, analysis, self.state.settings)
        known = {job.id for job in self.jobs}
        self.jobs += [job for job in new_jobs if job.id not in known]
        self._rebuild_rows()
        self.state.status(f"{len(self.jobs)} clips queued")

    def restore_jobs(self, jobs: Sequence[RenderJob]) -> None:
        self.jobs = list(jobs)
        self._rebuild_rows()

    def clear(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self.jobs = []
        self._rebuild_rows()
        RenderQueue.clear_journal(self.state.settings)

    def retry_failed(self) -> None:
        for job in self.jobs:
            if job.state in (JobState.FAILED, JobState.CANCELLED, JobState.SKIPPED):
                job.state = JobState.PENDING
                job.progress = 0.0
                job.error = ""
                job.message = "Queued for retry"
        self._refresh_rows()

    def _rebuild_rows(self) -> None:
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows.clear()
        for index, job in enumerate(self.jobs, start=1):
            row = JobRow(index, job)
            self.rows_layout.addWidget(row)
            self.rows.append(row)
        self.empty_label.setVisible(not self.jobs)
        self._refresh_rows()

    def _refresh_rows(self) -> None:
        for row in self.rows:
            row.update_view()
        self.summary_label.setText(summarise(self.jobs) if self.jobs else "Nothing queued")
        running = self.worker is not None and self.worker.isRunning()
        self.start_button.setEnabled(bool(self.jobs) and not running)
        self.cancel_button.setEnabled(running)
        self.skip_button.setEnabled(running)
        self.clear_button.setEnabled(not running)
        self.developer_card.setVisible(self.state.settings.ui.developer_mode)
        if self.state.settings.ui.developer_mode:
            self._update_developer_text()

    def _update_developer_text(self) -> None:
        from ...cs2.launcher import CS2Launcher
        from ...render.pipeline import describe_environment

        analysis = self.state.analysis
        current = next((job for job in self.jobs if job.state == JobState.RUNNING), None)
        environment = describe_environment(self.state.settings)
        lines = [
            f"CS2 process running: {CS2Launcher.is_running()}",
            f"Steam running: {CS2Launcher.is_steam_running()}",
            f"CS2 executable: {environment['cs2_executable'] or '—'}",
            f"FFmpeg: {environment['ffmpeg'] or '—'}",
            f"Tickrate: {analysis.tickrate if analysis else '—'}",
        ]
        if current is not None:
            highlight = current.highlight
            player = analysis.player(highlight.player_steamid) if analysis else None
            lines += [
                f"Current job: {current.label}",
                f"Ticks: {highlight.start_tick} → {highlight.end_tick}",
                f"POV slot: {player.slot if player else '—'} ({highlight.player_name})",
                f"Recorder state: {current.message}",
                f"Output: {current.output_path}",
            ]
        self.developer_text.setText("\n".join(lines))

    # -- execution -------------------------------------------------------
    def start(self) -> None:
        pending = [job for job in self.jobs if job.state == JobState.PENDING]
        analysis = self.state.analysis
        if not pending or analysis is None:
            return
        RenderQueue(self.state.settings, analysis).add(self.jobs)  # journal for crash recovery

        worker = RenderWorker(self.state.settings, analysis, pending, self)
        worker.job_progress.connect(self._on_job_progress)
        worker.job_finished.connect(lambda _job: self._refresh_rows())
        worker.finished_report.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(lambda: self.state.status("Rendering cancelled"))
        worker.finished.connect(self._on_worker_done)
        self.worker = worker
        worker.start()
        self.state.status(f"Rendering {len(pending)} clips — CS2 will open and play the demo")
        self._refresh_rows()

    def cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.state.status("Cancelling after the current step…")

    def skip(self) -> None:
        """Cancelling the current clip while leaving the rest of the queue."""
        if self.worker is not None:
            self.worker.cancel()
            self.state.status("Skipping the current clip…")

    def _on_job_progress(self, job: RenderJob, fraction: float, message: str) -> None:
        del job, fraction, message
        self._refresh_rows()

    def _on_finished(self, report: PipelineReport) -> None:
        self.state.add_clips(report.clips)
        while self.notes_column.count():
            item = self.notes_column.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for note in report.notes:
            self.notes_column.addWidget(label(f"· {note}", "faint", wrap=True))
        if report.clips:
            self.state.status(f"{len(report.clips)} clips written to {self.state.settings.paths.output_dir}")
        if report.failed:
            self.state.status(f"{len(report.failed)} clips failed — see the queue for details")
        RenderQueue.clear_journal(self.state.settings)

    def _on_worker_done(self) -> None:
        self.worker = None
        self._refresh_rows()

    def _on_failed(self, error: AppError) -> None:
        show_error(
            error,
            self,
            on_open_settings=lambda: self.state.navigate.emit("settings"),
            on_open_logs=lambda: open_in_file_manager(self.state.settings.logs_dir),
            on_retry=self.start,
        )

    # -- preview ---------------------------------------------------------
    def preview(self, highlight: Highlight) -> None:
        analysis = self.state.analysis
        if analysis is None:
            return
        if self.worker is not None and self.worker.isRunning():
            self.state.status("Cannot preview while rendering — CS2 is busy.")
            return
        worker = PreviewWorker(self.state.settings, analysis, highlight, self)
        worker.started_preview.connect(lambda: self.state.status("CS2 is opening the demo at this highlight…"))
        worker.failed.connect(self._on_failed)
        worker.finished.connect(lambda: setattr(self, "preview_worker", None))
        self.preview_worker = worker
        worker.start()
