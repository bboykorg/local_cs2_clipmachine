"""Everything FFmpeg.

Three responsibilities: find FFmpeg, know what the machine can encode with, and
build command lines that are correct the first time. The command builders are
pure functions returning argument *lists* — they are unit tested, and they can
never be mangled by a space in a player's name or a path.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import Cancelled, FFmpegError, ffmpeg_not_found
from ..core.logger import get_logger
from ..core.models import VideoSettings
from ..utils.process import CommandResult, run, stream

log = get_logger("ffmpeg")

#: encoder id -> (video codec, ffmpeg encoder name)
HARDWARE_ENCODERS: dict[str, dict[str, str]] = {
    "nvenc": {"h264": "h264_nvenc", "h265": "hevc_nvenc"},
    "amf": {"h264": "h264_amf", "h265": "hevc_amf"},
    "qsv": {"h264": "h264_qsv", "h265": "hevc_qsv"},
    "cpu": {"h264": "libx264", "h265": "libx265"},
}

ENCODER_LABELS = {
    "auto": "Auto (best available)",
    "cpu": "CPU (x264/x265)",
    "nvenc": "NVIDIA NVENC",
    "amf": "AMD AMF",
    "qsv": "Intel QuickSync",
}


# ---------------------------------------------------------------------------
# Locating FFmpeg
# ---------------------------------------------------------------------------


def find_ffmpeg(explicit: str = "") -> str | None:
    if explicit and Path(explicit).is_file():
        return str(explicit)
    found = shutil.which("ffmpeg")
    if found:
        return found
    if os.name == "nt":  # pragma: no cover - Windows only
        for candidate in (
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
        ):
            if Path(candidate).is_file():
                return candidate
    return None


def find_ffprobe(explicit: str = "", ffmpeg_path: str = "") -> str | None:
    if explicit and Path(explicit).is_file():
        return str(explicit)
    found = shutil.which("ffprobe")
    if found:
        return found
    if ffmpeg_path:
        sibling = Path(ffmpeg_path).with_name("ffprobe" + (".exe" if os.name == "nt" else ""))
        if sibling.is_file():
            return str(sibling)
    return None


def ffmpeg_version(ffmpeg: str) -> str:
    result = run([ffmpeg, "-hide_banner", "-version"], timeout=20, log_name="ffmpeg")
    if not result.ok:
        return ""
    first = (result.stdout or result.stderr).splitlines()[:1]
    return first[0].strip() if first else ""


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@dataclass
class EncoderSupport:
    available: set[str] = field(default_factory=set)

    def encoder_for(self, codec: str, preference: str = "auto") -> str:
        """Resolve ``(codec, preference)`` to a concrete FFmpeg encoder name."""
        codec = "h265" if codec.lower() in ("h265", "hevc") else "h264"
        if preference != "auto":
            candidate = HARDWARE_ENCODERS.get(preference, {}).get(codec)
            if candidate and candidate in self.available:
                return candidate
            if candidate:
                log.warning("encoder %s is not available; falling back", candidate)
        for family in ("nvenc", "qsv", "amf", "cpu"):
            candidate = HARDWARE_ENCODERS[family].get(codec)
            if candidate and candidate in self.available:
                return candidate
        return "libx264"

    def families(self) -> list[str]:
        out = ["auto"]
        for family, mapping in HARDWARE_ENCODERS.items():
            if any(name in self.available for name in mapping.values()):
                out.append(family)
        return out


def detect_encoders(ffmpeg: str) -> EncoderSupport:
    """Ask FFmpeg which encoders this build actually has."""
    result = run([ffmpeg, "-hide_banner", "-encoders"], timeout=30, log_name="ffmpeg")
    text = result.stdout + result.stderr
    wanted = {name for mapping in HARDWARE_ENCODERS.values() for name in mapping.values()}
    found = {name for name in wanted if re.search(rf"\b{re.escape(name)}\b", text)}
    log.debug("ffmpeg encoders found: %s", ", ".join(sorted(found)) or "none")
    return EncoderSupport(found)


# ---------------------------------------------------------------------------
# Command builders (pure)
# ---------------------------------------------------------------------------


def _video_quality_args(encoder: str, bitrate_kbps: int) -> list[str]:
    """Bitrate settings expressed the way each encoder family expects."""
    bitrate = f"{max(1000, int(bitrate_kbps))}k"
    maxrate = f"{int(max(1000, bitrate_kbps) * 1.5)}k"
    bufsize = f"{int(max(1000, bitrate_kbps) * 2)}k"
    if encoder.endswith("_nvenc"):
        return ["-preset", "p5", "-rc", "vbr", "-b:v", bitrate, "-maxrate", maxrate, "-bufsize", bufsize]
    if encoder.endswith("_amf"):
        return ["-quality", "balanced", "-rc", "vbr_peak", "-b:v", bitrate, "-maxrate", maxrate]
    if encoder.endswith("_qsv"):
        return ["-preset", "medium", "-b:v", bitrate, "-maxrate", maxrate]
    return ["-preset", "medium", "-b:v", bitrate, "-maxrate", maxrate, "-bufsize", bufsize]


def build_encode_command(
    ffmpeg: str,
    input_path: str,
    output_path: str,
    settings: VideoSettings,
    encoder: str = "libx264",
    start_seconds: float | None = None,
    duration_seconds: float | None = None,
    audio_path: str | None = None,
) -> list[str]:
    """Encode (and optionally trim) an existing video file."""
    args: list[str] = [ffmpeg, "-hide_banner", "-y"]
    if start_seconds:
        # Before -i: fast, keyframe-accurate seeking.
        args += ["-ss", f"{max(0.0, start_seconds):.3f}"]
    args += ["-i", str(input_path)]
    if audio_path:
        args += ["-i", str(audio_path)]
    if duration_seconds:
        args += ["-t", f"{max(0.1, duration_seconds):.3f}"]

    filters = [f"scale={settings.width}:{settings.height}:flags=lanczos", f"fps={settings.fps}"]
    args += ["-vf", ",".join(filters)]
    args += ["-c:v", encoder, *_video_quality_args(encoder, settings.bitrate_kbps), "-pix_fmt", "yuv420p"]

    if settings.game_audio or settings.voice_audio:
        args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
        if abs(settings.volume - 1.0) > 0.01:
            args += ["-af", f"volume={max(0.0, settings.volume):.2f}"]
        if audio_path:
            args += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    else:
        args += ["-an"]

    args += ["-movflags", "+faststart", str(output_path)]
    return args


def build_image_sequence_command(
    ffmpeg: str,
    pattern: str,
    output_path: str,
    settings: VideoSettings,
    encoder: str = "libx264",
    audio_path: str | None = None,
    start_number: int = 0,
) -> list[str]:
    """Encode a TGA/PNG frame dump (what CS2 and HLAE actually write)."""
    args: list[str] = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-framerate",
        str(int(settings.fps)),
        "-start_number",
        str(int(start_number)),
        "-i",
        str(pattern),
    ]
    if audio_path:
        args += ["-i", str(audio_path)]
    args += ["-vf", f"scale={settings.width}:{settings.height}:flags=lanczos"]
    args += ["-c:v", encoder, *_video_quality_args(encoder, settings.bitrate_kbps), "-pix_fmt", "yuv420p"]
    args += ["-r", str(int(settings.fps))]
    if audio_path:
        args += ["-c:a", "aac", "-b:a", "192k", "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
        if abs(settings.volume - 1.0) > 0.01:
            args += ["-af", f"volume={max(0.0, settings.volume):.2f}"]
    else:
        args += ["-an"]
    args += ["-movflags", "+faststart", str(output_path)]
    return args


def build_concat_command(
    ffmpeg: str,
    list_file: str,
    output_path: str,
    settings: VideoSettings,
    encoder: str = "libx264",
    reencode: bool = True,
) -> list[str]:
    """Join clips listed in a concat-demuxer file."""
    args = [ffmpeg, "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file)]
    if reencode:
        args += ["-vf", f"scale={settings.width}:{settings.height}:flags=lanczos,fps={settings.fps}"]
        args += ["-c:v", encoder, *_video_quality_args(encoder, settings.bitrate_kbps), "-pix_fmt", "yuv420p"]
        args += ["-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-c", "copy"]
    args += ["-movflags", "+faststart", str(output_path)]
    return args


def build_screen_capture_command(
    ffmpeg: str,
    output_path: str,
    settings: VideoSettings,
    window_title: str | None = None,
    audio_device: str | None = None,
    duration_seconds: float | None = None,
    method: str = "gdigrab",
    monitor_index: int = 0,
) -> list[str]:
    """Windows screen/window capture (the FFmpeg fallback recorder).

    Two capture methods, because ``gdigrab`` is not enough for CS2:

    * ``gdigrab`` reads the window through GDI ``BitBlt``. GDI never sees a
      Direct3D-rendered surface, so CS2 comes out as a **black rectangle with
      sound** — the classic symptom this recorder used to produce.
    * ``ddagrab`` uses the DXGI Desktop Duplication API (FFmpeg 6.0+, Windows 8+).
      It copies the *composited* desktop, Direct3D and all, so the game image is
      actually captured. It grabs a whole monitor rather than a single window,
      which is why borderless/fullscreen is recommended, and it hands frames off
      on the GPU — hence the ``hwdownload`` back to system memory before encoding.
    """
    method = (method or "gdigrab").lower()
    if method == "ddagrab":
        return _build_ddagrab_command(
            ffmpeg, output_path, settings, audio_device, duration_seconds, monitor_index
        )

    args = [ffmpeg, "-hide_banner", "-y", "-f", "gdigrab", "-framerate", str(int(settings.fps))]
    if settings.width and settings.height and not window_title:
        args += ["-video_size", f"{settings.width}x{settings.height}"]
    args += ["-i", f"title={window_title}" if window_title else "desktop"]
    if audio_device:
        args += ["-f", "dshow", "-i", f"audio={audio_device}"]
    if duration_seconds:
        args += ["-t", f"{max(0.5, duration_seconds):.2f}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p"]
    if audio_device:
        args += ["-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-an"]
    args += [str(output_path)]
    return args


def _build_ddagrab_command(
    ffmpeg: str,
    output_path: str,
    settings: VideoSettings,
    audio_device: str | None,
    duration_seconds: float | None,
    monitor_index: int,
) -> list[str]:
    """DXGI Desktop Duplication capture that can see the Direct3D image."""
    args = [ffmpeg, "-hide_banner", "-y", "-init_hw_device", "d3d11va"]
    # A dshow audio input, if any, becomes input 0 (ddagrab is a source filter
    # and takes no ``-i`` of its own).
    if audio_device:
        args += ["-f", "dshow", "-i", f"audio={audio_device}"]
    filtergraph = f"ddagrab=output_idx={int(monitor_index)}:framerate={int(settings.fps)},hwdownload,format=bgra"
    args += ["-filter_complex", f"{filtergraph}[v]", "-map", "[v]"]
    if audio_device:
        args += ["-map", "0:a"]
    if duration_seconds:
        args += ["-t", f"{max(0.5, duration_seconds):.2f}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p"]
    if audio_device:
        args += ["-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-an"]
    args += [str(output_path)]
    return args


def write_concat_list(paths: Sequence[str], list_path: str | os.PathLike[str]) -> Path:
    """Write a concat-demuxer list, escaping quotes the way FFmpeg wants."""
    target = Path(list_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for path in paths:
        escaped = str(Path(path).resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Running FFmpeg
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")


def parse_progress(line: str, total_seconds: float) -> float | None:
    """Turn an FFmpeg status line into a 0..1 fraction."""
    match = _TIME_RE.search(line)
    if not match or total_seconds <= 0:
        return None
    hours, minutes, seconds = match.groups()
    elapsed = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return max(0.0, min(1.0, elapsed / total_seconds))


class FFmpeg:
    """Thin runner that reports progress and turns failures into AppErrors."""

    def __init__(self, executable: str | None = None, ffprobe: str | None = None) -> None:
        self.executable = executable or find_ffmpeg() or ""
        self.ffprobe = ffprobe or find_ffprobe(ffmpeg_path=self.executable) or ""
        self._encoders: EncoderSupport | None = None

    # -- info ------------------------------------------------------------
    @property
    def available(self) -> bool:
        return bool(self.executable) and Path(self.executable).is_file()

    def ensure(self) -> str:
        if not self.available:
            raise ffmpeg_not_found()
        return self.executable

    def encoders(self) -> EncoderSupport:
        if self._encoders is None:
            self._encoders = detect_encoders(self.ensure()) if self.available else EncoderSupport(set())
        return self._encoders

    def encoder_for(self, settings: VideoSettings) -> str:
        return self.encoders().encoder_for(settings.codec, settings.encoder)

    def probe_duration(self, path: str | os.PathLike[str]) -> float | None:
        if not self.ffprobe:
            return None
        result = run(
            [
                self.ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            timeout=30,
            log_name="ffmpeg",
        )
        try:
            return float(result.stdout.strip())
        except (TypeError, ValueError):
            return None

    # -- execution -------------------------------------------------------
    def execute(
        self,
        args: Sequence[str],
        total_seconds: float = 0.0,
        on_progress: Callable[[float], None] | None = None,
        cancel: Callable[[], bool] | None = None,
        what: str = "encode",
    ) -> CommandResult:
        self.ensure()

        def handle(line: str) -> None:
            if on_progress and total_seconds:
                fraction = parse_progress(line, total_seconds)
                if fraction is not None:
                    on_progress(fraction)

        result = stream(args, on_line=handle, should_cancel=cancel, log_name="ffmpeg")
        if not result.ok:
            raise FFmpegError(
                title=f"FFmpeg failed to {what} the clip.",
                reasons=[
                    "The selected encoder is not supported by your GPU/driver",
                    "The recording produced no usable frames",
                    "The output folder is not writable",
                ],
                actions=["Switch the encoder to CPU in Settings", "Open the logs folder", "Retry"],
                detail=_tail(result.stderr or result.stdout),
            )
        return result

    def encode(
        self,
        input_path: str,
        output_path: str,
        settings: VideoSettings,
        start_seconds: float | None = None,
        duration_seconds: float | None = None,
        audio_path: str | None = None,
        on_progress: Callable[[float], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> Path:
        args = build_encode_command(
            self.ensure(),
            input_path,
            output_path,
            settings,
            encoder=self.encoder_for(settings),
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
            audio_path=audio_path,
        )
        total = duration_seconds or self.probe_duration(input_path) or 0.0
        self.execute(args, total_seconds=total, on_progress=on_progress, cancel=cancel)
        return Path(output_path)

    def encode_image_sequence(
        self,
        pattern: str,
        output_path: str,
        settings: VideoSettings,
        audio_path: str | None = None,
        frame_count: int = 0,
        start_number: int = 0,
        on_progress: Callable[[float], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> Path:
        args = build_image_sequence_command(
            self.ensure(),
            pattern,
            output_path,
            settings,
            encoder=self.encoder_for(settings),
            audio_path=audio_path,
            start_number=start_number,
        )
        total = (frame_count / settings.fps) if frame_count and settings.fps else 0.0
        self.execute(args, total_seconds=total, on_progress=on_progress, cancel=cancel, what="encode the frames of")
        return Path(output_path)

    def concat(
        self,
        clips: Sequence[str],
        output_path: str,
        settings: VideoSettings,
        work_dir: str | os.PathLike[str] | None = None,
        on_progress: Callable[[float], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> Path:
        if not clips:
            raise FFmpegError(title="Select at least one clip for the montage.")
        directory = Path(work_dir or Path(output_path).parent)
        list_file = write_concat_list(clips, directory / "montage_concat.txt")
        total = sum(self.probe_duration(clip) or 0.0 for clip in clips)
        args = build_concat_command(
            self.ensure(), str(list_file), output_path, settings, encoder=self.encoder_for(settings)
        )
        try:
            self.execute(args, total_seconds=total, on_progress=on_progress, cancel=cancel, what="join")
        finally:
            list_file.unlink(missing_ok=True)
        return Path(output_path)


def _tail(text: str, lines: int = 12) -> str:
    return "\n".join((text or "").strip().splitlines()[-lines:])


def raise_if_cancelled(cancel: Callable[[], bool] | None) -> None:
    if cancel is not None and cancel():
        raise Cancelled()
