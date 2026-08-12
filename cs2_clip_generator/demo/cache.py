"""Analysis cache.

Parsing a 2 GB demo is the slowest thing this app does, and the result never
changes. The cache key is a cheap content hash (size + first 64 MB) plus the
parser version, so a parser upgrade invalidates old entries automatically.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from ..core.logger import get_logger
from ..core.models import MatchAnalysis
from ..utils.filesystem import read_json, sha1_file, write_json

log = get_logger("app")

CACHE_SCHEMA = 2


@dataclass
class CacheEntry:
    key: str
    demo_path: str
    map_name: str
    created_at: float
    size_bytes: int


class AnalysisCache:
    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    # -- keys ------------------------------------------------------------
    @staticmethod
    def key_for(demo_path: str | os.PathLike[str], parser_version: str = "") -> str:
        return f"{sha1_file(demo_path)}-{CACHE_SCHEMA}{f'-{parser_version}' if parser_version else ''}"

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    # -- read / write ----------------------------------------------------
    def load(self, demo_path: str | os.PathLike[str], parser_version: str = "") -> MatchAnalysis | None:
        key = self.key_for(demo_path, parser_version)
        target = self._path(key)
        if not target.exists():
            return None
        payload = read_json(target)
        if not isinstance(payload, dict) or payload.get("schema_version") != CACHE_SCHEMA:
            return None
        try:
            analysis = MatchAnalysis.from_dict(payload)
        except (TypeError, ValueError, KeyError) as exc:
            log.debug("cache entry %s is unreadable: %s", key, exc)
            return None
        # The cached path may be stale (demo moved); trust the current one.
        analysis.demo_path = os.path.abspath(str(demo_path))
        log.info("cache hit for %s", os.path.basename(str(demo_path)))
        return analysis

    def store(self, analysis: MatchAnalysis, parser_version: str = "") -> Path:
        key = self.key_for(analysis.demo_path, parser_version or analysis.parser_version)
        payload = analysis.to_dict()
        payload["cached_at"] = time.time()
        path = write_json(self._path(key), payload)
        log.info("cached analysis for %s (%s)", os.path.basename(analysis.demo_path), key[:12])
        return path

    # -- housekeeping ----------------------------------------------------
    def entries(self) -> list[CacheEntry]:
        out: list[CacheEntry] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append(
                CacheEntry(
                    key=path.stem,
                    demo_path=str(payload.get("demo_path", "")),
                    map_name=str(payload.get("map_name", "")),
                    created_at=float(payload.get("cached_at") or path.stat().st_mtime),
                    size_bytes=path.stat().st_size,
                )
            )
        return sorted(out, key=lambda e: e.created_at, reverse=True)

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.directory.glob("*.json"))

    def clear(self) -> int:
        removed = 0
        for path in self.directory.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        log.info("cleared %d cache entries", removed)
        return removed
