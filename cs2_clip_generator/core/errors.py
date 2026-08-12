"""User-facing errors.

Every failure that can reach the UI is expressed as an :class:`AppError` with a
short title, a list of plausible reasons and a list of suggested actions. The
UI renders exactly that; the original exception is logged, not shown.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class Cancelled(Exception):
    """Raised when the user cancels a long-running operation."""


@dataclass
class AppError(Exception):
    title: str
    reasons: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.title

    def as_text(self) -> str:
        lines = [self.title]
        if self.reasons:
            lines.append("")
            lines.append("Possible reasons:")
            lines += [f"• {reason}" for reason in self.reasons]
        return "\n".join(lines)


class DemoError(AppError):
    pass


class ParserError(AppError):
    pass


class DownloadError(AppError):
    pass


class RecorderError(AppError):
    pass


class FFmpegError(AppError):
    pass


class CS2Error(AppError):
    pass


# ---------------------------------------------------------------------------
# Ready-made errors for the situations users actually hit
# ---------------------------------------------------------------------------


def unsupported_demo(path: str, detail: str = "") -> DemoError:
    return DemoError(
        title="This file is not a Counter-Strike 2 demo.",
        reasons=[
            "The file is a CS:GO (Source 1) demo, which this parser does not read",
            "The file is truncated or corrupted",
            "The download returned an HTML page instead of a demo",
        ],
        actions=["Pick another file", "Re-download the demo"],
        detail=detail or path,
    )


def parser_missing() -> ParserError:
    return ParserError(
        title="The CS2 demo parser is not installed.",
        reasons=["The 'demoparser2' package is missing from this installation"],
        actions=["Run: pip install -r requirements.txt", "Reinstall the application"],
    )


def cs2_not_found() -> CS2Error:
    return CS2Error(
        title="Counter-Strike 2 could not be found.",
        reasons=[
            "CS2 is not installed on this machine",
            "CS2 lives in a Steam library folder that was not detected",
            "The configured CS2 path is wrong",
        ],
        actions=["Open Settings and set the CS2 executable", "Start Steam and let it verify the game files"],
    )


def recording_failed(detail: str = "") -> RecorderError:
    return RecorderError(
        title="Unable to start CS2 recording.",
        reasons=[
            "CS2 is not installed or the path is incorrect",
            "Another CS2 instance is already running",
            "The selected recorder (OBS/HLAE) is unavailable",
            "Steam is not running",
        ],
        actions=["Open Settings", "Retry"],
        detail=detail,
    )


def ffmpeg_not_found() -> FFmpegError:
    return FFmpegError(
        title="FFmpeg was not found.",
        reasons=[
            "FFmpeg is not installed",
            "FFmpeg is not on PATH and no explicit path is configured",
        ],
        actions=["Open Settings and select ffmpeg.exe", "Install FFmpeg and restart the app"],
    )


def not_enough_disk_space(needed_mb: float, free_mb: float, path: str) -> AppError:
    return AppError(
        title="Not enough disk space.",
        reasons=[
            f"About {needed_mb:.0f} MB are required, {free_mb:.0f} MB are free",
            f"Target folder: {path}",
        ],
        actions=["Free up disk space", "Choose another output folder", "Lower the bitrate or resolution"],
    )
