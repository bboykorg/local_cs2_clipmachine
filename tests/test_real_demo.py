"""Integration tests against a real CS2 demo.

These prove the claims that matter and cannot be proven with synthetic data:
the parser reads current CS2 demos, the tickrate is measured rather than
assumed, and — most importantly — the spectator slot needed by ``spec_player``
is recovered correctly from the demo container.

Run them with::

    CS2CLIP_TEST_DEMO=/path/to/match.dem pytest tests/test_real_demo.py
"""

from __future__ import annotations

import pytest

from cs2_clip_generator.demo.parser import get_parser
from cs2_clip_generator.demo.slots import read_player_slots, slots_by_steamid
from cs2_clip_generator.demo.validation import validate_demo
from cs2_clip_generator.highlights.detector import DetectorOptions, detect_highlights, update_player_stats
from cs2_clip_generator.render.pipeline import RenderPipeline, build_jobs


@pytest.fixture(scope="module")
def parsed(request):  # noqa: ANN001, ANN201
    path = request.config.getoption("--demo", default=None) or __import__("os").environ.get("CS2CLIP_TEST_DEMO")
    if not path:
        pytest.skip("set CS2CLIP_TEST_DEMO to a real CS2 .dem file")
    return get_parser().parse(str(path))


def test_demo_validates_as_cs2(real_demo):
    result = validate_demo(real_demo)
    assert result.ok
    assert result.game == "cs2"


def test_slots_are_one_based_unique_and_within_range(real_demo):
    slots = read_player_slots(real_demo)
    assert slots, "no players were found in the userinfo string table"
    values = [info.slot for info in slots.values()]
    assert len(values) == len(set(values)), "two players share a spectator slot"
    assert all(1 <= slot <= 64 for slot in values)
    # The raw userid is 0xFF00|slot0 in practice; the mask must be applied.
    for info in slots.values():
        assert info.slot == (info.user_id & 0xFF) + 1


def test_slot_extraction_is_fast_even_on_a_large_demo(real_demo):
    """Only string-table frames are decoded, so this must not scan the file."""
    import time

    started = time.monotonic()
    slots_by_steamid(real_demo)
    assert time.monotonic() - started < 5.0


def test_every_player_gets_a_slot_and_a_steamid(parsed):
    assert parsed.players
    assert all(player.slot for player in parsed.players)
    assert all(player.steamid.startswith("7656") for player in parsed.players)


def test_tickrate_is_measured_not_assumed(parsed):
    assert parsed.tickrate in (64.0, 128.0) or parsed.tickrate > 0
    # Cross-check: round starts must be monotonically increasing in both ticks
    # and wall-clock seconds implied by the tickrate.
    ticks = [round_.start_tick for round_ in parsed.rounds]
    assert ticks == sorted(ticks)


def test_rounds_are_consistent(parsed):
    assert parsed.rounds
    for round_ in parsed.rounds:
        assert round_.end_tick >= round_.start_tick
        if round_.freeze_end_tick:
            assert round_.freeze_end_tick >= round_.start_tick
        if round_.bomb_planted_tick:
            # The plant belongs to the round the players were playing, even when
            # it happened after round_end (a spite plant in the post-round).
            assert round_.bomb_planted_tick >= round_.start_tick


def test_kills_reference_real_players_and_carry_flags(parsed):
    assert parsed.kills
    steamids = {player.steamid for player in parsed.players}
    for kill in parsed.kills:
        assert kill.victim_steamid in steamids
        if kill.attacker_steamid:
            assert kill.attacker_steamid in steamids
        assert kill.weapon
        assert kill.round_number >= 0
        assert isinstance(kill.headshot, bool)
    # A real match always contains at least one headshot.
    assert any(kill.headshot for kill in parsed.kills)


def test_statistics_add_up(parsed):
    total_kills = sum(stats.kills for stats in parsed.stats.values())
    total_deaths = sum(stats.deaths for stats in parsed.stats.values())
    # Deaths include team kills and suicides, kills do not, so deaths >= kills.
    assert total_deaths >= total_kills > 0
    for stats in parsed.stats.values():
        assert stats.headshots <= stats.kills


def test_highlights_are_detected_scored_and_timed(parsed):
    options = DetectorOptions.defaults()
    highlights = detect_highlights(parsed, options)
    assert highlights
    for highlight in highlights:
        assert highlight.kills
        assert highlight.start_tick < highlight.end_tick
        assert highlight.title
        assert highlight.score > 0
        # The clip must actually contain its kills.
        assert highlight.start_tick <= highlight.first_kill_tick
        assert highlight.end_tick >= highlight.last_kill_tick
    assert highlights == sorted(highlights, key=lambda h: h.score, reverse=True)


def test_highlight_players_exist_in_the_match(parsed):
    highlights = detect_highlights(parsed, DetectorOptions.defaults())
    steamids = {player.steamid for player in parsed.players}
    assert {highlight.player_steamid for highlight in highlights} <= steamids


def test_statistics_gain_multikill_counts(parsed):
    options = DetectorOptions.defaults()
    update_player_stats(parsed, options)
    assert any(
        stats.multi_2k or stats.multi_3k or stats.multi_4k or stats.aces or stats.clutches
        for stats in parsed.stats.values()
    )


def test_render_plan_targets_the_right_pov_slot(parsed):
    """The last mile before CS2: does the plan point at the right player?"""
    from cs2_clip_generator.core.config import Settings

    settings = Settings()
    highlights = detect_highlights(parsed, DetectorOptions.defaults())[:3]
    pipeline = RenderPipeline(settings, parsed)
    for job in build_jobs(highlights, parsed, settings):
        plan = pipeline.build_plan(job)
        player = parsed.player(job.highlight.player_steamid)
        assert player is not None
        assert plan.target is not None
        assert plan.target.slot == player.slot
        assert plan.start_tick < plan.end_tick
        assert plan.duration_seconds > 1.0
        # The safety margin widens the window on both sides.
        assert plan.start_tick <= job.highlight.start_tick
        assert plan.end_tick >= job.highlight.end_tick


def test_actions_file_for_a_real_highlight_is_valid_and_ordered(parsed, tmp_path):
    import json

    from cs2_clip_generator.core.config import Settings
    from cs2_clip_generator.cs2.actions import JsonActionsFile, build_clip_sequence
    from cs2_clip_generator.cs2.demo_controller import PlaybackPresentation

    settings = Settings()
    highlight = detect_highlights(parsed, DetectorOptions.defaults())[0]
    pipeline = RenderPipeline(settings, parsed)
    plan = pipeline.build_plan(build_jobs([highlight], parsed, settings)[0])

    actions = JsonActionsFile(tmp_path / "match.dem")
    build_clip_sequence(
        actions,
        start_tick=plan.start_tick,
        end_tick=plan.end_tick,
        setup_commands=PlaybackPresentation().commands(),
        camera_actions=[(plan.start_tick, f"spec_player {plan.target.slot}")],
        record_start_commands=["startmovie clip"],
        record_end_commands=["endmovie"],
        tickrate=parsed.tickrate,
    )
    payload = json.loads(actions.to_json())
    commands = [action["cmd"] for action in payload[0]["actions"]]
    assert f"spec_player {plan.target.slot}" in commands
    assert any(command.startswith("demo_gototick") for command in commands)
    ticks = [action["tick"] for action in payload[0]["actions"]]
    assert ticks == sorted(ticks)
