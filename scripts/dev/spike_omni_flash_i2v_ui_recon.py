# ruff: noqa: E501
"""Credit-free UI recon: does TODAY's Flow editor allow Frames-to-Video (i2v)
with the Omni Flash model? (Issue #125 re-verification, submit-free.)

The #125 exclusion of omni-flash from i2v rests on a single empirical capture
from 2026-05-30 (``capture_i2v_intercept_submit.py``): with Omni Flash selected
and both frame slots bound, Flow's frontend routed the submit to
``batchAsyncGenerateVideoText`` with null image inputs. Flow's UI has been
redesigned repeatedly since (e.g. the #404 count-label rename), so this spike
re-checks what the CURRENT editor says about the model x sub-mode matrix —
WITHOUT clicking submit and WITHOUT spending credits.

Evidence gathered (all DOM-level, no generate request is ever fired):

  A1. settings-popover tab inventory in the default video state — every
      ``[role='tab']``'s id/text/aria-selected/aria-disabled
  A2. model-picker menuitem inventory (text + disabled state), first in the
      default sub-mode and again with Frames active — does Flow itself hide
      or disable Omni Flash when Frames is the active sub-mode?
  A3. state after selecting Omni Flash while Frames is active — does the
      Frames tab stay selected? do the Start/End slots still render? does
      the 10s duration tab appear (proof Omni is genuinely active)?
  A4. reverse order — with Omni Flash already active, is the Frames tab
      still clickable/selectable?
  B   (``--with-frame-bind``) bind a real Start frame on veo-lite + Frames,
      then switch the model to Omni Flash and record whether the binding
      survives in the DOM (slot count, toast scan, screenshot).

Usage:
    uv run python scripts/dev/spike_omni_flash_i2v_ui_recon.py \\
        --profile mauryan-uppalapati \\
        [--with-frame-bind --probe-image path/to/img.png] \\
        [--out-dir tmp/omni_recon]

This script NEVER clicks the generate/submit button. The wire-level question
(what request shape the frontend would send for Omni Flash + bound frames)
can only be answered by a submit — aborted via ``page.route`` or real — which
is deliberately out of scope here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, resolve_profile_dir  # noqa: E402

from gflow_cli.api.transports.ui_automation_video import (  # noqa: E402
    FRAME_SLOTS_STRUCT,
    MODE_SWITCH_TRIGGER_SELECTORS,
    MODEL_PICKER_TRIGGER,
    VIDEO_MODEL_OPTION_SELECTORS,
    VIDEO_SUBMODE_SELECTORS,
    VideoGenerationMixin,
)
from gflow_cli.api.video import VideoModel  # noqa: E402

_TAB_DUMP_JS = """
() => Array.from(document.querySelectorAll("[role='tab']")).map(t => ({
    id: t.id || null,
    text: (t.textContent || "").trim().slice(0, 60),
    selected: t.getAttribute("aria-selected"),
    disabled: t.getAttribute("aria-disabled") || (t.hasAttribute("disabled") ? "true" : null),
    state: t.getAttribute("data-state"),
}))
"""

_MENUITEM_DUMP_JS = """
() => Array.from(document.querySelectorAll("[role='menuitem']")).map(m => ({
    text: (m.textContent || "").trim().slice(0, 80),
    disabled: m.getAttribute("aria-disabled") || m.getAttribute("data-disabled") ||
              (m.hasAttribute("disabled") ? "true" : null),
}))
"""

_TOAST_DUMP_JS = """
() => Array.from(document.querySelectorAll(
    "[role='status'], [data-sonner-toast], [class*='toast' i], [aria-live='assertive']"
)).map(n => (n.textContent || "").trim().slice(0, 200)).filter(t => t.length > 0)
"""


async def _dump_tabs(page: Any, evidence: dict[str, Any], label: str) -> list[dict[str, Any]]:
    tabs = await page.evaluate(_TAB_DUMP_JS)
    evidence[f"tabs_{label}"] = tabs
    interesting = [t for t in tabs if t["id"] or t["text"]]
    print(f"[recon] tabs[{label}]: {len(interesting)} entries")
    for t in interesting:
        print(
            f"    id={t['id']!r} text={t['text']!r} selected={t['selected']} disabled={t['disabled']}"
        )
    return tabs


async def _dump_menuitems(page: Any, evidence: dict[str, Any], label: str) -> list[dict[str, Any]]:
    items = await page.evaluate(_MENUITEM_DUMP_JS)
    evidence[f"menuitems_{label}"] = items
    print(f"[recon] menuitems[{label}]: {len(items)} entries")
    for m in items:
        print(f"    text={m['text']!r} disabled={m['disabled']}")
    return items


async def _dump_toasts(page: Any, evidence: dict[str, Any], label: str) -> None:
    toasts = await page.evaluate(_TOAST_DUMP_JS)
    evidence[f"toasts_{label}"] = toasts
    if toasts:
        print(f"[recon] toasts[{label}]: {toasts}")


async def _slot_count(page: Any) -> int:
    return await page.locator(FRAME_SLOTS_STRUCT).count()


async def _ensure_settings_open(page: Any) -> None:
    """Re-open the video settings popover if it closed (model trigger gone)."""
    trigger = page.locator(MODEL_PICKER_TRIGGER).first
    try:
        await trigger.wait_for(state="visible", timeout=1_500)
        return
    except Exception:
        pass
    opener = await VideoGenerationMixin._probe_selector_cascade(  # type: ignore[reportPrivateUsage]
        page, "mode_switch_trigger", MODE_SWITCH_TRIGGER_SELECTORS
    )
    if opener is None:
        print("[recon] WARN: could not re-open the settings popover")
        return
    await opener.click()
    await page.wait_for_timeout(800)


async def _open_model_menu(page: Any) -> bool:
    await _ensure_settings_open(page)
    trigger = page.locator(MODEL_PICKER_TRIGGER).first
    try:
        await trigger.wait_for(state="visible", timeout=4_000)
        await trigger.click()
        await page.wait_for_timeout(600)
        return True
    except Exception as e:
        print(f"[recon] WARN: model-picker trigger miss: {e}")
        return False


async def _click_submode(page: Any, sub: str) -> bool:
    tab = await VideoGenerationMixin._probe_selector_cascade(  # type: ignore[reportPrivateUsage]
        page, f"video_submode_{sub}", VIDEO_SUBMODE_SELECTORS[sub]
    )
    if tab is None:
        return False
    await tab.click()
    await page.wait_for_timeout(700)
    return True


async def recon(
    profile_name: str,
    out_dir: Path,
    with_frame_bind: bool,
    probe_image: Path | None,
) -> int:
    profile_dir = resolve_profile_dir(profile_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {"profile": profile_name, "with_frame_bind": with_frame_bind}

    async with build_client(profile_dir) as client:
        transport = client.transport
        if transport is None:
            sys.exit("FlowApiClient.transport is None")
        page = await client._checkout_page()  # type: ignore[reportPrivateUsage]

        await transport._enter_editor(page, None)  # type: ignore[attr-defined]
        await transport._dismiss_blocking_overlays(page, None)  # type: ignore[attr-defined]
        await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)  # type: ignore[reportPrivateUsage]
        await VideoGenerationMixin._wait_video_editor_ready(page)  # type: ignore[reportPrivateUsage]

        # ---- A1: default video-mode state --------------------------------
        await _dump_tabs(page, evidence, "initial")
        await page.screenshot(path=str(out_dir / "01-video-mode-initial.png"))

        # ---- A2a: model inventory in the DEFAULT sub-mode ----------------
        if await _open_model_menu(page):
            await _dump_menuitems(page, evidence, "default_submode")
            await page.screenshot(path=str(out_dir / "02-model-menu-default-submode.png"))
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)

        # ---- A2b: switch to Frames, model inventory again ----------------
        await _ensure_settings_open(page)
        frames_ok = await _click_submode(page, "frames")
        evidence["frames_tab_clickable_default_model"] = frames_ok
        print(f"[recon] frames sub-mode clickable (default model): {frames_ok}")
        await _dump_tabs(page, evidence, "frames_active")
        evidence["slot_count_frames_default_model"] = await _slot_count(page)
        print(
            f"[recon] frame slots visible (frames + default model): {evidence['slot_count_frames_default_model']}"
        )

        if await _open_model_menu(page):
            items = await _dump_menuitems(page, evidence, "frames_submode")
            await page.screenshot(path=str(out_dir / "03-model-menu-frames-submode.png"))
            omni_listed = any("Omni Flash" in m["text"] for m in items)
            omni_disabled = any(
                "Omni Flash" in m["text"] and m["disabled"] not in (None, "false") for m in items
            )
            evidence["omni_listed_in_frames_menu"] = omni_listed
            evidence["omni_disabled_in_frames_menu"] = omni_disabled
            print(
                f"[recon] Omni Flash listed={omni_listed} disabled={omni_disabled} (Frames active)"
            )

            # ---- A3: select Omni Flash while Frames is active ------------
            if omni_listed and not omni_disabled:
                opt = page.locator(VIDEO_MODEL_OPTION_SELECTORS[VideoModel.OMNI_FLASH]).first
                try:
                    await opt.click()
                    await page.wait_for_timeout(1_000)
                    evidence["omni_selected_in_frames"] = True
                except Exception as e:
                    evidence["omni_selected_in_frames"] = False
                    evidence["omni_select_error"] = str(e)
            else:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(300)
                evidence["omni_selected_in_frames"] = False

        await _dump_tabs(page, evidence, "after_omni_in_frames")
        evidence["slot_count_after_omni"] = await _slot_count(page)
        await _dump_toasts(page, evidence, "after_omni_in_frames")
        await page.screenshot(path=str(out_dir / "04-after-omni-in-frames.png"))
        print(
            f"[recon] frame slots visible (after Omni select): {evidence['slot_count_after_omni']}"
        )

        # ---- A4: with Omni active, is Frames still clickable? ------------
        await _ensure_settings_open(page)
        await _click_submode(page, "references")
        await _dump_tabs(page, evidence, "references_with_omni")
        frames_again = await _click_submode(page, "frames")
        evidence["frames_tab_clickable_with_omni"] = frames_again
        print(f"[recon] frames sub-mode clickable (Omni active): {frames_again}")
        await _dump_tabs(page, evidence, "frames_again_with_omni")
        evidence["slot_count_frames_with_omni"] = await _slot_count(page)
        await page.screenshot(path=str(out_dir / "05-frames-with-omni.png"))

        # ---- B: bind a frame on veo-lite, then switch to Omni ------------
        if with_frame_bind and probe_image is not None:
            print("[recon] phase B: veo-lite + Frames + bind Start, then switch to Omni Flash")
            if await _open_model_menu(page):
                lite = page.locator(VIDEO_MODEL_OPTION_SELECTORS[VideoModel.VEO_3_1_LITE]).first
                await lite.click()
                await page.wait_for_timeout(800)
            await _ensure_settings_open(page)
            await _click_submode(page, "frames")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(600)
            evidence["slots_before_bind"] = await _slot_count(page)
            await VideoGenerationMixin._attach_frame(  # type: ignore[reportPrivateUsage]
                page, 0, "Start", probe_image, out_dir=out_dir
            )
            evidence["slots_after_bind"] = await _slot_count(page)
            print(
                f"[recon] slots before bind={evidence['slots_before_bind']} "
                f"after bind={evidence['slots_after_bind']} (expect 2 -> 1)"
            )
            await page.screenshot(path=str(out_dir / "06-start-bound-veo-lite.png"))

            if await _open_model_menu(page):
                opt = page.locator(VIDEO_MODEL_OPTION_SELECTORS[VideoModel.OMNI_FLASH]).first
                try:
                    await opt.click()
                    await page.wait_for_timeout(1_200)
                    evidence["omni_selected_with_bound_frame"] = True
                except Exception as e:
                    evidence["omni_selected_with_bound_frame"] = False
                    evidence["omni_switch_error"] = str(e)
            evidence["slots_after_omni_switch"] = await _slot_count(page)
            await _dump_tabs(page, evidence, "bound_frame_omni")
            await _dump_toasts(page, evidence, "bound_frame_omni")
            await page.screenshot(path=str(out_dir / "07-bound-frame-after-omni-switch.png"))
            print(
                f"[recon] slots after Omni switch={evidence['slots_after_omni_switch']} "
                f"(1 = binding visually survived; 2 = Flow dropped it)"
            )
            await page.keyboard.press("Escape")

        # NO SUBMIT — this spike never clicks generate.
        (out_dir / "evidence.json").write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\n[recon] evidence written: {out_dir / 'evidence.json'}")
        print("[recon] NO generate request was fired — zero credits.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--with-frame-bind", action="store_true")
    parser.add_argument("--probe-image", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=Path("tmp/omni_recon"))
    args = parser.parse_args()
    if args.with_frame_bind and args.probe_image is None:
        parser.error("--with-frame-bind requires --probe-image")
    return asyncio.run(recon(args.profile, args.out_dir, args.with_frame_bind, args.probe_image))


if __name__ == "__main__":
    sys.exit(main())
