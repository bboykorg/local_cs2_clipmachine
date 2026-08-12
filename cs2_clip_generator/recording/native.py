"""CS2's own frame dumper: ``startmovie`` / ``endmovie``.

No third-party software at all. ``host_framerate <fps>`` detaches the simulation
from real time so the engine can write every single frame, then ``startmovie``
dumps them:

* TGA frames land in ``game/csgo/<searchpath>/movie/<name>NNNN.tga``
* the soundtrack lands in ``game/csgo/movie/<name>.wav``

Both are then handed to FFmpeg. The trade-off is speed and disk: an uncompressed
1080p TGA is about 6 MB, so ten seconds at 60 fps is roughly 3.5 GB of frames
before encoding. That is why the pipeline checks free space first and why this
recorder is the fallback rather than the default.

Requires ``sv_cheats 1`` (the playback presentation sets it) and, on current
builds, the CS2 server plugin — Valve hid ``startmovie`` from the console, and
the plugin un-hides it. Availability reflects that honestly.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from ..core.errors import RecorderError
from ..core.logger import get_logger
from ..cs2 import demo_controller, plugin
from ..cs2.controller import RecorderHooks
from ..cs2.launcher import cs2_game_dir
from ..utils.filesystem import free_space_mb
from .base import Recorder, RecordingContext

log = get_logger("recorder")

#: Rough size of one uncompressed 1080p TGA frame, in megabytes.
TGA_FRAME_MB = 6.0


class NativeStartMovieRecorder(Recorder):
    name = "native"
    in_game = True

    def available(self, cs2_executable: str = "") -> tuple[bool, str]:
        game_dir = cs2_game_dir(cs2_executable)
        if game_dir is None:
            return False, "CS2 installation not found"
        if not self.ffmpeg.available:
            return False, "FFmpeg is required to encode the frames CS2 writes"
        status = plugin.plugin_status(cs2_executable)
        if not status.usable:
            return (
                True,
                "Available, but 'startmovie' is hidden on current CS2 builds unless the server plugin is enabled",
            )
        return True, "CS2 startmovie + FFmpeg"

    # -- driving ---------------------------------------------------------
    def hooks(self, context: RecordingContext) -> RecorderHooks:
        name = _movie_name(context.clip_name)
        context.metadata["movie_name"] = name
        return RecorderHooks(
            start_commands=demo_controller.start_movie(name, context.video.fps),
            stop_commands=demo_controller.end_movie(),
            # host_framerate makes playback slower than real time.
            real_time_playback=False,
        )

    def begin_session(self, context: RecordingContext) -> None:
        frames = context.expected_duration * context.video.fps
        needed_mb = frames * TGA_FRAME_MB * (context.video.width * context.video.height) / (1920 * 1080)
        free_mb = free_space_mb(cs2_game_dir(context.cs2_executable) or context.work_dir)
        log.info("startmovie needs roughly %.0f MB of raw frames (%.0f MB free)", needed_mb, free_mb)
        if free_mb < needed_mb:
            raise RecorderError(
                title="Not enough disk space for CS2's frame dump.",
                reasons=[
                    f"About {needed_mb:.0f} MB of uncompressed frames are needed, {free_mb:.0f} MB are free",
                    "CS2 writes raw TGA frames next to the game files, not to the output folder",
                ],
                actions=["Free up space on the CS2 drive", "Lower the resolution or FPS", "Use OBS or HLAE instead"],
            )

    # -- output ----------------------------------------------------------
    def finalise(self, context: RecordingContext, on_progress=None, cancel=None) -> Path:  # noqa: ANN001
        name = str(context.metadata.get("movie_name") or _movie_name(context.clip_name))
        game_dir = cs2_game_dir(context.cs2_executable)
        if game_dir is None:
            raise RecorderError(title="CS2 installation not found while collecting the recording.")

        frames, wav = _find_raw_files(game_dir, name)
        if not frames:
            raise RecorderError(
                title="CS2 did not write any frames.",
                reasons=[
                    "The 'startmovie' command was blocked (it is hidden without the server plugin)",
                    "sv_cheats was not enabled",
                    "Playback never reached the highlight",
                ],
                actions=["Enable the CS2 server plugin in Settings", "Try the OBS recorder", "Open the logs folder"],
            )

        pattern, start_number = _frame_pattern(frames, name)
        log.info("encoding %d frames from %s", len(frames), pattern)
        self.ffmpeg.encode_image_sequence(
            pattern=pattern,
            output_path=str(context.output_path),
            settings=context.video,
            audio_path=str(wav) if wav and (context.video.game_audio or context.video.voice_audio) else None,
            frame_count=len(frames),
            start_number=start_number,
            on_progress=on_progress,
            cancel=cancel,
        )
        context.metadata["raw_frames"] = [str(frame) for frame in frames]
        context.metadata["raw_wav"] = str(wav) if wav else ""
        return context.output_path

    def cleanup(self, context: RecordingContext) -> None:
        """Delete the raw frames — and only those.

        The frames live in the game's own ``movie`` folder, which may hold other
        recordings and is expected to keep existing for the next clip, so the
        files are removed one by one instead of removing the directory.
        """
        for path in context.metadata.get("raw_frames", []) or []:
            try:
                Path(str(path)).unlink(missing_ok=True)
            except OSError:
                continue
        wav = context.metadata.get("raw_wav")
        if wav:
            try:
                Path(str(wav)).unlink(missing_ok=True)
            except OSError:
                pass


def _movie_name(clip_name: str) -> str:
    """CS2 uses the movie name as a file name; keep it boring and unique."""
    safe = re.sub(r"[^A-Za-z0-9_]", "_", clip_name)[:40].strip("_") or "clip"
    return f"{safe}_{int(time.time())}"


def _find_raw_files(game_dir: Path, name: str) -> tuple[list[Path], Path | None]:
    """Locate the TGA sequence and the WAV that ``startmovie`` produced.

    The frames follow the engine's search path, so they may be under
    ``movie/`` or inside a plugin folder such as ``csdm/movie/``; the audio has
    lived in ``csgo/movie`` since the Armory update. Both are searched.
    """
    frames = sorted(game_dir.glob(f"**/{name}*.tga"))
    wav_candidates = [
        game_dir / "movie" / f"{name}.wav",
        game_dir / "movie" / f"{name}.WAV",
        *sorted(game_dir.glob(f"**/{name}*.wav")),
    ]
    wav = next((path for path in wav_candidates if path.is_file()), None)
    return frames, wav


def _frame_pattern(frames: list[Path], name: str = "") -> tuple[str, int]:
    """Turn ``clip0000.tga, clip0001.tga`` into ``clip%04d.tga`` + first index.

    The movie name is passed in whenever the caller knows it, because guessing
    where the name ends and the frame counter begins is genuinely ambiguous: a
    name that itself ends in digits (``clip_1786443959``) would otherwise swallow
    the counter and produce a pattern FFmpeg cannot open.
    """
    first = frames[0]
    stem = first.stem

    digits: str | None = None
    prefix = ""
    if name and stem.startswith(name) and stem[len(name) :].isdigit():
        prefix, digits = name, stem[len(name) :]
    else:
        match = re.match(r"^(.*?)(\d+)$", stem)
        if match:
            prefix, digits = match.groups()

    if not digits:
        return str(first.parent / f"*{first.suffix}"), 0
    pattern = str(first.parent / f"{prefix}%0{len(digits)}d{first.suffix}")
    return pattern, int(digits)


def native_recorder_hint() -> str:
    if os.name != "nt":
        return "CS2's startmovie also works on Linux, but the paths differ from the Windows layout."
    return ""
