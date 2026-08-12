"""Driving a real CS2 process through a real demo.

This is the part of the application that cannot be faked, and the part where CS2
gives the least help. Three transports exist, in descending order of accuracy;
all three implement :class:`PlaybackController`, so the render pipeline does not
care which one is in use.

============  =======================  ========================================
Backend       How commands are timed   Requirements
============  =======================  ========================================
``plugin``    exact demo ticks         CS2 server plugin installed + enabled
``netcon``    live socket, real feed   ``-netconport`` works on this CS2 build
``cfg``       wall clock + hotkey      nothing (least accurate, Windows only)
============  =======================  ========================================

Every backend performs the same choreography for one clip::

    load demo → seek to tick → force POV → let playback settle
              → start recording → play the interval → stop recording

and every backend reports honestly whether it is available, so the UI can tell
the user what to install instead of silently producing a black video.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import Cancelled, CS2Error
from ..core.logger import get_logger
from ..utils.filesystem import sanitize_filename
from . import actions as actions_module
from . import console_log, demo_controller, netcon, plugin
from .camera_controller import CameraController, PlayerPovCameraController
from .launcher import CS2Launcher, LaunchOptions, cs2_cfg_dir, cs2_game_dir, split_extra_args
from .player_controller import CameraMode, SpectatorTarget

log = get_logger("cs2")

CFG_NAME = "cs2clip"
CLIP_CFG_NAME = "cs2clip_clip"

# ``+exec`` and the netcon port become available well before a large demo has
# finished loading.  Sending demo_gototick immediately at that point is silently
# ignored by CS2, after which the recorder captures the start of the match.
DEMO_STARTUP_GRACE_SECONDS = 12.0
MIN_SEEK_SETTLE_SECONDS = 4.0


@dataclass
class RecorderHooks:
    """How a recorder wants to be driven.

    An *external* recorder (OBS, FFmpeg screen capture) is started and stopped by
    Python callbacks. An *in-game* recorder (HLAE ``mirv_streams``, CS2's own
    ``startmovie``) is driven by console commands that must run at the right
    tick. A recorder may use either or both.
    """

    on_start: Callable[[], None] | None = None
    on_stop: Callable[[], None] | None = None
    start_commands: Sequence[str] = field(default_factory=tuple)
    stop_commands: Sequence[str] = field(default_factory=tuple)
    #: In-game recorders that dump frames run the demo slower than real time.
    real_time_playback: bool = True

    @property
    def is_external(self) -> bool:
        return self.on_start is not None or self.on_stop is not None


@dataclass
class ClipPlan:
    """One clip: which slice of which demo, seen through whose eyes."""

    demo_path: str
    start_tick: int
    end_tick: int
    tickrate: float = 64.0
    target: SpectatorTarget | None = None
    camera_mode: CameraMode = CameraMode.PLAYER_POV
    presentation: demo_controller.PlaybackPresentation = field(
        default_factory=demo_controller.PlaybackPresentation
    )
    label: str = "clip"

    @property
    def duration_seconds(self) -> float:
        return max(0.5, (self.end_tick - self.start_tick) / (self.tickrate or 64.0))


@dataclass
class SessionOptions:
    width: int = 1920
    height: int = 1080
    display_mode: str = "windowed"
    demo_load_timeout: float = 90.0
    stabilisation_seconds: float = 2.0
    close_game_after: bool = True
    extra_args: Sequence[str] = field(default_factory=tuple)
    #: Command that starts CS2 for us (HLAE); see launcher.build_launch_args.
    launch_wrapper: Sequence[str] = field(default_factory=tuple)


class PlaybackController(ABC):
    """Loads a demo in CS2 and plays exact intervals from exact viewpoints."""

    name = "abstract"
    #: Can this backend run a console command at a precise demo tick?
    supports_tick_scheduling = False

    def __init__(self, cs2_executable: str, options: SessionOptions | None = None) -> None:
        self.cs2_executable = cs2_executable
        self.options = options or SessionOptions()
        self.launcher = CS2Launcher(cs2_executable)
        self.watcher: console_log.ConsoleLogWatcher | None = None
        self._cancel: Callable[[], bool] | None = None
        self._session_demo: str | None = None

    # -- capability ------------------------------------------------------
    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """``(usable, human explanation)`` — checked before any launch."""

    # -- session ---------------------------------------------------------
    def set_cancel_hook(self, cancel: Callable[[], bool] | None) -> None:
        self._cancel = cancel

    def _check_cancel(self) -> None:
        if self._cancel is not None and self._cancel():
            raise Cancelled()

    def _console_watcher(self) -> console_log.ConsoleLogWatcher | None:
        path = console_log.console_log_path(cs2_game_dir(self.cs2_executable))
        return console_log.ConsoleLogWatcher(path) if path else None

    @abstractmethod
    def begin_session(self, demo_path: str, clips: Sequence[tuple[ClipPlan, RecorderHooks]]) -> None:
        """Start CS2 with the demo loaded and every clip scheduled.

        The whole batch is passed up front because a tick-scheduling backend
        writes all of it into one actions file and plays the clips back to back
        in a single CS2 session.
        """

    @abstractmethod
    def run_clip(self, clip: ClipPlan, hooks: RecorderHooks) -> None:
        """Play one clip, driving the recorder around it."""

    def end_session(self) -> None:
        if self.options.close_game_after:
            self.launcher.stop()
        self._session_demo = None

    # -- shared helpers --------------------------------------------------
    def _launch(self, demo_path: str, netcon_port: int | None = None, exec_cfg: str | None = None) -> None:
        if CS2Launcher.is_running():
            log.info("another CS2 instance is running; closing it first")
            CS2Launcher.kill()
            time.sleep(2.0)
        options = LaunchOptions(
            demo_path=demo_path,
            width=self.options.width,
            height=self.options.height,
            display_mode=self.options.display_mode,
            netcon_port=netcon_port,
            exec_cfg=exec_cfg,
            # -condebug mirrors the console into console.log, which is how the
            # pipeline learns that playback reached a tick.
            extra_args=["-condebug", *self.options.extra_args],
            launch_wrapper=self.options.launch_wrapper,
        )
        self.watcher = self._console_watcher()
        if self.watcher:
            self.watcher.reset()
        self.launcher.start(options)
        self._session_demo = demo_path

    def _write_cfg(self, name: str, commands: Sequence[str]) -> Path | None:
        directory = cs2_cfg_dir(self.cs2_executable)
        if directory is None:
            return None
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{sanitize_filename(name, fallback=CFG_NAME)}.cfg"
        target.write_text("\n".join([*commands, ""]), encoding="utf-8")
        log.debug("wrote cfg %s (%d commands)", target, len(commands))
        return target

    def _wait_for_process(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._check_cancel()
            if self.launcher.running:
                return
            time.sleep(0.5)
        raise CS2Error(
            title="Counter-Strike 2 did not start.",
            reasons=["Steam is not running", "The CS2 path is wrong", "Another CS2 instance is stuck"],
            actions=["Start Steam", "Open Settings", "Retry"],
        )


# ---------------------------------------------------------------------------
# 1. Plugin backend — tick-accurate
# ---------------------------------------------------------------------------


class PluginPlaybackController(PlaybackController):
    """Schedule commands at exact demo ticks through the CS2 server plugin.

    All of a session's clips are written into one actions file, so a batch of
    highlights costs a single CS2 start-up. Recording is driven either by
    in-game commands (scheduled at the exact tick) or by external hooks
    synchronised on ``echo`` markers that the plugin fires at those same ticks.
    """

    name = "plugin"
    supports_tick_scheduling = True

    def __init__(self, cs2_executable: str, options: SessionOptions | None = None) -> None:
        super().__init__(cs2_executable, options)
        self._actions: actions_module.JsonActionsFile | None = None
        self._clip_index = 0

    def available(self) -> tuple[bool, str]:
        status = plugin.plugin_status(self.cs2_executable)
        if status.usable:
            return True, "CS2 server plugin detected"
        if status.installed and not status.gameinfo_patched:
            return False, "Plugin binary found but gameinfo.gi is not patched (enable it in Settings)"
        return False, "CS2 server plugin is not installed"

    def begin_session(self, demo_path: str, clips: Sequence[tuple[ClipPlan, RecorderHooks]]) -> None:
        actions = actions_module.JsonActionsFile(demo_path)
        for index, (clip, hooks) in enumerate(clips):
            start_marker = console_log.marker(f"start_{index}")
            end_marker = console_log.marker(f"end_{index}")
            setup = [
                *clip.presentation.commands(),
                demo_controller.timescale(1.0),
            ]
            camera_controller: CameraController = PlayerPovCameraController(clip.camera_mode)
            camera_actions = [
                (keyframe.tick, command)
                for keyframe in camera_controller.plan(_as_highlight_stub(clip), clip.target, clip.tickrate)
                for command in keyframe.commands
            ]
            actions_module.build_clip_sequence(
                actions,
                start_tick=clip.start_tick,
                end_tick=clip.end_tick,
                setup_commands=setup,
                camera_actions=camera_actions,
                record_start_commands=[demo_controller.echo(start_marker), *hooks.start_commands],
                record_end_commands=[*hooks.stop_commands, demo_controller.echo(end_marker)],
                tickrate=clip.tickrate,
                finish_command=None,
            )
            if index < len(clips) - 1:
                actions.add_next_sequence(clip.end_tick + int(round(clip.tickrate)))
            elif self.options.close_game_after:
                actions.add_quit(clip.end_tick + int(round(clip.tickrate * 2)))
            actions.end_sequence()

        actions.write()
        self._actions = actions
        self._clip_index = 0
        self._launch(demo_path)
        self._wait_for_process(30.0)

    def run_clip(self, clip: ClipPlan, hooks: RecorderHooks) -> None:
        index = self._clip_index
        self._clip_index += 1
        start_marker = console_log.marker(f"start_{index}")
        end_marker = console_log.marker(f"end_{index}")

        if self.watcher is None:
            # Without console feedback the only honest option is wall-clock
            # timing based on the demo load timeout.
            log.warning("console.log is unavailable; falling back to timed waiting")
            time.sleep(min(self.options.demo_load_timeout, 20.0))
            self._drive_external(hooks, clip.duration_seconds)
            return

        log.info("waiting for clip %d to start (marker %s)", index + 1, start_marker)
        if not self.watcher.wait_for(start_marker, timeout=self.options.demo_load_timeout, cancel=self._cancel):
            self._check_cancel()
            raise CS2Error(
                title="CS2 never reached the highlight.",
                reasons=[
                    "The demo failed to load",
                    "The CS2 server plugin did not run the scheduled commands",
                    "The demo is shorter than the requested tick",
                ],
                actions=["Open Settings", "Retry"],
            )
        if hooks.on_start:
            hooks.on_start()
        timeout = clip.duration_seconds * (3.0 if not hooks.real_time_playback else 1.0) + 30.0
        reached_end = self.watcher.wait_for(end_marker, timeout=timeout, cancel=self._cancel)
        if hooks.on_stop:
            hooks.on_stop()
        if not reached_end:
            self._check_cancel()
            log.warning("clip %d end marker never appeared; the recording may be short", index + 1)

    def _drive_external(self, hooks: RecorderHooks, duration: float) -> None:
        if hooks.on_start:
            hooks.on_start()
        time.sleep(duration)
        if hooks.on_stop:
            hooks.on_stop()

    def end_session(self) -> None:
        super().end_session()
        if self._actions is not None:
            self._actions.delete()
            self._actions = None


# ---------------------------------------------------------------------------
# 2. Netcon backend — live console
# ---------------------------------------------------------------------------


class NetconPlaybackController(PlaybackController):
    """Drive CS2 over its TCP console.

    Accuracy comes from a different trick than the plugin's: seek, *pause*,
    configure the camera, start the recorder, then resume. Because the demo is
    paused while everything is set up, the recording starts on the intended tick
    even though commands are sent from outside the engine.
    """

    name = "netcon"
    supports_tick_scheduling = False

    def __init__(self, cs2_executable: str, options: SessionOptions | None = None, port: int | None = None) -> None:
        super().__init__(cs2_executable, options)
        self.port = port or netcon.find_free_port()
        self.console = netcon.NetConsole(port=self.port)
        self._first_clip = True

    def available(self) -> tuple[bool, str]:
        if not self.cs2_executable:
            return False, "CS2 executable not configured"
        # Whether -netconport opens a port depends on the CS2 build, so the only
        # truthful answer before launching is "maybe": the probe happens in
        # begin_session and the pipeline falls back if it fails.
        return True, "Remote console (-netconport); verified when CS2 starts"

    def begin_session(self, demo_path: str, clips: Sequence[tuple[ClipPlan, RecorderHooks]]) -> None:
        del clips
        actions_module.delete_actions_file(demo_path)
        self._launch(demo_path, netcon_port=self.port)
        self._wait_for_process(30.0)
        if not self.console.wait_for_port(timeout=self.options.demo_load_timeout, poll_interval=1.0):
            raise CS2Error(
                title="CS2's remote console never opened.",
                reasons=[
                    "This CS2 build only enables -netconport with the Workshop Tools installed",
                    "A firewall blocked the local connection",
                ],
                actions=["Install the CS2 server plugin for tick-accurate control", "Retry"],
            )
        if not self.console.ping(timeout=10.0):
            log.warning("remote console accepted the connection but did not echo back")
        # The socket opens during game startup, not when demo playback is ready.
        # Give the map and demo container time to finish loading before the first
        # seek.  The first seek is also retried below for slower machines.
        log.info("waiting %.1fs for demo playback to initialise", DEMO_STARTUP_GRACE_SECONDS)
        time.sleep(DEMO_STARTUP_GRACE_SECONDS)

    def run_clip(self, clip: ClipPlan, hooks: RecorderHooks) -> None:
        self._check_cancel()
        console = self.console
        console.clear_buffer()

        # Presentation first: these are convars, they survive the seek.
        console.send_all(clip.presentation.commands())
        console.send(demo_controller.timescale(1.0))

        # Pause before seeking so playback cannot run past the highlight while
        # CS2 is decoding the target tick.  Repeat the first seek once: on a
        # slow machine the first command can still arrive while the demo is
        # becoming active, and CS2 otherwise drops it without reporting an
        # error.
        seek_wait = max(MIN_SEEK_SETTLE_SECONDS, self.options.stabilisation_seconds)
        attempts = 2 if self._first_clip else 1
        for _attempt in range(attempts):
            console.send(demo_controller.pause())
            time.sleep(0.25)
            console.send(demo_controller.goto_tick(clip.start_tick))
            time.sleep(seek_wait)
        console.send(demo_controller.pause())
        self._first_clip = False
        time.sleep(0.4)

        camera = PlayerPovCameraController(clip.camera_mode)
        for keyframe in camera.plan(_as_highlight_stub(clip), clip.target, clip.tickrate):
            if keyframe.tick <= clip.start_tick + int(clip.tickrate):
                console.send_all(keyframe.commands)
        time.sleep(0.4)

        if hooks.start_commands:
            console.send_all(hooks.start_commands)
        if hooks.on_start:
            hooks.on_start()

        console.send(demo_controller.resume())
        duration = clip.duration_seconds
        deadline = time.monotonic() + duration / (1.0 if hooks.real_time_playback else 0.35)
        while time.monotonic() < deadline:
            if self._cancel is not None and self._cancel():
                break
            time.sleep(0.1)

        if hooks.stop_commands:
            console.send_all(hooks.stop_commands)
        if hooks.on_stop:
            hooks.on_stop()
        console.send(demo_controller.pause())
        self._check_cancel()

    def end_session(self) -> None:
        try:
            if self.console.connected and self.options.close_game_after:
                self.console.send(demo_controller.quit_game())
                time.sleep(1.0)
        finally:
            self.console.close()
        super().end_session()


# ---------------------------------------------------------------------------
# 3. Cfg backend — last resort
# ---------------------------------------------------------------------------


class CfgPlaybackController(PlaybackController):
    """No plugin, no console: a cfg file plus a synthetic key press.

    CS2 executes ``+exec`` before the demo is ready, so the seek cannot happen at
    launch. What *does* work without any third-party component is binding the
    seek to a key and pressing that key for the user once the demo is loaded.
    The key press is a real Win32 ``SendInput`` call, which means the CS2 window
    must be in the foreground — hence "last resort".
    """

    name = "cfg"
    supports_tick_scheduling = False
    HOTKEY = "F9"

    def __init__(self, cs2_executable: str, options: SessionOptions | None = None) -> None:
        super().__init__(cs2_executable, options)
        self._marker_sequence = 0
        self._first_clip = True

    def available(self) -> tuple[bool, str]:
        if os.name != "nt":
            return False, "Synthetic key presses are only implemented on Windows"
        if cs2_cfg_dir(self.cs2_executable) is None:
            return False, "CS2 cfg folder not found"
        return True, "Fallback: cfg + simulated key press (CS2 window must be focused)"

    def begin_session(self, demo_path: str, clips: Sequence[tuple[ClipPlan, RecorderHooks]]) -> None:
        del clips
        actions_module.delete_actions_file(demo_path)
        self._write_cfg(
            CFG_NAME,
            [
                "sv_cheats 1",
                f'bind {self.HOTKEY} "exec {CLIP_CFG_NAME}"',
                f"echo {console_log.marker('ready')}",
            ],
        )
        self._launch(demo_path, exec_cfg=CFG_NAME)
        self._wait_for_process(30.0)
        ready = bool(self.watcher and self.watcher.wait_for(
            console_log.marker("ready"), timeout=self.options.demo_load_timeout, cancel=self._cancel
        ))
        if self.watcher and not ready:
            log.warning("CS2 never confirmed the setup cfg; continuing on a timer")
        # The setup cfg is executed during startup, before +playdemo has
        # necessarily finished.  Its echo only confirms the key binding, not
        # that demo_gototick is ready to accept commands.
        log.info("waiting %.1fs for demo playback to initialise", DEMO_STARTUP_GRACE_SECONDS)
        time.sleep(DEMO_STARTUP_GRACE_SECONDS)

    def _execute_clip_cfg(self, commands: Sequence[str], stage: str, timeout: float = 20.0) -> None:
        """Execute commands in CS2 and require an echo acknowledgement."""
        self._marker_sequence += 1
        marker = f"{console_log.marker(stage)}_{self._marker_sequence}_{time.monotonic_ns()}"
        self._write_cfg(CLIP_CFG_NAME, [*commands, f"echo {marker}"])

        if self.watcher:
            self.watcher.poll()
        if not _focus_cs2_window():
            log.warning("could not confirm that the CS2 window is in the foreground")
        if not _press_key(self.HOTKEY):
            raise CS2Error(
                title="The clip command could not be sent to CS2.",
                reasons=["Windows rejected the simulated F9 key press"],
                actions=["Keep CS2 open while rendering", "Try the netcon or plugin playback backend"],
            )
        if self.watcher and not self.watcher.wait_for(marker, timeout=timeout, cancel=self._cancel):
            raise CS2Error(
                title="CS2 did not execute the clip command.",
                reasons=[
                    "The demo was still loading",
                    "The F9 binding was not active inside CS2",
                    "The CS2 window could not receive keyboard input",
                ],
                actions=["Retry the render", "Try the netcon or plugin playback backend", "Open the CS2 log"],
            )

    def run_clip(self, clip: ClipPlan, hooks: RecorderHooks) -> None:
        self._check_cancel()
        camera = PlayerPovCameraController(clip.camera_mode)
        keyframes = camera.plan(_as_highlight_stub(clip), clip.target, clip.tickrate)

        # Stage 1: pause and seek.  The old implementation put seek, camera and
        # echo in one cfg and treated the immediate echo as proof that seeking
        # had completed.  It then started recording while CS2 was still at the
        # beginning of the demo.
        seek_wait = max(MIN_SEEK_SETTLE_SECONDS, self.options.stabilisation_seconds)
        attempts = 2 if self._first_clip else 1
        for _attempt in range(attempts):
            self._execute_clip_cfg(
                [*clip.presentation.commands(), demo_controller.pause(), demo_controller.goto_tick(clip.start_tick)],
                "seek",
            )
            time.sleep(seek_wait)
        self._first_clip = False

        # Stage 2: while paused, set the requested player POV and confirm that
        # CS2 consumed the cfg before any recorder is started.
        self._execute_clip_cfg(
            [demo_controller.pause(), *[command for keyframe in keyframes[:1] for command in keyframe.commands]],
            "camera",
        )
        time.sleep(max(0.5, self.options.stabilisation_seconds))

        if hooks.start_commands:
            log.warning("in-game recording commands cannot be scheduled by the cfg backend")
        if hooks.on_start:
            hooks.on_start()

        # Stage 3: recording is live, so resume only now.
        self._execute_clip_cfg([demo_controller.resume()], "resume", timeout=5.0)
        deadline = time.monotonic() + clip.duration_seconds
        while time.monotonic() < deadline:
            if self._cancel is not None and self._cancel():
                break
            time.sleep(0.1)
        if hooks.on_stop:
            hooks.on_stop()
        # Leave playback paused between queued clips.
        self._execute_clip_cfg([demo_controller.pause()], "pause", timeout=5.0)


def _focus_cs2_window() -> bool:  # pragma: no cover - Windows UI integration
    """Bring CS2's top-level window forward before using SendInput."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        try:
            import psutil

            cs2_pids = {
                int(process.info["pid"])
                for process in psutil.process_iter(["pid", "name"])
                if str(process.info.get("name") or "").lower() == "cs2.exe"
            }
        except Exception:
            cs2_pids = set()

        user32 = ctypes.windll.user32
        matches: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd, _lparam):  # noqa: ANN001, ANN202 - Win32 callback
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            length = user32.GetWindowTextLengthW(hwnd)
            title_buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title_buffer, length + 1)
            title = title_buffer.value.lower()
            if int(pid.value) in cs2_pids or "counter-strike 2" in title:
                matches.append(int(hwnd))
                return False
            return True

        callback = callback_type(visit)
        user32.EnumWindows(callback, 0)
        if not matches:
            return False
        hwnd = matches[0]
        SW_RESTORE = 9  # noqa: N806 - Win32 constant
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        return int(user32.GetForegroundWindow()) == hwnd
    except Exception as exc:
        log.debug("could not focus CS2 window: %s", exc)
        return False


