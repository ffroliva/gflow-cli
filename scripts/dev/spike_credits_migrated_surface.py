r"""Can gflow read per-model credit prices and the credit balance on flow.google.com? ($0)

QUESTION. The spend-cap workstream (community-feedback-uplift WS2) wants to refuse a paid
run whose cost exceeds a cap. On labs, `trpc/flow.projectInitialData` carries both the
per-model `creditMapping` and `remainingCredits` (docs/superpowers/spikes/2026-08-31-veo-
extend-route-recon.md). On accounts served flow.google.com, #795 says the balance surface
"has not been located", and the 2026-09-05 wire spike saw a composer cost line
("Generating will use N credits") but never looked at the wire for prices or a balance.
Nobody has measured either on this host. This does.

COST: $0. Two navigations, DOM reads, and typing a throwaway prompt into the composer that
is cleared again. Nothing is submitted, created, uploaded or deleted. The balance read
through `api/credits.py` is an HTTP GET.

WHAT IT RECORDS
  * the host that actually served (never assumed from the account)
  * every response from flow.google.com batchexecute, labs trpc and aisandbox-pa: rpcid or
    route, status, size, and whether the body carries credit-shaped keys
    (credit*, remaining*, paygate*, *tier*). Matched key names and short snippets go to
    the gitignored capture only; the printed summary carries key names and counts.
  * DOM, before and after typing a prompt: text lines that pair a number with "credit"
    (discovery only, never a production anchor), aria-labels mentioning credits, the
    `prompt-warning-button` count, and `mat-icon` ligatures present.
  * `fetch_credits_http` outcome for the same profile, before the browser starts.

PRE-REGISTERED READING (written before the run; commit this file before the data exists)
  * A batchexecute response on project load carries credit-shaped keys with numbers
    -> price and/or balance are on the wire. WS2 is buildable from the wire; next step is
       decoding that rpcid, not DOM scraping.
  * No wire keys, but a DOM cost line with a number appears once a prompt is typed
    -> the declared price is readable before submit from the DOM. A cap on declared price
       is buildable (needs a structural anchor for the line, not its text). The balance is
       still unknown, so the cap cannot compare against the balance.
  * Neither
    -> **UNMEASURED**, not absent: the settings/count panel was not opened, and the cost
       line lives under its count row (2026-09-05). Do NOT record "no prices on this host".
       What would settle it: the HAR harness with a human opening the settings panel.
  * The profile is signed out, lands on /about, or is served labs
    -> UNMEASURED for this host. Say which, and stop.
  * `fetch_credits_http` succeeds on this profile
    -> #795's premise no longer holds for this account; re-read #795 before planning.

RUN 1 (2026-09-14, before v2 existed): `denon82` landed on /about -> UNMEASURED. `ci-probe`
reached a project; DOM showed no credit text before/after typing; the only credit-shaped wire
hit was `cPZSdc`, a promotional banner. Two blind spots were then named, not spun:
  (a) batchexecute payloads are POSITIONAL arrays -- a balance or price is a bare number with
      no key, invisible to the key scan;
  (b) the cost line lives in the settings pane, which run 1 never opened.

V2 ADDENDUM (written before run 2):
  * `--open-settings`: click gflow's own READY_ANCHOR `.settings-trigger-button`, read the
    visible `.cdk-overlay-pane` text, record responses fired while it is open, close with two
    Escapes (PANE_CLOSE_ESCAPES). Opening a pane is free; nothing is selected or submitted.
  * batchexecute bodies >= 1 KB are saved to the gitignored capture, and each is searched for
    model tokens (veo, omni, nano, imagen, lite, fast, quality) with the integers near them.
  Readings for v2:
  * pane text pairs a number with credits -> declared price is readable pre-submit from the
    DOM (cap on declared price buildable; anchor must be structural).
  * a body lists model tokens with small integers matching the pane's number -> price is on
    the wire under that rpcid (decode it; prefer it over DOM text).
  * a body carries a large integer that matches the account's known plan allowance only if a
    second source confirms it -> candidate balance; one run is a LEAD, not a finding.
  * pane opens but shows no number -> price not shown at this state; UNMEASURED for other
    model/count selections, not absent.

    python scripts/dev/spike_credits_migrated_surface.py --profile ci-probe --open-settings
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import Page, Response

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)
from gflow_cli.api.transports.migrated_composer import COMPOSER  # noqa: E402

MIGRATED_ROOT = "https://flow.google.com/"
_WATCHED = ("flow.google.com", "labs.google", "aisandbox-pa.googleapis.com")
_CREDIT_KEY = re.compile(r'"?([A-Za-z_]*(?:credit|remaining|paygate|Tier)[A-Za-z_]*)"?', re.I)
_PROMPT = "spike probe, not submitted"
_MODEL_TOKEN = re.compile(r"(veo|omni|nano|imagen|lite|fast|quality)[^\"]{0,40}", re.I)
_SETTINGS_TRIGGER = ".settings-trigger-button"  # migrated_composer.READY_ANCHOR
_PANE = ".cdk-overlay-pane:visible"  # migrated_composer.VISIBLE_OVERLAY
_STEM = "spike_credits_migrated_surface"

# `__COMPOSER__` is substituted below with migrated_composer.COMPOSER — a JS string
# literal cannot hold a Python constant, so the selector is interpolated, never re-typed.
_DOM_JS = r"""
() => {
  const lines = (document.body.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
  const creditLines = lines.filter(l => /\d/.test(l) && /credit/i.test(l)).slice(0, 20);
  const aria = [...document.querySelectorAll('[aria-label]')]
    .map(e => e.getAttribute('aria-label')).filter(a => /credit/i.test(a)).slice(0, 20);
  // Flow's icons are Material Symbols LIGATURES, not labels: the glyph name is the
  // element's text and is the one locale-invariant handle on this UI (memory:
  // flow-locale-leak-icon-ligatures). `mat-icon` is the labs-era tag, `i.google-symbols`
  // the migrated one; both are collected so a run says which shell it saw.
  const icons = [...new Set([...document.querySelectorAll('mat-icon, i.google-symbols')]
    .map(e => (e.textContent || '').trim()).filter(Boolean))].slice(0, 80);
  return {
    url: location.href,
    // `aisandbox-root` is the graduated app's Angular root — same name as
    // aisandbox-pa.googleapis.com, one product lineage. It is NOT a signed-in signal:
    // the migrated marketing page mounts the same shell (spike_host_lane.py).
    aisandbox_root: document.querySelectorAll('aisandbox-root').length,
    mat_icon: document.querySelectorAll('mat-icon').length,
    google_symbols: document.querySelectorAll('i.google-symbols').length,
    // Flow's pre-submit warning chip (quota / policy / cost notices hang off it); its
    // count is the cheapest structural signal that the composer is warning about
    // something, without reading its translated text.
    warning_button: document.querySelectorAll('button.prompt-warning-button').length,
    project_links: [...new Set([...document.querySelectorAll('a[href*="/project/"]')]
      .map(a => a.getAttribute('href')))].slice(0, 5),
    editors: document.querySelectorAll("__COMPOSER__").length,
    credit_lines: creditLines,
    credit_aria: aria,
    icons,
  };
}
""".replace("__COMPOSER__", COMPOSER)


def _route(url: str) -> str:
    match = re.search(r"rpcids=([^&]+)", url)
    if match:
        return f"batchexecute:{match.group(1)}"
    return re.sub(r"\?.*$", "", url.split("//", 1)[-1])[:120]


async def _balance_via_http(profile_dir: Path) -> dict[str, Any]:
    from gflow_cli.api.credits import fetch_credits_http

    try:
        info = await fetch_credits_http(profile_dir)
        return {"ok": True, "info": repr(info)[:300]}
    except Exception as exc:  # noqa: BLE001 - a spike records every outcome
        return {"ok": False, "error_type": type(exc).__name__, "detail": str(exc)[:300]}


async def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--profile", required=True)
    ap.add_argument("--settle", type=float, default=8.0)
    ap.add_argument("--open-settings", action="store_true")
    args = ap.parse_args()
    phase = ["load"]
    profile_dir = resolve_profile_dir(args.profile)
    out: dict[str, Any] = {"profile": args.profile, "responses": [], "dom": {}}

    step("http", "fetch_credits_http before the browser starts")
    out["balance_http"] = await _balance_via_http(profile_dir)

    async with build_client(profile_dir) as client:
        page: Page | None = None
        pending: list[asyncio.Task[None]] = []
        try:
            context = client._context  # noqa: SLF001 - spike reads the live context
            assert context is not None

            async def record(response: Response) -> None:
                url = response.url
                if not any(host in url for host in _WATCHED):
                    return
                entry: dict[str, Any] = {"route": _route(url), "status": response.status}
                try:
                    body = await response.text()
                except Exception as exc:  # noqa: BLE001 - redirects/aborted bodies have none
                    # An unread body would otherwise look identical to "no credits on the
                    # wire": size=0, no credit_keys, no model_numbers. Name the failure so
                    # a reader can tell a real negative from a read that never happened.
                    # Class name only — the message can carry a token.
                    body = ""
                    entry["body_error"] = type(exc).__name__
                entry["size"] = len(body)
                entry["phase"] = phase[0]
                if entry["route"].startswith("batchexecute:") and len(body) >= 1024:
                    entry["body"] = body
                    entry["model_numbers"] = [
                        body[max(0, m.start() - 60) : m.end() + 60]
                        for m in _MODEL_TOKEN.finditer(body)
                    ][:12]
                keys = sorted({m.group(1) for m in _CREDIT_KEY.finditer(body)})
                if keys:
                    entry["credit_keys"] = keys[:30]
                    entry["snippets"] = [
                        body[max(0, m.start() - 40) : m.end() + 60]
                        for m in list(_CREDIT_KEY.finditer(body))[:6]
                    ]
                out["responses"].append(entry)

            context.on("response", lambda r: pending.append(asyncio.ensure_future(record(r))))
            page = await context.new_page()

            step("nav", MIGRATED_ROOT)
            await page.goto(MIGRATED_ROOT, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(int(args.settle * 1000))
            out["dom"]["root"] = await page.evaluate(_DOM_JS)
            links = out["dom"]["root"]["project_links"]
            if "/about" in page.url or not links:
                out["verdict_hint"] = "UNMEASURED: signed out, /about, or no project grid"
            else:
                target = (
                    links[0]
                    if links[0].startswith("http")
                    else f"https://flow.google.com{links[0]}"
                )
                step("nav", "first existing project (nothing created)")
                await page.goto(target, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(int(args.settle * 1000))
                out["dom"]["project_before_typing"] = await page.evaluate(_DOM_JS)
                editor = page.locator(COMPOSER).first
                if await editor.count():
                    step("type", "throwaway prompt, never submitted")
                    await editor.click()
                    await page.keyboard.type(_PROMPT, delay=20)
                    await page.wait_for_timeout(3000)
                    out["dom"]["project_after_typing"] = await page.evaluate(_DOM_JS)
                    if args.open_settings:
                        trigger = page.locator(_SETTINGS_TRIGGER).first
                        if await trigger.is_visible():
                            step("pane", "open settings (free; nothing selected)")
                            phase[0] = "settings_open"
                            await trigger.click()
                            await page.wait_for_timeout(3000)
                            panes = page.locator(_PANE)
                            texts = [
                                await panes.nth(i).inner_text() for i in range(await panes.count())
                            ]
                            out["settings_pane"] = {
                                "panes": len(texts),
                                "credit_lines": [
                                    ln.strip()
                                    for t in texts
                                    for ln in t.splitlines()
                                    if re.search(r"\d", ln) and re.search(r"credit", ln, re.I)
                                ],
                                "text_sample": [t[:600] for t in texts],
                            }
                            for _ in range(2):  # migrated_composer.PANE_CLOSE_ESCAPES
                                await page.keyboard.press("Escape")
                                await page.wait_for_timeout(500)
                            phase[0] = "after_settings"
                        else:
                            out["settings_pane"] = "settings trigger not visible (agent mode?)"
                    await editor.click()
                    await page.keyboard.press("Control+A")
                    await page.keyboard.press("Delete")
                    await page.wait_for_timeout(1000)
                    out["dom"]["project_after_clearing"] = await page.evaluate(_DOM_JS)
                else:
                    out["dom"]["project_after_typing"] = "no contenteditable editor found"
        finally:
            # Capture BEFORE any teardown. A goto timeout, a click that misses, a failed
            # assert — anything raising above would otherwise close the browser with every
            # response and DOM read still only in memory (memory:
            # capture-evidence-before-any-teardown). The page is still alive here; the
            # browser is closed by build_client on the way out of this `async with`, so
            # nothing in this block races a teardown.
            settled = await asyncio.gather(*pending, return_exceptions=True)
            out["recorder_errors"] = [
                type(r).__name__ for r in settled if isinstance(r, BaseException)
            ]
            if page is not None:
                try:
                    shot = default_out_path(_STEM, ".png")
                    await page.screenshot(path=str(shot))
                    out["screenshot"] = shot.name
                except Exception as exc:  # noqa: BLE001 - a dead page must not eat the JSON
                    out["screenshot_error"] = type(exc).__name__
            path = default_out_path(_STEM)
            path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
            step("wrote", str(path))

    print("\nbalance_http:", {k: v for k, v in out["balance_http"].items() if k != "info"})
    for name, dom in out["dom"].items():
        if isinstance(dom, dict):
            print(
                f"dom[{name}] url={dom['url'][:70]} aisandbox_root={dom['aisandbox_root']} "
                f"mat_icon={dom['mat_icon']} warning={dom['warning_button']} "
                f"credit_lines={dom['credit_lines']} credit_aria={dom['credit_aria']}"
            )
        else:
            print(f"dom[{name}] {dom}")
    pane = out.get("settings_pane")
    if isinstance(pane, dict):
        print(f"settings_pane panes={pane['panes']} credit_lines={pane['credit_lines']}")
    elif pane:
        print("settings_pane:", pane)
    for r in out["responses"]:
        if r.get("model_numbers"):
            n = len(r["model_numbers"])
            print(f"  MODEL-TOKENS {r['route']} phase={r['phase']} size={r['size']} n={n}")
    carriers = [r for r in out["responses"] if r.get("credit_keys")]
    # Name every route, not just the credit-shaped ones: an rpcid inventory quoted in a
    # write-up has to be reproducible from stdout, not only from the gitignored capture.
    sizes: dict[str, int] = {}
    for r in out["responses"]:
        sizes[r["route"]] = max(sizes.get(r["route"], 0), r["size"])
    print(f"responses watched: {len(out['responses'])}, distinct routes: {len(sizes)}")
    for route, size in sorted(sizes.items()):
        print(f"  route {route} max_size={size}")
    for r in carriers:
        keys = r["credit_keys"][:12]
        print(f"  CREDIT-SHAPED {r['route']} status={r['status']} size={r['size']} keys={keys}")
    if not carriers:
        print("  no credit-shaped keys in any watched response")
    # A body that could not be read is size=0 with no keys — identical to a real negative.
    # These two counts are what separates "nothing on the wire" from "we never looked".
    unreadable = sorted(r["body_error"] for r in out["responses"] if r.get("body_error"))
    if unreadable:
        print(
            f"  UNREADABLE BODIES: {len(unreadable)} {unreadable[:8]} — a read failure, "
            "not an absence; every negative above is that many responses short"
        )
    if out.get("recorder_errors"):
        print(f"  RECORDER FAILURES: {len(out['recorder_errors'])} {out['recorder_errors'][:8]}")
    print("verdict_hint:", out.get("verdict_hint", "read the pre-registered table"))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
