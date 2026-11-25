"""Tests for Flow image --thinking-level and 4K Ultra video upscale support."""

from __future__ import annotations

import base64
import json
import struct
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner
from playwright.async_api import Page, Route

from gflow_cli.api.image import Aspect, GenerateImageRequest, Model, ThinkingLevel
from gflow_cli.api.transports import agent_only_composer as aoc
from gflow_cli.api.transports import migrated_video_upscale as mvu
from gflow_cli.api.transports._mc_common import IMAGE_MODEL_MENU_MATCHERS
from gflow_cli.api.transports._mc_settings_pane import SettingsPaneMixin
from gflow_cli.api.transports._ui_image import _resolve_labs_ui_mode
from gflow_cli.api.transports.direct_http import direct_can_serve
from gflow_cli.errors import (
    ConfigurationError,
    CreditCapExceededError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WireFormatError,
)
from gflow_cli.worker.codec import build_image_request
from tests.api._upscale_fakes import FakeItem, fake_page

_CLASSIC_PANE_HTML = """<!doctype html><html><body>
<div class="cdk-overlay-pane">
  <flow-prompt-box-settings>
    <div class="settings-content">
      <mat-button-toggle-group role="radiogroup">
        <mat-button-toggle>
          <button role="radio" aria-checked="true"><mat-icon>image</mat-icon>图片</button>
        </mat-button-toggle>
        <mat-button-toggle>
          <button role="radio" aria-checked="false"><mat-icon>videocam</mat-icon>视频</button>
        </mat-button-toggle>
      </mat-button-toggle-group>
      <mat-button-toggle-group role="radiogroup">
        <mat-button-toggle>
          <button role="radio" aria-checked="true"><mat-icon>crop_16_9</mat-icon>16:9</button>
        </mat-button-toggle>
        <mat-button-toggle>
          <button role="radio" aria-checked="false"><mat-icon>crop_9_16</mat-icon>9:16</button>
        </mat-button-toggle>
      </mat-button-toggle-group>
      <button aria-haspopup="menu">🍌 Nano Banana 2.1 <mat-icon>arrow_drop_down</mat-icon></button>
      __THINKING_GROUP__
      __COUNT_GROUP__
    </div>
  </flow-prompt-box-settings>
</div>
<script>
document.querySelectorAll('mat-button-toggle-group').forEach(g => {
  const btns = [...g.querySelectorAll('button[role="radio"]')];
  btns.forEach(b => b.addEventListener('click', () => {
    btns.forEach(o => o.setAttribute('aria-checked', String(o === b)));
  }));
});
</script>
</body></html>"""


def _toggle_group(labels: list[str], checked: int = 0) -> str:
    items = "".join(
        f'<mat-button-toggle><button role="radio" aria-checked="{str(i == checked).lower()}">'
        f"{label}</button></mat-button-toggle>"
        for i, label in enumerate(labels)
    )
    return f'<mat-button-toggle-group role="radiogroup">{items}</mat-button-toggle-group>'


def _classic_html(*, thinking_group: str = "", count_labels: list[str] | None = None) -> str:
    counts = _toggle_group(count_labels or ["x1", "x2", "x3", "x4"], checked=0)
    return _CLASSIC_PANE_HTML.replace("__THINKING_GROUP__", thinking_group).replace(
        "__COUNT_GROUP__", counts
    )


def _minimal_mp4_with_tkhd(width: int, height: int) -> bytes:
    tkhd_body = b"\x00" * 76 + struct.pack(">II", width << 16, height << 16)
    tkhd = struct.pack(">I4s", 8 + len(tkhd_body), b"tkhd") + tkhd_body
    trak = struct.pack(">I4s", 8 + len(tkhd), b"trak") + tkhd
    moov = struct.pack(">I4s", 8 + len(trak), b"moov") + trak
    ftyp = struct.pack(">I4s", 16, b"ftyp") + b"isom\x00\x00\x02\x00"
    return ftyp + moov


def test_thinking_level_enum_and_radio_index() -> None:
    assert ThinkingLevel.from_cli(None) is None
    assert ThinkingLevel.from_cli("HIGH") is ThinkingLevel.HIGH
    assert ThinkingLevel.from_cli("minimal") is ThinkingLevel.LOW
    assert ThinkingLevel.LOW.radio_index(3) == 0
    assert ThinkingLevel.MEDIUM.radio_index(3) == 1
    assert ThinkingLevel.HIGH.radio_index(3) == 2
    assert ThinkingLevel.LOW.radio_index(2) == 0
    assert ThinkingLevel.HIGH.radio_index(2) == 1
    with pytest.raises(ValueError, match="Unknown thinking level"):
        ThinkingLevel.from_cli("extreme")
    with pytest.raises(ValueError, match="2-option"):
        ThinkingLevel.MEDIUM.radio_index(2)


