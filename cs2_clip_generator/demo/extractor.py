"""Unpack demos that arrive compressed.

Supported inputs: ``.dem``, ``.dem.bz2``, ``.dem.gz``, ``.dem.zip`` (and plain
``.bz2``/``.gz``/``.zip`` archives that contain a demo). Extraction streams in
chunks; a 3 GB archive never lands in RAM.
"""

from __future__ import annotations

import bz2
import gzip
import os
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import Cancelled, DemoError
from ..core.logger import get_logger
from ..utils.filesystem import free_space_mb, sanitize_filename, unique_path

log = get_logger("app")

CHUNK = 1 << 20
ARCHIVE_SUFFIXES = (".bz2", ".gz", ".zip")

ProgressCallback = Callable[[float, str], None]
CancelCallback = Callable[[], bool]


@dataclass
class ArchiveEntry:
    """One demo found inside an archive."""

    name: str
    size: int


def is_archive(path: str | os.PathLike[str]) -> bool:
    return str(path).lower().endswith(ARCHIVE_SUFFIXES)


def list_demos_in_zip(path: str | os.PathLike[str]) -> list[ArchiveEntry]:
    try:
        with zipfile.ZipFile(path) as archive:
            return [
                ArchiveEntry(name=info.filename, size=info.file_size)
                for info in archive.infolist()
                if not info.is_dir() and info.filename.lower().endswith(".dem")
            ]
    except (zipfile.BadZipFile, OSError) as exc:
        raise DemoError(
            title="This ZIP archive could not be opened.",
            reasons=["The archive is corrupted or incomplete"],
            actions=["Download it again"],
            detail=str(exc),
        ) from exc


def _copy_stream(source, target: Path, expected_size: int | None, progress, cancel) -> None:  # noqa: ANN001
    written = 0
    with open(target, "wb") as handle:
        while True:
            if cancel and cancel():
                raise Cancelled()
            block = source.read(CHUNK)
            if not block:
                break
            handle.write(block)
            written += len(block)
            if progress:
                fraction = min(0.99, written / expected_size) if expected_size else 0.0
                progress(fraction, f"Extracting… {written / (1024 * 1024):.0f} MB")


def extract_demo(
    path: str | os.PathLike[str],
    target_dir: str | os.PathLike[str],
    member: str | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
) -> Path:
    """Extract a single ``.dem`` out of ``path``.

    ``member`` selects which file to take from a multi-demo ZIP; the UI asks the
    user when there is more than one.
    """
    source = Path(path)
    directory = Path(target_dir)
    directory.mkdir(parents=True, exist_ok=True)
    lowered = source.name.lower()

    if not is_archive(source):
        return source

    compressed_size = source.stat().st_size
    # bz2/gz demos routinely compress 3-5x; assume 5x and check we have room.
    if free_space_mb(directory) < (compressed_size * 5) / (1024 * 1024):
        raise DemoError(
            title="Not enough disk space to unpack this demo.",
            reasons=[f"The archive is {compressed_size / (1024 * 1024):.0f} MB and expands several times over"],
            actions=["Free up space", "Change the temporary folder in Settings"],
        )

    if lowered.endswith(".zip"):
        entries = list_demos_in_zip(source)
        if not entries:
            raise DemoError(
                title="This archive does not contain a CS2 demo.",
                reasons=["No .dem file was found inside the ZIP"],
                actions=["Pick another file"],
            )
        chosen = member or entries[0].name
        entry = next((e for e in entries if e.name == chosen), None)
        if entry is None:
            raise DemoError(title=f"'{chosen}' was not found inside the archive.")
        target = unique_path(directory / sanitize_filename(os.path.basename(entry.name), fallback="match.dem"))
        log.info("extracting %s from %s", entry.name, source.name)
        with zipfile.ZipFile(source) as archive, archive.open(entry.name) as handle:
            _copy_stream(handle, target, entry.size, progress, cancel)
    elif lowered.endswith(".bz2"):
        target = unique_path(directory / sanitize_filename(source.name[: -len(".bz2")], fallback="match.dem"))
        log.info("decompressing %s (bz2)", source.name)
        with bz2.open(source, "rb") as handle:
            _copy_stream(handle, target, None, progress, cancel)
    elif lowered.endswith(".gz"):
        target = unique_path(directory / sanitize_filename(source.name[: -len(".gz")], fallback="match.dem"))
        log.info("decompressing %s (gzip)", source.name)
        with gzip.open(source, "rb") as handle:
            _copy_stream(handle, target, None, progress, cancel)
    else:  # pragma: no cover - guarded by is_archive
        return source

    if not target.name.lower().endswith(".dem"):
        renamed = target.with_suffix(".dem")
        os.replace(target, renamed)
        target = renamed
    if progress:
        progress(1.0, "Extracted")
    return target
