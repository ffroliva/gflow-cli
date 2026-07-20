"""FlowUiDriver — the Strategy protocol every Flow composer layout implements.

Extracts the layout-specific DOM interactions out of ``UiAutomationTransport``
so the classic and agentic cohorts never share selector blocks. The transport
binds one concrete driver per generation (the cohort flaps) via
:func:`gflow_cli.api.transports.drivers.factory.get_ui_driver` and delegates all
composer actions to it.

See docs/superpowers/plans/2026-06-14-agentic-ui-detection/ (Task 1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.async_api import Page

    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest


@runtime_checkable
class FlowUiDriver(Protocol):
    """Layout-specific driver for the Flow editor composer.

    Implementations own only the DOM interactions that differ between cohorts;
    browser-context lifecycle, navigation, and download policy stay in the
    transport. Concrete drivers: :class:`ClassicFlowUiDriver` (classic media UI,
    network-captured responses) and :class:`AgenticFlowUiDriver` (agentic chat
    UI, DOM-scraped responses).

    All methods take the live ``page`` and a best-effort ``out_dir`` for debug
    screenshots; misses on optional settings are non-fatal (logged), while a
    missing prompt box or an unrecoverable mode is a hard error in the concrete
    implementation.

    Awaiting the generated images is deliberately **not** on this protocol: the
    two cohorts observe results by different mechanisms that do not share a
    signature. Classic captures the ``batchGenerateImages`` wire response and
    the transport drains it inline; agentic scrapes the DOM via its own
    :meth:`AgenticFlowUiDriver.submit_images` / ``await_images`` (page-level
    network capture is dead in that cohort). Callers branch on the concrete
    driver type rather than a dishonest shared method.
    """

    name: str  # "classic" | "agentic"

    async def switch_to_image_mode(self, page: Page, *, out_dir: Path | None = None) -> None:
        """Put the composer into image-generation mode."""
        ...

    async def switch_to_video_mode(self, page: Page, *, out_dir: Path | None = None) -> None:
        """Put the composer into video-generation mode."""
        ...

    async def configure_image_settings(
        self,
        page: Page,
        request: GenerateImageRequest,
        *,
        out_dir: Path | None = None,
        prompt_idx: int | None = None,
    ) -> None:
        """Apply model / aspect / count for an image generation.

        Classic drives the ``crop_*`` settings panel; agentic encodes the
        settings into the prompt (the conversational agent resolves them).
        """
        ...

    async def configure_video_settings(
        self,
        page: Page,
        request: GenerateVideoRequest,
        *,
        out_dir: Path | None = None,
    ) -> None:
        """Apply model / aspect / duration / output-count for a video generation."""
        ...

    async def send_prompt(
        self,
        page: Page,
        prompt_text: str,
        *,
        out_dir: Path | None = None,
    ) -> None:
        """Type ``prompt_text`` into the composer and submit it."""
        ...


class SupportsSendPrompt(Protocol):
    """The single transport capability the classic driver depends on.

    ``ClassicFlowUiDriver.send_prompt`` delegates to the transport's
    ``_send_prompt`` (which owns the prompt-box locate + submit-click). Typing
    the injected dependency as *this* narrow structural protocol — rather than
    the whole ``UiAutomationTransport`` — keeps the seam honest (the driver
    asks for exactly what it uses) and lets both ``UiAutomationTransport`` and
    the ``VideoGenerationMixin`` half satisfy it without an import cycle.
    """

    async def _send_prompt(
        self, page: Page, prompt_text: str, out_dir: Path | None = None
    ) -> None: ...
