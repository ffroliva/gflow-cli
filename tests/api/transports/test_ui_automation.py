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

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports.ui_automation import (
    FLOW_URL,
    ONBOARDING_SELECTORS,
    UiAutomationTransport,
)
from gflow_cli.errors import ContentPolicyError, WafRejectionError

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
    ctx.add_init_script = AsyncMock()
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
    async def test_generate_images_requires_setup(self) -> None:
        """Calling generate_images() before setup() raises a clear error."""
        t = UiAutomationTransport()
        with pytest.raises(RuntimeError, match="setup\\(\\) must be called"):
            await t.generate_images(project_id="x", request=_req())


# ---------------------------------------------------------------------------
# Unit 3.2 — setup(profile_dir, *, page=None)
# ---------------------------------------------------------------------------


class TestSetup:
    """setup() launches persistent context OR reuses caller-provided page."""

    @pytest.mark.asyncio
    async def test_shared_page_path_does_not_launch_playwright(self, tmp_path: Path) -> None:
        """When page= is provided, the strategy stores it and does NOT
        launch its own Playwright context. _owns_playwright stays False."""
        t = UiAutomationTransport()
        fake_page = MagicMock()
        # Patch async_playwright to confirm it is NOT called on the shared path.
        with patch("gflow_cli.api.transports.ui_automation.async_playwright") as mock_pw:
            await t.setup(tmp_path, page=fake_page)
        mock_pw.assert_not_called()
        assert t._page is fake_page  # type: ignore[attr-defined]
        assert t._owns_playwright is False  # type: ignore[attr-defined]
        assert t._setup_done is True  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_own_context_path_launches_persistent_context(self, tmp_path: Path) -> None:
        """When page=None, strategy launches Playwright with the same args
        the validated smoke uses (headless=False, viewport, locale)."""
        t = UiAutomationTransport()
        ctx = _make_fake_context(pages=[])
        pw_cm, fake_pw = _make_fake_playwright(ctx)
        with patch(
            "gflow_cli.api.transports.ui_automation.async_playwright",
            return_value=pw_cm,
        ):
            await t.setup(tmp_path)
        # launch_persistent_context called once with the expected kwargs.
        fake_pw.chromium.launch_persistent_context.assert_called_once()
        call_kwargs = fake_pw.chromium.launch_persistent_context.call_args.kwargs
        call_args = fake_pw.chromium.launch_persistent_context.call_args.args
        assert call_args[0] == str(tmp_path)
        assert call_kwargs.get("headless") is False
        assert call_kwargs.get("viewport") == {"width": 1280, "height": 800}
        assert call_kwargs.get("locale") == "en-US"
        assert t._owns_playwright is True  # type: ignore[attr-defined]
        assert t._setup_done is True  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_own_context_uses_existing_page_if_present(self, tmp_path: Path) -> None:
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
            await t.setup(tmp_path)
        assert t._page is existing_page  # type: ignore[attr-defined]
        ctx.new_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_own_context_creates_new_page_if_none(self, tmp_path: Path) -> None:
        """If context.pages is empty, strategy calls new_page()."""
        t = UiAutomationTransport()
        ctx = _make_fake_context(pages=[])
        pw_cm, _ = _make_fake_playwright(ctx)
        with patch(
            "gflow_cli.api.transports.ui_automation.async_playwright",
            return_value=pw_cm,
        ):
            await t.setup(tmp_path)
        ctx.new_page.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_navigates_to_flow_url(self, tmp_path: Path) -> None:
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
            await t.setup(tmp_path)
        page.goto.assert_called_once()
        assert page.goto.call_args.args[0] == FLOW_URL

    @pytest.mark.asyncio
    async def test_setup_is_idempotent(self, tmp_path: Path) -> None:
        """Second setup() call is a no-op (no second launch)."""
        t = UiAutomationTransport()
        ctx = _make_fake_context(pages=[])
        pw_cm, fake_pw = _make_fake_playwright(ctx)
        with patch(
            "gflow_cli.api.transports.ui_automation.async_playwright",
            return_value=pw_cm,
        ):
            await t.setup(tmp_path)
            await t.setup(tmp_path)
        # Launched exactly once across the two calls.
        assert fake_pw.chromium.launch_persistent_context.call_count == 1

    @pytest.mark.asyncio
    async def test_setup_swallows_initial_goto_failure(self, tmp_path: Path) -> None:
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
            await t.setup(tmp_path)
        assert t._setup_done is True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Unit 3.3 — _check_logged_in(page)
# ---------------------------------------------------------------------------


def _make_page(
    *,
    url: str,
    signin_count: int = 0,
    raise_on_count: bool = False,
) -> MagicMock:
    """Build a fake Page with the given URL and sign-in button count."""
    page = MagicMock()
    page.url = url
    locator = MagicMock()
    if raise_on_count:
        locator.count = AsyncMock(side_effect=RuntimeError("locator failed"))
    else:
        locator.count = AsyncMock(return_value=signin_count)
    page.locator = MagicMock(return_value=locator)
    return page


class TestCheckLoggedIn:
    """_check_logged_in URL-gates + negates on sign-in CTA presence (pattern G13).

    Authenticated when (a) we're on a labs.google/.../flow URL, (b) not on
    accounts.google.com, and (c) no top-level Sign-in button is visible.
    """

    @pytest.mark.asyncio
    async def test_returns_false_when_on_accounts_google_com(self) -> None:
        t = UiAutomationTransport()
        page = _make_page(url="https://accounts.google.com/v3/signin/identifier")
        assert await t._check_logged_in(page) is False  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_returns_false_when_not_on_flow(self) -> None:
        """Any URL outside labs.google/.../flow is treated as unauthenticated."""
        t = UiAutomationTransport()
        page = _make_page(url="https://example.com/")
        assert await t._check_logged_in(page) is False  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_returns_true_when_in_project_editor(self) -> None:
        """A /project/<uuid> URL means we're already in the editor."""
        t = UiAutomationTransport()
        page = _make_page(
            url="https://labs.google/fx/tools/flow/project/abc-123",
            signin_count=99,  # ignored — /project/ short-circuits.
        )
        assert await t._check_logged_in(page) is True  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_returns_true_on_flow_gallery_without_signin_button(self) -> None:
        t = UiAutomationTransport()
        page = _make_page(
            url="https://labs.google/fx/tools/flow?hl=en",
            signin_count=0,
        )
        assert await t._check_logged_in(page) is True  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_returns_false_on_flow_landing_with_signin_button(self) -> None:
        t = UiAutomationTransport()
        page = _make_page(
            url="https://labs.google/fx/tools/flow",
            signin_count=1,
        )
        assert await t._check_logged_in(page) is False  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_locator_failure_treats_as_no_signin_button(self) -> None:
        """Defensive: if locator.count() raises (DOM transient), treat as 0
        — the URL gate already established Flow context."""
        t = UiAutomationTransport()
        page = _make_page(
            url="https://labs.google/fx/tools/flow?hl=en",
            raise_on_count=True,
        )
        assert await t._check_logged_in(page) is True  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_returns_true_for_localized_flow_paths(self) -> None:
        """`/fx/pt/tools/flow` (Portuguese) and other locale variants still
        satisfy the labs.google + /flow gate."""
        t = UiAutomationTransport()
        page = _make_page(
            url="https://labs.google/fx/pt/tools/flow",
            signin_count=0,
        )
        assert await t._check_logged_in(page) is True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Unit 3.4 — _enter_editor(page, out_dir)
