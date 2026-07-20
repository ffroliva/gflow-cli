"""ClassicFlowUiDriver — the classic Flow media-composer layout.

Implements the :class:`FlowUiDriver` protocol methods by delegating to the
existing static helpers on ``UiAutomationTransport`` /
``VideoGenerationMixin``. Awaiting images is NOT a driver method for this
cohort — the transport owns the classic network-capture path (see the note by
``send_prompt``).

**Circular-import discipline:** this module must never import
``ui_automation`` or ``ui_automation_video`` at module load time (the
transport depends on ``drivers``, not the reverse).  All such imports happen
inside the method body (function-level late import), so a bare
``import gflow_cli.api.transports.drivers`` does not trigger a cycle.

The ``send_prompt`` method depends on two *instance* methods of the transport
(``_dismiss_blocking_overlays``, ``_locate_prompt_box``).  The reference is
injected at construction time via the optional ``transport`` parameter and
stored as a weak-referenced attribute (plain reference is fine — the driver
lifetime is scoped to one generation call and GC'd when the transport
releases it).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.async_api import Page

    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.transports.drivers.base import SupportsSendPrompt
    from gflow_cli.api.video import GenerateVideoRequest

log = structlog.get_logger(__name__)


class ClassicFlowUiDriver:
    """Driver for the classic media UI (inline ``crop_*`` settings, network capture)."""

    name = "classic"

    def __init__(self, transport: SupportsSendPrompt | None = None) -> None:
        # ``transport`` is the live transport, injected at construction time
        # (never mutated on late) — ``send_prompt`` delegates to its
        # ``_send_prompt``. Typed as the narrow ``SupportsSendPrompt`` protocol
        # (exactly what the driver uses), which both ``UiAutomationTransport``
        # and its ``VideoGenerationMixin`` half satisfy structurally. The import
        # is ``TYPE_CHECKING``-only, so this stays free of a module-load-time
        # ui_automation import (the transport depends on ``drivers``, not the
        # reverse).
        self._transport = transport

    # ------------------------------------------------------------------
    # FlowUiDriver protocol — mode switching
    # ------------------------------------------------------------------

    async def switch_to_image_mode(self, page: Page, *, out_dir: Path | None = None) -> None:
        """Open the 2-step mode dropdown and switch to Image mode."""
        from gflow_cli.api.transports.ui_automation import (  # noqa: PLC0415
            UiAutomationTransport,
        )

        await UiAutomationTransport._switch_to_image_mode(  # type: ignore[reportPrivateUsage]
            page, out_dir=out_dir
        )

    async def switch_to_video_mode(self, page: Page, *, out_dir: Path | None = None) -> None:
        """Open the 2-step mode dropdown and switch to Video mode."""
        from gflow_cli.api.transports.ui_automation_video import (  # noqa: PLC0415
            VideoGenerationMixin,
        )

        await VideoGenerationMixin._switch_to_video_mode(  # type: ignore[reportPrivateUsage]
            page, out_dir=out_dir
        )

    # ------------------------------------------------------------------
    # FlowUiDriver protocol — settings configuration
    # ------------------------------------------------------------------

    async def configure_image_settings(
        self,
        page: Page,
        request: GenerateImageRequest,
        *,
        out_dir: Path | None = None,
        prompt_idx: int | None = None,
    ) -> None:
        """Apply model / aspect / count for an image generation.

        Derives ``aspect_cli`` / ``count`` / ``model`` from ``request`` in the
        same way :meth:`UiAutomationTransport._generate_images_locked` does,
        then delegates to :meth:`UiAutomationTransport._configure_generation_settings`.
        """
        from gflow_cli.api.transports.ui_automation import (  # noqa: PLC0415
            UiAutomationTransport,
            aspect_cli_from_enum,
        )

        aspect_cli = aspect_cli_from_enum(request.aspect)
        await UiAutomationTransport._configure_generation_settings(  # type: ignore[reportPrivateUsage]
            page,
            aspect_cli,
            request.count,
            model=request.model,
            out_dir=out_dir,
            prompt_idx=prompt_idx,
        )

    async def configure_video_settings(
        self,
        page: Page,
        request: GenerateVideoRequest,
        *,
        out_dir: Path | None = None,
    ) -> None:
        """Apply model / aspect / sub-mode / count / duration for a video generation.

        Replicates the settings block from
        :meth:`VideoGenerationMixin._generate_video_locked` (model → sub-mode →
        aspect → count → duration → Escape-close).  The caller is responsible for
        attaching frame / reference images after this method returns (those actions
        require the panel to be *closed*, which this method ensures via the final
        Escape + settle).
        """
        from gflow_cli.api.transports.ui_automation_video import (  # noqa: PLC0415
            VideoGenerationMixin,
        )
        from gflow_cli.api.video import I2V_DEFAULT_MODEL, Mode  # noqa: PLC0415

        # Mirror the effective_model resolution from _generate_video_locked.
        is_i2v_with_frames = request.mode is Mode.I2V and (
            request.start_image is not None or request.end_image is not None
        )
        effective_model = request.model
        if is_i2v_with_frames and effective_model is None:
            effective_model = I2V_DEFAULT_MODEL

        if effective_model is not None:
            await VideoGenerationMixin._select_video_model(  # type: ignore[reportPrivateUsage]
                page,
                effective_model,
                out_dir=out_dir,
                required=is_i2v_with_frames,
            )
        if request.mode is Mode.I2V:
            await VideoGenerationMixin._switch_video_sub_mode(  # type: ignore[reportPrivateUsage]
                page, "frames", out_dir=out_dir
            )
        elif request.mode is Mode.R2V:
            await VideoGenerationMixin._switch_video_sub_mode(  # type: ignore[reportPrivateUsage]
                page, "references", out_dir=out_dir
            )
        await VideoGenerationMixin._select_video_aspect(  # type: ignore[reportPrivateUsage]
            page, request.aspect
        )
        await VideoGenerationMixin._set_output_count(  # type: ignore[reportPrivateUsage]
            page, request.count
        )
        if request.duration is not None:
            await VideoGenerationMixin._select_video_duration(  # type: ignore[reportPrivateUsage]
                page, request.duration, out_dir=out_dir
            )
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(600)

    # ------------------------------------------------------------------
    # FlowUiDriver protocol — prompt submission
    # ------------------------------------------------------------------

    async def send_prompt(
        self,
        page: Page,
        prompt_text: str,
        *,
        out_dir: Path | None = None,
    ) -> None:
        """Type ``prompt_text`` into Flow's editor and submit.

        Delegates to :meth:`UiAutomationTransport._send_prompt`, which is an
        instance method that calls ``self._dismiss_blocking_overlays`` and
        ``self._locate_prompt_box``.  The transport reference injected at
        construction time is required; raises ``RuntimeError`` when absent.
        """
        if self._transport is None:
            msg = (
                "ClassicFlowUiDriver.send_prompt requires a transport reference "
                "(pass transport=<UiAutomationTransport> at construction time)"
            )
            raise RuntimeError(msg)
        await self._transport._send_prompt(  # type: ignore[reportPrivateUsage]
            page, prompt_text, out_dir
        )

    # NOTE: there is deliberately NO ``await_images`` on this driver. The
    # classic network-capture flow (attach ``page.on('response')`` listener
    # BEFORE the submit click, then drain ``batchGenerateImages``) is owned by
    # ``UiAutomationTransport._generate_images_locked`` — it is entangled with
    # listener/submit-time/entity-backstop state the transport holds. Exposing a
    # driver method that only raises ``NotImplementedError`` advertised a
    # capability the driver refuses; it is gone. Only the agentic driver, which
    # genuinely scrapes the DOM, carries ``submit_images`` / ``await_images``.
