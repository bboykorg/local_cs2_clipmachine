"""Recorder registry and selection.

``auto`` walks the list in order of quality and picks the first backend that
reports itself usable, so a machine with HLAE gets HLAE, a machine with OBS
running gets OBS, and a bare machine still gets a working (if slower) capture.
"""

from __future__ import annotations

from ..core.config import RecordingSettings
from ..core.logger import get_logger
from ..video.ffmpeg import FFmpeg
from .base import Recorder, RecordingContext
from .ffmpeg_capture import FFmpegScreenRecorder
from .hlae import HLAERecorder
from .native import NativeStartMovieRecorder
from .obs import OBSRecorder

log = get_logger("recorder")

RECORDERS: dict[str, type[Recorder]] = {
    HLAERecorder.name: HLAERecorder,
    OBSRecorder.name: OBSRecorder,
    NativeStartMovieRecorder.name: NativeStartMovieRecorder,
    FFmpegScreenRecorder.name: FFmpegScreenRecorder,
}

#: Preference order for ``auto``: quality first, then convenience.
AUTO_ORDER = (HLAERecorder.name, OBSRecorder.name, NativeStartMovieRecorder.name, FFmpegScreenRecorder.name)

LABELS = {
    "auto": "Auto (best available)",
    HLAERecorder.name: "HLAE (mirv_streams)",
    OBSRecorder.name: "OBS Studio (WebSocket)",
    NativeStartMovieRecorder.name: "CS2 startmovie",
    FFmpegScreenRecorder.name: "FFmpeg window capture",
}


def build_recorder(name: str, settings: RecordingSettings, ffmpeg: FFmpeg | None = None) -> Recorder:
    recorder_cls = RECORDERS.get(name)
    if recorder_cls is None:
        raise KeyError(f"unknown recorder '{name}'")
    return recorder_cls(settings, ffmpeg)


def describe_recorders(
    settings: RecordingSettings, cs2_executable: str = "", ffmpeg: FFmpeg | None = None
) -> list[tuple[str, bool, str]]:
    """``(name, usable, explanation)`` for the Settings page."""
    ffmpeg = ffmpeg or FFmpeg()
    out: list[tuple[str, bool, str]] = []
    for name in AUTO_ORDER:
        recorder = build_recorder(name, settings, ffmpeg)
        try:
            usable, detail = recorder.available(cs2_executable)
        except Exception as exc:  # pragma: no cover - defensive
            usable, detail = False, str(exc)
        out.append((name, usable, detail))
    return out


def get_recorder(
    settings: RecordingSettings, cs2_executable: str = "", ffmpeg: FFmpeg | None = None
) -> tuple[Recorder, str]:
    """Return ``(recorder, explanation)`` honouring ``settings.backend``."""
    ffmpeg = ffmpeg or FFmpeg()
    preferred = (settings.backend or "auto").lower()

    if preferred != "auto":
        recorder = build_recorder(preferred, settings, ffmpeg)
        usable, detail = recorder.available(cs2_executable)
        if not usable:
            log.warning("recorder %s is not usable (%s); falling back to auto", preferred, detail)
        else:
            return recorder, detail

    for name in AUTO_ORDER:
        recorder = build_recorder(name, settings, ffmpeg)
        usable, detail = recorder.available(cs2_executable)
        if usable:
            log.info("recorder: %s (%s)", name, detail)
            return recorder, detail

    from ..core.errors import recording_failed

    raise recording_failed("no recording backend is available on this machine")


__all__ = [
    "Recorder",
    "RecordingContext",
    "RECORDERS",
    "AUTO_ORDER",
    "LABELS",
    "build_recorder",
    "describe_recorders",
    "get_recorder",
]