# ---------------------------------------------------------------------------


def _make_editor_page(
    *,
    initial_url: str = "https://labs.google/fx/tools/flow",
    locator_visible: bool = True,
    nav_succeeds: bool = True,
    post_click_url: str = "https://labs.google/fx/tools/flow/project/abc-123",
) -> MagicMock:
    """Build a fake Page that simulates the new-project CTA flow."""
    page = MagicMock()
    page.url = initial_url
    page.wait_for_timeout = AsyncMock()
    page.screenshot = AsyncMock()

    # Locator chain: page.locator(sel).first → wait_for / click
    loc = MagicMock()
    if locator_visible:
        loc.wait_for = AsyncMock()
    else:
        loc.wait_for = AsyncMock(side_effect=RuntimeError("not visible"))

    async def _click() -> None:
        # Successful click simulates Flow navigating to /project/<uuid>.
        if nav_succeeds:
            page.url = post_click_url

    loc.click = AsyncMock(side_effect=_click)

    page_locator = MagicMock()
    page_locator.first = loc
    page.locator = MagicMock(return_value=page_locator)

    async def _wait_for_url(predicate, timeout) -> None:  # noqa: ANN001
        if not nav_succeeds:
            raise RuntimeError("nav did not happen")
        if not predicate(page.url):
            raise RuntimeError("predicate not satisfied")

    page.wait_for_url = AsyncMock(side_effect=_wait_for_url)
    return page


class TestEnterEditor:
    """_enter_editor clicks '+ New project' and waits for /project/ navigation."""

    @pytest.mark.asyncio
    async def test_navigates_to_gallery_when_restored_project_url(self) -> None:
        """Flow's PWA restores the last project URL on browser launch. The
        transport must navigate back to the gallery and create a fresh
        project rather than reusing the restored one (which would
        accumulate images across CLI invocations)."""
        t = UiAutomationTransport()
        page = _make_editor_page(
            initial_url="https://labs.google/fx/tools/flow/project/zzz",
        )
        page.goto = AsyncMock()
        await t._enter_editor(page)  # type: ignore[attr-defined]
        # Gallery navigation happened, then "+ New project" flow ran.
        page.goto.assert_awaited_once()
        assert "tools/flow" in page.goto.call_args.args[0]
        page.wait_for_timeout.assert_called()
        page.locator.assert_called()

    @pytest.mark.asyncio
    async def test_first_selector_works(self) -> None:
        t = UiAutomationTransport()
        page = _make_editor_page()
        await t._enter_editor(page)  # type: ignore[attr-defined]
        # The icon-class (google-symbols) selector — the editor's first
        # declared candidate — must be probed. `_bypass_onboarding` runs
        # first and tries its own selectors, so we check presence anywhere
        # in the call list rather than at index 0.
        all_selectors = [c.args[0] for c in page.locator.call_args_list]
        assert any("google-symbols" in s for s in all_selectors), (
            f"Expected icon-class selector probed; saw {all_selectors}"
        )
        assert "/project/" in page.url

    @pytest.mark.asyncio
    async def test_falls_back_through_selectors_on_visibility_miss(self) -> None:
        """When the first locator's wait_for raises, the loop should try
        the next selector. Use a page where wait_for fails N-1 times then
        succeeds — verify multiple selector probes happen."""
        t = UiAutomationTransport()

        # Build a page where the FIRST two selectors raise on wait_for,
        # the THIRD succeeds + navigates.
        page = MagicMock()
        page.url = "https://labs.google/fx/tools/flow"
        page.wait_for_timeout = AsyncMock()
        page.screenshot = AsyncMock()

        call_count = {"n": 0}

        def _make_loc() -> MagicMock:
            call_count["n"] += 1
            loc = MagicMock()
            if call_count["n"] < 3:
                loc.wait_for = AsyncMock(side_effect=RuntimeError("not visible"))
            else:
                loc.wait_for = AsyncMock()

            async def _click() -> None:
                page.url = "https://labs.google/fx/tools/flow/project/xyz"

            loc.click = AsyncMock(side_effect=_click)
            wrapper = MagicMock()
            wrapper.first = loc
            return wrapper

        page.locator = MagicMock(side_effect=lambda _sel: _make_loc())

        async def _wait_for_url(predicate, timeout) -> None:  # noqa: ANN001
            if not predicate(page.url):
                raise RuntimeError("predicate not satisfied")

        page.wait_for_url = AsyncMock(side_effect=_wait_for_url)

        await t._enter_editor(page)  # type: ignore[attr-defined]
        assert call_count["n"] >= 3
        assert "/project/" in page.url

    @pytest.mark.asyncio
    async def test_all_selectors_fail_raises_runtime_error(self, tmp_path: Path) -> None:
        """Every selector miss + screenshot written + RuntimeError raised."""
        t = UiAutomationTransport()
        page = _make_editor_page(locator_visible=False)
        with pytest.raises(RuntimeError, match="Could not find 'New project'"):
            await t._enter_editor(page, out_dir=tmp_path)  # type: ignore[attr-defined]
        page.screenshot.assert_called_once()
        # Screenshot path created under out_dir.
        called_path = Path(page.screenshot.call_args.kwargs["path"])
        assert called_path.parent == tmp_path

    @pytest.mark.asyncio
    async def test_all_selectors_fail_no_screenshot_when_out_dir_none(self) -> None:
        t = UiAutomationTransport()
        page = _make_editor_page(locator_visible=False)
        with pytest.raises(RuntimeError):
            await t._enter_editor(page)  # type: ignore[attr-defined]
        page.screenshot.assert_not_called()


