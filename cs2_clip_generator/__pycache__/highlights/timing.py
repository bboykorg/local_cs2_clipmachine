"""Turn a highlight into a tick range, then keep the ranges sane.

Two jobs:

1. **Windows.** A single kill wants a short run-up; an ACE wants to show the
   whole sequence. The lead-in/lead-out per highlight kind is configurable and
   the result is clamped to the round (nobody wants to watch the buy menu of the
   next round) and to the demo itself.

2. **Merging.** Two highlights three seconds apart would produce two nearly
   identical videos. When *Merge overlapping clips* is on, their ranges are
   fused into one clip and the highlights are combined, keeping every kill.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..core.config import ClipSettings
from ..core.models import Highlight, HighlightKind, MatchAnalysis, Round


def _window_for(kind: HighlightKind, settings: ClipSettings) -> tuple[float, float]:
    lead_in = settings.lead_in.get(kind.value, settings.lead_in.get("KILL", 6.0))
    lead_out = settings.lead_out.get(kind.value, settings.lead_out.get("KILL", 4.0))
    return float(lead_in), float(lead_out)


def compute_clip_range(
    highlight: Highlight,
    analysis: MatchAnalysis,
    settings: ClipSettings | None = None,
    round_: Round | None = None,
) -> tuple[int, int]:
    """Compute ``(start_tick, end_tick)`` for one highlight."""
    settings = settings or ClipSettings()
    tickrate = analysis.tickrate or 64.0
    lead_in, lead_out = _window_for(highlight.kind, settings)

    first = highlight.first_kill_tick
    last = highlight.last_kill_tick
    start = first - int(round(lead_in * tickrate))
    end = last + int(round(lead_out * tickrate))

    round_ = round_ or analysis.round(highlight.round_number)
    if settings.clamp_to_round and round_ is not None:
        padding = int(round(settings.round_padding_seconds * tickrate))
        # Never start before the round's freeze time ends: earlier ticks are the
        # buy phase and are frozen anyway.
        floor = max(round_.start_tick, round_.play_start_tick - padding)
        ceiling = (round_.official_end_tick or round_.end_tick) + padding
        start = max(start, floor)
        end = min(end, ceiling)

    start = max(0, start)
    if analysis.total_ticks:
        end = min(end, analysis.total_ticks)
    if end <= start:
        end = start + int(round(2 * tickrate))
    return int(start), int(end)


def apply_clip_ranges(
    highlights: Sequence[Highlight], analysis: MatchAnalysis, settings: ClipSettings | None = None
) -> list[Highlight]:
    """Fill in ``start_tick``/``end_tick`` on every highlight (in place)."""
    rounds = {r.number: r for r in analysis.rounds}
    for highlight in highlights:
        start, end = compute_clip_range(highlight, analysis, settings, rounds.get(highlight.round_number))
        highlight.start_tick = start
        highlight.end_tick = end
    return list(highlights)


def merge_overlapping(
    highlights: Sequence[Highlight],
    tickrate: float,
    gap_seconds: float = 1.0,
    enabled: bool = True,
) -> list[Highlight]:
    """Fuse highlights of the same player whose clip ranges touch.

    ``Clip A: 100–130s`` and ``Clip B: 125–150s`` become one ``100–150s`` clip.
    Only highlights of the same player in the same round are merged: two players
    acing at the same time are two different videos.
    """
    if not enabled:
        return list(highlights)

    gap_ticks = max(0, int(round(gap_seconds * tickrate)))
    buckets: dict[tuple[str, int], list[Highlight]] = {}
    for highlight in highlights:
        buckets.setdefault((highlight.player_steamid, highlight.round_number), []).append(highlight)

    merged: list[Highlight] = []
    for group in buckets.values():
        group.sort(key=lambda h: h.start_tick)
        current = group[0]
        for candidate in group[1:]:
            if candidate.start_tick <= current.end_tick + gap_ticks:
                current = _fuse(current, candidate)
            else:
                merged.append(current)
                current = candidate
        merged.append(current)

    merged.sort(key=lambda h: h.start_tick)
    return merged


def _fuse(first: Highlight, second: Highlight) -> Highlight:
    """Combine two highlights, keeping every kill and the stronger headline."""
    kills = sorted({k.tick: k for k in [*first.kills, *second.kills]}.values(), key=lambda k: k.tick)
    keep_clutch = HighlightKind.CLUTCH in (first.kind, second.kind)
    kind = HighlightKind.CLUTCH if keep_clutch else HighlightKind.for_kill_count(len(kills))

    winner = first if first.score >= second.score else second
    fused = Highlight(
        id=winner.id,
        kind=kind,
        player_steamid=first.player_steamid,
        player_name=first.player_name,
        round_number=first.round_number,
        kills=kills,
        tags=sorted({*first.tags, *second.tags}, key=lambda t: t.value),
        score=max(first.score, second.score),
        score_breakdown=dict(winner.score_breakdown),
        title=winner.title,
        start_tick=min(first.start_tick, second.start_tick),
        end_tick=max(first.end_tick, second.end_tick),
        enemies_alive=winner.enemies_alive,
        teammates_alive=winner.teammates_alive,
        clutch_vs=first.clutch_vs or second.clutch_vs,
        team=first.team,
        score_t=winner.score_t,
        score_ct=winner.score_ct,
        merged_from=[*first.merged_from, *second.merged_from, first.id, second.id],
    )
    fused.merged_from = sorted(set(fused.merged_from) - {fused.id})
    return fused
