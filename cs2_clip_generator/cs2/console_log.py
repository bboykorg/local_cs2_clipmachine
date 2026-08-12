"""Read CS2's console output from disk.

Launching the game with ``-condebug`` makes it mirror the console into
``game/csgo/console.log``. Tailing that file turns the console into a one-way
feedback channel even when no TCP console is available: schedule
``echo cs2clip_start`` at the clip's first tick and the moment the marker
appears on disk is the moment playback reached that tick.

That is what makes tick-accurate *external* recording (OBS, FFmpeg) possible
together with the tick-scheduling plugin.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from ..core.logger import get_logger

log = get_logger("cs2")

MARKER_PREFIX = "cs2clip"


def marker(name: str) -> str:
    return f"{MARKER_PREFIX}_{name}"


class ConsoleLogWatcher:
    """Tail ``console.log``, waiting for markers."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._offset = 0
        self._buffer = ""

    # -- lifecycle -------------------------------------------------------
    def reset(self, truncate: bool = True) -> None:
        """Start from a clean slate before a session."""
        self._buffer = ""
        self._offset = 0
        if truncate:
            try:
                if self.path.exists():
                    self.path.unlink()
            except OSError:
                # The game may hold the handle open; skip to the end instead.
                try:
                    self._offset = self.path.stat().st_size
                except OSError:
                    self._offset = 0

    def poll(self) -> str:
        """Return whatever was appended since the last call."""
        try:
            size = self.path.stat().st_size
        except OSError:
            return ""
        if size < self._offset:  # the game restarted and truncated the file
            self._offset = 0
        if size == self._offset:
            return ""
        try:
            with open(self.path, encoding="utf-8", errors="replace") as handle:
                handle.seek(self._offset)
                text = handle.read()
                self._offset = handle.tell()
        except OSError:
            return ""
        self._buffer += text
        if len(self._buffer) > 400_000:
            self._buffer = self._buffer[-200_000:]
        return text

    # -- waiting ---------------------------------------------------------
    def contains(self, needle: str) -> bool:
        self.poll()
        return needle in self._buffer

    def wait_for(
        self,
        needle: str,
        timeout: float = 60.0,
        poll_interval: float = 0.1,
        cancel=None,  # noqa: ANN001 - Callable[[], bool] | None
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel is not None and cancel():
                return False
            if self.contains(needle):
                return True
            time.sleep(poll_interval)
        log.debug("console marker '%s' did not appear within %.1fs", needle, timeout)
        return False

    def wait_for_any(
        self,
        needles: list[str],
        timeout: float = 60.0,
        poll_interval: float = 0.1,
        cancel=None,  # noqa: ANN001
    ) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel is not None and cancel():
                return None
            self.poll()
            for needle in needles:
                if needle in self._buffer:
                    return needle
            time.sleep(poll_interval)
        return None

    @property
    def text(self) -> str:
        return self._buffer


def console_log_path(cs2_game_dir: str | os.PathLike[str] | None) -> Path | None:
    if not cs2_game_dir:
        return None
    return Path(cs2_game_dir) / "console.log"
