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
import json
from pathlib import Path
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.image import AgentInstruction, Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports.ui_automation import (
    _COUNT_TAB_TEXT_RE,  # noqa: PLC2701
    _ONBOARDING_STRUCTURAL_SELECTORS,  # noqa: PLC2701
    _ONBOARDING_TEXT_SELECTORS,  # noqa: PLC2701
    FLOW_URL,
    IMAGE_MODEL_OPTION_SELECTORS,
    NEW_PROJECT_SELECTORS,
    ONBOARDING_SELECTORS,
    SUBMIT_BUTTON_SELECTORS,
    UiAutomationTransport,
    _count_tabs_locator,  # noqa: PLC2701
    _summarize_batch_request_body,  # noqa: PLC2701
)
from gflow_cli.api.transports.ui_automation_video import (
    COMPOSER_AGENT_TOGGLE_SELECTOR,
    VideoGenerationMixin,
    zip_entity_refs,
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


def _record_lease_events(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    """Patch ProfileLease.acquire/release to append to ``events`` — no real locks."""
    from gflow_cli.profile_lease import ProfileLease

    def acq(self: ProfileLease) -> ProfileLease:
        events.append("acquire")
        return self

    def rel(self: ProfileLease) -> None:
        events.append("release")

    monkeypatch.setattr(ProfileLease, "acquire", acq)
    monkeypatch.setattr(ProfileLease, "release", rel)


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
        assert call_kwargs.get("viewport") == {"width": 1920, "height": 1080}
        assert call_kwargs.get("locale") == "en-US"
        assert t._owns_playwright is True  # type: ignore[attr-defined]
        assert t._setup_done is True  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_own_context_acquires_and_releases_profile_lease(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Standalone path (page=None): acquire the profile lease BEFORE the
        persistent context launches and release it AFTER teardown (D3)."""
        events: list[str] = []
        _record_lease_events(monkeypatch, events)
        t = UiAutomationTransport()
        ctx = _make_fake_context(pages=[])
        pw_cm, fake_pw = _make_fake_playwright(ctx)

        async def _launch(*_a: object, **_k: object) -> MagicMock:
            events.append("launch")
            return ctx

        fake_pw.chromium.launch_persistent_context = AsyncMock(side_effect=_launch)
        with patch(
            "gflow_cli.api.transports.ui_automation.async_playwright",
            return_value=pw_cm,
        ):
            await t.setup(tmp_path)
        assert events == ["acquire", "launch"]  # acquire strictly before launch
        await t.teardown()
        assert events == ["acquire", "launch", "release"]  # released after close

    @pytest.mark.asyncio
    async def test_shared_page_path_acquires_no_lease(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Shared-page path (caller owns the context): the transport must NOT
        take a second lease (D3 — no double-acquire)."""
        events: list[str] = []
        _record_lease_events(monkeypatch, events)
        t = UiAutomationTransport()
        await t.setup(tmp_path, page=MagicMock())
        await t.teardown()
        assert events == []

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

    # --- cascade-order invariants -------------------------------------------

    def test_structural_selectors_precede_text_selectors(self) -> None:
        """ONBOARDING_SELECTORS must lead with structural/ARIA tiers before
        any text-based selector — validates the cascade ordering."""
        first_text_idx = next(i for i, s in enumerate(ONBOARDING_SELECTORS) if ":has-text(" in s)
        last_structural_idx = max(
            i for i, s in enumerate(ONBOARDING_SELECTORS) if s in _ONBOARDING_STRUCTURAL_SELECTORS
        )
        assert last_structural_idx < first_text_idx, (
            "All structural selectors must appear before the first text selector"
        )

    def test_structural_selectors_are_subset_of_combined(self) -> None:
        """Every structural selector must appear in the combined tuple."""
        assert all(s in ONBOARDING_SELECTORS for s in _ONBOARDING_STRUCTURAL_SELECTORS)

    def test_text_selectors_are_subset_of_combined(self) -> None:
        """Every text selector must appear in the combined tuple."""
        assert all(s in ONBOARDING_SELECTORS for s in _ONBOARDING_TEXT_SELECTORS)

    def test_combined_has_no_duplicates(self) -> None:
        """No selector should appear twice."""
        assert len(ONBOARDING_SELECTORS) == len(set(ONBOARDING_SELECTORS))

    def test_structural_tier_is_contiguous_prefix(self) -> None:
        """The structural tier must be an unbroken prefix of the combined
        tuple, with the text tier as the contiguous suffix. Stronger than
        ``test_structural_selectors_precede_text_selectors`` because it does
        not rely on the ``:has-text(`` boundary marker — the text tier leads
        with two ``aria-label*`` ARIA-partial entries that lack that marker
        but are still text-tier (not locale-guaranteed)."""
        n = len(_ONBOARDING_STRUCTURAL_SELECTORS)
        assert ONBOARDING_SELECTORS[:n] == _ONBOARDING_STRUCTURAL_SELECTORS, (
            "Structural selectors must form an unbroken prefix"
        )
        assert ONBOARDING_SELECTORS[n:] == _ONBOARDING_TEXT_SELECTORS, (
            "Text selectors must form the contiguous suffix"
        )

    # --- structural-tier behaviour ------------------------------------------

    @pytest.mark.asyncio
    async def test_clicks_structural_selector_when_visible(self) -> None:
        """A visible structural (ARIA/id-based) selector is force-clicked and
        a settle delay runs — validates the structural tier is exercised."""
        structural_target = _ONBOARDING_STRUCTURAL_SELECTORS[0]
        t = UiAutomationTransport()
        page, clicked = _make_onboarding_page(visible_selectors={structural_target})
        await t._bypass_onboarding(page)  # type: ignore[attr-defined]
        assert structural_target in {sel for sel, _ in clicked}
        assert clicked[0][1].get("force") is True
        page.wait_for_timeout.assert_awaited()

    @pytest.mark.asyncio
    async def test_clicks_visible_onboarding_cta_with_force(self) -> None:
        """Any visible onboarding CTA is force-clicked (overlays intercept
        pointer events) and a settle delay follows."""
        target = ONBOARDING_SELECTORS[0]
        t = UiAutomationTransport()
        page, clicked = _make_onboarding_page(visible_selectors={target})
        await t._bypass_onboarding(page)  # type: ignore[attr-defined]
        assert [sel for sel, _ in clicked] == [target]
        assert clicked[0][1].get("force") is True
        page.wait_for_timeout.assert_awaited()

    # --- sweep behaviour ----------------------------------------------------

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
        cookie banner (structural) and a landing CTA (text) has both dismissed."""
        structural = _ONBOARDING_STRUCTURAL_SELECTORS[0]
        text = _ONBOARDING_TEXT_SELECTORS[0]
        targets = {structural, text}
        t = UiAutomationTransport()
        page, clicked = _make_onboarding_page(visible_selectors=targets)
        await t._bypass_onboarding(page)  # type: ignore[attr-defined]
        assert {sel for sel, _ in clicked} == targets

    # --- fault-tolerance ----------------------------------------------------

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


@pytest.mark.asyncio
async def test_submit_listener_is_registered_before_click() -> None:
    """Classic path invariant: the ``batchGenerateImages`` response listener is
    attached BEFORE the submit button is clicked, so a fast response is never
    missed. Exercises the real building blocks the classic ``_generate_images_locked``
    runs back-to-back: ``_attach_batch_response_listener`` (``page.on``) then the
    classic driver's ``send_prompt`` (which clicks submit via the typed transport
    seam)."""
    from gflow_cli.api.transports.drivers.classic import ClassicFlowUiDriver

    events: list[str] = []
    page = _make_prompt_page(input_visible=True, submit_visible=True)
    page.on = MagicMock(side_effect=lambda _event, _cb: events.append("listener_registered"))
    page._submit_loc.click = AsyncMock(  # type: ignore[attr-defined]
        side_effect=lambda *_a, **_k: events.append("submit_clicked")
    )

    t = UiAutomationTransport()
    driver = ClassicFlowUiDriver(transport=t)

    # Production ordering: attach the listener, THEN submit.
    t._attach_batch_response_listener(page, project_id="abc-123")  # type: ignore[attr-defined]
    await driver.send_prompt(page, "hello")

    assert events.index("listener_registered") < events.index("submit_clicked")


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

    @pytest.fixture(autouse=True)
    def _stub_image_mode_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stub _switch_to_image_mode for orchestration tests in this class.
        The dedicated mode-switch tests live in test_ui_automation_image_mode.py."""
        monkeypatch.setattr(UiAutomationTransport, "_switch_to_image_mode", AsyncMock())

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
    async def test_generate_images_threads_ui_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from gflow_cli.config import UiMode

        t = UiAutomationTransport()
        t._setup_done = True
        t._page = MagicMock()

        # GFLOW_CLI_UI_MODE=classic resolves to UiMode.CLASSIC and is threaded
        # into get_ui_driver (subsumes the deprecated prefer_classic).
        monkeypatch.setenv("GFLOW_CLI_UI_MODE", "classic")
        from gflow_cli.config import reset_settings

        reset_settings()

        mock_get_driver = AsyncMock()
        mock_get_driver.return_value.name = "classic"

        with (
            patch("gflow_cli.api.transports.drivers.factory.get_ui_driver", new=mock_get_driver),
            patch.object(t, "_enter_editor", new=AsyncMock()),
            patch.object(t, "_send_prompt", new=AsyncMock()),
            patch.object(t, "_await_captured", new=AsyncMock(return_value=[_flow_200_capture()])),
        ):
            await t.generate_images(project_id="ignored", request=_req())

        mock_get_driver.assert_called_once_with(t._page, ui_mode=UiMode.CLASSIC, transport=t)

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

    @pytest.mark.asyncio
    async def test_i2i_ref_paths_bound_via_attach_references(self) -> None:
        """I2I local refs (request.ref_paths) bind through the editor media
        dialog — _generate_images_locked awaits the inherited _attach_references
        with the local paths. Without this, the i2i bind is only covered at the
        CLI layer (mirrors the R2V transport-attach coverage)."""
        t = UiAutomationTransport()
        t._setup_done = True  # type: ignore[attr-defined]
        t._page = MagicMock()  # type: ignore[attr-defined]
        ref = Path("hero.png")
        req = GenerateImageRequest(prompt="stylize", model=Model.NARWHAL, ref_paths=(ref,))

        with (
            patch.object(t, "_enter_editor", new=AsyncMock()),
            patch.object(t, "_send_prompt", new=AsyncMock()),
            patch.object(t, "_attach_references", new=AsyncMock()) as attach,
            patch.object(t, "_await_captured", new=AsyncMock(return_value=[_flow_200_capture()])),
        ):
            await t.generate_images(project_id="x", request=req)

        attach.assert_awaited_once()
        call = attach.await_args
        assert call is not None
        # The local path is forwarded to the attach helper (positional arg 1).
        assert list(call.args[1]) == [ref]

    @pytest.mark.asyncio
    async def test_t2i_without_ref_paths_skips_attach(self) -> None:
        """T2I (no ref_paths) must NOT touch _attach_references."""
        t = UiAutomationTransport()
        t._setup_done = True  # type: ignore[attr-defined]
        t._page = MagicMock()  # type: ignore[attr-defined]

        with (
            patch.object(t, "_enter_editor", new=AsyncMock()),
            patch.object(t, "_send_prompt", new=AsyncMock()),
            patch.object(t, "_attach_references", new=AsyncMock()) as attach,
            patch.object(t, "_await_captured", new=AsyncMock(return_value=[_flow_200_capture()])),
        ):
            await t.generate_images(project_id="x", request=_req())

        attach.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reference_entities_attached_via_character_picker(self) -> None:
        """Character entity ids bind through the Personagens picker —
        _generate_images_locked awaits the inherited _attach_character_entities
        with (entity_id, name) pairs when request.reference_entities is set."""
        t = UiAutomationTransport()
        t._setup_done = True  # type: ignore[attr-defined]
        t._page = MagicMock()  # type: ignore[attr-defined]
        req = GenerateImageRequest(
            prompt="stacky and drako",
            model=Model.NARWHAL,
            reference_entities=("ent-1", "ent-2"),
            reference_entity_names=("Stacky",),  # fewer names than ids on purpose
        )

        def _seeding_logger(page: Any, *, project_id: Any = None, sink: Any = None) -> Any:
            # Feed the #170 submit backstop: pretend the captured submit
            # carried both staged entities (the real logger fills the sink
            # from outgoing batchGenerateImages bodies).
            if sink is not None:
                sink.append({"entity_ids": {"ent-1", "ent-2"}})
            return lambda: None

        with (
            patch.object(t, "_enter_editor", new=AsyncMock()),
            patch.object(t, "_send_prompt", new=AsyncMock()),
            patch.object(t, "_attach_character_entities", new=AsyncMock()) as attach,
            patch.object(
                t, "_attach_batch_request_logger", new=MagicMock(side_effect=_seeding_logger)
            ),
            patch.object(t, "_await_captured", new=AsyncMock(return_value=[_flow_200_capture()])),
        ):
            await t.generate_images(project_id="x", request=req)

        attach.assert_awaited_once()
        call = attach.await_args
        assert call is not None
        # Pairs are (id, name) — name falls back to the id when no name is given.
        assert list(call.args[1]) == [("ent-1", "Stacky"), ("ent-2", "ent-2")]

    @pytest.mark.asyncio
    async def test_without_reference_entities_skips_character_attach(self) -> None:
        t = UiAutomationTransport()
        t._setup_done = True  # type: ignore[attr-defined]
        t._page = MagicMock()  # type: ignore[attr-defined]

        with (
            patch.object(t, "_enter_editor", new=AsyncMock()),
            patch.object(t, "_send_prompt", new=AsyncMock()),
            patch.object(t, "_attach_character_entities", new=AsyncMock()) as attach,
            patch.object(t, "_await_captured", new=AsyncMock(return_value=[_flow_200_capture()])),
        ):
            await t.generate_images(project_id="x", request=_req())

        attach.assert_not_awaited()


class TestZipEntities:
    def test_pairs_ids_with_names(self) -> None:
        assert zip_entity_refs(("a", "b"), ("Stacky", "Drako")) == [
            ("a", "Stacky"),
            ("b", "Drako"),
        ]

    def test_name_falls_back_to_id_when_missing(self) -> None:
        assert zip_entity_refs(("a", "b"), ("Stacky",)) == [("a", "Stacky"), ("b", "b")]

    def test_empty(self) -> None:
        assert zip_entity_refs((), ()) == []


class TestSummarizeBatchRequestBody:
    """The make-or-break spike reads this summary to learn whether the image
    submit carries `referenceEntities` — without dumping i2i image bytes."""

    def test_none_body(self) -> None:
        assert _summarize_batch_request_body(None) == {"present": False}

    def test_extracts_reference_entity_fields(self) -> None:
        body = json.dumps(
            {
                "requests": [
                    {
                        "structuredPrompt": {"parts": [{"text": "x"}]},
                        "referenceEntities": [{"entityId": "ent-1"}],
                        "imageInputs": [],
                    }
                ]
            }
        )
        out = _summarize_batch_request_body(body)
        assert out["present"] is True
        assert out["mentions_reference_entities"] is True
        assert "referenceEntities" in out["request0_keys"]
        assert out["reference_fields"]["referenceEntities"] == [{"entityId": "ent-1"}]

    def test_no_reference_fields(self) -> None:
        body = json.dumps({"requests": [{"structuredPrompt": {"parts": []}, "imageInputs": []}]})
        out = _summarize_batch_request_body(body)
        assert out["present"] is True
        assert out["mentions_reference_entities"] is False
        assert "reference_fields" not in out

    def test_non_json_body_still_flags_substring(self) -> None:
        out = _summarize_batch_request_body("garbage-not-json-referenceEntities")
        assert out["present"] is True
        assert out["mentions_reference_entities"] is True
        assert "request0_keys" not in out

    def test_large_reference_field_is_elided_not_dumped(self) -> None:
        # If Flow names an i2i image field `reference*`, its base64 bytes must NOT
        # be logged verbatim — they get elided to a length marker.
        big = "A" * 5000
        body = json.dumps({"requests": [{"referenceImage": big, "imageInputs": []}]})
        out = _summarize_batch_request_body(body)
        assert out["reference_fields"]["referenceImage"] != big
        assert "elided" in out["reference_fields"]["referenceImage"]


class TestElideLargeValue:
    def test_small_value_passes_through(self) -> None:
        from gflow_cli.api.transports.ui_automation import _elide_large_value

        v = [{"entityId": "ent-1"}]
        assert _elide_large_value(v) == v

    def test_large_value_is_elided(self) -> None:
        from gflow_cli.api.transports.ui_automation import _elide_large_value

        out = _elide_large_value("Z" * 5000)
        assert isinstance(out, str) and "elided" in out


class TestAttachBatchRequestLogger:
    """The request-body logger must: fire only for batchGenerateImages, never let
    a post_data failure break generation, and detach idempotently."""

    class _Req:
        def __init__(self, url: str, *, raises: bool = False, post: str | None = None) -> None:
            self.url = url
            self._raises = raises
            self._post = post

        @property
        def post_data(self) -> str | None:
            if self._raises:
                raise RuntimeError("post_data unavailable")
            return self._post

    def test_registers_fires_safely_and_detaches_once(self) -> None:
        page = MagicMock()
        handlers: dict[str, object] = {}
        page.on.side_effect = lambda event, fn: handlers.__setitem__(event, fn)

        detach = UiAutomationTransport._attach_batch_request_logger(page, project_id="P")
        on_request = handlers["request"]
        assert callable(on_request)

        # Non-matching URL: must early-return WITHOUT touching post_data (raises if touched).
        on_request(self._Req("https://x/other", raises=True))
        # Matching URL but post_data raises: must be swallowed (generation unaffected).
        on_request(self._Req("https://x/flowMedia:batchGenerateImages", raises=True))
        # Matching URL with a real body: must not raise.
        on_request(
            self._Req(
                "https://x/flowMedia:batchGenerateImages",
                post=json.dumps({"requests": [{"referenceEntities": [{"entityId": "e"}]}]}),
            )
        )

        detach()
        detach()  # idempotent
        page.remove_listener.assert_called_once_with("request", on_request)


class TestImageEntityBackstop:
    """Issue #170: when reference entities were requested, the captured
    batchGenerateImages submit must carry them — otherwise a missed UI attach
    silently degrades to a text-only generation reported as success (the
    image path previously only LOGGED the summary; the video path has
    _assert_entities_attached)."""

    @pytest.fixture(autouse=True)
    def _stub_image_mode_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Same orchestration stub as TestGenerateImages — mode-switch coverage
        lives in test_ui_automation_image_mode.py."""
        monkeypatch.setattr(UiAutomationTransport, "_switch_to_image_mode", AsyncMock())

    def test_entity_ids_extracted_from_request_body(self) -> None:
        from gflow_cli.api.transports.ui_automation import _entity_ids_from_request_body

        body = json.dumps(
            {
                "requests": [
                    {"referenceEntities": [{"entityId": "ent-1"}, {"entityId": "ent-2"}]},
                    {"referenceEntities": [{"entityId": "ent-3"}]},
                ]
            }
        )
        assert _entity_ids_from_request_body(body) == {"ent-1", "ent-2", "ent-3"}

    def test_entity_ids_empty_for_none_garbage_or_absent(self) -> None:
        from gflow_cli.api.transports.ui_automation import _entity_ids_from_request_body

        assert _entity_ids_from_request_body(None) == set()
        assert _entity_ids_from_request_body("garbage-not-json") == set()
        assert _entity_ids_from_request_body(json.dumps({"requests": [{}]})) == set()
        assert _entity_ids_from_request_body(json.dumps({"requests": "nope"})) == set()

    def test_request_logger_sink_collects_entity_ids(self) -> None:
        page = MagicMock()
        handlers: dict[str, object] = {}
        page.on.side_effect = lambda event, fn: handlers.__setitem__(event, fn)
        sink: list[dict[str, object]] = []

        detach = UiAutomationTransport._attach_batch_request_logger(page, project_id="P", sink=sink)
        on_request = handlers["request"]
        assert callable(on_request)
        on_request(
            TestAttachBatchRequestLogger._Req(
                "https://x/flowMedia:batchGenerateImages",
                post=json.dumps({"requests": [{"referenceEntities": [{"entityId": "ent-1"}]}]}),
            )
        )
        # Non-matching URLs never reach the sink.
        on_request(TestAttachBatchRequestLogger._Req("https://x/other", post="{}"))
        detach()

        assert len(sink) == 1
        assert sink[0]["entity_ids"] == {"ent-1"}

    def test_assert_raises_when_entity_missing(self) -> None:
        from gflow_cli.errors import WireFormatError

        with pytest.raises(WireFormatError, match="referenceEntities"):
            UiAutomationTransport._assert_image_entities_attached(
                [{"entity_ids": set()}], expected=["ent-1"]
            )

    def test_assert_raises_when_nothing_was_captured(self) -> None:
        from gflow_cli.errors import WireFormatError

        with pytest.raises(WireFormatError, match="referenceEntities"):
            UiAutomationTransport._assert_image_entities_attached([], expected=["ent-1"])

    def test_assert_passes_when_entities_present_across_bodies(self) -> None:
        UiAutomationTransport._assert_image_entities_attached(
            [{"entity_ids": {"ent-1"}}, {"entity_ids": {"ent-2"}}],
            expected=["ent-1", "ent-2"],
        )

    def test_assert_error_carries_issue_174_hint_and_discovery(self) -> None:
        """Issue #174: an attach miss on the new library UI must point the
        user at the tracking issue (typed-error remediation hint) and tag
        the surface in the discovery payload."""
        from gflow_cli.errors import WireFormatError

        with pytest.raises(WireFormatError) as exc_info:
            UiAutomationTransport._assert_image_entities_attached([], expected=["ent-1"])
        err = exc_info.value
        assert "github.com/ffroliva/gflow-cli/issues/174" in err.remediation_hint
        assert err.to_problem_details().get("remediation_hint") == err.remediation_hint
        assert err.discovery == {"entity_attach_context": "image"}

    @pytest.mark.asyncio
    async def test_generate_images_raises_when_entities_never_rode_the_wire(self) -> None:
        """The wiring: entities requested + no captured submit carries them →
        WireFormatError instead of returning images as a success."""
        from gflow_cli.errors import WireFormatError

        t = UiAutomationTransport()
        t._setup_done = True  # type: ignore[attr-defined]
        t._page = MagicMock()  # type: ignore[attr-defined]
        req = GenerateImageRequest(
            prompt="stacky",
            model=Model.NARWHAL,
            reference_entities=("ent-1",),
            reference_entity_names=("Stacky",),
        )

        with (
            patch.object(t, "_enter_editor", new=AsyncMock()),
            patch.object(t, "_send_prompt", new=AsyncMock()),
            patch.object(t, "_attach_character_entities", new=AsyncMock()),
            patch.object(t, "_await_captured", new=AsyncMock(return_value=[_flow_200_capture()])),
            pytest.raises(WireFormatError, match="referenceEntities"),
        ):
            await t.generate_images(project_id="x", request=req)

    @pytest.mark.asyncio
    async def test_generate_images_passes_when_submit_carries_entities(self) -> None:
        t = UiAutomationTransport()
        t._setup_done = True  # type: ignore[attr-defined]
        t._page = MagicMock()  # type: ignore[attr-defined]
        req = GenerateImageRequest(
            prompt="stacky",
            model=Model.NARWHAL,
            reference_entities=("ent-1",),
            reference_entity_names=("Stacky",),
        )

        def _seeding_logger(page: Any, *, project_id: Any = None, sink: Any = None) -> Any:
            if sink is not None:
                sink.append({"entity_ids": {"ent-1"}})
            return lambda: None

        with (
            patch.object(t, "_enter_editor", new=AsyncMock()),
            patch.object(t, "_send_prompt", new=AsyncMock()),
            patch.object(t, "_attach_character_entities", new=AsyncMock()),
            patch.object(
                t, "_attach_batch_request_logger", new=MagicMock(side_effect=_seeding_logger)
            ),
            patch.object(t, "_await_captured", new=AsyncMock(return_value=[_flow_200_capture()])),
        ):
            images = await t.generate_images(project_id="x", request=req)
        assert images


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
    """Minimal fake page for _read_displayed_count tests.

    Models the new implementation which calls:
      page.locator('[role="tab"][aria-selected="true"]').filter(has_text=RE)

    The ``filter(has_text=...)`` call is simulated by checking whether
    ``selected_text`` matches :data:`_COUNT_TAB_TEXT_RE`. When it matches
    (e.g. "1x", "x2") the filtered locator reports ``count()=1``; when it
    does not (e.g. "imageImagem", Mode/Aspect tab text) it reports ``count()=0``
    so ``_read_displayed_count`` returns ``None``.
    """
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()

    def _locator(sel: str) -> MagicMock:
        outer = MagicMock()

        def _filter(**kwargs: object) -> MagicMock:
            # Simulate has_text filtering: count=1 only when text matches RE.
            text_matches = (
                selected_text is not None
                and visible
                and bool(_COUNT_TAB_TEXT_RE.match(selected_text.strip()))
            )
            filtered = MagicMock()
            filtered.count = AsyncMock(return_value=1 if text_matches else 0)
            first_loc = MagicMock()
            first_loc.text_content = AsyncMock(return_value=selected_text)
            filtered.first = first_loc
            return filtered

        outer.filter = MagicMock(side_effect=_filter)

        # Also wire .first for any direct first-access patterns.
        loc = MagicMock()
        loc.is_visible = AsyncMock(return_value=selected_text is not None and visible)
        loc.text_content = AsyncMock(return_value=selected_text)
        loc.wait_for = AsyncMock()
        loc.click = AsyncMock()
        outer.first = loc
        return outer

    page.locator = MagicMock(side_effect=_locator)
    return page


def _make_tablist_page(
    *,
    tab_count: int = 4,
    selected_idx: int = 0,
    selected_text: str = "1x",
    readback_after_click: str | None = None,
) -> tuple[MagicMock, list[int]]:
    """Build a fake page modelling the new ``_count_tabs_locator`` / ``_read_displayed_count``.

    The new implementation calls:
    - ``page.locator('[role="tab"]').filter(has_text=RE).nth(i)`` for click
    - ``page.locator('[role="tab"][aria-selected="true"]').filter(has_text=RE)``
      for read-back

    Returns ``(page, clicked_indices)`` where ``clicked_indices`` accumulates
    the nth-index of every ``click()`` call.

    ``selected_text`` defaults to ``"1x"`` (the count-1 tab label). After any
    click, ``readback_after_click`` is returned by the read-back filter.
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

    def _make_count_tabs_filtered_loc() -> MagicMock:
        """Filtered locator for _count_tabs_locator: all 4 count tabs."""
        filtered = MagicMock()
        filtered.first = tab_mocks[0]

        def _nth(i: int) -> MagicMock:
            return tab_mocks[i] if 0 <= i < tab_count else MagicMock()

        filtered.nth = MagicMock(side_effect=_nth)
        return filtered

    def _make_selected_filtered_loc() -> MagicMock:
        """Filtered locator for _read_displayed_count: aria-selected + RE filter."""
        filtered = MagicMock()

        async def _count_selected() -> int:
            # selected_text matches RE → count=1; else count=0.
            has_clicked = readback_after_click is not None and click_count_store[0] > 0
            text = readback_after_click if has_clicked else selected_text
            return 1 if (text and _COUNT_TAB_TEXT_RE.match(text.strip())) else 0

        filtered.count = AsyncMock(side_effect=_count_selected)

        first_loc = MagicMock()

        async def _text(timeout: int = 500) -> str | None:
            if readback_after_click is not None and click_count_store[0] > 0:
                return readback_after_click
            return selected_text

        first_loc.text_content = AsyncMock(side_effect=_text)
        filtered.first = first_loc
        return filtered

    def _locator(sel: str) -> MagicMock:
        wrapper = MagicMock()

        if '[role="tab"]' in sel and "aria-selected" not in sel:
            # _count_tabs_locator: page.locator('[role="tab"]').filter(has_text=RE)
            outer = MagicMock()
            outer.filter = MagicMock(return_value=_make_count_tabs_filtered_loc())
            # Also wire first/is_visible for _is_settings_panel_open
            outer.first = tab_mocks[0]
            return outer

        if 'aria-selected="true"' in sel:
            # _read_displayed_count: page.locator('[role="tab"][aria-selected="true"]').filter(...)
            outer = MagicMock()
            outer.filter = MagicMock(return_value=_make_selected_filtered_loc())
            outer.first = tab_mocks[selected_idx]
            return outer

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
# Inline digit extraction — documents the re.search(r"\d", text) logic used
# inside _read_displayed_count after the _COUNT_TAB_TEXT_RE filter passes.
# ---------------------------------------------------------------------------


def _extract_digit(text: str) -> int | None:
    """Mirror of the inline digit extraction in _read_displayed_count.

    ``re.search(r"\\d", text)`` finds the first digit character. For count-tab
    labels ("1x", "x2", "x3", "x4") this always yields the count digit. The
    function is defined here rather than in production code because it is now
    an implementation detail of _read_displayed_count only.
    """
    import re

    m = re.search(r"\d", text)
    return int(m.group()) if m else None


class TestExtractCountDigit:
    """Documents digit extraction for count-tab label text.

    The old _extract_count_digit is gone from production code; its logic lives
    inline in _read_displayed_count. These tests exercise _extract_digit (the
    local mirror) to ensure the inline behaviour is still well-understood.
    """

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # Flow count-tab labels (the only ones that reach digit extraction now)
            ("1x", 1),
            ("x2", 2),
            ("x3", 3),
            ("x4", 4),
            # No digit — icon-ligature artefact (filtered out before this point by RE)
            ("imageImagem", None),
            ("", None),
        ],
    )
    def test_extract(self, text: str, expected: int | None) -> None:
        assert _extract_digit(text) == expected


