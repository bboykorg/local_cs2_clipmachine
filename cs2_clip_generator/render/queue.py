"""The render queue: one job at a time, pausable, cancellable, crash-aware.

Only one CS2 recording can run at once — the game is a singleton and a second
instance would fight over the window, the console log and the demo. So the queue
is strictly serial, runs on a worker thread, and reports progress through plain
callbacks (the Qt layer adapts them to signals).

Crash recovery works through a small journal file: the queue is written to disk
whenever it changes, so if the app dies mid-render the next start can offer to
resume exactly the jobs that had not finished.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import Settings
from ..core.errors import AppError, Cancelled
from ..core.logger import get_logger
from ..core.models import Clip, JobState, MatchAnalysis, RenderJob
from ..utils.filesystem import read_json, write_json
from .pipeline import PipelineReport, RenderPipeline

log = get_logger("app")

JOURNAL_NAME = "render_queue.json"


@dataclass
class QueueEvents:
    """Callbacks the UI (or the CLI) subscribes to."""

    on_job_started: Callable[[RenderJob], None] | None = None
    on_job_progress: Callable[[RenderJob, float, str], None] | None = None
    on_job_finished: Callable[[RenderJob], None] | None = None
    on_queue_finished: Callable[[PipelineReport], None] | None = None
    on_error: Callable[[AppError], None] | None = None

    def emit(self, name: str, *args: object) -> None:
        callback = getattr(self, name, None)
        if callback:
            try:
                callback(*args)
            except Exception:  # pragma: no cover - never let the UI break the queue
                log.exception("queue callback %s failed", name)


@dataclass
class QueueState:
    """Serialisable snapshot used for crash recovery."""

    demo_path: str = ""
    analysis_sha1: str = ""
    jobs: list[RenderJob] = field(default_factory=list)
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "demo_path": self.demo_path,
            "analysis_sha1": self.analysis_sha1,
            "updated_at": self.updated_at,
            "jobs": [job.to_dict() for job in self.jobs],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> QueueState:
        jobs = []
        for raw in payload.get("jobs", []):
            try:
                jobs.append(RenderJob.from_dict(raw))
            except (TypeError, ValueError, KeyError):
                continue
        return cls(
            demo_path=str(payload.get("demo_path", "")),
            analysis_sha1=str(payload.get("analysis_sha1", "")),
            jobs=jobs,
            updated_at=float(payload.get("updated_at") or 0.0),
        )

    @property
    def unfinished(self) -> list[RenderJob]:
        return [job for job in self.jobs if job.state in (JobState.PENDING, JobState.RUNNING, JobState.FAILED)]


class RenderQueue:
    """Serial job runner with pause / cancel / skip / retry."""

    def __init__(self, settings: Settings, analysis: MatchAnalysis, events: QueueEvents | None = None) -> None:
        self.settings = settings
        self.analysis = analysis
        self.events = events or QueueEvents()
        self.jobs: list[RenderJob] = []
        self.clips: list[Clip] = []

        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self._skip = threading.Event()
        self._lock = threading.RLock()
        self._current: RenderJob | None = None
        self._report: PipelineReport | None = None

    # -- queue management ------------------------------------------------
    def add(self, jobs: Iterable[RenderJob]) -> None:
        with self._lock:
            existing = {job.id for job in self.jobs}
            for job in jobs:
                if job.id not in existing:
                    self.jobs.append(job)
                    existing.add(job.id)
        self.save_journal()

    def clear(self) -> None:
        with self._lock:
            self.jobs = [job for job in self.jobs if job.state == JobState.RUNNING]
        self.save_journal()

    def remove(self, job_id: str) -> None:
        with self._lock:
            self.jobs = [job for job in self.jobs if job.id != job_id or job.state == JobState.RUNNING]
        self.save_journal()

    def retry(self, job_id: str) -> None:
        with self._lock:
            for job in self.jobs:
                if job.id == job_id and job.state in (JobState.FAILED, JobState.CANCELLED, JobState.SKIPPED):
                    job.state = JobState.PENDING
                    job.error = ""
                    job.progress = 0.0
                    job.message = "Queued for retry"
        self.save_journal()

    def retry_failed(self) -> None:
        for job in list(self.jobs):
            self.retry(job.id)

    @property
    def pending(self) -> list[RenderJob]:
        return [job for job in self.jobs if job.state == JobState.PENDING]

    @property
    def current(self) -> RenderJob | None:
        return self._current

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- controls --------------------------------------------------------
    def pause(self) -> None:
        self._pause.set()
        log.info("render queue paused")

    def resume(self) -> None:
        self._pause.clear()
        log.info("render queue resumed")

    @property
    def paused(self) -> bool:
        return self._pause.is_set()

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.clear()
        log.info("render queue cancelled")

    def skip_current(self) -> None:
        self._skip.set()
        log.info("skipping the current render job")

    def _should_cancel(self) -> bool:
        while self._pause.is_set() and not self._cancel.is_set():
            time.sleep(0.2)
        return self._cancel.is_set() or self._skip.is_set()

    # -- execution -------------------------------------------------------
    def start(self) -> None:
        """Start processing in the background. Returns immediately."""
        if self.running:
            return
        self._cancel.clear()
        self._skip.clear()
        self._thread = threading.Thread(target=self._run, name="render-queue", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def run_blocking(self) -> PipelineReport:
        """Process the queue on the calling thread (used by the CLI)."""
        self._cancel.clear()
        self._skip.clear()
        self._run()
        return self._report or PipelineReport()

    def _run(self) -> None:
        pipeline = RenderPipeline(self.settings, self.analysis)
        report = PipelineReport()
        try:
            while True:
                with self._lock:
                    job = next((j for j in self.jobs if j.state == JobState.PENDING), None)
                if job is None:
                    break
                if self._cancel.is_set():
                    break
                self._skip.clear()
                self._current = job
                job.state = JobState.RUNNING
                self.events.emit("on_job_started", job)
                self.save_journal()

                try:
                    single = pipeline.run(
                        [job],
                        on_progress=lambda j, fraction, message: self.events.emit(
                            "on_job_progress", j, fraction, message
                        ),
                        cancel=self._should_cancel,
                    )
                    report.clips += single.clips
                    report.failed += single.failed
                    report.recorder_name = single.recorder_name or report.recorder_name
                    report.playback_name = single.playback_name or report.playback_name
                    for note in single.notes:
                        if note not in report.notes:
                            report.notes.append(note)
                    self.clips += single.clips
                except Cancelled:
                    if self._skip.is_set() and not self._cancel.is_set():
                        job.state = JobState.SKIPPED
                        job.message = "Skipped"
                        self._skip.clear()
                    else:
                        job.state = JobState.CANCELLED
                        job.message = "Cancelled"
                except AppError as exc:
                    job.state = JobState.FAILED
                    job.error = exc.as_text()
                    report.failed.append((job, exc.title))
                    self.events.emit("on_error", exc)
                except Exception as exc:  # pragma: no cover - defensive
                    log.exception("render job %s crashed", job.id)
                    job.state = JobState.FAILED
                    job.error = "Unexpected error; see logs/app.log"
                    report.failed.append((job, str(exc)))

                if job.state == JobState.RUNNING:
                    job.state = JobState.DONE
                self._current = None
                self.events.emit("on_job_finished", job)
                self.save_journal()

                if self._cancel.is_set():
                    break
        finally:
            self._report = report
            self.events.emit("on_queue_finished", report)
            self.save_journal()

    # -- crash recovery --------------------------------------------------
    def journal_path(self) -> Path:
        return self.settings.state_dir / JOURNAL_NAME

    def save_journal(self) -> None:
        state = QueueState(
            demo_path=self.analysis.demo_path,
            analysis_sha1=self.analysis.demo_sha1,
            jobs=list(self.jobs),
            updated_at=time.time(),
        )
        try:
            write_json(self.journal_path(), state.to_dict())
        except OSError as exc:  # pragma: no cover - disk full
            log.debug("could not write the render journal: %s", exc)

    @staticmethod
    def load_journal(settings: Settings) -> QueueState | None:
        payload = read_json(settings.state_dir / JOURNAL_NAME)
        if not isinstance(payload, dict):
            return None
        state = QueueState.from_dict(payload)
        return state if state.jobs else None

    @staticmethod
    def clear_journal(settings: Settings) -> None:
        try:
            (settings.state_dir / JOURNAL_NAME).unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def has_interrupted_render(settings: Settings) -> QueueState | None:
        """Was the app killed while rendering? Returns the resumable state."""
        state = RenderQueue.load_journal(settings)
        if state is None:
            return None
        return state if state.unfinished else None


def resume_jobs(state: QueueState) -> list[RenderJob]:
    """Reset the jobs of an interrupted session so they can run again."""
    jobs: Sequence[RenderJob] = state.unfinished
    for job in jobs:
        job.state = JobState.PENDING
        job.progress = 0.0
        job.message = "Resumed"
        job.error = ""
    return list(jobs)
