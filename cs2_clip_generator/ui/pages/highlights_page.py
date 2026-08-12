"""Highlights page: player picker, timeline, filters, cards, AUTO CLIP."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.models import Highlight, MatchAnalysis
from ...highlights.detector import auto_select
from ...highlights.filters import FILTER_DEFINITIONS, HighlightFilter
from ...utils.timeutil import format_timestamp, parse_timestamp
from .. import theme
from ..state import AppState
from ..widgets.common import (
    Card,
    HighlightCard,
    Metric,
    TimelineWidget,
    chip_row,
    heading,
    label,
)

MANUAL_ID = "__manual__"


class HighlightsPage(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.filter = HighlightFilter()
        self._cards: list[HighlightCard] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 18)
        layout.setSpacing(16)

        header = QHBoxLayout()
        header.addWidget(heading("Highlights", "Every moment the demo actually proves — scored and ready to clip."))
        header.addStretch(1)
        self.auto_button = QPushButton("AUTO CLIP")
        self.auto_button.setObjectName("primary")
        self.auto_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_button.setToolTip("Queue the best highlights for the selected player")
        self.auto_button.clicked.connect(self._auto_clip)
        header.addWidget(self.auto_button)
        layout.addLayout(header)

        layout.addWidget(self._controls_card())
        layout.addWidget(self._timeline_card())

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.cards_container = QWidget()
        self.cards_layout = QGridLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(14)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.cards_container)
        layout.addWidget(self.scroll, 1)

        self.empty_label = label("Load a demo to see its highlights.", "muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label)

        state.analysis_changed.connect(self._on_analysis)
        state.highlights_changed.connect(lambda _: self.refresh())

    # -- controls --------------------------------------------------------
    def _controls_card(self) -> Card:
        card = Card()
        top = QHBoxLayout()
        top.setSpacing(10)

        top.addWidget(label("Player", "faint"))
        self.player_combo = QComboBox()
        self.player_combo.setMinimumWidth(220)
        self.player_combo.currentIndexChanged.connect(self._on_player_changed)
        top.addWidget(self.player_combo)

        top.addWidget(label("Sort by", "faint"))
        self.sort_combo = QComboBox()
        for key, text in (
            ("score", "Score"),
            ("round", "Round"),
            ("time", "Time"),
            ("kills", "Kill count"),
            ("player", "Player"),
        ):
            self.sort_combo.addItem(text, key)
        self.sort_combo.currentIndexChanged.connect(lambda _: self.refresh())
        top.addWidget(self.sort_combo)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search highlights…  (ACE, AWP, round 17, headshot)")
        self.search_input.textChanged.connect(self._on_search)
        top.addWidget(self.search_input, 1)

        manual = QPushButton("Manual clip")
        manual.setObjectName("ghost")
        manual.setToolTip("Create a clip from an arbitrary tick range")
        manual.clicked.connect(self._manual_clip)
        top.addWidget(manual)
        card.body().addLayout(top)

        chips, self.chip_buttons = chip_row(
            FILTER_DEFINITIONS, self._on_filter_toggled, self.state.settings.ui.active_filters
        )
        card.body().addWidget(chips)

        stats = QHBoxLayout()
        stats.setSpacing(26)
        self.metric_total = Metric("0", "SHOWN")
        self.metric_kills = Metric("0", "KILLS")
        self.metric_aces = Metric("0", "ACES", theme.KIND_COLORS["ACE"])
        self.metric_clutches = Metric("0", "CLUTCHES", theme.SUCCESS)
        self.metric_hs = Metric("0", "HEADSHOTS")
        for metric in (self.metric_total, self.metric_kills, self.metric_aces, self.metric_clutches, self.metric_hs):
            stats.addWidget(metric)
        stats.addStretch(1)

        auto_box = QHBoxLayout()
        auto_box.setSpacing(8)
        auto_box.addWidget(label("Max clips", "faint"))
        self.max_clips = QSpinBox()
        self.max_clips.setRange(1, 100)
        self.max_clips.setValue(self.state.settings.clips.max_clips)
        auto_box.addWidget(self.max_clips)
        auto_box.addWidget(label("Min score", "faint"))
        self.min_score = QDoubleSpinBox()
        self.min_score.setRange(0, 1000)
        self.min_score.setDecimals(0)
        self.min_score.setValue(self.state.settings.clips.min_score)
        auto_box.addWidget(self.min_score)
        stats.addLayout(auto_box)
        card.body().addLayout(stats)
        return card

    def _timeline_card(self) -> Card:
        card = Card()
        card.body().addWidget(label("Timeline", "h3"))
        self.timeline = TimelineWidget()
        self.timeline.highlight_clicked.connect(self._on_timeline_click)
        card.body().addWidget(self.timeline)
        self.timeline_detail = label("Click a marker to jump to that highlight.", "faint")
        card.body().addWidget(self.timeline_detail)
        return card

    # -- state -----------------------------------------------------------
    def _on_analysis(self, analysis: MatchAnalysis | None) -> None:
        self.player_combo.blockSignals(True)
        self.player_combo.clear()
        self.player_combo.addItem("All players", None)
        if analysis is not None:
            for player in analysis.players:
                stats = analysis.stats.get(player.steamid)
                suffix = f"  ({stats.kills}K / {stats.deaths}D)" if stats else ""
                self.player_combo.addItem(f"{player.name}{suffix}", player.steamid)
        self.player_combo.blockSignals(False)
        self.refresh()

    def _on_player_changed(self, index: int) -> None:
        self.state.selected_player = self.player_combo.itemData(index)
        self.refresh()

    def _on_search(self, text: str) -> None:
        self.filter.query = text
        self.refresh()

    def _on_filter_toggled(self, key: str, enabled: bool) -> None:
        if enabled:
            self.filter.active.add(key)
        else:
            self.filter.active.discard(key)
        self.state.settings.ui.active_filters = sorted(self.filter.active)
        self.refresh()

    # -- rendering -------------------------------------------------------
    def visible_highlights(self) -> list[Highlight]:
        self.filter.player_steamid = self.state.selected_player
        sort_key = self.sort_combo.currentData() or "score"
        return self.filter.apply(self.state.highlights, sort_key)

    def refresh(self) -> None:
        highlights = self.visible_highlights()
        analysis = self.state.analysis

        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()

        self.empty_label.setVisible(not highlights)
        if not highlights:
            self.empty_label.setText(
                "No highlights match the current filters."
                if self.state.highlights
                else "Load a demo to see its highlights."
            )
        self.auto_button.setEnabled(bool(highlights))

        tickrate = analysis.tickrate if analysis else 64.0
        columns = 3
        for index, highlight in enumerate(highlights):
            card = HighlightCard(highlight, tickrate)
            card.generate_requested.connect(self._generate_one)
            card.preview_requested.connect(self._preview_one)
            card.edit_requested.connect(self._edit_clip)
            self.cards_layout.addWidget(card, index // columns, index % columns)
            self._cards.append(card)

        self.metric_total.set_value(str(len(highlights)))
        self.metric_kills.set_value(str(sum(h.kill_count for h in highlights)))
        self.metric_aces.set_value(str(sum(1 for h in highlights if h.kill_count >= 5)))
        self.metric_clutches.set_value(str(sum(1 for h in highlights if h.clutch_vs)))
        self.metric_hs.set_value(str(sum(h.headshot_count for h in highlights)))

        if analysis is not None:
            self.timeline.set_data(highlights, analysis.total_ticks, analysis.tickrate)

    # -- actions ---------------------------------------------------------
    def _on_timeline_click(self, highlight_id: str) -> None:
        highlight = self.state.highlight(highlight_id)
        if highlight is None:
            return
        self.timeline_detail.setText(
            f"Round {highlight.round_number} → {highlight.kind.value} → {highlight.player_name} "
            f"→ tick {highlight.first_kill_tick}"
        )
        for card in self._cards:
            if card.highlight.id == highlight_id:
                self.scroll.ensureWidgetVisible(card, 40, 40)
                card.setObjectName("cardStrong")
                card.style().unpolish(card)
                card.style().polish(card)
                break

    def _generate_one(self, highlight_id: str) -> None:
        highlight = self.state.highlight(highlight_id)
        if highlight is None:
            return
        self.state.navigate.emit(f"render:queue:{highlight.id}")

    def _preview_one(self, highlight_id: str) -> None:
        self.state.navigate.emit(f"preview:{highlight_id}")

    def _auto_clip(self) -> None:
        self.state.settings.clips.max_clips = self.max_clips.value()
        self.state.settings.clips.min_score = self.min_score.value()
        self.state.save_settings()
        selected = auto_select(
            self.visible_highlights(),
            max_clips=self.max_clips.value(),
            min_score=self.min_score.value(),
            steamid=self.state.selected_player,
        )
        if not selected:
            self.state.status("No highlight reaches the minimum score — lower it and try again.")
            return
        self.state.navigate.emit("render:queue:" + ",".join(h.id for h in selected))

    def _manual_clip(self) -> None:
        analysis = self.state.analysis
        if analysis is None:
            return
        dialog = ManualClipDialog(analysis, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.highlight is not None:
            self.state.highlights.append(dialog.highlight)
            self.state.highlights_changed.emit(self.state.highlights)
            self.state.navigate.emit(f"render:queue:{dialog.highlight.id}")

    def _edit_clip(self, highlight_id: str) -> None:
        highlight = self.state.highlight(highlight_id)
        analysis = self.state.analysis
        if highlight is None or analysis is None:
            return
        dialog = ClipRangeDialog(highlight, analysis.tickrate, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            highlight.start_tick, highlight.end_tick = dialog.tick_range()
            self.refresh()
            self.state.status(
                f"Clip range set to {format_timestamp(highlight.start_tick / analysis.tickrate)} – "
                f"{format_timestamp(highlight.end_tick / analysis.tickrate)}"
            )
            if dialog.regenerate:
                self.state.navigate.emit(f"render:queue:{highlight.id}")


class ClipRangeDialog(QDialog):
    """Adjust a clip's start and end by hand, then optionally regenerate."""

    def __init__(self, highlight: Highlight, tickrate: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Clip range")
        self.setMinimumWidth(380)
        self.tickrate = tickrate or 64.0
        self.regenerate = False
        self.setStyleSheet(f"QDialog {{ background: {theme.BACKGROUND_ALT}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(label(highlight.title or highlight.kind.value, "h2", wrap=True))
        layout.addWidget(
            label(
                f"Round {highlight.round_number} · {highlight.kill_count} kills · "
                f"kills at ticks {highlight.first_kill_tick}–{highlight.last_kill_tick}",
                "muted",
            )
        )

        grid = QGridLayout()
        grid.addWidget(label("Start", "faint"), 0, 0)
        self.start_input = QLineEdit(format_timestamp(highlight.start_tick / self.tickrate))
        grid.addWidget(self.start_input, 0, 1)
        grid.addWidget(label("End", "faint"), 1, 0)
        self.end_input = QLineEdit(format_timestamp(highlight.end_tick / self.tickrate))
        grid.addWidget(self.end_input, 1, 1)
        layout.addLayout(grid)
        layout.addWidget(label("Format: m:ss.mmm — the times are relative to the start of the demo.", "faint"))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self.accept)
        regenerate = QPushButton("Apply & Regenerate")
        regenerate.setObjectName("primary")
        regenerate.clicked.connect(self._regenerate)
        buttons.addWidget(cancel)
        buttons.addWidget(apply_button)
        buttons.addWidget(regenerate)
        layout.addLayout(buttons)

    def _regenerate(self) -> None:
        self.regenerate = True
        self.accept()

    def tick_range(self) -> tuple[int, int]:
        start = parse_timestamp(self.start_input.text()) or 0.0
        end = parse_timestamp(self.end_input.text()) or (start + 5.0)
        if end <= start:
            end = start + 5.0
        return int(start * self.tickrate), int(end * self.tickrate)


class ManualClipDialog(QDialog):
    """Build a clip from a tick range and a player, with no detection at all."""

    def __init__(self, analysis: MatchAnalysis, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manual clip")
        self.setMinimumWidth(400)
        self.analysis = analysis
        self.highlight: Highlight | None = None
        self.setStyleSheet(f"QDialog {{ background: {theme.BACKGROUND_ALT}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(label("Manual clip", "h2"))
        layout.addWidget(label("Pick a player and a tick range; nothing is detected for you.", "muted", wrap=True))

        grid = QGridLayout()
        grid.addWidget(label("Player", "faint"), 0, 0)
        self.player_combo = QComboBox()
        for player in analysis.players:
            self.player_combo.addItem(f"{player.name} (slot {player.slot})", player.steamid)
        grid.addWidget(self.player_combo, 0, 1)

        grid.addWidget(label("Start tick", "faint"), 1, 0)
        self.start_tick = QSpinBox()
        self.start_tick.setRange(0, max(1, analysis.total_ticks))
        self.start_tick.setValue(min(analysis.total_ticks, 1000))
        grid.addWidget(self.start_tick, 1, 1)

        grid.addWidget(label("End tick", "faint"), 2, 0)
        self.end_tick = QSpinBox()
        self.end_tick.setRange(1, max(1, analysis.total_ticks))
        self.end_tick.setValue(min(analysis.total_ticks, 1000 + int(analysis.tickrate * 10)))
        grid.addWidget(self.end_tick, 2, 1)
        layout.addLayout(grid)

        self.hint = QLabel("")
        self.hint.setObjectName("faint")
        layout.addWidget(self.hint)
        self.start_tick.valueChanged.connect(self._update_hint)
        self.end_tick.valueChanged.connect(self._update_hint)
        self._update_hint()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        create = QPushButton("Create clip")
        create.setObjectName("primary")
        create.clicked.connect(self._create)
        buttons.addWidget(cancel)
        buttons.addWidget(create)
        layout.addLayout(buttons)

    def _update_hint(self) -> None:
        tickrate = self.analysis.tickrate or 64.0
        duration = max(0, self.end_tick.value() - self.start_tick.value()) / tickrate
        self.hint.setText(
            f"{format_timestamp(self.start_tick.value() / tickrate)} → "
            f"{format_timestamp(self.end_tick.value() / tickrate)}  ({duration:.1f}s)"
        )

    def _create(self) -> None:
        from ...core.models import HighlightKind

        steamid = self.player_combo.currentData()
        player = self.analysis.player(steamid)
        if player is None:
            self.reject()
            return
        round_number = 0
        for round_ in self.analysis.rounds:
            if round_.start_tick <= self.start_tick.value():
                round_number = round_.number
        self.highlight = Highlight(
            id=f"{MANUAL_ID}{self.start_tick.value()}",
            kind=HighlightKind.KILL,
            player_steamid=player.steamid,
            player_name=player.name,
            round_number=round_number,
            kills=[],
            score=0.0,
            title=f"Manual clip — Round {round_number}",
            start_tick=self.start_tick.value(),
            end_tick=self.end_tick.value(),
            team=player.team,
        )
        self.accept()