# ---------------------------------------------------------------------------
# Unit 3.13a — _COUNT_TAB_TEXT_RE pattern correctness
# ---------------------------------------------------------------------------


class TestCountTabTextRe:
    """_COUNT_TAB_TEXT_RE must match exactly 1x/x2/x3/x4 and nothing else."""

    @pytest.mark.parametrize(
        ("text", "should_match"),
        [
            # Count tab labels (must match)
            ("1x", True),
            ("x2", True),
            ("x3", True),
            ("x4", True),
            # Mode / Aspect tab labels (must NOT match)
            ("imageImagem", False),
            ("image\nImagem", False),
            ("16:9", False),
            ("9:16", False),
            ("1:1", False),
            ("4:3", False),
            ("3:4", False),
            ("crop_16_9", False),
            ("", False),
            # Locale-variant forms that used to work in the old impl (now filtered out)
            ("x1", False),  # x1 is NOT a Flow count tab — Flow uses 1x for count=1
            ("1 image", False),
            ("1 imagem", False),
            ("2 imagens", False),
        ],
    )
    def test_pattern(self, text: str, should_match: bool) -> None:
        result = bool(_COUNT_TAB_TEXT_RE.match(text))
        assert result == should_match, (
            f"_COUNT_TAB_TEXT_RE.match({text!r}) → {result}, expected {should_match}"
        )