def test_direct_http_declines_explicit_thinking_level() -> None:
    base = GenerateImageRequest(prompt="red apple", aspect=Aspect.LANDSCAPE, model=Model.NARWHAL)
    assert direct_can_serve(base) is True
    high = GenerateImageRequest(
        prompt="red apple",
        aspect=Aspect.LANDSCAPE,
        model=Model.NARWHAL,
        thinking_level=ThinkingLevel.HIGH,
    )
    assert direct_can_serve(high) is False


def test_worker_codec_decodes_thinking_level() -> None:
    req = build_image_request(
        {
            "prompt": "a lantern",
            "model": "nano2",
            "aspect": "16:9",
            "count": 2,
            "thinking_level": "high",
        }
    )
    assert req.thinking_level is ThinkingLevel.HIGH


@pytest.mark.asyncio
async def test_classic_settings_pane_selects_thinking_level_locale_invariantly(page: Page) -> None:
    await page.set_content(
        _classic_html(thinking_group=_toggle_group(["低", "中", "高"], checked=1))
    )
    pane = page.locator(".cdk-overlay-pane").first
    mixin = SettingsPaneMixin()

    await mixin._select_image_thinking_level(page, pane, ThinkingLevel.HIGH)  # pyright: ignore[reportPrivateUsage]
    radios = pane.locator('[role="radiogroup"]').nth(2).locator('button[role="radio"]')
    assert await radios.nth(2).get_attribute("aria-checked") == "true"

    await mixin._select_image_thinking_level(page, pane, ThinkingLevel.LOW)  # pyright: ignore[reportPrivateUsage]
    assert await radios.nth(0).get_attribute("aria-checked") == "true"


@pytest.mark.asyncio
async def test_classic_settings_pane_ignores_localized_numeric_count_groups(page: Page) -> None:
    mixin = SettingsPaneMixin()
    # German 2-option count group ("1-mal", "2-mal") must never collide with thinking levels.
    await page.set_content(
        _classic_html(
            thinking_group=_toggle_group(["Niedrig", "Mittel", "Hoch"], checked=0),
            count_labels=["1-mal", "2-mal"],
        )
    )
    pane = page.locator(".cdk-overlay-pane").first
    await mixin._select_image_thinking_level(page, pane, ThinkingLevel.HIGH)  # pyright: ignore[reportPrivateUsage]
    think_radios = pane.locator('[role="radiogroup"]').nth(2).locator('button[role="radio"]')
    count_radios = pane.locator('[role="radiogroup"]').nth(3).locator('button[role="radio"]')
    assert await think_radios.nth(2).get_attribute("aria-checked") == "true"
    assert await count_radios.nth(0).get_attribute("aria-checked") == "true"

    # Without a thinking group, localized count radios must raise ConfigurationError.
    await page.set_content(_classic_html(thinking_group="", count_labels=["1-mal", "2-mal"]))
    pane = page.locator(".cdk-overlay-pane").first
    with pytest.raises(ConfigurationError, match="no thinking-level control"):
        await mixin._select_image_thinking_level(page, pane, ThinkingLevel.HIGH)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_classic_settings_pane_handles_missing_and_ambiguous_thinking_group(
    page: Page,
) -> None:
    mixin = SettingsPaneMixin()
    await page.set_content(_classic_html(thinking_group=""))
    pane = page.locator(".cdk-overlay-pane").first
    with pytest.raises(ConfigurationError, match="no thinking-level control"):
        await mixin._select_image_thinking_level(page, pane, ThinkingLevel.HIGH)  # pyright: ignore[reportPrivateUsage]

    dup = _toggle_group(["Low", "Medium", "High"], checked=0) * 2
    await page.set_content(_classic_html(thinking_group=dup))
    pane = page.locator(".cdk-overlay-pane").first
    with pytest.raises(UiSelectorDriftError, match="ambiguous text-only radiogroups"):
        await mixin._select_image_thinking_level(page, pane, ThinkingLevel.HIGH)  # pyright: ignore[reportPrivateUsage]


