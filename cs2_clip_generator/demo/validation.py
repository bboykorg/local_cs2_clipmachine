"""Pre-flight checks shown to the user before a demo is analysed."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import unsupported_demo
from .parser import available_backends
from .slots import CS2_MAGIC

CSGO_MAGIC = b"HL2DEMO\x00"


@dataclass
class ValidationResult:
    ok: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    game: str = "unknown"

    def add(self, label: str, passed: bool, detail: str = "") -> None:
        self.checks.append((label, passed, detail))
        if not passed:
            self.ok = False

    def as_lines(self) -> list[str]:
        return [
            f"{'✓' if passed else '⚠'} {label}{f' — {detail}' if detail else ''}"
            for label, passed, detail in self.checks
        ]


def detect_game(path: str | os.PathLike[str]) -> str:
    """``cs2``, ``csgo`` or ``unknown``, based on the file magic."""
    try:
        with open(path, "rb") as handle:
            magic = handle.read(8)
    except OSError:
        return "unknown"
    if magic == CS2_MAGIC:
        return "cs2"
    if magic == CSGO_MAGIC:
        return "csgo"
    return "unknown"


def validate_demo(path: str | os.PathLike[str]) -> ValidationResult:
    """Check the file before spending minutes parsing it."""
    target = Path(path)
    result = ValidationResult(ok=True)

    exists = target.is_file()
    result.add("File exists", exists, "" if exists else str(target))
    if not exists:
        return result

    size = target.stat().st_size
    result.add("File is not empty", size > 1024, f"{size / (1024 * 1024):.1f} MB")

    game = detect_game(target)
    result.game = game
    result.add(
        "Valid CS2 demo",
        game == "cs2",
        {
            "csgo": "this is a CS:GO (Source 1) demo",
            "unknown": "unrecognised file header",
        }.get(game, ""),
    )

    backends = available_backends()
    result.add(
        "Parser compatible",
        bool(backends),
        ", ".join(f"{b.name} {b.version()}" for b in backends) if backends else "no parser installed",
    )
    return result


def assert_supported(path: str | os.PathLike[str]) -> None:
    """Raise a user-friendly error if the demo cannot possibly be parsed."""
    result = validate_demo(path)
    if not result.ok:
        failed = [f"{label}: {detail}" for label, passed, detail in result.checks if not passed]
        raise unsupported_demo(str(path), "; ".join(failed))