# ---------------------------------------------------------------------------
# Unit 3.13b — _count_tabs_locator filter disambiguation
# ---------------------------------------------------------------------------


class TestCountTabsLocator:
    """_count_tabs_locator must return only the 4 count tabs, not Mode/Aspect tabs."""

    @pytest.mark.asyncio
    async def test_filter_excludes_mode_and_aspect_tabs(self) -> None:
        """page.locator('[role="tab"]').filter(has_text=RE) is called with the
        correct regex. When the DOM has Mode + Aspect + Count tabs all present,
        only the count tabs survive the filter."""
        page = MagicMock()

        # The locator chain: page.locator(...).filter(has_text=RE)
        all_tabs_loc = MagicMock()
        filtered_loc = MagicMock()
        filtered_loc.count = AsyncMock(return_value=4)  # exactly 4 count tabs survive
        all_tabs_loc.filter = MagicMock(return_value=filtered_loc)
        page.locator = MagicMock(return_value=all_tabs_loc)

        result = _count_tabs_locator(page)

        # Must query role="tab" (not role="tablist")
        page.locator.assert_called_once_with('[role="tab"]')
        # Must call filter with has_text= the RE
        all_tabs_loc.filter.assert_called_once()
        call_kwargs = all_tabs_loc.filter.call_args.kwargs
        assert "has_text" in call_kwargs, "filter must use has_text= keyword"
        assert call_kwargs["has_text"] is _COUNT_TAB_TEXT_RE

        # Result is the filtered locator (not the unfiltered all_tabs_loc)
        assert result is filtered_loc

    @pytest.mark.asyncio
    async def test_filtered_locator_count_is_4(self) -> None:
        """After filtering, exactly 4 count tabs are found."""
        page = MagicMock()
        all_tabs_loc = MagicMock()
        filtered_loc = MagicMock()
        filtered_loc.count = AsyncMock(return_value=4)
        all_tabs_loc.filter = MagicMock(return_value=filtered_loc)
        page.locator = MagicMock(return_value=all_tabs_loc)

        result = _count_tabs_locator(page)
        count = await result.count()
        assert count == 4


