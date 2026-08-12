"""Dashboard: what happened last, and the three things you probably want next."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.detection import detect_all
from ...core.hardware import collect_hardware, disk_report
from ...core.models import MatchAnalysis
from ...highlights.titles import pretty_map_name
from ...utils.timeutil import format_duration
from .. import theme
from ..state import AppState
from ..widgets.common import Card, Metric, heading, label, separator


class DashboardPage(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)
        self.layout_ = QVBoxLayout(container)
        self.layout_.setContentsMargins(28, 26, 28, 26)
        self.layout_.setSpacing(18)

        self.layout_.addWidget(heading("Welcome back", "Drop a demo, pick a player, get MP4 clips."))
        self.layout_.addWidget(self._quick_actions())
        self.match_card = self._match_card()
        self.layout_.addWidget(self.match_card)
        self.layout_.addWidget(self._recent_card())
        self.layout_.addWidget(self._system_card())
        self.layout_.addStretch(1)

        state.analysis_changed.connect(self._on_analysis)
        state.highlights_changed.connect(lambda _: self._on_analysis(state.analysis))

    # -- sections --------------------------------------------------------
    def _quick_actions(self) -> QWidget:
        card = Card()
        card.body().addWidget(label("Quick actions", "h3"))
        row = QHBoxLayout()
        row.setSpacing(10)

        import_button = QPushButton("Import Demo")
        import_button.setObjectName("primary")
        import_button.clicked.connect(lambda: self.state.navigate.emit("demo"))

        url_button = QPushButton("Paste URL")
        url_button.clicked.connect(lambda: self.state.navigate.emit("demo"))

        auto_button = QPushButton("Auto Clip")
        auto_button.setToolTip("Pick the best highlights of the loaded demo and render them")
        auto_button.clicked.connect(lambda: self.state.navigate.emit("highlights"))

        folder_button = QPushButton("Open Clips Folder")
        folder_button.setObjectName("ghost")
        folder_button.clicked.connect(self._open_output)

        for button in (import_button, url_button, auto_button, folder_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(button)
        row.addStretch(1)
        card.body().addLayout(row)
        return card

    def _match_card(self) -> Card:
        card = Card(strong=True)
        card.body().addWidget(label("Last match", "faint"))
        self.match_title = label("No demo loaded yet", "h2")
        card.body().addWidget(self.match_title)
        self.match_subtitle = label("Import a demo to see the match overview here.", "muted", wrap=True)
        card.body().addWidget(self.match_subtitle)

        self.metrics_row = QHBoxLayout()
        self.metrics_row.setSpacing(28)
        self.metric_rounds = Metric("—", "ROUNDS")
        self.metric_duration = Metric("—", "DURATION")
        self.metric_kills = Metric("—", "KILLS")
        self.metric_highlights = Metric("—", "HIGHLIGHTS", theme.ACCENT)
        self.metric_clips = Metric("—", "CLIPS", theme.SUCCESS)
        for metric in (
            self.metric_rounds,
            self.metric_duration,
            self.metric_kills,
            self.metric_highlights,
            self.metric_clips,
        ):
            self.metrics_row.addWidget(metric)
        self.metrics_row.addStretch(1)
        card.body().addLayout(self.metrics_row)

        buttons = QHBoxLayout()
        self.open_highlights = QPushButton("Open highlights")
        self.open_highlights.setObjectName("primary")
        self.open_highlights.setEnabled(False)
        self.open_highlights.clicked.connect(lambda: self.state.navigate.emit("highlights"))
        buttons.addWidget(self.open_highlights)
        buttons.addStretch(1)
        card.body().addLayout(buttons)
        return card

    def _recent_card(self) -> Card:
        card = Card()
        card.body().addWidget(label("Recent demos", "h3"))
        self.recent_container = QVBoxLayout()
        self.recent_container.setSpacing(6)
        card.body().addLayout(self.recent_container)
        self._refresh_recent()
        return card

    def _system_card(self) -> Card:
        card = Card()
        header = QHBoxLayout()
        header.addWidget(label("System", "h3"))
        header.addStretch(1)
        refresh = QPushButton("Re-detect")
        refresh.setObjectName("ghost")
        refresh.clicked.connect(self._redetect)
        header.addWidget(refresh)
        card.body().addLayout(header)

        self.system_grid = QGridLayout()
        self.system_grid.setHorizontalSpacing(28)
        self.system_grid.setVerticalSpacing(6)
        card.body().addLayout(self.system_grid)
        card.body().addWidget(separator())
        self.detection_column = QVBoxLayout()
        self.detection_column.setSpacing(4)
        card.body().addLayout(self.detection_column)
        self._refresh_system()
        return card

    # -- updates ---------------------------------------------------------
    def _on_analysis(self, analysis: MatchAnalysis | None) -> None:
        if analysis is None:
            return
        self.match_title.setText(pretty_map_name(analysis.map_name))
        self.match_subtitle.setText(Path(analysis.demo_path).name)
        self.metric_rounds.set_value(str(len(analysis.rounds)))
        self.metric_duration.set_value(format_duration(analysis.duration_seconds))
        self.metric_kills.set_value(str(len(analysis.kills)))
        self.metric_highlights.set_value(str(len(self.state.highlights)))
        self.metric_clips.set_value(str(len(self.state.clips)))
        self.open_highlights.setEnabled(bool(self.state.highlights))
        self._refresh_recent()

    def _refresh_recent(self) -> None:
        while self.recent_container.count():
            item = self.recent_container.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        recent = self.state.settings.ui.recent_demos
        if not recent:
            self.recent_container.addWidget(label("Nothing yet — imported demos appear here.", "faint"))
            return
        for path in recent[:5]:
            row = QHBoxLayout()
            row.addWidget(label(Path(path).name, "muted"))
            row.addStretch(1)
            open_button = QPushButton("Open")
            open_button.setObjectName("ghost")
            open_button.setEnabled(Path(path).is_file())
            open_button.clicked.connect(lambda _=False, p=path: self.state.navigate.emit(f"demo:{p}"))
            row.addWidget(open_button)
            wrapper = QWidget()
            wrapper.setLayout(row)
            self.recent_container.addWidget(wrapper)

    def _refresh_system(self) -> None:
        while self.system_grid.count():
            item = self.system_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        hardware = collect_hardware()
        rows = [
            ("CPU", f"{hardware.cpu} · {hardware.cores} threads"),
            ("GPU", hardware.gpu),
            ("RAM", f"{hardware.ram_available_gb:.1f} / {hardware.ram_total_gb:.1f} GB free"),
        ]
        for path, free_mb, total_mb in disk_report(
            [self.state.settings.paths.output_dir, self.state.settings.paths.temp_dir]
        ):
            rows.append(("Disk", f"{free_mb / 1024:.1f} GB free of {total_mb / 1024:.0f} GB — {path}"))
        for index, (key, value) in enumerate(rows):
            self.system_grid.addWidget(label(key, "faint"), index, 0)
            self.system_grid.addWidget(label(value, "muted"), index, 1)

        while self.detection_column.count():
            item = self.detection_column.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        report = detect_all(self.state.settings, apply=False)
        for result in report.results:
            line = label(result.line, "muted" if result.found else "faint", wrap=True)
            if not result.found:
                line.setStyleSheet(f"color: {theme.WARNING};")
            self.detection_column.addWidget(line)

    def _redetect(self) -> None:
        detect_all(self.state.settings, apply=True)
        self.state.save_settings()
        self._refresh_system()
        self.state.status("Re-detected Steam, CS2, FFmpeg, OBS and HLAE")

    def _open_output(self) -> None:
        from ...utils.filesystem import open_in_file_manager

        directory = Path(self.state.settings.paths.output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        open_in_file_manager(directory)
