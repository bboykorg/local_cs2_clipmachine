"""Demo parsing.

The application never talks to a parser library directly: it talks to
:class:`DemoParserBackend`. Today there is one implementation, built on
``demoparser2`` (Rust, actively maintained, reads current ``PBDEMS2`` demos).
Adding a second backend — a Go binary, a future pure-Python reader — means
implementing three methods and registering the class.
"""

from __future__ import annotations

import math
import os
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import Any

from ..core.errors import Cancelled, ParserError, parser_missing, unsupported_demo
from ..core.logger import get_logger
from ..core.models import KillEvent, MatchAnalysis, Player, PlayerStats, Round, Team
from ..utils.filesystem import sha1_file
from . import slots as slot_reader

log = get_logger("parser")

ProgressCallback = Callable[[float, str], None]
CancelCallback = Callable[[], bool]


class DemoParserBackend(ABC):
    """A source of :class:`MatchAnalysis` objects."""

    name: str = "abstract"

    @classmethod
    def is_available(cls) -> bool:
        return False

    @classmethod
    def version(cls) -> str:
        return "unknown"

    @abstractmethod
    def parse(
        self,
        path: str,
        progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
    ) -> MatchAnalysis:
        """Parse ``path`` into a :class:`MatchAnalysis`."""

    # -- helpers for subclasses -----------------------------------------
    @staticmethod
    def _report(progress: ProgressCallback | None, value: float, message: str) -> None:
        if progress:
            progress(max(0.0, min(1.0, value)), message)

    @staticmethod
    def _check_cancel(cancel: CancelCallback | None) -> None:
        if cancel and cancel():
            raise Cancelled()


# ---------------------------------------------------------------------------
# Weapon helpers (shared by the highlight detector)
# ---------------------------------------------------------------------------

AWP_WEAPONS = {"awp"}
SCOUT_WEAPONS = {"ssg08"}
DEAGLE_WEAPONS = {"deagle", "revolver"}
PISTOLS = {
    "glock", "usp_silencer", "hkp2000", "p250", "fiveseven", "tec9", "cz75a", "elite", "deagle", "revolver",
}
KNIVES = {
    "knife", "bayonet", "knife_t", "knife_karambit", "knife_m9_bayonet", "knife_butterfly", "knife_flip",
    "knife_gut", "knife_tactical", "knife_falchion", "knife_survival_bowie", "knife_push", "knife_ursus",
    "knife_gypsy_jackknife", "knife_stiletto", "knife_widowmaker", "knife_css", "knife_cord", "knife_canis",
    "knife_outdoor", "knife_skeleton", "knife_kukri",
}
GRENADES = {"hegrenade", "flashbang", "smokegrenade", "decoy"}
FIRE_WEAPONS = {"molotov", "incgrenade", "inferno", "firebomb"}
ZEUS = {"taser"}


def normalise_weapon(weapon: str) -> str:
    weapon = (weapon or "").lower().strip()
    if weapon.startswith("weapon_"):
        weapon = weapon[len("weapon_") :]
    return weapon


def is_knife(weapon: str) -> bool:
    weapon = normalise_weapon(weapon)
    return weapon in KNIVES or weapon.startswith("knife")


def weapon_display_name(weapon: str) -> str:
    weapon = normalise_weapon(weapon)
    pretty = {
        "ak47": "AK47",
        "awp": "AWP",
        "ssg08": "Scout",
        "m4a1": "M4A4",
        "m4a1_silencer": "M4A1-S",
        "usp_silencer": "USP-S",
        "hkp2000": "P2000",
        "deagle": "Deagle",
        "fiveseven": "Five-SeveN",
        "cz75a": "CZ75",
        "tec9": "Tec-9",
        "mp9": "MP9",
        "mp7": "MP7",
        "mp5sd": "MP5-SD",
        "ump45": "UMP-45",
        "p90": "P90",
        "bizon": "PP-Bizon",
        "galilar": "Galil",
        "famas": "FAMAS",
        "sg556": "SG 553",
        "aug": "AUG",
        "g3sg1": "G3SG1",
        "scar20": "SCAR-20",
        "nova": "Nova",
        "xm1014": "XM1014",
        "mag7": "MAG-7",
        "sawedoff": "Sawed-Off",
        "m249": "M249",
        "negev": "Negev",
        "hegrenade": "HE Grenade",
        "molotov": "Molotov",
        "incgrenade": "Incendiary",
        "inferno": "Molotov",
        "taser": "Zeus",
        "glock": "Glock",
        "elite": "Dual Berettas",
        "p250": "P250",
        "revolver": "R8 Revolver",
    }
    if weapon in pretty:
        return pretty[weapon]
    if is_knife(weapon):
        return "Knife"
    return weapon.upper() if len(weapon) <= 4 else weapon.replace("_", " ").title()