# ---------------------------------------------------------------------------
# Unit 3.13 — _read_displayed_count + _set_count retry logic
# ---------------------------------------------------------------------------


class TestReadDisplayedCount:
    """_read_displayed_count returns digit from the selected COUNT tab only.

    The new implementation filters [aria-selected="true"] by _COUNT_TAB_TEXT_RE
    (``^(1x|x[2-4])$``) so Mode tabs ("image\\nImagem") and Aspect tabs ("16:9")
    that are also aria-selected never pollute the result.
    """

    @pytest.mark.asyncio
    async def test_count_tab_1x_returns_1(self) -> None:
        """'1x' (Flow's count-1 tab label) → 1."""
        page = _make_selected_tab_page("1x")
        result = await UiAutomationTransport._read_displayed_count(page)  # type: ignore[attr-defined]
        assert result == 1

    @pytest.mark.asyncio
    async def test_count_tab_x2_returns_2(self) -> None:
        """'x2' (Flow's count-2 tab label) → 2."""
        page = _make_selected_tab_page("x2")
        result = await UiAutomationTransport._read_displayed_count(page)  # type: ignore[attr-defined]
        assert result == 2

    @pytest.mark.asyncio
    async def test_count_tab_x3_returns_3(self) -> None:
        """'x3' → 3."""
        page = _make_selected_tab_page("x3")
        result = await UiAutomationTransport._read_displayed_count(page)  # type: ignore[attr-defined]
        assert result == 3

    @pytest.mark.asyncio
    async def test_count_tab_x4_returns_4(self) -> None:
        """'x4' → 4."""
        page = _make_selected_tab_page("x4")
        result = await UiAutomationTransport._read_displayed_count(page)  # type: ignore[attr-defined]
        assert result == 4

    @pytest.mark.asyncio
    async def test_mode_tab_text_returns_none(self) -> None:
        """'image\\nImagem' (Mode tab, pt-BR) → None.

        This was the original bug: the old unfiltered [aria-selected="true"]
        matched the Mode tab first (it appeared before the Count tab in DOM
        order) and returned 'imageImagem', causing _extract_count_digit to
        return None — which then fell through to a mismatched positional click.
        The filter now excludes Mode tab text entirely.
        """
        page = _make_selected_tab_page("imageImagem")
        result = await UiAutomationTransport._read_displayed_count(page)  # type: ignore[attr-defined]
        assert result is None

    @pytest.mark.asyncio
    async def test_aspect_tab_text_returns_none(self) -> None:
        """'9:16' (Aspect tab) → None — aspect tabs are also aria-selected."""
        page = _make_selected_tab_page("9:16")
        result = await UiAutomationTransport._read_displayed_count(page)  # type: ignore[attr-defined]
        assert result is None

    @pytest.mark.asyncio
    async def test_no_count_tab_visible_returns_none(self) -> None:
        """Returns None when no aria-selected count tab is present."""
        page = _make_selected_tab_page(None)
        result = await UiAutomationTransport._read_displayed_count(page)  # type: ignore[attr-defined]
        assert result is None


class TestSetCountRetry:
    """_set_count uses position-based click with digit read-back; raises after 3 failed attempts.

    The new implementation uses _count_tabs_locator (text-pattern filtered) for
    nth() click, so selected_text / readback_after_click use RE-matching labels
    ("1x", "x2", "x3", "x4") to model the real DOM.
    """

    @pytest.mark.asyncio
    async def test_returns_early_when_count_already_matches(self) -> None:
        """If the displayed count already matches desired, no tab click is made."""
        # Panel open, selected count tab shows "x2" → count=2 already, no click.
        page, clicked = _make_tablist_page(selected_text="x2")
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
        # Readback returns "x3" after click so the method completes normally.
        page, clicked = _make_tablist_page(selected_text="1x", readback_after_click="x3")
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
                # before: None (filtered out by RE); after click: None (still filtered)
                new=AsyncMock(return_value=None),
            ),
        ):
            # Should NOT raise — deterministic position click is trusted when readback=None.
            await UiAutomationTransport._set_count(page, 1)  # type: ignore[attr-defined]
        assert 0 in clicked  # nth(0) was clicked for count=1

    @pytest.mark.asyncio
    async def test_clicks_tab_and_succeeds_on_first_attempt(self) -> None:
        """Happy path: displayed=None (unrecognised), click succeeds, read-back matches."""
        page, clicked = _make_tablist_page(selected_text="1x", readback_after_click="1x")
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
        page, clicked = _make_tablist_page(selected_text="x2", readback_after_click="x2")
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
        page, clicked = _make_tablist_page(selected_text="x2", readback_after_click="1x")
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


