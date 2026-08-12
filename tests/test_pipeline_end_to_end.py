"""End-to-end pipeline test with a stand-in for CS2.

Everything except the game itself is the real code path: the real render
pipeline, the real JSON actions file, the real plugin-backed playback
controller, the real ``startmovie`` recorder, the real FFmpeg encode and the
real metadata writer.

In place of Counter-Strike 2 there is a small script that behaves the way CS2
behaves when the tick-scheduling plugin is loaded:

* it reads ``<demo>.dem.json``,
* it echoes the scheduled ``echo cs2clip_start_N`` / ``..._end_N`` markers into
  ``game/csgo/console.log`` (which is what ``-condebug`` does),
* when it sees ``startmovie <name>`` it writes a TGA frame sequence and a WAV
  into ``game/csgo/movie`` — exactly where and how the engine does.

If the orchestration is wrong — a command scheduled on the wrong tick, a marker
never waited for, a frame pattern that FFmpeg cannot read, a metadata field that
does not survive the round trip — this test fails. What it cannot prove is that
CS2 itself renders the right frame; that needs Windows and the game, and the
README says so.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from cs2_clip_generator.core.config import Settings
from cs2_clip_generator.core.models import JobState
from cs2_clip_generator.highlights.detector import DetectorOptions, detect_highlights
from cs2_clip_generator.render.pipeline import RenderPipeline, build_jobs

from .conftest import TICKRATE, kill, make_analysis

SEC = int(TICKRATE)

FAKE_CS2 = r'''#!/usr/bin/env python3
"""A stand-in for cs2.exe that honours a JSON actions file."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
demo = ""
for index, argument in enumerate(args):
    if argument == "+playdemo" and index + 1 < len(args):
        demo = args[index + 1].strip('"')

game_dir = Path(__file__).resolve().parents[2] / "csgo"
console_log = game_dir / "console.log"
movie_dir = game_dir / "movie"
movie_dir.mkdir(parents=True, exist_ok=True)


def log(line):
    with open(console_log, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()


log("Fake CS2 started: " + " ".join(args))
actions_path = Path(demo + ".json") if demo else None
if actions_path is None or not actions_path.is_file():
    log("no actions file")
    sys.exit(0)

sequences = json.loads(actions_path.read_text(encoding="utf-8"))
log("loaded %d sequences" % len(sequences))

for sequence in sequences:
    movie_name = None
    for action in sorted(sequence["actions"], key=lambda a: a["tick"]):
        command = action["cmd"]
        log("[%d] %s" % (action["tick"], command))
        if command.startswith("echo "):
            log(command[len("echo "):].strip('"'))
        elif command.startswith("startmovie"):
            movie_name = command.split(" ", 1)[1].strip().strip('"')
        elif command.startswith("endmovie") and movie_name:
            # The engine writes an uncompressed TGA per frame plus one WAV.
            pattern = str(movie_dir / (movie_name + "%04d.tga"))
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=1",
                 pattern],
                check=True,
            )
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                 str(movie_dir / (movie_name + ".wav"))],
                check=True,
            )
            log("wrote movie " + movie_name)
            movie_name = None
        time.sleep(0.01)
