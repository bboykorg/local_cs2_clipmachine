"""Clutch detection from reconstructed alive sets."""

from __future__ import annotations

from cs2_clip_generator.core.models import HighlightKind, Team
from cs2_clip_generator.highlights.clutch import alive_counts_at, find_clutches
from cs2_clip_generator.highlights.detector import DetectorOptions, detect_highlights

from .conftest import TICKRATE, kill, make_analysis

SEC = int(TICKRATE)
from .conftest import make_players  # noqa: E402

_PLAYERS = make_players()
T_PLAYERS = [p.steamid for p in _PLAYERS if p.team == Team.T]
CT_PLAYERS = [p.steamid for p in _PLAYERS if p.team == Team.CT]


def _ct_kills_t(tick: int, victim: str, attacker: str = CT_PLAYERS[0]):
    return kill(tick=tick, attacker=attacker, victim=victim, attacker_team=Team.CT, victim_team=Team.T)


def _t_kills_ct(tick: int, victim: str, attacker: str = T_PLAYERS[0]):
    return kill(tick=tick, attacker=attacker, victim=victim, attacker_team=Team.T, victim_team=Team.CT)


def test_1v3_clutch_is_detected_when_the_hero_wins():
    kills = [
        _ct_kills_t(1_000, T_PLAYERS[1]),
        _ct_kills_t(1_100, T_PLAYERS[2]),
        _ct_kills_t(1_200, T_PLAYERS[3]),
        _ct_kills_t(1_300, T_PLAYERS[4]),  # P1 is now alone against 5 CTs...
        _t_kills_ct(1_400, CT_PLAYERS[0]),
        _t_kills_ct(1_500, CT_PLAYERS[1]),
        _t_kills_ct(1_600, CT_PLAYERS[2]),
        _t_kills_ct(1_700, CT_PLAYERS[3]),
        _t_kills_ct(1_800, CT_PLAYERS[4]),
    ]
    analysis = make_analysis(kills)
    analysis.rounds[0].winner = Team.T

    clutches = find_clutches(analysis)
    assert len(clutches) == 1
    assert clutches[0].player_steamid == T_PLAYERS[0]
    assert clutches[0].enemies_alive == 5
    assert clutches[0].won is True


def test_a_lost_clutch_is_not_reported():
    kills = [
        _ct_kills_t(1_000, T_PLAYERS[1]),
        _ct_kills_t(1_100, T_PLAYERS[2]),
        _ct_kills_t(1_200, T_PLAYERS[3]),
        _ct_kills_t(1_300, T_PLAYERS[4]),
        _t_kills_ct(1_400, CT_PLAYERS[0]),  # one kill, then dies
        _ct_kills_t(1_500, T_PLAYERS[0], attacker=CT_PLAYERS[1]),
    ]
    analysis = make_analysis(kills)
    analysis.rounds[0].winner = Team.CT
    assert find_clutches(analysis) == []


def test_a_clutch_without_kills_is_not_reported():
    """Being last alive is not a clutch; winning it with a kill is."""
    kills = [_ct_kills_t(1_000 + index * 100, T_PLAYERS[index + 1]) for index in range(4)]
    analysis = make_analysis(kills)
    analysis.rounds[0].winner = Team.T  # bomb ran out, no kills
    assert find_clutches(analysis) == []


def test_clutch_becomes_the_highlight_headline_and_carries_the_1vn():
    kills = [
        _ct_kills_t(1_000, T_PLAYERS[1]),
        _ct_kills_t(1_100, T_PLAYERS[2]),
        _ct_kills_t(1_200, T_PLAYERS[3]),
        _ct_kills_t(1_300, T_PLAYERS[4]),
        _t_kills_ct(1_400, CT_PLAYERS[0]),
        _t_kills_ct(1_500, CT_PLAYERS[1]),
    ]
    analysis = make_analysis(kills)
    analysis.rounds[0].winner = Team.T
    highlights = detect_highlights(analysis, DetectorOptions.defaults())
    hero = next(h for h in highlights if h.player_steamid == T_PLAYERS[0])
    assert hero.kind == HighlightKind.CLUTCH
    assert hero.clutch_vs == 5
    assert "1v5" in hero.title


def test_an_ace_that_is_also_a_clutch_stays_an_ace():
    kills = [
        _ct_kills_t(1_000, T_PLAYERS[1]),
        _ct_kills_t(1_100, T_PLAYERS[2]),
        _ct_kills_t(1_200, T_PLAYERS[3]),
        _ct_kills_t(1_300, T_PLAYERS[4]),
        *[_t_kills_ct(1_400 + index * 100, CT_PLAYERS[index]) for index in range(5)],
    ]
    analysis = make_analysis(kills)
    analysis.rounds[0].winner = Team.T
    highlights = detect_highlights(analysis, DetectorOptions.defaults())
    hero = next(h for h in highlights if h.player_steamid == T_PLAYERS[0])
    assert hero.kind == HighlightKind.ACE
    assert hero.clutch_vs == 5  # the clutch context is still recorded


def test_alive_counts_track_deaths_within_the_round():
    kills = [_ct_kills_t(1_000, T_PLAYERS[1]), _ct_kills_t(1_100, T_PLAYERS[2])]
    analysis = make_analysis(kills)
    assert alive_counts_at(analysis, 1, 900) == (5, 5)
    assert alive_counts_at(analysis, 1, 1_050) == (4, 5)
    assert alive_counts_at(analysis, 1, 1_200) == (3, 5)
