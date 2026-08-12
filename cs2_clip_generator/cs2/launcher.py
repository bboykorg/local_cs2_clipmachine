"""Find Counter-Strike 2 and start it.

Finding the game is a surprisingly involved job on a real machine: Steam can be
installed anywhere, games can live in any number of library folders on any
number of drives, and the CS2 folder is still called "Counter-Strike Global
Offensive" for historical reasons. The order of attempts is:

1. an explicit path from Settings,
2. the Steam install path from the Windows registry,
3. a handful of conventional locations,
4. every library folder listed in ``libraryfolders.vdf``.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import cs2_not_found
from ..core.logger import get_logger
from ..utils.process import BackgroundProcess, kill_processes, process_names_running

log = get_logger("cs2")

CS2_APP_ID = "730"
CS2_FOLDER_NAME = "Counter-Strike Global Offensive"
CS2_PROCESS_NAMES = ("cs2.exe", "cs2")
STEAM_PROCESS_NAMES = ("steam.exe", "steam")

IS_WINDOWS = os.name == "nt"


# ---------------------------------------------------------------------------
# Minimal VDF reader
# ---------------------------------------------------------------------------

_VDF_PAIR = re.compile(r'^\s*"([^"]+)"\s+"([^"]*)"\s*$')
_VDF_KEY = re.compile(r'^\s*"([^"]+)"\s*$')


def parse_vdf(text: str) -> dict:
    """Parse Valve's KeyValues text format (enough of it for library folders)."""
    root: dict = {}
    stack: list[dict] = [root]
    pending_key: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        pair = _VDF_PAIR.match(line)
        if pair:
            stack[-1][pair.group(1)] = pair.group(2)
            continue
        if stripped == "{":
            child: dict = {}
            if pending_key is not None:
                stack[-1][pending_key] = child
                pending_key = None
            stack.append(child)
            continue
        if stripped == "}":
            if len(stack) > 1:
                stack.pop()
            continue
        key = _VDF_KEY.match(line)
        if key:
            pending_key = key.group(1)
    return root


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _registry_steam_path() -> str | None:  # pragma: no cover - Windows only
    if not IS_WINDOWS:
        return None
    try:
        import winreg  # type: ignore

        for hive, key_path, value in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, key_path) as handle:
                    path = winreg.QueryValueEx(handle, value)[0]
                if path and os.path.isdir(path):
                    return str(Path(path))
            except OSError:
                continue
    except Exception as exc:
        log.debug("registry lookup failed: %s", exc)
    return None


def candidate_steam_paths() -> list[Path]:
    candidates: list[Path] = []
    registry = _registry_steam_path()
    if registry:
        candidates.append(Path(registry))
    if IS_WINDOWS:
        for drive in ("C:", "D:", "E:", "F:", "G:"):
            candidates += [
                Path(f"{drive}/Program Files (x86)/Steam"),
                Path(f"{drive}/Program Files/Steam"),
                Path(f"{drive}/Steam"),
                Path(f"{drive}/SteamLibrary"),
            ]
    else:
        home = Path.home()
        candidates += [
            home / ".steam/steam",
            home / ".local/share/Steam",
            home / ".var/app/com.valvesoftware.Steam/data/Steam",
            Path("/usr/local/share/Steam"),
        ]
    return [c for c in candidates if c.is_dir()]


def find_steam_path(explicit: str = "") -> Path | None:
    if explicit and Path(explicit).is_dir():
        return Path(explicit)
    candidates = candidate_steam_paths()
    return candidates[0] if candidates else None


