"""HLAE (Half-Life Advanced Effects) recorder.

HLAE launches CS2 through its own loader and injects ``mirv_*`` commands. For
recording, ``mirv_streams`` is far better behaved than the engine's own
``startmovie``: it writes a take folder per clip and can hand frames straight to
FFmpeg through a preset, skipping the terabyte of TGA files.

    mirv_streams record name "<folder>"
    mirv_streams record fps 60
    mirv_streams settings add ffmpeg csdmPreset "-c:v libx264 ... {QUOTE}out.mp4{QUOTE}"
    mirv_streams record screen settings csdmPreset
    mirv_streams record start
    ...
    mirv_streams record end

HLAE is not bundled and is never downloaded automatically: the user points at
their own ``HLAE.exe`` in Settings. Without it this recorder simply reports that
it is unavailable.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core.errors import RecorderError
from ..core.logger import get_logger
from ..cs2 import demo_controller
from ..cs2.controller import RecorderHooks
from .base import Recorder, RecordingContext

log = get_logger("recorder")

PRESET_NAME = "cs2clipPreset"


class HLAERecorder(Recorder):
    name = "hlae"
    in_game = True

    def available(self, cs2_executable: str = "") -> tuple[bool, str]:
        del cs2_executable
        executable = self.settings_hlae_path()
        if not executable:
            return False, "HLAE.exe is not configured"
        if not Path(executable).is_file():
            return False, f"HLAE not found at {executable}"
        if not self.ffmpeg.available:
            return False, "FFmpeg is required alongside HLAE"
        return True, "HLAE mirv_streams + FFmpeg"

    def settings_hlae_path(self) -> str:
        return getattr(self.settings, "hlae_executable", "") or ""

    # -- launching -------------------------------------------------------
    def launch_wrapper(self, cs2_executable: str) -> list[str]:
        """HLAE starts the game itself, so CS2's command line goes through it.

        ``-customLoader`` is what allows HLAE to attach to CS2; ``-noGui
        -autoStart`` keep the launcher out of the way.
        """
        executable = self.settings_hlae_path()
        if not executable:
            return []
        return [
            executable,
            "-customLoader",
            "-noGui",
            "-autoStart",
            "-csgoLauncher",
            "-gameExe",
            cs2_executable,
        ]

    # -- driving ---------------------------------------------------------
    def hooks(self, context: RecordingContext) -> RecorderHooks:
        take_dir = self._ensure_dir(context.work_dir / f"hlae_{context.clip_name}")
        context.metadata["hlae_dir"] = str(take_dir)

        encoder = self.ffmpeg.encoder_for(context.video)
        target = take_dir / "video.mp4"
        context.metadata["hlae_video"] = str(target)
        parameters = (
            f"-c:v {encoder} -pix_fmt yuv420p "
            f"-b:v {max(1000, context.video.bitrate_kbps)}k -r {context.video.fps}"
        )

        start = [
            demo_controller.hlae_ffmpeg_preset(PRESET_NAME, parameters, str(target).replace("\\", "\\\\")),
            *demo_controller.hlae_record_start(
                output_folder=str(take_dir),
                fps=context.video.fps,
                record_audio=context.video.game_audio or context.video.voice_audio,
                preset=PRESET_NAME,
            ),
        ]
        return RecorderHooks(
            start_commands=start,
            stop_commands=demo_controller.hlae_record_end(),
            real_time_playback=False,
        )

    # -- output ----------------------------------------------------------
    def finalise(self, context: RecordingContext, on_progress=None, cancel=None) -> Path:  # noqa: ANN001
        take_dir = Path(str(context.metadata.get("hlae_dir") or context.work_dir))
        produced = Path(str(context.metadata.get("hlae_video") or ""))

        if produced.is_file() and produced.stat().st_size > 0:
            log.info("HLAE produced %s directly", produced.name)
            self.ffmpeg.encode(
                input_path=str(produced),
                output_path=str(context.output_path),
                settings=context.video,
                on_progress=on_progress,
                cancel=cancel,
            )
            return context.output_path

        frames = sorted(take_dir.glob("take*/*.tga")) or sorted(take_dir.glob("**/*.tga"))
        if not frames:
            raise RecorderError(
                title="HLAE did not record anything.",
                reasons=[
                    "HLAE did not attach to CS2 (the custom loader may be blocked)",
                    "mirv_streams commands were not executed — tick scheduling requires the CS2 plugin",
                    "The HLAE version does not match this CS2 build",
                ],
                actions=["Update HLAE", "Try the OBS recorder", "Open the logs folder"],
            )
        wav = next(iter(sorted(take_dir.glob("take*/audio.wav")) or sorted(take_dir.glob("**/audio.wav"))), None)
        from .native import _frame_pattern  # shared TGA sequence handling

        pattern, start_number = _frame_pattern(frames)
        self.ffmpeg.encode_image_sequence(
            pattern=pattern,
            output_path=str(context.output_path),
            settings=context.video,
            audio_path=str(wav) if wav else None,
            frame_count=len(frames),
            start_number=start_number,
            on_progress=on_progress,
            cancel=cancel,
        )
        return context.output_path

    def cleanup(self, context: RecordingContext) -> None:
        take_dir = context.metadata.get("hlae_dir")
        if take_dir and Path(str(take_dir)).is_dir():
            shutil.rmtree(str(take_dir), ignore_errors=True)
