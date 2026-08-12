"""Clip windows, round clamping and the merging of overlapping clips."""

from __future__ import annotations

from cs2_clip_generator.core.config import ClipSettings
from cs2_clip_generator.core.models import Highlight, HighlightKind
from cs2_clip_generator.highlights.detector import DetectorOptions, detect_highlights
from cs2_clip_generator.highlights.timing import (
    apply_clip_ranges,
    compute_clip_range,
    merge_overlapping,
)

from .conftest import TICKRATE, kill, make_analysis

SEC = int(TICKRATE)


def _highlight(kind: HighlightKind, kills: list, **kwargs) -> Highlight:
    return Highlight(
        id=f"h{kills[0].tick}",
        kind=kind,
        player_steamid="76561198000000001",
        player_name="P1",
        round_number=1,
        kills=kills,
        **kwargs,
    )


def test_single_kill_gets_six_seconds_before_and_four_after():
    analysis = make_analysis()
    analysis.rounds[0].freeze_end_tick = 0  # keep the clamp out of the way
    highlight = _highlight(HighlightKind.KILL, [kill(tick=100 * SEC)])
    start, end = compute_clip_range(highlight, analysis, ClipSettings())
    assert (100 * SEC - start) / TICKRATE == 6.0
    assert (end - 100 * SEC) / TICKRATE == 4.0


def test_each_kind_has_its_own_window():
    analysis = make_analysis()
    analysis.rounds[0].freeze_end_tick = 0
    expected = {
        HighlightKind.MULTI_2K: (8.0, 5.0),
        HighlightKind.MULTI_3K: (8.0, 6.0),
        HighlightKind.MULTI_4K: (10.0, 7.0),
        HighlightKind.ACE: (10.0, 8.0),
        HighlightKind.CLUTCH: (10.0, 10.0),
    }
    for kind, (lead_in, lead_out) in expected.items():
        highlight = _highlight(kind, [kill(tick=100 * SEC), kill(tick=110 * SEC, victim="76561198000000007")])
        start, end = compute_clip_range(highlight, analysis, ClipSettings())
        assert (100 * SEC - start) / TICKRATE == lead_in, kind
        assert (end - 110 * SEC) / TICKRATE == lead_out, kind


def test_windows_are_configurable():
    analysis = make_analysis()
    analysis.rounds[0].freeze_end_tick = 0
    settings = ClipSettings()
    settings.lead_in["KILL"] = 2.0
    settings.lead_out["KILL"] = 1.0
    start, end = compute_clip_range(_highlight(HighlightKind.KILL, [kill(tick=100 * SEC)]), analysis, settings)
    assert (100 * SEC - start) / TICKRATE == 2.0
    assert (end - 100 * SEC) / TICKRATE == 1.0


def test_clip_never_starts_in_the_previous_round_or_ends_in_the_next():
    analysis = make_analysis()
    round_ = analysis.rounds[1]  # ticks 10000..18000, freeze ends at 11000
    highlight = _highlight(HighlightKind.ACE, [kill(tick=round_.freeze_end_tick + 10, round_number=2)])
    highlight.round_number = 2
    settings = ClipSettings(round_padding_seconds=3.0)
    start, end = compute_clip_range(highlight, analysis, settings)
    assert start >= round_.play_start_tick - int(3 * TICKRATE)
    assert start >= round_.start_tick
    assert end <= (round_.official_end_tick or round_.end_tick) + int(3 * TICKRATE)


def test_clamping_can_be_switched_off():
    analysis = make_analysis()
    highlight = _highlight(HighlightKind.ACE, [kill(tick=analysis.rounds[0].freeze_end_tick + 10)])
    clamped, _ = compute_clip_range(highlight, analysis, ClipSettings(clamp_to_round=True))
    free, _ = compute_clip_range(highlight, analysis, ClipSettings(clamp_to_round=False))
    assert free <= clamped


def test_clip_never_starts_before_the_demo_or_ends_after_it():
    analysis = make_analysis()
    highlight = _highlight(HighlightKind.ACE, [kill(tick=10)])
    start, end = compute_clip_range(highlight, analysis, ClipSettings(clamp_to_round=False))
    assert start >= 0
    assert end <= analysis.total_ticks


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


def _with_range(start: float, end: float, ident: str = "a") -> Highlight:
    highlight = _highlight(HighlightKind.KILL, [kill(tick=int((start + end) / 2 * SEC))])
    highlight.id = ident
    highlight.start_tick = int(start * SEC)
    highlight.end_tick = int(end * SEC)
    return highlight


def test_overlapping_clips_of_the_same_player_are_merged():
    # Clip A 100-130s, clip B 125-150s -> one clip 100-150s
    merged = merge_overlapping([_with_range(100, 130, "a"), _with_range(125, 150, "b")], TICKRATE)
    assert len(merged) == 1
    assert merged[0].start_tick == 100 * SEC
    assert merged[0].end_tick == 150 * SEC
    assert set(merged[0].merged_from) >= {"b"} or set(merged[0].merged_from) >= {"a"}


def test_clips_that_only_touch_within_the_gap_are_merged():
    close = merge_overlapping([_with_range(100, 130, "a"), _with_range(130.5, 150, "b")], TICKRATE, gap_seconds=1.0)
    far = merge_overlapping([_with_range(100, 130, "a"), _with_range(140, 150, "b")], TICKRATE, gap_seconds=1.0)
    assert len(close) == 1
    assert len(far) == 2


def test_merging_keeps_every_kill_and_upgrades_the_kind():
    first = _highlight(HighlightKind.KILL, [kill(tick=100 * SEC)])
    first.id, first.start_tick, first.end_tick = "a", 94 * SEC, 104 * SEC
    second = _highlight(HighlightKind.KILL, [kill(tick=106 * SEC, victim="76561198000000007")])
    second.id, second.start_tick, second.end_tick = "b", 100 * SEC, 110 * SEC

    merged = merge_overlapping([first, second], TICKRATE)
    assert len(merged) == 1
    assert merged[0].kill_count == 2
    assert merged[0].kind == HighlightKind.MULTI_2K


def test_different_players_are_never_merged():
    first = _with_range(100, 130, "a")
    second = _with_range(105, 135, "b")
    second.player_steamid = "76561198000000002"
    assert len(merge_overlapping([first, second], TICKRATE)) == 2


def test_merging_can_be_disabled():
    highlights = [_with_range(100, 130, "a"), _with_range(125, 150, "b")]
    assert len(merge_overlapping(highlights, TICKRATE, enabled=False)) == 2


def test_a_merged_clutch_stays_a_clutch():
    clutch = _highlight(HighlightKind.CLUTCH, [kill(tick=100 * SEC)], clutch_vs=3)
    clutch.id, clutch.start_tick, clutch.end_tick = "a", 90 * SEC, 110 * SEC
    other = _with_range(105, 120, "b")
    merged = merge_overlapping([clutch, other], TICKRATE)
    assert merged[0].kind == HighlightKind.CLUTCH
    assert merged[0].clutch_vs == 3


def test_apply_clip_ranges_fills_every_highlight():
    kills = [kill(tick=(20 + index * 30) * SEC, victim=f"7656119800000000{index + 5}") for index in range(3)]
    analysis = make_analysis(kills, rounds=1)
    highlights = detect_highlights(analysis, DetectorOptions.defaults())
    apply_clip_ranges(highlights, analysis, ClipSettings())
    assert all(h.end_tick > h.start_tick for h in highlights)
