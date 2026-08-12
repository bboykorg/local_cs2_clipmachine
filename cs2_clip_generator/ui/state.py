"""Shared application state.

One object holds the current settings, the parsed match, its highlights and the
clips produced so far, and emits a signal whenever any of that changes. Pages
subscribe instead of reaching into each other, which keeps the navigation
independent of the work in progress.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..core.config import Settings
from ..core.models import Clip, Highlight, MatchAnalysis
from ..highlights.detector import DetectorOptions, detect_highlights, update_player_stats


class AppState(QObject):
    analysis_changed = Signal(object)  # MatchAnalysis | None
    highlights_changed = Signal(object)  # list[Highlight]
    clips_changed = Signal(object)  # list[Clip]
    settings_changed = Signal(object)  # Settings
    status_message = Signal(str)
    navigate = Signal(str)  # page key

    def __init__(self, settings: Settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.analysis: MatchAnalysis | None = None
        self.highlights: list[Highlight] = []
        self.clips: list[Clip] = []
        self.selected_player: str | None = None

    # -- mutations -------------------------------------------------------
    def set_analysis(self, analysis: MatchAnalysis, highlights: Sequence[Highlight]) -> None:
        self.analysis = analysis
        self.highlights = list(highlights)
        self.selected_player = None
        self.remember_demo(analysis.demo_path)
        self.analysis_changed.emit(analysis)
        self.highlights_changed.emit(self.highlights)

    def redetect(self) -> None:
        """Re-run highlight detection after the user changed clip settings."""
        if self.analysis is None:
            return
        options = DetectorOptions(clips=self.settings.clips, scoring=self.settings.scoring)
        self.highlights = detect_highlights(self.analysis, options)
        update_player_stats(self.analysis, options)
        self.highlights_changed.emit(self.highlights)

    def add_clips(self, clips: Sequence[Clip]) -> None:
        known = {clip.video for clip in self.clips}
        for clip in clips:
            if clip.video not in known:
                self.clips.append(clip)
        self.clips_changed.emit(self.clips)

    def load_existing_clips(self) -> None:
        """Pick up clips rendered in earlier sessions from their metadata files."""
        if self.analysis is None:
            return
        from ..render.pipeline import output_dir_for
        from ..utils.filesystem import read_json

        found: list[Clip] = []
        match_dir = output_dir_for(self.settings, self.analysis, "x").parent
        if not match_dir.is_dir():
            return
        for metadata in sorted(match_dir.rglob("*.json")):
            payload = read_json(metadata)
            if not isinstance(payload, dict) or "video" not in payload:
                continue
            video = metadata.with_suffix(".mp4")
            if not video.is_file():
                continue
            try:
                clip = Clip.from_dict(payload)
            except (TypeError, ValueError):
                continue
            clip.metadata_path = str(metadata)
            clip.video = str(video)
            found.append(clip)
        if found:
            self.clips = found
            self.clips_changed.emit(self.clips)

    def player_highlights(self) -> list[Highlight]:
        if not self.selected_player:
            return self.highlights
        return [h for h in self.highlights if h.player_steamid == self.selected_player]

    def highlight(self, highlight_id: str) -> Highlight | None:
        return next((h for h in self.highlights if h.id == highlight_id), None)

    def remember_demo(self, path: str) -> None:
        recent = [path, *[p for p in self.settings.ui.recent_demos if p != path]]
        self.settings.ui.recent_demos = recent[:8]
        self.settings.ui.last_demo = path
        self.save_settings()

    def save_settings(self) -> None:
        self.settings.save()
        self.settings_changed.emit(self.settings)

    def status(self, message: str) -> None:
        self.status_message.emit(message)

    # -- helpers ---------------------------------------------------------
    @property
    def output_dir(self) -> Path:
        return Path(self.settings.paths.output_dir)
