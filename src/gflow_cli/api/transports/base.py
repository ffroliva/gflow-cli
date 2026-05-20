"""FlowTransportStrategy Protocol — the abstraction every strategy implements.

See docs/superpowers/specs/2026-05-11-gflow-cli-b007-transport-strategy-design.md § 4.1.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from playwright.async_api import Page

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import GenerateImageRequest


class FlowTransportStrategy(Protocol):
    """Pluggable transport for Flow API calls.

    Implementations send the actual HTTP requests. The abstraction owns no
    state about which strategy is active — it depends only on this Protocol.

    Lifecycle: setup() → (generate_images()|refresh_auth())* → teardown()
    """

    name: str  # e.g. "evaluate_fetch", "bearer", "sapisidhash"

    async def setup(self, profile_dir: Path, *, page: Page | None = None) -> None:
        """Initialize. Idempotent. Strategy may launch playwright, capture
        a Bearer token, derive SAPISIDHASH inputs from cookies, etc.

        The optional ``page`` kwarg is for the S1 shared-page fix (spec § 5.4.4):
        FlowApiClient passes its already-open Page so EvaluateFetchTransport can
        reuse it instead of launching a second Playwright context against the same
        profile dir (which would conflict on the Chromium lockfile). S2 and S3
        accept and ignore this kwarg for Protocol conformance.
        """
        ...

    async def refresh_auth(self) -> None:
        """Refresh auth state without a full setup teardown. Called by the
        strategy on AuthExpired from the API, or proactively if the strategy
        knows its credential is about to expire. MUST raise AuthExpiredError
        if refresh fails — never silently swallow."""
        ...

    async def generate_images(
        self,
        *,
        project_id: str | None,
        request: GenerateImageRequest,
    ) -> list[GeneratedImage]:
        """Send batchGenerateImages. recaptcha_token lives on `request` —
        keeping the Protocol media-agnostic across image and video generation."""
        ...

    async def teardown(self) -> None:
        """Release resources. Idempotent. Safe to call multiple times."""
        ...
