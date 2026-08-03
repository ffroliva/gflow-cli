"""E2E test suite for transport strategy criteria (spec § 8.2–8.4).

These tests hit the **real Flow API** and therefore:
  - Are NOT collected by default ``pytest`` runs.
  - Opt-in: ``GFLOW_CLI_E2E_PROFILE=<profile_name> pytest -m e2e``
  - Require the named Chromium profile to be logged-in (a Pro/Ultra account).
  - Task D.2 drives the real execution; this file is the Task D.1 scaffold.

Criteria covered (spec § 8.4):
  C2 — single image generation returns ≥1 PNG with an https:// URL
  C3 — 5 sequential batches × 4 images = 20 images total
  C4a — recoverable auth expiry: stale credential triggers silent refresh
  C4b — unrecoverable auth expiry: missing profile raises AuthExpiredError /
        AuthMissingError with the correct exit_code

Note: C5 (30-second timeout budget) was moved to
``tests/api/transports/test_transport_timeout.py`` (``integration`` marker)
because it mocks all network I/O and does not require a real API call.

Strategies under test (spec § 8.2):
  evaluate_fetch  — Playwright page.evaluate() passthrough
  bearer          — OAuth 2.0 bearer token cached on disk
  sapisidhash     — SAPISIDHASH cookie + HMAC header
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import structlog

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import GenerateImageRequest, Model
from gflow_cli.api.transports import make_transport
from gflow_cli.api.transports.experimental.bearer import BearerTransport
from gflow_cli.api.transports.experimental.sapisidhash import SapisidhashTransport
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.transports.ui_automation_video import (
    AGENT_CHAT_PANEL_CLOSE_SELECTOR,
    COMPOSER_AGENT_TOGGLE_SELECTOR,
)
from gflow_cli.api.video import (
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoModel,
    VideoResult,
)
from gflow_cli.errors import (
    EXIT_CODE_MAP,
    AuthExpiredError,
    AuthMissingError,
)

# ---------------------------------------------------------------------------
# Module-level marker — every test in this file inherits e2e
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STRATEGIES = ["evaluate_fetch", "bearer", "sapisidhash"]

_PROMPT = "A motivational sunrise over mountains, cinematic, 4K"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(strategy: str, profile: Path) -> FlowApiClient:
    """Construct a FlowApiClient wired to the requested transport strategy.

    Pass `transport=strategy_name` (string) — NOT an instance — so the client
    owns the lifecycle and calls `transport.setup(profile_dir)` in __aenter__.
    Per spec § 4.3, passing a pre-initialized instance signals the caller
    owns lifecycle and the client SKIPS setup. The strategy then refuses
    `generate_images` with AuthMissingError because state is uninitialized.
    """
    return FlowApiClient(profile_dir=profile, transport=strategy)


# ---------------------------------------------------------------------------
# Criterion C2 — single image generation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
@pytest.mark.e2e_image
async def test_e2e_single_image_gen(strategy: str, e2e_profile_dir: Path) -> None:
    """C2: generate_image() returns ≥ 1 GeneratedImage with an https:// fife_url."""
    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL)

    async with _make_client(strategy, e2e_profile_dir) as client:
        project = await client.create_project(title=f"e2e-c2-{strategy}")
        image = await client.generate_image(project_id=project.project_id, req=req)

    assert image.media_name, "media_name must be non-empty"
    assert image.fife_url.startswith("https://"), (
        f"fife_url must be an https:// URL, got: {image.fife_url!r}"
    )


# ---------------------------------------------------------------------------
# Criterion C2 (i2i variant) — local-file reference attach via media dialog (#56)
# ---------------------------------------------------------------------------


def _tiny_png(path: Path) -> Path:
    """Write a valid 8x8 red RGBA PNG (no external asset / Pillow dependency)."""
    import struct
    import zlib

    def _chunk(typ: bytes, data: bytes) -> bytes:
        body = typ + data
        crc = zlib.crc32(body) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + body + struct.pack(">I", crc)

    w = h = 8
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * w for _ in range(h))
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(raw))
    png += _chunk(b"IEND", b"")
    path.write_bytes(png)
    return path


