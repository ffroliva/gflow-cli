"""Provider protocol — implemented by FlowProvider, future OfficialVeoProvider, etc."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from flow_cli.models import Asset, GenerationJob, GenerationRequest


class Provider(Protocol):
    """Backend that can upload assets, kick off Veo generations, and fetch results.

    All async. Implementations may keep internal state (auth session, http client,
    project context) but must expose this uniform interface so commands swap freely.
    """

    name: str

    async def upload_image(self, path: Path) -> Asset:
        """Upload PNG/JPG, return the registered Asset (with UUID)."""
        ...

    async def start_generation(self, req: GenerationRequest) -> GenerationJob:
        """Kick off a Veo I2V generation. Returns a job that's PENDING/RUNNING."""
        ...

    async def get_job(self, job_id: str) -> GenerationJob:
        """Poll job status. Once SUCCEEDED, output_url is populated."""
        ...

    async def download(self, asset_or_url: Asset | str, out_path: Path) -> Path:
        """Download an asset's bytes to out_path. Returns out_path."""
        ...
