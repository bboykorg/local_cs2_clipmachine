"""Tick-accurate command scheduling for CS2 via a JSON actions file.

Source 1 had VDM files: a text file next to the demo telling the engine to run
commands at given ticks. Source 2 dropped them, and nothing native replaced
them. The community answer — used by CS Demo Manager — is a small server plugin
that the game loads, which reads ``<demo>.dem.json`` and executes each command
when the demo reaches its tick.

This module writes exactly that file, in exactly that format, so an already
installed plugin drives our clips as well::

    [
      {"actions": [
        {"tick": 1,    "cmd": "sv_cheats 1"},
        {"tick": 1,    "cmd": "demo_gototick 8100"},
        {"tick": 8100, "cmd": "spec_mode 1"},
        {"tick": 8100, "cmd": "spec_player 4"},
        {"tick": 8164, "cmd": "startmovie clip"},
        {"tick": 8804, "cmd": "endmovie"}
      ]}
    ]

Each element of the top-level array is a *sequence*: one clip. The plugin plays
them in order, which is how several clips come out of a single CS2 session.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..core.logger import get_logger

log = get_logger("cs2")

#: Internal commands understood by the plugin rather than by the engine.
PLUGIN_PAUSE_PLAYBACK = "pause_playback"
PLUGIN_NEXT_SEQUENCE = "next_sequence"


@dataclass
class Action:
    tick: int
    cmd: str
    #: Insertion counter. Sorting by (tick, order) keeps commands scheduled on
    #: the same tick in the order they were added — which matters a great deal:
    #: `spec_mode` must run before `spec_player`, and `endmovie` before the
    #: marker that tells the app the clip is over. Sorting by (tick, cmd) would
    #: silently reorder them alphabetically.
    order: int = 0

    def to_dict(self) -> dict[str, object]:
        return {"cmd": self.cmd, "tick": int(self.tick)}

    @property
    def sort_key(self) -> tuple[int, int]:
        return int(self.tick), int(self.order)


@dataclass
class ActionSequence:
    actions: list[Action] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {"actions": [a.to_dict() for a in self.actions]}


class JsonActionsFile:
    """Builder for ``<demo>.dem.json``."""

    def __init__(self, demo_path: str | os.PathLike[str]) -> None:
        self.demo_path = Path(demo_path)
        self.path = Path(str(self.demo_path) + ".json")
        self.sequences: list[ActionSequence] = []
        self._current = ActionSequence()
        self._counter = 0

    # -- building --------------------------------------------------------
    @staticmethod
    def _valid_tick(tick: int) -> int:
        # Tick 0 is before the plugin is watching; 1 is the earliest useful tick.
        return max(1, int(tick))

    def add(self, tick: int, command: str) -> JsonActionsFile:
        self._current.actions.append(Action(self._valid_tick(tick), command))
        return self

    def add_many(self, tick: int, commands: Iterable[str]) -> JsonActionsFile:
        for command in commands:
            self.add(tick, command)
        return self

    def add_goto_tick(self, at_tick: int, target_tick: int) -> JsonActionsFile:
        return self.add(at_tick, f"demo_gototick {self._valid_tick(target_tick)}")

    def add_spectate(self, tick: int, slot: int, spec_mode: int = 1) -> JsonActionsFile:
        # spec_mode must precede spec_player, otherwise the target is ignored.
        return self.add(tick, f"spec_mode {int(spec_mode)}").add(tick, f"spec_player {int(slot)}")

    def add_pause_playback(self, tick: int) -> JsonActionsFile:
        """Ask the plugin to hold the demo for a moment.

        Used just before a clip starts so the recording does not open on the
        loading-screen tint that follows a long seek.
        """
        return self.add(tick, PLUGIN_PAUSE_PLAYBACK)

    def add_next_sequence(self, tick: int) -> JsonActionsFile:
        return self.add(tick, PLUGIN_NEXT_SEQUENCE)

    def add_disconnect(self, tick: int) -> JsonActionsFile:
        return self.add(tick, "disconnect")

    def add_quit(self, tick: int) -> JsonActionsFile:
        return self.add(tick, "quit")

    def end_sequence(self) -> JsonActionsFile:
        if self._current.actions:
            self._current.actions.sort(key=lambda action: action.sort_key)
            self.sequences.append(self._current)
        self._current = ActionSequence()
        return self

    # -- output ----------------------------------------------------------
    def to_list(self) -> list[dict[str, object]]:
        pending = ActionSequence(list(self._current.actions))
        sequences = list(self.sequences)
        if pending.actions:
            pending.actions.sort(key=lambda action: action.sort_key)
            sequences.append(pending)
        return [sequence.to_dict() for sequence in sequences]

    def to_json(self) -> str:
        return json.dumps(self.to_list(), indent=2)

    def write(self) -> Path:
        self.path.write_text(self.to_json(), encoding="utf-8")
        log.info("wrote actions file %s (%d sequences)", self.path.name, len(self.to_list()))
        return self.path

    def delete(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - permissions
            log.debug("could not delete %s: %s", self.path, exc)


def delete_actions_file(demo_path: str | os.PathLike[str]) -> None:
    """Remove a stale actions file so a manual `playdemo` behaves normally."""
    try:
        Path(str(demo_path) + ".json").unlink(missing_ok=True)
    except OSError:
        pass


def build_clip_sequence(
    actions: JsonActionsFile,
    start_tick: int,
    end_tick: int,
    setup_commands: Sequence[str],
    camera_actions: Sequence[tuple[int, str]],
    record_start_commands: Sequence[str] = (),
    record_end_commands: Sequence[str] = (),
    tickrate: float = 64.0,
    finish_command: str | None = None,
) -> JsonActionsFile:
    """Compose one clip's worth of actions.

    Timeline of a sequence::

        tick 1              setup convars, then seek to just before the clip
        setup_tick          per-clip setup (recorder configuration, camera)
        start_tick - 4      ask the plugin to pause briefly (hide the seek)
        start_tick          start recording
        ...                 camera re-asserted around each kill
        end_tick            stop recording
        end_tick + 1s       next sequence / quit

    The one-second gap between the setup tick and the start tick exists because
    CS2 skips ticks while seeking: commands scheduled on the exact seek target
    can be missed, and a missed ``startmovie`` means a missing clip.
    """
    tickrate = tickrate or 64.0
    one_second = int(round(tickrate))
    setup_tick = max(1, int(start_tick) - one_second)

    for command in setup_commands:
        actions.add(1, command)
    # Seek to one tick before the setup tick: doing the seek and the setup on the
    # same tick can make the engine drop the setup commands.
    actions.add_goto_tick(1, max(1, setup_tick - 1))

    for command in setup_commands:
        actions.add(setup_tick, command)
    for tick, command in sorted(camera_actions):
        actions.add(max(setup_tick, int(tick)), command)

    actions.add_pause_playback(max(1, int(start_tick) - 4))
    for command in record_start_commands:
        actions.add(int(start_tick), command)
    for command in record_end_commands:
        actions.add(int(end_tick), command)
    if finish_command:
        actions.add(int(end_tick) + one_second, finish_command)
    return actions
