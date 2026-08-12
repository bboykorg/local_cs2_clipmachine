"""The recorder interface.

A recorder turns "CS2 is playing the interesting part right now" into a file on
disk. The implementations differ wildly — an external screen capture, a
WebSocket request to OBS, console commands that make the engine dump frames — so
the interface is deliberately small:

``available()``  can this recorder run on this machine, and if not, why not
``begin(...)``   prepare output paths and any per-session configuration
``hooks(...)``   how the playback controller should drive it for one clip
``finalise(...)``  produce the final ``.mp4`` (usually by handing FFmpeg the raw
                 frames or the intermediate file)

Everything the pipeline needs to know about *timing* is expressed by the hooks,
which is what lets the same recorder work with a tick-scheduling plugin and with
a live console.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import RecordingSettings
from ..core.models import VideoSettings
from ..cs2.controller import ClipPlan, RecorderHooks
from ..video.ffmpeg import FFmpeg


@dataclass
class RecordingContext:
    """Everything a recorder needs for one clip."""

    clip: ClipPlan
    output_path: Path
    work_dir: Path
    video: VideoSettings
    recording: RecordingSettings
    cs2_executable: str = ""
    #: Extra seconds recorded on each side and trimmed away later.
    safety_margin: float = 2.0
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def clip_name(self) -> str:
        return self.output_path.stem

    @property
    def expected_duration(self) -> float:
        return self.clip.duration_seconds


class Recorder(ABC):
    """Base class for every recording backend."""

    name = "abstract"
    #: True when frames are produced by the engine itself (playback is not
    #: real time and the pipeline must wait longer).
    in_game = False

    def __init__(self, settings: RecordingSettings, ffmpeg: FFmpeg | None = None) -> None:
        self.settings = settings
        self.ffmpeg = ffmpeg or FFmpeg()

    # -- capability ------------------------------------------------------
    @abstractmethod
    def available(self, cs2_executable: str = "") -> tuple[bool, str]:
        """``(usable, explanation)``."""

    # -- lifecycle -------------------------------------------------------
    def begin_session(self, context: RecordingContext) -> None:
        """Called once before the first clip of a CS2 session."""

    @abstractmethod
    def hooks(self, context: RecordingContext) -> RecorderHooks:
        """How to start and stop this recorder around one clip."""

    @abstractmethod
    def finalise(self, context: RecordingContext, on_progress=None, cancel=None) -> Path:  # noqa: ANN001
        """Produce the final MP4 and return its path."""

    def end_session(self, context: RecordingContext | None = None) -> None:
        """Called once after the last clip."""

    def cleanup(self, context: RecordingContext) -> None:
        """Delete intermediate files. Never raises."""

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _ensure_dir(path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def launch_wrapper(self, cs2_executable: str) -> list[str]:
        """Extra command prefix needed to launch CS2 (HLAE uses this)."""
        del cs2_executable
        return []
