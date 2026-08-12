"""How interesting is a moment? Turn a highlight into a number.

The score is a sum of three configurable parts:

* a **base** for the headline (ACE, 4K, 1v3 clutch, …),
* a **per-kill** bonus for every flavour a kill actually had (headshot, AWP,
  wallbang, …), so an ACE with five headshots outscores a quiet one,
* a **bonus** for whole-sequence properties (all-headshot, ninja defuse).

Every number lives in :class:`~cs2_clip_generator.core.config.ScoringSettings`
and can be edited in Settings, which is why nothing here is hard-coded.
"""

from __future__ import annotations

from ..core.config import ScoringSettings
from ..core.models import Highlight, HighlightKind, HighlightTag

#: Tags that are counted once per kill that carries them.
PER_KILL_TAGS = {
    HighlightTag.HEADSHOT,
    HighlightTag.KNIFE,
    HighlightTag.ZEUS,
    HighlightTag.GRENADE,
    HighlightTag.MOLOTOV,
    HighlightTag.WALLBANG,
    HighlightTag.NOSCOPE,
    HighlightTag.THROUGH_SMOKE,
    HighlightTag.BLINDED,
    HighlightTag.JUMPING,
    HighlightTag.AWP,
    HighlightTag.SCOUT,
    HighlightTag.DEAGLE,
    HighlightTag.PISTOL,
    HighlightTag.LONG_RANGE,
}


def kill_tag_counts(highlight: Highlight, long_range_meters: float = 30.0) -> dict[str, int]:
    """How many kills in this highlight carried each per-kill tag."""
    counts: dict[str, int] = {}

    def bump(tag: HighlightTag) -> None:
        counts[tag.value] = counts.get(tag.value, 0) + 1

    from ..demo.parser import (  # local import keeps the module import-light
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

    for kill in highlight.kills:
        weapon = normalise_weapon(kill.weapon)
        if kill.headshot:
            bump(HighlightTag.HEADSHOT)
        if kill.noscope:
            bump(HighlightTag.NOSCOPE)
        if kill.penetrated > 0:
            bump(HighlightTag.WALLBANG)
        if kill.through_smoke:
            bump(HighlightTag.THROUGH_SMOKE)
        if kill.attacker_blinded:
            bump(HighlightTag.BLINDED)
        if kill.attacker_in_air:
            bump(HighlightTag.JUMPING)
        if kill.distance >= long_range_meters:
            bump(HighlightTag.LONG_RANGE)
        if weapon in AWP_WEAPONS:
            bump(HighlightTag.AWP)
        elif weapon in SCOUT_WEAPONS:
            bump(HighlightTag.SCOUT)
        elif weapon in DEAGLE_WEAPONS:
            bump(HighlightTag.DEAGLE)
        elif weapon in PISTOLS:
            bump(HighlightTag.PISTOL)
        if is_knife(weapon):
            bump(HighlightTag.KNIFE)
        if weapon in ZEUS:
            bump(HighlightTag.ZEUS)
        if weapon in GRENADES:
            bump(HighlightTag.GRENADE)
        if weapon in FIRE_WEAPONS:
            bump(HighlightTag.MOLOTOV)
    return counts


def base_key(highlight: Highlight) -> str:
    """The scoring key for a highlight's headline."""
    if highlight.kind == HighlightKind.CLUTCH and highlight.clutch_vs:
        return f"CLUTCH_1V{min(5, highlight.clutch_vs)}"
    return highlight.kind.value


def score_highlight(
    highlight: Highlight, settings: ScoringSettings | None = None, long_range_meters: float = 30.0
) -> tuple[float, dict[str, float]]:
    """Return ``(score, breakdown)``; the breakdown is shown in developer mode."""
    settings = settings or ScoringSettings()
    breakdown: dict[str, float] = {}

    key = base_key(highlight)
    base = settings.base.get(key)
    if base is None:
        base = settings.base.get(highlight.kind.value, 0.0)
    breakdown[key] = float(base)

    # A clutch that is also a multi-kill keeps the better of the two bases and
    # adds a quarter of the weaker one, so a 1v3 that is also a 3K stands out
    # without double counting.
    if highlight.kind == HighlightKind.CLUTCH:
        multi_key = HighlightKind.for_kill_count(highlight.kill_count).value
        multi_base = float(settings.base.get(multi_key, 0.0))
        if multi_base:
            breakdown[f"+{multi_key}"] = round(multi_base * 0.25, 2)

    counts = kill_tag_counts(highlight, long_range_meters)
    for tag_name, count in counts.items():
        value = settings.per_kill.get(tag_name)
        if value:
            breakdown[f"{tag_name} x{count}"] = float(value) * count

    if highlight.kill_count >= 2 and counts.get(HighlightTag.HEADSHOT.value, 0) == highlight.kill_count:
        bonus = settings.bonus.get(HighlightTag.HEADSHOT_ONLY.value, 0.0)
        if bonus:
            breakdown[HighlightTag.HEADSHOT_ONLY.value] = float(bonus)
    if highlight.has_tag(HighlightTag.POST_PLANT):
        bonus = settings.bonus.get(HighlightTag.POST_PLANT.value, 0.0)
        if bonus:
            breakdown[HighlightTag.POST_PLANT.value] = float(bonus)
    if highlight.has_tag(HighlightTag.NINJA_DEFUSE):
        bonus = settings.bonus.get(HighlightTag.NINJA_DEFUSE.value, 0.0)
        if bonus:
            breakdown[HighlightTag.NINJA_DEFUSE.value] = float(bonus)

    total = round(sum(breakdown.values()), 2)
    return total, {k: round(v, 2) for k, v in breakdown.items()}
