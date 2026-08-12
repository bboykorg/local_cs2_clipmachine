"""Local, deterministic clip titles — no AI, no templates pulled from a server.

Examples produced by this module::

    ACE on Mirage — Round 17
    Insane 4K — Round 12
    Clean 3K with AK47 — Round 8
    1v3 Clutch — Round 21
    AWP noscope — Round 4
"""

from __future__ import annotations

from collections import Counter

from ..core.models import Highlight, HighlightKind, HighlightTag
from ..demo.parser import weapon_display_name

_MAP_NAMES = {
    "de_mirage": "Mirage",
    "de_inferno": "Inferno",
    "de_nuke": "Nuke",
    "de_ancient": "Ancient",
    "de_anubis": "Anubis",
    "de_dust2": "Dust2",
    "de_vertigo": "Vertigo",
    "de_overpass": "Overpass",
    "de_cache": "Cache",
    "de_train": "Train",
    "de_cbble": "Cobblestone",
    "de_office": "Office",
    "de_italy": "Italy",
    "cs_office": "Office",
}


def pretty_map_name(map_name: str) -> str:
    """``de_mirage`` → ``Mirage``; unknown maps keep their own name, tidied up."""
    key = (map_name or "").lower()
    if key in _MAP_NAMES:
        return _MAP_NAMES[key]
    for prefix in ("de_", "cs_", "ar_", "dz_"):
        if key.startswith(prefix):
            key = key[len(prefix) :]
            break
    return key.replace("_", " ").title() if key else "Unknown map"


def dominant_weapon(highlight: Highlight) -> str | None:
    if not highlight.kills:
        return None
    counts = Counter(k.weapon for k in highlight.kills if k.weapon)
    if not counts:
        return None
    weapon, count = counts.most_common(1)[0]
    if count < max(1, len(highlight.kills) // 2 + len(highlight.kills) % 2):
        return None
    return weapon_display_name(weapon)


def _adjective(highlight: Highlight) -> str:
    if highlight.kind == HighlightKind.ACE:
        return ""
    if highlight.has_tag(HighlightTag.HEADSHOT_ONLY):
        return "Clean"
    if highlight.kind == HighlightKind.MULTI_4K:
        return "Insane"
    if highlight.has_tag(HighlightTag.NOSCOPE) or highlight.has_tag(HighlightTag.WALLBANG):
        return "Crazy"
    if highlight.kind in (HighlightKind.MULTI_3K, HighlightKind.MULTI_2K):
        return "Clean" if highlight.headshot_count == highlight.kill_count else ""
    return ""


def generate_title(highlight: Highlight, map_name: str = "") -> str:
    """Compose a human title for a highlight."""
    map_label = pretty_map_name(map_name) if map_name else ""
    round_label = f"Round {highlight.round_number}"
    weapon = dominant_weapon(highlight)

    if highlight.kind == HighlightKind.CLUTCH:
        headline = f"1v{highlight.clutch_vs} Clutch" if highlight.clutch_vs else "Clutch"
        if highlight.kill_count >= 3:
            headline = f"{headline} ({highlight.kill_count}K)"
        return f"{headline} — {round_label}"

    if highlight.kind == HighlightKind.ACE:
        base = f"ACE on {map_label}" if map_label else "ACE"
        if highlight.has_tag(HighlightTag.HEADSHOT_ONLY):
            base = f"All-headshot {base}"
        return f"{base} — {round_label}"

    if highlight.kind == HighlightKind.KILL:
        specials = [
            (HighlightTag.KNIFE, "Knife kill"),
            (HighlightTag.ZEUS, "Zeus kill"),
            (HighlightTag.NOSCOPE, f"{weapon or 'AWP'} noscope"),
            (HighlightTag.WALLBANG, f"{weapon or 'Rifle'} wallbang"),
            (HighlightTag.GRENADE, "Grenade kill"),
            (HighlightTag.MOLOTOV, "Molotov kill"),
            (HighlightTag.THROUGH_SMOKE, f"{weapon or 'Rifle'} through smoke"),
            (HighlightTag.JUMPING, f"Jumping {weapon or 'kill'}"),
        ]
        for tag, label in specials:
            if highlight.has_tag(tag):
                return f"{label} — {round_label}"
        if highlight.has_tag(HighlightTag.LONG_RANGE) and weapon:
            return f"Long range {weapon} — {round_label}"
        suffix = " headshot" if highlight.headshot_count else " kill"
        return f"{weapon or 'Clean'}{suffix} — {round_label}"

    adjective = _adjective(highlight)
    kind_label = highlight.kind.value
    parts = [part for part in (adjective, kind_label) if part]
    headline = " ".join(parts)
    if weapon:
        headline = f"{headline} with {weapon}"
    return f"{headline} — {round_label}"


def clip_filename(highlight: Highlight, extension: str = "mp4") -> str:
    """``ACE_Round17_Player1.mp4`` — sortable, obvious, and Windows-safe."""
    from ..utils.filesystem import sanitize_filename

    kind = highlight.kind.value
    if highlight.kind == HighlightKind.CLUTCH and highlight.clutch_vs:
        kind = f"1v{highlight.clutch_vs}"
    player = sanitize_filename(highlight.player_name, fallback="player", max_length=40)
    return f"{kind}_Round{highlight.round_number:02d}_{player}.{extension}"