def _press_key(key: str) -> bool:  # pragma: no cover - Windows input
    """Press a function key with Win32 ``SendInput``."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        virtual_keys = {f"F{i}": 0x70 + (i - 1) for i in range(1, 13)}
        code = virtual_keys.get(key.upper())
        if code is None:
            return False

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
            ]

        class INPUT(ctypes.Structure):
            class _Union(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]

            _anonymous_ = ("union",)
            _fields_ = [("type", wintypes.DWORD), ("union", _Union)]

        KEYEVENTF_KEYUP = 0x0002  # noqa: N806 - Win32 constant
        down = INPUT(type=1)
        down.ki = KEYBDINPUT(wVk=code, wScan=0, dwFlags=0, time=0, dwExtraInfo=None)
        up = INPUT(type=1)
        up.ki = KEYBDINPUT(wVk=code, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)
        sent = ctypes.windll.user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
        time.sleep(0.05)
        sent += ctypes.windll.user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
        return sent == 2
    except Exception as exc:
        log.debug("SendInput failed: %s", exc)
        return False


def _as_highlight_stub(clip: ClipPlan):  # noqa: ANN202 - tiny adapter
    """Adapt a :class:`ClipPlan` to what :class:`CameraController` expects."""
    from ..core.models import Highlight, HighlightKind

    return Highlight(
        id="plan",
        kind=HighlightKind.KILL,
        player_steamid=clip.target.steamid if clip.target else "",
        player_name=clip.target.name if clip.target else "",
        round_number=0,
        start_tick=clip.start_tick,
        end_tick=clip.end_tick,
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

CONTROLLERS: dict[str, type[PlaybackController]] = {
    PluginPlaybackController.name: PluginPlaybackController,
    NetconPlaybackController.name: NetconPlaybackController,
    CfgPlaybackController.name: CfgPlaybackController,
}

#: Preference order when the user asks for "auto".
AUTO_ORDER = (PluginPlaybackController.name, NetconPlaybackController.name, CfgPlaybackController.name)


def describe_backends(cs2_executable: str, options: SessionOptions | None = None) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []
    for name in AUTO_ORDER:
        controller = CONTROLLERS[name](cs2_executable, options)
        usable, detail = controller.available()
        out.append((name, usable, detail))
    return out


def get_playback_controller(
    cs2_executable: str,
    preferred: str = "auto",
    options: SessionOptions | None = None,
    extra_args: str = "",
) -> PlaybackController:
    """Pick a playback backend, honouring the user's choice when possible."""
    options = options or SessionOptions()
    if extra_args:
        options.extra_args = [*options.extra_args, *split_extra_args(extra_args)]

    if preferred and preferred != "auto":
        controller_cls = CONTROLLERS.get(preferred)
        if controller_cls is None:
            raise CS2Error(title=f"Unknown playback backend '{preferred}'.")
        return controller_cls(cs2_executable, options)

    for name in AUTO_ORDER:
        controller = CONTROLLERS[name](cs2_executable, options)
        usable, detail = controller.available()
        if usable:
            log.info("playback backend: %s (%s)", name, detail)
            return controller
    raise CS2Error(
        title="CS2 cannot be controlled automatically on this machine.",
        reasons=[
            "The CS2 server plugin is not installed",
            "This CS2 build does not expose a remote console",
            "The cfg fallback needs Windows",
        ],
        actions=["Open Settings to configure the plugin", "See the README section 'CS2 playback'"],
    )