# ---------------------------------------------------------------------------
# Unit 3.4b — _bypass_onboarding(page)
# ---------------------------------------------------------------------------


def _make_onboarding_page(
    *,
    visible_selectors: set[str] | None = None,
    is_visible_raises: bool = False,
    click_raises: bool = False,
) -> tuple[MagicMock, list[tuple[str, dict[str, object]]]]:
    """Build a fake page for _bypass_onboarding tests.

    Returns ``(page, clicked)`` where ``clicked`` accumulates
    ``(selector, click_kwargs)`` for every locator that received a click.
    A selector reports visible iff it is in ``visible_selectors``.
    """
    visible = visible_selectors or set()
    clicked: list[tuple[str, dict[str, object]]] = []
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()

    def _locator(sel: str) -> MagicMock:
        loc = MagicMock()
        if is_visible_raises:
            loc.is_visible = AsyncMock(side_effect=RuntimeError("probe boom"))
        else:
            loc.is_visible = AsyncMock(return_value=sel in visible)

        async def _click(*_args: object, **kwargs: object) -> None:
            if click_raises:
                raise RuntimeError("click boom")
            clicked.append((sel, dict(kwargs)))

        loc.click = AsyncMock(side_effect=_click)
        wrapper = MagicMock()
        wrapper.first = loc
        return wrapper

    page.locator = MagicMock(side_effect=_locator)
    return page, clicked


class TestBypassOnboarding:
    """_bypass_onboarding force-clicks visible cookie/onboarding CTAs and
    tolerates every miss — the gallery often loads with no interstitial."""

    @pytest.mark.asyncio
    async def test_clicks_visible_onboarding_cta_with_force(self) -> None:
        """A visible onboarding CTA is force-clicked (overlays intercept
        pointer events, so force=True is required) and a settle delay runs."""
        target = ONBOARDING_SELECTORS[0]
        t = UiAutomationTransport()
        page, clicked = _make_onboarding_page(visible_selectors={target})
        await t._bypass_onboarding(page)  # type: ignore[attr-defined]
        assert [sel for sel, _ in clicked] == [target]
        assert clicked[0][1].get("force") is True
        page.wait_for_timeout.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_interstitial_is_a_noop(self) -> None:
        """When nothing matches, _bypass_onboarding clicks nothing and does
        not raise — the common case where the gallery loads clean."""
        t = UiAutomationTransport()
        page, clicked = _make_onboarding_page(visible_selectors=set())
        await t._bypass_onboarding(page)  # type: ignore[attr-defined]
        assert clicked == []

    @pytest.mark.asyncio
    async def test_clicks_every_visible_selector(self) -> None:
        """The loop does not stop at the first hit — a page stacking a
        cookie banner and a 'Get Started' CTA has both dismissed."""
        targets = {ONBOARDING_SELECTORS[0], ONBOARDING_SELECTORS[-1]}
        t = UiAutomationTransport()
        page, clicked = _make_onboarding_page(visible_selectors=targets)
        await t._bypass_onboarding(page)  # type: ignore[attr-defined]
        assert {sel for sel, _ in clicked} == targets

    @pytest.mark.asyncio
    async def test_is_visible_failure_is_swallowed(self) -> None:
        """A transient DOM error from is_visible() must not abort the sweep
        — the selector is skipped and no exception escapes."""
        t = UiAutomationTransport()
        page, clicked = _make_onboarding_page(is_visible_raises=True)
        await t._bypass_onboarding(page)  # type: ignore[attr-defined]  # must not raise
        assert clicked == []

    @pytest.mark.asyncio
    async def test_click_failure_is_swallowed(self) -> None:
        """A click that raises (overlay vanished mid-sweep) is swallowed —
        onboarding bypass is best-effort, never fatal."""
        target = ONBOARDING_SELECTORS[0]
        t = UiAutomationTransport()
        page, _ = _make_onboarding_page(visible_selectors={target}, click_raises=True)
        await t._bypass_onboarding(page)  # type: ignore[attr-defined]  # must not raise


# ---------------------------------------------------------------------------
# Unit 3.5 — _send_prompt(page, prompt_text, out_dir)
# ---------------------------------------------------------------------------


def _make_prompt_page(
    *,
    input_visible: bool = True,
    submit_visible: bool = True,
    url: str = "https://labs.google/fx/tools/flow/project/abc-123",
) -> MagicMock:
    """Build a fake page that simulates input + submit-button visibility.

    Dispatches on selector text so input-selector calls always hit the
    input locator and submit-selector calls always hit the submit
    locator — independent of call order or selector count.
    """
    page = MagicMock()
    page.url = url
    page.wait_for_timeout = AsyncMock()
    page.screenshot = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.keyboard.type = AsyncMock()
    page.keyboard.insert_text = AsyncMock()

    input_loc = MagicMock()
    input_loc.wait_for = (
        AsyncMock() if input_visible else AsyncMock(side_effect=RuntimeError("not visible"))
    )
    input_loc.click = AsyncMock()
    input_wrapper = MagicMock()
    input_wrapper.first = input_loc

    submit_loc = MagicMock()
    submit_loc.wait_for = (
        AsyncMock() if submit_visible else AsyncMock(side_effect=RuntimeError("not visible"))
    )
    submit_loc.click = AsyncMock()
    submit_wrapper = MagicMock()
    submit_wrapper.first = submit_loc

    # Selector fingerprints — input selectors mention slate/contenteditable/
    # textarea/prompt; submit selectors mention arrow_forward/Create.
    def _is_input_selector(sel: str) -> bool:
        lowered = sel.lower()
        return any(k in lowered for k in ("slate", "contenteditable", "textarea", "prompt"))

    def _locator(sel: str) -> MagicMock:
        return input_wrapper if _is_input_selector(sel) else submit_wrapper

    page.locator = MagicMock(side_effect=_locator)
    page._input_loc = input_loc  # type: ignore[attr-defined]
    page._submit_loc = submit_loc  # type: ignore[attr-defined]
    return page


