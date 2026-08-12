"""Detect (and, with consent, enable) the CS2 server plugin.

Tick-accurate playback control needs a plugin loaded into the game, because
Source 2 has no VDM files and no other native hook. The plugin used here is the
one shipped by CS Demo Manager: a ``server.dll`` placed in a search path that
``gameinfo.gi`` points at, which reads ``<demo>.dem.json``.

This application deliberately does **not** download it. Fetching and loading a
native library into someone's game without asking is not acceptable, and the
requirements say so too. What it does instead:

* look for an installation that already exists (CS Demo Manager users have one),
* let the user point at a plugin binary they already trust,
* patch ``gameinfo.gi`` only after explicit consent, always with a backup,
* offer a clean uninstall that restores the backup.

Without the plugin the app still works: the playback layer falls back to the
netcon console or to a plain cfg with wall-clock timing.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..core.logger import get_logger
from .launcher import cs2_game_dir, cs2_install_root

log = get_logger("cs2")

#: Search-path folder name used by CS Demo Manager's plugin.
CSDM_FOLDER = "csdm"
GAMEINFO_MARKER = "Game\tcsgo/csdm"
GAMEINFO_ANCHOR = "Game\tcsgo"


@dataclass
class PluginStatus:
    installed: bool
    gameinfo_patched: bool
    binary_path: str = ""
    gameinfo_path: str = ""
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.installed and self.gameinfo_patched


def _binary_name() -> str:
    return "server.dll" if os.name == "nt" else "libserver.so"


def _binary_dir(root: Path) -> Path:
    sub = "win64" if os.name == "nt" else "linuxsteamrt64"
    return root / "game" / "csgo" / CSDM_FOLDER / "bin" / sub


def plugin_status(cs2_executable: str | os.PathLike[str] | None) -> PluginStatus:
    """Is a compatible plugin present and enabled?"""
    root = cs2_install_root(cs2_executable)
    if root is None:
        return PluginStatus(False, False, detail="CS2 installation not found")

    binary = _binary_dir(root) / _binary_name()
    gameinfo = root / "game" / "csgo" / "gameinfo.gi"
    patched = False
    if gameinfo.is_file():
        try:
            patched = GAMEINFO_MARKER in gameinfo.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.debug("cannot read gameinfo.gi: %s", exc)

    return PluginStatus(
        installed=binary.is_file(),
        gameinfo_patched=patched,
        binary_path=str(binary),
        gameinfo_path=str(gameinfo),
        detail="" if binary.is_file() else "plugin binary not present",
    )


def patch_gameinfo(cs2_executable: str | os.PathLike[str] | None) -> bool:
    """Add the plugin's search path to ``gameinfo.gi`` (backing the file up)."""
    root = cs2_install_root(cs2_executable)
    if root is None:
        return False
    gameinfo = root / "game" / "csgo" / "gameinfo.gi"
    if not gameinfo.is_file():
        return False
    try:
        content = gameinfo.read_text(encoding="utf-8", errors="replace")
        if GAMEINFO_MARKER in content:
            return True
        if GAMEINFO_ANCHOR not in content:
            log.warning("gameinfo.gi has an unexpected layout; refusing to patch it")
            return False
        backup = gameinfo.with_suffix(gameinfo.suffix + ".cs2clip-backup")
        if not backup.exists():
            shutil.copy2(gameinfo, backup)
        patched = content.replace(GAMEINFO_ANCHOR, f"{GAMEINFO_MARKER}\n\t\t\t{GAMEINFO_ANCHOR}", 1)
        gameinfo.write_text(patched, encoding="utf-8")
        log.info("patched gameinfo.gi to load the CS2 plugin (backup at %s)", backup.name)
        return True
    except OSError as exc:
        log.error("failed to patch gameinfo.gi: %s", exc)
        return False


def restore_gameinfo(cs2_executable: str | os.PathLike[str] | None) -> bool:
    """Undo :func:`patch_gameinfo` from the backup."""
    root = cs2_install_root(cs2_executable)
    if root is None:
        return False
    gameinfo = root / "game" / "csgo" / "gameinfo.gi"
    backup = gameinfo.with_suffix(gameinfo.suffix + ".cs2clip-backup")
    try:
        if backup.is_file():
            shutil.copy2(backup, gameinfo)
            backup.unlink(missing_ok=True)
            log.info("restored the original gameinfo.gi")
            return True
        # No backup: strip our line if it is there.
        if gameinfo.is_file():
            content = gameinfo.read_text(encoding="utf-8", errors="replace")
            if GAMEINFO_MARKER in content:
                lines = [line for line in content.splitlines(keepends=True) if GAMEINFO_MARKER not in line]
                gameinfo.write_text("".join(lines), encoding="utf-8")
                return True
    except OSError as exc:
        log.error("failed to restore gameinfo.gi: %s", exc)
    return False


def install_plugin_binary(cs2_executable: str | os.PathLike[str] | None, source_binary: str | os.PathLike[str]) -> bool:
    """Copy a plugin binary the user already has into place."""
    root = cs2_install_root(cs2_executable)
    source = Path(source_binary)
    if root is None or not source.is_file():
        return False
    target_dir = _binary_dir(root)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target_dir / _binary_name())
        log.info("installed plugin binary from %s", source)
        return True
    except OSError as exc:
        log.error("failed to install the plugin binary: %s", exc)
        return False


def uninstall_plugin(cs2_executable: str | os.PathLike[str] | None, remove_binary: bool = False) -> bool:
    """Disable the plugin again; optionally delete the binary we copied."""
    ok = restore_gameinfo(cs2_executable)
    if remove_binary:
        root = cs2_install_root(cs2_executable)
        if root is not None:
            folder = root / "game" / "csgo" / CSDM_FOLDER
            try:
                shutil.rmtree(folder, ignore_errors=True)
            except OSError as exc:  # pragma: no cover - permissions
                log.debug("could not remove %s: %s", folder, exc)
    return ok


def find_existing_plugin_binaries() -> list[Path]:
    """Look for a plugin binary in the usual CS Demo Manager locations."""
    candidates: list[Path] = []
    if os.name == "nt":
        for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA"), os.environ.get("PROGRAMFILES")):
            if not base:
                continue
            root = Path(base)
            for pattern in (
                "cs-demo-manager/**/static/cs2/server.dll",
                "CS Demo Manager/**/static/cs2/server.dll",
                "Programs/cs-demo-manager/**/server.dll",
            ):
                candidates += list(root.glob(pattern))
    else:
        for base in (Path.home() / ".config", Path("/opt"), Path("/usr/share")):
            candidates += list(base.glob("cs-demo-manager/**/libserver.so"))
    unique: list[Path] = []
    for candidate in candidates:
        if candidate.is_file() and candidate not in unique:
            unique.append(candidate)
    return unique


def cfg_dir_for(cs2_executable: str | os.PathLike[str] | None) -> Path | None:
    game = cs2_game_dir(cs2_executable)
    return game / "cfg" if game else None
