"""CS2 control: actions file, launch arguments, console commands, camera."""

from __future__ import annotations

import json

from cs2_clip_generator.core.models import Player, Team
from cs2_clip_generator.cs2 import demo_controller
from cs2_clip_generator.cs2.actions import JsonActionsFile, build_clip_sequence, delete_actions_file
from cs2_clip_generator.cs2.launcher import LaunchOptions, build_launch_args, parse_vdf, split_extra_args
from cs2_clip_generator.cs2.player_controller import (
    CameraMode,
    SpectatorTarget,
    spectate_commands,
)

TICKRATE = 64.0


# ---------------------------------------------------------------------------
# JSON actions file
# ---------------------------------------------------------------------------


def test_actions_file_is_written_next_to_the_demo(tmp_path):
    demo = tmp_path / "match.dem"
    demo.write_bytes(b"PBDEMS2\x00")
    actions = JsonActionsFile(demo)
    assert actions.path.name == "match.dem.json"
    actions.add(1, "sv_cheats 1").write()
    assert actions.path.is_file()
    delete_actions_file(demo)
    assert not actions.path.is_file()


def test_commands_on_the_same_tick_keep_their_insertion_order(tmp_path):
    """Sorting must not reorder same-tick commands alphabetically.

    ``spec_mode`` has to precede ``spec_player``, and ``endmovie`` has to precede
    the marker that announces the end of the clip — both would break under a
    plain (tick, command) sort.
    """
    actions = JsonActionsFile(tmp_path / "m.dem")
    actions.add(100, "endmovie").add(100, 'echo "cs2clip_end_0"').add(100, "spec_mode 1").add(100, "spec_player 4")
    commands = [action["cmd"] for action in actions.to_list()[0]["actions"]]  # type: ignore[index]
    assert commands == ["endmovie", 'echo "cs2clip_end_0"', "spec_mode 1", "spec_player 4"]


def test_actions_are_sorted_by_tick_and_shaped_as_sequences(tmp_path):
    actions = JsonActionsFile(tmp_path / "m.dem")
    actions.add(500, "late").add(10, "early").end_sequence()
    payload = json.loads(actions.to_json())
    assert isinstance(payload, list) and len(payload) == 1
    ticks = [action["tick"] for action in payload[0]["actions"]]
    assert ticks == sorted(ticks)
    assert set(payload[0]["actions"][0]) == {"cmd", "tick"}


def test_tick_zero_is_promoted_to_one():
    """The plugin only starts watching at tick 1; tick 0 actions would be lost."""
    actions = JsonActionsFile("m.dem")
    actions.add(0, "sv_cheats 1")
    assert actions.to_list()[0]["actions"][0]["tick"] == 1  # type: ignore[index]


def test_spectate_always_sets_the_mode_before_the_target():
    actions = JsonActionsFile("m.dem")
    actions.add_spectate(100, slot=4)
    commands = [action["cmd"] for action in actions.to_list()[0]["actions"]]  # type: ignore[index]
    assert commands == ["spec_mode 1", "spec_player 4"]


def test_clip_sequence_seeks_first_records_at_the_start_tick_and_stops_at_the_end():
    actions = JsonActionsFile("m.dem")
    build_clip_sequence(
        actions,
        start_tick=10_000,
        end_tick=10_640,
        setup_commands=["sv_cheats 1"],
        camera_actions=[(10_000, "spec_mode 1"), (10_000, "spec_player 4")],
        record_start_commands=["startmovie clip"],
        record_end_commands=["endmovie"],
        tickrate=TICKRATE,
    )
    by_command = {
        action["cmd"]: action["tick"] for action in actions.to_list()[0]["actions"]  # type: ignore[index]
    }
    setup_tick = 10_000 - int(TICKRATE)
    assert by_command[f"demo_gototick {setup_tick - 1}"] == 1
    assert by_command["startmovie clip"] == 10_000
    assert by_command["endmovie"] == 10_640
    assert by_command["pause_playback"] == 10_000 - 4
    # The seek target is a second early so the setup commands are not skipped.
    assert setup_tick < 10_000


def test_multiple_clips_become_multiple_sequences():
    actions = JsonActionsFile("m.dem")
    for start in (1_000, 5_000):
        build_clip_sequence(
            actions,
            start_tick=start,
            end_tick=start + 640,
            setup_commands=[],
            camera_actions=[],
            tickrate=TICKRATE,
        )
        actions.end_sequence()
    assert len(actions.to_list()) == 2


# ---------------------------------------------------------------------------
# Launch arguments
# ---------------------------------------------------------------------------


def test_launch_args_load_the_demo_and_stay_insecure():
    args = build_launch_args("cs2.exe", LaunchOptions(demo_path="C:/demos/match.dem"))
    assert args[0] == "cs2.exe"
    assert "-insecure" in args and "-novid" in args
    assert args[args.index("+playdemo") + 1] == "C:/demos/match.dem"


def test_launch_args_are_a_list_so_paths_with_spaces_are_safe():
    args = build_launch_args("cs2.exe", LaunchOptions(demo_path="C:/my demos/match 1.dem"))
    assert "C:/my demos/match 1.dem" in args  # one element, not split


def test_display_modes_map_to_real_flags():
    windowed = build_launch_args("cs2", LaunchOptions(display_mode="windowed", width=1280, height=720))
    assert "-sw" in windowed and windowed[windowed.index("-width") + 1] == "1280"
    fullscreen = build_launch_args("cs2", LaunchOptions(display_mode="fullscreen"))
    assert "-fullscreen" in fullscreen and "-width" not in fullscreen
    borderless = build_launch_args("cs2", LaunchOptions(display_mode="borderless"))
    assert "-noborder" in borderless


