"""Data models shared across the whole application.

Everything that crosses a module boundary is a dataclass, never a raw dict.
The models are JSON-serialisable so they can be cached on disk, exported and
handed to the render queue after a crash.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Team(enum.IntEnum):
    """CS2 team numbers as they appear in demo data."""

    UNKNOWN = 0
    SPECTATOR = 1
    T = 2
    CT = 3

    @property
    def label(self) -> str:
        return {
            Team.T: "Terrorists",
            Team.CT: "Counter-Terrorists",
            Team.SPECTATOR: "Spectators",
            Team.UNKNOWN: "Unknown",
        }[self]

    @classmethod
    def parse(cls, value: Any) -> Team:
        try:
            return cls(int(value))
        except (TypeError, ValueError):
            text = str(value or "").strip().upper()
            if text in ("T", "TERRORIST", "TERRORISTS"):
                return cls.T
            if text in ("CT", "COUNTER-TERRORIST", "COUNTER-TERRORISTS"):
                return cls.CT
            return cls.UNKNOWN


class HighlightKind(enum.StrEnum):
    """The primary classification of a highlight.

    A highlight has exactly one *kind* (its headline) plus any number of
    :class:`HighlightTag` values that describe its flavour.
    """

    KILL = "KILL"
    MULTI_2K = "2K"
    MULTI_3K = "3K"
    MULTI_4K = "4K"
    ACE = "ACE"
    CLUTCH = "CLUTCH"

    @classmethod
    def for_kill_count(cls, count: int) -> HighlightKind:
        return {
            1: cls.KILL,
            2: cls.MULTI_2K,
            3: cls.MULTI_3K,
            4: cls.MULTI_4K,
        }.get(count, cls.ACE)


class HighlightTag(enum.StrEnum):
    """Optional descriptors. Only ever set when the demo actually proves them."""

    HEADSHOT = "HEADSHOT"
    HEADSHOT_ONLY = "HEADSHOT_ONLY"
    AWP = "AWP"
    SCOUT = "SCOUT"
    DEAGLE = "DEAGLE"
    PISTOL = "PISTOL"
    KNIFE = "KNIFE"
    ZEUS = "ZEUS"
    GRENADE = "GRENADE"
    MOLOTOV = "MOLOTOV"
    WALLBANG = "WALLBANG"
    NOSCOPE = "NOSCOPE"
    THROUGH_SMOKE = "THROUGH_SMOKE"
    BLINDED = "BLINDED"
    JUMPING = "JUMPING"
    LONG_RANGE = "LONG_RANGE"
    CLUTCH = "CLUTCH"
    ACE = "ACE"
    POST_PLANT = "POST_PLANT"
    NINJA_DEFUSE = "NINJA_DEFUSE"


class JobState(enum.StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _encode(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return value.to_dict() if hasattr(value, "to_dict") else dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_encode(v) for v in value]
    return value


class JsonMixin:
    """Tiny dataclass <-> JSON helper; avoids a hard pydantic dependency."""

    def to_dict(self) -> dict[str, Any]:
        return {f.name: _encode(getattr(self, f.name)) for f in dataclasses.fields(self)}  # type: ignore[arg-type]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]):  # noqa: ANN206 - generic factory
        names = {f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
        return cls(**{k: v for k, v in payload.items() if k in names})  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Match data
# ---------------------------------------------------------------------------


@dataclass
class Player(JsonMixin):
    steamid: str
    name: str
    team: Team = Team.UNKNOWN
    #: CS2 spectator slot used by `spec_player <slot>`; see demo/slots.py.
    slot: int | None = None
    #: Raw userinfo user id (`CMsgPlayerInfo.userid & 0xff`).
    user_id: int | None = None
    is_bot: bool = False

    @property
    def account_id(self) -> int | None:
        """Steam 32-bit account id (used by CS:GO-era spectator commands)."""
        try:
            return int(self.steamid) - 76561197960265728
        except (TypeError, ValueError):
            return None

    def __post_init__(self) -> None:
        self.steamid = str(self.steamid)
        self.team = Team.parse(self.team)


@dataclass
class Round(JsonMixin):
    number: int  # 1-based, as humans count rounds
    start_tick: int
    end_tick: int
    freeze_end_tick: int | None = None
    official_end_tick: int | None = None
    winner: Team = Team.UNKNOWN
    reason: str = ""
    bomb_planted_tick: int | None = None
    bomb_exploded_tick: int | None = None
    bomb_defused_tick: int | None = None

    @property
    def play_start_tick(self) -> int:
        """Tick at which players may move (end of freeze time)."""
        return self.freeze_end_tick or self.start_tick

    def contains(self, tick: int) -> bool:
        return self.start_tick <= tick <= (self.official_end_tick or self.end_tick)


@dataclass
class KillEvent(JsonMixin):
    tick: int
    round_number: int
    time: float  # seconds since demo start
    attacker_steamid: str | None
    attacker_name: str | None
    victim_steamid: str | None
    victim_name: str | None
    weapon: str
    headshot: bool = False
    noscope: bool = False
    through_smoke: bool = False
    penetrated: int = 0
    attacker_blinded: bool = False
    assister_steamid: str | None = None
    assister_name: str | None = None
    flash_assist: bool = False
    distance: float = 0.0
    attacker_team: Team = Team.UNKNOWN
    victim_team: Team = Team.UNKNOWN
    attacker_place: str = ""
    victim_place: str = ""
    is_bomb_planted: bool = False
    attacker_in_air: bool = False

    @property
    def is_teamkill(self) -> bool:
        return (
            self.attacker_team != Team.UNKNOWN
            and self.attacker_team == self.victim_team
            and self.attacker_steamid != self.victim_steamid
        )

    @property
    def is_suicide(self) -> bool:
        return self.attacker_steamid is None or self.attacker_steamid == self.victim_steamid


@dataclass
class PlayerStats(JsonMixin):
    steamid: str
    name: str
    team: Team = Team.UNKNOWN
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    headshots: int = 0
    damage: int = 0
    rounds_played: int = 0
    multi_2k: int = 0
    multi_3k: int = 0
    multi_4k: int = 0
    aces: int = 0
    clutches: int = 0

    @property
    def kd(self) -> float:
        return self.kills / self.deaths if self.deaths else float(self.kills)

    @property
    def headshot_percentage(self) -> float:
        return 100.0 * self.headshots / self.kills if self.kills else 0.0

    @property
    def adr(self) -> float | None:
        if not self.rounds_played or not self.damage:
            return None
        return self.damage / self.rounds_played


@dataclass
class MatchAnalysis(JsonMixin):
    """The complete, parser-independent result of analysing one demo."""

    demo_path: str
    demo_sha1: str
    map_name: str
    server_name: str = ""
    tickrate: float = 64.0
    duration_seconds: float = 0.0
    total_ticks: int = 0
    parser_name: str = ""
    parser_version: str = ""
    players: list[Player] = field(default_factory=list)
    rounds: list[Round] = field(default_factory=list)
    kills: list[KillEvent] = field(default_factory=list)
    stats: dict[str, PlayerStats] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    schema_version: int = 2

    # -- lookups ---------------------------------------------------------
    def player(self, steamid: str) -> Player | None:
        return next((p for p in self.players if p.steamid == str(steamid)), None)

    def player_by_name(self, name: str) -> Player | None:
        return next((p for p in self.players if p.name == name), None)

    def round(self, number: int) -> Round | None:
        return next((r for r in self.rounds if r.number == number), None)

    def tick_to_seconds(self, tick: int) -> float:
        return tick / self.tickrate if self.tickrate else 0.0

    def seconds_to_ticks(self, seconds: float) -> int:
        return int(round(seconds * self.tickrate))

    @property
    def match_name(self) -> str:
        """Human friendly folder name, e.g. ``de_mirage_2024-05-01``."""
        import os

        stem = os.path.splitext(os.path.basename(self.demo_path))[0]
        if stem.endswith(".dem"):
            stem = stem[:-4]
        return f"{self.map_name}_{stem}" if self.map_name else stem

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["stats"] = {k: v.to_dict() for k, v in self.stats.items()}
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> MatchAnalysis:
        data = dict(payload)
        data["players"] = [Player.from_dict(p) for p in data.get("players", [])]
        data["rounds"] = [Round.from_dict(r) for r in data.get("rounds", [])]
        data["kills"] = [KillEvent.from_dict(k) for k in data.get("kills", [])]
        data["stats"] = {k: PlayerStats.from_dict(v) for k, v in data.get("stats", {}).items()}
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


# ---------------------------------------------------------------------------
# Highlights
# ---------------------------------------------------------------------------


@dataclass
class Highlight(JsonMixin):
    """One interesting moment: a group of kills by one player in one round."""

    id: str
    kind: HighlightKind
    player_steamid: str
    player_name: str
    round_number: int
    kills: list[KillEvent] = field(default_factory=list)
    tags: list[HighlightTag] = field(default_factory=list)
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    title: str = ""
    #: Clip boundaries in demo ticks, computed by highlights.timing.
    start_tick: int = 0
    end_tick: int = 0
    #: Enemies alive when the sequence started, if it could be determined.
    enemies_alive: int | None = None
    teammates_alive: int | None = None
    clutch_vs: int | None = None
    team: Team = Team.UNKNOWN
    score_t: int | None = None
    score_ct: int | None = None
    #: True when this highlight is the result of merging overlapping clips.
    merged_from: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.kind = HighlightKind(self.kind)
        self.tags = [HighlightTag(t) for t in self.tags]
        self.team = Team.parse(self.team)
        self.kills = [k if isinstance(k, KillEvent) else KillEvent.from_dict(k) for k in self.kills]

    @property
    def kill_count(self) -> int:
        return len(self.kills)

    @property
    def first_kill_tick(self) -> int:
        return min((k.tick for k in self.kills), default=self.start_tick)

    @property
    def last_kill_tick(self) -> int:
        return max((k.tick for k in self.kills), default=self.end_tick)

    @property
    def headshot_count(self) -> int:
        return sum(1 for k in self.kills if k.headshot)

    @property
    def weapons(self) -> list[str]:
        return [k.weapon for k in self.kills]

    def duration_seconds(self, tickrate: float) -> float:
        return max(0.0, (self.end_tick - self.start_tick) / tickrate) if tickrate else 0.0

    def has_tag(self, tag: HighlightTag) -> bool:
        return tag in self.tags

    def matches_query(self, query: str) -> bool:
        """Free-text search across kind, tags, player, weapons and round."""
        query = (query or "").strip().lower()
        if not query:
            return True
        haystack = " ".join(
            [
                self.kind.value,
                self.title,
                self.player_name,
                self.player_steamid,
                f"round {self.round_number}",
                f"r{self.round_number}",
                *[t.value for t in self.tags],
                *self.weapons,
                *(["headshot"] if self.headshot_count else []),
            ]
        ).lower()
        return all(token in haystack for token in query.split())


# ---------------------------------------------------------------------------
# Render / clips
# ---------------------------------------------------------------------------


@dataclass
class VideoSettings(JsonMixin):
    width: int = 1920
    height: int = 1080
    fps: int = 60
    codec: str = "h264"  # h264 | h265
    bitrate_kbps: int = 20000
    encoder: str = "auto"  # auto | cpu | nvenc | amf | qsv
    game_audio: bool = True
    voice_audio: bool = True
    volume: float = 1.0

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass
class Clip(JsonMixin):
    """A finished (or planned) video file for one highlight."""

    highlight_id: str
    player: str
    player_steamid: str
    round: int
    type: str
    score: float
    start_tick: int
    end_tick: int
    map: str
    title: str = ""
    video: str = ""
    duration_seconds: float = 0.0
    tags: list[str] = field(default_factory=list)
    metadata_path: str = ""


@dataclass
class RenderJob(JsonMixin):
    id: str
    highlight: Highlight
    demo_path: str
    output_path: str
    video: VideoSettings = field(default_factory=VideoSettings)
    state: JobState = JobState.PENDING
    progress: float = 0.0
    message: str = ""
    error: str = ""
    attempts: int = 0
    clip: Clip | None = None

    def __post_init__(self) -> None:
        self.state = JobState(self.state)
        if isinstance(self.highlight, dict):
            self.highlight = Highlight.from_dict(self.highlight)
        if isinstance(self.video, dict):
            self.video = VideoSettings.from_dict(self.video)
        if isinstance(self.clip, dict):
            self.clip = Clip.from_dict(self.clip)

    @property
    def label(self) -> str:
        return f"{self.highlight.kind.value} — Round {self.highlight.round_number} — {self.highlight.player_name}"


def dedupe_preserving_order(items: Iterable[Any]) -> list[Any]:
    seen: set[Any] = set()
    out: list[Any] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


#: Sort keys offered by the UI and the CLI.
HIGHLIGHT_SORT_KEYS: dict[str, Any] = {
    "score": lambda h: (h.score, -h.round_number),
    "round": lambda h: (h.round_number, h.start_tick),
    "time": lambda h: h.start_tick,
    "kills": lambda h: (h.kill_count, h.score),
    "player": lambda h: h.player_name.lower(),
}


def sort_highlights(
    highlights: Sequence[Highlight], key: str = "score", descending: bool | None = None
) -> list[Highlight]:
    """Sort highlights by one of :data:`HIGHLIGHT_SORT_KEYS`.

    ``descending`` defaults to the natural direction of the key: "best first"
    for score/kill-count, chronological for round/time, A→Z for player.
    """
    func = HIGHLIGHT_SORT_KEYS.get(key, HIGHLIGHT_SORT_KEYS["score"])
    if descending is None:
        descending = key in ("score", "kills")
    return sorted(highlights, key=func, reverse=bool(descending))
