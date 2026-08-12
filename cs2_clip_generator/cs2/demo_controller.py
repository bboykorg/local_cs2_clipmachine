"""Console commands that drive demo playback.

Pure command builders: no side effects, no processes. Whichever transport is in
use (netcon socket, JSON actions file, cfg file) sends the strings produced
here, which makes the whole playback layer unit-testable without CS2.

Only real CS2 commands are used:

``playdemo <path>``       load a demo
``demo_gototick <tick>``  seek; the demo keeps playing from there
``demo_pause`` / ``demo_resume``
``demo_timescale <x>``    playback speed (1 = real time)
``spec_mode`` / ``spec_player``  camera (see player_controller.py)
``startmovie`` / ``endmovie``    CS2's own frame dumper
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


def quote(value: str) -> str:
    """Wrap a path in quotes for the CS2 console, dropping embedded quotes."""
    cleaned = str(value).replace('"', "")
    return f'"{cleaned}"'


def play_demo(demo_path: str) -> str:
    return f"playdemo {quote(demo_path)}"


def goto_tick(tick: int) -> str:
    return f"demo_gototick {max(0, int(tick))}"


def pause() -> str:
    return "demo_pause"


def resume() -> str:
    return "demo_resume"


def timescale(value: float) -> str:
    return f"demo_timescale {max(0.05, float(value)):g}"


def disconnect() -> str:
    return "disconnect"


def quit_game() -> str:
    return "quit"


def echo(marker: str) -> str:
    """Used as a synchronisation marker when the console can be read back."""
    return f"echo {quote(marker)}"


@dataclass
class PlaybackPresentation:
    """Everything that makes a recording look like a clip instead of a demo."""

    hide_hud: bool = False
    only_death_notices: bool = False
    player_voices: bool = False
    x_ray: bool = False
    show_assists: bool = True
    extra: Sequence[str] = field(default_factory=tuple)

    def commands(self) -> list[str]:
        commands = [
            "sv_cheats 1",
            # Telemetry overlays and the build stamp would otherwise be baked
            # into every frame of the recording.
            "cl_hud_telemetry_frametime_show 0",
            "cl_hud_telemetry_net_misdelivery_show 0",
            "cl_hud_telemetry_ping_show 0",
            "cl_hud_telemetry_serverrecvmargin_graph_show 0",
            "cl_trueview_show_status 0",
            "r_show_build_info 0",
            # Never bake the demo playback bar (the Shift+F2 / demoui panel)
            # into a recording: mode 0 disables it outright, regardless of the
            # state it was in, and unlike the key toggle it cannot turn it ON.
            "demoui",
            f"spec_show_xray {int(self.x_ray)}",
            f"mp_display_kill_assists {int(self.show_assists)}",
            f"cl_draw_only_deathnotices {int(self.only_death_notices)}",
        ]
        if self.hide_hud:
            commands += ["cl_drawhud 0", "cl_draw_only_deathnotices 1"]
        # CS2 exposes GOTV voice through tv_listen_voice_indices; -1 means "all".
        commands += (
            ["tv_listen_voice_indices -1", "tv_listen_voice_indices_h -1"]
            if self.player_voices
            else ["tv_listen_voice_indices 0", "tv_listen_voice_indices_h 0"]
        )
        commands += [c for c in self.extra if str(c).strip()]
        return commands


def start_movie(name: str, fps: int) -> list[str]:
    """CS2's built-in recorder: fixed framerate frame dump.

    ``host_framerate`` decouples the simulation from real time so every frame is
    written, at the cost of playback no longer running in real time.
    """
    return [f"host_framerate {int(fps)}", f"startmovie {quote(name)}"]


def end_movie() -> list[str]:
    return ["endmovie", "host_framerate 0"]


def hlae_record_start(output_folder: str, fps: int, record_audio: bool = True, preset: str | None = None) -> list[str]:
    """HLAE's ``mirv_streams`` recorder (Advanced Effects)."""
    commands = [
        "mirv_streams record screen enabled 1",
        f"mirv_streams record name {quote(output_folder)}",
        f"mirv_streams record fps {int(fps)}",
        f"mirv_streams record startMovieWav {int(record_audio)}",
    ]
    if preset:
        commands.append(f"mirv_streams record screen settings {preset}")
    commands.append("mirv_streams record start")
    return commands


def hlae_record_end() -> list[str]:
    return ["mirv_streams record end"]


def hlae_ffmpeg_preset(name: str, parameters: str, output_file: str) -> str:
    """Register an FFmpeg preset with HLAE.

    ``{QUOTE}`` is HLAE's own escape for a double quote inside a command.
    """
    return f'mirv_streams settings add ffmpeg {name} "{parameters} {{QUOTE}}{output_file}{{QUOTE}}"'