def steam_library_folders(steam_path: str | os.PathLike[str] | None) -> list[Path]:
    """Every Steam library folder, read from ``libraryfolders.vdf``."""
    if not steam_path:
        return []
    base = Path(steam_path)
    libraries: list[Path] = [base]
    for relative in ("steamapps/libraryfolders.vdf", "config/libraryfolders.vdf", "SteamApps/libraryfolders.vdf"):
        vdf_path = base / relative
        if not vdf_path.is_file():
            continue
        try:
            data = parse_vdf(vdf_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        folders = data.get("libraryfolders") or data.get("LibraryFolders") or {}
        if isinstance(folders, dict):
            for key, value in folders.items():
                path_value = value.get("path") if isinstance(value, dict) else value
                if isinstance(path_value, str) and Path(path_value).is_dir():
                    libraries.append(Path(path_value))
                del key
        break
    unique: list[Path] = []
    for library in libraries:
        resolved = library.resolve()
        if resolved not in [u.resolve() for u in unique]:
            unique.append(library)
    return unique


def cs2_executable_in(root: str | os.PathLike[str]) -> Path | None:
    """``.../Counter-Strike Global Offensive`` → the cs2 binary inside it."""
    base = Path(root)
    for relative in (
        "game/bin/win64/cs2.exe",
        "game/bin/linuxsteamrt64/cs2",
        "game/cs2.exe",
        "game/cs2.sh",
    ):
        candidate = base / relative
        if candidate.is_file():
            return candidate
    return None


def find_cs2_executable(explicit: str = "", steam_path: str = "") -> Path | None:
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    steam = find_steam_path(steam_path)
    for library in steam_library_folders(steam):
        for apps in ("steamapps", "SteamApps"):
            root = library / apps / "common" / CS2_FOLDER_NAME
            executable = cs2_executable_in(root)
            if executable:
                return executable
    found = shutil.which("cs2")
    return Path(found) if found else None


def cs2_install_root(executable: str | os.PathLike[str] | None) -> Path | None:
    """The ``Counter-Strike Global Offensive`` folder for a given binary."""
    if not executable:
        return None
    path = Path(executable).resolve()
    for parent in path.parents:
        if parent.name == CS2_FOLDER_NAME:
            return parent
        if (parent / "game" / "csgo" / "gameinfo.gi").is_file():
            return parent
    return None


def cs2_cfg_dir(executable: str | os.PathLike[str] | None) -> Path | None:
    root = cs2_install_root(executable)
    return root / "game" / "csgo" / "cfg" if root else None


def cs2_game_dir(executable: str | os.PathLike[str] | None) -> Path | None:
    root = cs2_install_root(executable)
    return root / "game" / "csgo" if root else None


# ---------------------------------------------------------------------------
# Launching
# ---------------------------------------------------------------------------


@dataclass
class LaunchOptions:
    demo_path: str | None = None
    width: int = 1920
    height: int = 1080
    display_mode: str = "windowed"  # windowed | fullscreen | borderless
    netcon_port: int | None = None
    exec_cfg: str | None = None  # cfg name without the .cfg extension
    extra_args: Sequence[str] = field(default_factory=tuple)
    #: Command that launches CS2 on our behalf (HLAE); see build_launch_args.
    launch_wrapper: Sequence[str] = field(default_factory=tuple)
    #: ``-insecure`` keeps VAC out of the picture; required for the plugin path
    #: and generally correct for offline demo playback.
    insecure: bool = True


def build_launch_args(executable: str | os.PathLike[str], options: LaunchOptions) -> list[str]:
    """Assemble the CS2 command line. Pure function — unit tested.

    With ``launch_wrapper`` set (HLAE), the game's own arguments are handed to the
    wrapper through ``-cmdLine`` instead of being executed directly, because HLAE
    starts the game itself in order to attach to it.
    """
    game_args = _game_args(options)
    if options.launch_wrapper:
        return [*[str(a) for a in options.launch_wrapper], "-cmdLine", " ".join(game_args)]
    return [str(executable), *game_args]


def _game_args(options: LaunchOptions) -> list[str]:
    args: list[str] = []
    if options.insecure:
        args.append("-insecure")
    args.append("-novid")
    if options.netcon_port:
        # Remote console over TCP. Availability depends on the CS2 build (and on
        # Windows may require the Workshop Tools), which is why callers probe it
        # instead of assuming it works.
        args += ["-netconport", str(int(options.netcon_port))]
    if options.display_mode == "fullscreen":
        args.append("-fullscreen")
    elif options.display_mode == "borderless":
        args.append("-window")
        args.append("-noborder")
    else:
        args.append("-sw")
    if options.display_mode != "fullscreen":
        args += ["-width", str(int(options.width)), "-height", str(int(options.height))]
    if options.demo_path:
        args += ["+playdemo", str(options.demo_path)]
    if options.exec_cfg:
        args += ["+exec", options.exec_cfg]
    args += [str(a) for a in options.extra_args if str(a).strip()]
    return args


def split_extra_args(text: str) -> list[str]:
    """Split a user-typed argument string without invoking a shell."""
    import shlex

    try:
        return shlex.split(text or "", posix=not IS_WINDOWS)
    except ValueError:
        return [part for part in (text or "").split() if part]


class CS2Launcher:
    """Starts, watches and stops the game process."""

    def __init__(self, executable: str | os.PathLike[str] | None) -> None:
        self.executable = str(executable) if executable else ""
        self.process: BackgroundProcess | None = None

    # -- checks ----------------------------------------------------------
    def ensure_executable(self) -> str:
        if self.executable and Path(self.executable).is_file():
            return self.executable
        found = find_cs2_executable()
        if found:
            self.executable = str(found)
            return self.executable
        raise cs2_not_found()

    @staticmethod
    def is_running() -> bool:
        return bool(process_names_running(CS2_PROCESS_NAMES))

    @staticmethod
    def is_steam_running() -> bool:
        return bool(process_names_running(STEAM_PROCESS_NAMES))

    @staticmethod
    def kill() -> bool:
        return kill_processes(CS2_PROCESS_NAMES)

    # -- lifecycle -------------------------------------------------------
    def start(self, options: LaunchOptions) -> BackgroundProcess:
        executable = self.ensure_executable()
        args = build_launch_args(executable, options)
        log.info("launching CS2: %s", " ".join(args))
        self.process = BackgroundProcess(args, log_name="cs2", cwd=str(Path(executable).parent))
        self.process.start()
        return self.process

    @property
    def running(self) -> bool:
        return bool(self.process and self.process.running) or self.is_running()

    def stop(self) -> None:
        if self.process:
            self.process.stop()
            self.process = None
        if self.is_running():
            self.kill()


def describe_installation(explicit_executable: str = "", explicit_steam: str = "") -> dict[str, str]:
    """Everything the Settings page needs to show about the CS2 installation."""
    steam = find_steam_path(explicit_steam)
    executable = find_cs2_executable(explicit_executable, str(steam) if steam else "")
    root = cs2_install_root(executable)
    return {
        "steam_path": str(steam or ""),
        "cs2_executable": str(executable or ""),
        "cs2_root": str(root or ""),
        "cfg_dir": str(cs2_cfg_dir(executable) or ""),
        "libraries": os.pathsep.join(str(p) for p in steam_library_folders(steam)),
    }


def iter_local_demos(paths: Iterable[str | os.PathLike[str]]) -> list[Path]:
    """Demos found in CS2's own replay folders — handy for the dashboard."""
    out: list[Path] = []
    for path in paths:
        base = Path(path)
        if not base.is_dir():
            continue
        out += sorted(base.glob("*.dem"), key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def default_demo_dirs(executable: str | os.PathLike[str] | None) -> list[Path]:
    root = cs2_install_root(executable)
    if not root:
        return []
    return [root / "game" / "csgo", root / "game" / "csgo" / "replays"]
