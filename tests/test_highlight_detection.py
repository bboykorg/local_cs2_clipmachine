"""Highlight detection: kinds, tags, ACE, and what must never be invented."""

from __future__ import annotations

from cs2_clip_generator.core.models import HighlightKind, HighlightTag
from cs2_clip_generator.highlights.detector import (
    DetectorOptions,
    auto_select,
    detect_highlights,
    update_player_stats,
)

from .conftest import TICKRATE, kill, make_analysis

SEC = int(TICKRATE)


def _options(**clip_overrides: object) -> DetectorOptions:
    options = DetectorOptions.defaults()
    for key, value in clip_overrides.items():
        setattr(options.clips, key, value)
    return options


def test_two_kills_become_a_2k_three_a_3k_and_five_an_ace():
    for count, expected in ((2, HighlightKind.MULTI_2K), (3, HighlightKind.MULTI_3K), (4, HighlightKind.MULTI_4K)):
        kills = [kill(tick=(10 + i * 2) * SEC, victim=f"7656119800000000{i + 5}") for i in range(count)]
        highlights = detect_highlights(make_analysis(kills), _options(merge_overlapping=False))
        assert [h.kind for h in highlights] == [expected], f"{count} kills"

    ace = [kill(tick=(10 + i * 2) * SEC, victim=f"7656119800000000{i + 5}") for i in range(5)]
    highlights = detect_highlights(make_analysis(ace), _options(merge_overlapping=False))
    assert highlights[0].kind == HighlightKind.ACE
    assert highlights[0].has_tag(HighlightTag.ACE)


def test_tags_come_only_from_real_demo_flags():
    kills = [
        kill(tick=10 * SEC, weapon="awp", noscope=True),
        kill(tick=12 * SEC, weapon="ak47", penetrated=1, victim="76561198000000007"),
        kill(tick=14 * SEC, weapon="ak47", through_smoke=True, victim="76561198000000008"),
        kill(tick=16 * SEC, weapon="knife", victim="76561198000000009", attacker_in_air=True),
    ]
    highlight = detect_highlights(make_analysis(kills), _options(merge_overlapping=False))[0]
    tags = set(highlight.tags)
    assert {
        HighlightTag.AWP,
        HighlightTag.NOSCOPE,
        HighlightTag.WALLBANG,
        HighlightTag.THROUGH_SMOKE,
        HighlightTag.KNIFE,
        HighlightTag.JUMPING,
    } <= tags
    # Nothing was blinded or a headshot, so those tags must be absent.
    assert HighlightTag.BLINDED not in tags
    assert HighlightTag.HEADSHOT not in tags


def test_all_headshot_sequences_are_marked_and_single_kills_are_not():
    kills = [kill(tick=10 * SEC, headshot=True), kill(tick=12 * SEC, headshot=True, victim="76561198000000007")]
    highlight = detect_highlights(make_analysis(kills), _options(merge_overlapping=False))[0]
    assert highlight.has_tag(HighlightTag.HEADSHOT_ONLY)

    one_kill = make_analysis([kill(tick=10 * SEC, headshot=True)])
    single = detect_highlights(one_kill, _options(merge_overlapping=False))[0]
    assert not single.has_tag(HighlightTag.HEADSHOT_ONLY)


def test_post_plant_tag_needs_an_actual_bomb_plant():
    kills = [kill(tick=5_000)]
    analysis = make_analysis(kills)
    assert not detect_highlights(analysis, _options())[0].has_tag(HighlightTag.POST_PLANT)

    analysis.rounds[0].bomb_planted_tick = 4_000
    assert detect_highlights(analysis, _options())[0].has_tag(HighlightTag.POST_PLANT)


def test_titles_are_generated_and_mention_the_round():
    kills = [kill(tick=10 * SEC, weapon="ak47", headshot=True)]
    highlight = detect_highlights(make_analysis(kills), _options())[0]
    assert "Round 1" in highlight.title
    assert highlight.title


def test_multikill_statistics_use_the_strict_window_not_merged_clips():
    """Two kills 20 s apart are two kills, even if one clip shows both."""
    kills = [kill(tick=10 * SEC), kill(tick=30 * SEC, victim="76561198000000007")]
    analysis = make_analysis(kills)
    options = _options(merge_overlapping=True, merge_gap_seconds=30.0)
    highlights = detect_highlights(analysis, options)
    update_player_stats(analysis, options)

    assert len(highlights) == 1 and highlights[0].kill_count == 2  # merged into one clip
    assert analysis.stats["76561198000000001"].multi_2k == 0  # but not a 2K


def test_auto_select_respects_max_and_min_score():
    kills = [kill(tick=(10 + index * 20) * SEC, victim=f"7656119800000000{index + 5}") for index in range(4)]
    highlights = detect_highlights(make_analysis(kills), _options(merge_overlapping=False))
    for index, highlight in enumerate(highlights):
        highlight.score = 100 - index * 30  # 100, 70, 40, 10

    selected = auto_select(highlights, max_clips=2, min_score=40)
    assert [h.score for h in selected] == [100, 70]
    assert all(h.score >= 40 for h in auto_select(highlights, max_clips=10, min_score=40))


def test_auto_select_can_be_limited_to_one_player():
    kills = [
        kill(tick=10 * SEC, attacker="76561198000000001"),
        kill(tick=40 * SEC, attacker="76561198000000002", victim="76561198000000007"),
    ]
    highlights = detect_highlights(make_analysis(kills), _options())
    selected = auto_select(highlights, 10, 0, steamid="76561198000000002")
    assert {h.player_steamid for h in selected} == {"76561198000000002"}


def test_detector_is_deterministic():
    kills = [kill(tick=(10 + index * 2) * SEC, victim=f"7656119800000000{index + 5}") for index in range(3)]
    first = detect_highlights(make_analysis(kills), _options())
    second = detect_highlights(make_analysis(kills), _options())
    assert [h.id for h in first] == [h.id for h in second]
    assert [h.score for h in first] == [h.score for h in second]
