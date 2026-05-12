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
    async def test_noop_when_already_in_editor(self) -> None:
        t = UiAutomationTransport()
        page = _make_editor_page(
            initial_url="https://labs.google/fx/tools/flow/project/zzz",
        )
        await t._enter_editor(page)  # type: ignore[attr-defined]
        # No timeout, no locator, no click.
        page.wait_for_timeout.assert_not_called()
        page.locator.assert_not_called()

    @pytest.mark.asyncio
    async def test_first_selector_works(self) -> None:
        t = UiAutomationTransport()
        page = _make_editor_page()
        await t._enter_editor(page)  # type: ignore[attr-defined]
        # locator called at least once with the first (icon-class) selector.
        first_call_sel = page.locator.call_args_list[0].args[0]
        assert "google-symbols" in first_call_sel
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
        # Clear (Ctrl+A + Delete) then type.
        press_calls = [c.args[0] for c in page.keyboard.press.call_args_list]
        assert "Control+A" in press_calls
        assert "Delete" in press_calls
        page.keyboard.type.assert_called_once_with("hello world")
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