class TestSendPrompt:
    """_send_prompt types into the editor and submits via button or Enter."""

    @pytest.mark.asyncio
    async def test_types_prompt_and_clicks_submit(self) -> None:
        t = UiAutomationTransport()
        page = _make_prompt_page(input_visible=True, submit_visible=True)
        await t._send_prompt(page, "hello world")  # type: ignore[attr-defined]
        page._input_loc.click.assert_called_once()  # type: ignore[attr-defined]
        # Clear (Ctrl+A + Delete) then insert_text (single beforeinput event — near-instant).
        press_calls = [c.args[0] for c in page.keyboard.press.call_args_list]
        assert "Control+A" in press_calls
        assert "Delete" in press_calls
        page.keyboard.insert_text.assert_called_once_with("hello world")
        page._submit_loc.click.assert_called_once()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_falls_back_to_enter_when_no_submit_button(self) -> None:
        t = UiAutomationTransport()
        page = _make_prompt_page(input_visible=True, submit_visible=False)
        await t._send_prompt(page, "no submit btn")  # type: ignore[attr-defined]
        page._submit_loc.click.assert_not_called()  # type: ignore[attr-defined]
        # Enter pressed as fallback.
        press_calls = [c.args[0] for c in page.keyboard.press.call_args_list]
        assert "Enter" in press_calls

    @pytest.mark.asyncio
    async def test_input_not_found_raises_with_screenshot(self, tmp_path: Path) -> None:
        t = UiAutomationTransport()
        page = _make_prompt_page(input_visible=False)
        with pytest.raises(RuntimeError, match="Prompt input not found"):
            await t._send_prompt(  # type: ignore[attr-defined]
                page, "any", out_dir=tmp_path
            )
        page.screenshot.assert_called_once()
        assert Path(page.screenshot.call_args.kwargs["path"]).parent == tmp_path

    @pytest.mark.asyncio
    async def test_input_not_found_no_screenshot_when_out_dir_none(self) -> None:
        t = UiAutomationTransport()
        page = _make_prompt_page(input_visible=False)
        with pytest.raises(RuntimeError):
            await t._send_prompt(page, "x")  # type: ignore[attr-defined]
        page.screenshot.assert_not_called()


# ---------------------------------------------------------------------------
# Unit 3.6 — _capture_batch_response(page, timeout_s, poll_interval_s)
# ---------------------------------------------------------------------------


def _make_listener_page() -> tuple[MagicMock, list]:
    """Build a fake page that captures registered event handlers."""
    page = MagicMock()
    handlers: list = []

    def _on(event: str, cb: object) -> None:
        handlers.append((event, cb))

    page.on = MagicMock(side_effect=_on)
    return page, handlers


def _make_response(
    *,
    url: str = "https://aisandbox-pa.googleapis.com/v1/projects/x/flowMedia:batchGenerateImages",
    status: int = 200,
    body: dict | None = None,
    json_raises: Exception | None = None,
) -> MagicMock:
    """Build a fake Playwright Response object."""
    resp = MagicMock()
    resp.url = url
    resp.status = status
    if json_raises is not None:
        resp.json = AsyncMock(side_effect=json_raises)
    else:
        resp.json = AsyncMock(return_value=body or _flow_200_body())
    return resp


class TestCaptureBatchResponse:
    """Captures the first batchGenerateImages response or times out."""

    @pytest.mark.asyncio
    async def test_returns_first_batch_response(self) -> None:
        page, handlers = _make_listener_page()

        async def _runner() -> list[dict]:
            return await UiAutomationTransport._capture_batch_response(
                page, timeout_s=2.0, poll_interval_s=0.05
            )

        task = asyncio.create_task(_runner())
        # Wait a moment for the handler to be registered.
        await asyncio.sleep(0.05)
        assert handlers and handlers[0][0] == "response"
        await handlers[0][1](_make_response())
        result = await task
        assert result[0]["status"] == 200
        assert "batchGenerateImages" in result[0]["url"]
        assert result[0]["body"]["media"][0]["image"]["generatedImage"]["fifeUrl"]

    @pytest.mark.asyncio
    async def test_ignores_non_batch_responses(self) -> None:
        page, handlers = _make_listener_page()

        async def _runner() -> dict:
            return await UiAutomationTransport._capture_batch_response(
                page, timeout_s=0.5, poll_interval_s=0.05
            )

        task = asyncio.create_task(_runner())
        await asyncio.sleep(0.05)
        # Fire a NON-matching response.
        await handlers[0][1](_make_response(url="https://example.com/other-endpoint"))
        # Should time out since no batch response was captured.
        with pytest.raises(TimeoutError):
            await task

    @pytest.mark.asyncio
    async def test_timeout_raises_when_no_response(self) -> None:
        page, _ = _make_listener_page()
        with pytest.raises(TimeoutError, match="No batchGenerateImages response"):
            await UiAutomationTransport._capture_batch_response(
                page, timeout_s=0.2, poll_interval_s=0.05
            )

    @pytest.mark.asyncio
    async def test_parse_failure_does_not_capture(self) -> None:
        """Response.json() raising means the response is skipped, not crashed."""
        page, handlers = _make_listener_page()

        async def _runner() -> dict:
            return await UiAutomationTransport._capture_batch_response(
                page, timeout_s=0.5, poll_interval_s=0.05
            )

        task = asyncio.create_task(_runner())
        await asyncio.sleep(0.05)
        await handlers[0][1](_make_response(json_raises=ValueError("bad json")))
        with pytest.raises(TimeoutError):
            await task


# ---------------------------------------------------------------------------
# Unit 3.8 — _download(urls, out_dir, cookies)
# ---------------------------------------------------------------------------


class _FakeHttpxResponse:
    def __init__(self, content: bytes, status: int = 200, content_type: str = "image/png") -> None:
        self.content = content
        self.status_code = status
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeHttpxClient:
    """Minimal stand-in for httpx.AsyncClient as an async ctx manager."""

    def __init__(self, responses: dict[str, _FakeHttpxResponse] | None = None) -> None:
        self._responses = responses or {}
        self.requested_urls: list[str] = []

    async def __aenter__(self) -> _FakeHttpxClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def get(self, url: str) -> _FakeHttpxResponse:
        self.requested_urls.append(url)
        if url in self._responses:
            return self._responses[url]
        return _FakeHttpxResponse(b"\x89PNG fake")