@pytest.mark.asyncio
@pytest.mark.e2e_image
async def test_e2e_i2i_local_ref_attach(
    e2e_profile_dir: Path,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """C2/i2i (#56): generate_image with a LOCAL-FILE ``ref_paths`` binds the
    reference through the editor's media dialog and returns >= 1 image.

    UI-automation transport ONLY — the REST transports (bearer/sapisidhash)
    cannot drive the add-media dialog, so they never invoke ``_attach_references``.
    Routing this through ``evaluate_fetch`` (the old bug) silently drops
    ``ref_paths`` and still returns a text-only image, so the URL asserts alone
    are a false positive — hence the explicit transport + the event assertion.

    This exercises the locale-agnostic media-dialog selectors (icon ``upload`` +
    iconless 'Add to Prompt') that replaced the text-based selectors which hung
    on non-English Chrome profiles. Costs 1 credit when it runs.
    """
    ref = _tiny_png(tmp_path / "ref.png")
    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL, ref_paths=(ref,))

    async with _make_client("ui_automation", e2e_profile_dir) as client:
        image = await client.generate_image(req=req)

    assert image.media_name, "i2i ref-attach returned no image"
    assert image.fife_url.startswith("https://"), (
        f"fife_url must be an https:// URL, got: {image.fife_url!r}"
    )
    # Prove the reference was ACTUALLY attached through the media dialog rather
    # than silently dropped (the #56 false-positive class). ``_attach_references``
    # emits one ``reference_attached`` event per bound ref; its absence means the
    # ref never bound even though an image came back.
    events = [e["event"] for e in install_log_capture.entries]
    assert "ui_automation_video.reference_attached" in events, (
        "expected a 'reference_attached' event proving the local ref bound through "
        f"the media dialog; captured events: {events}"
    )


# ---------------------------------------------------------------------------
# Criterion C2/i2v (#63) — I2V Start + End frame attach via media dialog
# ---------------------------------------------------------------------------


# Defaults are tuned for minimum credit spend: veo-lite, 4 s duration, count=1,
# landscape. Override via env for variation (per [[e2e-tests-parameterize]]).
# NOTE: the i2v default is veo-lite (NOT omni-flash) — the default must
# support the full i2v surface incl. --end-frame (issue #125). Using omni-flash
# here previously made this test a false positive: frame_attached + terminal
# success both held while the actual output was a text-only video.
_E2E_VIDEO_ASPECT_ENV = "GFLOW_CLI_E2E_VIDEO_ASPECT"
_E2E_VIDEO_MODEL_ENV = "GFLOW_CLI_E2E_VIDEO_MODEL"
_E2E_VIDEO_DURATION_ENV = "GFLOW_CLI_E2E_VIDEO_DURATION"
_I2V_POLL_TIMEOUT_S = 600.0
_I2V_PROMPT = "the subject moves gently in a calm scene, cinematic"
# The generate request MUST route here — the start+end frame endpoint. If it
# lands on batchAsyncGenerateVideoText, the frames were dropped (issue #125).
_I2V_EXPECTED_GENERATE_URL_SUBSTR = "batchAsyncGenerateVideoStartAndEndImage"


def _i2v_aspect() -> Aspect:
    raw = os.environ.get(_E2E_VIDEO_ASPECT_ENV, "landscape").strip().lower()
    if raw == "landscape":
        return Aspect.LANDSCAPE
    if raw == "portrait":
        return Aspect.PORTRAIT
    pytest.skip(f"Unsupported {_E2E_VIDEO_ASPECT_ENV}={raw!r}")


def _i2v_model() -> VideoModel | None:
    # veo-lite default (issue #125): omni-flash is start-frame-only for i2v.
    raw = os.environ.get(_E2E_VIDEO_MODEL_ENV, "veo-lite").strip().lower()
    return VideoModel.from_cli(raw)