# ---------------------------------------------------------------------------
# Unit 3.14 — _dump_count_panel_dom diagnostic helper
# ---------------------------------------------------------------------------


class TestDumpCountPanelDom:
    """_dump_count_panel_dom writes a structured JSON snapshot to out_dir."""

    @pytest.mark.asyncio
    async def test_dump_count_panel_dom_writes_json(self, tmp_path: Path) -> None:
        """Mock page.evaluate returns a known snapshot; JSON file is written with correct shape."""
        import json

        known_snapshot = {
            "url": "https://labs.google/fx/tools/flow/project/123",
            "title": "Flow",
            "roles": {
                "tab": [
                    {
                        "text": "1 Imagem",
                        "aria_label": None,
                        "aria_selected": "true",
                        "aria_controls": None,
                        "id": None,
                        "classes": "mat-tab",
                    }
                ],
                "tablist": [],
                "radiogroup": [],
                "radio": [],
            },
            "buttons_with_digits": [
                {
                    "text": "1 Imagem",
                    "aria_label": None,
                    "aria_selected": "true",
                    "role": "tab",
                    "parent_role": "tablist",
                    "parent_class": "mat-tab-group",
                }
            ],
            "google_symbols_ligatures": [
                {
                    "ligature": "image",
                    "parent_text": "1 Imagem",
                    "parent_role": "tab",
                    "parent_aria_label": None,
                }
            ],
        }

        page = MagicMock()
        page.evaluate = AsyncMock(return_value=known_snapshot)

        await UiAutomationTransport._dump_count_panel_dom(page, tmp_path, 0)  # type: ignore[attr-defined]

        out_file = tmp_path / "_diagnostics" / "count_panel_dom_prompt_0.json"
        assert out_file.exists(), "JSON dump file must be created"

        written = json.loads(out_file.read_text(encoding="utf-8"))
        assert written["url"] == known_snapshot["url"]
        assert written["title"] == known_snapshot["title"]
        assert "tab" in written["roles"]
        assert len(written["roles"]["tab"]) == 1
        assert written["roles"]["tab"][0]["text"] == "1 Imagem"
        assert len(written["buttons_with_digits"]) == 1
        assert written["buttons_with_digits"][0]["role"] == "tab"
        assert len(written["google_symbols_ligatures"]) == 1
        assert written["google_symbols_ligatures"][0]["ligature"] == "image"

    @pytest.mark.asyncio
    async def test_dump_count_panel_dom_noop_without_out_dir(self) -> None:
        """No-op (no write, no evaluate call) when out_dir is None."""
        page = MagicMock()
        page.evaluate = AsyncMock()

        await UiAutomationTransport._dump_count_panel_dom(page, None, 0)  # type: ignore[attr-defined]

        page.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_dump_count_panel_dom_noop_without_prompt_idx(self, tmp_path: Path) -> None:
        """No-op when prompt_idx is None even if out_dir is set."""
        page = MagicMock()
        page.evaluate = AsyncMock()

        await UiAutomationTransport._dump_count_panel_dom(page, tmp_path, None)  # type: ignore[attr-defined]

        page.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_dump_count_panel_dom_swallows_evaluate_error(self, tmp_path: Path) -> None:
        """Failures in page.evaluate are swallowed — diagnostic must not raise."""
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=RuntimeError("CDP disconnected"))

        # Must not raise
        await UiAutomationTransport._dump_count_panel_dom(page, tmp_path, 1)  # type: ignore[attr-defined]

        out_file = tmp_path / "_diagnostics" / "count_panel_dom_prompt_1.json"
        assert not out_file.exists(), "No file written on evaluate failure"


# ---------------------------------------------------------------------------
# Selector locale-invariance invariants
# ---------------------------------------------------------------------------

_NEW_PROJECT_REQUIRED_LOCALES = [
    "New project",  # EN
    "Novo projeto",  # PT
    "Nuevo proyecto",  # ES
    "Nouveau projet",  # FR
    "Neues Projekt",  # DE
    "Nuovo progetto",  # IT
    "Nieuw project",  # NL
    "新しいプロジェクト",  # JA
    "新建项目",  # ZH
    "새 프로젝트",  # KO
    "Nowy projekt",  # PL
    "Новый проект",  # RU
    "Yeni proje",  # TR
    "Proyek baru",  # ID
]


class TestSelectorLocaleInvariance:
    """Static invariants ensuring selector tuples are locale-agnostic."""

    def test_submit_button_selectors_no_english_aria(self) -> None:
        """SUBMIT_BUTTON_SELECTORS must not contain English aria-label fallbacks."""
        for sel in SUBMIT_BUTTON_SELECTORS:
            assert "Create" not in sel, (
                f"English-only aria-label fallback found in SUBMIT_BUTTON_SELECTORS: {sel!r}"
            )

    def test_submit_button_selectors_lead_with_icon(self) -> None:
        """First selector in SUBMIT_BUTTON_SELECTORS must be the icon-class anchor."""
        assert "google-symbols" in SUBMIT_BUTTON_SELECTORS[0], (
            "SUBMIT_BUTTON_SELECTORS must lead with the google-symbols icon selector"
        )

    def test_new_project_selectors_no_duplicates(self) -> None:
        assert len(NEW_PROJECT_SELECTORS) == len(set(NEW_PROJECT_SELECTORS))

    def test_new_project_selectors_icon_leads(self) -> None:
        """First selector must be the exact google-symbols icon-class anchor.

        Locks the full selector prefix (not just substring) so a future rename of
        the icon ligature, container tag, or class can't silently slip past.
        """
        assert NEW_PROJECT_SELECTORS[0] == "button:has(i.google-symbols:text('add_2'))", (
            f"NEW_PROJECT_SELECTORS[0] drifted: {NEW_PROJECT_SELECTORS[0]!r}"
        )

    def test_new_project_selectors_plus_regex_is_anchored(self) -> None:
        """The '+ <word>' Tier-1 regex must be anchored to avoid over-matching.

        Without ``^`` / ``$``, the pattern matches buttons like '+ Filter' or
        '+ Add member'. Anchoring keeps it scoped to the new-project CTA shape.
        """
        plus_regex = next((s for s in NEW_PROJECT_SELECTORS if "text-matches" in s), None)
        assert plus_regex is not None, "No text-matches '+' regex in NEW_PROJECT_SELECTORS"
        assert "^" in plus_regex and "$" in plus_regex, (
            f"Plus regex must be anchored: {plus_regex!r}"
        )

    def test_new_project_selectors_covers_all_14_locales(self) -> None:
        """Every required locale text must appear in at least one selector."""
        combined = " ".join(NEW_PROJECT_SELECTORS)
        missing = [loc for loc in _NEW_PROJECT_REQUIRED_LOCALES if loc not in combined]
        assert not missing, f"NEW_PROJECT_SELECTORS missing locale entries: {missing}"

    def test_new_project_selectors_no_english_only_aria(self) -> None:
        """No English-only aria-label partial match should appear."""
        for sel in NEW_PROJECT_SELECTORS:
            assert "[aria-label*='New project'" not in sel, (
                f"English-only aria-label in NEW_PROJECT_SELECTORS: {sel!r}"
            )
            assert "[aria-label*='Project'" not in sel, (
                f"English-only aria-label in NEW_PROJECT_SELECTORS: {sel!r}"
            )

    def test_image_model_option_selectors_are_tuples(self) -> None:
        """Every model entry in IMAGE_MODEL_OPTION_SELECTORS must be a non-empty tuple."""
        from gflow_cli.api.image import Model

        for model in (Model.NARWHAL, Model.GEM_PIX_2, Model.IMAGEN_3_5):
            sels = IMAGE_MODEL_OPTION_SELECTORS.get(model)
            assert sels is not None, f"Missing entry for {model!r}"
            assert isinstance(sels, tuple), f"Entry for {model!r} must be a tuple, got {type(sels)}"
            assert len(sels) > 0, f"Selector tuple for {model!r} must not be empty"

    def test_image_model_option_selectors_no_duplicates(self) -> None:
        """No duplicate selectors within any model's cascade."""
        for model, sels in IMAGE_MODEL_OPTION_SELECTORS.items():
            assert len(sels) == len(set(sels)), (
                f"Duplicate selectors in IMAGE_MODEL_OPTION_SELECTORS[{model!r}]: {sels}"
            )

    def test_image_model_option_selectors_all_models_covered(self) -> None:
        """All three image models must have selector entries."""
        from gflow_cli.api.image import Model

        for model in (Model.NARWHAL, Model.GEM_PIX_2, Model.IMAGEN_3_5):
            assert model in IMAGE_MODEL_OPTION_SELECTORS, (
                f"{model!r} missing from IMAGE_MODEL_OPTION_SELECTORS"
            )

    def test_launch_args_no_lang_en_us(self) -> None:
        """--lang=en-US must not appear in UiAutomationTransport executable code.

        Filters comment lines so future documentation comments (e.g. "# Note:
        --lang=en-US was removed in PR #127") don't trip this guard.
        IMAGE_MODEL_OPTION_SELECTORS uses locale-stable product names; locale is
        controlled by the ``locale=`` Playwright kwarg (issue #94 / issue #24 Phase 5).
        """
        import inspect

        from gflow_cli.api.transports import ui_automation

        non_comment_lines = [
            line
            for line in inspect.getsource(ui_automation).splitlines()
            if not line.lstrip().startswith("#")
        ]
        assert "--lang=en-US" not in "\n".join(non_comment_lines), (
            "--lang=en-US was re-introduced into executable code; "
            "IMAGE_MODEL_OPTION_SELECTORS must not require it (issue #94)"
        )


