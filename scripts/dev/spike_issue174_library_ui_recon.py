#!/usr/bin/env python3
r"""Issue #174 recon — new full-page media-library UI attach gesture (0 credits).

Flow is A/B-rolling a full-page media-library UI: on affected accounts the
composer's 'Add Media' button NAVIGATES to a library page instead of opening
the `[role='dialog']` resource picker. The right-click include still lands (a
chip appears in the library page's floating quick-create composer) but the
staged entity never reaches the submit — no `referenceEntities` on the wire.

This spike answers, credit-free (all generate routes are route-aborted):

  Phase A  — variant probe: after clicking Add Media, does a dialog open or
             does the page navigate? (rollout re-probe answer on any account)
  Phase C  — new-UI DOM dump: sidebar items, entity tiles, floating-composer
             candidates, full ligature inventory (selector design input).
  Phase B1 — stage entity via right-click include, then submit FROM the
             library page's floating quick-create composer. Capture payload.
  Phase B2 — stage entity, navigate BACK to the editor, submit from the
             editor composer (does staging survive navigation?). Capture.

Every phase is non-fatal: errors are recorded in the JSON and the run
continues. Output JSON + screenshots land in scripts/dev/_spike_out/ —
LOCAL ONLY, never commit (screenshots may carry account avatar/email).

Usage (headed, supervised; ONE disciplined run — WAF heat, see #174 plan):

    PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\spike_issue174_library_ui_recon.py \
        --profile denon82 --project <project-uuid> \
        --entity-id <entityId> --name Stickman --locale pt
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.transports.ui_automation_video import (  # noqa: E402
    ADD_MEDIA_BUTTON,
    PICKER_CONTEXT_INCLUDE,
    VideoGenerationMixin,
)

# Abort BOTH generation families — the library page may submit either.
_GEN_ROUTE_GLOBS = (
    "**/video:batchAsyncGenerateVideo*",
    "**/*batchGenerateImages*",
)
_OPEN_DIALOG = "[role='dialog'][data-state='open']"
_PROMPT_BOX = "div[role='textbox'][data-slate-editor='true']"
_SUBMIT_BTN = "button:has(i.google-symbols:text('arrow_forward'))"
_ENTITY_TILE_PREFIX = "[data-tile-id^='fe_id_']"
# ADD_MEDIA_BUTTON requires aria-haspopup='dialog'; a new-UI account may drop
# that attribute (the button navigates) — fall back to the bare icon anchor.
_ADD_MEDIA_FALLBACK = "button:has(i.google-symbols:text-is('add_2'))"
_SPIKE_PROMPT = "Stands on a clifftop at sunset, waves at the camera."
# New-UI sidebar: Personagens entry carries the same accessibility_new
# ligature as the old picker tab (locale-invariant) but may be an <a>.
_LIBRARY_PERSONAGENS = (
    "a:has(i.google-symbols:text-is('accessibility_new')),"
    " [role='tab']:has(i.google-symbols:text-is('accessibility_new')),"
    " button:has(i.google-symbols:text-is('accessibility_new'))"
)

# DOM dump of the library page: nav/sidebar entries, tile inventory,
# composer candidates (any slate textbox + sibling button ligatures), and the
# full ligature inventory — everything selector design needs, no PII fields.
_DUMP_LIBRARY_JS = """
() => {
  const lig = (el) => {
    const i = el.querySelector("i.google-symbols, i[class*='symbols']");
    return i ? (i.textContent || "").trim() : null;
  };
  const brief = (el) => ({
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute("role"),
    href: el.getAttribute("href"),
    ligature: lig(el),
    text: (el.textContent || "").trim().slice(0, 60),
  });
  const navItems = [...document.querySelectorAll(
    "nav a, nav button, aside a, aside button, [role='navigation'] a, [role='navigation'] button"
  )].map(brief);
  const tiles = [...document.querySelectorAll("[data-tile-id]")].slice(0, 30).map((el) => ({
    tileId: el.getAttribute("data-tile-id"),
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute("role"),
  }));
  const composers = [...document.querySelectorAll(
    "div[role='textbox'][data-slate-editor='true']"
  )].map((box) => {
    const host = box.closest("form, [class*='composer'], [class*='prompt']") ||
      box.parentElement?.parentElement || box;
    return {
      inDialog: !!box.closest("[role='dialog']"),
      hostTag: host.tagName.toLowerCase(),
      buttons: [...host.querySelectorAll("button")].map((b) => ({
        ligature: lig(b),
        ariaLabel: b.getAttribute("aria-label"),
        haspopup: b.getAttribute("aria-haspopup"),
        disabled: b.disabled,
      })).slice(0, 20),
    };
  });
  const ligatures = {};
  for (const i of document.querySelectorAll("i.google-symbols")) {
    const t = (i.textContent || "").trim();
    if (t) ligatures[t] = (ligatures[t] || 0) + 1;
  }
  return { url: location.href, navItems, tiles, composers, ligatures };
}
"""


def _parse_capture(captured: dict[str, Any]) -> dict[str, Any]:
    """Shared payload parse (same discovery shape as spike_movie_attach_payload)."""
    out: dict[str, Any] = {
        "url": captured.get("url"),
        "captured": bool(captured.get("post_data")),
        "route": (captured.get("url") or "").split("/")[-1],
    }
    pd = captured.get("post_data")
    if not pd:
        return out
    try:
        body = json.loads(pd)
        reqs = body.get("requests") or []
        ents: list[Any] = []
        imgs: list[Any] = []
        keys: list[Any] = []
        for r in reqs:
            ents += [e.get("entityId") for e in (r.get("referenceEntities") or [])]
            imgs += [i.get("mediaId") for i in (r.get("referenceImages") or [])]
            if r.get("videoModelKey"):
                keys.append(r.get("videoModelKey"))
        out["referenceEntities"] = ents
        out["referenceImages"] = imgs
        out["videoModelKeys"] = keys
        if reqs and isinstance(reqs[0], dict):
            out["request0_keys"] = sorted(reqs[0].keys())
            out["reference_like"] = {
                k: (v if len(json.dumps(v, default=str)) <= 600 else "<elided>")
                for k, v in reqs[0].items()
                if "entit" in k.lower() or "reference" in k.lower() or "input" in k.lower()
            }
    except Exception as e:  # noqa: BLE001
        out["parse_error"] = f"{type(e).__name__}: {e}"
        out["post_data_head"] = pd[:500]
    return out


async def _detect_variant(page: Any) -> dict[str, Any]:
    """Phase A: click Add Media, then poll dialog-appears vs URL-changes.

    A poll loop (100 ms) instead of two racing wait tasks keeps the spike
    simple and shows the timing; the production branch will use a race.
    """
    url_before = page.url
    add = page.locator(ADD_MEDIA_BUTTON).first
    if await add.count() == 0:
        add = page.locator(_ADD_MEDIA_FALLBACK).first
    await add.wait_for(state="visible", timeout=8000)
    await add.click()
    t0 = time.monotonic()
    verdict: dict[str, Any] = {"urlBefore": url_before}
    while time.monotonic() - t0 < 5.0:
        if await page.locator(_OPEN_DIALOG).count() > 0:
            verdict["variant"] = "dialog"
            break
        if page.url != url_before:
            verdict["variant"] = "navigate"
            break
        await asyncio.sleep(0.1)
    else:
        verdict["variant"] = "unknown"
    verdict["elapsedMs"] = round((time.monotonic() - t0) * 1000)
    verdict["urlAfter"] = page.url
    return verdict


async def _stage_entity(page: Any, entity_id: str, out_dir: Path, label: str) -> dict[str, Any]:
    """Right-click the entity tile and click the include action. Tolerant —
    records what it found rather than raising."""
    info: dict[str, Any] = {}
    tile_sel = f"[data-tile-id='fe_id_{entity_id}']" if entity_id else _ENTITY_TILE_PREFIX
    tile = page.locator(tile_sel).first
    await tile.wait_for(state="visible", timeout=8000)
    info["tileId"] = await tile.get_attribute("data-tile-id")
    await tile.scroll_into_view_if_needed(timeout=8000)
    await tile.click(button="right")
    await page.wait_for_timeout(700)
    include = page.locator(", ".join(PICKER_CONTEXT_INCLUDE)).first
    await include.wait_for(state="visible", timeout=5000)
    await include.click()
    await page.wait_for_timeout(800)
    await page.screenshot(path=str(out_dir / f"{label}_after_include.png"))
    return info


async def _submit_and_capture(
    page: Any,
    captured: dict[str, Any],
    out_dir: Path,
    label: str,
    *,
    prompt: str,
) -> dict[str, Any]:
    """Fill the visible (non-dialog) composer textbox, click its submit, and
    wait for the route-aborted capture."""
    captured.clear()
    box = page.locator(_PROMPT_BOX).first
    await box.wait_for(state="visible", timeout=8000)
    await box.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.type(prompt, delay=15)
    await page.wait_for_timeout(400)
    submit = page.locator(_SUBMIT_BTN).first
    await submit.wait_for(state="visible", timeout=8000)
    await submit.click()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not captured:
        await asyncio.sleep(0.3)
    await page.screenshot(path=str(out_dir / f"{label}_after_submit.png"))
    return _parse_capture(captured)


async def _run(
    *,
    profile_dir: Path,
    project_id: str,
    entity_id: str,
    name: str,
    locale: str,
    gestures: list[str],
    out_path: Path,
) -> int:
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    captured: dict[str, Any] = {}
    result: dict[str, Any] = {
        "spike": "issue174-library-ui-recon",
        "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project": project_id,
        "locale": locale,
        "entity": {"id": entity_id, "name": name},
        "gestures": {},
    }

    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001

        async def _on_route(route: Any) -> None:
            req = route.request
            if not captured:  # first generate request per gesture (cleared between)
                try:
                    captured["url"] = req.url
                    captured["post_data"] = req.post_data
                except Exception as e:  # noqa: BLE001
                    captured["error"] = f"{type(e).__name__}: {e}"
            # ABORT — the request never reaches Google; no credit is charged.
            await route.abort()

        try:
            for glob in _GEN_ROUTE_GLOBS:
                await page.route(glob, _on_route)
            editor_url = routes.project_editor_url(locale, project_id)
            step("A", f"goto {editor_url}", prefix="174")
            await page.goto(editor_url, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(4_000)
            await page.keyboard.press("Escape")
            await VideoGenerationMixin._exit_agent_mode(page)  # noqa: SLF001
            await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)  # noqa: SLF001
            await VideoGenerationMixin._switch_video_sub_mode(  # noqa: SLF001
                page, "references", out_dir=None
            )
            await page.wait_for_timeout(800)

            step("A", "variant probe: Add Media -> dialog or navigate?", prefix="174")
            result["phaseA"] = await _detect_variant(page)
            await page.screenshot(path=str(out_dir / "A_after_add_media.png"))
            print(f"[174] phaseA = {result['phaseA']}", flush=True)

            new_ui = result["phaseA"]["variant"] == "navigate"
            if not new_ui:
                # Old UI (or unknown): nothing more to recon here. This IS the
                # rollout re-probe answer for this account.
                result["note"] = "account does not show the new library UI; Phase B/C skipped"
                gestures = []

            # ---- Phase C: DOM dump of the library page ----------------------
            if new_ui:
                step("C", "library page DOM dump", prefix="174")
                try:
                    result["libraryDom"] = await page.evaluate(_DUMP_LIBRARY_JS)
                    await page.screenshot(path=str(out_dir / "C_library_page.png"))
                except Exception as e:  # noqa: BLE001
                    result["libraryDomError"] = f"{type(e).__name__}: {e}"

            # ---- Phase B1: stage + submit from the floating composer --------
            if "b1" in gestures:
                step("B1", "stage entity -> submit from floating composer", prefix="174")
                try:
                    ptab = page.locator(_LIBRARY_PERSONAGENS).first
                    if await ptab.count() > 0 and await ptab.is_visible():
                        await ptab.click()
                        await page.wait_for_timeout(900)
                    stage = await _stage_entity(page, entity_id, out_dir, "B1")
                    cap = await _submit_and_capture(
                        page, captured, out_dir, "B1", prompt=_SPIKE_PROMPT
                    )
                    result["gestures"]["b1_floating_composer"] = {"stage": stage, **cap}
                except Exception as e:  # noqa: BLE001
                    result["gestures"]["b1_floating_composer"] = {
                        "error": f"{type(e).__name__}: {e}"
                    }
                    await page.screenshot(path=str(out_dir / "B1_FAILED.png"))

            # ---- Phase B2: stage, navigate back to editor, submit -----------
            if "b2" in gestures:
                step("B2", "stage entity -> back to editor -> submit", prefix="174")
                try:
                    # Re-enter the library fresh (B1 may have consumed staging).
                    await page.goto(editor_url, wait_until="domcontentloaded", timeout=45_000)
                    await page.wait_for_timeout(3_000)
                    await page.keyboard.press("Escape")
                    await VideoGenerationMixin._exit_agent_mode(page)  # noqa: SLF001
                    await VideoGenerationMixin._switch_to_video_mode(  # noqa: SLF001
                        page, out_dir=None
                    )
                    await VideoGenerationMixin._switch_video_sub_mode(  # noqa: SLF001
                        page, "references", out_dir=None
                    )
                    probe = await _detect_variant(page)
                    result["gestures"]["b2_probe"] = probe
                    if probe["variant"] != "navigate":
                        raise RuntimeError(f"expected navigate, got {probe['variant']}")
                    ptab = page.locator(_LIBRARY_PERSONAGENS).first
                    if await ptab.count() > 0 and await ptab.is_visible():
                        await ptab.click()
                        await page.wait_for_timeout(900)
                    stage = await _stage_entity(page, entity_id, out_dir, "B2")
                    await page.goto(editor_url, wait_until="domcontentloaded", timeout=45_000)
                    await page.wait_for_timeout(3_000)
                    await page.keyboard.press("Escape")
                    # The remounted editor can land in agent/image mode — re-run
                    # the mode-switch sequence or _PROMPT_BOX/_SUBMIT_BTN hit the
                    # wrong composer and B2 reports a false "staging lost".
                    await VideoGenerationMixin._exit_agent_mode(page)  # noqa: SLF001
                    await VideoGenerationMixin._switch_to_video_mode(  # noqa: SLF001
                        page, out_dir=None
                    )
                    await VideoGenerationMixin._switch_video_sub_mode(  # noqa: SLF001
                        page, "references", out_dir=None
                    )
                    cap = await _submit_and_capture(
                        page, captured, out_dir, "B2", prompt=_SPIKE_PROMPT
                    )
                    result["gestures"]["b2_back_to_editor"] = {"stage": stage, **cap}
                except Exception as e:  # noqa: BLE001
                    result["gestures"]["b2_back_to_editor"] = {"error": f"{type(e).__name__}: {e}"}
                    await page.screenshot(path=str(out_dir / "B2_FAILED.png"))
        finally:
            for glob in _GEN_ROUTE_GLOBS:
                try:
                    await page.unroute(glob, _on_route)
                except Exception:  # noqa: BLE001, S110
                    pass
            client._checkin_page(page)  # noqa: SLF001
            out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- compact findings ----------------------------------------------------
    print(f"\n[174] json -> {out_path}", flush=True)
    print(f"[174] variant = {result.get('phaseA', {}).get('variant')}", flush=True)
    for label, g in result.get("gestures", {}).items():
        if "error" in g:
            print(f"[174] {label}: ERROR {g['error']}", flush=True)
        elif "captured" in g:
            print(
                f"[174] {label}: captured={g.get('captured')} "
                f"route={g.get('route')} referenceEntities={g.get('referenceEntities')}",
                flush=True,
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Issue #174: recon the new library UI attach gesture (0 credits)."
    )
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_PROFILE", "denon82"))
    p.add_argument(
        "--project",
        default=os.environ.get("GFLOW_CLI_PROJECT"),
        required="GFLOW_CLI_PROJECT" not in os.environ,
    )
    p.add_argument("--entity-id", dest="entity_id", default="", help="entityId for fe_id_ tile")
    p.add_argument("--name", default="Stickman")
    p.add_argument("--locale", default=os.environ.get("GFLOW_CLI_LOCALE", "pt"))
    p.add_argument(
        "--gestures",
        default="b1,b2",
        help="comma list of gestures to run (b1=floating composer, b2=back-to-editor)",
    )
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    profile_dir = resolve_profile_dir(args.profile)
    out_path = (
        Path(args.out) if args.out else default_out_path("spike_issue174_library_ui", ".json")
    )
    gestures = [g.strip() for g in args.gestures.split(",") if g.strip()]
    step("--", f"profile={args.profile} project={args.project} gestures={gestures}", prefix="174")
    try:
        return asyncio.run(
            _run(
                profile_dir=profile_dir,
                project_id=args.project,
                entity_id=args.entity_id,
                name=args.name,
                locale=args.locale,
                gestures=gestures,
                out_path=out_path,
            )
        )
    except KeyboardInterrupt:
        print("[174] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
