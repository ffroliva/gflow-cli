"""Tests for D.2.4 UiAutomationTransport — TDD.

Mirrors the empirically-validated ``scripts/smoke_worker_style.py`` flow:
Playwright persistent-context launch (internal random CDP port — no public
debug port exposed), UI-driven prompt submission against the Flow editor,
``page.on("response")`` capture of the ``batchGenerateImages`` payload, and
URL extraction from ``media[].image.generatedImage.fifeUrl``.

Each test pins ONE Protocol method's behavior. The implementation lives at
``src/gflow_cli/api/transports/ui_automation.py``.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports.ui_automation import FLOW_URL, UiAutomationTransport

# ---------------------------------------------------------------------------
# Async helpers shared across units
# ---------------------------------------------------------------------------


class _AsyncCtxManager:
    """Minimal async context manager returning `val` on __aenter__."""

    def __init__(self, val: object) -> None:
        self._val = val
        self.exit_calls = 0

    async def __aenter__(self) -> object:
        return self._val

    async def __aexit__(self, *args: object) -> None:
        self.exit_calls += 1


def _make_fake_playwright(fake_ctx: MagicMock) -> tuple[_AsyncCtxManager, MagicMock]:
    """Build a (pw_cm, pw) pair where pw.chromium.launch_persistent_context returns fake_ctx."""
    fake_pw = MagicMock()
    fake_pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_ctx)
    pw_cm = _AsyncCtxManager(fake_pw)
    return pw_cm, fake_pw


def _make_fake_context(*, pages: list[MagicMock] | None = None) -> MagicMock:
    """Build a fake BrowserContext with the given pages list."""
    ctx = MagicMock()
    ctx.pages = pages or []
    new_page = MagicMock()
    new_page.goto = AsyncMock()
    ctx.new_page = AsyncMock(return_value=new_page)
    ctx.close = AsyncMock()
    return ctx


# ---------------------------------------------------------------------------
# Helpers — shared across units
# ---------------------------------------------------------------------------


def _req(prompt: str = "a calm forest at dawn") -> GenerateImageRequest:
    """Build a minimal GenerateImageRequest for ui_automation tests."""
    return GenerateImageRequest(
        prompt=prompt,
        model=Model.NARWHAL,
        aspect=Aspect.PORTRAIT,
        recaptcha_token="not_used_by_ui_automation",
    )


def _flow_200_body() -> dict:
    """Minimal valid batchGenerateImages 200 body (matches real wire shape)."""
    return {
        "media": [
            {
                "name": "projects/proj-uuid/assets/asset-001",
                "workflowId": "wf-001",
                "image": {
                    "generatedImage": {
                        "seed": 42,
                        "prompt": "a calm forest at dawn",
                        "modelNameType": "NARWHAL",
                        "aspectRatio": "IMAGE_ASPECT_RATIO_PORTRAIT",
                        "fifeUrl": "https://lh3.googleusercontent.com/abc123",
                    },
                    "dimensions": {"width": 576, "height": 1024},
                },
            }
        ],
        "workflows": [],
    }


# ---------------------------------------------------------------------------
# Unit 3.1 — Module + Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """UiAutomationTransport must satisfy FlowTransportStrategy."""

    def test_name_attribute_is_ui_automation(self) -> None:
        """Strategy is identified by the registry key 'ui_automation'."""
        assert UiAutomationTransport.name == "ui_automation"

    def test_setup_signature(self) -> None:
        """setup(profile_dir, *, page=None) — matches Protocol § 4.1."""
        sig = inspect.signature(UiAutomationTransport.setup)
        params = sig.parameters
        assert "profile_dir" in params
        assert "page" in params
        assert params["page"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["page"].default is None
        assert inspect.iscoroutinefunction(UiAutomationTransport.setup)

    def test_refresh_auth_signature(self) -> None:
        """refresh_auth() — async, no args beyond self."""
        sig = inspect.signature(UiAutomationTransport.refresh_auth)
        # Only 'self'.
        assert list(sig.parameters) == ["self"]
        assert inspect.iscoroutinefunction(UiAutomationTransport.refresh_auth)

    def test_generate_images_signature(self) -> None:
        """generate_images(*, project_id, request) — async, kwargs-only."""
        sig = inspect.signature(UiAutomationTransport.generate_images)
        params = sig.parameters
        assert "project_id" in params
        assert "request" in params
        assert params["project_id"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["request"].kind == inspect.Parameter.KEYWORD_ONLY
        assert inspect.iscoroutinefunction(UiAutomationTransport.generate_images)

    def test_teardown_signature(self) -> None:
        """teardown() — async, no args beyond self, idempotent."""
        sig = inspect.signature(UiAutomationTransport.teardown)
        assert list(sig.parameters) == ["self"]
        assert inspect.iscoroutinefunction(UiAutomationTransport.teardown)

    @pytest.mark.asyncio
    async def test_unimplemented_methods_still_raise(self) -> None:
        """Methods not yet TDD'd raise NotImplementedError."""
        t = UiAutomationTransport()
        with pytest.raises(NotImplementedError):
            await t.refresh_auth()
        with pytest.raises(NotImplementedError):
            await t.generate_images(project_id="x", request=_req())
        with pytest.raises(NotImplementedError):
            await t.teardown()


