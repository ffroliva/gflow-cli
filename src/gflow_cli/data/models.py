from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

JsonObject = dict[str, Any]


class AssetKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class OperationKind(StrEnum):
    UPLOAD_IMAGE = "upload_image"
    T2I = "t2i"
    I2I = "i2i"
    T2V = "t2v"
    I2V = "i2v"
    R2V = "r2v"
    SCENE_CREATE = "scene_create"


class OperationStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OperationAssetRole(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    SEED_START = "seed_start"
    SEED_END = "seed_end"
    REFERENCE = "reference"


@dataclass(frozen=True)
class ProjectRecord:
    id: str
    profile_name: str
    flow_project_id: str
    title: str | None
    source: str
    created_at: str | None = None


@dataclass(frozen=True)
class AssetRecord:
    id: str
    profile_name: str
    flow_project_id: str | None
    flow_media_id: str
    flow_workflow_id: str | None
    flow_media_generation_id: str | None
    kind: AssetKind
    status: str
    model: str | None
    aspect_ratio: str | None
    width: int | None
    height: int | None
    duration_seconds: float | None
    seed: int | None
    metadata_json: JsonObject
    created_at: str | None = None

    @classmethod
    def minimal_image(
        cls,
        *,
        id: str,
        profile_name: str,
        flow_project_id: str | None,
        flow_media_id: str,
    ) -> AssetRecord:
        return cls(
            id=id,
            profile_name=profile_name,
            flow_project_id=flow_project_id,
            flow_media_id=flow_media_id,
            flow_workflow_id=None,
            flow_media_generation_id=None,
            kind=AssetKind.IMAGE,
            status="ready",
            model=None,
            aspect_ratio=None,
            width=None,
            height=None,
            duration_seconds=None,
            seed=None,
            metadata_json={},
        )


@dataclass(frozen=True)
class OperationRecord:
    id: str
    profile_name: str
    flow_project_id: str | None
    command: str | None
    mode: OperationKind
    status: OperationStatus
    flow_operation_id: str | None
    flow_batch_id: str | None
    prompt: str | None
    prompt_hash: str | None
    prompt_redacted: bool
    model: str | None
    aspect_ratio: str | None
    error_type: str | None
    error_detail: str | None
    started_at: str | None = None
    completed_at: str | None = None

    @classmethod
    def minimal(
        cls,
        *,
        id: str,
        profile_name: str,
        flow_project_id: str | None,
        mode: OperationKind,
    ) -> OperationRecord:
        return cls(
            id=id,
            profile_name=profile_name,
            flow_project_id=flow_project_id,
            command=None,
            mode=mode,
            status=OperationStatus.SUCCEEDED,
            flow_operation_id=None,
            flow_batch_id=None,
            prompt=None,
            prompt_hash=None,
            prompt_redacted=False,
            model=None,
            aspect_ratio=None,
            error_type=None,
            error_detail=None,
        )


@dataclass(frozen=True)
class LocalFileRecord:
    id: str
    profile_name: str
    asset_id: str
    path: Path | None  # None for cloud-only files
    media_type: str | None
    bytes: int | None
    sha256: str | None
    storage_provider: str | None = None  # "gcs" | "s3" | None (= local)
    cloud_uri: str | None = None  # gs://bucket/key or s3://bucket/key
    created_at: str | None = None


@dataclass(frozen=True)
class SceneRecord:
    id: str
    profile_name: str
    flow_project_id: str
    flow_scene_id: str
    total_duration: float | None
    source: str
    # Local path of the rendered extended video (server-side concat output),
    # when `gflow scene create --output` was used. None = compose-only.
    output_path: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class SceneClipRecord:
    id: str
    scene_id: str
    position: int
    flow_instance_workflow_id: str
    flow_source_workflow_id: str | None
    flow_media_id: str | None
    start_time: float
    end_time: float
    total_duration: float
    created_at: str | None = None


@dataclass(frozen=True)
class AssetLookup:
    id: str
    profile_name: str
    flow_project_id: str | None
    flow_media_id: str
    kind: AssetKind
    local_files: list[LocalFileRecord]


@dataclass(frozen=True)
class SeedImage:
    profile_name: str
    flow_project_id: str
    flow_media_id: str
    flow_workflow_id: str | None
    kind: AssetKind
    width: int | None
    height: int | None
    local_path: Path | None
    prompt: str | None
    model: str | None
    aspect_ratio: str | None
    created_at: str
