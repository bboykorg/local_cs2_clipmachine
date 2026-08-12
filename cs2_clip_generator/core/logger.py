"""Logging setup: one rotating file per subsystem plus a shared console stream.

The rule enforced everywhere else in the code base: *tracebacks go to the log,
never to the user*. :mod:`cs2_clip_generator.core.errors` turns exceptions into
messages a player can act on; this module makes sure the details survive.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

_CONFIGURED = False
_LOG_DIR: Path | None = None

#: subsystem -> file name
LOG_FILES = {
    "app": "app.log",
    "parser": "parser.log",
    "recorder": "recorder.log",
    "ffmpeg": "ffmpeg.log",
    "cs2": "cs2.log",
}

_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


def setup_logging(log_dir: str | os.PathLike[str], verbose: bool = False) -> Path:
    """Attach rotating file handlers. Safe to call more than once."""
    global _CONFIGURED, _LOG_DIR

    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    _LOG_DIR = directory

    root = logging.getLogger("cs2clip")
    root.setLevel(logging.DEBUG)
    root.propagate = False

    if _CONFIGURED:
        return directory

    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    # The console belongs to the user's progress output; details go to the files.
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(formatter)
    root.addHandler(console)

    for subsystem, filename in LOG_FILES.items():
        handler = logging.handlers.RotatingFileHandler(
            directory / filename, maxBytes=4 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(formatter)
        logger = logging.getLogger(f"cs2clip.{subsystem}")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        # Everything also lands in app.log for a single chronological view.
        if subsystem != "app":
            logger.propagate = True

    _CONFIGURED = True
    logging.getLogger("cs2clip.app").debug("Logging initialised in %s", directory)
    return directory


def get_logger(name: str = "app") -> logging.Logger:
    """Return the logger for a subsystem (``app``, ``parser``, ``recorder``...)."""
    if name in LOG_FILES:
        return logging.getLogger(f"cs2clip.{name}")
    return logging.getLogger(f"cs2clip.app.{name}")


def log_dir() -> Path | None:
    return _LOG_DIR