# ---------------------------------------------------------------------------
# Unit 3.2 — setup(profile_dir, *, page=None)
# ---------------------------------------------------------------------------


class TestSetup:
    """setup() launches persistent context OR reuses caller-provided page."""

    @pytest.mark.asyncio
    async def test_shared_page_path_does_not_launch_playwright(self) -> None:
        """When page= is provided, the strategy stores it and does NOT
        launch its own Playwright context. _owns_playwright stays False."""
        t = UiAutomationTransport()
        fake_page = MagicMock()
        # Patch async_playwright to confirm it is NOT called on the shared path.
        with patch("gflow_cli.api.transports.ui_automation.async_playwright") as mock_pw:
            await t.setup(Path("/tmp/prof"), page=fake_page)
        mock_pw.assert_not_called()
        assert t._page is fake_page  # type: ignore[attr-defined]
        assert t._owns_playwright is False  # type: ignore[attr-defined]
        assert t._setup_done is True  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_own_context_path_launches_persistent_context(self) -> None:
        """When page=None, strategy launches Playwright with the same args
        the validated smoke uses (headless=False, viewport, locale)."""
        t = UiAutomationTransport()
        ctx = _make_fake_context(pages=[])
        pw_cm, fake_pw = _make_fake_playwright(ctx)
        with patch(
            "gflow_cli.api.transports.ui_automation.async_playwright",
            return_value=pw_cm,
        ):
            await t.setup(Path("/tmp/prof"))
        # launch_persistent_context called once with the expected kwargs.
        fake_pw.chromium.launch_persistent_context.assert_called_once()
        call_kwargs = fake_pw.chromium.launch_persistent_context.call_args.kwargs
        call_args = fake_pw.chromium.launch_persistent_context.call_args.args
        assert call_args[0] == str(Path("/tmp/prof"))
        assert call_kwargs.get("headless") is False
        assert call_kwargs.get("viewport") == {"width": 1280, "height": 800}
        assert call_kwargs.get("locale") == "en-US"
        assert t._owns_playwright is True  # type: ignore[attr-defined]
        assert t._setup_done is True  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_own_context_uses_existing_page_if_present(self) -> None:
        """If context.pages is non-empty, strategy reuses pages[0]."""
        t = UiAutomationTransport()
        existing_page = MagicMock()
        existing_page.goto = AsyncMock()
        ctx = _make_fake_context(pages=[existing_page])
        pw_cm, _ = _make_fake_playwright(ctx)
        with patch(
            "gflow_cli.api.transports.ui_automation.async_playwright",
            return_value=pw_cm,
        ):
            await t.setup(Path("/tmp/prof"))
        assert t._page is existing_page  # type: ignore[attr-defined]
        ctx.new_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_own_context_creates_new_page_if_none(self) -> None:
        """If context.pages is empty, strategy calls new_page()."""
        t = UiAutomationTransport()
        ctx = _make_fake_context(pages=[])
        pw_cm, _ = _make_fake_playwright(ctx)
        with patch(
            "gflow_cli.api.transports.ui_automation.async_playwright",
            return_value=pw_cm,
        ):
            await t.setup(Path("/tmp/prof"))
        ctx.new_page.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_navigates_to_flow_url(self) -> None:
        """After acquiring a page, strategy navigates to FLOW_URL."""
        t = UiAutomationTransport()
        page = MagicMock()
        page.goto = AsyncMock()
        ctx = _make_fake_context(pages=[page])
        pw_cm, _ = _make_fake_playwright(ctx)
        with patch(
            "gflow_cli.api.transports.ui_automation.async_playwright",
            return_value=pw_cm,
        ):
            await t.setup(Path("/tmp/prof"))
        page.goto.assert_called_once()
        assert page.goto.call_args.args[0] == FLOW_URL

    @pytest.mark.asyncio
    async def test_setup_is_idempotent(self) -> None:
        """Second setup() call is a no-op (no second launch)."""
        t = UiAutomationTransport()
        ctx = _make_fake_context(pages=[])
        pw_cm, fake_pw = _make_fake_playwright(ctx)
        with patch(
            "gflow_cli.api.transports.ui_automation.async_playwright",
            return_value=pw_cm,
        ):
            await t.setup(Path("/tmp/prof"))
            await t.setup(Path("/tmp/prof"))
        # Launched exactly once across the two calls.
        assert fake_pw.chromium.launch_persistent_context.call_count == 1

    @pytest.mark.asyncio
    async def test_setup_swallows_initial_goto_failure(self) -> None:
        """page.goto() failure during initial navigation logs but does not
        crash setup — auth/UI flow runs in generate_images and can recover."""
        t = UiAutomationTransport()
        page = MagicMock()
        page.goto = AsyncMock(side_effect=RuntimeError("nav failed"))
        ctx = _make_fake_context(pages=[page])
        pw_cm, _ = _make_fake_playwright(ctx)
        with patch(
            "gflow_cli.api.transports.ui_automation.async_playwright",
            return_value=pw_cm,
        ):
            # Should NOT raise.
            await t.setup(Path("/tmp/prof"))
        assert t._setup_done is True  # type: ignore[attr-defined]