# ---------------------------------------------------------------------------
# demoparser2 backend
# ---------------------------------------------------------------------------

#: Extra per-player properties requested for every kill. All of them are real
#: entity properties; anything the demo does not contain comes back as NaN and
#: is then simply not claimed as a fact.
_KILL_PLAYER_PROPS = [
    "team_num",
    "last_place_name",
    "is_airborne",
    "velocity_Z",
    "flash_duration",
]
_KILL_OTHER_PROPS = ["total_rounds_played", "is_bomb_planted", "game_time"]


class Demoparser2Backend(DemoParserBackend):
    """CS2 demo parsing through the ``demoparser2`` Rust extension."""

    name = "demoparser2"

    @classmethod
    def is_available(cls) -> bool:
        try:
            import demoparser2  # noqa: F401
        except Exception:
            return False
        return True

    @classmethod
    def version(cls) -> str:
        try:
            from importlib.metadata import version

            return version("demoparser2")
        except Exception:
            return "unknown"

    # -- public ----------------------------------------------------------
    def parse(
        self,
        path: str,
        progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
    ) -> MatchAnalysis:
        try:
            from demoparser2 import DemoParser as _Parser
        except Exception as exc:  # pragma: no cover - import guard
            raise parser_missing() from exc

        if not os.path.isfile(path):
            raise unsupported_demo(path, "file does not exist")

        self._report(progress, 0.02, "Opening demo")
        parser = _Parser(path)

        try:
            header = parser.parse_header()
        except Exception as exc:
            raise unsupported_demo(path, f"header could not be read: {exc}") from exc
        self._check_cancel(cancel)

        map_name = str(header.get("map_name") or "")
        server_name = str(header.get("server_name") or "")
        log.info("parsing %s (map=%s, parser=%s %s)", os.path.basename(path), map_name, self.name, self.version())

        self._report(progress, 0.06, "Reading player list")
        players, warnings = self._parse_players(parser, path)

        self._report(progress, 0.20, "Reading round structure")
        events = self._parse_events(parser, cancel)
        rounds, tickrate, round_warnings = self._build_rounds(events)
        warnings.extend(round_warnings)

        self._report(progress, 0.55, "Reading kill events")
        kills = self._build_kills(events, players, rounds)

        self._report(progress, 0.80, "Computing statistics")
        stats = self._build_stats(events, players, kills, rounds)

        total_ticks = self._max_tick(events)
        analysis = MatchAnalysis(
            demo_path=os.path.abspath(path),
            demo_sha1=sha1_file(path),
            map_name=map_name,
            server_name=server_name,
            tickrate=tickrate,
            duration_seconds=total_ticks / tickrate if tickrate else 0.0,
            total_ticks=total_ticks,
            parser_name=self.name,
            parser_version=self.version(),
            players=players,
            rounds=rounds,
            kills=kills,
            stats=stats,
            warnings=warnings,
        )
        self._report(progress, 1.0, "Analysis complete")
        log.info(
            "parsed %s: %d players, %d rounds, %d kills, tickrate=%.1f",
            os.path.basename(path),
            len(players),
            len(rounds),
            len(kills),
            tickrate,
        )
        return analysis

    # -- internals -------------------------------------------------------
    def _parse_players(self, parser: Any, path: str) -> tuple[list[Player], list[str]]:
        warnings: list[str] = []
        try:
            frame = parser.parse_player_info()
        except Exception as exc:
            raise ParserError(
                title="The player list could not be read from this demo.",
                reasons=["The demo may be truncated", "The parser does not understand this demo revision"],
                actions=["Try another demo"],
                detail=str(exc),
            ) from exc

        slot_map = slot_reader.read_player_slots(path)
        if not slot_map:
            warnings.append(
                "Spectator slots could not be read from the demo container; POV targeting falls back to "
                "connection order and may be wrong."
            )

        players: list[Player] = []
        rows = frame.to_dict("records") if hasattr(frame, "to_dict") else list(frame)
        for index, row in enumerate(rows):
            steamid = str(row.get("steamid") or "")
            if not steamid or steamid == "0":
                continue
            info = slot_map.get(steamid)
            players.append(
                Player(
                    steamid=steamid,
                    name=str(row.get("name") or f"Player {index + 1}"),
                    team=Team.parse(row.get("team_number")),
                    slot=info.slot if info else None,
                    user_id=(info.user_id & 0xFF) if info else None,
                    is_bot=bool(info.is_bot) if info else False,
                )
            )

        # Fallback slots: connection order is the same order the userinfo table
        # uses, so it is the least-wrong guess available.
        if any(p.slot is None for p in players):
            for index, player in enumerate(players):
                if player.slot is None:
                    player.slot = index + 1
        return players, warnings

    def _parse_events(self, parser: Any, cancel: CancelCallback | None) -> dict[str, list[dict[str, Any]]]:
        """Read every event we need in as few passes as possible."""
        wanted = [
            "player_death",
            "round_start",
            "round_end",
            "round_officially_ended",
            "round_freeze_end",
            "bomb_planted",
            "bomb_exploded",
            "bomb_defused",
            "player_hurt",
        ]
        # NOTE: list_game_events() is unreliable on some demos (e.g. FACEIT):
        # round_start/round_end parse fine but are never listed, so skipping
        # unlisted events silently drops every round. Try every wanted event;
        # a parse failure below simply means "event absent".
        out: dict[str, list[dict[str, Any]]] = {}
        for name in wanted:
            self._check_cancel(cancel)
            player_props = _KILL_PLAYER_PROPS if name == "player_death" else None
            try:
                frame = parser.parse_event(name, player=player_props, other=_KILL_OTHER_PROPS)
            except Exception as exc:
                log.debug("event %s could not be parsed: %s", name, exc)
                out[name] = []
                continue
            out[name] = frame.to_dict("records") if hasattr(frame, "to_dict") else list(frame)
        return out

    # -- rounds ----------------------------------------------------------
    @staticmethod
    def _max_tick(events: dict[str, list[dict[str, Any]]]) -> int:
        ticks = [
            int(row["tick"])
            for rows in events.values()
            for row in rows
            if row.get("tick") is not None and not _is_nan(row.get("tick"))
        ]
        return max(ticks, default=0)

    def _estimate_tickrate(self, events: dict[str, list[dict[str, Any]]]) -> tuple[float, list[str]]:
        """Derive the tickrate from ``tick`` vs ``game_time`` pairs.

        CS2 demo headers do not carry the tickrate, so it is measured: the slope
        of tick over game_time across round boundaries. 64 for matchmaking, 128
        for most third-party servers.
        """
        warnings: list[str] = []
        samples: list[tuple[int, float]] = []
        for name in ("round_start", "round_end", "round_freeze_end", "player_death"):
            for row in events.get(name, []):
                tick, game_time = row.get("tick"), row.get("game_time")
                if tick is None or game_time is None or _is_nan(tick) or _is_nan(game_time):
                    continue
                samples.append((int(tick), float(game_time)))
        samples.sort()
        if len(samples) >= 2:
            (first_tick, first_time), (last_tick, last_time) = samples[0], samples[-1]
            delta_time = last_time - first_time
            delta_tick = last_tick - first_tick
            if delta_time > 1.0 and delta_tick > 0:
                measured = delta_tick / delta_time
                for candidate in (64.0, 128.0, 100.0, 32.0, 60.0):
                    if abs(measured - candidate) / candidate < 0.05:
                        return candidate, warnings
                warnings.append(f"Unusual tickrate measured from the demo: {measured:.2f} ticks/s.")
                return round(measured, 2), warnings
        warnings.append("Tickrate could not be measured from the demo; assuming 64.")
        return 64.0, warnings

    def _build_rounds(self, events: dict[str, list[dict[str, Any]]]) -> tuple[list[Round], float, list[str]]:
        tickrate, warnings = self._estimate_tickrate(events)

        starts: dict[int, int] = {}
        for row in events.get("round_start", []):
            index = _int(row.get("total_rounds_played"), default=None)
            tick = _int(row.get("tick"), default=None)
            if index is None or tick is None:
                continue
            starts.setdefault(index, tick)

        ends: dict[int, dict[str, Any]] = {}
        for row in events.get("round_end", []):
            index = _int(row.get("total_rounds_played"), default=None)
            if index is None:
                continue
            ends[index] = row

        freeze: dict[int, int] = {}
        for row in events.get("round_freeze_end", []):
            index = _int(row.get("total_rounds_played"), default=None)
            tick = _int(row.get("tick"), default=None)
            if index is not None and tick is not None:
                freeze.setdefault(index, tick)

        official: dict[int, int] = {}
        for row in events.get("round_officially_ended", []):
            # This event fires *after* the round it closes, so it reports the
            # index of the next round.
            index = _int(row.get("total_rounds_played"), default=None)
            tick = _int(row.get("tick"), default=None)
            if index is not None and tick is not None:
                official.setdefault(max(0, index - 1), tick)

        indices = sorted(set(starts) | set(ends))
        if not indices:
            warnings.append("No round boundaries were found in the demo; round context will be limited.")
            return [], tickrate, warnings

        rounds: list[Round] = []
        for position, index in enumerate(indices):
            start_tick = starts.get(index)
            end_row = ends.get(index, {})
            end_tick = _int(end_row.get("tick"), default=None)
            if start_tick is None:
                # Fall back to the previous round's end.
                start_tick = rounds[-1].end_tick + 1 if rounds else 0
            if end_tick is None:
                next_index = indices[position + 1] if position + 1 < len(indices) else None
                end_tick = (starts.get(next_index, start_tick) - 1) if next_index is not None else start_tick
            rounds.append(
                Round(
                    number=index + 1,
                    start_tick=start_tick,
                    end_tick=end_tick,
                    freeze_end_tick=freeze.get(index),
                    official_end_tick=official.get(index) or None,
                    winner=Team.parse(end_row.get("winner")),
                    reason=_clean_round_reason(end_row.get("message")),
                )
            )

        # Bomb events are placed by tick, not by ``total_rounds_played``: that
        # counter increments the instant a round *ends*, so anything happening
        # in the post-round window (a spite plant after the last CT dies, a
        # trade kill) would otherwise be filed under the next round.
        for attribute, event_name in (
            ("bomb_planted_tick", "bomb_planted"),
            ("bomb_exploded_tick", "bomb_exploded"),
            ("bomb_defused_tick", "bomb_defused"),
        ):
            for row in events.get(event_name, []):
                tick = _int(row.get("tick"), default=None)
                if tick is None:
                    continue
                number = round_number_for_tick(tick, rounds)
                target = next((r for r in rounds if r.number == number), None)
                if target is not None and getattr(target, attribute) is None:
                    setattr(target, attribute, tick)
        return rounds, tickrate, warnings

    # -- kills -----------------------------------------------------------
    def _build_kills(
        self, events: dict[str, list[dict[str, Any]]], players: Sequence[Player], rounds: Sequence[Round]
    ) -> list[KillEvent]:
        by_steamid = {p.steamid: p for p in players}
        kills: list[KillEvent] = []
        for row in events.get("player_death", []):
            tick = _int(row.get("tick"), default=None)
            if tick is None:
                continue
            attacker_steamid = _steamid(row.get("attacker_steamid"))
            victim_steamid = _steamid(row.get("user_steamid"))
            round_number = round_number_for_tick(tick, rounds, fallback=_int(row.get("total_rounds_played"), None))
            attacker_team = Team.parse(row.get("attacker_team_num") or row.get("attacker_team_name"))
            victim_team = Team.parse(row.get("user_team_num") or row.get("user_team_name"))
            if attacker_team == Team.UNKNOWN and attacker_steamid in by_steamid:
                attacker_team = by_steamid[attacker_steamid].team
            if victim_team == Team.UNKNOWN and victim_steamid in by_steamid:
                victim_team = by_steamid[victim_steamid].team

            kills.append(
                KillEvent(
                    tick=tick,
                    round_number=round_number,
                    time=float(row.get("game_time") or 0.0) if not _is_nan(row.get("game_time")) else 0.0,
                    attacker_steamid=attacker_steamid,
                    attacker_name=_text(row.get("attacker_name")),
                    victim_steamid=victim_steamid,
                    victim_name=_text(row.get("user_name")),
                    weapon=normalise_weapon(str(row.get("weapon") or "")),
                    headshot=_bool(row.get("headshot")),
                    noscope=_bool(row.get("noscope")),
                    through_smoke=_bool(row.get("thrusmoke")),
                    penetrated=_int(row.get("penetrated"), default=0) or 0,
                    attacker_blinded=_bool(row.get("attackerblind")),
                    assister_steamid=_steamid(row.get("assister_steamid")),
                    assister_name=_text(row.get("assister_name")),
                    flash_assist=_bool(row.get("assistedflash")),
                    distance=float(row.get("distance") or 0.0) if not _is_nan(row.get("distance")) else 0.0,
                    attacker_team=attacker_team,
                    victim_team=victim_team,
                    attacker_place=_text(row.get("attacker_last_place_name")) or "",
                    victim_place=_text(row.get("user_last_place_name")) or "",
                    is_bomb_planted=_bool(row.get("is_bomb_planted")),
                    attacker_in_air=_bool(row.get("attacker_is_airborne")),
                )
            )
        kills.sort(key=lambda k: k.tick)
        return kills

    # -- statistics ------------------------------------------------------
    def _build_stats(
        self,
        events: dict[str, list[dict[str, Any]]],
        players: Sequence[Player],
        kills: Sequence[KillEvent],
        rounds: Sequence[Round],
    ) -> dict[str, PlayerStats]:
        stats = {p.steamid: PlayerStats(steamid=p.steamid, name=p.name, team=p.team) for p in players}
        rounds_played = len(rounds)

        for kill in kills:
            if kill.attacker_steamid in stats and not kill.is_teamkill and not kill.is_suicide:
                entry = stats[kill.attacker_steamid]
                entry.kills += 1
                if kill.headshot:
                    entry.headshots += 1
            if kill.victim_steamid in stats:
                stats[kill.victim_steamid].deaths += 1
            if kill.assister_steamid in stats:
                stats[kill.assister_steamid].assists += 1

        for row in events.get("player_hurt", []):
            attacker = _steamid(row.get("attacker_steamid"))
            if attacker not in stats:
                continue
            victim_team = Team.parse(row.get("user_team_num"))
            attacker_team = Team.parse(row.get("attacker_team_num"))
            if attacker_team != Team.UNKNOWN and attacker_team == victim_team:
                continue
            damage = _int(row.get("dmg_health"), default=0) or 0
            # CS2 reports the raw damage, which can exceed the victim's health.
            health = _int(row.get("health"), default=None)
            if health is not None and health <= 0:
                damage = min(damage, 100)
            stats[attacker].damage += max(0, damage)

        for entry in stats.values():
            entry.rounds_played = rounds_played
        return stats


