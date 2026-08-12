"""Multi-kill grouping — the rule users notice most when it is wrong."""

from __future__ import annotations

from cs2_clip_generator.core.models import Team
from cs2_clip_generator.highlights.multikill import group_kills, relevant_kills

from .conftest import TICKRATE, kill

SEC = int(TICKRATE)


def test_kills_within_the_window_chain_into_one_group():
    # 12:10, 12:13, 12:16 -> one 3K (each gap is 3s, the window is 7s)
    kills = [kill(tick=100 * SEC), kill(tick=103 * SEC), kill(tick=106 * SEC)]
    groups = group_kills(kills, TICKRATE, window_seconds=7.0)
    assert len(groups) == 1
    assert groups[0].count == 3


def test_kills_beyond_the_window_stay_separate():
    # 12:10 and 12:30 -> two separate events
    kills = [kill(tick=100 * SEC), kill(tick=120 * SEC)]
    groups = group_kills(kills, TICKRATE, window_seconds=7.0)
    assert [group.count for group in groups] == [1, 1]


def test_window_is_measured_between_consecutive_kills_not_from_the_first():
    # Gaps of 6s each: the sequence lasts 18s but every link is inside a 7s window.
    kills = [kill(tick=t * SEC) for t in (10, 16, 22, 28)]
    groups = group_kills(kills, TICKRATE, window_seconds=7.0)
    assert len(groups) == 1
    assert groups[0].count == 4


def test_window_is_configurable():
    kills = [kill(tick=10 * SEC), kill(tick=19 * SEC)]
    assert len(group_kills(kills, TICKRATE, window_seconds=7.0)) == 2
    assert len(group_kills(kills, TICKRATE, window_seconds=10.0)) == 1


def test_grouping_is_per_player_and_per_round():
    kills = [
        kill(tick=10 * SEC, attacker="76561198000000001", round_number=1),
        kill(tick=11 * SEC, attacker="76561198000000002", round_number=1),
        kill(tick=12 * SEC, attacker="76561198000000001", round_number=2),
    ]
    groups = group_kills(kills, TICKRATE)
    assert len(groups) == 3
    assert all(group.count == 1 for group in groups)


def test_five_kills_in_a_round_are_one_ace_even_when_spread_out():
    # A round-long ACE: 30 second gaps, far beyond the multi-kill window.
    kills = [kill(tick=t * SEC, victim=f"7656119800000000{i + 5}") for i, t in enumerate((10, 40, 70, 100, 130))]
    groups = group_kills(kills, TICKRATE, window_seconds=7.0)
    assert len(groups) == 1
    assert groups[0].count == 5
    assert groups[0].is_ace is True


def test_teamkills_suicides_and_warmup_are_not_highlights():
    kills = [
        kill(tick=SEC, attacker="76561198000000001", victim="76561198000000002", victim_team=Team.T),  # teamkill
        kill(tick=2 * SEC, attacker="76561198000000001", victim="76561198000000001"),  # suicide
        kill(tick=3 * SEC, round_number=0),  # warmup
        kill(tick=4 * SEC),  # the only real one
    ]
    assert len(relevant_kills(kills)) == 1
    assert len(group_kills(kills, TICKRATE)) == 1


def test_warmup_can_be_included_explicitly():
    kills = [kill(tick=SEC, round_number=0)]
    assert group_kills(kills, TICKRATE) == []
    assert len(group_kills(kills, TICKRATE, include_warmup=True)) == 1


def test_grouping_is_tickrate_independent():
    """The same 3 second gaps group identically on a 128 tick demo."""
    kills_64 = [kill(tick=int(t * 64)) for t in (10, 13, 16)]
    kills_128 = [kill(tick=int(t * 128)) for t in (10, 13, 16)]
    assert len(group_kills(kills_64, 64.0, 7.0)) == len(group_kills(kills_128, 128.0, 7.0)) == 1
