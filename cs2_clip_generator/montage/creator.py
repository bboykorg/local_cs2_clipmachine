"""Montage: glue finished clips into one video.

Deliberately not a video editor. Three things are supported because they are the
three things people actually want, and each is a real FFmpeg operation:

* **order** — the clips play in the order the user arranged them,
* **transitions** — a hard cut, or a cross-fade built with the ``xfade`` filter,
* **intro/outro/music** — an image or video bumper, and a background track that
  is mixed under the game audio and faded out at the end.

Everything is re-encoded once to a common format first, because concatenating
files that disagree about resolution, frame rate or audio layout is the classic
way to end up with a montage that plays for three seconds and then freezes.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import Cancelled, FFmpegError
from ..core.logger import get_logger
from ..core.models import VideoSettings
from ..utils.filesystem import unique_path
from ..video.ffmpeg import FFmpeg, write_concat_list

log = get_logger("ffmpeg")

ProgressCallback = Callable[[float, str], None]
CancelCallback = Callable[[], bool]


@dataclass
class MontageSettings:
    transition: str = "none"  # none | fade
    transition_seconds: float = 0.5
    intro_path: str = ""
    outro_path: str = ""
    intro_seconds: float = 3.0
    outro_seconds: float = 3.0
    music_path: str = ""
    music_volume: float = 0.35
    keep_game_audio: bool = True
    video: VideoSettings = field(default_factory=VideoSettings)


class MontageCreator:
    def __init__(self, ffmpeg: FFmpeg | None = None) -> None:
        self.ffmpeg = ffmpeg or FFmpeg()

    # -- helpers ---------------------------------------------------------
    def _normalise(
        self,
        source: str,
        target: Path,
        settings: MontageSettings,
        duration: float | None = None,
        still_image: bool = False,
        cancel: CancelCallback | None = None,
    ) -> Path:
        """Re-encode one input to the montage's common format."""
        ffmpeg = self.ffmpeg.ensure()
        video = settings.video
        encoder = self.ffmpeg.encoder_for(video)
        args = [ffmpeg, "-hide_banner", "-y"]
        if still_image:
            args += ["-loop", "1", "-t", f"{max(0.5, duration or 3.0):.2f}", "-i", source]
            # A still needs a silent track, otherwise concat drops the audio of
            # everything that follows.
            args += ["-f", "lavfi", "-t", f"{max(0.5, duration or 3.0):.2f}", "-i", "anullsrc=r=48000:cl=stereo"]
        else:
            args += ["-i", source]
        args += [
            "-vf",
            f"scale={video.width}:{video.height}:force_original_aspect_ratio=decrease,"
            f"pad={video.width}:{video.height}:-1:-1:color=black,fps={video.fps},format=yuv420p",
            "-c:v",
            encoder,
            "-b:v",
            f"{max(1000, video.bitrate_kbps)}k",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
        ]
        if still_image:
            args += ["-shortest"]
        args += [str(target)]
        self.ffmpeg.execute(args, cancel=cancel, what="prepare a montage segment")
        return target

    @staticmethod
    def _is_image(path: str) -> bool:
        return Path(path).suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".webp")

    # -- main ------------------------------------------------------------
    def create(
        self,
        clips: Sequence[str],
        output_path: str | os.PathLike[str],
        settings: MontageSettings | None = None,
        work_dir: str | os.PathLike[str] | None = None,
        on_progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
    ) -> Path:
        settings = settings or MontageSettings()
        if not clips:
            raise FFmpegError(
                title="No clips selected for the montage.",
                actions=["Tick at least two clips in the Montage page"],
            )
        missing = [clip for clip in clips if not Path(clip).is_file()]
        if missing:
            raise FFmpegError(
                title="Some clips no longer exist.",
                reasons=[f"Missing: {os.path.basename(missing[0])}"],
                actions=["Re-render the missing clips"],
            )

        output = unique_path(Path(output_path))
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = Path(work_dir or output.parent) / f".montage_{output.stem}"
        temp.mkdir(parents=True, exist_ok=True)

        try:
            segments: list[Path] = []
            inputs: list[tuple[str, bool, float | None]] = []
            if settings.intro_path:
                inputs.append((settings.intro_path, self._is_image(settings.intro_path), settings.intro_seconds))
            inputs += [(clip, False, None) for clip in clips]
            if settings.outro_path:
                inputs.append((settings.outro_path, self._is_image(settings.outro_path), settings.outro_seconds))

            for index, (source, still, duration) in enumerate(inputs):
                if cancel is not None and cancel():
                    raise Cancelled()
                if on_progress:
                    on_progress(0.05 + 0.55 * index / max(1, len(inputs)), f"Preparing segment {index + 1}")
                segments.append(
                    self._normalise(source, temp / f"{index:03d}.mp4", settings, duration, still, cancel)
                )

            if on_progress:
                on_progress(0.65, "Joining clips")
            joined = temp / "joined.mp4"
            if settings.transition == "fade" and len(segments) > 1:
                self._join_with_fade(segments, joined, settings, cancel)
            else:
                list_file = write_concat_list([str(s) for s in segments], temp / "concat.txt")
                args = [
                    self.ffmpeg.ensure(),
                    "-hide_banner",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_file),
                    "-c",
                    "copy",
                    str(joined),
                ]
                self.ffmpeg.execute(args, cancel=cancel, what="join the clips of")

            if settings.music_path and Path(settings.music_path).is_file():
                if on_progress:
                    on_progress(0.85, "Mixing music")
                self._add_music(joined, output, settings, cancel)
            else:
                joined.replace(output)

            if on_progress:
                on_progress(1.0, "Montage complete")
            log.info("montage written to %s", output)
            return output
        finally:
            for leftover in temp.glob("*"):
                try:
                    leftover.unlink()
                except OSError:
                    pass
            try:
                temp.rmdir()
            except OSError:
                pass

    # -- transitions -----------------------------------------------------
    def _join_with_fade(
        self,
        segments: Sequence[Path],
        output: Path,
        settings: MontageSettings,
        cancel: CancelCallback | None,
    ) -> Path:
        """Cross-fade consecutive segments with the ``xfade`` filter.

        ``xfade`` needs each transition to overlap the previous output, so the
        offsets accumulate: segment *n* starts fading in ``sum(durations) -
        n * transition`` seconds into the growing timeline.
        """
        durations = [self.ffmpeg.probe_duration(str(path)) or 0.0 for path in segments]
        if any(duration <= 0 for duration in durations):
            log.warning("a segment has unknown duration; falling back to hard cuts")
            list_file = write_concat_list([str(s) for s in segments], output.parent / "concat.txt")
            args = [
                self.ffmpeg.ensure(), "-hide_banner", "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_file), "-c", "copy", str(output),
            ]
            self.ffmpeg.execute(args, cancel=cancel, what="join the clips of")
            return output

        transition = max(0.1, min(settings.transition_seconds, min(durations) / 2))
        args = [self.ffmpeg.ensure(), "-hide_banner", "-y"]
        for path in segments:
            args += ["-i", str(path)]

        filters: list[str] = []
        video_label = "0:v"
        audio_label = "0:a"
        offset = durations[0]
        for index in range(1, len(segments)):
            next_video, next_audio = f"v{index}", f"a{index}"
            offset_at = max(0.0, offset - transition)
            filters.append(
                f"[{video_label}][{index}:v]xfade=transition=fade:duration={transition:.2f}"
                f":offset={offset_at:.3f}[{next_video}]"
            )
            filters.append(
                f"[{audio_label}][{index}:a]acrossfade=d={transition:.2f}[{next_audio}]"
            )
            video_label, audio_label = next_video, next_audio
            offset = offset_at + durations[index]

        encoder = self.ffmpeg.encoder_for(settings.video)
        args += [
            "-filter_complex",
            ";".join(filters),
            "-map",
            f"[{video_label}]",
            "-map",
            f"[{audio_label}]",
            "-c:v",
            encoder,
            "-b:v",
            f"{max(1000, settings.video.bitrate_kbps)}k",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output),
        ]
        self.ffmpeg.execute(args, cancel=cancel, what="cross-fade the clips of")
        return output

    # -- music -----------------------------------------------------------
    def _add_music(
        self,
        source: Path,
        output: Path,
        settings: MontageSettings,
        cancel: CancelCallback | None,
    ) -> Path:
        duration = self.ffmpeg.probe_duration(str(source)) or 0.0
        fade_out_start = max(0.0, duration - 2.0)
        music_filter = f"volume={max(0.0, settings.music_volume):.2f}"
        if duration:
            music_filter += f",afade=t=out:st={fade_out_start:.2f}:d=2"

        args = [self.ffmpeg.ensure(), "-hide_banner", "-y", "-i", str(source), "-i", settings.music_path]
        if settings.keep_game_audio:
            args += [
                "-filter_complex",
                f"[1:a]{music_filter}[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                "-map",
                "0:v",
                "-map",
                "[aout]",
            ]
        else:
            args += ["-filter_complex", f"[1:a]{music_filter}[aout]", "-map", "0:v", "-map", "[aout]", "-shortest"]
        args += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(output)]
        self.ffmpeg.execute(args, cancel=cancel, what="add music to")
        return output


def default_montage_name(match_name: str, player: str) -> str:
    from ..utils.filesystem import sanitize_filename

    return f"Montage_{sanitize_filename(match_name, 'match')}_{sanitize_filename(player, 'player')}.mp4"