class TestDownload:
    """_download fetches URLs via httpx and saves as image_NN.png."""

    @pytest.mark.asyncio
    async def test_saves_single_url(self, tmp_path: Path) -> None:
        client = _FakeHttpxClient()
        with patch("httpx.AsyncClient", return_value=client):
            paths = await UiAutomationTransport._download(
                ["https://lh3.googleusercontent.com/a.png"], tmp_path, cookies={"a": "1"}
            )
        assert len(paths) == 1
        assert paths[0] == tmp_path / "image_00.png"
        assert paths[0].read_bytes() == b"\x89PNG fake"

    @pytest.mark.asyncio
    async def test_saves_multiple_urls_zero_padded(self, tmp_path: Path) -> None:
        client = _FakeHttpxClient()
        with patch("httpx.AsyncClient", return_value=client):
            paths = await UiAutomationTransport._download(
                [
                    "https://lh3.googleusercontent.com/a.png",
                    "https://lh3.googleusercontent.com/b.png",
                ],
                tmp_path,
                cookies={},
            )
        assert [p.name for p in paths] == ["image_00.png", "image_01.png"]

    @pytest.mark.asyncio
    async def test_continues_past_individual_download_failure(self, tmp_path: Path) -> None:
        """One URL fails, the other still downloads. Failure is logged, not raised."""
        bad_resp = _FakeHttpxResponse(b"", status=500)
        good_resp = _FakeHttpxResponse(b"good")
        client = _FakeHttpxClient(
            responses={
                "https://lh3.googleusercontent.com/bad.png": bad_resp,
                "https://lh3.googleusercontent.com/good.png": good_resp,
            }
        )
        with patch("httpx.AsyncClient", return_value=client):
            paths = await UiAutomationTransport._download(
                [
                    "https://lh3.googleusercontent.com/bad.png",
                    "https://lh3.googleusercontent.com/good.png",
                ],
                tmp_path,
                cookies={},
            )
        assert len(paths) == 1
        assert paths[0].name == "image_01.png"
        assert paths[0].read_bytes() == b"good"

    @pytest.mark.asyncio
    async def test_empty_urls_returns_empty_paths(self, tmp_path: Path) -> None:
        client = _FakeHttpxClient()
        with patch("httpx.AsyncClient", return_value=client):
            paths = await UiAutomationTransport._download([], tmp_path, cookies={})
        assert paths == []

    @pytest.mark.asyncio
    async def test_rejects_url_with_disallowed_host(self, tmp_path: Path) -> None:
        """A fifeUrl pointing at a non-Google host is skipped — session
        cookies never reach the foreign domain. This is the H1 security fix."""
        client = _FakeHttpxClient()
        with patch("httpx.AsyncClient", return_value=client):
            paths = await UiAutomationTransport._download(
                ["https://evil.example.com/payload.png"],
                tmp_path,
                cookies={"SAPISID": "secret"},
            )
        # No file written, no HTTP request made.
        assert paths == []
        assert client.requested_urls == []

    @pytest.mark.asyncio
    async def test_rejects_http_scheme(self, tmp_path: Path) -> None:
        """Plain-http URLs are rejected even on allowed hosts — fifeUrl is
        always https in practice."""
        client = _FakeHttpxClient()
        with patch("httpx.AsyncClient", return_value=client):
            paths = await UiAutomationTransport._download(
                ["http://lh3.googleusercontent.com/x.png"],
                tmp_path,
                cookies={},
            )
        assert paths == []
        assert client.requested_urls == []

    @pytest.mark.asyncio
    async def test_accepts_other_google_subdomains(self, tmp_path: Path) -> None:
        """Suffix-match covers any googleusercontent.com / googleapis.com host."""
        client = _FakeHttpxClient()
        with patch("httpx.AsyncClient", return_value=client):
            paths = await UiAutomationTransport._download(
                ["https://aisandbox-pa.googleapis.com/v1/something.png"],
                tmp_path,
                cookies={},
            )
        assert len(paths) == 1
        assert paths[0].name == "image_00.png"


# ---------------------------------------------------------------------------
# Unit 3.9 — generate_images(*, project_id, request)
# ---------------------------------------------------------------------------


def _flow_200_capture(body: dict | None = None) -> dict:
    """Build a captured response dict like _capture_batch_response returns."""
    return {
        "status": 200,
        "url": "https://aisandbox-pa.googleapis.com/v1/projects/p/flowMedia:batchGenerateImages",
        "body": body or _flow_200_body(),
    }