async def _load_stateful_unified_fixture(
    page: Page,
    *,
    initial_model: str = "Nano Banana 2.1",
    thinking_models: tuple[str, ...] = ("Nano Banana 2.1",),
) -> list[dict[str, Any]]:
    """Unified settings fixture that persists thinking level and gates it by model."""
    from tests.api.transports import test_agent_only_unified_settings as unified_mod

    model_json = json.dumps(initial_model)
    html = unified_mod.FIXTURE
    html = html.replace(
        "const state = {confirm: 0, image: {aspect: 0, count: 1, model: 'Nano Banana 2.1'},",
        f"const THINKING_MODELS = new Set({json.dumps(list(thinking_models))});\n"
        f"const state = {{confirm: 0, image: {{aspect: 0, count: 1, thinking: 0, "
        f"model: {model_json}}},",
    )
    html = html.replace(
        "(k === 'image' ? toggles('thinking', ['Low', 'High'], 0) : '')",
        "(k === 'image' && THINKING_MODELS.has(s.model) "
        "? toggles('thinking', ['Low', 'High'], s.thinking) : '')",
    )
    html = html.replace(
        "if (g === 'confirm') state.confirm = i; else if (g !== 'thinking') state[view][g] = i;",
        "if (g === 'confirm') state.confirm = i; else state[view][g] = i;",
    )
    saved: list[dict[str, Any]] = []

    async def answer(route: Route) -> None:
        if "batchexecute" in route.request.url:
            saved.append(json.loads(route.request.post_data or "{}"))
            await route.fulfill(status=200, content_type="text/plain", body=")]}'\n[]")
            return
        await route.fulfill(status=200, content_type="text/html", body=html)

    await page.route("https://flow.google.com/**", answer)
    await page.goto("https://flow.google.com/fx/tools/flow/project/test-thinking")
    return saved


