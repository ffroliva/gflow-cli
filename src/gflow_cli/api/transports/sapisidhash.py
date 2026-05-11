"""S3 SapisidhashTransport stub — full impl in Phase B Task B.3."""
from __future__ import annotations

from pathlib import Path

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import GenerateImageRequest


class SapisidhashTransport:
    name = "sapisidhash"

    async def setup(self, profile_dir: Path) -> None:
        raise NotImplementedError

    async def refresh_auth(self) -> None:
        raise NotImplementedError

    async def generate_images(
        self,
        *,
        project_id: str,
        request: GenerateImageRequest,
    ) -> list[GeneratedImage]:
        raise NotImplementedError

    async def teardown(self) -> None:
        pass
