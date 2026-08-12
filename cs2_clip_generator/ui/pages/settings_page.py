"""Settings: paths, recorder, playback backend, clip timing, video, scoring."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.detection import DOWNLOAD_PAGES, detect_all
from ...cs2 import plugin as plugin_module
from ...cs2.controller import describe_backends
from ...cs2.launcher import find_cs2_executable
from ...demo.cache import AnalysisCache
from ...recording.factory import LABELS as RECORDER_LABELS
from ...recording.factory import describe_recorders
from ...utils.filesystem import open_in_file_manager
from ...video.ffmpeg import ENCODER_LABELS, FFmpeg
from .. import theme
from ..state import AppState
from ..widgets.common import Card, heading, label, separator


class SettingsPage(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.settings = state.settings

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        container = QWidget()
        scroll.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(16)

        layout.addWidget(heading("Settings", "Everything the pipeline needs, in one place."))
        layout.addWidget(self._paths_card())
        layout.addWidget(self._recorder_card())
        layout.addWidget(self._clips_card())
        layout.addWidget(self._video_card())
        layout.addWidget(self._maintenance_card())
        layout.addStretch(1)

    # -- helpers ---------------------------------------------------------
    def _path_row(self, grid: QGridLayout, row: int, title: str, value: str, directory: bool = False) -> QLineEdit:
        grid.addWidget(label(title, "faint"), row, 0)
        field = QLineEdit(value)
        grid.addWidget(field, row, 1)
        browse = QPushButton("Browse…")
        browse.setObjectName("ghost")

        def choose() -> None:
            if directory:
                path = QFileDialog.getExistingDirectory(self, f"Select {title}", field.text() or str(Path.home()))
            else:
                path, _ = QFileDialog.getOpenFileName(self, f"Select {title}", field.text() or str(Path.home()))
            if path:
                field.setText(path)
                self._save()

        browse.clicked.connect(choose)
        field.editingFinished.connect(self._save)
        grid.addWidget(browse, row, 2)
        return field

    # -- sections --------------------------------------------------------
    def _paths_card(self) -> Card:
        card = Card()
        header = QHBoxLayout()
        header.addWidget(label("Paths", "h3"))
        header.addStretch(1)
        detect = QPushButton("Auto-detect")
        detect.setObjectName("ghost")
        detect.clicked.connect(self._auto_detect)
        header.addWidget(detect)
        card.body().addLayout(header)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        paths = self.settings.paths
        self.cs2_field = self._path_row(grid, 0, "CS2 executable", paths.cs2_executable)
        self.steam_field = self._path_row(grid, 1, "Steam folder", paths.steam_path, directory=True)
        self.ffmpeg_field = self._path_row(grid, 2, "FFmpeg", paths.ffmpeg_executable)
        self.obs_field = self._path_row(grid, 3, "OBS Studio", paths.obs_executable)
        self.hlae_field = self._path_row(grid, 4, "HLAE", paths.hlae_executable)
        self.output_field = self._path_row(grid, 5, "Output folder", paths.output_dir, directory=True)
        self.temp_field = self._path_row(grid, 6, "Temporary folder", paths.temp_dir, directory=True)
        card.body().addLayout(grid)

        self.detection_label = label("", "faint", wrap=True)
        card.body().addWidget(self.detection_label)
        self._refresh_detection()

        links = QHBoxLayout()
        for name, url in (
            ("Get FFmpeg", DOWNLOAD_PAGES["ffmpeg"]),
            ("Get OBS", DOWNLOAD_PAGES["obs"]),
            ("Get HLAE", DOWNLOAD_PAGES["hlae"]),
        ):
            button = QPushButton(name)
            button.setObjectName("ghost")
            button.clicked.connect(lambda _=False, target=url: self._open_url(target))
            links.addWidget(button)
        links.addStretch(1)
        card.body().addLayout(links)
        card.body().addWidget(
            label(
                "Nothing is downloaded automatically — these buttons open the official pages in your browser.",
                "faint",
            )
        )
        return card

    def _recorder_card(self) -> Card:
        card = Card()
        card.body().addWidget(label("Recording", "h3"))
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        recording = self.settings.recording

        grid.addWidget(label("Recorder", "faint"), 0, 0)
        self.recorder_combo = QComboBox()
        self.recorder_combo.addItem(RECORDER_LABELS["auto"], "auto")
        for name in ("hlae", "obs", "native", "ffmpeg"):
            self.recorder_combo.addItem(RECORDER_LABELS[name], name)
        self._select(self.recorder_combo, recording.backend)
        self.recorder_combo.currentIndexChanged.connect(self._save)
        grid.addWidget(self.recorder_combo, 0, 1)

        grid.addWidget(label("CS2 control", "faint"), 1, 0)
        self.playback_combo = QComboBox()
        self.playback_combo.addItem("Auto (best available)", "auto")
        self.playback_combo.addItem("Server plugin (tick accurate)", "plugin")
        self.playback_combo.addItem("Remote console (-netconport)", "netcon")
        self.playback_combo.addItem("Config file + hotkey (fallback)", "cfg")
        self._select(self.playback_combo, recording.playback_backend)
        self.playback_combo.currentIndexChanged.connect(self._save)
        grid.addWidget(self.playback_combo, 1, 1)

        grid.addWidget(label("OBS WebSocket", "faint"), 2, 0)
        obs_row = QHBoxLayout()
        self.obs_host = QLineEdit(recording.obs_host)
        self.obs_host.setPlaceholderText("localhost")
        self.obs_port = QSpinBox()
        self.obs_port.setRange(1, 65535)
        self.obs_port.setValue(recording.obs_port)
        self.obs_password = QLineEdit(recording.obs_password)
        self.obs_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.obs_password.setPlaceholderText("password")
        self.obs_scene = QLineEdit(recording.obs_scene)
        self.obs_scene.setPlaceholderText("scene (optional)")
        test = QPushButton("Test")
        test.setObjectName("ghost")
        test.clicked.connect(self._test_obs)
        for widget in (self.obs_host, self.obs_port, self.obs_password, self.obs_scene, test):
            obs_row.addWidget(widget)
        for widget in (self.obs_host, self.obs_password, self.obs_scene):
            widget.editingFinished.connect(self._save)
        self.obs_port.valueChanged.connect(self._save)
        container = QWidget()
        container.setLayout(obs_row)
        grid.addWidget(container, 2, 1)

        grid.addWidget(label("Display", "faint"), 3, 0)
        display_row = QHBoxLayout()
        self.display_combo = QComboBox()
        for key, text in (("windowed", "Windowed"), ("borderless", "Borderless"), ("fullscreen", "Fullscreen")):
            self.display_combo.addItem(text, key)
        self._select(self.display_combo, recording.display_mode)
        self.display_combo.currentIndexChanged.connect(self._save)
        display_row.addWidget(self.display_combo)
        self.hide_hud = QCheckBox("Hide HUD")
        self.hide_hud.setChecked(recording.hide_hud)
        self.only_death_notices = QCheckBox("Only kill feed")
        self.only_death_notices.setChecked(recording.show_only_death_notices)
        self.player_voices = QCheckBox("Player voices")
        self.player_voices.setChecked(recording.player_voices)
        self.close_after = QCheckBox("Close CS2 when done")
        self.close_after.setChecked(recording.close_game_after_render)
        for box in (self.hide_hud, self.only_death_notices, self.player_voices, self.close_after):
            box.toggled.connect(self._save)
            display_row.addWidget(box)
        display_row.addStretch(1)
        display_container = QWidget()
        display_container.setLayout(display_row)
        grid.addWidget(display_container, 3, 1)

        grid.addWidget(label("Extra launch args", "faint"), 4, 0)
        self.extra_args = QLineEdit(recording.extra_launch_args)
        self.extra_args.setPlaceholderText("-high +fps_max 0")
        self.extra_args.editingFinished.connect(self._save)
        grid.addWidget(self.extra_args, 4, 1)

        grid.addWidget(label("Extra console cfg", "faint"), 5, 0)
        self.extra_cfg = QPlainTextEdit(recording.extra_cfg)
        self.extra_cfg.setPlaceholderText("One console command per line, run before each clip")
        self.extra_cfg.setMaximumHeight(70)
        self.extra_cfg.textChanged.connect(self._save)
        grid.addWidget(self.extra_cfg, 5, 1)
        card.body().addLayout(grid)

        card.body().addWidget(separator())
        self.status_label = label("", "faint", wrap=True)
        card.body().addWidget(self.status_label)
        refresh = QPushButton("Check availability")
        refresh.setObjectName("ghost")
        refresh.clicked.connect(self._refresh_backends)
        card.body().addWidget(refresh)

        card.body().addWidget(separator())
        card.body().addWidget(label("CS2 server plugin", "h3"))
        card.body().addWidget(
            label(
                "Source 2 has no VDM files, so running a console command at an exact demo tick needs a plugin "
                "loaded by CS2. If you already have the CS Demo Manager plugin, it is detected here. Nothing is "
                "downloaded for you, and gameinfo.gi is only edited after you press the button (a backup is kept).",
                "faint",
                wrap=True,
            )
        )
        self.plugin_label = label("", "muted", wrap=True)
        card.body().addWidget(self.plugin_label)
        plugin_row = QHBoxLayout()
        self.enable_plugin = QPushButton("Enable plugin (patch gameinfo.gi)")
        self.enable_plugin.setObjectName("ghost")
        self.enable_plugin.clicked.connect(self._enable_plugin)
        self.disable_plugin = QPushButton("Disable plugin")
        self.disable_plugin.setObjectName("ghost")
        self.disable_plugin.clicked.connect(self._disable_plugin)
        pick_binary = QPushButton("Select plugin binary…")
        pick_binary.setObjectName("ghost")
        pick_binary.clicked.connect(self._pick_plugin_binary)
        plugin_row.addWidget(self.enable_plugin)
        plugin_row.addWidget(self.disable_plugin)
        plugin_row.addWidget(pick_binary)
        plugin_row.addStretch(1)
        card.body().addLayout(plugin_row)

        self._refresh_backends()
        self._refresh_plugin()
        return card

    def _clips_card(self) -> Card:
        card = Card()
        card.body().addWidget(label("Clips", "h3"))
        clips = self.settings.clips
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)

        grid.addWidget(label("Multi-kill window", "faint"), 0, 0)
        self.window_spin = QDoubleSpinBox()
        self.window_spin.setRange(1.0, 30.0)
        self.window_spin.setSingleStep(0.5)
        self.window_spin.setSuffix(" s")
        self.window_spin.setValue(clips.multikill_window_seconds)
        self.window_spin.valueChanged.connect(self._save_and_redetect)
        grid.addWidget(self.window_spin, 0, 1)

        grid.addWidget(label("Max clips (Auto Clip)", "faint"), 0, 2)
        self.max_clips_spin = QSpinBox()
        self.max_clips_spin.setRange(1, 100)
        self.max_clips_spin.setValue(clips.max_clips)
        self.max_clips_spin.valueChanged.connect(self._save)
        grid.addWidget(self.max_clips_spin, 0, 3)

        grid.addWidget(label("Minimum score", "faint"), 1, 0)
        self.min_score_spin = QDoubleSpinBox()
        self.min_score_spin.setRange(0, 1000)
        self.min_score_spin.setDecimals(0)
        self.min_score_spin.setValue(clips.min_score)
        self.min_score_spin.valueChanged.connect(self._save)
        grid.addWidget(self.min_score_spin, 1, 1)

        self.merge_box = QCheckBox("Merge overlapping clips")
        self.merge_box.setChecked(clips.merge_overlapping)
        self.merge_box.toggled.connect(self._save_and_redetect)
        grid.addWidget(self.merge_box, 1, 2, 1, 2)

        self.clamp_box = QCheckBox("Keep clips inside their round")
        self.clamp_box.setChecked(clips.clamp_to_round)
        self.clamp_box.toggled.connect(self._save_and_redetect)
        grid.addWidget(self.clamp_box, 2, 2, 1, 2)
        card.body().addLayout(grid)

        card.body().addWidget(separator())
        card.body().addWidget(label("Seconds before and after each highlight", "faint"))
        windows = QGridLayout()
        windows.setHorizontalSpacing(14)
        self.lead_in_spins: dict[str, QDoubleSpinBox] = {}
        self.lead_out_spins: dict[str, QDoubleSpinBox] = {}
        for column, kind in enumerate(("KILL", "2K", "3K", "4K", "ACE", "CLUTCH")):
            windows.addWidget(label(kind, "h3"), 0, column)
            before = QDoubleSpinBox()
            before.setRange(0.0, 60.0)
            before.setSingleStep(0.5)
            before.setValue(clips.lead_in.get(kind, 6.0))
            before.valueChanged.connect(self._save_and_redetect)
            after = QDoubleSpinBox()
            after.setRange(0.0, 60.0)
            after.setSingleStep(0.5)
            after.setValue(clips.lead_out.get(kind, 4.0))
            after.valueChanged.connect(self._save_and_redetect)
            windows.addWidget(before, 1, column)
            windows.addWidget(after, 2, column)
            self.lead_in_spins[kind] = before
            self.lead_out_spins[kind] = after
        windows.addWidget(label("before ↑ / after ↓", "faint"), 1, 6, 2, 1)
        card.body().addLayout(windows)
        return card

    def _video_card(self) -> Card:
        card = Card()
        card.body().addWidget(label("Video", "h3"))
        video = self.settings.video
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)

        grid.addWidget(label("Preset", "faint"), 0, 0)
        self.preset_combo = QComboBox()
        for key, text in (
            ("fast", "Fast — 1080p60"),
            ("balanced", "Balanced — 1080p60"),
            ("quality", "Quality — 1440p60"),
            ("custom", "Custom"),
        ):
            self.preset_combo.addItem(text, key)
        self._select(self.preset_combo, self.settings.ui.quality_preset)
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        grid.addWidget(self.preset_combo, 0, 1)

        grid.addWidget(label("Resolution", "faint"), 0, 2)
        self.resolution_combo = QComboBox()
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)):
            # Item data is a plain "WxH" string, not a (width, height) tuple:
            # QComboBox.findData cannot match a Python tuple (it compares the
            # boxed variants by identity), so a tuple would leave the combo
            # stuck on the first entry — which is how every render silently
            # became 1280x720 regardless of the saved resolution.
            self.resolution_combo.addItem(f"{width}x{height}", f"{width}x{height}")
        self._select(self.resolution_combo, f"{video.width}x{video.height}")
        self.resolution_combo.currentIndexChanged.connect(self._save)
        grid.addWidget(self.resolution_combo, 0, 3)

        grid.addWidget(label("FPS", "faint"), 1, 0)
        self.fps_combo = QComboBox()
        for fps in (30, 60, 120, 144):
            self.fps_combo.addItem(str(fps), fps)
        self._select(self.fps_combo, video.fps)
        self.fps_combo.currentIndexChanged.connect(self._save)
        grid.addWidget(self.fps_combo, 1, 1)

        grid.addWidget(label("Codec", "faint"), 1, 2)
        self.codec_combo = QComboBox()
        self.codec_combo.addItem("H.264", "h264")
        self.codec_combo.addItem("H.265 / HEVC", "h265")
        self._select(self.codec_combo, video.codec)
        self.codec_combo.currentIndexChanged.connect(self._save)
        grid.addWidget(self.codec_combo, 1, 3)

        grid.addWidget(label("Bitrate", "faint"), 2, 0)
        self.bitrate_spin = QSpinBox()
        self.bitrate_spin.setRange(1000, 200000)
        self.bitrate_spin.setSingleStep(1000)
        self.bitrate_spin.setSuffix(" kbps")
        self.bitrate_spin.setValue(video.bitrate_kbps)
        self.bitrate_spin.valueChanged.connect(self._save)
        grid.addWidget(self.bitrate_spin, 2, 1)

        grid.addWidget(label("Encoder", "faint"), 2, 2)
        self.encoder_combo = QComboBox()
        ffmpeg = FFmpeg(self.settings.paths.ffmpeg_executable or None)
        families = ffmpeg.encoders().families() if ffmpeg.available else ["auto", "cpu"]
        for family in families:
            self.encoder_combo.addItem(ENCODER_LABELS.get(family, family), family)
        self._select(self.encoder_combo, video.encoder)
        self.encoder_combo.currentIndexChanged.connect(self._save)
        grid.addWidget(self.encoder_combo, 2, 3)
        card.body().addLayout(grid)

        audio_row = QHBoxLayout()
        self.game_audio = QCheckBox("Game audio")
        self.game_audio.setChecked(video.game_audio)
        self.voice_audio = QCheckBox("Voice audio (if present in the recording)")
        self.voice_audio.setChecked(video.voice_audio)
        for box in (self.game_audio, self.voice_audio):
            box.toggled.connect(self._save)
            audio_row.addWidget(box)
        audio_row.addWidget(label("Volume", "faint"))
        self.volume_spin = QDoubleSpinBox()
        self.volume_spin.setRange(0.0, 2.0)
        self.volume_spin.setSingleStep(0.05)
        self.volume_spin.setValue(video.volume)
        self.volume_spin.valueChanged.connect(self._save)
        audio_row.addWidget(self.volume_spin)
        audio_row.addStretch(1)
        card.body().addLayout(audio_row)

        self.encoder_note = label("", "faint", wrap=True)
        card.body().addWidget(self.encoder_note)
        if ffmpeg.available:
            available = ", ".join(sorted(ffmpeg.encoders().available)) or "none"
            self.encoder_note.setText(f"FFmpeg encoders detected: {available}")
        else:
            self.encoder_note.setText("FFmpeg was not found, so no encoder could be detected.")
            self.encoder_note.setStyleSheet(f"color: {theme.WARNING};")
        return card

    def _maintenance_card(self) -> Card:
        card = Card()
        card.body().addWidget(label("Maintenance", "h3"))
        row = QHBoxLayout()
        logs = QPushButton("Open Logs Folder")
        logs.setObjectName("ghost")
        logs.clicked.connect(lambda: open_in_file_manager(self.settings.logs_dir))
        clips = QPushButton("Open Clips Folder")
        clips.setObjectName("ghost")
        clips.clicked.connect(lambda: open_in_file_manager(self.settings.paths.output_dir))
        cache = QPushButton("Clear cache")
        cache.setObjectName("ghost")
        cache.clicked.connect(self._clear_cache)
        for button in (logs, clips, cache):
            row.addWidget(button)
        self.developer_box = QCheckBox("Developer mode")
        self.developer_box.setChecked(self.settings.ui.developer_mode)
        self.developer_box.setToolTip("Show ticks, POV slot, CS2 and FFmpeg state on the Render page")
        self.developer_box.toggled.connect(self._save)
        row.addWidget(self.developer_box)
        row.addStretch(1)
        card.body().addLayout(row)
        self.cache_label = label("", "faint")
        card.body().addWidget(self.cache_label)
        self._refresh_cache_label()
        return card

    # -- behaviour -------------------------------------------------------
    @staticmethod
    def _select(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _selected_resolution(self, fallback: tuple[int, int]) -> tuple[int, int]:
        """Parse the ``"WxH"`` item data back into an ``(int, int)`` pair."""
        data = self.resolution_combo.currentData()
        try:
            width, height = str(data).lower().split("x")
            return int(width), int(height)
        except (AttributeError, ValueError):
            return fallback

    def _save(self) -> None:
        paths = self.settings.paths
        paths.cs2_executable = self.cs2_field.text().strip()
        paths.steam_path = self.steam_field.text().strip()
        paths.ffmpeg_executable = self.ffmpeg_field.text().strip()
        paths.obs_executable = self.obs_field.text().strip()
        paths.hlae_executable = self.hlae_field.text().strip()
        paths.output_dir = self.output_field.text().strip() or paths.output_dir
        paths.temp_dir = self.temp_field.text().strip() or paths.temp_dir

        recording = self.settings.recording
        recording.backend = self.recorder_combo.currentData()
        recording.playback_backend = self.playback_combo.currentData()
        recording.obs_host = self.obs_host.text().strip()
        recording.obs_port = self.obs_port.value()
        recording.obs_password = self.obs_password.text()
        recording.obs_scene = self.obs_scene.text().strip()
        recording.display_mode = self.display_combo.currentData()
        recording.hide_hud = self.hide_hud.isChecked()
        recording.show_only_death_notices = self.only_death_notices.isChecked()
        recording.player_voices = self.player_voices.isChecked()
        recording.close_game_after_render = self.close_after.isChecked()
        recording.extra_launch_args = self.extra_args.text().strip()
        recording.extra_cfg = self.extra_cfg.toPlainText()

        clips = self.settings.clips
        clips.multikill_window_seconds = self.window_spin.value()
        clips.max_clips = self.max_clips_spin.value()
        clips.min_score = self.min_score_spin.value()
        clips.merge_overlapping = self.merge_box.isChecked()
        clips.clamp_to_round = self.clamp_box.isChecked()
        for kind, spin in self.lead_in_spins.items():
            clips.lead_in[kind] = spin.value()
        for kind, spin in self.lead_out_spins.items():
            clips.lead_out[kind] = spin.value()

        video = self.settings.video
        video.width, video.height = self._selected_resolution((video.width, video.height))
        video.fps = self.fps_combo.currentData() or video.fps
        video.codec = self.codec_combo.currentData() or video.codec
        video.bitrate_kbps = self.bitrate_spin.value()
        video.encoder = self.encoder_combo.currentData() or "auto"
        video.game_audio = self.game_audio.isChecked()
        video.voice_audio = self.voice_audio.isChecked()
        video.volume = self.volume_spin.value()

        self.settings.ui.developer_mode = self.developer_box.isChecked()
        self.state.save_settings()

    def _save_and_redetect(self) -> None:
        self._save()
        self.state.redetect()

    def _apply_preset(self) -> None:
        preset = self.preset_combo.currentData()
        if preset and preset != "custom":
            self.settings.apply_quality_preset(preset)
            video = self.settings.video
            self._select(self.resolution_combo, f"{video.width}x{video.height}")
            self._select(self.fps_combo, video.fps)
            self._select(self.codec_combo, video.codec)
            self.bitrate_spin.setValue(video.bitrate_kbps)
        self._save()

    def _auto_detect(self) -> None:
        detect_all(self.settings, apply=True)
        self.cs2_field.setText(self.settings.paths.cs2_executable)
        self.steam_field.setText(self.settings.paths.steam_path)
        self.ffmpeg_field.setText(self.settings.paths.ffmpeg_executable)
        self.obs_field.setText(self.settings.paths.obs_executable)
        self.hlae_field.setText(self.settings.paths.hlae_executable)
        self._save()
        self._refresh_detection()
        self._refresh_backends()
        self._refresh_plugin()

    def _refresh_detection(self) -> None:
        report = detect_all(self.settings, apply=False)
        self.detection_label.setText("   ".join(result.line for result in report.results))

    def _refresh_backends(self) -> None:
        cs2 = str(find_cs2_executable(self.settings.paths.cs2_executable, self.settings.paths.steam_path) or "")
        ffmpeg = FFmpeg(self.settings.paths.ffmpeg_executable or None)
        lines = ["Recorders:"]
        lines += [
            f"  {'✓' if usable else '⚠'} {RECORDER_LABELS.get(name, name)} — {detail}"
            for name, usable, detail in describe_recorders(self.settings.recording, cs2, ffmpeg)
        ]
        lines.append("CS2 control:")
        lines += [f"  {'✓' if usable else '⚠'} {name} — {detail}" for name, usable, detail in describe_backends(cs2)]
        self.status_label.setText("\n".join(lines))

    def _refresh_plugin(self) -> None:
        cs2 = str(find_cs2_executable(self.settings.paths.cs2_executable, self.settings.paths.steam_path) or "")
        status = plugin_module.plugin_status(cs2)
        if status.usable:
            text = f"✓ Plugin installed and enabled ({status.binary_path})"
        elif status.installed:
            text = "⚠ Plugin binary found but not enabled — press Enable plugin."
        else:
            found = plugin_module.find_existing_plugin_binaries()
            text = (
                f"⚠ No plugin installed. Found a candidate from CS Demo Manager: {found[0]}"
                if found
                else "⚠ No plugin installed. Tick-accurate control is unavailable; the app will use another backend."
            )
        self.plugin_label.setText(text)
        self.enable_plugin.setEnabled(bool(cs2))
        self.disable_plugin.setEnabled(status.gameinfo_patched)

    def _enable_plugin(self) -> None:
        cs2 = str(find_cs2_executable(self.settings.paths.cs2_executable, self.settings.paths.steam_path) or "")
        answer = QMessageBox.question(
            self,
            "Enable the CS2 plugin?",
            "This edits gameinfo.gi in your CS2 installation so the game loads the plugin.\n\n"
            "A backup is written next to it and can be restored with 'Disable plugin'.\n"
            "CS2 will be started with -insecure, so VAC is not involved.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ok = plugin_module.patch_gameinfo(cs2)
        self.settings.recording.allow_plugin_install = ok
        self.state.save_settings()
        self.state.status("Plugin enabled" if ok else "gameinfo.gi could not be patched — see the logs")
        self._refresh_plugin()
        self._refresh_backends()

    def _disable_plugin(self) -> None:
        cs2 = str(find_cs2_executable(self.settings.paths.cs2_executable, self.settings.paths.steam_path) or "")
        plugin_module.uninstall_plugin(cs2, remove_binary=False)
        self.state.status("Plugin disabled and gameinfo.gi restored")
        self._refresh_plugin()
        self._refresh_backends()

    def _pick_plugin_binary(self) -> None:
        cs2 = str(find_cs2_executable(self.settings.paths.cs2_executable, self.settings.paths.steam_path) or "")
        path, _ = QFileDialog.getOpenFileName(self, "Select the plugin binary", str(Path.home()), "Plugin (*.dll *.so)")
        if not path:
            return
        ok = plugin_module.install_plugin_binary(cs2, path)
        self.settings.recording.cs2_plugin_path = path if ok else ""
        self.state.save_settings()
        self.state.status("Plugin binary installed" if ok else "The plugin binary could not be copied")
        self._refresh_plugin()

    def _test_obs(self) -> None:
        from ...recording.obs import OBSRecorder

        self._save()
        recorder = OBSRecorder(self.settings.recording, FFmpeg(self.settings.paths.ffmpeg_executable or None))
        ok, detail = recorder.check_connection()
        self.state.status(("OBS: " if ok else "OBS problem: ") + detail)
        QMessageBox.information(self, "OBS WebSocket", detail)

    def _clear_cache(self) -> None:
        removed = AnalysisCache(self.settings.cache_dir).clear()
        self.state.status(f"Cleared {removed} cached analyses")
        self._refresh_cache_label()

    def _refresh_cache_label(self) -> None:
        cache = AnalysisCache(self.settings.cache_dir)
        entries = cache.entries()
        self.cache_label.setText(
            f"{len(entries)} cached analyses · {cache.size_bytes() / (1024 * 1024):.1f} MB"
            f" · {self.settings.cache_dir}"
        )

    def _open_url(self, url: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(url))
