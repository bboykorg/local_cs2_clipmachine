"""Point the camera at a player.

The single most important command in this whole application is::

    spec_mode 1
    spec_player <slot>

``spec_mode 1`` forces first person *before* the target is chosen — with the
order reversed the game frequently ignores ``spec_player`` and leaves the camera
in free-roam, which is how you end up recording a beautiful view of a wall.

CS:GO's convenient ``spec_player_by_accountid`` does not exist in CS2, so the
numeric slot is mandatory. See :mod:`cs2_clip_generator.demo.slots` for how the
slot is recovered from the demo.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from ..core.models import Player


class SpectatorMode(enum.IntEnum):
    """Values accepted by the ``spec_mode`` console variable."""

    FIRST_PERSON = 1
    THIRD_PERSON = 2
    FREE_CAMERA = 6


class CameraMode(enum.StrEnum):
    """What the user picked in the UI."""

    PLAYER_POV = "player_pov"
    THIRD_PERSON = "third_person"
    SPECTATOR = "spectator"
    FREE_CAMERA = "free_camera"

    @property
    def label(self) -> str:
        return {
            CameraMode.PLAYER_POV: "Player POV (first person)",
            CameraMode.THIRD_PERSON: "Third person",
            CameraMode.SPECTATOR: "Spectator (follow)",
            CameraMode.FREE_CAMERA: "Free camera",
        }[self]

    @property
    def spec_mode(self) -> SpectatorMode:
        return {
            CameraMode.PLAYER_POV: SpectatorMode.FIRST_PERSON,
            CameraMode.THIRD_PERSON: SpectatorMode.THIRD_PERSON,
            CameraMode.SPECTATOR: SpectatorMode.THIRD_PERSON,
            CameraMode.FREE_CAMERA: SpectatorMode.FREE_CAMERA,
        }[self]


@dataclass
class SpectatorTarget:
    slot: int
    name: str = ""
    steamid: str = ""

    @classmethod
    def from_player(cls, player: Player) -> SpectatorTarget:
        return cls(slot=int(player.slot or 1), name=player.name, steamid=player.steamid)


def spectate_commands(target: SpectatorTarget | None, mode: CameraMode = CameraMode.PLAYER_POV) -> list[str]:
    """Commands that put the camera where the user asked for.

    The ``spec_mode`` line is always emitted first; see the module docstring.
    """
    commands = [f"spec_mode {int(mode.spec_mode)}"]
    if mode == CameraMode.FREE_CAMERA or target is None:
        return commands
    commands.append(f"spec_player {int(target.slot)}")
    # Re-assert first person: switching target can drop the camera back to the
    # observer's last mode on some CS2 builds.
    if mode == CameraMode.PLAYER_POV:
        commands.append(f"spec_mode {int(SpectatorMode.FIRST_PERSON)}")
    return commands


def follow_commands(target: SpectatorTarget | None, mode: CameraMode, ticks: list[int]) -> list[tuple[int, str]]:
    """``(tick, command)`` pairs that keep the camera glued to one player.

    Re-issuing the spectate commands periodically is not paranoia: observer
    entities in a GOTV demo can steal the camera when the recorded spectator
    switches players, and a highlight that silently jumps to another POV halfway
    through is worse than no clip at all.
    """
    out: list[tuple[int, str]] = []
    for tick in ticks:
        for command in spectate_commands(target, mode):
            out.append((tick, command))
    return out
