"""Domain models used by all providers."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class Asset:
    """A media asset (image or video) stored in Flow's library."""
    uuid: str
    media_url: str
    kind: str  # "image" | "video"


@dataclass(frozen=True)
class GenerationJob:
    """A Veo generation job."""
    job_id: str
    status: JobStatus
    start_asset_uuid: Optional[str] = None
    end_asset_uuid: Optional[str] = None
    motion_prompt: str = ""
    output_url: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class GenerationRequest:
    """Input for kicking off a Veo I2V generation."""
    start_image: Path
    motion_prompt: str
    aspect: str = "9:16"
    end_image: Optional[Path] = None
