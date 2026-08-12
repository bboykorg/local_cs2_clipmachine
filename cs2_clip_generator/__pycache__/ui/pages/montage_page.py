"""Montage page: pick clips, order them, add music, render one video."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.errors import AppError
from ...montage.creator import MontageSettings, default_montage_name
from ...utils.filesystem import open_in_file_manager
from .. import theme
from ..state import AppState
from ..widgets.common import Card, ProgressRow, heading, label
from ..widgets.error_dialog import show_error
from ..workers import MontageWorker


class MontagePage(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.worker: MontageWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 18)
        layout.setSpacing(16)
        layout.addWidget(heading("Montage", "Join finished clips into one video. Real files, real FFmpeg."))

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self._clips_card(), 3)
        body.addWidget(self._settings_card(), 2)
        layout.addLayout(body, 1)

        self.progress_card = Card()
        self.progress_row = ProgressRow("Idle")
        self.progress_card.body().addWidget(self.progress_row)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("danger")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        row.addWidget(self.cancel_button)
        self.progress_card.body().addLayout(row)
        self.progress_card.setVisible(False)
        layout.addWidget(self.progress_card)

        state.clips_changed.connect(lambda _: self.refresh())
        self.refresh()

    # -- widgets ---------------------------------------------------------
    def _clips_card(self) -> Card:
        card = Card()
        header = QHBoxLayout()
        header.addWidget(label("Clips", "h3"))
        header.addStretch(1)
        up = QPushButton("↑")
        up.setObjectName("ghost")
        up.setFixedWidth(36)
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("↓")
        down.setObjectName("ghost")
        down.setFixedWidth(36)
        down.clicked.connect(lambda: self._move(1))
        add = QPushButton("Add files…")
        add.setObjectName("ghost")
        add.clicked.connect(self._add_files)
        header.addWidget(up)
        header.addWidget(down)
        header.addWidget(add)
        card.body().addLayout(header)

        self.clip_list = QListWidget()
        self.clip_list.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none; }}"
            f"QListWidget::item {{ padding: 7px; border-bottom: 1px solid {theme.BORDER}; }}"
            f"QListWidget::item:selected {{ background: {theme.ACCENT_SOFT}; }}"
        )
        card.body().addWidget(self.clip_list, 1)
        self.empty_label = label("Render some clips first — they show up here automatically.", "faint", wrap=True)
        card.body().addWidget(self.empty_label)
        return card

    def _settings_card(self) -> Card:
        card = Card()
        card.body().addWidget(label("Montage settings", "h3"))
        grid = QGridLayout()
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(10)

        grid.addWidget(label("Transition", "faint"), 0, 0)
        self.transition_combo = QComboBox()
        self.transition_combo.addItem("None (hard cut)", "none")
        self.transition_combo.addItem("Cross fade", "fade")
        grid.addWidget(self.transition_combo, 0, 1)

        grid.addWidget(label("Fade length", "faint"), 1, 0)
        self.transition_seconds = QDoubleSpinBox()
        self.transition_seconds.setRange(0.1, 3.0)
        self.transition_seconds.setSingleStep(0.1)
        self.transition_seconds.setValue(0.5)
        self.transition_seconds.setSuffix(" s")
        grid.addWidget(self.transition_seconds, 1, 1)

        self.intro_button, self.intro_label = self._file_picker("Intro", grid, 2)
        self.outro_button, self.outro_label = self._file_picker("Outro", grid, 3)
        self.music_button, self.music_label = self._file_picker("Music", grid, 4, audio=True)

        grid.addWidget(label("Music volume", "faint"), 5, 0)
        self.music_volume = QDoubleSpinBox()
        self.music_volume.setRange(0.0, 1.0)
        self.music_volume.setSingleStep(0.05)
        self.music_volume.setValue(0.35)
        grid.addWidget(self.music_volume, 5, 1)

        self.keep_audio = QCheckBox("Keep game audio under the music")
        self.keep_audio.setChecked(True)
        grid.addWidget(self.keep_audio, 6, 0, 1, 2)
        card.body().addLayout(grid)

        card.body().addStretch(1)
        self.create_button = QPushButton("Create Montage")
        self.create_button.setObjectName("primary")
        self.create_button.clicked.connect(self._create)
        card.body().addWidget(self.create_button)

        self.result_label = label("", "muted", wrap=True)
        card.body().addWidget(self.result_label)
        return card

    def _file_picker(self, name: str, grid: QGridLayout, row: int, audio: bool = False) -> tuple[QPushButton, object]:
        grid.addWidget(label(name, "faint"), row, 0)
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        button = QPushButton("Choose…")
        button.setObjectName("ghost")
        value_label = label("None", "muted")
        value_label.setMinimumWidth(80)

        def choose() -> None:
            filters = (
                "Audio (*.mp3 *.wav *.m4a *.aac *.ogg)"
                if audio
                else "Video or image (*.mp4 *.mov *.mkv *.png *.jpg)"
            )
            path, _ = QFileDialog.getOpenFileName(self, f"Select {name.lower()}", str(Path.home()), filters)
            if path:
                value_label.setText(Path(path).name)
                value_label.setProperty("path", path)

        button.clicked.connect(choose)
        clear = QPushButton("×")
        clear.setObjectName("ghost")
        clear.setFixedWidth(28)
        clear.clicked.connect(lambda: (value_label.setText("None"), value_label.setProperty("path", "")))
        layout.addWidget(button)
        layout.addWidget(value_label, 1)
        layout.addWidget(clear)
        grid.addWidget(container, row, 1)
        return button, value_label

    # -- data ------------------------------------------------------------
    def refresh(self) -> None:
        self.clip_list.clear()
        for clip in self.state.clips:
            path = clip.video if Path(clip.video).is_absolute() else ""
            if not path and clip.metadata_path:
                path = str(Path(clip.metadata_path).with_suffix(".mp4"))
            item = QListWidgetItem(
                f"{clip.type}  ·  Round {clip.round}  ·  {clip.player}  ·  {clip.duration_seconds:.0f}s"
            )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.clip_list.addItem(item)
        self.empty_label.setVisible(self.clip_list.count() == 0)
        self.create_button.setEnabled(self.clip_list.count() > 0)

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add clips", self.state.settings.paths.output_dir, "Video (*.mp4 *.mkv *.mov)"
        )
        for path in paths:
            item = QListWidgetItem(Path(path).name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.clip_list.addItem(item)
        self.empty_label.setVisible(self.clip_list.count() == 0)
        self.create_button.setEnabled(self.clip_list.count() > 0)

    def _move(self, delta: int) -> None:
        row = self.clip_list.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < self.clip_list.count()):
            return
        item = self.clip_list.takeItem(row)
        self.clip_list.insertItem(target, item)
        self.clip_list.setCurrentRow(target)

    def selected_paths(self) -> list[str]:
        paths = []
        for index in range(self.clip_list.count()):
            item = self.clip_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                path = str(item.data(Qt.ItemDataRole.UserRole) or "")
                if path:
                    paths.append(path)
        return paths

    # -- creation --------------------------------------------------------
    def _create(self) -> None:
        clips = self.selected_paths()
        if not clips:
            self.state.status("Tick at least one clip for the montage.")
            return
        analysis = self.state.analysis
        name = default_montage_name(
            analysis.match_name if analysis else "match",
            self.state.clips[0].player if self.state.clips else "clips",
        )
        output = str(Path(self.state.settings.paths.output_dir) / name)

        settings = MontageSettings(
            transition=self.transition_combo.currentData(),
            transition_seconds=self.transition_seconds.value(),
            intro_path=str(self.intro_label.property("path") or ""),
            outro_path=str(self.outro_label.property("path") or ""),
            music_path=str(self.music_label.property("path") or ""),
            music_volume=self.music_volume.value(),
            keep_game_audio=self.keep_audio.isChecked(),
            video=self.state.settings.video,
        )

        self.progress_card.setVisible(True)
        self.cancel_button.setEnabled(True)
        self.create_button.setEnabled(False)
        worker = MontageWorker(clips, output, settings, self)
        worker.progress.connect(
            lambda fraction, message: self.progress_row.set_progress(fraction, "Creating montage", message)
        )
        worker.finished_path.connect(self._on_created)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(lambda: self._reset("Montage cancelled"))
        worker.finished.connect(lambda: setattr(self, "worker", None))
        self.worker = worker
        worker.start()

    def _on_created(self, path: str) -> None:
        self._reset("Montage complete")
        self.result_label.setText(f"Saved to {path}")
        self.state.status(f"Montage saved to {path}")
        open_in_file_manager(Path(path).parent)

    def _on_failed(self, error: AppError) -> None:
        self._reset("Failed")
        show_error(error, self, on_open_logs=lambda: open_in_file_manager(self.state.settings.logs_dir))

    def _reset(self, message: str) -> None:
        self.cancel_button.setEnabled(False)
        self.create_button.setEnabled(True)
        self.progress_row.set_progress(1.0, message, "")

    def _cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