@pytest.mark.asyncio
async def test_agent_only_unified_settings_persists_restores_and_switches_model_for_thinking(
    page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(aoc, "RADIO_SETTLE_S", 0.6)
    monkeypatch.setattr(aoc, "PANE_S", 1.5)
    monkeypatch.setattr(aoc, "SAVE_QUIET_S", 0.2)
    monkeypatch.setattr(aoc, "SAVE_BUDGET_S", 5.0)
    saved = await _load_stateful_unified_fixture(page)

    composer = aoc.AgentOnlyComposer()
    snap = await composer.apply_defaults(
        page,
        section="image",
        aspect_ligature="crop_16_9",
        count=2,
        model=None,
        confirm="account",
        thinking_level=ThinkingLevel.HIGH,
    )
    assert snap.thinking_index == 0
    assert await page.evaluate("() => window.state.image.thinking") == 1
    assert saved[-1]["image"]["thinking"] == 1

    await composer.restore_defaults(page, snap)
    assert await page.evaluate("() => window.state.image.thinking") == 0
    assert saved[-1]["image"]["thinking"] == 0

    # Starting from a model without thinking controls, switching to Nano Banana 2.1 succeeds.
    saved2 = await _load_stateful_unified_fixture(
        page, initial_model="Nano Banana Pro", thinking_models=("Nano Banana 2.1",)
    )
    snap2 = await composer.apply_defaults(
        page,
        section="image",
        aspect_ligature="crop_16_9",
        count=2,
        model=IMAGE_MODEL_MENU_MATCHERS[Model.NARWHAL],
        confirm="account",
        thinking_level=ThinkingLevel.HIGH,
    )
    assert snap2.thinking_index == -1
    assert await page.evaluate("() => window.state.image.model") == "Nano Banana 2.1"
    assert await page.evaluate("() => window.state.image.thinking") == 1
    assert saved2[-1]["image"]["thinking"] == 1


@pytest.mark.asyncio
async def test_legacy_agent_pane_and_labs_ui_reject_thinking_level(page: Page) -> None:
    await page.set_content(
        "<html><body><flow-creative-agent-prompt-box></flow-creative-agent-prompt-box></body></html>"
    )
    composer = aoc.AgentOnlyComposer()
    with pytest.raises(ConfigurationError, match="legacy settings pane has no thinking-level"):
        await composer.apply_defaults(
            page,
            section="image",
            aspect_ligature="crop_16_9",
            count=1,
            model=None,
            confirm="account",
            thinking_level=ThinkingLevel.HIGH,
        )

    req = GenerateImageRequest(
        prompt="a lantern",
        aspect=Aspect.LANDSCAPE,
        model=Model.NARWHAL,
        thinking_level=ThinkingLevel.HIGH,
    )
    with pytest.raises(
        ConfigurationError, match="only supported on migrated flow.google.com"
    ) as exc:
        _resolve_labs_ui_mode(req)
    assert exc.value.charged == "no"


@pytest.mark.asyncio
async def test_video_upscale_4k_constants_credit_cap_and_non_retryable_errors() -> None:
    assert "4k" in mvu.VALID_VIDEO_SCALES
    assert mvu.export_timeout_s("4k") == 360.0
    mvu._check_mp4_resolution(_minimal_mp4_with_tkhd(3840, 2160), "4k")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(WireFormatError, match="smaller than 4k") as wf_exc:
        mvu._check_mp4_resolution(_minimal_mp4_with_tkhd(1920, 1080), "4k")  # pyright: ignore[reportPrivateUsage]
    assert "spends no credits" not in wf_exc.value.remediation_hint

    # Credit cap blocks 4K BEFORE clicking the 50-credit menu item.
    menu = [
        FakeItem("270p"),
        FakeItem("720p"),
        FakeItem("1080p"),
        FakeItem("4K 已高清重塑 · 消耗 50 个点数", disabled=False),
    ]
    page = fake_page(
        menu, captured_b64=base64.b64encode(_minimal_mp4_with_tkhd(3840, 2160)).decode("ascii")
    )
    with pytest.raises(CreditCapExceededError) as cap_exc:
        await mvu.upscale_video_migrated(
            page,
            project_id="00000000-0000-4000-8000-000000000001",
            media_id="00000000-0000-4000-8000-000000000002",
            scale="4k",
            max_credits=20,
        )
    assert cap_exc.value.charged == "no"
    assert cap_exc.value.quoted == 50
    assert menu[3].clicked is False

    # Post-click 4K timeout is non-retryable and demands reconciliation.
    menu_to = [FakeItem("4K Upscaled · 50 credits", disabled=False)]
    page_to = fake_page(menu_to, captured_b64=None)
    with pytest.raises(TransportTimeoutError) as to_exc:
        await mvu.upscale_video_migrated(
            page_to,
            project_id="00000000-0000-4000-8000-000000000001",
            media_id="00000000-0000-4000-8000-000000000002",
            scale="4k",
            timeout_s=0.05,
        )
    assert to_exc.value.charged == "maybe"
    assert to_exc.value.retryable is False
    assert to_exc.value.resolved_next_action() == "reconcile"


def test_cli_image_t2i_accepts_thinking_level_and_rejects_multi_prompt() -> None:
    from gflow_cli.cli import main

    runner = CliRunner()
    res_multi = runner.invoke(
        main,
        ["image", "t2i", "first prompt", "second prompt", "--thinking-level", "high"],
    )
    assert res_multi.exit_code == 2
    assert "--thinking-level is single-prompt only" in res_multi.output


@pytest.mark.asyncio
async def test_mcp_generate_image_validates_and_forwards_thinking_level() -> None:
    from gflow_cli.mcp import tools as mcp_tools

    bad = await mcp_tools.gflow_generate_image(prompt="lantern", thinking_level="extreme")
    assert bad["status"] == "error"
    assert bad["error"]["title"] == "Invalid thinking_level"

    captured: dict[str, object] = {}

    async def fake_run(
        *,
        profile: str,
        task_type: str,
        payload: dict[str, object],
        wait: bool,
    ) -> dict[str, object]:
        captured["payload"] = dict(payload)
        return {"status": "completed", "files": []}

    with (
        patch.object(mcp_tools._rate_limiter, "acquire", AsyncMock(return_value=True)),
        patch.object(mcp_tools, "_resolve_and_validate_profile", return_value="default"),
        patch.object(mcp_tools, "default_project", return_value=None),
        patch.object(mcp_tools, "_run_generation_task", side_effect=fake_run),
    ):
        ok = await mcp_tools.gflow_generate_image(prompt="lantern", thinking_level="HIGH")
    assert ok["status"] == "completed"
    assert captured["payload"]["thinking_level"] == "high"  # type: ignore[index]


@pytest.mark.asyncio
async def test_mcp_upscale_video_accepts_4k_and_is_gated_by_no_spend() -> None:
    from gflow_cli.mcp import server as mcp_server
    from gflow_cli.mcp import tools as mcp_tools

    assert "gflow_upscale_video" in mcp_server._SPEND_TOOLS  # pyright: ignore[reportPrivateUsage]
    run_mock = AsyncMock(return_value={"status": "ok", "scale": "4k"})
    with (
        patch.object(mcp_tools, "_resolve_and_validate_profile", return_value="default"),
        patch.object(mcp_tools, "_run_upscale_tool", run_mock),
    ):
        res = await mcp_tools.gflow_upscale_video(
            media_id="3a56bb5e-92a2-44f4-9992-3c6a9bf0cd14",
            scale="4K",
        )
    assert res["status"] == "ok"
    assert run_mock.await_args is not None
    assert run_mock.await_args.kwargs["scale_label"] == "4k"