def _i2v_duration() -> int:
    raw = os.environ.get(_E2E_VIDEO_DURATION_ENV, "4").strip()
    return int(raw)


@pytest.mark.asyncio
@pytest.mark.e2e_video
async def test_e2e_i2v_start_end_frame_attach(
    e2e_profile_dir: Path,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """I2V (#63): generate_video with ``mode=I2V`` + ``start_image`` + ``end_image``
    binds BOTH frames through the editor's media dialog and returns a successful
    ``VideoResult`` with a downloaded mp4 on disk.

    UI-automation transport ONLY — the REST transports drop UI-only DTO fields
    silently (see [[rest-transports-drop-ui-fields]]).

    This exercises the locale-free structural cascade in ``_attach_frame``
    (``FRAME_SLOTS_STRUCT = "div[type='button'][aria-haspopup='dialog']"``).
    PR #70's earlier anchor (``swap_horiz`` icon container) was broken on real
    Flow DOMs and matched ZERO elements; production I2V silently relied on the
    English-text fallback, which fails on non-English Chrome profiles. The
    ``ui_automation_video.frame_attached`` event MUST fire twice (one per slot)
    — its absence indicates the structural tier still misses.

    Costs 1 credit per run (omni-flash, 4 s, count=1 by default). Override:
        GFLOW_CLI_E2E_VIDEO_MODEL=veo-fast
        GFLOW_CLI_E2E_VIDEO_DURATION=6
        GFLOW_CLI_E2E_VIDEO_ASPECT=portrait
    """
    start = _tiny_png(tmp_path / "start.png")
    end = _tiny_png(tmp_path / "end.png")

    req = GenerateVideoRequest(
        prompt=_I2V_PROMPT,
        mode=Mode.I2V,
        aspect=_i2v_aspect(),
        model=_i2v_model(),
        duration=_i2v_duration(),
        count=1,
        start_image=start,
        end_image=end,
    )

    transport = UiAutomationTransport()
    try:
        await transport.setup(e2e_profile_dir)
        result: VideoResult = await transport.generate_video(
            request=req,
            out_dir=tmp_path,
            poll_timeout_s=_I2V_POLL_TIMEOUT_S,
        )
    finally:
        await transport.teardown()

    # 1. Terminal-success contract (mirrors test_video_t2v_e2e).
    assert isinstance(result, VideoResult), (
        f"generate_video() must return a VideoResult, got {type(result)!r}"
    )
    assert result.status.is_terminal and result.status.succeeded, (
        f"Expected SUCCESSFUL terminal status, got {result.status.status!r}; "
        f"failure_reasons={result.status.failure_reasons!r}"
    )
    assert result.status.media_id, "VideoStatus.media_id must be non-empty"

    # 2. File-on-disk contract (per [[verification-ledger-5-layer]]).
    assert result.local_path is not None and result.local_path.exists(), (
        f"VideoResult.local_path must point to a downloaded mp4; got {result.local_path!r}"
    )
    head = result.local_path.read_bytes()[:32]
    assert b"ftyp" in head, (
        f"mp4 magic bytes not found in first 32 bytes of {result.local_path}: {head!r}"
    )

    # 3. Locale-free selector contract (the #63 closure):
    #    _attach_frame must fire `frame_attached` exactly twice — once with
    #    slot=Start and once with slot=End. Its presence proves the structural
    #    cascade resolved both slots without falling through to the text-tier
    #    (which only works on EN profiles). The text-tier would still allow a
    #    successful run on EN but the EVENT would not fire if the cascade had
    #    silently mismatched.
    frame_events = [
        e for e in install_log_capture.entries if e["event"] == "ui_automation_video.frame_attached"
    ]
    slots = {e.get("slot") for e in frame_events}
    assert slots == {"Start", "End"}, (
        f"expected frame_attached for {{Start, End}}, got {slots!r}; "
        f"all events: {[e['event'] for e in install_log_capture.entries]}"
    )

    # 4. Frame-routing contract (issue #125 — the assertion this test was
    #    missing). frame_attached fires even when Flow drops the refs and routes
    #    to T2V, so it cannot prove the frames reached Veo. The captured generate
    #    request URL is the only proof: it MUST be the StartAndEndImage endpoint,
    #    NOT batchAsyncGenerateVideoText. And the model/mode guard must NOT have
    #    fired (a valid veo-lite + i2v combination).
    rejected = [
        e
        for e in install_log_capture.entries
        if e["event"] == "ui_automation_video.model_mode_rejected"
    ]
    assert not rejected, f"model_mode_rejected fired unexpectedly: {rejected!r}"
    generate_events = [
        e
        for e in install_log_capture.entries
        if e["event"] == "ui_automation_video.generate_captured"
    ]
    assert generate_events, "no ui_automation_video.generate_captured event was emitted"
    gen_url = str(generate_events[-1].get("url", ""))
    assert _I2V_EXPECTED_GENERATE_URL_SUBSTR in gen_url, (
        f"i2v generate request routed to {gen_url!r}; expected it to contain "
        f"{_I2V_EXPECTED_GENERATE_URL_SUBSTR!r}. If it landed on "
        f"batchAsyncGenerateVideoText, the start/end frames were dropped "
        f"(issue #125 regression)."
    )


# ---------------------------------------------------------------------------
# Criterion C3 — 5 sequential batches × 4 images = 20 images
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
@pytest.mark.e2e_batch
async def test_e2e_5_sequential_batches(strategy: str, e2e_profile_dir: Path) -> None:
    """C3: 5 sequential generate_images_batch(count=4) calls return 20 images total."""
    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL)
    all_images = []

    async with _make_client(strategy, e2e_profile_dir) as client:
        project = await client.create_project(title=f"e2e-c3-{strategy}")
        for _ in range(5):
            batch = await client.generate_images_batch(
                project_id=project.project_id,
                req=req,
                count=4,
            )
            all_images.extend(batch)

    assert len(all_images) == 20, f"Expected 20 images across 5 batches, got {len(all_images)}"
    for img in all_images:
        assert img.fife_url.startswith("https://"), (
            f"fife_url must be https://, got: {img.fife_url!r}"
        )


