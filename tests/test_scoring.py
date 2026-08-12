"""Scoring: configurable, additive, and sensitive to every real flavour."""

from __future__ import annotations

from cs2_clip_generator.core.config import ScoringSettings
from cs2_clip_generator.core.models import Highlight, HighlightKind, HighlightTag
from cs2_clip_generator.highlights.scoring import base_key, kill_tag_counts, score_highlight

from .conftest import TICKRATE, kill

SEC = int(TICKRATE)


def make_highlight(kind: HighlightKind, kills: list, tags: list[HighlightTag] | None = None, **kwargs) -> Highlight:
    return Highlight(
        id="test",
        kind=kind,
        player_steamid="76561198000000001",
        player_name="P1",
        round_number=1,
        kills=kills,
        tags=tags or [],
        **kwargs,
    )


def test_base_scores_follow_the_configured_table():
    settings = ScoringSettings()
    for kind, expected in (
        (HighlightKind.ACE, 100.0),
        (HighlightKind.MULTI_4K, 80.0),
        (HighlightKind.MULTI_3K, 60.0),
        (HighlightKind.MULTI_2K, 30.0),
    ):
        counts = {HighlightKind.ACE: 5, HighlightKind.MULTI_4K: 4, HighlightKind.MULTI_3K: 3, HighlightKind.MULTI_2K: 2}
        count = counts[kind]
        kills = [kill(tick=index * SEC, victim=f"7656119800000000{index + 5}") for index in range(count)]
        score, breakdown = score_highlight(make_highlight(kind, kills), settings)
        assert breakdown[kind.value] == expected
        assert score == expected  # nothing special about these kills


def test_clutch_base_depends_on_the_number_of_enemies():
    settings = ScoringSettings()
    for enemies, expected in ((5, 100.0), (4, 90.0), (3, 75.0), (2, 40.0), (1, 20.0)):
        highlight = make_highlight(HighlightKind.CLUTCH, [kill(tick=SEC)], clutch_vs=enemies)
        assert base_key(highlight) == f"CLUTCH_1V{enemies}"
        _score, breakdown = score_highlight(highlight, settings)
        assert breakdown[f"CLUTCH_1V{enemies}"] == expected


def test_an_ace_with_five_headshots_and_an_awp_scores_more_than_a_plain_ace():
    settings = ScoringSettings()
    plain_kills = [kill(tick=index * SEC, victim=f"7656119800000000{index + 5}") for index in range(5)]
    fancy_kills = [
        kill(
            tick=index * SEC,
            victim=f"7656119800000000{index + 5}",
            headshot=True,
            weapon="awp" if index == 0 else "ak47",
        )
        for index in range(5)
    ]
    plain, _ = score_highlight(make_highlight(HighlightKind.ACE, plain_kills), settings)
    fancy, breakdown = score_highlight(
        make_highlight(HighlightKind.ACE, fancy_kills, [HighlightTag.HEADSHOT_ONLY]), settings
    )
    assert fancy > plain
    # 100 base + 5x10 headshot + 1x15 AWP + 25 all-headshot bonus
    assert breakdown["ACE"] == 100.0
    assert breakdown["HEADSHOT x5"] == 50.0
    assert breakdown["AWP x1"] == 15.0
    assert breakdown["HEADSHOT_ONLY"] == 25.0
    assert fancy == 190.0


def test_per_kill_bonuses_are_counted_per_kill():
    kills = [
        kill(tick=SEC, weapon="awp", headshot=True),
        kill(tick=2 * SEC, weapon="awp", headshot=True, victim="76561198000000007"),
    ]
    counts = kill_tag_counts(make_highlight(HighlightKind.MULTI_2K, kills))
    assert counts["AWP"] == 2
    assert counts["HEADSHOT"] == 2


def test_scoring_is_configurable():
    settings = ScoringSettings()
    settings.base["ACE"] = 500.0
    settings.per_kill["HEADSHOT"] = 0.0
    kills = [kill(tick=index * SEC, victim=f"7656119800000000{index + 5}", headshot=True) for index in range(5)]
    score, breakdown = score_highlight(make_highlight(HighlightKind.ACE, kills), settings)
    assert breakdown["ACE"] == 500.0
    assert "HEADSHOT x5" not in breakdown
    assert score == 500.0 + settings.bonus["HEADSHOT_ONLY"]


def test_long_range_threshold_is_configurable():
    settings = ScoringSettings()
    highlight = make_highlight(HighlightKind.KILL, [kill(tick=SEC, distance=25.0)])
    without, _ = score_highlight(highlight, settings, long_range_meters=30.0)
    with_bonus, breakdown = score_highlight(highlight, settings, long_range_meters=20.0)
    assert with_bonus > without
    assert "LONG_RANGE x1" in breakdown


def test_a_clutch_that_is_also_a_multikill_gets_a_partial_bonus():
    settings = ScoringSettings()
    kills = [kill(tick=index * SEC, victim=f"7656119800000000{index + 5}") for index in range(3)]
    _score, breakdown = score_highlight(make_highlight(HighlightKind.CLUTCH, kills, clutch_vs=3), settings)
    assert breakdown["CLUTCH_1V3"] == 75.0
    assert breakdown["+3K"] == 15.0  # a quarter of the 3K base, not the full 60