class TestGenerateImages:
    """generate_images orchestrates enter_editor → send_prompt → capture → parse."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_generated_images(self) -> None:
        t = UiAutomationTransport()
        # Pretend setup() already ran with a shared page.
        t._setup_done = True  # type: ignore[attr-defined]
        t._page = MagicMock()  # type: ignore[attr-defined]

        with (
            patch.object(t, "_enter_editor", new=AsyncMock()),
            patch.object(t, "_send_prompt", new=AsyncMock()),
            patch.object(
                t,
                "_await_captured",
                new=AsyncMock(return_value=[_flow_200_capture()]),
            ),
        ):
            images = await t.generate_images(project_id="ignored", request=_req())

        assert len(images) == 1
        assert images[0].fife_url == "https://lh3.googleusercontent.com/abc123"
        assert images[0].seed == 42

    @pytest.mark.asyncio
    async def test_non_200_response_raises(self) -> None:
        t = UiAutomationTransport()
        t._setup_done = True  # type: ignore[attr-defined]
        t._page = MagicMock()  # type: ignore[attr-defined]

        with (
            patch.object(t, "_enter_editor", new=AsyncMock()),
            patch.object(t, "_send_prompt", new=AsyncMock()),
            patch.object(
                t,
                "_await_captured",
                new=AsyncMock(
                    return_value=[
                        {
                            "status": 403,
                            "url": "https://aisandbox-pa.googleapis.com/x",
                            "body": {},
                        }
                    ]
                ),
            ),
            pytest.raises(WafRejectionError),
        ):
            await t.generate_images(project_id="x", request=_req())

    @pytest.mark.asyncio
    async def test_200_with_no_parseable_media_raises(self) -> None:
        t = UiAutomationTransport()
        t._setup_done = True  # type: ignore[attr-defined]
        t._page = MagicMock()  # type: ignore[attr-defined]

        with (
            patch.object(t, "_enter_editor", new=AsyncMock()),
            patch.object(t, "_send_prompt", new=AsyncMock()),
            patch.object(
                t,
                "_await_captured",
                new=AsyncMock(return_value=[_flow_200_capture(body={"media": []})]),
            ),
            pytest.raises(ContentPolicyError),
        ):
            await t.generate_images(project_id="x", request=_req())


# ---------------------------------------------------------------------------
# Unit 3.10 — refresh_auth (no-op)
# ---------------------------------------------------------------------------


class TestRefreshAuth:
    @pytest.mark.asyncio
    async def test_refresh_auth_is_a_noop(self) -> None:
        """refresh_auth() returns without raising — UI auto-refreshes."""
        t = UiAutomationTransport()
        await t.refresh_auth()  # Should not raise.


# ---------------------------------------------------------------------------
# Unit 3.11 — teardown
# ---------------------------------------------------------------------------


class TestTeardown:
    @pytest.mark.asyncio
    async def test_teardown_is_noop_on_shared_page_setup(self) -> None:
        """When _owns_playwright is False, teardown does not close anything."""
        t = UiAutomationTransport()
        # Simulate post-shared-page-setup state.
        t._setup_done = True  # type: ignore[attr-defined]
        t._owns_playwright = False  # type: ignore[attr-defined]
        shared_pw_cm = AsyncMock()
        t._pw_cm = shared_pw_cm  # type: ignore[attr-defined]
        await t.teardown()
        shared_pw_cm.__aexit__.assert_not_called()
        # State reset regardless.
        assert t._setup_done is False  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_teardown_closes_own_context(self) -> None:
        """When _owns_playwright is True, teardown closes ctx + exits pw_cm."""
        t = UiAutomationTransport()
        t._setup_done = True  # type: ignore[attr-defined]
        t._owns_playwright = True  # type: ignore[attr-defined]
        ctx = AsyncMock()
        pw_cm = AsyncMock()
        t._ctx = ctx  # type: ignore[attr-defined]
        t._pw_cm = pw_cm  # type: ignore[attr-defined]
        await t.teardown()
        ctx.close.assert_called_once()
        pw_cm.__aexit__.assert_called_once()
        assert t._setup_done is False  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_teardown_idempotent(self) -> None:
        """Second teardown() is a no-op (already torn down)."""
        t = UiAutomationTransport()
        # No setup() — never opened anything.
        await t.teardown()
        await t.teardown()
        # Doesn't raise.

    @pytest.mark.asyncio
    async def test_teardown_swallows_close_errors(self) -> None:
        """Errors during ctx.close or pw_cm.__aexit__ are logged, not raised."""
        t = UiAutomationTransport()
        t._setup_done = True  # type: ignore[attr-defined]
        t._owns_playwright = True  # type: ignore[attr-defined]
        ctx = MagicMock()
        ctx.close = AsyncMock(side_effect=RuntimeError("ctx close boom"))
        pw_cm = MagicMock()
        pw_cm.__aexit__ = AsyncMock(side_effect=RuntimeError("pw_cm boom"))
        t._ctx = ctx  # type: ignore[attr-defined]
        t._pw_cm = pw_cm  # type: ignore[attr-defined]
        # Should NOT raise.
        await t.teardown()


# ---------------------------------------------------------------------------
# Unit 3.12 — _dismiss_blocking_overlays(page, out_dir)
# ---------------------------------------------------------------------------


def _make_overlay_page(
    *,
    iframe_visible: bool = False,
    close_button_visible: bool = False,
    keyboard_press_raises: bool = False,
) -> MagicMock:
    """Build a fake page for _dismiss_blocking_overlays tests.

    When ``iframe_visible=True`` a changelog iframe selector is visible.
    When ``close_button_visible=True`` a close-button locator is also visible.
    """
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.screenshot = AsyncMock()

    if keyboard_press_raises:
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock(side_effect=RuntimeError("keyboard boom"))
    else:
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()

    # Track click calls per selector for assertions.
    clicked: list[str] = []

    def _locator(sel: str) -> MagicMock:
        loc = MagicMock()
        # Changelog iframe selectors
        is_iframe = "changelogs" in sel
        # Close-button selectors: aria-label close / role=button with close icon
        is_close = any(
            k in sel.lower() for k in ("aria-label", "close", "dialog", "dismiss", "cancel")
        )

        if is_iframe and iframe_visible:
            loc.is_visible = AsyncMock(return_value=True)
        elif is_close and close_button_visible:
            loc.is_visible = AsyncMock(return_value=True)
        else:
            loc.is_visible = AsyncMock(return_value=False)

        async def _click(**kwargs: object) -> None:
            clicked.append(sel)

        loc.click = AsyncMock(side_effect=_click)
        wrapper = MagicMock()
        wrapper.first = loc
        return wrapper

    page.locator = MagicMock(side_effect=_locator)
    page._clicked = clicked  # type: ignore[attr-defined]
    return page


class TestDismissBlockingOverlays:
    """_dismiss_blocking_overlays handles changelog iframes and close buttons.

    Acceptance criteria from issue #26:
    - No overlay → returns False, no clicks, no log noise.
    - Iframe + visible close button → clicked (force=True), returns True.
    - Iframe + NO close button → Escape pressed, returns True (regression test).
    - Iframe + close cascade + Escape both fail → returns False, debug screenshot.
    - Non-changelog iframes are ignored.
    """

    @pytest.mark.asyncio
    async def test_no_overlay_returns_false_and_no_clicks(self) -> None:
        """When no changelog iframe is visible, returns False and makes no clicks."""
        t = UiAutomationTransport()
        page = _make_overlay_page(iframe_visible=False)
        result = await t._dismiss_blocking_overlays(page)  # type: ignore[attr-defined]
        assert result is False
        assert page._clicked == []  # type: ignore[attr-defined]
        page.keyboard.press.assert_not_called()

    @pytest.mark.asyncio
    async def test_iframe_with_close_button_clicks_and_returns_true(self) -> None:
        """A changelog iframe + visible close button → close button clicked
        (force=True) and True returned."""
        t = UiAutomationTransport()
        page = _make_overlay_page(iframe_visible=True, close_button_visible=True)
        result = await t._dismiss_blocking_overlays(page)  # type: ignore[attr-defined]
        assert result is True
        # A close-related selector was clicked.
        assert len(page._clicked) >= 1  # type: ignore[attr-defined]
        page.keyboard.press.assert_not_called()

    @pytest.mark.asyncio
    async def test_iframe_no_close_button_uses_escape_fallback(self) -> None:
        """Regression test (issue #26 AC): iframe present, no close button →
        Escape is pressed as fallback and True is returned."""
        t = UiAutomationTransport()
        page = _make_overlay_page(iframe_visible=True, close_button_visible=False)
        result = await t._dismiss_blocking_overlays(page)  # type: ignore[attr-defined]
        assert result is True
        page.keyboard.press.assert_called_once_with("Escape")
        # No close button was clicked.
        assert page._clicked == []  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_escape_failure_captures_screenshot_and_returns_false(
        self, tmp_path: Path
    ) -> None:
        """If the close cascade AND Escape both fail, a debug screenshot is
        captured and False is returned — diagnostic output is preserved."""
        t = UiAutomationTransport()
        page = _make_overlay_page(
            iframe_visible=True,
            close_button_visible=False,
            keyboard_press_raises=True,
        )
        result = await t._dismiss_blocking_overlays(  # type: ignore[attr-defined]
            page, out_dir=tmp_path
        )
        assert result is False
        page.screenshot.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_changelog_iframes_are_ignored(self) -> None:
        """Selectors that do NOT match changelog iframes produce no dismissal."""
        t = UiAutomationTransport()
        # Page where no changelog iframe is visible but other elements might be.
        page = _make_overlay_page(iframe_visible=False)
        result = await t._dismiss_blocking_overlays(page)  # type: ignore[attr-defined]
        assert result is False


# ---------------------------------------------------------------------------
# Unit 3.13 — _read_displayed_count + _set_count retry logic
# ---------------------------------------------------------------------------


def _make_selected_tab_page(
    selected_text: str | None,
    *,
    visible: bool = True,
) -> MagicMock:
    """Minimal fake page wiring [aria-selected="true"] to ``selected_text``."""
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()

    def _locator(sel: str) -> MagicMock:
        loc = MagicMock()
        wrapper = MagicMock()
        wrapper.first = loc
        tab_vis = selected_text is not None and visible
        loc.is_visible = AsyncMock(return_value=tab_vis)
        loc.text_content = AsyncMock(return_value=selected_text)
        loc.wait_for = AsyncMock()
        loc.click = AsyncMock()
        return wrapper

    page.locator = MagicMock(side_effect=_locator)
    return page


def _make_tablist_page(
    *,
    tab_count: int = 4,
    selected_idx: int = 0,
    selected_text: str = "1",
    readback_after_click: str | None = None,
) -> tuple[MagicMock, list[int]]:
    """Build a fake page with a ``[role="tablist"] [role="tab"]`` locator chain.

    Returns ``(page, clicked_indices)`` where ``clicked_indices`` accumulates
    the nth-index of every ``click()`` call.

    ``selected_text`` is returned by ``[aria-selected="true"]`` before any click.
    ``readback_after_click`` is returned after the first click (simulates DOM update).
    """
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()

    clicked_indices: list[int] = []
    click_count_store: list[int] = [0]

    # Build per-tab mocks (0-indexed).
    tab_mocks: list[MagicMock] = []
    for i in range(tab_count):
        t = MagicMock()
        t.is_visible = AsyncMock(return_value=True)
        t.wait_for = AsyncMock()
        tab_idx = i  # capture

        async def _click_tab(idx: int = tab_idx, **kw: object) -> None:
            clicked_indices.append(idx)
            click_count_store[0] += 1

        t.click = AsyncMock(side_effect=_click_tab)
        tab_mocks.append(t)

    def _locator(sel: str) -> MagicMock:
        wrapper = MagicMock()

        if '[role="tablist"]' in sel:
            # Return a locator whose .count() == tab_count and .nth(i) == tab_mocks[i].
            tab_list_loc = MagicMock()
            tab_list_loc.count = AsyncMock(return_value=tab_count)
            tab_list_loc.first = tab_mocks[0]

            def _nth(i: int) -> MagicMock:
                return tab_mocks[i] if 0 <= i < tab_count else MagicMock()

            tab_list_loc.nth = MagicMock(side_effect=_nth)
            return tab_list_loc  # NOTE: returned directly (not wrapper.first) for locator chains

        if 'aria-selected="true"' in sel:
            loc = MagicMock()
            wrapper.first = loc
            loc.is_visible = AsyncMock(return_value=True)

            async def _text(timeout: int = 500) -> str | None:
                if readback_after_click is not None and click_count_store[0] > 0:
                    return readback_after_click
                return selected_text

            loc.text_content = AsyncMock(side_effect=_text)
            return wrapper

        if "button[aria-selected]" in sel:
            loc = MagicMock()
            wrapper.first = loc
            loc.is_visible = AsyncMock(return_value=True)
            return wrapper

        # Default — invisible/unmatched.
        loc = MagicMock()
        wrapper.first = loc
        loc.is_visible = AsyncMock(return_value=False)
        loc.count = AsyncMock(return_value=0)
        return wrapper

    page.locator = MagicMock(side_effect=_locator)
    page._clicked_indices = clicked_indices  # type: ignore[attr-defined]
    return page, clicked_indices


# ---------------------------------------------------------------------------
# _extract_count_digit — locale-agnostic digit extraction
# ---------------------------------------------------------------------------


class TestExtractCountDigit:
    """_extract_count_digit parses count from any locale rendering."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # English variants
            ("1", 1),
            ("x1", 1),
            ("1x", 1),
            ("1 image", 1),
            ("2 images", 2),
            ("x3", 3),
            ("4x", 4),
            # Portuguese variants (live-observed on denon82)
            ("1 imagem", 1),
            ("2 imagens", 2),
            # No digit — icon-ligature artefact
            ("imageImagem", None),
            ("", None),
        ],
    )
    def test_extract(self, text: str, expected: int | None) -> None:
        from gflow_cli.api.transports.ui_automation import _extract_count_digit

        assert _extract_count_digit(text) == expected


