"""Typed DTOs for Flow API requests/responses.

All frozen dataclasses — once constructed, instances are immutable and
hashable. Parsers (`*.from_response`) defensively read JSON dicts and
raise `ValueError` on missing/malformed fields rather than letting
KeyErrors leak.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProjectInfo:
    """A Flow project — owns assets, jobs, library entries."""

    project_id: str
    title: str

    @classmethod
    def from_create_response(cls, data: dict[str, Any]) -> ProjectInfo:
        """Parse `POST .../project.createProject` JSON.

        Wire shape:
          {result: {data: {json: {result: {projectId, projectInfo: {projectTitle}}}}}}
        """
        try:
            inner = data["result"]["data"]["json"]["result"]
            return cls(
                project_id=inner["projectId"],
                title=inner["projectInfo"]["projectTitle"],
            )
        except (KeyError, TypeError) as e:
            raise ValueError(f"unexpected createProject response shape: {e}") from e


@dataclass(frozen=True)
class AssetInfo:
    """A media asset (image or video) registered in a Flow project."""

    name: str  # asset UUID
    project_id: str
    workflow_id: str
    display_name: str
    width: int
    height: int

    @classmethod
    def from_upload_response(cls, data: dict[str, Any]) -> AssetInfo:
        """Parse `POST /v1/flow/uploadImage` JSON.

        Wire shape:
          {media: {name, projectId, workflowId, image: {dimensions: {width, height}}, ...},
           workflow: {metadata: {displayName}, ...}}
        """
        try:
            media = data["media"]
            dims = media.get("image", {}).get("dimensions", {})
            return cls(
                name=media["name"],
                project_id=media["projectId"],
                workflow_id=media["workflowId"],
                display_name=data.get("workflow", {}).get("metadata", {}).get("displayName", ""),
                width=int(dims.get("width", 0)),
                height=int(dims.get("height", 0)),
            )
        except (KeyError, TypeError) as e:
            raise ValueError(f"unexpected uploadImage response shape: {e}") from e


@dataclass(frozen=True)
class VideoStatus:
    """Snapshot of one in-flight video generation."""

    media_name: str  # asset UUID, used to track the job
    project_id: str
    status: str  # MEDIA_GENERATION_STATUS_PENDING|RUNNING|COMPLETED|FAILED
    operation_name: str | None  # set once generation has started
    workflow_id: str | None

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            "MEDIA_GENERATION_STATUS_COMPLETED",
            "MEDIA_GENERATION_STATUS_FAILED",
        )

    @property
    def succeeded(self) -> bool:
        return self.status == "MEDIA_GENERATION_STATUS_COMPLETED"

    @classmethod
    def from_check_status_item(cls, item: dict[str, Any]) -> VideoStatus:
        """Parse one element of the `media[]` array in the check-status response."""
        try:
            return cls(
                media_name=item["name"],
                project_id=item["projectId"],
                status=item["mediaMetadata"]["mediaStatus"]["mediaGenerationStatus"],
                operation_name=item.get("video", {}).get("operation", {}).get("name"),
                workflow_id=item.get("workflowId"),
            )
        except (KeyError, TypeError) as e:
            raise ValueError(f"unexpected check-status item shape: {e}") from e


@dataclass(frozen=True)
class VideoOperation:
    """Reference returned when a generation request is enqueued."""

    media_name: str  # asset UUID — pass to get_video_status() to poll
    project_id: str
    operation_name: str
    workflow_id: str

    @classmethod
    def from_generate_response(cls, data: dict[str, Any]) -> VideoOperation:
        """Parse `POST /v1/video:batchAsyncGenerateVideoText` JSON.

        Wire shape (one operation):
            {operations: [{operation: {name}, ...}],
             media: [{name, projectId, workflowId, ...}],
             workflows: [{name, projectId, ...}]}
        """
        try:
            op = data["operations"][0]["operation"]["name"]
            media = data["media"][0]
            return cls(
                media_name=media["name"],
                project_id=media["projectId"],
                operation_name=op,
                workflow_id=media["workflowId"],
            )
        except (KeyError, IndexError, TypeError) as e:
            raise ValueError(f"unexpected generateVideo response shape: {e}") from e