log("Fake CS2 exiting")
'''


@pytest.fixture
def fake_cs2(tmp_path) -> Path:
    """A CS2 installation layout with the plugin already 'installed'."""
    root = tmp_path / "steam" / "steamapps" / "common" / "Counter-Strike Global Offensive"
    bin_dir = root / "game" / "bin" / ("win64" if os.name == "nt" else "linuxsteamrt64")
    bin_dir.mkdir(parents=True)
    game_dir = root / "game" / "csgo"
    (game_dir / "cfg").mkdir(parents=True)

    # gameinfo.gi patched the way the plugin needs, plus the plugin binary.
    (game_dir / "gameinfo.gi").write_text(
        'GameInfo\n{\n\tFileSystem\n\t{\n\t\tSearchPaths\n\t\t{\n\t\t\tGame\tcsgo/csdm\n\t\t\tGame\tcsgo\n\t\t}\n\t}\n}\n',
        encoding="utf-8",
    )
    plugin_bin = game_dir / "csdm" / "bin" / ("win64" if os.name == "nt" else "linuxsteamrt64")
    plugin_bin.mkdir(parents=True)
    (plugin_bin / ("server.dll" if os.name == "nt" else "libserver.so")).write_bytes(b"fake plugin")

    executable = bin_dir / ("cs2.exe" if os.name == "nt" else "cs2")
    executable.write_text(FAKE_CS2, encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return executable


@pytest.fixture
def settings_for(tmp_path, fake_cs2) -> Settings:
    settings = Settings()
    settings.paths.cs2_executable = str(fake_cs2)
    settings.paths.output_dir = str(tmp_path / "clips")
    settings.paths.temp_dir = str(tmp_path / "temp")
    settings.recording.backend = "native"  # CS2's own startmovie
    settings.recording.playback_backend = "plugin"  # tick-accurate
    settings.recording.close_game_after_render = True
    settings.recording.demo_load_timeout = 40.0
    settings.recording.stabilisation_seconds = 0.1
    settings.video.width, settings.video.height, settings.video.fps = 320, 180, 30
    settings.video.bitrate_kbps = 1200
    settings.video.encoder = "cpu"
    settings.ensure_dirs()
    return settings


@pytest.fixture
def demo_and_analysis(tmp_path):
    demo = tmp_path / "match.dem"
    demo.write_bytes(b"PBDEMS2\x00" + b"\x00" * 4096)
    kills = [
        kill(tick=20 * SEC, weapon="awp", headshot=True),
        kill(tick=22 * SEC, weapon="awp", victim="76561198000000007"),
    ]
    analysis = make_analysis(kills, rounds=1)
    analysis.demo_path = str(demo)
    analysis.total_ticks = 200 * SEC
    analysis.rounds[0].end_tick = 180 * SEC
    analysis.rounds[0].official_end_tick = 190 * SEC
    return demo, analysis


@pytest.mark.usefixtures("ffmpeg_available")
@pytest.mark.skipif(os.name == "nt", reason="the stand-in script is POSIX; on Windows CS2 itself is used")
def test_full_pipeline_produces_a_real_mp4_and_metadata(settings_for, demo_and_analysis, tmp_path):
    demo, analysis = demo_and_analysis
    highlights = detect_highlights(analysis, DetectorOptions.defaults())
    assert highlights, "the fixture must produce at least one highlight"
    jobs = build_jobs(highlights[:1], analysis, settings_for)

    pipeline = RenderPipeline(settings_for, analysis)
    progress: list[tuple[float, str]] = []
    report = pipeline.run(jobs, on_progress=lambda job, fraction, message: progress.append((fraction, message)))

    # 1. A clip came out, and it is a real video file.
    assert not report.failed, report.failed
    assert len(report.clips) == 1
    clip = report.clips[0]
    video = Path(jobs[0].output_path)
    assert video.is_file()
    assert video.stat().st_size > 2000
    duration = pipeline.ffmpeg.probe_duration(str(video))
    assert duration and duration > 0.2

    # 2. It is really an MP4 with a video stream, not a renamed file.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
         "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    assert any(stream["codec_type"] == "video" for stream in streams)

    # 3. The metadata sidecar describes the highlight.
    metadata = Path(clip.metadata_path)
    assert metadata.is_file()
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    assert payload["type"] == highlights[0].kind.value
    assert payload["round"] == highlights[0].round_number
    assert payload["player"] == highlights[0].player_name
    assert payload["start_tick"] == highlights[0].start_tick
    assert payload["video"] == video.name
    assert payload["kills"]

    # 4. The job ended up in the right state and reported progress honestly.
    assert jobs[0].state == JobState.DONE
    assert progress and progress[-1][0] == 1.0
    assert [fraction for fraction, _ in progress] == sorted(fraction for fraction, _ in progress)

    # 5. The output layout is CS2Clips/<match>/<player>/<clip>.
    assert video.parent.name == highlights[0].player_name
    assert video.parent.parent.parent == Path(settings_for.paths.output_dir)


@pytest.mark.usefixtures("ffmpeg_available")
@pytest.mark.skipif(os.name == "nt", reason="the stand-in script is POSIX; on Windows CS2 itself is used")
def test_the_actions_file_the_game_received_is_correct(settings_for, demo_and_analysis, monkeypatch):
    """Capture the actions file before it is cleaned up and inspect it."""
    demo, analysis = demo_and_analysis
    highlights = detect_highlights(analysis, DetectorOptions.defaults())[:1]
    jobs = build_jobs(highlights, analysis, settings_for)

    captured: dict[str, object] = {}
    from cs2_clip_generator.cs2.actions import JsonActionsFile

    original_write = JsonActionsFile.write

    def spy(self):  # noqa: ANN001, ANN202
        captured["actions"] = self.to_list()
        captured["path"] = str(self.path)
        return original_write(self)

    monkeypatch.setattr(JsonActionsFile, "write", spy)

    pipeline = RenderPipeline(settings_for, analysis)
    pipeline.run(jobs)

    assert captured["path"] == str(demo) + ".json"
    sequences = captured["actions"]
    assert len(sequences) == 1
    actions = sequences[0]["actions"]  # type: ignore[index]
    commands = [action["cmd"] for action in actions]
    ticks = [action["tick"] for action in actions]

    plan = pipeline.build_plan(jobs[0])
    player = analysis.player(highlights[0].player_steamid)
    assert player is not None

    # The POV is set with spec_mode before spec_player, on the right slot.
    assert f"spec_player {player.slot}" in commands
    assert commands.index("spec_mode 1") < commands.index(f"spec_player {player.slot}")
    # Recording starts at the clip's first tick and stops at its last.
    start_index = commands.index(f'startmovie "{_movie_name(actions)}"')
    assert ticks[start_index] == plan.start_tick
    assert ticks[commands.index("endmovie")] == plan.end_tick
    # The seek happens at tick 1, before anything else.
    assert any(command.startswith("demo_gototick") and tick == 1 for command, tick in zip(commands, ticks, strict=True))
    # Cheats are on (startmovie and spectator control need it) and telemetry off.
    assert "sv_cheats 1" in commands
    assert any("telemetry" in command for command in commands)
    # Ticks are ordered.
    assert ticks == sorted(ticks)


@pytest.mark.usefixtures("ffmpeg_available")
@pytest.mark.skipif(os.name == "nt", reason="the stand-in script is POSIX")
def test_two_clips_share_one_cs2_session(settings_for, demo_and_analysis):
    """A batch must not restart the game for every clip."""
    demo, analysis = demo_and_analysis
    analysis.kills.append(kill(tick=120 * SEC, victim="76561198000000008", weapon="ak47"))
    highlights = detect_highlights(analysis, DetectorOptions.defaults())
    assert len(highlights) >= 2
    jobs = build_jobs(highlights[:2], analysis, settings_for)

    pipeline = RenderPipeline(settings_for, analysis)
    launches: list[str] = []
    from cs2_clip_generator.cs2.launcher import CS2Launcher

    original_start = CS2Launcher.start

    def counting_start(self, options):  # noqa: ANN001, ANN202
        launches.append(str(options.demo_path))
        return original_start(self, options)

    CS2Launcher.start = counting_start  # type: ignore[method-assign]
    try:
        report = pipeline.run(jobs)
    finally:
        CS2Launcher.start = original_start  # type: ignore[method-assign]

    assert len(launches) == 1, "CS2 was started more than once for one batch"
    assert len(report.clips) == 2


@pytest.mark.usefixtures("ffmpeg_available")
@pytest.mark.skipif(os.name == "nt", reason="the stand-in script is POSIX")
def test_a_missing_recording_fails_with_a_clear_message(settings_for, demo_and_analysis, monkeypatch):
    """If the game writes no frames the user gets advice, not a traceback."""
    demo, analysis = demo_and_analysis
    highlights = detect_highlights(analysis, DetectorOptions.defaults())[:1]
    jobs = build_jobs(highlights, analysis, settings_for)

    # Break the stand-in: it will echo the markers but write no frames.
    executable = Path(settings_for.paths.cs2_executable)
    executable.write_text(
        executable.read_text(encoding="utf-8").replace('elif command.startswith("endmovie")', 'elif False'),
        encoding="utf-8",
    )

    report = RenderPipeline(settings_for, analysis).run(jobs)
    assert not report.clips
    assert report.failed
    job, reason = report.failed[0]
    assert "did not write any frames" in reason
    assert "Traceback" not in job.error
    assert job.state == JobState.FAILED
    assert job.error  # the actionable text is kept on the job for the UI


def _movie_name(actions: list[dict]) -> str:
    for action in actions:
        if str(action["cmd"]).startswith("startmovie"):
            return str(action["cmd"]).split(" ", 1)[1].strip().strip('"')
    raise AssertionError("no startmovie command was scheduled")


def test_ffprobe_is_available_for_these_tests():
    assert shutil.which("ffprobe"), "ffprobe is needed to verify the produced MP4"
