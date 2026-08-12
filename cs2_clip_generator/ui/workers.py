"""Background workers.

Rule: the UI thread never parses a demo, never talks to CS2 and never waits for
FFmpeg. Every long operation lives in a :class:`QThread` and reports back with
signals; each worker carries a cancel flag that the operation polls, so "Cancel"
actually stops work instead of just hiding a dialog.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from ..core.config import Settings
from ..core.errors import AppError, Cancelled
from ..core.logger import get_logger
from ..core.models import Clip, Highlight, MatchAnalysis, RenderJob
from ..demo.cache import AnalysisCache
from ..demo.downloader import DownloadProgress, download_demo
from ..demo.extractor import extract_demo, is_archive
from ..demo.parser import get_parser
from ..demo.validation import assert_supported
from ..highlights.detector import DetectorOptions, detect_highlights, update_player_stats
from ..montage.creator import MontageCreator, MontageSettings
from ..render.pipeline import PipelineReport, RenderPipeline

log = get_logger("app")


class BaseWorker(QThread):
    """A cancellable worker thread that never lets an exception escape."""

    failed = Signal(object)  # AppError
    cancelled = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel_hook(self):  # noqa: ANN201 - Callable[[], bool]
        return self._cancel.is_set

    def run(self) -> None:  # noqa: D102 - Qt entry point
        try:
            self.work()
        except Cancelled:
            self.cancelled.emit()
        except AppError as error:
            log.error("%s (%s)", error.title, error.detail)
            self.failed.emit(error)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("worker crashed")
            self.failed.emit(
                AppError(
                    title="Something went wrong.",
                    reasons=["An unexpected error occurred"],
                    actions=["Open the logs folder", "Retry"],
                    detail=str(exc),
                )
            )

    def work(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class DownloadWorker(BaseWorker):
    """Fetch a demo from a URL, reporting bytes, speed and ETA."""

    progress = Signal(object)  # DownloadProgress
    finished_path = Signal(str)

    def __init__(self, url: str, target_dir: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.url = url
        self.target_dir = target_dir

    def work(self) -> None:
        def on_progress(update: DownloadProgress) -> None:
            self.progress.emit(update)

        path = download_demo(self.url, self.target_dir, progress=on_progress, cancel=self.cancel_hook())
        self.finished_path.emit(str(path))


class AnalysisWorker(BaseWorker):
    """Extract if needed, parse the demo, detect highlights."""

    progress = Signal(float, str)
    finished_analysis = Signal(object, object)  # MatchAnalysis, list[Highlight]

    def __init__(
        self,
        demo_path: str,
        settings: Settings,
        member: str | None = None,
        use_cache: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.demo_path = demo_path
        self.settings = settings
        self.member = member
        self.use_cache = use_cache

    def work(self) -> None:
        path = Path(self.demo_path)
        if is_archive(path):
            self.progress.emit(0.02, "Extracting archive…")
            path = extract_demo(
                path,
                self.settings.paths.temp_dir,
                member=self.member,
                progress=lambda fraction, message: self.progress.emit(0.02 + fraction * 0.08, message),
                cancel=self.cancel_hook(),
            )
        assert_supported(path)

        parser = get_parser()
        cache = AnalysisCache(self.settings.cache_dir)
        analysis: MatchAnalysis | None = None
        if self.use_cache:
            self.progress.emit(0.12, "Checking the cache…")
            analysis = cache.load(path, parser.version())

        if analysis is None:
            analysis = parser.parse(
                str(path),
                progress=lambda fraction, message: self.progress.emit(0.12 + fraction * 0.78, message),
                cancel=self.cancel_hook(),
            )
            cache.store(analysis, parser.version())
        else:
            self.progress.emit(0.9, "Loaded from cache")

        self.progress.emit(0.94, "Detecting highlights…")
        options = DetectorOptions(clips=self.settings.clips, scoring=self.settings.scoring)
        highlights = detect_highlights(analysis, options)
        update_player_stats(analysis, options)
        self.progress.emit(1.0, f"{len(highlights)} highlights found")
        self.finished_analysis.emit(analysis, highlights)


class RenderWorker(BaseWorker):
    """Drive CS2 and the recorder for a batch of jobs."""

    job_progress = Signal(object, float, str)  # RenderJob, fraction, message
    job_finished = Signal(object)  # RenderJob
    finished_report = Signal(object)  # PipelineReport

    def __init__(
        self,
        settings: Settings,
        analysis: MatchAnalysis,
        jobs: Sequence[RenderJob],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.analysis = analysis
        self.jobs = list(jobs)

    def work(self) -> None:
        pipeline = RenderPipeline(self.settings, self.analysis)
        report = PipelineReport()
        for job in self.jobs:
            if self.is_cancelled:
                break
            single = pipeline.run(
                [job],
                on_progress=lambda j, fraction, message: self.job_progress.emit(j, fraction, message),
                cancel=self.cancel_hook(),
            )
            report.clips += single.clips
            report.failed += single.failed
            report.recorder_name = single.recorder_name or report.recorder_name
            report.playback_name = single.playback_name or report.playback_name
            for note in single.notes:
                if note not in report.notes:
                    report.notes.append(note)
            self.job_finished.emit(job)
        self.finished_report.emit(report)


class PreviewWorker(BaseWorker):
    """Open CS2 at a highlight without recording anything."""

    started_preview = Signal()

    def __init__(
        self,
        settings: Settings,
        analysis: MatchAnalysis,
        highlight: Highlight,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.analysis = analysis
        self.highlight = highlight

    def work(self) -> None:
        from ..cs2 import controller as controller_module
        from ..cs2.controller import RecorderHooks, SessionOptions
        from ..render.pipeline import RenderPipeline, build_job

        settings = self.settings
        pipeline = RenderPipeline(settings, self.analysis)
        plan = pipeline.build_plan(build_job(self.highlight, self.analysis, settings))

        controller = controller_module.get_playback_controller(
            pipeline.cs2_executable,
            preferred=settings.recording.playback_backend,
            options=SessionOptions(
                width=settings.video.width,
                height=settings.video.height,
                display_mode=settings.recording.display_mode,
                demo_load_timeout=settings.recording.demo_load_timeout,
                stabilisation_seconds=settings.recording.stabilisation_seconds,
                close_game_after=False,  # a preview stays open for the user
            ),
            extra_args=settings.recording.extra_launch_args,
        )
        controller.set_cancel_hook(self.cancel_hook())
        hooks = RecorderHooks()  # nothing to record
        controller.begin_session(self.analysis.demo_path, [(plan, hooks)])
        self.started_preview.emit()
        controller.run_clip(plan, hooks)


class MontageWorker(BaseWorker):
    """Join finished clips with FFmpeg."""

    progress = Signal(float, str)
    finished_path = Signal(str)

    def __init__(
        self,
        clips: Sequence[str],
        output_path: str,
        montage_settings: MontageSettings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.clips = list(clips)
        self.output_path = output_path
        self.montage_settings = montage_settings

    def work(self) -> None:
        creator = MontageCreator()
        path = creator.create(
            self.clips,
            self.output_path,
            self.montage_settings,
            on_progress=lambda fraction, message: self.progress.emit(fraction, message),
            cancel=self.cancel_hook(),
        )
        self.finished_path.emit(str(path))


class DetectionWorker(BaseWorker):
    """Run tool auto-detection off the UI thread (it shells out a lot)."""

    finished_report = Signal(object)

    def __init__(self, settings: Settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings

    def work(self) -> None:
        from ..core.detection import detect_all

        report = detect_all(self.settings, apply=True)
        self.settings.save()
        self.finished_report.emit(report)


__all__ = [
    "AnalysisWorker",
    "BaseWorker",
    "Clip",
    "DetectionWorker",
    "DownloadWorker",
    "MontageWorker",
    "PreviewWorker",
    "RenderWorker",
]
