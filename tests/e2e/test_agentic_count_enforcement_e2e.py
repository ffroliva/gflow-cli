"""Live e2e regression test for issue #313: Flow's Agent-mode "tune" settings
panel has a STICKY "Image generation default" count that can silently
override the requested count if left mismatched. Spends Imagen credits (free
— image generation costs 0 Flow credits, only video does). Skipped by
default; opt in with ``GFLOW_CLI_E2E_PROFILE=<agentic profile>`` and
``-m e2e_image``.

Setup: deliberately drive the count to a MISMATCHED value via raw Playwright
(mirroring the manual verification done during PR #325 / its rework), then
request a DIFFERENT count through the real ``gflow`` agentic generation path
and assert exactly that count comes back — this is the actual bug class
issue #313 reported (mocks can't represent Flow's own persisted UI state, so
only a real browser against real Flow can catch a regression here).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import structlog

from gflow_cli.api import routes
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_image]

_E2E_PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"

_TUNE_BUTTON_SELECTOR = "button:has(i.google-symbols:text-is('tune'))"


def _image_kind(path: Path) -> str | None:
    with path.open("rb") as f:
        head = f.read(12)
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    return None


async def _set_mismatched_sticky_count(
    client: FlowApiClient, *, project_id: str, wrong_count: int
) -> None:
    """Drive Flow's Agent settings panel to a count that does NOT match the
    generation about to be requested, so the test proves the fix corrects a
    real mismatch rather than passing on an accidentally-already-correct
    default."""
    page = await client._checkout_page()  # noqa: SLF001
    try:
        await page.goto(
            routes.project_editor_url("en", project_id),
            wait_until="domcontentloaded",
            timeout=45_000,
        )
        await page.wait_for_timeout(4_000)
        await page.keyboard.press("Escape")

        tune_btn = page.locator(_TUNE_BUTTON_SELECTOR).first
        await tune_btn.wait_for(state="visible", timeout=10_000)
        await tune_btn.click(timeout=5_000)
        await page.wait_for_timeout(1_000)

        # Anchor on x2+x3 (present in BOTH label cohorts — Flow renamed the
        # count-1 label from '1x' to 'x1', issue #404); for count=1 try the
        # renamed label first, then the legacy one.
        labels = ("x1", "1x") if wrong_count == 1 else (f"x{wrong_count}",)
        count_tablist = page.locator(
            "[role='tablist']:has(button:text-is('x2')):has(button:text-is('x3'))"
        ).first
        clicked = False
        for label in labels:
            target_btn = count_tablist.locator(f"button:text-is('{label}')").first
            if await target_btn.count() > 0:
                await target_btn.click(timeout=5_000)
                clicked = True
                break
        assert clicked, f"no count tab found for {wrong_count} (tried {labels})"
        await page.wait_for_timeout(300)

        # Structurally locate + click Save (locale-invariant — see
        # agentic.py's _FIND_SAVE_BUTTON_JS for the identical, live-verified
        # algorithm this mirrors).
        found_save = await page.evaluate(
            """
            () => {
              const backBtn = [...document.querySelectorAll('button')].find((b) => {
                const i = b.querySelector('i.google-symbols');
                return i && (i.textContent || '').trim() === 'arrow_back';
              });
              if (!backBtn) return false;
              let node = backBtn.parentElement;
              for (let i = 0; i < 8 && node; i++) {
                const hasCountTablist = [...node.querySelectorAll("[role='tablist']")].some((t) => {
                  const texts = [...t.querySelectorAll('button')].map(
                    (b) => (b.textContent || '').trim()
                  );
                  return texts.includes('x2') && texts.includes('x3');
                });
                if (hasCountTablist) {
                  const visible = [...node.querySelectorAll('button')].filter((b) => {
                    const r = b.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                  });
                  const save = visible[visible.length - 1];
                  if (!save) return false;
                  save.setAttribute('data-e2e-save-target', '1');
                  return true;
                }
                node = node.parentElement;
              }
              return false;
            }
            """
        )
        assert found_save, "could not locate the Agent settings panel's Save button"
        await page.locator("[data-e2e-save-target='1']").first.click(timeout=5_000)
        await page.wait_for_timeout(500)
    finally:
        client._checkin_page(page)  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 2, 3, 4])
async def test_requested_count_overrides_mismatched_sticky_default(
    count: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """LIVE: a project's Agent settings count is deliberately left mismatched;
    requesting ``count`` images must still return exactly ``count``, with the
    agentic driver bound and no MediaAttributionError."""
    from gflow_cli.config import reset_settings

    monkeypatch.delenv("GFLOW_CLI_HOME", raising=False)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(tmp_path / f"e2e_count_{count}.db"))
    # Force the agentic composer deterministically regardless of the
    # server-side A/B cohort flap (GFLOW_CLI_FORCE_AGENT_UI is deprecated —
    # use the current UI-mode setting).
    monkeypatch.setenv("GFLOW_CLI_UI_MODE", "agentic")
    reset_settings()

    name = os.environ.get(_E2E_PROFILE_ENV, "").strip()
    if not name:
        pytest.skip(f"set {_E2E_PROFILE_ENV} to a logged-in agentic profile, run with -m e2e_image")
    from gflow_cli.auth import profile_dir as _resolve_profile_dir

    profile = _resolve_profile_dir(name)
    if not profile.exists():
        pytest.skip(f"profile not found: {profile} — run `gflow auth login --profile {name}`")

    # Always mismatched: pick any label different from the requested count.
    wrong_count = (count % 4) + 1

    req = GenerateImageRequest(
        prompt=f"a distinct test image, variant count-{count}",
        aspect=Aspect.LANDSCAPE,
        model=Model.NARWHAL,
    )

    async with FlowApiClient(profile_dir=profile, out_dir=tmp_path) as client:
        project = await client.create_project(title=f"gflow-cli e2e count-{count}")
        await _set_mismatched_sticky_count(
            client, project_id=project.project_id, wrong_count=wrong_count
        )

        images = await client.generate_images_batch(
            project_id=project.project_id, req=req, count=count
        )

        assert len(images) == count, (
            f"requested {count} images but got {len(images)} back "
            f"(sticky default was left at a mismatched {wrong_count})"
        )

        for i, image in enumerate(images):
            assert image.media_name, f"image {i} has no media_name"
            out_path = tmp_path / f"{image.media_name}.png"
            saved = await client.download_image(image, out_path)
            saved_path = Path(str(saved))
            assert saved_path.exists() and saved_path.stat().st_size > 0, (
                f"image {i}: no bytes written"
            )
            assert _image_kind(saved_path) is not None, f"image {i}: not a known image format"

    events = [e.get("event") for e in install_log_capture.entries]
    bound = [e for e in install_log_capture.entries if e.get("event") == "ui_driver.bound"]
    assert any(e.get("mode") == "agentic" for e in bound), (
        f"expected the agentic driver to bind; ui_driver.bound events: {bound}"
    )
    failures = [
        e for e in install_log_capture.entries if "enforce_count_failed" in str(e.get("event"))
    ]
    assert "agentic_driver.settings_panel.enforce_count_failed" not in events, (
        f"count enforcement raised/logged a failure for count={count}: {failures}"
    )
