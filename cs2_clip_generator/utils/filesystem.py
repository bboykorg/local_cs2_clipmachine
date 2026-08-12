"""File-system helpers: safe names, hashing, disk space, atomic writes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}


def sanitize_filename(name: str, fallback: str = "clip", max_length: int = 120) -> str:
    """Turn arbitrary text (player names!) into a valid Windows file name.

    Cyrillic, Chinese and emoji-laden nicknames are extremely common in CS2, so
    letters are kept as-is and only characters that Windows forbids are
    replaced. Names that reduce to nothing fall back to ``fallback``.
    """
    text = unicodedata.normalize("NFC", str(name or "")).strip()
    text = _ILLEGAL.sub("_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_{2,}", "_", text).strip("._ ")
    if not text:
        return fallback
    if text.split(".")[0].upper() in _RESERVED:
        text = f"_{text}"
    if len(text) > max_length:
        text = text[:max_length].rstrip("._ ") or fallback
    return text


def unique_path(path: str | os.PathLike[str]) -> Path:
    """Return ``path`` or ``path (2)``, ``path (3)``... if it already exists."""
    target = Path(path)
    if not target.exists():
        return target
    stem, suffix, parent = target.stem, target.suffix, target.parent
    for index in range(2, 10_000):
        candidate = parent / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"cannot find a free file name near {target}")


def sha1_file(path: str | os.PathLike[str], chunk_size: int = 1 << 20, max_bytes: int | None = 64 << 20) -> str:
    """Hash a file in chunks — never load a 2 GB demo into RAM.

    Only the first ``max_bytes`` are hashed (plus the file size), which is
    plenty to identify a demo and keeps cache lookups instant on huge files.
    """
    digest = hashlib.sha1()  # noqa: S324 - cache key, not a security primitive
    size = os.path.getsize(path)
    digest.update(str(size).encode())
    read = 0
    with open(path, "rb") as handle:
        while True:
            if max_bytes is not None and read >= max_bytes:
                break
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
            read += len(block)
    return digest.hexdigest()


def free_space_mb(path: str | os.PathLike[str]) -> float:
    """Free megabytes on the volume that holds ``path`` (walks up if needed)."""
    target = Path(path)
    while not target.exists() and target.parent != target:
        target = target.parent
    try:
        usage = shutil.disk_usage(str(target))
    except OSError:
        return float("inf")
    return usage.free / (1024 * 1024)


def estimate_video_size_mb(duration_seconds: float, bitrate_kbps: int) -> float:
    """Rough encoded size, generously rounded up (audio + container overhead)."""
    return max(1.0, duration_seconds * bitrate_kbps / 8 / 1024 * 1.15)


def write_json(path: str | os.PathLike[str], payload: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)
    return target


def read_json(path: str | os.PathLike[str], default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def open_in_file_manager(path: str | os.PathLike[str]) -> bool:
    """Reveal a file or folder in Explorer/Finder/xdg-open."""
    target = Path(path)
    try:
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]  # noqa: S606
            return True
        from .process import run

        opener = "open" if os.uname().sysname == "Darwin" else "xdg-open"
        return run([opener, str(target)], timeout=10).ok
    except Exception:
        return False


def iter_files(root: str | os.PathLike[str], suffixes: tuple[str, ...]) -> list[Path]:
    base = Path(root)
    if not base.exists():
        return []
    return sorted(p for p in base.rglob("*") if p.suffix.lower() in suffixes)


def remove_tree(path: str | os.PathLike[str], on_error: Callable[[Exception], None] | None = None) -> None:
    try:
        shutil.rmtree(path, ignore_errors=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        if on_error:
            on_error(exc)
