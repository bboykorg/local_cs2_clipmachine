"""Demo page: import, download, analyse — and show what the match contained."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.errors import AppError
from ...core.models import MatchAnalysis, Team
from ...demo.extractor import is_archive, list_demos_in_zip
from ...demo.validation import validate_demo
from ...highlights.titles import pretty_map_name
from ...utils.timeutil import format_bytes, format_duration, format_eta, format_speed
from .. import theme
from ..state import AppState
from ..widgets.common import Card, DropZone, Metric, ProgressRow, heading, label, separator
from ..widgets.error_dialog import show_error
from ..workers import AnalysisWorker, DownloadWorker


class DemoPage(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.download_worker: DownloadWorker | None = None
        self.analysis_worker: AnalysisWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        container = QWidget()
        scroll.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(18)

        layout.addWidget(heading("Demo", "Local file, archive or direct link — the app does the rest."))

        self.drop_zone = DropZone()
        self.drop_zone.file_dropped.connect(self.load_demo)
        self.drop_zone.browse_requested.connect(self._browse)
        layout.addWidget(self.drop_zone)

        layout.addWidget(self._url_card())

        self.progress_card = Card()
        self.progress_row = ProgressRow("Idle")
        self.progress_card.body().addWidget(self.progress_row)
        cancel_row = QHBoxLayout()
        cancel_row.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("danger")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        cancel_row.addWidget(self.cancel_button)
        self.progress_card.body().addLayout(cancel_row)
        self.progress_card.setVisible(False)
        layout.addWidget(self.progress_card)

        self.validation_card = Card()
        self.validation_column = QVBoxLayout()
        self.validation_card.body().addWidget(label("Demo check", "h3"))
        self.validation_card.body().addLayout(self.validation_column)
        self.validation_card.setVisible(False)
        layout.addWidget(self.validation_card)

        self.overview_card = self._overview_card()
        self.overview_card.setVisible(False)
        layout.addWidget(self.overview_card)
        layout.addStretch(1)

        state.analysis_changed.connect(self._show_overview)

    # -- widgets ---------------------------------------------------------
    def _url_card(self) -> Card:
        card = Card()
        card.body().addWidget(label("Download from a URL", "h3"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com/match.dem")
        self.url_input.returnPressed.connect(self._download)
        download = QPushButton("Download")
        download.setObjectName("primary")
        download.clicked.connect(self._download)
        row.addWidget(self.url_input, 1)
        row.addWidget(download)
        card.body().addLayout(row)
        card.body().addWidget(
            label("Only the demo file is downloaded, and it is never executed — it is treated as data.", "faint")
        )
        return card

    def _overview_card(self) -> Card:
        card = Card(strong=True)
        self.map_label = label("", "h1")
        card.body().addWidget(self.map_label)
        self.map_subtitle = label("", "muted")
        card.body().addWidget(self.map_subtitle)

        metrics = QHBoxLayout()
        metrics.setSpacing(28)
        self.metric_rounds = Metric("—", "ROUNDS")
        self.metric_duration = Metric("—", "DURATION")
        self.metric_tickrate = Metric("—", "TICKRATE")
        self.metric_kills = Metric("—", "KILLS")
        self.metric_highlights = Metric("—", "HIGHLIGHTS", theme.ACCENT)
        for metric in (
            self.metric_rounds,
            self.metric_duration,
            self.metric_tickrate,
            self.metric_kills,
            self.metric_highlights,
        ):
            metrics.addWidget(metric)
        metrics.addStretch(1)
        card.body().addLayout(metrics)
        card.body().addWidget(separator())

        self.teams_grid = QGridLayout()
        self.teams_grid.setHorizontalSpacing(24)
        self.teams_grid.setVerticalSpacing(4)
        card.body().addLayout(self.teams_grid)

        self.warnings_column = QVBoxLayout()
        card.body().addLayout(self.warnings_column)

        actions = QHBoxLayout()
        open_highlights = QPushButton("Go to highlights")
        open_highlights.setObjectName("primary")
        open_highlights.clicked.connect(lambda: self.state.navigate.emit("highlights"))
        export = QPushButton("Export analysis")
        export.setObjectName("ghost")
        export.clicked.connect(self._export)
        actions.addWidget(open_highlights)
        actions.addWidget(export)
        actions.addStretch(1)
        card.body().addLayout(actions)
        return card

    # -- import ----------------------------------------------------------
    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select a CS2 demo",
            self.state.settings.ui.last_demo or str(Path.home()),
            "CS2 demos (*.dem *.dem.bz2 *.dem.gz *.dem.zip *.bz2 *.gz *.zip);;All files (*)",
        )
        if path:
            self.load_demo(path)

    def _download(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            return
        self._set_busy(True, "Downloading")
        worker = DownloadWorker(url, self.state.settings.paths.temp_dir, self)
        worker.progress.connect(self._on_download_progress)
        worker.finished_path.connect(self._on_downloaded)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(lambda: self._set_busy(False, "Download cancelled"))
        worker.finished.connect(lambda: setattr(self, "download_worker", None))
        self.download_worker = worker
        worker.start()

    def _on_download_progress(self, update) -> None:  # noqa: ANN001 - DownloadProgress
        total = f" of {format_bytes(update.total)}" if update.total else ""
        detail = (
            f"{format_bytes(update.downloaded)}{total} · {format_speed(update.speed_bps)}"
            f" · ETA {format_eta(update.eta_seconds)}"
        )
        self.progress_row.set_progress(update.fraction, "Downloading demo", detail)

    def _on_downloaded(self, path: str) -> None:
        self.state.status(f"Downloaded {Path(path).name}")
        self.load_demo(path)

    def load_demo(self, path: str) -> None:
        """Validate, then analyse — the single entry point for every source."""
        target = Path(path)
        member: str | None = None
        if is_archive(target) and target.suffix.lower() == ".zip":
            try:
                entries = list_demos_in_zip(target)
            except AppError as error:
                show_error(error, self)
                return
            if len(entries) > 1:
                choice, ok = QInputDialog.getItem(
                    self,
                    "Several demos found",
                    "This archive contains more than one demo. Which one?",
                    [entry.name for entry in entries],
                    0,
                    False,
                )
                if not ok:
                    return
                member = choice

        if not is_archive(target):
            result = validate_demo(target)
            self._show_validation(result)
            if not result.ok:
                return

        self._set_busy(True, "Analyzing demo")
        worker = AnalysisWorker(str(target), self.state.settings, member=member, parent=self)
        worker.progress.connect(
            lambda fraction, message: self.progress_row.set_progress(fraction, "Analyzing demo", message)
        )
        worker.finished_analysis.connect(self._on_analysed)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(lambda: self._set_busy(False, "Analysis cancelled"))
        worker.finished.connect(lambda: setattr(self, "analysis_worker", None))
        self.analysis_worker = worker
        worker.start()

    def _on_analysed(self, analysis: MatchAnalysis, highlights: list) -> None:
        self._set_busy(False, f"{len(highlights)} highlights found")
        self.state.set_analysis(analysis, highlights)
        self.state.load_existing_clips()
        self.state.status(
            f"{pretty_map_name(analysis.map_name)} · {len(analysis.rounds)} rounds · {len(highlights)} highlights"
        )

    # -- state -----------------------------------------------------------
    def _set_busy(self, busy: bool, title: str = "") -> None:
        self.progress_card.setVisible(busy or bool(title))
        self.cancel_button.setEnabled(busy)
        if busy:
            self.progress_row.set_progress(0.0, title, "")
        elif title:
            self.progress_row.set_progress(1.0, title, "")

    def _cancel(self) -> None:
        for worker in (self.download_worker, self.analysis_worker):
            if worker is not None and worker.isRunning():
                worker.cancel()
        self.state.status("Cancelling…")

    def _on_failed(self, error: AppError) -> None:
        self._set_busy(False, "Failed")
        show_error(
            error,
            self,
            on_open_settings=lambda: self.state.navigate.emit("settings"),
            on_open_logs=self._open_logs,
        )

    def _open_logs(self) -> None:
        from ...utils.filesystem import open_in_file_manager

        open_in_file_manager(self.state.settings.logs_dir)

    def _show_validation(self, result) -> None:  # noqa: ANN001 - ValidationResult
        while self.validation_column.count():
            item = self.validation_column.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for line in result.as_lines():
            row = label(line, "muted", wrap=True)
            if line.startswith("⚠"):
                row.setStyleSheet(f"color: {theme.WARNING};")
            self.validation_column.addWidget(row)
        self.validation_card.setVisible(True)

    # -- overview --------------------------------------------------------
    def _show_overview(self, analysis: MatchAnalysis | None) -> None:
        if analysis is None:
            return
        self.overview_card.setVisible(True)
        self.map_label.setText(pretty_map_name(analysis.map_name).upper())
        self.map_subtitle.setText(
            f"{Path(analysis.demo_path).name} · {analysis.server_name or 'unknown server'} · "
            f"{analysis.parser_name} {analysis.parser_version}"
        )
        self.metric_rounds.set_value(str(len(analysis.rounds)))
        self.metric_duration.set_value(format_duration(analysis.duration_seconds))
        self.metric_tickrate.set_value(f"{analysis.tickrate:g}")
        self.metric_kills.set_value(str(len(analysis.kills)))
        self.metric_highlights.set_value(str(len(self.state.highlights)))

        while self.teams_grid.count():
            item = self.teams_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        headers = ["Player", "K", "D", "K/D", "HS%", "ADR", "2K", "3K", "4K", "ACE", "CL"]
        row_index = 0
        for team in (Team.T, Team.CT):
            members = [player for player in analysis.players if player.team == team]
            if not members:
                continue
            title = label(team.label, "h3")
            title.setStyleSheet(f"color: {theme.WARNING if team == Team.T else theme.ACCENT};")
            self.teams_grid.addWidget(title, row_index, 0, 1, len(headers))
            row_index += 1
            for column, header in enumerate(headers):
                self.teams_grid.addWidget(label(header, "faint"), row_index, column)
            row_index += 1
            for player in members:
                stats = analysis.stats.get(player.steamid)
                if stats is None:
                    continue
                values = [
                    player.name,
                    str(stats.kills),
                    str(stats.deaths),
                    f"{stats.kd:.2f}",
                    f"{stats.headshot_percentage:.0f}%",
                    f"{stats.adr:.0f}" if stats.adr else "—",
                    str(stats.multi_2k),
                    str(stats.multi_3k),
                    str(stats.multi_4k),
                    str(stats.aces),
                    str(stats.clutches),
                ]
                for column, value in enumerate(values):
                    self.teams_grid.addWidget(label(value, "muted" if column else "h3"), row_index, column)
                row_index += 1

        while self.warnings_column.count():
            item = self.warnings_column.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for warning in analysis.warnings:
            row = label(f"⚠ {warning}", "faint", wrap=True)
            row.setStyleSheet(f"color: {theme.WARNING};")
            self.warnings_column.addWidget(row)

    def _export(self) -> None:
        analysis = self.state.analysis
        if analysis is None:
            return
        directory = QFileDialog.getExistingDirectory(self, "Export analysis to…", self.state.settings.paths.output_dir)
        if not directory:
            return
        from ...highlights.filters import highlights_to_json, kills_to_csv
        from ...utils.filesystem import write_json

        base = Path(directory)
        write_json(base / "analysis.json", analysis.to_dict())
        highlights_to_json(self.state.highlights, base / "highlights.json")
        kills_to_csv(self.state.highlights, base / "kills.csv")
        self.state.status(f"Exported analysis.json, highlights.json and kills.csv to {base}")


class DemoChoiceDialog(QDialog):
    """Kept for archives with many demos when a richer picker is wanted."""

    def __init__(self, names: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose a demo")
        self.selected: str | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(label("This archive contains several demos:", "muted"))
        for name in names:
            button = QPushButton(name)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, value=name: self._choose(value))
            layout.addWidget(button)

    def _choose(self, name: str) -> None:
        self.selected = name
        self.accept()
