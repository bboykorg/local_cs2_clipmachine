"""Filtering, searching and exporting highlight lists."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..core.models import Highlight, HighlightKind, HighlightTag, sort_highlights
from ..utils.filesystem import write_json

#: The checkboxes shown in the Highlights page, in display order.
FILTER_DEFINITIONS: list[tuple[str, str]] = [
    ("ACE", "ACE"),
    ("4K", "4K"),
    ("3K", "3K"),
    ("2K", "2K"),
    ("KILL", "Single kills"),
    ("CLUTCH", "Clutch"),
    ("KNIFE", "Knife"),
    ("GRENADE", "Grenade"),
    ("AWP", "AWP"),
    ("NOSCOPE", "Noscope"),
    ("WALLBANG", "Wallbang"),
    ("THROUGH_SMOKE", "Through smoke"),
    ("JUMPING", "Jump shot"),
    ("HEADSHOT", "Headshot"),
]

_KINDS = {kind.value for kind in HighlightKind}


@dataclass
class HighlightFilter:
    """Kind/tag checkboxes + free text + score floor."""

    active: set[str] = field(default_factory=set)
    query: str = ""
    min_score: float = 0.0
    player_steamid: str | None = None

    def matches(self, highlight: Highlight) -> bool:
        if self.player_steamid and highlight.player_steamid != self.player_steamid:
            return False
        if highlight.score < self.min_score:
            return False
        if self.active:
            kinds = {name for name in self.active if name in _KINDS}
            tags = {name for name in self.active if name not in _KINDS}
            kind_ok = highlight.kind.value in kinds if kinds else False
            tag_ok = any(tag.value in tags for tag in highlight.tags) if tags else False
            if not (kind_ok or tag_ok):
                return False
        if self.query and not highlight.matches_query(self.query):
            return False
        return True

    def apply(self, highlights: Iterable[Highlight], sort_key: str = "score") -> list[Highlight]:
        return sort_highlights([h for h in highlights if self.matches(h)], sort_key)


def filter_highlights(
    highlights: Iterable[Highlight],
    kinds: Iterable[str] = (),
    tags: Iterable[HighlightTag] = (),
    min_score: float = 0.0,
    query: str = "",
    sort_key: str = "score",
) -> list[Highlight]:
    """Convenience wrapper used by the CLI."""
    active = {*kinds, *[t.value for t in tags]}
    return HighlightFilter(active=active, min_score=min_score, query=query).apply(highlights, sort_key)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def highlights_to_json(highlights: Sequence[Highlight], path: str | Path) -> Path:
    return write_json(path, [h.to_dict() for h in highlights])


KILL_CSV_COLUMNS = [
    "round",
    "tick",
    "time",
    "attacker",
    "attacker_steamid",
    "victim",
    "victim_steamid",
    "weapon",
    "headshot",
    "noscope",
    "wallbang",
    "through_smoke",
    "attacker_blinded",
    "airborne",
    "distance_m",
    "assister",
]


def kills_to_csv_rows(highlights: Sequence[Highlight]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for highlight in highlights:
        for kill in highlight.kills:
            rows.append(
                {
                    "round": kill.round_number,
                    "tick": kill.tick,
                    "time": round(kill.time, 2),
                    "attacker": kill.attacker_name or "",
                    "attacker_steamid": kill.attacker_steamid or "",
                    "victim": kill.victim_name or "",
                    "victim_steamid": kill.victim_steamid or "",
                    "weapon": kill.weapon,
                    "headshot": int(kill.headshot),
                    "noscope": int(kill.noscope),
                    "wallbang": int(kill.penetrated > 0),
                    "through_smoke": int(kill.through_smoke),
                    "attacker_blinded": int(kill.attacker_blinded),
                    "airborne": int(kill.attacker_in_air),
                    "distance_m": round(kill.distance, 1),
                    "assister": kill.assister_name or "",
                }
            )
    rows.sort(key=lambda row: (row["round"], row["tick"]))
    return rows


def kills_to_csv(highlights: Sequence[Highlight], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = kills_to_csv_rows(highlights)
    with open(target, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=KILL_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return target


def highlights_to_csv_text(highlights: Sequence[Highlight]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["type", "round", "player", "kills", "score", "title", "start_tick", "end_tick", "tags"])
    for highlight in highlights:
        writer.writerow(
            [
                highlight.kind.value,
                highlight.round_number,
                highlight.player_name,
                highlight.kill_count,
                highlight.score,
                highlight.title,
                highlight.start_tick,
                highlight.end_tick,
                " ".join(t.value for t in highlight.tags),
            ]
        )
    return buffer.getvalue()
