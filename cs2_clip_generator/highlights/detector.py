"""The highlight detector: match analysis in, ranked highlights out.

Order of operations::

    kills ──► group into multi-kills ──► tag each group
                                            │
    clutches (reconstructed alive sets) ────┤
                                            ▼
                                  score ──► clip windows ──► merge overlaps
                                                                  │
                                                                  ▼
                                                          titles + ranking

Nothing here invents facts: a tag is only attached when the demo carried the
corresponding flag, and a clutch is only reported when the alive-set
reconstruction is unambiguous.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from ..core.config import ClipSettings, ScoringSettings
from ..core.logger import get_logger
from ..core.models import (
    Highlight,
    HighlightKind,
    HighlightTag,
    MatchAnalysis,
    Round,
    Team,
    sort_highlights,
)
from ..demo.parser import (
    AWP_WEAPONS,
    DEAGLE_WEAPONS,
    FIRE_WEAPONS,
    GRENADES,
    PISTOLS,
    SCOUT_WEAPONS,
    ZEUS,
    is_knife,
    normalise_weapon,
)
from . import clutch as clutch_module
from . import timing
from .multikill import KillGroup, group_kills
from .scoring import score_highlight
from .titles import generate_title

log = get_logger("app")

LONG_RANGE_METERS = 30.0


@dataclass
class DetectorOptions:
    clips: ClipSettings
    scoring: ScoringSettings
    include_warmup: bool = False
    detect_clutches: bool = True
    long_range_meters: float = LONG_RANGE_METERS

    @classmethod
    def defaults(cls) -> DetectorOptions:
        return cls(clips=ClipSettings(), scoring=ScoringSettings())


def _highlight_id(steamid: str, round_number: int, first_tick: int, kind: str) -> str:
    raw = f"{steamid}:{round_number}:{first_tick}:{kind}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]  # noqa: S324 - identifier, not security


def _tags_for(group: KillGroup, round_: Round | None) -> list[HighlightTag]:
    """Derive tags strictly from what the kills prove."""
    tags: set[HighlightTag] = set()
    headshots = 0
    for kill in group.kills:
        weapon = normalise_weapon(kill.weapon)
        if kill.headshot:
            tags.add(HighlightTag.HEADSHOT)
            headshots += 1
        if kill.noscope:
            tags.add(HighlightTag.NOSCOPE)
        if kill.penetrated > 0:
            tags.add(HighlightTag.WALLBANG)
        if kill.through_smoke:
            tags.add(HighlightTag.THROUGH_SMOKE)
        if kill.attacker_blinded:
            tags.add(HighlightTag.BLINDED)
        if kill.attacker_in_air:
            tags.add(HighlightTag.JUMPING)
        if kill.distance >= LONG_RANGE_METERS:
            tags.add(HighlightTag.LONG_RANGE)
        if weapon in AWP_WEAPONS:
            tags.add(HighlightTag.AWP)
        if weapon in SCOUT_WEAPONS:
            tags.add(HighlightTag.SCOUT)
        if weapon in DEAGLE_WEAPONS:
            tags.add(HighlightTag.DEAGLE)
        if weapon in GRENADES:
            tags.add(HighlightTag.GRENADE)
        if weapon in FIRE_WEAPONS:
            tags.add(HighlightTag.MOLOTOV)
        if weapon in ZEUS:
            tags.add(HighlightTag.ZEUS)
        if is_knife(weapon):
            tags.add(HighlightTag.KNIFE)
    if group.kills and all(normalise_weapon(k.weapon) in PISTOLS for k in group.kills):
        tags.add(HighlightTag.PISTOL)
    if len(group.kills) >= 2 and headshots == len(group.kills):
        tags.add(HighlightTag.HEADSHOT_ONLY)
    if group.count >= 5:
        tags.add(HighlightTag.ACE)
    if round_ is not None and round_.bomb_planted_tick is not None:
        if any(kill.tick >= round_.bomb_planted_tick for kill in group.kills):
            tags.add(HighlightTag.POST_PLANT)
    return sorted(tags, key=lambda t: t.value)


def detect_highlights(analysis: MatchAnalysis, options: DetectorOptions | None = None) -> list[Highlight]:
    """Find every highlight in the match, scored, timed and titled."""
    options = options or DetectorOptions.defaults()
    rounds = {r.number: r for r in analysis.rounds}
    names = {p.steamid: p.name for p in analysis.players}
    teams = {p.steamid: p.team for p in analysis.players}

    groups = group_kills(
        analysis.kills,
        tickrate=analysis.tickrate or 64.0,
        window_seconds=options.clips.multikill_window_seconds,
        include_warmup=options.include_warmup,
    )

    clutches = clutch_module.find_clutches(analysis) if options.detect_clutches else []
    clutch_index = {(c.player_steamid, c.round_number): c for c in clutches}

    highlights: list[Highlight] = []
    for group in groups:
        round_ = rounds.get(group.round_number)
        tags = _tags_for(group, round_)
        kind = HighlightKind.for_kill_count(group.count)

        situation = clutch_index.get((group.player_steamid, group.round_number))
        clutch_vs = None
        enemies_alive = None
        teammates_alive = None
        if situation is not None and group.last_tick >= situation.start_tick:
            clutch_vs = situation.enemies_alive
            enemies_alive = situation.enemies_alive
            teammates_alive = 0
            tags = sorted({*tags, HighlightTag.CLUTCH}, key=lambda t: t.value)
            # An ACE stays an ACE; anything smaller is billed as the clutch.
            if kind != HighlightKind.ACE:
                kind = HighlightKind.CLUTCH
        else:
            counts = clutch_module.alive_counts_at(analysis, group.round_number, group.first_tick)
            team = teams.get(group.player_steamid, Team.UNKNOWN)
            if team == Team.T:
                teammates_alive, enemies_alive = counts[0], counts[1]
            elif team == Team.CT:
                teammates_alive, enemies_alive = counts[1], counts[0]

        highlight = Highlight(
            id=_highlight_id(group.player_steamid, group.round_number, group.first_tick, kind.value),
            kind=kind,
            player_steamid=group.player_steamid,
            player_name=names.get(group.player_steamid, group.kills[0].attacker_name or "Unknown"),
            round_number=group.round_number,
            kills=list(group.kills),
            tags=tags,
            clutch_vs=clutch_vs,
            enemies_alive=enemies_alive,
            teammates_alive=teammates_alive,
            team=teams.get(group.player_steamid, group.kills[0].attacker_team),
        )
        highlight.score, highlight.score_breakdown = score_highlight(
            highlight, options.scoring, options.long_range_meters
        )
        highlights.append(highlight)

    timing.apply_clip_ranges(highlights, analysis, options.clips)
    highlights = timing.merge_overlapping(
        highlights,
        tickrate=analysis.tickrate or 64.0,
        gap_seconds=options.clips.merge_gap_seconds,
        enabled=options.clips.merge_overlapping,
    )

    # Merging can change a highlight's kind (2K + 2K -> 4K), so score and title
    # are finalised afterwards.
    for highlight in highlights:
        highlight.score, highlight.score_breakdown = score_highlight(
            highlight, options.scoring, options.long_range_meters
        )
        highlight.title = generate_title(highlight, analysis.map_name)

    highlights = sort_highlights(highlights, "score")
    log.info("detected %d highlights across %d rounds", len(highlights), len(analysis.rounds))
    return highlights


def highlights_for_player(highlights: Sequence[Highlight], steamid: str | None) -> list[Highlight]:
    if not steamid:
        return list(highlights)
    return [h for h in highlights if h.player_steamid == str(steamid)]


def update_player_stats(analysis: MatchAnalysis, options: DetectorOptions | None = None) -> None:
    """Fold multi-kill and clutch counts into the per-player statistics.

    Deliberately computed from the *strict* multi-kill grouping rather than from
    the final highlight list: merging overlapping clips can turn two separate
    kills into one two-kill clip, and that must not be advertised as a 2K in the
    scoreboard.
    """
    options = options or DetectorOptions.defaults()
    for stats in analysis.stats.values():
        stats.multi_2k = stats.multi_3k = stats.multi_4k = stats.aces = stats.clutches = 0

    for group in group_kills(
        analysis.kills,
        tickrate=analysis.tickrate or 64.0,
        window_seconds=options.clips.multikill_window_seconds,
        include_warmup=options.include_warmup,
    ):
        stats = analysis.stats.get(group.player_steamid)
        if stats is None:
            continue
        if group.count >= 5:
            stats.aces += 1
        elif group.count == 4:
            stats.multi_4k += 1
        elif group.count == 3:
            stats.multi_3k += 1
        elif group.count == 2:
            stats.multi_2k += 1

    if options.detect_clutches:
        for situation in clutch_module.find_clutches(analysis):
            stats = analysis.stats.get(situation.player_steamid)
            if stats is not None:
                stats.clutches += 1


def auto_select(
    highlights: Sequence[Highlight],
    max_clips: int,
    min_score: float,
    steamid: str | None = None,
) -> list[Highlight]:
    """The selection behind the AUTO CLIP button: best first, above threshold."""
    candidates = highlights_for_player(highlights, steamid)
    candidates = [h for h in candidates if h.score >= min_score]
    candidates = sort_highlights(candidates, "score")
    return candidates[: max(0, int(max_clips))]
