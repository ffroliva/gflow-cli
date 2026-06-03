"""Domain models for Flow Scenes (Add Clip). Frozen + immutable, like dto.py.

Wire times are protobuf-duration strings ("8s", "3.226666870s"). Trim lives in
sceneWorkflowMetadata.startTime/endTime (NOT updateVideoOffset). position = order.
Field paths are HAR-exact (samples/captured/12,14).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


def _seconds_to_duration(value: float) -> str:
    """Serialize seconds as a protobuf Duration string.

    Whole seconds -> "8s"; fractional -> 9 fractional digits "3.226666870s".
    """
    if not math.isfinite(value) or value < 0:
        msg = f"duration must be finite and non-negative, got {value!r}"
        raise ValueError(msg)
    if value == int(value):
        return f"{int(value)}s"
    return f"{value:.9f}s"


def _duration_to_seconds(text: str) -> float:
    """Parse a protobuf Duration string ("8s", "3.2s") back to float seconds."""
    return float(text.rstrip("s")) if text else 0.0


@dataclass(frozen=True)
class SceneWorkflowMetadata:
    position: int
    start_time: float
    end_time: float
    total_duration: float

    def to_wire(self) -> dict[str, Any]:
        return {
            "startTime": _seconds_to_duration(self.start_time),
            "endTime": _seconds_to_duration(self.end_time),
            "position": self.position,
            "totalDuration": _seconds_to_duration(self.total_duration),
        }


@dataclass(frozen=True)
class SceneWorkflow:
    workflow_id: str  # the scene-INSTANCE id (clone), not the source clip id
    metadata: SceneWorkflowMetadata
    media_id: str | None = None  # primaryMediaId of this instance (from read-back)

    def to_wire(self, *, scene_id: str) -> dict[str, Any]:
        return {
            "sceneId": scene_id,
            "workflow": {"name": self.workflow_id},
            "sceneWorkflowMetadata": self.metadata.to_wire(),
        }


def _seconds_to_nanos(value: float) -> str:
    """Serialize seconds as an integer-nanoseconds string (concat `length`)."""
    if not math.isfinite(value) or value < 0:
        msg = f"length must be finite and non-negative, got {value!r}"
        raise ValueError(msg)
    return str(int(round(value * 1_000_000_000)))


@dataclass(frozen=True)
class ConcatInput:
    """One clip in a server-side concatenation request (runVideoFxConcatenation).

    ``media_id`` is the scene-clip's ``primaryMediaId`` (from the create
    response). ``length`` is the clip's full duration; ``start``/``end`` are the
    trim offsets within it (visible length = end - start).
    """

    media_id: str
    length: float
    start: float
    end: float

    def to_wire(self) -> dict[str, Any]:
        return {
            "mediaGenerationId": self.media_id,
            "length": _seconds_to_nanos(self.length),
            "startTimeOffset": _seconds_to_duration(self.start),
            "endTimeOffset": _seconds_to_duration(self.end),
        }


@dataclass(frozen=True)
class Scene:
    scene_id: str
    project_id: str
    workflows: tuple[SceneWorkflow, ...]

    def to_concat_inputs(self) -> tuple[ConcatInput, ...]:
        """Build the ordered concat inputs from this scene's clips.

        Workflows are already position-sorted by the parser. Each clip MUST
        carry a ``media_id`` (``primaryMediaId``) — Flow's concat addresses
        media, not workflow instances. Raises ``ValueError`` if any is missing.
        """
        inputs: list[ConcatInput] = []
        for w in self.workflows:
            if not w.media_id:
                msg = f"clip {w.workflow_id!r} has no media_id; cannot concatenate"
                raise ValueError(msg)
            m = w.metadata
            inputs.append(
                ConcatInput(
                    media_id=w.media_id,
                    length=m.total_duration,
                    start=m.start_time,
                    end=m.end_time if m.end_time > 0 else m.total_duration,
                )
            )
        return tuple(inputs)

    @staticmethod
    def _parse_workflows(entries: list[dict[str, Any]]) -> tuple[SceneWorkflow, ...]:
        # Flow OMITS "position" when 0 and GET reverses order -> default absent
        # position to 0 (NOT the enumerate index) then sort, or positions collide.
        out: list[SceneWorkflow] = []
        for e in entries:
            wf: dict[str, Any] = e.get("workflow", {}) or {}
            meta: dict[str, Any] = e.get("sceneWorkflowMetadata", {}) or {}
            wf_meta: dict[str, Any] = wf.get("metadata", {}) or {}
            out.append(
                SceneWorkflow(
                    workflow_id=str(wf.get("name", "")),
                    media_id=wf_meta.get("primaryMediaId"),
                    metadata=SceneWorkflowMetadata(
                        position=int(meta.get("position", 0)),
                        start_time=_duration_to_seconds(str(meta.get("startTime", "0s"))),
                        end_time=_duration_to_seconds(str(meta.get("endTime", "0s"))),
                        total_duration=_duration_to_seconds(str(meta.get("totalDuration", "0s"))),
                    ),
                )
            )
        out.sort(key=lambda w: w.metadata.position)
        return tuple(out)

    @classmethod
    def from_create_response(cls, data: dict[str, Any], *, project_id: str) -> Scene:
        # fixture 12: sceneId at data["scene"]["sceneId"];
        # sceneWorkflows is a top-level sibling of "scene".
        try:
            return cls(
                scene_id=str(data["scene"]["sceneId"]),
                project_id=project_id,
                workflows=cls._parse_workflows(data.get("sceneWorkflows", []) or []),
            )
        except (KeyError, TypeError) as e:
            msg = f"unexpected create-scene response shape: {e}"
            raise ValueError(msg) from e

    @classmethod
    def from_get_response(cls, data: dict[str, Any], *, scene_id: str, project_id: str) -> Scene:
        # fixture 14: {"sceneWorkflows":[...], "media":[...]}.
        try:
            return cls(
                scene_id=scene_id,
                project_id=project_id,
                workflows=cls._parse_workflows(data.get("sceneWorkflows", []) or []),
            )
        except (KeyError, TypeError) as e:
            msg = f"unexpected get-scene response shape: {e}"
            raise ValueError(msg) from e
