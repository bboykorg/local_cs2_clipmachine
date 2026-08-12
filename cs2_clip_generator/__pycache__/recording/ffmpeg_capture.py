"""FFmpeg screen-capture recorder — the "works with nothing installed" option.

``-f gdigrab -i title=Counter-Strike 2`` captures the game window directly on
Windows. It needs no plugin, no OBS and no HLAE, which makes it the honest
default when nothing else is configured. The limits are real and reported rather
than hidden: capture happens in real time (so a 20 second clip takes 20
seconds), the window must be visible, and desktop audio needs a DirectShow
device such as VB-Cable, because Windows has no default loopback input.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from ..core.errors import RecorderError
from ..core.logger import get_logger
from ..cs2.controller import RecorderHooks
from ..utils.process import no_window_kwargs, run, terminate
from ..video.ffmpeg import build_screen_capture_command
from .base import Recorder, RecordingContext

log = get_logger("recorder")

CS2_WINDOW_TITLE = "Counter-Strike 2"


class FFmpegScreenRecorder(Recorder):
    name = "ffmpeg"
    in_game = False

    def __init__(self, settings, ffmpeg=None) -> None:  # noqa: ANN001 - see base class
        super().__init__(settings, ffmpeg)
        self._process: subprocess.Popen | None = None
        self._raw_path: Path | None = None

    def available(self, cs2_executable: str = "") -> tuple[bool, str]:
        del cs2_executable
        if not self.ffmpeg.available:
            return False, "FFmpeg was not found"
        if os.name != "nt":
            return False, "Window capture with gdigrab is Windows-only"
        return True, "FFmpeg window capture (gdigrab)"

    # -- driving ---------------------------------------------------------
    def hooks(self, context: RecordingContext) -> RecorderHooks:
        raw = context.work_dir / f"{context.clip_name}_capture.mp4"
        self._ensure_dir(context.work_dir)
        self._raw_path = raw
        context.metadata["capture_raw"] = str(raw)

        audio_device = self.audio_device()
        # Record generously: playback needs a moment to settle and the tail is
        # trimmed by FFmpeg during finalise().
        duration = context.expected_duration + context.safety_margin * 2

        args = build_screen_capture_command(
            self.ffmpeg.ensure(),
            output_path=str(raw),
            settings=context.video,
            window_title=CS2_WINDOW_TITLE,
            audio_device=audio_device,
            duration_seconds=duration,
        )

        def start() -> None:
            log.info("starting window capture -> %s", raw.name)
            try:
                self._process = subprocess.Popen(  # noqa: S603 - argument list, no shell
                    args,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **no_window_kwargs(),
                )
            except OSError as exc:
                raise RecorderError(
                    title="Screen capture could not be started.",
                    reasons=["FFmpeg could not open the CS2 window", "The CS2 window is minimised"],
                    actions=["Keep the CS2 window visible", "Choose another recorder in Settings"],
                    detail=str(exc),
                ) from exc

        def stop() -> None:
            process = self._process
            self._process = None
            if process is None:
                return
            # 'q' on stdin makes FFmpeg close the file cleanly.
            try:
                if process.stdin:
                    process.stdin.write(b"q")
                    process.stdin.flush()
            except OSError:
                pass
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and process.poll() is None:
                time.sleep(0.2)
            if process.poll() is None:
                terminate(process)

        return RecorderHooks(on_start=start, on_stop=stop, real_time_playback=True)

    # -- output ----------------------------------------------------------
    def finalise(self, context: RecordingContext, on_progress=None, cancel=None) -> Path:  # noqa: ANN001
        raw = Path(str(context.metadata.get("capture_raw") or ""))
        if not raw.is_file() or raw.stat().st_size == 0:
            raise RecorderError(
                title="The screen capture produced no video.",
                reasons=[
                    "The CS2 window was not found (is the game running?)",
                    "The window was minimised during recording",
                ],
                actions=["Keep CS2 visible while rendering", "Try OBS instead", "Open the logs folder"],
            )
        self.ffmpeg.encode(
            input_path=str(raw),
            output_path=str(context.output_path),
            settings=context.video,
            start_seconds=context.safety_margin or None,
            duration_seconds=context.expected_duration,
            on_progress=on_progress,
            cancel=cancel,
        )
        return context.output_path

    def cleanup(self, context: RecordingContext) -> None:
        raw = context.metadata.get("capture_raw")
        if raw and Path(str(raw)).is_file():
            try:
                Path(str(raw)).unlink()
            except OSError:
                pass

    # -- audio -----------------------------------------------------------
    def audio_device(self) -> str | None:
        """First DirectShow audio device, if the user wants sound."""
        if not (self.settings and (getattr(self.settings, "capture_audio_device", "") or "")):
            device = self.detect_audio_device()
        else:
            device = getattr(self.settings, "capture_audio_device", "")
        return device

    def detect_audio_device(self) -> str | None:  # pragma: no cover - Windows only
        if os.name != "nt" or not self.ffmpeg.available:
            return None
        result = run(
            [self.ffmpeg.executable, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            timeout=20,
            log_name="ffmpeg",
        )
        text = result.stderr + result.stdout
        devices: list[str] = []
        for line in text.splitlines():
            if '"' in line and "audio" in line.lower():
                name = line.split('"')[1]
                devices.append(name)
        for name in devices:
            if any(token in name.lower() for token in ("cable", "loopback", "stereo mix", "what you hear")):
                return name
        return devices[0] if devices else None
