"""Tests for S1 EvaluateFetchTransport — TDD RED phase.

Covers:
- setup() launches playwright persistent context and navigates to FLOW_URL
- generate_images() uses page.evaluate with fetch() returning images
- HTTP 401 triggers refresh_auth() then retries; if still 401 raises AuthExpiredError
- HTTP 403 raises WafRejectionError
- 30s timeout raises TransportTimeoutError
- teardown() is idempotent (safe to call multiple times)
- name class attribute is "evaluate_fetch"
- partial-setup failure triggers teardown() cleanup (resource-leak guard)
- seed is deterministic for the same ref name (stable sha256 digest)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.image import Aspect, GenerateImageRequest, ImageRef, Model
from gflow_cli.api.transports.evaluate_fetch import EvaluateFetchTransport
from gflow_cli.errors import AuthExpiredError, TransportTimeoutError, WafRejectionError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _req(prompt: str = "a motivational sunrise") -> GenerateImageRequest:
    """Build a minimal GenerateImageRequest using real field names."""
    return GenerateImageRequest(
        prompt=prompt,
        model=Model.NARWHAL,
        aspect=Aspect.PORTRAIT,
        recaptcha_token="recap_token_x",
    )


def _flow_200_body() -> str:
    """Return a minimal valid batchGenerateImages 200 response body."""
    return json.dumps({
        "media": [
            {
                "name": "projects/proj-uuid/assets/asset-001",
                "workflowId": "wf-001",
                "image": {
                    "generatedImage": {
                        "seed": 42,
                        "prompt": "a motivational sunrise",
                        "modelNameType": "NARWHAL",
                        "aspectRatio": "IMAGE_ASPECT_RATIO_PORTRAIT",
                        "fifeUrl": "https://lh3.googleusercontent.com/abc123",
                    },
                    "dimensions": {"width": 576, "height": 1024},
                },
            }
        ],
        "workflows": [],
    })


class _AsyncCtxManager:
    """Minimal async context manager that returns `val` on __aenter__."""

    def __init__(self, val: object) -> None:
        self._val = val

    async def __aenter__(self) -> object:
        return self._val

    async def __aexit__(self, *args: object) -> None:
        pass


def _make_fake_playwright(fake_ctx: MagicMock) -> MagicMock:
    """Build a minimal fake playwright object whose chromium context is fake_ctx."""
    fake_pw = MagicMock()
    fake_pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_ctx)
    return fake_pw


# ---------------------------------------------------------------------------
# T1 — class attribute
# ---------------------------------------------------------------------------


def test_name_class_attribute() -> None:
    """EvaluateFetchTransport.name must equal 'evaluate_fetch'."""
    assert EvaluateFetchTransport.name == "evaluate_fetch"


# ---------------------------------------------------------------------------
# T2 — setup()
# ---------------------------------------------------------------------------


async def test_setup_launches_playwright_persistent_context(tmp_path: Path) -> None:
    """setup() calls launch_persistent_context with the profile dir and navigates."""
    transport = EvaluateFetchTransport()

    fake_page = MagicMock()
    fake_page.goto = AsyncMock()

    fake_ctx = MagicMock()
    fake_ctx.new_page = AsyncMock(return_value=fake_page)

    fake_pw = _make_fake_playwright(fake_ctx)

    # async_playwright is lazily imported inside setup(), so patch the source module.
    with patch(
        "playwright.async_api.async_playwright",
        return_value=_AsyncCtxManager(fake_pw),
    ):
        await transport.setup(tmp_path)

    fake_pw.chromium.launch_persistent_context.assert_awaited_once()
    call_args = fake_pw.chromium.launch_persistent_context.call_args
    # profile_dir must be the first positional arg (str(profile_dir))
    assert call_args.args[0] == str(tmp_path)

    # Page navigation to FLOW_URL must have happened
    fake_page.goto.assert_awaited_once()
    nav_url = fake_page.goto.call_args.args[0]
    assert "labs.google" in nav_url


async def test_setup_is_idempotent(tmp_path: Path) -> None:
    """Calling setup() a second time is a no-op (does not re-launch playwright)."""
    transport = EvaluateFetchTransport()

    fake_page = MagicMock()
    fake_page.goto = AsyncMock()
    fake_ctx = MagicMock()
    fake_ctx.new_page = AsyncMock(return_value=fake_page)
    fake_pw = _make_fake_playwright(fake_ctx)

    with patch(
        "playwright.async_api.async_playwright",
        return_value=_AsyncCtxManager(fake_pw),
    ):
        await transport.setup(tmp_path)
        await transport.setup(tmp_path)  # second call

    # launch_persistent_context called exactly once despite two setup() calls
    assert fake_pw.chromium.launch_persistent_context.await_count == 1


# ---------------------------------------------------------------------------
# T3 — generate_images() happy path
# ---------------------------------------------------------------------------


async def test_generate_images_uses_page_evaluate_fetch() -> None:
    """generate_images() must call page.evaluate() and return parsed GeneratedImage list."""
    transport = EvaluateFetchTransport()

    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(
        return_value={"status": 200, "body": _flow_200_body()}
    )
    transport._page = fake_page  # type: ignore[attr-defined]
    transport._setup_done = True  # type: ignore[attr-defined]

    images = await transport.generate_images(project_id="proj-uuid", request=_req())

    fake_page.evaluate.assert_awaited_once()
    # The JS snippet must use fetch with credentials:'include'
    js_snippet: str = fake_page.evaluate.call_args.args[0]
    assert "fetch" in js_snippet
    assert "credentials" in js_snippet

    assert len(images) == 1
    assert images[0].fife_url == "https://lh3.googleusercontent.com/abc123"
    assert images[0].media_name == "projects/proj-uuid/assets/asset-001"


# ---------------------------------------------------------------------------
# T4 — HTTP 401 → refresh once → AuthExpiredError
# ---------------------------------------------------------------------------


async def test_generate_images_401_calls_refresh_then_raises_auth_expired() -> None:
    """On 401, refresh_auth() is called; if it raises AuthExpiredError, that propagates."""
    transport = EvaluateFetchTransport()

    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value={"status": 401, "body": "{}"})
    transport._page = fake_page  # type: ignore[attr-defined]
    transport._setup_done = True  # type: ignore[attr-defined]

    # refresh_auth raises immediately (simulates re-nav also failing)
    transport.refresh_auth = AsyncMock(side_effect=AuthExpiredError("expired"))  # type: ignore[method-assign]

    with pytest.raises(AuthExpiredError):
        await transport.generate_images(project_id="proj", request=_req())

    transport.refresh_auth.assert_awaited_once()


async def test_generate_images_401_retries_once_then_raises_if_still_401() -> None:
    """After a successful refresh, a second 401 raises AuthExpiredError (no infinite loop)."""
    transport = EvaluateFetchTransport()

    # Both calls return 401
    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value={"status": 401, "body": "{}"})
    transport._page = fake_page  # type: ignore[attr-defined]
    transport._setup_done = True  # type: ignore[attr-defined]

    # refresh_auth succeeds (no exception) — but second call still 401
    refresh_mock = AsyncMock()
    transport.refresh_auth = refresh_mock  # type: ignore[method-assign]

    with pytest.raises(AuthExpiredError):
        await transport.generate_images(project_id="proj", request=_req())

    # refresh_auth called once (not infinitely)
    refresh_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# T5 — HTTP 403 → WafRejectionError
# ---------------------------------------------------------------------------


async def test_generate_images_403_raises_waf_rejection() -> None:
    """HTTP 403 from the page.evaluate fetch must raise WafRejectionError."""
    transport = EvaluateFetchTransport()

    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value={"status": 403, "body": "access denied"})
    transport._page = fake_page  # type: ignore[attr-defined]
    transport._setup_done = True  # type: ignore[attr-defined]

    with pytest.raises(WafRejectionError):
        await transport.generate_images(project_id="proj", request=_req())


# ---------------------------------------------------------------------------
# T6 — 30s timeout → TransportTimeoutError
# ---------------------------------------------------------------------------


async def test_generate_images_30s_timeout_raises_transport_timeout() -> None:
    """page.evaluate() hanging > 30s must raise TransportTimeoutError."""
    transport = EvaluateFetchTransport()

    async def _hang(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(9999)

    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(side_effect=_hang)
    transport._page = fake_page  # type: ignore[attr-defined]
    transport._setup_done = True  # type: ignore[attr-defined]

    # Patch PER_CALL_TIMEOUT_S to a tiny value so the test is fast
    with patch("gflow_cli.api.transports.evaluate_fetch.PER_CALL_TIMEOUT_S", 0.05):
        with pytest.raises(TransportTimeoutError):
            await transport.generate_images(project_id="proj", request=_req())


# ---------------------------------------------------------------------------
# T7 — teardown() idempotency
# ---------------------------------------------------------------------------


async def test_teardown_is_idempotent() -> None:
    """teardown() called multiple times must not raise."""
    transport = EvaluateFetchTransport()

    fake_page = MagicMock()
    fake_page.goto = AsyncMock()
    fake_ctx = MagicMock()
    fake_ctx.new_page = AsyncMock(return_value=fake_page)
    fake_ctx.close = AsyncMock()
    fake_pw = _make_fake_playwright(fake_ctx)

    fake_pw_cm = MagicMock()
    fake_pw_cm.__aenter__ = AsyncMock(return_value=fake_pw)
    fake_pw_cm.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "playwright.async_api.async_playwright",
        return_value=_AsyncCtxManager(fake_pw),
    ):
        await transport.setup(Path("/tmp/profile"))

    # Manually set _pw_cm so teardown can close it
    transport._pw_cm = fake_pw_cm  # type: ignore[attr-defined]

    await transport.teardown()
    await transport.teardown()  # second call — must not raise


async def test_teardown_before_setup_does_not_raise() -> None:
    """teardown() on a never-setup transport must be a no-op."""
    transport = EvaluateFetchTransport()
    await transport.teardown()  # should not raise


# ---------------------------------------------------------------------------
# T8 — HIGH #1: partial-setup resource leak guard
# ---------------------------------------------------------------------------


async def test_setup_partial_failure_calls_teardown(tmp_path: Path) -> None:
    """If ctx.new_page() raises mid-setup, teardown() must be invoked to release
    the already-opened playwright/context handles (no leak)."""
    transport = EvaluateFetchTransport()

    fake_ctx = MagicMock()
    # Simulate failure at ctx.new_page()
    fake_ctx.new_page = AsyncMock(side_effect=RuntimeError("simulated new_page failure"))
    fake_ctx.close = AsyncMock()

    fake_pw = _make_fake_playwright(fake_ctx)

    fake_pw_cm = MagicMock()
    fake_pw_cm.__aenter__ = AsyncMock(return_value=fake_pw)
    fake_pw_cm.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "playwright.async_api.async_playwright",
        return_value=fake_pw_cm,
    ):
        with pytest.raises(RuntimeError, match="simulated new_page failure"):
            await transport.setup(tmp_path)

    # pw_cm.__aexit__ must have been called (playwright closed, no leak)
    fake_pw_cm.__aexit__.assert_awaited_once()
    # Transport state must be fully reset
    assert transport._pw_cm is None
    assert transport._ctx is None
    assert transport._page is None
    assert transport._setup_done is False


# ---------------------------------------------------------------------------
# T9 — MEDIUM #10: deterministic seed (sha256-based)
# ---------------------------------------------------------------------------


async def test_generate_images_seed_is_deterministic_for_same_ref() -> None:
    """The seed passed to _build_batch_generate_images_body must be identical
    across two calls with the same request.refs[0].name (sha256-stable)."""
    captured_seeds: list[int] = []

    def _capture_seed(*args: object, **kwargs: object) -> dict:  # type: ignore[return]
        captured_seeds.append(kwargs["seed"])
        return {}  # minimal — generate_images will fail later but that's OK

    req_with_ref = GenerateImageRequest(
        prompt="sunrise",
        model=Model.NARWHAL,
        aspect=Aspect.PORTRAIT,
        recaptcha_token="tok",
        refs=(ImageRef(name="550e8400-e29b-41d4-a716-446655440000"),),
    )

    transport = EvaluateFetchTransport()
    fake_page = MagicMock()
    # Return a 200 body so interpret_response can parse it
    fake_page.evaluate = AsyncMock(
        return_value={"status": 200, "body": _flow_200_body()}
    )
    transport._page = fake_page  # type: ignore[attr-defined]
    transport._setup_done = True  # type: ignore[attr-defined]

    with patch(
        "gflow_cli.api.transports.evaluate_fetch._build_batch_generate_images_body",
        side_effect=_capture_seed,
    ):
        try:
            await transport.generate_images(project_id="proj", request=req_with_ref)
        except Exception:
            pass
        try:
            await transport.generate_images(project_id="proj", request=req_with_ref)
        except Exception:
            pass

    assert len(captured_seeds) == 2, "Expected _build_batch_generate_images_body called twice"
    assert captured_seeds[0] == captured_seeds[1], (
        f"Seeds differ across runs: {captured_seeds[0]} vs {captured_seeds[1]}"
    )
