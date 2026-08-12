"""The render pipeline: highlight in, MP4 out.

This is where the promise of the whole application is kept::

    highlight ──► ClipPlan (demo, tick range, POV slot)
              ──► CS2 launched and seeked to the tick
              ──► recorder driven around the interval
              ──► FFmpeg encodes / trims the raw capture
              ──► ACE_Round17_Player1.mp4 + ACE_Round17_Player1.json

Everything that can be checked before starting a 20-minute batch is checked
first: FFmpeg, CS2, the recorder, the playback backend and free disk space. A
failure at that point is a clear message; a failure later is a failed job with a
retry button, never a hung UI.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import Settings
from ..core.errors import AppError, Cancelled, not_enough_disk_space
from ..core.logger import get_logger
from ..core.models import Clip, Highlight, JobState, MatchAnalysis, Player, RenderJob
from ..cs2 import controller as controller_module
from ..cs2.controller import ClipPlan, RecorderHooks, SessionOptions
from ..cs2.demo_controller import PlaybackPresentation
from ..cs2.launcher import find_cs2_executable
from ..cs2.player_controller import CameraMode, SpectatorTarget
from ..highlights.titles import clip_filename
from ..recording.base import Recorder, RecordingContext
from ..recording.factory import get_recorder
from ..utils.filesystem import (
    estimate_video_size_mb,
    free_space_mb,
    sanitize_filename,
    unique_path,
    write_json,
)
from ..video.ffmpeg import FFmpeg

log = get_logger("app")

ProgressCallback = Callable[[RenderJob, float, str], None]
CancelCallback = Callable[[], bool]


@dataclass
class PipelineReport:
    """What actually happened, for the UI and the logs."""

    clips: list[Clip] = field(default_factory=list)
    failed: list[tuple[RenderJob, str]] = field(default_factory=list)
    recorder_name: str = ""
    playback_name: str = ""
    notes: list[str] = field(default_factory=list)


def output_dir_for(settings: Settings, analysis: MatchAnalysis, player_name: str) -> Path:
    """``CS2Clips/<match>/<player>/`` — one folder per player per match."""
    base = Path(settings.paths.output_dir)
    match = sanitize_filename(analysis.match_name, fallback="match")
    player = sanitize_filename(player_name, fallback="player", max_length=48)
    return base / match / player


def build_job(
    highlight: Highlight,
    analysis: MatchAnalysis,
    settings: Settings,
    job_id: str | None = None,
) -> RenderJob:
    """Turn a highlight into a render job with a concrete output path."""
    directory = output_dir_for(settings, analysis, highlight.player_name)
    output = directory / clip_filename(highlight)
    return RenderJob(
        id=job_id or highlight.id,
        highlight=highlight,
        demo_path=analysis.demo_path,
        output_path=str(output),
        video=settings.video,
    )


def build_jobs(
    highlights: Sequence[Highlight], analysis: MatchAnalysis, settings: Settings
) -> list[RenderJob]:
    return [build_job(highlight, analysis, settings) for highlight in highlights]


class RenderPipeline:
    """Runs render jobs against a real CS2 instance."""

    def __init__(
        self,
        settings: Settings,
        analysis: MatchAnalysis,
        ffmpeg: FFmpeg | None = None,
        camera_mode: CameraMode = CameraMode.PLAYER_POV,
    ) -> None:
        self.settings = settings
        self.analysis = analysis
        self.ffmpeg = ffmpeg or FFmpeg(
            settings.paths.ffmpeg_executable or None, settings.paths.ffprobe_executable or None
        )
        self.camera_mode = camera_mode
        self.cs2_executable = str(
            find_cs2_executable(settings.paths.cs2_executable, settings.paths.steam_path) or ""
        )

    # -- pre-flight ------------------------------------------------------
    def preflight(self, jobs: Sequence[RenderJob]) -> list[str]:
        """Raise on anything fatal, return warnings worth showing."""
        notes: list[str] = []
        self.ffmpeg.ensure()
        if not self.cs2_executable:
            from ..core.errors import cs2_not_found

            raise cs2_not_found()

        total_seconds = sum(job.highlight.duration_seconds(self.analysis.tickrate) for job in jobs)
        needed_mb = estimate_video_size_mb(total_seconds, self.settings.video.bitrate_kbps)
        output_root = Path(self.settings.paths.output_dir)
        output_root.mkdir(parents=True, exist_ok=True)
        free_mb = free_space_mb(output_root)
        if free_mb < needed_mb:
            raise not_enough_disk_space(needed_mb, free_mb, str(output_root))
        if free_mb < needed_mb * 3:
            notes.append(
                f"Disk space is tight: about {needed_mb:.0f} MB of clips will be written and "
                f"{free_mb:.0f} MB are free."
            )

        for player in self.analysis.players:
            if player.slot is None:
                notes.append(f"No spectator slot for {player.name}; the camera may target the wrong player.")
        return notes

    # -- planning --------------------------------------------------------
    def _player_for(self, steamid: str) -> Player | None:
        return self.analysis.player(steamid)

    def _presentation(self) -> PlaybackPresentation:
        recording = self.settings.recording
        return PlaybackPresentation(
            hide_hud=recording.hide_hud,
            only_death_notices=recording.show_only_death_notices,
            player_voices=recording.player_voices and self.settings.video.voice_audio,
            extra=[line for line in (recording.extra_cfg or "").splitlines() if line.strip()],
        )

    def build_plan(self, job: RenderJob) -> ClipPlan:
        """Expand a job into the tick range CS2 will actually play.

        The safety margin is added *here*: the recording is a little longer than
        the clip on both sides and FFmpeg trims it back, which absorbs the jitter
        of starting an external recorder.
        """
        highlight = job.highlight
        tickrate = self.analysis.tickrate or 64.0
        margin_ticks = int(round(self.settings.recording.safety_margin_seconds * tickrate))
        player = self._player_for(highlight.player_steamid)
        target = SpectatorTarget.from_player(player) if player else None
        return ClipPlan(
            demo_path=job.demo_path,
            start_tick=max(1, highlight.start_tick - margin_ticks),
            end_tick=highlight.end_tick + margin_ticks,
            tickrate=tickrate,
            target=target,
            camera_mode=self.camera_mode,
            presentation=self._presentation(),
            label=highlight.title or highlight.kind.value,
        )

    def _session_options(self, recorder: Recorder) -> SessionOptions:
        recording = self.settings.recording
        return SessionOptions(
            width=self.settings.video.width,
            height=self.settings.video.height,
            display_mode=recording.display_mode,
            demo_load_timeout=recording.demo_load_timeout,
            stabilisation_seconds=recording.stabilisation_seconds,
            close_game_after=recording.close_game_after_render,
            launch_wrapper=recorder.launch_wrapper(self.cs2_executable),
        )

    # -- execution -------------------------------------------------------
    def run(
        self,
        jobs: Sequence[RenderJob],
        on_progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
    ) -> PipelineReport:
        """Render every job in one CS2 session. Returns what was produced."""
        report = PipelineReport()
        if not jobs:
            return report

        report.notes = self.preflight(jobs)
        recorder, recorder_detail = get_recorder(self.settings.recording, self.cs2_executable, self.ffmpeg)
        report.recorder_name = recorder.name
        report.notes.append(f"Recorder: {recorder.name} — {recorder_detail}")

        controller = controller_module.get_playback_controller(
            self.cs2_executable,
            preferred=self.settings.recording.playback_backend,
            options=self._session_options(recorder),
            extra_args=self.settings.recording.extra_launch_args,
        )
        controller.set_cancel_hook(cancel)
        report.playback_name = controller.name
        report.notes.append(f"Playback: {controller.name}")
        if recorder.in_game and not controller.supports_tick_scheduling:
            report.notes.append(
                f"{recorder.name} records from inside the game but the '{controller.name}' backend cannot "
                "schedule commands at exact ticks; clip boundaries may drift by a few frames."
            )

        work_dir = Path(self.settings.paths.temp_dir) / "render"
        work_dir.mkdir(parents=True, exist_ok=True)

        contexts: list[tuple[RenderJob, ClipPlan, RecorderHooks, RecordingContext]] = []
        for job in jobs:
            plan = self.build_plan(job)
            output = unique_path(Path(job.output_path))
            output.parent.mkdir(parents=True, exist_ok=True)
            job.output_path = str(output)
            context = RecordingContext(
                clip=plan,
                output_path=output,
                work_dir=work_dir,
                video=job.video,
                recording=self.settings.recording,
                cs2_executable=self.cs2_executable,
                safety_margin=self.settings.recording.safety_margin_seconds,
            )
            contexts.append((job, plan, recorder.hooks(context), context))

        started = time.monotonic()
        try:
            first_context = contexts[0][3]
            recorder.begin_session(first_context)
            controller.begin_session(
                self.analysis.demo_path, [(plan, hooks) for _job, plan, hooks, _ctx in contexts]
            )

            for job, plan, hooks, context in contexts:
                if cancel is not None and cancel():
                    raise Cancelled()
                job.attempts += 1
                job.state = JobState.RUNNING
                self._report(on_progress, job, 0.05, "Waiting for CS2 to reach the highlight")
                try:
                    controller.run_clip(plan, hooks)
                    self._report(on_progress, job, 0.6, "Encoding")
                    recorder.finalise(
                        context,
                        on_progress=lambda fraction, j=job: self._report(
                            on_progress, j, 0.6 + 0.35 * fraction, "Encoding"
                        ),
                        cancel=cancel,
                    )
                    clip = self._write_metadata(job, context)
                    job.clip = clip
                    job.state = JobState.DONE
                    job.progress = 1.0
                    report.clips.append(clip)
                    self._report(on_progress, job, 1.0, "Done")
                    recorder.cleanup(context)
                except Cancelled:
                    job.state = JobState.CANCELLED
                    raise
                except AppError as exc:
                    job.state = JobState.FAILED
                    job.error = exc.as_text()
                    report.failed.append((job, exc.title))
                    log.error("job %s failed: %s (%s)", job.id, exc.title, exc.detail)
                    self._report(on_progress, job, job.progress, exc.title)
                except Exception as exc:  # pragma: no cover - defensive
                    job.state = JobState.FAILED
                    job.error = "Unexpected error; see logs/app.log"
                    report.failed.append((job, str(exc)))
                    log.exception("job %s crashed", job.id)
        finally:
            try:
                recorder.end_session(contexts[0][3] if contexts else None)
            finally:
                controller.end_session()

        log.info(
            "render finished: %d clips, %d failed, %.1fs",
            len(report.clips),
            len(report.failed),
            time.monotonic() - started,
        )
        return report

    # -- output ----------------------------------------------------------
    def _write_metadata(self, job: RenderJob, context: RecordingContext) -> Clip:
        highlight = job.highlight
        duration = self.ffmpeg.probe_duration(context.output_path) or highlight.duration_seconds(
            self.analysis.tickrate
        )
        clip = Clip(
            highlight_id=highlight.id,
            player=highlight.player_name,
            player_steamid=highlight.player_steamid,
            round=highlight.round_number,
            type=highlight.kind.value,
            score=highlight.score,
            start_tick=highlight.start_tick,
            end_tick=highlight.end_tick,
            map=self.analysis.map_name,
            title=highlight.title,
            video=os.path.basename(str(context.output_path)),
            duration_seconds=round(float(duration), 2),
            tags=[tag.value for tag in highlight.tags],
        )
        metadata_path = Path(context.output_path).with_suffix(".json")
        payload = clip.to_dict()
        payload["demo"] = os.path.basename(self.analysis.demo_path)
        payload["tickrate"] = self.analysis.tickrate
        payload["kills"] = [kill.to_dict() for kill in highlight.kills]
        payload["score_breakdown"] = highlight.score_breakdown
        write_json(metadata_path, payload)
        clip.metadata_path = str(metadata_path)
        return clip

    @staticmethod
    def _report(callback: ProgressCallback | None, job: RenderJob, progress: float, message: str) -> None:
        job.progress = max(0.0, min(1.0, progress))
        job.message = message
        if callback:
            callback(job, job.progress, message)


def describe_environment(settings: Settings) -> dict[str, object]:
    """Everything the Dashboard and the ``doctor`` command report."""
    ffmpeg = FFmpeg(settings.paths.ffmpeg_executable or None, settings.paths.ffprobe_executable or None)
    cs2_executable = str(find_cs2_executable(settings.paths.cs2_executable, settings.paths.steam_path) or "")
    from ..recording.factory import describe_recorders

    return {
        "cs2_executable": cs2_executable,
        "ffmpeg": ffmpeg.executable,
        "encoders": sorted(ffmpeg.encoders().available) if ffmpeg.available else [],
        "recorders": describe_recorders(settings.recording, cs2_executable, ffmpeg),
        "playback": controller_module.describe_backends(cs2_executable),
    }