# ---------------------------------------------------------------------------
# Unit 3.13 — _read_displayed_count + _set_count retry logic
# ---------------------------------------------------------------------------


class TestReadDisplayedCount:
    """_read_displayed_count returns digit from aria-selected tab, locale-agnostically."""

    @pytest.mark.asyncio
    async def test_english_x1_returns_1(self) -> None:
        """'x1' → 1 (English locale)."""
        page = _make_selected_tab_page("x1")
        result = await UiAutomationTransport._read_displayed_count(page)  # type: ignore[attr-defined]
        assert result == 1

    @pytest.mark.asyncio
    async def test_english_1x_returns_1(self) -> None:
        """'1x' → 1 (alternate English rendering)."""
        page = _make_selected_tab_page("1x")
        result = await UiAutomationTransport._read_displayed_count(page)  # type: ignore[attr-defined]
        assert result == 1

    @pytest.mark.asyncio
    async def test_english_1_image_returns_1(self) -> None:
        """'1 image' → 1."""
        page = _make_selected_tab_page("1 image")
        result = await UiAutomationTransport._read_displayed_count(page)  # type: ignore[attr-defined]
        assert result == 1

    @pytest.mark.asyncio
    async def test_portuguese_1_imagem_returns_1(self) -> None:
        """'1 imagem' → 1 (Portuguese locale)."""
        page = _make_selected_tab_page("1 imagem")
        result = await UiAutomationTransport._read_displayed_count(page)  # type: ignore[attr-defined]
        assert result == 1

    @pytest.mark.asyncio
    async def test_portuguese_2_imagens_returns_2(self) -> None:
        """'2 imagens' → 2 (Portuguese locale)."""
        page = _make_selected_tab_page("2 imagens")
        result = await UiAutomationTransport._read_displayed_count(page)  # type: ignore[attr-defined]
        assert result == 2

    @pytest.mark.asyncio
    async def test_icon_ligature_artefact_returns_none(self) -> None:
        """'imageImagem' (icon ligature + pt-BR label) → None, not a crash."""
        page = _make_selected_tab_page("imageImagem")
        result = await UiAutomationTransport._read_displayed_count(page)  # type: ignore[attr-defined]
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_tab_not_visible(self) -> None:
        """Returns None when aria-selected tab is invisible and button fallback also absent."""
        page = _make_selected_tab_page(None)
        result = await UiAutomationTransport._read_displayed_count(page)  # type: ignore[attr-defined]
        assert result is None


