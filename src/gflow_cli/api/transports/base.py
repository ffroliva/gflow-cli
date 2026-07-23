"""FlowTransportStrategy Protocol — the abstraction every strategy implements.

See docs/superpowers/specs/2026-05-11-gflow-cli-b007-transport-strategy-design.md § 4.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.async_api import Page

    from gflow_cli.api.dto import GeneratedImage
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest, VideoResult, VideoStartedCallback


@dataclass(frozen=True, slots=True)
class TransportSetup:
    """Immutable output/storage wiring the client hands a transport.

    Replaces the old client-reaches-into-`transport.__dict__` plumbing (the
    `hasattr(...)`-then-set-`_out_dir`/storage-field pattern): the client now
    builds this typed record and passes it through the public
    :class:`SupportsTransportSetup` seam. A transport that opts in owns its own
    private slots, derived from this record — the client never writes them.
    """

    out_dir: Path | None = None
    """Directory for debug screenshots (#18). ``None`` disables capture."""
    storage_uri: str | None = None
    """Cloud-storage target for video uploads. ``None`` keeps downloads local."""
    output_dir: Path | None = None
    """Local directory video downloads land in (``settings.output_dir``)."""


@runtime_checkable
class SupportsTransportSetup(Protocol):
    """Narrow, structural seam a transport implements to accept output/storage
    configuration publicly, instead of the client writing private attributes.

    ``isinstance(transport, SupportsTransportSetup)`` gates the call, so
    transports that need no such wiring (e.g. the fetch-based strategies) are
    simply left untouched — exactly as the old ``hasattr`` guard behaved.
    """

    def apply_setup(self, config: TransportSetup) -> None: ...


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


@runtime_checkable
class VideoCapableTransport(Protocol):
    """Mixin protocol for transports that support video generation.

    ``isinstance(transport, VideoCapableTransport)`` returns True at runtime
    iff the transport provides ``generate_video`` — used by
    :meth:`FlowApiClient.generate_video` to fail fast with a clear error
    rather than an AttributeError.
    """

    async def generate_video(
        self,
        *,
        request: GenerateVideoRequest,
        project_id: str | None = None,
        out_dir: Path | None,
        poll_timeout_s: float,
        download: bool,
        on_started: VideoStartedCallback | None = None,
    ) -> VideoResult:
        """Drive the Flow editor UI to generate a video and return the result."""
        raise NotImplementedError