# ---------------------------------------------------------------------------
# Criterion C4a — recoverable auth expiry (silent refresh)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
@pytest.mark.e2e_image
async def test_e2e_recoverable_auth_expiry(strategy: str, e2e_profile_dir: Path) -> None:
    """C4a: Deliberately staling the cached credential triggers a silent refresh.

    Strategy-specific staleness injection:
      bearer       — set _cached.expires_at to now - 1 (already expired)
      sapisidhash  — overwrite _sapisid with a garbage value
      evaluate_fetch — no in-process cache; validate that a 401 response from
                       the server triggers a page reload + retry (refresh path)
    """
    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL)
    transport = make_transport(strategy)

    async with FlowApiClient(profile_dir=e2e_profile_dir, transport=transport) as client:
        project = await client.create_project(title=f"e2e-c4a-{strategy}")

        # Inject stale credential AFTER setup so the transport is fully
        # initialised but before the API call so the refresh path fires.
        if strategy == "bearer":
            assert isinstance(transport, BearerTransport)
            if transport._cached is not None:
                # Mutate the expires_at field via object replacement —
                # _CachedAuth is a frozen dataclass so we use dataclasses.replace.
                import dataclasses

                transport._cached = dataclasses.replace(
                    transport._cached,
                    expires_at=time.time() - 1.0,
                )
        elif strategy == "sapisidhash":
            assert isinstance(transport, SapisidhashTransport)
            # Overwrite the in-memory SAPISID with a garbage value.
            # The transport will re-read from the profile on the next 401.
            transport._sapisid = "deliberately_invalidated_sapisid_value"
        # evaluate_fetch: no in-process credential cache; the browser's session
        # cookies handle auth.  No injection needed — we just verify the call
        # succeeds, confirming the transport handles the round-trip correctly.

        # The call must succeed despite the stale / injected credential.
        image = await client.generate_image(project_id=project.project_id, req=req)

    assert image.media_name, "Silent recovery failed — no image returned"
    assert image.fife_url.startswith("https://")


