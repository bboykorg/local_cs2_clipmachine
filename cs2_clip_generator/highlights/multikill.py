"""Group a player's kills into multi-kills.

The rule that matters (and the one users notice when it is wrong): kills belong
to the same multi-kill when *each consecutive pair* is closer together than the
multi-kill window. So with a 7 second window::

    12:10  12:13  12:16   ->  one 3K   (gaps 3s, 3s)
    12:10  12:30          ->  two separate kills (gap 20s)

Chaining is deliberate: a 12-second-long 3K with 5-second gaps reads as one
sequence to a viewer, and that is what Allstar-style clips show.

An ACE is special-cased: five kills in one round is an ACE even when the kills
are minutes apart, so all of a player's groups in such a round are fused.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ..core.models import KillEvent


@dataclass
class KillGroup:
    """Kills by one player, in one round, that belong to one highlight."""

    player_steamid: str
    round_number: int
    kills: list[KillEvent] = field(default_factory=list)
    #: True when the group was fused because the player aced the round.
    is_ace: bool = False

    @property
    def count(self) -> int:
        return len(self.kills)

    @property
    def first_tick(self) -> int:
        return min(k.tick for k in self.kills)

    @property
    def last_tick(self) -> int:
        return max(k.tick for k in self.kills)


def relevant_kills(kills: Iterable[KillEvent], include_warmup: bool = False) -> list[KillEvent]:
    """Drop everything that should never become a highlight."""
    out = []
    for kill in kills:
        if kill.attacker_steamid is None:
            continue  # world damage / suicide
        if kill.is_suicide or kill.is_teamkill:
            continue
        if not include_warmup and kill.round_number <= 0:
            continue
        out.append(kill)
    return out


def group_kills(
    kills: Sequence[KillEvent],
    tickrate: float,
    window_seconds: float = 7.0,
    ace_kill_count: int = 5,
    include_warmup: bool = False,
) -> list[KillGroup]:
    """Split kills into :class:`KillGroup` objects, newest last.

    Grouping happens per (player, round); the window is measured in ticks so the
    behaviour is identical on 64 and 128 tick demos.
    """
    window_ticks = max(1, int(round(window_seconds * tickrate)))
    per_player: dict[tuple[str, int], list[KillEvent]] = {}
    for kill in sorted(relevant_kills(kills, include_warmup), key=lambda k: k.tick):
        assert kill.attacker_steamid is not None
        per_player.setdefault((kill.attacker_steamid, kill.round_number), []).append(kill)

    groups: list[KillGroup] = []
    for (steamid, round_number), player_kills in per_player.items():
        if len(player_kills) >= ace_kill_count:
            groups.append(
                KillGroup(player_steamid=steamid, round_number=round_number, kills=list(player_kills), is_ace=True)
            )
            continue
        current: list[KillEvent] = []
        for kill in player_kills:
            if current and kill.tick - current[-1].tick > window_ticks:
                groups.append(KillGroup(player_steamid=steamid, round_number=round_number, kills=current))
                current = []
            current.append(kill)
        if current:
            groups.append(KillGroup(player_steamid=steamid, round_number=round_number, kills=current))

    groups.sort(key=lambda g: (g.first_tick, g.player_steamid))
    return groups
