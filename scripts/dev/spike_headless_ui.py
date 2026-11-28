r"""Spike (#1371): can the UI-automation transport drive flow.google.com in HEADLESS Chrome?

$0. Read-only surfaces only; no generate or submit button is ever clicked. The session is
the production one (``FlowApiClient``, which owns the profile lease and launches Chrome with
``headless=<flag>``); the surfaces are driven by the production helpers where they exist:

  composer   ``MigratedComposer.ensure_editor`` on ``/project/<id>``: the settings trigger
             (``.settings-trigger-button``) and the prompt editor (``[contenteditable]``).
  settings   ``MigratedComposer._open_pane``: the option groups (``[role='radiogroup']``),
             the checked radio in each, then the model picker (``arrow_drop_down``) opened
             and its menu items (``[role='menuitem']``) counted, then Escape. Nothing is
             selected.
  characters ``FlowApiClient.list_characters`` (the ``gflow character list`` path; on
             flow.google.com it reads the project through the page).
  media      ``/project/<id>/edit/<media_id>`` for the first grid tile
             (``img[data-media-id]``): a ``video`` or ``img`` and the download button
             (``download`` ligature) visible.

Each failure records the selector, the URL, ``document.body.innerText.length`` (a blank
render), a visible reCAPTCHA challenge frame (``iframe[src*='bframe']``) and a screenshot.

Pre-registered readings (written before any run):
  | Observation                                        | Reading                                  |
  |----------------------------------------------------|------------------------------------------|
  | a surface N/N headless and N/N headed              | headless can drive it                    |
  | a surface fails headless, passes headed            | headless-specific: record the selector,  |
  |                                                    | the URL and the screenshot, not "cannot" |
  | a surface fails in both arms                       | the probe or the account, not headless   |
  | a challenge page / bframe / sign-in redirect       | the session is scored or signed out:     |
  |   headless only                                    | headless is refused on that surface      |
  | mixed within one arm                               | it flaps                                 |

Usage: uv run python scripts/dev/spike_headless_ui.py --profile denon82 [--headless]
       [--runs 3] [--project ID]
Evidence -> scripts/dev/_spike_out/ (gitignored).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.transports._mc_common import (  # noqa: E402
    COMPOSER,
    MENU_ITEM,
    OVERLAY,
    RADIO,
    RADIOGROUP,
    READY_ANCHOR,
    _visible_model_picker,
)
from gflow_cli.api.transports.migrated_composer import MigratedComposer  # noqa: E402
from gflow_cli.api.transports.migrated_upscale import DOWNLOAD_BUTTON_SELECTOR  # noqa: E402

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

_ORIGIN = "https://flow.google.com"
_TILE = "img[data-media-id]"
_MEDIA = "a video or img rendered >= 200 px wide"
#: The 32 px account avatar is an <img> too: "rendered media" means a large one, painted.
_MEDIA_JS = (
    "() => [...document.querySelectorAll('video, img')].some(e => {"
    " const r = e.getBoundingClientRect();"
    " return r.width >= 200 && (e.tagName === 'VIDEO' ? e.readyState > 0 : e.naturalWidth > 0); })"
)
_CHALLENGE = "iframe[src*='bframe']"
_records: list[dict[str, Any]] = []
_OUT: Path | None = None


def _log(tag: str, **data: Any) -> None:
    rec = {"ts": time.strftime("%H:%M:%S"), "tag": tag, **data}
    _records.append(rec)
    if _OUT is not None:
        with _OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    step(tag, json.dumps(data, default=str)[:500])


async def _page_state(page: Any) -> dict[str, Any]:
    """What is on screen: path, body text length, a visible challenge frame."""
    try:
        return {
            "url": page.url.split("?")[0],
            "body_len": await page.evaluate(
                "() => (document.body && document.body.innerText || '').length"
            ),
            "elements": await page.evaluate("() => document.getElementsByTagName('*').length"),
            "challenge_frames": await page.locator(_CHALLENGE).count(),
            "about_landing": "/about" in page.url,
            "signin": "accounts.google.com" in page.url,
        }
    except Exception as exc:  # noqa: BLE001 - a closed page is a finding
        return {"state_error": f"{type(exc).__name__}: {exc}"[:200]}


async def _surface(page: Any, run: str, name: str, selector: str, fn: Any) -> Any:
    t0 = time.perf_counter()
    try:
        detail = await fn()
    except Exception as exc:  # noqa: BLE001 - the failure IS the measurement
        shot = Path(str(_OUT)).with_name(f"{Path(str(_OUT)).stem}_{run}_{name}.png")
        try:
            await page.screenshot(path=str(shot), full_page=False)
        except Exception:  # noqa: BLE001
            shot = None
        _log(
            f"{name}",
            run=run,
            ok=False,
            selector=selector,
            s=round(time.perf_counter() - t0, 1),
            error=f"{type(exc).__name__}: {exc}"[:400],
            screenshot=str(shot) if shot else None,
            **await _page_state(page),
        )
        return None
    _log(f"{name}", run=run, ok=True, s=round(time.perf_counter() - t0, 1), **(detail or {}))
    return detail


async def _pick_project(page: Any) -> str | None:
    """The first project on the home page that has at least one grid tile."""
    await page.goto(_ORIGIN, wait_until="domcontentloaded", timeout=45_000)
    await page.wait_for_timeout(5000)
    hrefs = await page.eval_on_selector_all(
        "a[href*='/project/']", "els => els.map(e => e.getAttribute('href'))"
    )
    ids = list(dict.fromkeys(h.split("/project/")[1].split("/")[0] for h in hrefs if h))
    _log("home", projects=len(ids), **await _page_state(page))
    for pid in ids[:6]:
        await page.goto(f"{_ORIGIN}/project/{pid}", wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.locator(_TILE).first.wait_for(state="attached", timeout=10_000)
            return pid
        except Exception:  # noqa: BLE001, S112 - an empty project: try the next
            continue
    return None


async def one_run(profile_dir: Path, headless: bool, run: str, project: str | None) -> str | None:
    t0 = time.perf_counter()
    async with build_client(profile_dir, headless=headless) as client:
        page = client._page  # noqa: SLF001 - spike-only
        assert page is not None  # noqa: S101 - spike guard
        _log(
            "browser",
            run=run,
            headless=headless,
            launch_s=round(time.perf_counter() - t0, 1),
            navigator_ua=await page.evaluate("navigator.userAgent"),
            webdriver=await page.evaluate("navigator.webdriver"),
        )
        if project is None:
            project = await _pick_project(page)
            _log("project_picked", run=run, project_id=project)
            if project is None:
                return None
        composer = MigratedComposer()

        async def composer_surface() -> dict[str, Any]:
            kind = await composer.ensure_editor(page, project)
            await page.locator(COMPOSER).first.wait_for(state="visible", timeout=10_000)
            return {"kind": kind, **await _page_state(page)}

        async def settings_surface() -> dict[str, Any]:
            pane = await composer._open_pane(page)  # noqa: SLF001
            groups = pane.locator(RADIOGROUP)
            checked = []
            for i in range(await groups.count()):
                sel = groups.nth(i).locator(f"{RADIO}[aria-checked='true']")
                checked.append(
                    " ".join((await sel.first.inner_text(timeout=2000)).split())
                    if await sel.count()
                    else None
                )
            picker = await _visible_model_picker(page, pane, what="model picker")
            bound = " ".join((await picker.inner_text(timeout=2000)).split())
            await picker.click(timeout=5000)
            items = page.locator(f"{OVERLAY} {MENU_ITEM}")
            await items.first.wait_for(state="visible", timeout=5000)
            n_items = await items.count()
            await composer._close_pane(page, strict=False)  # noqa: SLF001 - Escape x2
            return {
                "groups": len(checked),
                "checked": checked,
                "model_bound": bound,
                "menu_items": n_items,
            }

        async def characters_surface() -> dict[str, Any]:
            chars = await client.list_characters(project)
            return {"characters": len(chars)}

        async def media_surface() -> dict[str, Any]:
            if "/project/" not in page.url or "/edit/" in page.url:
                await composer.ensure_editor(page, project)
            tile = page.locator(_TILE).first
            await tile.wait_for(state="attached", timeout=15_000)
            media_id = await tile.get_attribute("data-media-id")
            await page.goto(
                f"{_ORIGIN}/project/{project}/edit/{media_id}",
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            await composer._dismiss_dialog(page)  # noqa: SLF001 - e.g. a bonus-credits modal
            await page.wait_for_function(_MEDIA_JS, timeout=20_000)
            await page.locator(DOWNLOAD_BUTTON_SELECTOR).first.wait_for(
                state="visible", timeout=10_000
            )
            return {
                "videos": await page.locator("video").count(),
                "on_edit_route": f"/edit/{media_id}" in page.url,
                **await _page_state(page),
            }

        await _surface(page, run, "composer", f"{READY_ANCHOR} + {COMPOSER}", composer_surface)
        await _surface(
            page, run, "settings", f"{OVERLAY} {RADIOGROUP} / {MENU_ITEM}", settings_surface
        )
        await _surface(
            page, run, "characters", "list_characters (Zzl0ze page read)", characters_surface
        )
        await _surface(page, run, "media", f"{_TILE} -> {_MEDIA} + download", media_surface)
    return project


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--profile", default="denon82")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--project", help="a project with at least one media item (default: picked)")
    args = ap.parse_args()
    global _OUT  # noqa: PLW0603 - spike-wide evidence sink
    arm = "headless" if args.headless else "headed"
    _OUT = default_out_path(f"spike_headless_ui_{args.profile}_{arm}", ".jsonl")
    step("OUTPUT", str(_OUT))
    project = args.project
    profile_dir = resolve_profile_dir(args.profile)
    for i in range(1, args.runs + 1):
        try:
            project = await one_run(profile_dir, args.headless, f"{arm}{i}", project)
        except Exception as exc:  # noqa: BLE001 - a launch failure is a finding too
            _log("run_failed", run=f"{arm}{i}", error=f"{type(exc).__name__}: {exc}"[:400])
    ok = [r for r in _records if "ok" in r]
    summary = {
        name: f"{sum(r['ok'] for r in ok if r['tag'] == name)}/"
        f"{sum(1 for r in ok if r['tag'] == name)}"
        for name in ("composer", "settings", "characters", "media")
    }
    _log("summary", arm=arm, project_id=project, **summary)


if __name__ == "__main__":
    asyncio.run(main())