# ---------------------------------------------------------------------------
# Criterion C4b — unrecoverable auth expiry (missing profile)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_e2e_unrecoverable_auth_expiry(
    strategy: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C4b: Pointing at an empty (non-logged-in) profile raises AuthExpiredError
    or AuthMissingError with the expected exit_code.

    ``tmp_path`` is an empty directory — no cookies, no bearer cache file.
    The transport's setup() will fail to find credentials and must raise.
    """
    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL)

    with pytest.raises((AuthExpiredError, AuthMissingError)) as exc_info:
        async with FlowApiClient(
            profile_dir=tmp_path, transport=make_transport(strategy)
        ) as client:
            project = await client.create_project(title=f"e2e-c4b-{strategy}")
            await client.generate_image(project_id=project.project_id, req=req)

    exc = exc_info.value
    # Both error types must carry a non-zero exit_code (see errors.py EXIT_CODE_MAP).
    assert EXIT_CODE_MAP.get(type(exc), 0) != 0, (
        f"{type(exc).__name__} must have a non-zero exit_code in EXIT_CODE_MAP"
    )


# ---------------------------------------------------------------------------
# Auto-create project_id (Issue #16 — optional project_id)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
@pytest.mark.e2e_image
async def test_e2e_generate_image_without_project_id(strategy: str, e2e_profile_dir: Path) -> None:
    """generate_image(req=req) without project_id auto-creates a project.

    Confirms the auto-create path works end-to-end against the real Flow API.
    """
    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL)

    async with _make_client(strategy, e2e_profile_dir) as client:
        # Intentionally omit project_id — the client must create one internally.
        image = await client.generate_image(req=req)

    assert image.media_name, "media_name must be non-empty"
    assert image.fife_url.startswith("https://"), (
        f"fife_url must be an https:// URL, got: {image.fife_url!r}"
    )


# ---------------------------------------------------------------------------
# health_check() (Issue #16 — new method)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_e2e_health_check_returns_true_when_active(
    strategy: str, e2e_profile_dir: Path
) -> None:
    """health_check() returns True for a live browser context on a Google domain."""
    async with _make_client(strategy, e2e_profile_dir) as client:
        result = await client.health_check()

    assert result is True, "health_check() must return True for an active Google-domain page"


# ---------------------------------------------------------------------------
# health_check() false path (Issue #16 — new method)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_e2e_health_check_false_after_close(e2e_profile_dir: Path) -> None:
    """health_check() returns False (never raises) once the client is closed.

    A long-lived worker holding a client whose context has been torn down must
    get a clean False, not an exception. Zero credits — no image generation.

    Not parametrized over STRATEGIES: health_check is transport-agnostic, and
    the bearer / sapisidhash experimental transports fail at setup() (obsolete —
    see KNOWN_ISSUES.md). evaluate_fetch is the live transport.
    """
    client = _make_client("evaluate_fetch", e2e_profile_dir)

    async with client:
        assert await client.health_check() is True, (
            "health_check() must be True while the context is live"
        )

    # Context is now closed.
    assert await client.health_check() is False, (
        "health_check() must return False (not raise) on a closed client"
    )


# ---------------------------------------------------------------------------
# Agent composer mode recovery (create-project gen panel) — zero credits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_e2e_agent_mode_recovered_before_mode_switch(
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """Flow's "Agent" composer mode no longer breaks the generate path.

    Flow's newer editor adds an Agent toggle next to the prompt box. When Agent
    mode is active the whole media-generation panel — including the ``crop_*``
    settings trigger that ``_switch_to_image_mode`` / ``_switch_to_video_mode``
    probe — is removed from the DOM, so the mode switch used to raise
    "mode-switch dropdown trigger not found" and create-project generation
    failed. ``_exit_agent_mode`` (called at the top of the mode switch) must
    re-mount the panel.

    This reproduces the bug on the **real DOM** then proves the fix: enter a
    fresh project, force Agent mode on (panel disappears), call
    ``_switch_to_image_mode``, and assert the ``crop_*`` trigger is back. Costs
    **zero credits** — no generation is submitted; it stops once the panel is
    confirmed re-mounted (the exact precondition the generate path needs).

    Skips on accounts whose Flow UI predates the Agent toggle (older editor).
    """
    # Undo the autouse `_isolate_settings` fixture (tests/conftest.py) so profile
    # lookup resolves to the user's real platformdirs path where the live Chrome
    # session lives — the `e2e_profile_dir` fixture is pinned to tmp by isolation.
    import os

    from gflow_cli.auth import profile_dir as _resolve_profile_dir
    from gflow_cli.config import reset_settings

    monkeypatch.delenv("GFLOW_CLI_HOME", raising=False)
    monkeypatch.delenv("GFLOW_CLI_DB_PATH", raising=False)
    reset_settings()

    name = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    if not name:
        pytest.skip("set GFLOW_CLI_E2E_PROFILE to a logged-in profile name")
    profile = _resolve_profile_dir(name)
    if not profile.exists():
        pytest.skip(f"profile dir not found: {profile}")

    transport = UiAutomationTransport()
    try:
        await transport.setup(profile)
        page = transport._page  # noqa: SLF001 — e2e drives the transport's live page
        assert page is not None, "setup() must acquire a page"

        # Fresh project so the composer is in its default editor state. Mirror
        # the real generate path: enter the editor, dismiss any changelog overlay
        # that can cover the composer (#26), and wait for the prompt box to mount.
        # The editor SPA renders incrementally after the /project/ URL nav (the
        # same reason _wait_video_editor_ready exists), and the Agent pill mounts
        # a beat after the prompt box, so poll generously for it before deciding
        # the account lacks the toggle — otherwise the test false-skips flakily.
        await transport._enter_editor(page)  # noqa: SLF001
        await transport._dismiss_blocking_overlays(page)  # noqa: SLF001
        await transport._wait_video_editor_ready(page)  # noqa: SLF001

        toggle = page.locator(COMPOSER_AGENT_TOGGLE_SELECTOR)
        try:
            await toggle.first.wait_for(state="attached", timeout=15000)
        except Exception:
            pytest.skip("Flow account has no Agent composer toggle (older editor UI)")

        # Uniqueness on the real DOM (PR #124 must-fix): the scoped selector must
        # resolve to exactly one element, so the ``.first`` the production helper
        # uses can never pick the wrong button. Only a real browser can evaluate
        # the Playwright :has()/:text() selector, so it is asserted here.
        count = await toggle.count()
        assert count == 1, (
            f"COMPOSER_AGENT_TOGGLE_SELECTOR must match exactly one element; got {count}"
        )

        # Reproduce: force Agent mode ON so the media panel is removed. Clicking
        # the pill from media mode enters Agent mode; if it is already on, the
        # crop_* trigger is already gone.
        if await transport._media_panel_present(page):  # noqa: SLF001
            await toggle.first.click()
            await page.wait_for_timeout(700)
        assert not await transport._media_panel_present(page), (  # noqa: SLF001
            "could not reproduce Agent mode — crop_* trigger still present after "
            "toggling the Agent pill; the toggle semantics may have changed"
        )

        # The fix: switching to image mode must first exit Agent mode, which
        # re-mounts the media panel and makes the crop_* trigger probe succeed.
        await transport._switch_to_image_mode(page)  # noqa: SLF001

        assert await transport._media_panel_present(page), (  # noqa: SLF001
            "media panel did not re-mount after _switch_to_image_mode — "
            "_exit_agent_mode failed to leave Agent mode"
        )
        events = [e["event"] for e in install_log_capture.entries]
        assert "ui_automation_video.exited_agent_mode" in events, (
            f"expected an 'exited_agent_mode' event proving the fix ran; captured events: {events}"
        )
    finally:
        await transport.teardown()


@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_e2e_agent_chat_panel_recovered_before_mode_switch(
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """Flow's Agent **chat side-panel** shape no longer breaks the generate path.

    Besides the in-composer pill, Flow sometimes promotes Agent mode to a docked
    chat panel ("Untitled session") on the right. While it is up the in-composer
    pill is not in the DOM at all, so the pill-only recovery cannot find it — the
    panel must be dismissed (its X) first, after which the pill reappears and is
    clicked. ``_exit_agent_mode`` now handles both shapes in one call.

    This reproduces the chat-panel shape on the **real DOM** (open Agent, then
    its expand control) and proves ``_switch_to_image_mode`` recovers: the chat
    panel is gone and the ``crop_*`` media trigger is back. **Zero credits.**

    Skips on accounts whose Flow UI lacks the Agent toggle or the chat-panel
    expand control (older editor).
    """
    import os

    from gflow_cli.auth import profile_dir as _resolve_profile_dir
    from gflow_cli.config import reset_settings

    monkeypatch.delenv("GFLOW_CLI_HOME", raising=False)
    monkeypatch.delenv("GFLOW_CLI_DB_PATH", raising=False)
    reset_settings()

    name = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    if not name:
        pytest.skip("set GFLOW_CLI_E2E_PROFILE to a logged-in profile name")
    profile = _resolve_profile_dir(name)
    if not profile.exists():
        pytest.skip(f"profile dir not found: {profile}")

    transport = UiAutomationTransport()
    try:
        await transport.setup(profile)
        page = transport._page  # noqa: SLF001
        assert page is not None, "setup() must acquire a page"

        await transport._enter_editor(page)  # noqa: SLF001
        await transport._dismiss_blocking_overlays(page)  # noqa: SLF001
        await transport._wait_video_editor_ready(page)  # noqa: SLF001

        pill = page.locator(COMPOSER_AGENT_TOGGLE_SELECTOR)
        try:
            await pill.first.wait_for(state="attached", timeout=15000)
        except Exception:
            pytest.skip("Flow account has no Agent composer toggle (older editor UI)")

        # Reproduce the chat-panel shape: enter Agent mode (pill), then promote it
        # to the docked chat panel via the composer's expand control.
        if await transport._media_panel_present(page):  # noqa: SLF001
            await pill.first.click()
            await page.wait_for_timeout(700)
        expand = page.locator("button:has(i:text-is('expand_content'))").first
        if await expand.count() == 0:
            pytest.skip("Flow account has no Agent chat-panel expand control")
        await expand.click(force=True)
        await page.wait_for_timeout(1300)

        # Confirm we actually reproduced State A: media panel absent AND the chat
        # panel's close (X) is present (so the pill path alone would be stuck).
        assert not await transport._media_panel_present(page), (  # noqa: SLF001
            "could not reproduce Agent mode — crop_* trigger still present"
        )
        chat_close = page.locator(AGENT_CHAT_PANEL_CLOSE_SELECTOR)
        if await chat_close.count() == 0:
            pytest.skip("Agent chat panel did not open (no close X) on this build")

        # The fix: switching to image mode must close the chat panel AND turn the
        # revealed pill off, re-mounting the media panel.
        await transport._switch_to_image_mode(page)  # noqa: SLF001

        assert await transport._media_panel_present(page), (  # noqa: SLF001
            "media panel did not re-mount — chat-panel exit path failed"
        )
        assert await chat_close.count() == 0, "agent chat panel still open after recovery"
        events = [e["event"] for e in install_log_capture.entries]
        assert "ui_automation_video.exited_agent_mode" in events, (
            f"expected an 'exited_agent_mode' event; captured events: {events}"
        )
    finally:
        await transport.teardown()
