"""Render queue, crash recovery, output layout and metadata."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cs2_clip_generator.core.config import Settings
from cs2_clip_generator.core.errors import AppError
from cs2_clip_generator.core.models import JobState
from cs2_clip_generator.highlights.detector import DetectorOptions, detect_highlights
from cs2_clip_generator.render.job import summarise
from cs2_clip_generator.render.pipeline import build_job, build_jobs, output_dir_for
from cs2_clip_generator.render.queue import QueueEvents, RenderQueue, resume_jobs

from .conftest import TICKRATE, kill, make_analysis

SEC = int(TICKRATE)


@pytest.fixture
def settings(tmp_path) -> Settings:
    settings = Settings()
    settings.paths.output_dir = str(tmp_path / "clips")
    settings.paths.temp_dir = str(tmp_path / "temp")
    return settings


@pytest.fixture
def analysis_with_highlights():
    kills = [kill(tick=(20 + index * 40) * SEC, victim=f"7656119800000000{index + 5}") for index in range(3)]
    analysis = make_analysis(kills, rounds=1)
    analysis.total_ticks = 300 * SEC
    analysis.rounds[0].end_tick = 280 * SEC
    analysis.rounds[0].official_end_tick = 290 * SEC
    highlights = detect_highlights(analysis, DetectorOptions.defaults())
    return analysis, highlights


def test_output_layout_is_match_then_player(settings, analysis_with_highlights):
    analysis, highlights = analysis_with_highlights
    directory = output_dir_for(settings, analysis, highlights[0].player_name)
    assert directory.parent.name.startswith("de_mirage")
    assert directory.name == highlights[0].player_name
    job = build_job(highlights[0], analysis, settings)
    assert Path(job.output_path).suffix == ".mp4"
    assert Path(job.output_path).parent == directory


def test_jobs_start_pending_with_a_readable_label(settings, analysis_with_highlights):
    analysis, highlights = analysis_with_highlights
    jobs = build_jobs(highlights, analysis, settings)
    assert all(job.state == JobState.PENDING for job in jobs)
    assert "Round" in jobs[0].label


def test_queue_runs_jobs_one_at_a_time_and_reports_events(settings, analysis_with_highlights, monkeypatch):
    analysis, highlights = analysis_with_highlights
    jobs = build_jobs(highlights, analysis, settings)

    concurrent = {"max": 0, "now": 0}
    order: list[str] = []

    def fake_run(self, batch, on_progress=None, cancel=None):  # noqa: ANN001, ANN202
        from cs2_clip_generator.render.pipeline import PipelineReport

        concurrent["now"] += 1
        concurrent["max"] = max(concurrent["max"], concurrent["now"])
        report = PipelineReport()
        for job in batch:
            order.append(job.id)
            if on_progress:
                on_progress(job, 0.5, "Recording")
            clip = _fake_clip(job)
            job.clip = clip
            report.clips.append(clip)
        concurrent["now"] -= 1
        return report

    monkeypatch.setattr("cs2_clip_generator.render.pipeline.RenderPipeline.run", fake_run)

    finished: list[str] = []
    queue = RenderQueue(settings, analysis, QueueEvents(on_job_finished=lambda job: finished.append(job.id)))
    queue.add(jobs)
    report = queue.run_blocking()

    assert concurrent["max"] == 1, "two CS2 recordings must never run at once"
    assert len(report.clips) == len(jobs)
    assert finished == order
    assert all(job.state == JobState.DONE for job in queue.jobs)


def test_a_failing_job_does_not_stop_the_queue_and_can_be_retried(settings, analysis_with_highlights, monkeypatch):
    analysis, highlights = analysis_with_highlights
    jobs = build_jobs(highlights, analysis, settings)
    attempts: dict[str, int] = {}

    def flaky_run(self, batch, on_progress=None, cancel=None):  # noqa: ANN001, ANN202
        from cs2_clip_generator.render.pipeline import PipelineReport

        job = batch[0]
        attempts[job.id] = attempts.get(job.id, 0) + 1
        if job.id == jobs[1].id and attempts[job.id] == 1:
            raise AppError(title="Recorder exploded", actions=["Retry"])
        report = PipelineReport()
        report.clips.append(_fake_clip(job))
        return report

    monkeypatch.setattr("cs2_clip_generator.render.pipeline.RenderPipeline.run", flaky_run)

    queue = RenderQueue(settings, analysis)
    queue.add(jobs)
    queue.run_blocking()
    assert queue.jobs[1].state == JobState.FAILED
    assert "Recorder exploded" in queue.jobs[1].error
    assert sum(1 for job in queue.jobs if job.state == JobState.DONE) == len(jobs) - 1

    queue.retry(jobs[1].id)
    assert queue.jobs[1].state == JobState.PENDING
    queue.run_blocking()
    assert queue.jobs[1].state == JobState.DONE


def test_cancelling_stops_before_the_next_job(settings, analysis_with_highlights, monkeypatch):
    analysis, highlights = analysis_with_highlights
    jobs = build_jobs(highlights, analysis, settings)
    started: list[str] = []

    def cancelling_run(self, batch, on_progress=None, cancel=None):  # noqa: ANN001, ANN202
        from cs2_clip_generator.core.errors import Cancelled
        from cs2_clip_generator.render.pipeline import PipelineReport

        started.append(batch[0].id)
        if len(started) == 1:
            report = PipelineReport()
            report.clips.append(_fake_clip(batch[0]))
            return report
        raise Cancelled()

    monkeypatch.setattr("cs2_clip_generator.render.pipeline.RenderPipeline.run", cancelling_run)

    queue = RenderQueue(settings, analysis)
    queue.add(jobs)

    original = queue._should_cancel

    def cancel_after_first() -> bool:
        if len(started) >= 2:
            queue.cancel()
        return original()

    queue._should_cancel = cancel_after_first  # type: ignore[method-assign]
    queue.run_blocking()

    assert queue.jobs[0].state == JobState.DONE
    assert any(job.state in (JobState.PENDING, JobState.CANCELLED) for job in queue.jobs[1:])


def test_the_journal_survives_a_crash_and_only_unfinished_jobs_resume(settings, analysis_with_highlights):
    analysis, highlights = analysis_with_highlights
    jobs = build_jobs(highlights, analysis, settings)
    queue = RenderQueue(settings, analysis)
    queue.add(jobs)
    jobs[0].state = JobState.DONE
    jobs[1].state = JobState.RUNNING  # the app died here
    queue.save_journal()

    state = RenderQueue.has_interrupted_render(settings)
    assert state is not None
    assert state.demo_path == analysis.demo_path
    resumed = resume_jobs(state)
    assert {job.id for job in resumed} == {jobs[1].id, jobs[2].id}
    assert all(job.state == JobState.PENDING for job in resumed)

    RenderQueue.clear_journal(settings)
    assert RenderQueue.has_interrupted_render(settings) is None


def test_journal_round_trips_highlights_including_kills(settings, analysis_with_highlights):
    analysis, highlights = analysis_with_highlights
    queue = RenderQueue(settings, analysis)
    queue.add(build_jobs(highlights, analysis, settings))
    queue.save_journal()

    payload = json.loads(queue.journal_path().read_text())
    assert payload["jobs"][0]["highlight"]["kills"]
    state = RenderQueue.load_journal(settings)
    assert state is not None
    assert state.jobs[0].highlight.kills[0].weapon


def test_metadata_json_describes_the_clip(settings, analysis_with_highlights, tmp_path, monkeypatch):
    from cs2_clip_generator.cs2.controller import ClipPlan
    from cs2_clip_generator.recording.base import RecordingContext
    from cs2_clip_generator.render.pipeline import RenderPipeline

    analysis, highlights = analysis_with_highlights
    highlight = highlights[0]
    job = build_job(highlight, analysis, settings)
    output = tmp_path / "ACE_Round01_P1.mp4"
    output.write_bytes(b"fake video")

    pipeline = RenderPipeline(settings, analysis)
    monkeypatch.setattr(pipeline.ffmpeg, "probe_duration", lambda path: 12.5)
    context = RecordingContext(
        clip=ClipPlan(demo_path=analysis.demo_path, start_tick=1, end_tick=2),
        output_path=output,
        work_dir=tmp_path,
        video=settings.video,
        recording=settings.recording,
    )
    clip = pipeline._write_metadata(job, context)

    payload = json.loads(Path(clip.metadata_path).read_text(encoding="utf-8"))
    assert payload["player"] == highlight.player_name
    assert payload["round"] == highlight.round_number
    assert payload["type"] == highlight.kind.value
    assert payload["map"] == "de_mirage"
    assert payload["video"] == output.name
    assert payload["duration_seconds"] == 12.5
    assert payload["kills"]
    assert payload["tickrate"] == analysis.tickrate


def test_preflight_refuses_to_start_without_disk_space(settings, analysis_with_highlights, monkeypatch):
    from cs2_clip_generator.render.pipeline import RenderPipeline

    analysis, highlights = analysis_with_highlights
    pipeline = RenderPipeline(settings, analysis)
    pipeline.cs2_executable = "/fake/cs2"
    monkeypatch.setattr(pipeline.ffmpeg, "ensure", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr("cs2_clip_generator.render.pipeline.free_space_mb", lambda path: 1.0)

    with pytest.raises(AppError) as exc:
        pipeline.preflight(build_jobs(highlights, analysis, settings))
    assert "disk space" in exc.value.title.lower()


def test_summarise_counts_states(settings, analysis_with_highlights):
    analysis, highlights = analysis_with_highlights
    jobs = build_jobs(highlights, analysis, settings)
    jobs[0].state = JobState.DONE
    jobs[1].state = JobState.FAILED
    text = summarise(jobs)
    assert "1 done" in text and "failed" in text


def _fake_clip(job):  # noqa: ANN001, ANN202
    from cs2_clip_generator.core.models import Clip

    return Clip(
        highlight_id=job.highlight.id,
        player=job.highlight.player_name,
        player_steamid=job.highlight.player_steamid,
        round=job.highlight.round_number,
        type=job.highlight.kind.value,
        score=job.highlight.score,
        start_tick=job.highlight.start_tick,
        end_tick=job.highlight.end_tick,
        map="de_mirage",
        video=Path(job.output_path).name,
    )
