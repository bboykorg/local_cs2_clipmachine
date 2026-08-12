"""First-run auto-detection.

On the first start the app looks for Steam, CS2, FFmpeg, OBS and HLAE, fills in
what it finds and reports what it did not. Nothing is ever downloaded or
installed automatically: a missing tool becomes a line of text and a button that
opens the vendor's own download page in the user's browser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..cs2.launcher import find_cs2_executable, find_steam_path, steam_library_folders
from ..recording.obs import find_obs_executable, obs_client_available
from ..utils.process import which
from ..video.ffmpeg import find_ffmpeg, find_ffprobe
from .config import Settings
from .logger import get_logger

log = get_logger("app")

DOWNLOAD_PAGES = {
    "ffmpeg": "https://www.gyan.dev/ffmpeg/builds/",
    "obs": "https://obsproject.com/download",
    "hlae": "https://github.com/advancedfx/advancedfx/releases",
    "cs2": "steam://install/730",
}


@dataclass
class DetectionResult:
    name: str
    found: bool
    path: str = ""
    detail: str = ""

    @property
    def line(self) -> str:
        icon = "✓" if self.found else "⚠"
        suffix = f" — {self.detail}" if self.detail else ""
        return f"{icon} {self.name}{'' if self.found else ' not found'}{suffix}"


@dataclass
class DetectionReport:
    results: list[DetectionResult] = field(default_factory=list)

    def add(self, result: DetectionResult) -> None:
        self.results.append(result)

    def get(self, name: str) -> DetectionResult | None:
        return next((r for r in self.results if r.name.lower() == name.lower()), None)

    @property
    def missing(self) -> list[DetectionResult]:
        return [r for r in self.results if not r.found]

    def as_lines(self) -> list[str]:
        return [result.line for result in self.results]


def find_hlae(explicit: str = "") -> str | None:
    if explicit and Path(explicit).is_file():
        return explicit
    candidates = [
        r"C:\HLAE\HLAE.exe",
        r"C:\Program Files\HLAE\HLAE.exe",
        r"C:\Program Files (x86)\HLAE\HLAE.exe",
    ]
    return which("HLAE", extra_paths=candidates)


def detect_all(settings: Settings, apply: bool = True) -> DetectionReport:
    """Detect the tool chain and, by default, write findings into ``settings``."""
    report = DetectionReport()

    steam = find_steam_path(settings.paths.steam_path)
    report.add(DetectionResult("Steam", bool(steam), str(steam or ""), str(steam or "")))
    if apply and steam:
        settings.paths.steam_path = str(steam)
        settings.paths.steam_libraries = [str(p) for p in steam_library_folders(steam)]

    cs2 = find_cs2_executable(settings.paths.cs2_executable, str(steam or ""))
    report.add(DetectionResult("CS2", bool(cs2), str(cs2 or ""), str(cs2 or "")))
    if apply and cs2:
        settings.paths.cs2_executable = str(cs2)

    ffmpeg = find_ffmpeg(settings.paths.ffmpeg_executable)
    report.add(DetectionResult("FFmpeg", bool(ffmpeg), ffmpeg or "", ffmpeg or "install it to encode clips"))
    if apply and ffmpeg:
        settings.paths.ffmpeg_executable = ffmpeg
        ffprobe = find_ffprobe(settings.paths.ffprobe_executable, ffmpeg)
        if ffprobe:
            settings.paths.ffprobe_executable = ffprobe

    obs = find_obs_executable(settings.paths.obs_executable)
    obs_detail = obs or "optional; enables the OBS recorder"
    if obs and not obs_client_available():
        obs_detail = f"{obs} (install 'obsws-python' to control it)"
    report.add(DetectionResult("OBS Studio", bool(obs), obs or "", obs_detail))
    if apply and obs:
        settings.paths.obs_executable = obs

    hlae = find_hlae(settings.paths.hlae_executable)
    report.add(DetectionResult("HLAE", bool(hlae), hlae or "", hlae or "optional; best recording quality"))
    if apply and hlae:
        settings.paths.hlae_executable = hlae

    if apply:
        settings.ensure_dirs()
    log.info("detection: %s", "; ".join(report.as_lines()))
    return report


def first_run_needed(settings: Settings) -> bool:
    return not (settings.paths.cs2_executable or settings.paths.ffmpeg_executable)