class TestSetCountRetry:
    """_set_count uses position-based click with digit read-back; raises after 3 failed attempts."""

    @pytest.mark.asyncio
    async def test_returns_early_when_count_already_matches(self) -> None:
        """If the displayed count already matches desired, no tab click is made."""
        # Panel open, selected tab shows "2" → count=2 already, no click.
        page, clicked = _make_tablist_page(selected_text="2")
        with patch.object(
            UiAutomationTransport,
            "_is_settings_panel_open",
            new=AsyncMock(return_value=True),
        ):
            await UiAutomationTransport._set_count(page, 2)  # type: ignore[attr-defined]
        assert clicked == []

    @pytest.mark.asyncio
    async def test_position_click_nth_index(self) -> None:
        """_set_count(3) must call nth(2).click() — 0-indexed position."""
        # Readback returns "3" after click so the method completes normally.
        page, clicked = _make_tablist_page(selected_text="1", readback_after_click="3")
        with (
            patch.object(
                UiAutomationTransport,
                "_is_settings_panel_open",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                UiAutomationTransport,
                "_read_displayed_count",
                new=AsyncMock(side_effect=[1, 3]),  # before=1, after=3
            ),
        ):
            await UiAutomationTransport._set_count(page, 3)  # type: ignore[attr-defined]
        # nth(2) was clicked (count=3 → index 2).
        assert 2 in clicked

    @pytest.mark.asyncio
    async def test_succeeds_when_readback_returns_none(self) -> None:
        """When read-back is None (unrecognised locale text), position click is trusted."""
        page, clicked = _make_tablist_page(selected_text="imageImagem", readback_after_click=None)
        with (
            patch.object(
                UiAutomationTransport,
                "_is_settings_panel_open",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                UiAutomationTransport,
                "_read_displayed_count",
                # before: None (can't parse); after click: None (still can't parse)
                new=AsyncMock(return_value=None),
            ),
        ):
            # Should NOT raise — deterministic position click is trusted when readback=None.
            await UiAutomationTransport._set_count(page, 1)  # type: ignore[attr-defined]
        assert 0 in clicked  # nth(0) was clicked for count=1

    @pytest.mark.asyncio
    async def test_clicks_tab_and_succeeds_on_first_attempt(self) -> None:
        """Happy path: displayed=None, click succeeds, read-back matches."""
        page, clicked = _make_tablist_page(selected_text="1", readback_after_click="1")
        with (
            patch.object(
                UiAutomationTransport,
                "_is_settings_panel_open",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                UiAutomationTransport,
                "_read_displayed_count",
                new=AsyncMock(side_effect=[None, 1]),  # before=None, after=1
            ),
        ):
            await UiAutomationTransport._set_count(page, 1)  # type: ignore[attr-defined]
        assert 0 in clicked  # nth(0) for count=1

    @pytest.mark.asyncio
    async def test_raises_after_three_failed_attempts(self) -> None:
        """If read-back never converges after 3 attempts, RuntimeError is raised."""
        page, clicked = _make_tablist_page(selected_text="2", readback_after_click="2")
        with (
            patch.object(
                UiAutomationTransport,
                "_is_settings_panel_open",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                UiAutomationTransport,
                "_read_displayed_count",
                # Always returns 2 — mismatch when we want 1.
                new=AsyncMock(return_value=2),
            ),
            pytest.raises(RuntimeError, match="_set_count\\(1\\) failed to update Flow UI"),
        ):
            await UiAutomationTransport._set_count(page, 1)  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self) -> None:
        """If read-back returns wrong value on attempt 1, then correct on attempt 2,
        _set_count succeeds without raising."""
        page, clicked = _make_tablist_page(selected_text="2", readback_after_click="1")
        with (
            patch.object(
                UiAutomationTransport,
                "_is_settings_panel_open",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                UiAutomationTransport,
                "_read_displayed_count",
                # Sequence: initial=2, after-click-1=2 (mismatch), after-click-2=1 (match).
                new=AsyncMock(side_effect=[2, 2, 1]),
            ),
        ):
            await UiAutomationTransport._set_count(page, 1)  # type: ignore[attr-defined]
        assert len(clicked) >= 1