def test_netcon_port_is_only_added_when_requested():
    assert "-netconport" not in build_launch_args("cs2", LaunchOptions())
    args = build_launch_args("cs2", LaunchOptions(netcon_port=29070))
    assert args[args.index("-netconport") + 1] == "29070"


def test_hlae_wrapper_passes_the_game_arguments_through_cmdline():
    options = LaunchOptions(demo_path="m.dem", launch_wrapper=["HLAE.exe", "-customLoader", "-gameExe", "cs2.exe"])
    args = build_launch_args("cs2.exe", options)
    assert args[0] == "HLAE.exe"
    assert "-cmdLine" in args
    cmdline = args[args.index("-cmdLine") + 1]
    assert "+playdemo m.dem" in cmdline
    assert "cs2.exe" not in cmdline.split()  # the game exe is HLAE's own argument


def test_extra_arguments_are_split_without_a_shell():
    assert split_extra_args('-high +fps_max 0') == ["-high", "+fps_max", "0"]
    assert split_extra_args("") == []


# ---------------------------------------------------------------------------
# Console command builders
# ---------------------------------------------------------------------------


def test_demo_commands_are_the_real_console_commands():
    assert demo_controller.goto_tick(1234) == "demo_gototick 1234"
    assert demo_controller.goto_tick(-5) == "demo_gototick 0"
    assert demo_controller.play_demo("C:/a b/m.dem") == 'playdemo "C:/a b/m.dem"'
    assert demo_controller.timescale(2) == "demo_timescale 2"
    assert demo_controller.pause() == "demo_pause"


def test_quotes_cannot_escape_out_of_a_console_command():
    command = demo_controller.play_demo('m".dem')
    argument = command[len("playdemo ") :]
    # Exactly one pair of quotes, wrapping the whole argument.
    assert argument.startswith('"') and argument.endswith('"')
    assert argument.count('"') == 2


def test_presentation_hides_the_telemetry_overlays_and_sets_cheats():
    commands = demo_controller.PlaybackPresentation().commands()
    assert "sv_cheats 1" in commands
    assert any("telemetry" in command for command in commands)
    assert "r_show_build_info 0" in commands


def test_player_voices_toggle_uses_gotv_convars():
    on = demo_controller.PlaybackPresentation(player_voices=True).commands()
    off = demo_controller.PlaybackPresentation(player_voices=False).commands()
    assert "tv_listen_voice_indices -1" in on
    assert "tv_listen_voice_indices 0" in off


def test_startmovie_pins_the_framerate():
    assert demo_controller.start_movie("clip", 60) == ["host_framerate 60", 'startmovie "clip"']
    assert demo_controller.end_movie() == ["endmovie", "host_framerate 0"]


def test_hlae_preset_uses_hlae_quote_escaping():
    command = demo_controller.hlae_ffmpeg_preset("p", "-c:v libx264", "C:/out/video.mp4")
    assert "{QUOTE}C:/out/video.mp4{QUOTE}" in command


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------


def test_player_pov_sets_first_person_before_and_after_the_target():
    player = Player(steamid="76561198000000001", name="P1", team=Team.T, slot=4)
    commands = spectate_commands(SpectatorTarget.from_player(player), CameraMode.PLAYER_POV)
    assert commands[0] == "spec_mode 1"
    assert commands[1] == "spec_player 4"
    assert commands[-1] == "spec_mode 1"


def test_free_camera_does_not_target_a_player():
    commands = spectate_commands(SpectatorTarget(slot=4), CameraMode.FREE_CAMERA)
    assert commands == ["spec_mode 6"]


def test_third_person_uses_spec_mode_two():
    commands = spectate_commands(SpectatorTarget(slot=2), CameraMode.THIRD_PERSON)
    assert commands[0] == "spec_mode 2"
    assert "spec_player 2" in commands


def test_camera_plan_reasserts_the_pov_around_every_kill():
    from cs2_clip_generator.core.models import Highlight, HighlightKind, KillEvent
    from cs2_clip_generator.cs2.camera_controller import PlayerPovCameraController

    highlight = Highlight(
        id="h",
        kind=HighlightKind.MULTI_3K,
        player_steamid="76561198000000001",
        player_name="P1",
        round_number=1,
        kills=[
            KillEvent(tick=tick, round_number=1, time=0, attacker_steamid="1", attacker_name="P1",
                      victim_steamid="2", victim_name="P2", weapon="ak47")
            for tick in (10_500, 10_600, 10_700)
        ],
        start_tick=10_000,
        end_tick=11_000,
    )
    keyframes = PlayerPovCameraController().plan(highlight, SpectatorTarget(slot=4), TICKRATE)
    assert len(keyframes) >= 4  # clip start + settle + one per kill
    assert all(keyframe.commands[0] == "spec_mode 1" for keyframe in keyframes)
    assert all(10_000 <= keyframe.tick <= 11_000 for keyframe in keyframes)


# ---------------------------------------------------------------------------
# Steam library discovery
# ---------------------------------------------------------------------------


def test_vdf_parser_reads_nested_library_folders():
    text = """
    "libraryfolders"
    {
        "0"
        {
            "path"		"C:\\\\Program Files (x86)\\\\Steam"
            "apps"
            {
                "730"		"12345"
            }
        }
        "1"
        {
            "path"		"D:\\\\SteamLibrary"
        }
    }
    """
    data = parse_vdf(text)
    folders = data["libraryfolders"]
    assert folders["0"]["path"].endswith("Steam")
    assert folders["1"]["path"].endswith("SteamLibrary")
    assert folders["0"]["apps"]["730"] == "12345"
