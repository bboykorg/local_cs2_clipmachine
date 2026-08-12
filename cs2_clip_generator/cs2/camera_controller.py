"""Camera planning.

A clip is a sequence of camera instructions attached to ticks. Today the plan is
simple — lock onto one player for the whole clip — but the shape of the data
supports smooth pans and cuts, so new camera behaviour becomes a new
:class:`CameraController` rather than a rewrite of the render pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..core.models import Highlight
from .player_controller import CameraMode, SpectatorTarget, spectate_commands


@dataclass
class CameraKeyframe:
    """One camera instruction, expressed as console commands at a tick."""

    tick: int
    commands: list[str] = field(default_factory=list)
    label: str = ""


@dataclass
class FreeCameraPose:
    x: float
    y: float
    z: float
    pitch: float = 0.0
    yaw: float = 0.0

    def command(self) -> str:
        return f"spec_goto {self.x:.1f} {self.y:.1f} {self.z:.1f} {self.pitch:.1f} {self.yaw:.1f}"


class CameraController(ABC):
    """Turns a highlight into camera keyframes."""

    name: str = "abstract"

    @abstractmethod
    def plan(
        self,
        highlight: Highlight,
        target: SpectatorTarget | None,
        tickrate: float,
    ) -> list[CameraKeyframe]:
        """Keyframes for the clip, ordered by tick."""


class PlayerPovCameraController(CameraController):
    """Lock first-person POV on the highlight's player.

    The camera is re-asserted at the clip start, shortly after playback settles
    and again just before each kill, because those are the moments when a GOTV
    observer is most likely to have switched the view.
    """

    name = "player_pov"

    def __init__(self, mode: CameraMode = CameraMode.PLAYER_POV, reassert_lead_seconds: float = 1.5) -> None:
        self.mode = mode
        self.reassert_lead_seconds = reassert_lead_seconds

    def plan(
        self, highlight: Highlight, target: SpectatorTarget | None, tickrate: float
    ) -> list[CameraKeyframe]:
        lead = int(round(self.reassert_lead_seconds * (tickrate or 64.0)))
        ticks = {max(1, highlight.start_tick), max(1, highlight.start_tick + max(1, lead // 2))}
        for kill in highlight.kills:
            ticks.add(max(1, kill.tick - lead))
        keyframes = [
            CameraKeyframe(tick=tick, commands=spectate_commands(target, self.mode), label=self.mode.value)
            for tick in sorted(t for t in ticks if t <= highlight.end_tick)
        ]
        return keyframes


class FreeCameraController(CameraController):
    """Static free-camera shots, one pose per keyframe."""

    name = "free_camera"

    def __init__(self, poses: list[tuple[int, FreeCameraPose]] | None = None) -> None:
        self.poses = poses or []

    def plan(
        self, highlight: Highlight, target: SpectatorTarget | None, tickrate: float
    ) -> list[CameraKeyframe]:
        del target, tickrate
        keyframes = [
            CameraKeyframe(tick=max(1, highlight.start_tick), commands=["spec_mode 6"], label="free_camera")
        ]
        for tick, pose in sorted(self.poses):
            keyframes.append(CameraKeyframe(tick=max(1, tick), commands=[pose.command()], label="pose"))
        return keyframes


def get_camera_controller(mode: CameraMode) -> CameraController:
    if mode == CameraMode.FREE_CAMERA:
        return FreeCameraController()
    return PlayerPovCameraController(mode)