def _make_model_page_mock(
    *,
    trigger_visible: bool = True,
    selector_side_effects: list[list[Exception | None]] | None = None,
) -> MagicMock:
    """Build a page mock for _select_image_model tests.

    selector_side_effects: per-call list of exceptions (or None for success)
    that page.locator(...).first.wait_for raises. Index 0 = first selector tried.
    """
    page = MagicMock()
    trigger_loc = MagicMock()
    trigger_loc.first = trigger_loc
    trigger_loc.wait_for = AsyncMock(
        side_effect=None if trigger_visible else Exception("trigger not found")
    )
    trigger_loc.click = AsyncMock()

    effects = selector_side_effects or [[None]]
    call_index = [0]

    def _locator(sel: str) -> MagicMock:
        loc = MagicMock()
        loc.first = loc
        idx = call_index[0]
        call_index[0] += 1
        effect = effects[idx][0] if idx < len(effects) else None
        loc.wait_for = AsyncMock(side_effect=effect)
        loc.click = AsyncMock()
        return loc

    def _side_effect(sel: str) -> MagicMock:
        if "arrow_drop_down" in sel:
            return trigger_loc
        return _locator(sel)

    page.locator = MagicMock(side_effect=_side_effect)
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    return page


class TestSelectImageModel:
    """Unit tests for UiAutomationTransport._select_image_model cascade logic."""

    @pytest.mark.asyncio
    async def test_happy_path_first_selector_wins(self) -> None:
        """First selector succeeds → model selected, log.info emitted, no Escape."""
        page = _make_model_page_mock(selector_side_effects=[[None]])
        with patch("gflow_cli.api.transports.ui_automation.log") as mock_log:
            await UiAutomationTransport._select_image_model(page, Model.NARWHAL)
        mock_log.info.assert_called_once()
        call_kwargs = mock_log.info.call_args[0][0]
        assert call_kwargs == "ui_automation.image_model_selected"
        page.keyboard.press.assert_not_called()

    @pytest.mark.asyncio
    async def test_cascade_fallthrough_second_selector_wins(self) -> None:
        """First selector times out, second succeeds → second selector used."""
        from gflow_cli.api.transports.ui_automation import IMAGE_MODEL_OPTION_SELECTORS

        page = MagicMock()
        trigger_loc = MagicMock()
        trigger_loc.first = trigger_loc
        trigger_loc.wait_for = AsyncMock()
        trigger_loc.click = AsyncMock()

        call_count = [0]

        def _locator(sel: str) -> MagicMock:
            loc = MagicMock()
            loc.first = loc
            n = call_count[0]
            call_count[0] += 1
            loc.wait_for = AsyncMock(side_effect=Exception("timeout") if n == 0 else None)
            loc.click = AsyncMock()
            return loc

        page.locator = MagicMock(
            side_effect=lambda sel: trigger_loc if "arrow_drop_down" in sel else _locator(sel)
        )
        page.wait_for_timeout = AsyncMock()
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()

        # Patch a model to have two selectors so the fallthrough is exercised.
        two_sel = ("[role='menuitem']:has-text('MISS')", "[role='menuitem']:has-text('Imagen 4')")
        patched = {**IMAGE_MODEL_OPTION_SELECTORS, Model.IMAGEN_3_5: two_sel}
        with (
            patch(
                "gflow_cli.api.transports.ui_automation.IMAGE_MODEL_OPTION_SELECTORS",
                patched,
            ),
            patch("gflow_cli.api.transports.ui_automation.log") as mock_log,
        ):
            await UiAutomationTransport._select_image_model(page, Model.IMAGEN_3_5)

        mock_log.debug.assert_called_once()
        mock_log.info.assert_called_once()
        page.keyboard.press.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_selectors_fail_logs_warning_and_presses_escape(self) -> None:
        """All selectors raise → RuntimeError caught, warning logged, Escape pressed."""
        page = _make_model_page_mock(selector_side_effects=[[Exception("timeout")]])
        with patch("gflow_cli.api.transports.ui_automation.log") as mock_log:
            await UiAutomationTransport._select_image_model(page, Model.GEM_PIX_2)
        mock_log.warning.assert_called()
        warned_event = mock_log.warning.call_args[0][0]
        assert warned_event == "ui_automation.image_model_not_set"
        page.keyboard.press.assert_called_once_with("Escape")

    @pytest.mark.asyncio
    async def test_unknown_model_logs_warning_and_returns(self) -> None:
        """Model not in dict → early return with image_model_unknown warning."""
        page = MagicMock()
        page.wait_for_timeout = AsyncMock()
        with patch("gflow_cli.api.transports.ui_automation.log") as mock_log:
            with patch(
                "gflow_cli.api.transports.ui_automation.IMAGE_MODEL_OPTION_SELECTORS",
                {},
            ):
                await UiAutomationTransport._select_image_model(page, Model.NARWHAL)
        mock_log.warning.assert_called_once()
        assert mock_log.warning.call_args[0][0] == "ui_automation.image_model_unknown"
        page.locator.assert_not_called()


def _exit_agent_page(initial: dict) -> tuple[MagicMock, dict]:
    """Build a stateful page mock for ``_exit_agent_mode``.

    ``initial`` seeds a mutable DOM state dict with keys ``crop`` / ``pill`` /
    ``chat`` (each an element count). The click handlers mutate that state to
    model Flow's real transitions, so the helper's loop is exercised against a
    DOM that actually changes shape:

    * clicking the **chat-close** removes the chat panel and reveals the pill
      (``chat`` → 0, ``pill`` → 1), matching the live behaviour;
    * clicking the **pill** re-mounts the media panel (``crop`` → 1).

    Override either transition by passing ``crop_after_pill`` /
    ``pill_after_chat`` / ``chat_after_chat`` in ``initial``. Returns
    ``(page, state)`` so a test can assert final counts + click tallies (the
    state dict also accrues ``pill_clicks`` / ``chat_clicks``).
    """
    from gflow_cli.api.transports import mode_control
    from gflow_cli.api.transports.ui_automation_video import (
        COMPOSER_AGENT_TOGGLE_SELECTOR,
    )

    state = {
        "crop": 0,
        "pill": 0,
        "chat": 0,
        "pill_clicks": 0,
        "chat_clicks": 0,
        "crop_after_pill": 1,
        "pill_after_chat": 1,
        "chat_after_chat": 0,
        # The Agent toggle's aria-pressed (the mode-control source of truth).
        # "true" = agent-on; a pill click flips it to "false" (binary toggle).
        "agent_pressed": "true",
        **initial,
    }

    async def _pill_click(*_a, **_k) -> None:
        state["pill_clicks"] += 1
        state["agent_pressed"] = "false"  # binary toggle flips off
        state["crop"] = state["crop_after_pill"]

    async def _chat_click(*_a, **_k) -> None:
        state["chat_clicks"] += 1
        state["chat"] = state["chat_after_chat"]
        state["pill"] = state["pill_after_chat"]

    def _loc(key: str, on_click=None, aria_key: str | None = None) -> MagicMock:
        loc = MagicMock()
        loc.first = loc
        loc.count = AsyncMock(side_effect=lambda: state[key])
        loc.click = AsyncMock(side_effect=on_click) if on_click else AsyncMock()
        loc.get_attribute = AsyncMock(
            side_effect=lambda name: (
                state[aria_key] if aria_key and name == "aria-pressed" else None
            )
        )
        return loc

    def locator(sel: str) -> MagicMock:
        if sel == mode_control.AGENT_TOGGLE_SELECTOR:
            return _loc("pill", _pill_click, aria_key="agent_pressed")
        if sel == mode_control.SIDEBAR_CLOSE_SELECTOR:
            return _loc("chat", _chat_click)
        # The legacy pill selector is still a "still-stuck" indicator consulted by
        # _check_forced_agentic_ui (AGENTIC_UI_INDICATORS) — map it to the pill so
        # the forced-agentic escalation fires when recovery leaves the pill up.
        if sel == COMPOSER_AGENT_TOGGLE_SELECTOR:
            return _loc("pill")
        for k in state:
            if isinstance(state[k], int) and k in sel:
                return _loc(k)
        return _loc("crop")  # any crop_* MODE_SWITCH_TRIGGER probe

    page = AsyncMock()
    page.locator = MagicMock(side_effect=locator)
    page.wait_for_timeout = AsyncMock()
    return page, state