# ---------------------------------------------------------------------------
# Tick → round mapping
# ---------------------------------------------------------------------------


def round_number_for_tick(tick: int, rounds: Sequence[Round], fallback: int | None = None) -> int:
    """Which round does ``tick`` belong to? ``0`` means warmup.

    A tick belongs to the last round that had already started, so kills and
    plants that happen after ``round_end`` but before the next ``round_start``
    stay with the round the players were actually playing.
    """
    number = 0
    for round_ in rounds:
        if round_.start_tick <= tick:
            number = round_.number
        else:
            break
    if number == 0 and fallback is not None and rounds:
        # Before the first round_start: warmup, unless the demo says otherwise.
        return fallback + 1 if fallback > 0 else 0
    return number


# ---------------------------------------------------------------------------
# Value coercion helpers — demo data is full of NaN and mixed types
# ---------------------------------------------------------------------------


def _is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _int(value: Any, default: int | None = 0) -> int | None:
    if value is None or _is_nan(value):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    if value is None or _is_nan(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def _text(value: Any) -> str | None:
    if value is None or _is_nan(value):
        return None
    text = str(value)
    return text if text and text.lower() != "nan" else None


def _steamid(value: Any) -> str | None:
    text = _text(value)
    if not text or text == "0":
        return None
    return text


def _clean_round_reason(message: Any) -> str:
    text = _text(message) or ""
    return text.replace("#SFUI_Notice_", "").replace("_", " ").strip()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

BACKENDS: list[type[DemoParserBackend]] = [Demoparser2Backend]


def available_backends() -> list[type[DemoParserBackend]]:
    return [backend for backend in BACKENDS if backend.is_available()]


def get_parser(preferred: str | None = None) -> DemoParserBackend:
    """Return the best available parser backend."""
    backends = available_backends()
    if not backends:
        raise parser_missing()
    if preferred:
        for backend in backends:
            if backend.name == preferred:
                return backend()
    return backends[0]()
