"""Clutch detection from kill events alone.

A clutch is "last player standing wins the round". Detecting it needs to know
who was alive at a given moment, and asking the parser for per-tick alive flags
across a whole match is expensive. It is also unnecessary: within a round, the
alive set is fully determined by who started the round and who died since.

So the alive set is *reconstructed*: everyone on a team is alive at the round's
freeze-time end, and each ``player_death`` removes exactly one player. When a
player becomes the last one alive on their team, the number of enemies still
standing is the ``1vN`` figure.

Honesty about limits: disconnects and mid-round reconnects are not visible in
kill events, so a player who leaves is still counted as alive. Such rounds are
rare and the resulting clutch is simply not reported (the round winner check
below fails), rather than reported wrongly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..core.models import KillEvent, MatchAnalysis, Player, Round, Team


@dataclass
class ClutchSituation:
    player_steamid: str
    round_number: int
    #: Tick at which the player became the last one alive on their team.
    start_tick: int
    enemies_alive: int
    kills_after: int
    won: bool

    @property
    def label(self) -> str:
        return f"1v{self.enemies_alive}"


def _round_roster(players: Sequence[Player], kills: Sequence[KillEvent], round_number: int) -> dict[str, Team]:
    """Best available guess of who played this round, and for which team.

    Team membership is taken from the round's own kill events when possible
    (players swap sides at half time), falling back to the match-level team.
    """
    roster: dict[str, Team] = {}
    for kill in kills:
        if kill.round_number != round_number:
            continue
        if kill.attacker_steamid and kill.attacker_team != Team.UNKNOWN:
            roster[kill.attacker_steamid] = kill.attacker_team
        if kill.victim_steamid and kill.victim_team != Team.UNKNOWN:
            roster[kill.victim_steamid] = kill.victim_team
    for player in players:
        if player.steamid not in roster and player.team in (Team.T, Team.CT):
            roster[player.steamid] = player.team
    return roster


def find_clutches(analysis: MatchAnalysis, min_enemies: int = 1) -> list[ClutchSituation]:
    """Find every clutch situation in the match."""
    out: list[ClutchSituation] = []
    for round_ in analysis.rounds:
        out.extend(_clutches_in_round(analysis, round_, min_enemies))
    return out


def _clutches_in_round(analysis: MatchAnalysis, round_: Round, min_enemies: int) -> list[ClutchSituation]:
    kills = [k for k in analysis.kills if k.round_number == round_.number]
    if not kills:
        return []
    roster = _round_roster(analysis.players, analysis.kills, round_.number)
    alive: dict[Team, set[str]] = {Team.T: set(), Team.CT: set()}
    for steamid, team in roster.items():
        if team in alive:
            alive[team].add(steamid)
    if len(alive[Team.T]) < 2 or len(alive[Team.CT]) < 2:
        return []  # not enough information to talk about clutches

    found: list[ClutchSituation] = []
    announced: set[str] = set()

    for kill in sorted(kills, key=lambda k: k.tick):
        victim, victim_team = kill.victim_steamid, kill.victim_team
        if victim_team not in alive and victim:
            victim_team = roster.get(victim, Team.UNKNOWN)
        if victim and victim_team in alive:
            alive[victim_team].discard(victim)

        for team in (Team.T, Team.CT):
            enemy = Team.CT if team == Team.T else Team.T
            survivors = alive[team]
            if len(survivors) != 1:
                continue
            hero = next(iter(survivors))
            if hero in announced:
                continue
            enemies_alive = len(alive[enemy])
            if enemies_alive < min_enemies:
                continue
            announced.add(hero)
            kills_after = sum(
                1
                for k in kills
                if k.tick > kill.tick and k.attacker_steamid == hero and not k.is_teamkill
            )
            won = round_.winner == team if round_.winner != Team.UNKNOWN else kills_after >= enemies_alive
            found.append(
                ClutchSituation(
                    player_steamid=hero,
                    round_number=round_.number,
                    start_tick=kill.tick,
                    enemies_alive=enemies_alive,
                    kills_after=kills_after,
                    won=won,
                )
            )

    # Only successful clutches with at least one kill are interesting.
    return [c for c in found if c.won and c.kills_after >= 1]


def alive_counts_at(analysis: MatchAnalysis, round_number: int, tick: int) -> tuple[int, int]:
    """``(t_alive, ct_alive)`` just before ``tick`` in ``round_number``."""
    roster = _round_roster(analysis.players, analysis.kills, round_number)
    alive: dict[Team, set[str]] = {Team.T: set(), Team.CT: set()}
    for steamid, team in roster.items():
        if team in alive:
            alive[team].add(steamid)
    for kill in sorted((k for k in analysis.kills if k.round_number == round_number), key=lambda k: k.tick):
        if kill.tick >= tick:
            break
        team = kill.victim_team if kill.victim_team in alive else roster.get(kill.victim_steamid or "", Team.UNKNOWN)
        if kill.victim_steamid and team in alive:
            alive[team].discard(kill.victim_steamid)
    return len(alive[Team.T]), len(alive[Team.CT])