class TestExitAgentMode:
    """``_exit_agent_mode`` restores the media panel when Flow's composer is in
    "Agent" mode — without matching any localized UI string or aria attribute
    (issue #24 locale discipline + the aria-selector pushback in past reviews)."""

    def test_toggle_selector_is_locale_safe_aria_free_and_scoped(self) -> None:
        """The toggle selector is locale-safe, aria-free, AND scoped to the composer.

        Guards the three review concerns at once:

        * **Locale (issue #24):** no visible-text match (``:has-text`` /
          ``:text-is`` / ``text-matches``) and the literal label "Agent" never
          appears — it is translated per Flow locale. The only ``:text(...)`` is
          ``arrow_forward``, a Material Symbols icon ligature, which is
          locale-invariant (same technique the module uses for ``crop_*``).
        * **No ARIA:** aria-* anchors were rejected in past reviews; none here.
        * **Scoped (PR #124 must-fix):** the pill is matched only inside the
          composer holding the Slate prompt box, so ``.first`` cannot grab an
          unrelated ``span.content`` button added elsewhere in a future build.
        """
        sel = COMPOSER_AGENT_TOGGLE_SELECTOR
        # Structural pill marker.
        assert "span.content" in sel
        # Locale-safe: no aria, no visible-text engines, no literal label.
        assert "aria-" not in sel
        assert ":has-text(" not in sel
        assert ":text-is(" not in sel
        assert "text-matches" not in sel
        assert "Agent" not in sel and "agent" not in sel
        # The only :text() permitted is the locale-invariant icon ligature.
        assert sel.count(":text(") == 1
        assert ":text('arrow_forward')" in sel
        # Scoped to the prompt-box composer — not the bare global selector.
        assert "data-slate-editor" in sel
        assert sel.strip() != "button:has(span.content)"

    def test_chat_close_selector_is_locale_safe_and_aria_free(self) -> None:
        """The chat-panel close selector is structural — no UI text, no ARIA.

        It anchors on the panel header's New-session (``edit_square``) + close
        (``close``) Material Symbols ligatures, using ``:text-is`` (EXACT) so it
        does NOT also match the sidebar's ``left_panel_close`` ligature. No
        localized text and no ``aria-`` attribute (same discipline as the pill).
        """
        from gflow_cli.api.transports.ui_automation_video import (
            AGENT_CHAT_PANEL_CLOSE_SELECTOR,
        )

        sel = AGENT_CHAT_PANEL_CLOSE_SELECTOR
        assert "aria-" not in sel
        assert ":has-text(" not in sel
        assert "text-matches" not in sel
        # Exact icon-ligature matches only (avoids left_panel_close substring).
        assert ":text-is('edit_square')" in sel
        assert ":text-is('close')" in sel
        assert ":text('close')" not in sel  # would over-match left_panel_close
        # Both icons are qualified to the Material Symbols font (``google-symbols``),
        # matching the rest of the module's ligature discipline so a bare
        # ``<i>close</i>`` text node outside the icon font can never match (#139).
        assert "i.google-symbols:text-is('edit_square')" in sel
        assert "i.google-symbols:text-is('close')" in sel

    @pytest.mark.asyncio
    async def test_noop_when_media_panel_present(self) -> None:
        """The crop_* media-settings trigger is mounted → media mode already, so
        the helper returns False and never touches any toggle (common path)."""
        page, state = _exit_agent_page({"crop": 1, "pill": 1, "chat": 0})

        switched = await UiAutomationTransport._exit_agent_mode(page)

        assert switched is False
        page.wait_for_timeout.assert_not_awaited()
        assert state["pill_clicks"] == 0
        assert state["chat_clicks"] == 0

    @pytest.mark.asyncio
    async def test_clicks_pill_and_confirms_panel_remounts(self) -> None:
        """State B (pill active): panel absent + pill present → click the pill
        once; when crop_* comes back, return True."""
        page, state = _exit_agent_page(
            {"crop": 0, "pill": 1, "chat": 0, "crop_after_pill": 1},
        )

        switched = await UiAutomationTransport._exit_agent_mode(page)

        assert switched is True
        assert state["pill_clicks"] == 1
        assert state["chat_clicks"] == 0

    @pytest.mark.asyncio
    async def test_closes_chat_panel_then_clicks_revealed_pill(self) -> None:
        """State A (chat side-panel): panel absent, pill NOT in DOM, chat X
        present → close the chat (reveals the pill), then click the pill →
        crop_* returns. Covers the panel-then-pill transition in one call."""
        page, state = _exit_agent_page(
            {
                "crop": 0,
                "pill": 0,  # pill suppressed while chat panel is up
                "chat": 1,
                "pill_after_chat": 1,  # closing chat reveals the pill
                "chat_after_chat": 0,
                "crop_after_pill": 1,  # clicking the pill re-mounts the panel
            },
        )

        switched = await UiAutomationTransport._exit_agent_mode(page)

        assert switched is True
        assert state["chat_clicks"] == 1
        assert state["pill_clicks"] == 1

    @pytest.mark.asyncio
    async def test_pill_clicked_at_most_once_when_panel_never_remounts(self) -> None:
        """State B but the click does NOT re-mount the panel → click the pill
        exactly ONCE (never flip-flop the binary toggle) and raise FlowAgentUiError."""
        page, state = _exit_agent_page(
            {"crop": 0, "pill": 1, "chat": 0, "crop_after_pill": 0},
        )

        from gflow_cli.errors import FlowAgentUiError

        with pytest.raises(FlowAgentUiError):
            await UiAutomationTransport._exit_agent_mode(page)

        assert state["pill_clicks"] == 1  # NOT 2/3 — no flip-flop

    @pytest.mark.asyncio
    async def test_raises_flow_agent_ui_error_on_forced_agent_mode(self) -> None:
        """If crop is absent but forced agentic UI indicators are present (e.g. tune icon),
        raise FlowAgentUiError immediately after exit loop."""
        page, state = _exit_agent_page(
            {"crop": 0, "pill": 0, "chat": 0, "tune": 1},
        )

        from gflow_cli.errors import FlowAgentUiError

        with pytest.raises(FlowAgentUiError) as exc_info:
            await UiAutomationTransport._exit_agent_mode(page)

        assert "Agentic UI detected" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_noop_when_no_affordance_present(self) -> None:
        """Crop absent AND no pill AND no chat panel (older UI / unknown shape)
        → clean no-op, nothing clicked, returns False."""
        page, state = _exit_agent_page({"crop": 0, "pill": 0, "chat": 0})

        switched = await UiAutomationTransport._exit_agent_mode(page)

        assert switched is False
        assert state["pill_clicks"] == 0
        assert state["chat_clicks"] == 0

    @pytest.mark.asyncio
    async def test_never_raises_on_probe_error(self) -> None:
        """A DOM probe failure is swallowed (best-effort) and returns False —
        a transient editor error must not abort generation."""
        page = AsyncMock()
        page.locator = MagicMock(side_effect=RuntimeError("execution context destroyed"))

        switched = await UiAutomationTransport._exit_agent_mode(page)

        assert switched is False

    def test_scope_excludes_a_decoy_span_content_button_outside_composer(self) -> None:
        """Structural guard for the PR #124 must-fix: the scoped selector must
        match the Agent pill *inside the composer* and exclude a
        ``button > span.content`` that lives elsewhere on the page (a future Flow
        build could add one in the header/sidebar).

        Playwright's ``:has()`` / ``:text()`` pseudo-classes can only be resolved
        by a real browser (covered live by the e2e's ``count() == 1`` assert), so
        this pins the same invariant the selector encodes against a hand-written
        DOM using the stdlib parser — no browser, runs in CI. It confirms exactly
        one ``span.content`` button sits within the element that holds BOTH the
        Slate prompt box and the ``arrow_forward`` submit (the composer), while a
        decoy in the page header is seen as outside.
        """
        from html.parser import HTMLParser

        html = (
            '<header><button><span class="content">Sidebar</span></button></header>'
            '<div class="composer">'
            '<div role="textbox" data-slate-editor="true"></div>'
            '<button><span class="content">Agent</span></button>'
            '<button><i class="google-symbols">arrow_forward</i></button>'
            "</div>"
        )

        class _Counter(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.depth = 0
                self.composer_depth: int | None = None
                self.in_button = 0
                self.cur_has_span_content = False
                self.cur_in_composer = False
                self.pill_in_composer = 0
                self.pill_outside = 0

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                a = dict(attrs)
                self.depth += 1
                if tag == "div" and a.get("class") == "composer":
                    self.composer_depth = self.depth
                if tag == "button":
                    self.in_button += 1
                    self.cur_has_span_content = False
                    self.cur_in_composer = (
                        self.composer_depth is not None and self.depth > self.composer_depth
                    )
                if tag == "span" and self.in_button and "content" in (a.get("class") or "").split():
                    self.cur_has_span_content = True

            def handle_endtag(self, tag: str) -> None:
                if tag == "button" and self.in_button:
                    if self.cur_has_span_content:
                        if self.cur_in_composer:
                            self.pill_in_composer += 1
                        else:
                            self.pill_outside += 1
                    self.in_button -= 1
                if tag == "div" and self.depth == self.composer_depth:
                    self.composer_depth = None
                self.depth -= 1

        counter = _Counter()
        counter.feed(html)

        assert counter.pill_in_composer == 1, "expected one span.content pill inside the composer"
        assert counter.pill_outside == 1, "the header decoy must be seen as outside the composer"
        # The production selector is the scoped form that achieves this.
        assert "data-slate-editor" in COMPOSER_AGENT_TOGGLE_SELECTOR
        assert "arrow_forward" in COMPOSER_AGENT_TOGGLE_SELECTOR


class TestModeSwitchExitsAgentFirst:
    """Both mode switches must call ``_exit_agent_mode`` BEFORE probing for the
    ``crop_*`` trigger — otherwise an Agent-mode composer (panel removed) makes
    the trigger probe fail. The image path is exercised live by the e2e; these
    cheap mock tests pin the call-site ordering for BOTH paths in CI (PR #124)."""

    @pytest.mark.asyncio
    async def test_switch_to_image_mode_exits_agent_first(self) -> None:
        page = AsyncMock()
        page.keyboard = AsyncMock()
        order: list[str] = []

        async def _exit(_p: object, **_kw: object) -> bool:
            order.append("exit_agent")
            return True

        async def _probe(_p: object, _label: str, _sels: object) -> MagicMock:
            order.append("probe")
            trigger = MagicMock()
            trigger.click = AsyncMock()
            return trigger

        # The mode switches reference the helpers on VideoGenerationMixin
        # directly (the base class), so patch there — patching the subclass would
        # not intercept the base-class lookup.
        with (
            patch.object(VideoGenerationMixin, "_exit_agent_mode", new=_exit),
            patch.object(VideoGenerationMixin, "_probe_selector_cascade", new=_probe),
        ):
            await UiAutomationTransport._switch_to_image_mode(page)

        assert order and order[0] == "exit_agent", f"expected exit_agent first, got {order}"

    @pytest.mark.asyncio
    async def test_switch_to_video_mode_exits_agent_first(self) -> None:
        page = AsyncMock()
        order: list[str] = []

        async def _exit(_p: object, **_kw: object) -> bool:
            order.append("exit_agent")
            return True

        async def _probe(_p: object, _label: str, _sels: object) -> MagicMock:
            order.append("probe")
            loc = MagicMock()
            loc.click = AsyncMock()
            return loc

        with (
            patch.object(VideoGenerationMixin, "_exit_agent_mode", new=_exit),
            patch.object(VideoGenerationMixin, "_probe_selector_cascade", new=_probe),
        ):
            await UiAutomationTransport._switch_to_video_mode(page, out_dir=None)

        assert order and order[0] == "exit_agent", f"expected exit_agent first, got {order}"


class _StopFlowError(Exception):
    """Sentinel to halt _generate_images_locked right after the cohort branch."""


@pytest.mark.asyncio
async def test_instructions_infer_agentic_required_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``-i`` instructions are agentic-only, so the transport must REQUIRE the
    agentic arm (get_ui_driver ui_mode=AGENTIC). The switch/verify/fail-fast is
    then owned by get_ui_driver (see test_ui_mode.py) — the transport never
    binds classic with instructions, so the old silent-drop warning is gone."""
    from gflow_cli.api.transports.drivers import factory as _factory
    from gflow_cli.config import UiMode, reset_settings

    monkeypatch.delenv("GFLOW_CLI_UI_MODE", raising=False)
    reset_settings()

    t = UiAutomationTransport()
    t._page = MagicMock()  # noqa: SLF001
    t._out_dir = None  # noqa: SLF001
    monkeypatch.setattr(t, "_enter_editor", AsyncMock())
    monkeypatch.setattr(t, "_dismiss_blocking_overlays", AsyncMock())

    agentic_driver = MagicMock()
    agentic_driver.name = "agentic"
    agentic_driver.switch_to_image_mode = AsyncMock(side_effect=_StopFlowError)
    get_driver = AsyncMock(return_value=agentic_driver)
    monkeypatch.setattr(_factory, "get_ui_driver", get_driver)

    req = GenerateImageRequest(
        prompt="a red apple",
        instructions=(AgentInstruction(text="crayon style", enabled=True),),
    )

    with pytest.raises(_StopFlowError):
        await t._generate_images_locked(req)  # noqa: SLF001

    assert get_driver.await_args.kwargs["ui_mode"] is UiMode.AGENTIC


@pytest.mark.asyncio
async def test_no_instructions_defaults_to_auto_ui_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No instructions and no explicit mode → the transport requires nothing
    (ui_mode=AUTO): bind whatever renders."""
    from gflow_cli.api.transports.drivers import factory as _factory
    from gflow_cli.config import UiMode, reset_settings

    for var in ("GFLOW_CLI_UI_MODE", "GFLOW_CLI_PREFER_CLASSIC", "GFLOW_CLI_FORCE_AGENT_UI"):
        monkeypatch.delenv(var, raising=False)
    reset_settings()

    t = UiAutomationTransport()
    t._page = MagicMock()  # noqa: SLF001
    t._out_dir = None  # noqa: SLF001
    monkeypatch.setattr(t, "_enter_editor", AsyncMock())
    monkeypatch.setattr(t, "_dismiss_blocking_overlays", AsyncMock())

    classic_driver = MagicMock()
    classic_driver.name = "classic"
    classic_driver.switch_to_image_mode = AsyncMock(side_effect=_StopFlowError)
    get_driver = AsyncMock(return_value=classic_driver)
    monkeypatch.setattr(_factory, "get_ui_driver", get_driver)

    req = GenerateImageRequest(prompt="a red apple")

    with pytest.raises(_StopFlowError):
        await t._generate_images_locked(req)  # noqa: SLF001

    assert get_driver.await_args.kwargs["ui_mode"] is UiMode.AUTO


def _force_agent_page(
    tune_counts: list[int], *, toggle_raises: bool = False, expand_count: int = 0
) -> tuple[MagicMock, MagicMock]:
    """Page mock for _force_agent_mode: the ``tune`` indicator count is consumed
    from ``tune_counts`` per query (last value repeats); toggle + expand are
    AsyncMock-driven. Returns (page, toggle_first_locator)."""
    idx = {"i": 0}

    async def _tune_count() -> int:
        i = idx["i"]
        idx["i"] = i + 1
        return tune_counts[i] if i < len(tune_counts) else tune_counts[-1]

    tune_loc = MagicMock()
    tune_loc.count = _tune_count
    toggle_first = MagicMock()
    toggle_first.wait_for = AsyncMock(
        side_effect=RuntimeError("not found") if toggle_raises else None
    )
    toggle_first.click = AsyncMock()
    toggle_loc = MagicMock(first=toggle_first)
    expand_first = MagicMock()
    expand_first.count = AsyncMock(return_value=expand_count)
    expand_first.click = AsyncMock()
    expand_loc = MagicMock(first=expand_first)

    def _locator(sel: str) -> MagicMock:
        if "tune" in sel:
            return tune_loc
        if "expand_content" in sel:
            return expand_loc
        return toggle_loc

    page = MagicMock()
    page.locator = MagicMock(side_effect=_locator)
    page.wait_for_timeout = AsyncMock()
    return page, toggle_first


@pytest.mark.asyncio
async def test_force_agent_mode_already_active_no_click() -> None:
    """Already agentic (tune present) → returns True without clicking the toggle."""
    page, toggle = _force_agent_page([1])
    assert await UiAutomationTransport._force_agent_mode(page) is True  # noqa: SLF001
    toggle.click.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_agent_mode_succeeds_after_render_poll() -> None:
    """The tune indicator renders a beat after the click — polling catches it."""
    # [already-active check=0, poll#1=0, poll#2=1]
    page, toggle = _force_agent_page([0, 0, 1])
    assert await UiAutomationTransport._force_agent_mode(page) is True  # noqa: SLF001
    toggle.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_agent_mode_retries_then_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never activates → re-clicks up to the max, then returns False."""
    monkeypatch.setattr("gflow_cli.api.transports.ui_automation._AGENT_ACTIVATE_TIMEOUT_S", 0.05)
    page, toggle = _force_agent_page([0])  # always 0
    assert await UiAutomationTransport._force_agent_mode(page) is False  # noqa: SLF001
    assert toggle.click.await_count == 2  # noqa: PLR2004 — _AGENT_FORCE_MAX_CLICKS


@pytest.mark.asyncio
async def test_force_agent_mode_returns_false_when_toggle_missing() -> None:
    """Toggle never becomes visible → returns False without clicking."""
    page, toggle = _force_agent_page([0], toggle_raises=True)
    assert await UiAutomationTransport._force_agent_mode(page) is False  # noqa: SLF001
    toggle.click.assert_not_awaited()


class TestReferenceEntitiesInterception:
    """Tests the _intercept_reference_entities context manager's ability to
    filter/strip referenceEntities from outgoing HTTP request bodies.
    """

    @pytest.mark.asyncio
    async def test_intercept_reference_entities_strips_unrequested(self) -> None:
        transport = UiAutomationTransport()
        mock_page = MagicMock()
        mock_page.route = AsyncMock()
        mock_page.unroute = AsyncMock()

        expected = {"requested-character-id"}

        async with transport._intercept_reference_entities(mock_page, expected):  # noqa: SLF001
            # Verify routes were registered
            mock_page.route.assert_any_call("**/batchGenerateImages", ANY)
            mock_page.route.assert_any_call("**/batchAsyncGenerateVideo*", ANY)

        # Verify unroute was called
        mock_page.unroute.assert_any_call("**/batchGenerateImages")
        mock_page.unroute.assert_any_call("**/batchAsyncGenerateVideo*")

        # Now test the route handler logic
        intercept_handler = mock_page.route.call_args_list[0][0][1]

        # Case 1: unrequested entity (should be stripped)
        mock_route = MagicMock()
        mock_route.request.post_data = json.dumps(
            {
                "requests": [
                    {
                        "prompt": "some prompt",
                        "referenceEntities": [{"entityId": "poisoned-character-id"}],
                    }
                ]
            }
        )
        mock_route.continue_ = AsyncMock()

        await intercept_handler(mock_route)

        mock_route.continue_.assert_awaited_once()
        sent_body = json.loads(mock_route.continue_.call_args[1]["post_data"])
        # Should be stripped entirely
        assert "referenceEntities" not in sent_body["requests"][0]

        # Case 2: requested entity (should be kept)
        mock_route = MagicMock()
        mock_route.request.post_data = json.dumps(
            {
                "requests": [
                    {
                        "prompt": "some prompt",
                        "referenceEntities": [{"entityId": "requested-character-id"}],
                    }
                ]
            }
        )
        mock_route.continue_ = AsyncMock()

        await intercept_handler(mock_route)

        # If unmodified, continue_ is called with no post_data argument
        mock_route.continue_.assert_awaited_once_with()
