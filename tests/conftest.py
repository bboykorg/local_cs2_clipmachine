"""Shared fixtures.

Two kinds of tests live here: pure-logic tests built on synthetic matches, and
integration tests that run against a real CS2 demo. The real demo is optional —
set ``CS2CLIP_TEST_DEMO`` to a ``.dem`` file and the integration tests come to
life; without it they skip instead of failing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cs2_clip_generator.core.models import (
    KillEvent,
    MatchAnalysis,
    Player,
    PlayerStats,
    Round,
    Team,
)

TICKRATE = 64.0


def kill(
    tick: int,
    attacker: str = "76561198000000001",
    victim: str = "76561198000000006",
    round_number: int = 1,
    weapon: str = "ak47",
    headshot: bool = False,
    attacker_team: Team = Team.T,
    victim_team: Team = Team.CT,
    **kwargs: object,
) -> KillEvent:
    """A kill event with sensible defaults; override what a test cares about."""
    return KillEvent(
        tick=tick,
        round_number=round_number,
        time=tick / TICKRATE,
        attacker_steamid=attacker,
        attacker_name=f"P{attacker[-1]}",
        victim_steamid=victim,
        victim_name=f"P{victim[-1]}",
        weapon=weapon,
        headshot=headshot,
        attacker_team=attacker_team,
        victim_team=victim_team,
        **kwargs,  # type: ignore[arg-type]
    )


def make_players() -> list[Player]:
    players = []
    for index in range(1, 6):
        players.append(
            Player(steamid=f"7656119800000000{index}", name=f"P{index}", team=Team.T, slot=index)
        )
    for index in range(6, 11):
        players.append(
            Player(
                steamid=f"765611980000000{index if index > 9 else '0' + str(index)}",
                name=f"P{index}",
                team=Team.CT,
                slot=index,
            )
        )
    return players


def make_analysis(kills: list[KillEvent] | None = None, rounds: int = 3) -> MatchAnalysis:
    """A three-round de_mirage match with 10 players and no kills by default."""
    players = make_players()
    round_list = []
    for number in range(1, rounds + 1):
        start = (number - 1) * 10_000
        round_list.append(
            Round(
                number=number,
                start_tick=start,
                end_tick=start + 8_000,
                freeze_end_tick=start + 1_000,
                official_end_tick=start + 9_000,
                winner=Team.T,
            )
        )
    analysis = MatchAnalysis(
        demo_path="/tmp/match.dem",
        demo_sha1="deadbeef",
        map_name="de_mirage",
        tickrate=TICKRATE,
        total_ticks=rounds * 10_000,
        duration_seconds=rounds * 10_000 / TICKRATE,
        players=players,
        rounds=round_list,
        kills=kills or [],
    )
    analysis.stats = {
        player.steamid: PlayerStats(steamid=player.steamid, name=player.name, team=player.team)
        for player in players
    }
    return analysis


@pytest.fixture
def analysis() -> MatchAnalysis:
    return make_analysis()


@pytest.fixture
def real_demo() -> Path:
    path = os.environ.get("CS2CLIP_TEST_DEMO", "")
    if not path or not Path(path).is_file():
        pytest.skip("set CS2CLIP_TEST_DEMO to a real CS2 .dem file to run this test")
    return Path(path)


@pytest.fixture
def ffmpeg_available() -> bool:
    from cs2_clip_generator.video.ffmpeg import find_ffmpeg

    if not find_ffmpeg():
        pytest.skip("FFmpeg is not installed")
    return True


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch) -> Path:  # noqa: ANN001
    """Keep tests away from the developer's real settings and cache."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CS2CLIP_HOME", str(home))
    return home


def pytest_addoption(parser) -> None:  # noqa: ANN001
    parser.addoption("--demo", action="store", default=None, help="path to a real CS2 demo for integration tests")
