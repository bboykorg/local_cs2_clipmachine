"""FFmpeg command construction, plus a real encode to prove it works.

The command builders are pure and tested exactly; the integration tests actually
run FFmpeg on generated frames, so a broken argument order fails here rather
than at the end of a 20 minute render.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cs2_clip_generator.core.models import VideoSettings
from cs2_clip_generator.video.ffmpeg import (
    EncoderSupport,
    FFmpeg,
    build_concat_command,
    build_encode_command,
    build_image_sequence_command,
    build_screen_capture_command,
    parse_progress,
    write_concat_list,
)


def test_encode_command_has_input_before_filters_and_output_last():
    args = build_encode_command(
        "ffmpeg", "in.mp4", "out.mp4", VideoSettings(width=2560, height=1440, fps=120, bitrate_kbps=40000)
    )
    assert args[0] == "ffmpeg"
    assert args[-1] == "out.mp4"
    assert args[args.index("-i") + 1] == "in.mp4"
    assert "scale=2560:1440:flags=lanczos,fps=120" in args[args.index("-vf") + 1]
    assert "40000k" in args


def test_trim_seeks_before_the_input_and_limits_the_duration():
    args = build_encode_command(
        "ffmpeg", "in.mp4", "out.mp4", VideoSettings(), start_seconds=2.0, duration_seconds=12.5
    )
    assert args.index("-ss") < args.index("-i")
    assert args[args.index("-ss") + 1] == "2.000"
    assert args[args.index("-t") + 1] == "12.500"


def test_audio_is_dropped_when_the_user_disabled_it():
    settings = VideoSettings(game_audio=False, voice_audio=False)
    assert "-an" in build_encode_command("ffmpeg", "in.mp4", "out.mp4", settings)
    settings = VideoSettings(game_audio=True)
    args = build_encode_command("ffmpeg", "in.mp4", "out.mp4", settings)
    assert "-an" not in args and "aac" in args


def test_volume_filter_only_appears_when_it_is_not_unity():
    assert "-af" not in build_encode_command("ffmpeg", "in.mp4", "out.mp4", VideoSettings(volume=1.0))
    args = build_encode_command("ffmpeg", "in.mp4", "out.mp4", VideoSettings(volume=0.5))
    assert args[args.index("-af") + 1] == "volume=0.50"


def test_each_encoder_family_gets_its_own_rate_control_flags():
    settings = VideoSettings(bitrate_kbps=20000)
    nvenc = build_encode_command("ffmpeg", "i", "o", settings, encoder="h264_nvenc")
    amf = build_encode_command("ffmpeg", "i", "o", settings, encoder="h264_amf")
    qsv = build_encode_command("ffmpeg", "i", "o", settings, encoder="h264_qsv")
    cpu = build_encode_command("ffmpeg", "i", "o", settings, encoder="libx264")
    assert "-rc" in nvenc and nvenc[nvenc.index("-rc") + 1] == "vbr"
    assert "vbr_peak" in amf
    assert "-preset" in qsv
    assert "libx264" in cpu


def test_image_sequence_command_sets_the_input_framerate_and_start_number():
    args = build_image_sequence_command(
        "ffmpeg", "/tmp/clip%04d.tga", "out.mp4", VideoSettings(fps=60), audio_path="/tmp/a.wav", start_number=7
    )
    assert args[args.index("-framerate") + 1] == "60"
    assert args[args.index("-start_number") + 1] == "7"
    assert args.count("-i") == 2
    assert "-shortest" in args


def test_screen_capture_targets_the_cs2_window():
    args = build_screen_capture_command(
        "ffmpeg", "out.mp4", VideoSettings(fps=60), window_title="Counter-Strike 2", duration_seconds=10
    )
    assert "gdigrab" in args
    assert args[args.index("-i") + 1] == "title=Counter-Strike 2"
    assert args[args.index("-t") + 1] == "10.00"


def test_concat_list_quotes_and_normalises_paths(tmp_path):
    first = tmp_path / "a b.mp4"
    first.write_bytes(b"x")
    listing = write_concat_list([str(first)], tmp_path / "list.txt")
    content = listing.read_text()
    assert content.startswith("file '")
    assert "a b.mp4" in content


def test_concat_command_can_copy_or_reencode(tmp_path):
    copy = build_concat_command("ffmpeg", "list.txt", "out.mp4", VideoSettings(), reencode=False)
    assert copy[copy.index("-c") + 1] == "copy"
    encode = build_concat_command("ffmpeg", "list.txt", "out.mp4", VideoSettings(), reencode=True)
    assert "-c:v" in encode


def test_progress_parsing():
    assert parse_progress("frame= 120 fps=60 time=00:00:05.00 bitrate=...", total_seconds=10.0) == 0.5
    assert parse_progress("no time here", 10.0) is None
    assert parse_progress("time=00:00:20.00", 10.0) == 1.0  # clamped


def test_encoder_selection_prefers_hardware_but_falls_back_honestly():
    support = EncoderSupport({"libx264", "libx265", "h264_nvenc"})
    assert support.encoder_for("h264", "auto") == "h264_nvenc"
    assert support.encoder_for("h265", "auto") == "libx265"  # no hevc_nvenc available
    assert support.encoder_for("h264", "amf") == "h264_nvenc"  # asked for AMF, not present
    assert support.encoder_for("h264", "cpu") == "libx264"
    assert "nvenc" in support.families()
    assert "amf" not in support.families()


# ---------------------------------------------------------------------------
# Integration: really run FFmpeg
# ---------------------------------------------------------------------------


def _make_test_clip(path: Path, seconds: int = 2, colour: str = "red") -> Path:
    """Synthesise a small clip with a tone, standing in for a CS2 recording."""
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={colour}:s=640x360:r=30:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.mark.usefixtures("ffmpeg_available")
def test_real_encode_produces_a_playable_trimmed_clip(tmp_path):
    source = _make_test_clip(tmp_path / "source.mp4", seconds=4)
    ffmpeg = FFmpeg()
    output = tmp_path / "clip.mp4"
    ffmpeg.encode(
        input_path=str(source),
        output_path=str(output),
        settings=VideoSettings(width=320, height=180, fps=30, bitrate_kbps=1000, encoder="cpu"),
        start_seconds=1.0,
        duration_seconds=2.0,
    )
    assert output.is_file() and output.stat().st_size > 1000
    duration = ffmpeg.probe_duration(str(output))
    assert duration is not None and 1.5 < duration < 2.6


@pytest.mark.usefixtures("ffmpeg_available")
def test_real_concat_joins_two_clips_into_one(tmp_path):
    first = _make_test_clip(tmp_path / "a.mp4", seconds=2, colour="red")
    second = _make_test_clip(tmp_path / "b.mp4", seconds=2, colour="blue")
    ffmpeg = FFmpeg()
    output = tmp_path / "joined.mp4"
    ffmpeg.concat(
        [str(first), str(second)],
        str(output),
        VideoSettings(width=320, height=180, fps=30, bitrate_kbps=1000, encoder="cpu"),
        work_dir=tmp_path,
    )
    duration = ffmpeg.probe_duration(str(output))
    assert duration is not None and 3.5 < duration < 4.6


@pytest.mark.usefixtures("ffmpeg_available")
def test_real_image_sequence_encode(tmp_path):
    """Exactly what the CS2 startmovie and HLAE recorders hand to FFmpeg."""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=1",
            str(frames_dir / "clip%05d.tga"),
        ],
        check=True,
        capture_output=True,
    )
    frames = sorted(frames_dir.glob("*.tga"))
    assert frames, "no frames were generated"

    from cs2_clip_generator.recording.native import _frame_pattern

    pattern, start_number = _frame_pattern(frames)
    assert pattern.endswith("clip%05d.tga")

    output = tmp_path / "from_frames.mp4"
    FFmpeg().encode_image_sequence(
        pattern=pattern,
        output_path=str(output),
        settings=VideoSettings(width=320, height=180, fps=30, bitrate_kbps=1000, encoder="cpu"),
        frame_count=len(frames),
        start_number=start_number,
    )
    assert output.is_file() and output.stat().st_size > 1000


@pytest.mark.usefixtures("ffmpeg_available")
def test_encoder_detection_finds_at_least_the_cpu_encoder():
    support = FFmpeg().encoders()
    assert "libx264" in support.available
