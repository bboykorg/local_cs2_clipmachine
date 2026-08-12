"""Persistent application settings.

Settings live in a single JSON file inside the user's data directory, so the
packaged ``CS2ClipGenerator.exe`` never needs write access to its own folder.
Unknown keys in the file are ignored, missing keys fall back to defaults: that
keeps old configs loadable after an upgrade.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import JsonMixin, VideoSettings

APP_NAME = "CS2ClipGenerator"
IS_WINDOWS = os.name == "nt"


def app_data_dir() -> Path:
    """Per-user writable directory for settings, cache and logs."""
    override = os.environ.get("CS2CLIP_HOME")
    if override:
        return Path(override).expanduser()
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / APP_NAME


def default_output_dir() -> Path:
    if IS_WINDOWS:
        videos = Path(os.path.expanduser("~")) / "Videos"
        if videos.exists():
            return videos / "CS2Clips"
    return Path.home() / "CS2Clips"


def default_temp_dir() -> Path:
    return Path(tempfile.gettempdir()) / "cs2clip"


# ---------------------------------------------------------------------------
# Settings sections
# ---------------------------------------------------------------------------


@dataclass
class PathSettings(JsonMixin):
    steam_path: str = ""
    cs2_executable: str = ""
    #: Extra Steam library folders discovered from libraryfolders.vdf.
    steam_libraries: list[str] = field(default_factory=list)
    ffmpeg_executable: str = ""
    ffprobe_executable: str = ""
    obs_executable: str = ""
    hlae_executable: str = ""
    output_dir: str = field(default_factory=lambda: str(default_output_dir()))
    temp_dir: str = field(default_factory=lambda: str(default_temp_dir()))


@dataclass
class ClipSettings(JsonMixin):
    """Clip window lengths, in seconds, per highlight kind."""

    lead_in: dict[str, float] = field(
        default_factory=lambda: {"KILL": 6.0, "2K": 8.0, "3K": 8.0, "4K": 10.0, "ACE": 10.0, "CLUTCH": 10.0}
    )
    lead_out: dict[str, float] = field(
        default_factory=lambda: {"KILL": 4.0, "2K": 5.0, "3K": 6.0, "4K": 7.0, "ACE": 8.0, "CLUTCH": 10.0}
    )
    multikill_window_seconds: float = 7.0
    max_clips: int = 10
    min_score: float = 40.0
    merge_overlapping: bool = True
    #: Two clips are merged when the gap between them is below this value.
    merge_gap_seconds: float = 1.0
    #: Never clip past the end of the round + this many seconds.
    clamp_to_round: bool = True
    round_padding_seconds: float = 3.0


@dataclass
class ScoringSettings(JsonMixin):
    base: dict[str, float] = field(
        default_factory=lambda: {
            "ACE": 100.0,
            "4K": 80.0,
            "3K": 60.0,
            "2K": 30.0,
            "KILL": 5.0,
            "CLUTCH_1V5": 100.0,
            "CLUTCH_1V4": 90.0,
            "CLUTCH_1V3": 75.0,
            "CLUTCH_1V2": 40.0,
            "CLUTCH_1V1": 20.0,
        }
    )
    per_kill: dict[str, float] = field(
        default_factory=lambda: {
            "HEADSHOT": 10.0,
            "KNIFE": 40.0,
            "ZEUS": 40.0,
            "GRENADE": 40.0,
            "MOLOTOV": 30.0,
            "WALLBANG": 30.0,
            "NOSCOPE": 30.0,
            "THROUGH_SMOKE": 20.0,
            "BLINDED": 15.0,
            "JUMPING": 20.0,
            "AWP": 15.0,
            "SCOUT": 15.0,
            "DEAGLE": 10.0,
            "PISTOL": 8.0,
            "LONG_RANGE": 5.0,
        }
    )
    bonus: dict[str, float] = field(
        default_factory=lambda: {"HEADSHOT_ONLY": 25.0, "POST_PLANT": 5.0, "NINJA_DEFUSE": 50.0}
    )


@dataclass
class RecordingSettings(JsonMixin):
    #: obs | hlae | native | ffmpeg
    backend: str = "auto"
    #: plugin | vanilla — how CS2 is driven during playback.
    playback_backend: str = "auto"
    obs_host: str = "localhost"
    obs_port: int = 4455
    obs_password: str = ""
    obs_scene: str = ""
    #: Seconds to wait for CS2 to load the demo before trusting playback.
    demo_load_timeout: float = 90.0
    #: Extra seconds of playback recorded before/after, trimmed by FFmpeg later.
    safety_margin_seconds: float = 2.0
    #: Seconds to let the game settle after seeking, before recording.
    stabilisation_seconds: float = 2.0
    close_game_after_render: bool = True
    display_mode: str = "windowed"  # windowed | fullscreen | borderless
    hide_hud: bool = False
    show_only_death_notices: bool = False
    player_voices: bool = False
    #: Allow patching gameinfo.gi to load the CS2 server plugin (needs consent).
    allow_plugin_install: bool = False
    cs2_plugin_path: str = ""
    #: Mirrored from PathSettings by Settings.sync() so recorders need only one
    #: settings object.
    hlae_executable: str = ""
    #: DirectShow audio device for the FFmpeg capture recorder ("" = autodetect).
    capture_audio_device: str = ""
    extra_launch_args: str = ""
    extra_cfg: str = ""


@dataclass
class UiSettings(JsonMixin):
    developer_mode: bool = False
    quality_preset: str = "balanced"  # fast | balanced | quality | custom
    last_demo: str = ""
    recent_demos: list[str] = field(default_factory=list)
    sort_key: str = "score"
    active_filters: list[str] = field(default_factory=list)


@dataclass
class Settings(JsonMixin):
    paths: PathSettings = field(default_factory=PathSettings)
    clips: ClipSettings = field(default_factory=ClipSettings)
    scoring: ScoringSettings = field(default_factory=ScoringSettings)
    recording: RecordingSettings = field(default_factory=RecordingSettings)
    video: VideoSettings = field(default_factory=VideoSettings)
    ui: UiSettings = field(default_factory=UiSettings)

    # -- persistence -----------------------------------------------------
    @staticmethod
    def path() -> Path:
        return app_data_dir() / "settings.json"

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        target = Path(path) if path else cls.path()
        if not target.exists():
            return cls()
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls.from_payload(payload).sync()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Settings:
        sections: dict[str, Any] = {}
        for f in dataclasses.fields(cls):
            raw = payload.get(f.name)
            section_cls = f.type if isinstance(f.type, type) else None
            factory = {
                "paths": PathSettings,
                "clips": ClipSettings,
                "scoring": ScoringSettings,
                "recording": RecordingSettings,
                "video": VideoSettings,
                "ui": UiSettings,
            }[f.name]
            del section_cls
            if isinstance(raw, dict):
                base = factory()
                for key, value in raw.items():
                    if hasattr(base, key):
                        setattr(base, key, value)
                sections[f.name] = base
            else:
                sections[f.name] = factory()
        return cls(**sections)

    def save(self, path: Path | None = None) -> Path:
        self.sync()
        target = Path(path) if path else self.path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
        return target

    # -- derived paths ---------------------------------------------------
    @property
    def cache_dir(self) -> Path:
        return app_data_dir() / "cache"

    @property
    def logs_dir(self) -> Path:
        return app_data_dir() / "logs"

    @property
    def state_dir(self) -> Path:
        return app_data_dir() / "state"

    def ensure_dirs(self) -> None:
        for path in (
            self.cache_dir,
            self.logs_dir,
            self.state_dir,
            Path(self.paths.output_dir),
            Path(self.paths.temp_dir),
        ):
            try:
                Path(path).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

    def sync(self) -> Settings:
        """Propagate values that two sections need to agree on."""
        self.recording.hlae_executable = self.paths.hlae_executable
        return self

    def apply_quality_preset(self, preset: str) -> None:
        """Map a friendly preset onto concrete video settings."""
        preset = (preset or "").lower()
        presets: dict[str, dict[str, Any]] = {
            "fast": {"width": 1920, "height": 1080, "fps": 60, "codec": "h264", "bitrate_kbps": 12000},
            "balanced": {"width": 1920, "height": 1080, "fps": 60, "codec": "h264", "bitrate_kbps": 20000},
            "quality": {"width": 2560, "height": 1440, "fps": 60, "codec": "h265", "bitrate_kbps": 40000},
        }
        if preset in presets:
            for key, value in presets[preset].items():
                setattr(self.video, key, value)
        self.ui.quality_preset = preset if preset in presets else "custom"
