"""Spike: does clicking the primary CTA on `flow.google.com/about` recover the session? (#881, #888)

**The question.** PR #881 recovers from Flow's public landing page by clicking its
"Create with Google Flow" button, on the premise that `/about` means the session lacks
a `flow.google.com` session and the CTA hops to Google's account chooser. Nobody has
measured the click. #756 measured the redirect with `gflow auth status` reporting the
session verified, and on 2026-09-20 `denon82` reproduced `/about` on `gflow project
create` while `ffroliva` and `ci-probe` did not (9 of the 13 canary failures in #888).

**Method.** Through `FlowApiClient` (so the profile lease is held and the page is the
one gflow drives):

1. Control: navigate `https://flow.google.com/` twice with no click, record where the
   client-side hop settles (production `flow_landing_kind`).
2. Inventory `/about`: every button and link — tag, class, role, href, aria-label,
   icon ligature, visibility, and what `elementFromPoint` returns over it. Display text
   is recorded for the reader only; nothing anchors on it.
3. Click the first visible match of the PR's STRUCTURAL arms (`button.flow-button.
   variant-primary`, `button.cta-button`). The text arm is counted, never clicked.
4. Record the main-frame navigation chain and every request host (no paths, no queries).
   If it lands on the chooser, hand it to production `client._handle_account_chooser`.
   A chooser hop during BOOTSTRAP is already resolved by the client before step 1.
5. Persistence: navigate `https://flow.google.com/` again and record where it settles.

Cookie NAMES (never values) for `flow.google.com` are recorded before and after, which
is the premise's own claim. Screenshots go to the gitignored `_spike_out/`.

**Cost.** Navigation, clicks and a Google account-row pick only. No project is
created, nothing is submitted: **zero credits, zero quota.**

**How to read the result (written before the run).**

| Outcome | Reading |
|---|---|
| control not `/about` on either visit | **does not reproduce today; settles NOTHING** |
| click -> Flow app, step 5 not `/about` | **RECOVERS** — PR premise holds |
| click -> chooser -> pick -> Flow app, step 5 not `/about` | **RECOVERS via chooser** |
| click -> anywhere, step 5 on `/about` again | **DOES NOT RECOVER** — not a fix |
| click -> Google password / sign-in form | needs a human; not an automatic recovery |
| no structural arm matches | evidence about the SELECTOR; read the inventory |

*Amended after the first runs (council on #903), table left as registered.* The rows
overlap: the observed runs fit both "step 5 on `/about` again" and "click -> sign-in
form", and the first version graded only step 5, so it printed DOES NOT RECOVER.
`_verdict` now checks the click landing first (sign-in -> NEEDS A HUMAN), and grades
RECOVERS only when step 5 renders `aisandbox-root`. Anything else is INCONCLUSIVE.

Scope: one profile is one account. This answers "does the click recover THIS account,
now" — nothing about a cohort.

    python scripts/dev/spike_about_cta_recovery.py --profile denon82
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)
from gflow_cli.api.transports._common import safe_page_url  # noqa: E402

FLOW_ROOT = "https://flow.google.com/"
STRUCTURAL_ARMS = ("button.flow-button.variant-primary", "button.cta-button")
TEXT_ARM = "button:has-text('Create with Google Flow')"

_INVENTORY_JS = """
() => [...document.querySelectorAll('button, a, [role=button], [role=link]')].map(e => {
  const r = e.getBoundingClientRect();
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const top = (r.width && r.height) ? document.elementFromPoint(cx, cy) : null;
  const icon = e.querySelector('mat-icon, i.google-symbols, .material-symbols-outlined');
  return {
    tag: e.tagName.toLowerCase(),
    cls: e.className && e.className.baseVal === undefined ? String(e.className) : '',
    role: e.getAttribute('role'),
    href: e.getAttribute('href'),
    aria: e.getAttribute('aria-label'),
    jsname: e.getAttribute('jsname'),
    icon: icon ? icon.textContent.trim() : null,
    text: (e.innerText || '').trim().slice(0, 60),
    visible: !!(r.width && r.height) && getComputedStyle(e).visibility !== 'hidden',
    occluded_by: top && !e.contains(top) ? top.tagName.toLowerCase() : null,
  };
})
"""


async def _cookie_names(page: Any) -> list[str]:
    cookies = await page.context.cookies(FLOW_ROOT)
    return sorted({c["name"] for c in cookies})


async def _settle(page: Any, timeout_s: float) -> dict[str, Any]:
    """Wait for Flow's client-side hop to finish, then classify with production code."""
    from gflow_cli.api.transports._common import (
        UNAVAILABLE_SCREEN,
        flow_host_kind,
        flow_landing_kind,
    )

    started = time.monotonic()
    deadline = started + timeout_s
    last, stable_since, settled = "", started, False
    while time.monotonic() < deadline:
        url = page.url
        if url != last:
            last, stable_since = url, time.monotonic()
        # ponytail: 4 s without a URL change is "settled" — the /about hop lands within
        # ~2 s of goto; a navigation committing later than 4 s would be missed.
        if time.monotonic() - stable_since >= 4.0:
            settled = True
            break
        await asyncio.sleep(0.5)
    url = page.url
    return {
        "url": safe_page_url(url),
        "settled": settled,
        "elapsed_s": round(time.monotonic() - started, 2),
        "host_kind": flow_host_kind(url),
        "landing_kind": flow_landing_kind(url),
        "app_root": await page.locator("aisandbox-root").count(),
        "unavailable_screen": await page.locator(UNAVAILABLE_SCREEN).count(),
    }


async def _visit_root(page: Any, label: str, out_dir: Path, timeout_s: float) -> dict[str, Any]:
    await page.goto(FLOW_ROOT, wait_until="domcontentloaded", timeout=60_000)
    landed = await _settle(page, timeout_s)
    await page.screenshot(path=str(out_dir / f"{label}.png"))
    step(label, json.dumps(landed))
    return landed


async def _run(profile: str, timeout_s: float, follow_chooser: bool) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")
    out = default_out_path("about_cta_recovery")
    shots = out.with_suffix("")
    shots.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {"profile": profile, "verdict": "INCOMPLETE — see `error`"}
    nav: list[dict[str, Any]] = []
    reqs: list[str] = []

    # Written in `finally`: an exception mid-run (a goto timeout, the chooser handler
    # raising) must not throw away the controls and inventory already gathered.
    try:
        async with build_client(profile_dir) as client:
            await _measure(client, record, nav, reqs, shots, timeout_s, follow_chooser)
    except Exception as exc:  # noqa: BLE001 — the failure is part of the evidence
        record["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        record["nav_chain"] = nav
        record["request_hosts"] = sorted({urlsplit(u).netloc for u in reqs})
        out.write_text(json.dumps(record, indent=2), encoding="utf-8")
        step("verdict", record["verdict"])
        step("out", str(out))
    return 0


async def _measure(
    client: Any,
    record: dict[str, Any],
    nav: list[dict[str, Any]],
    reqs: list[str],
    shots: Path,
    timeout_s: float,
    follow_chooser: bool,
) -> None:
    page = client._page  # noqa: SLF001 — dev instrument
    t0 = time.monotonic()

    def _on_nav(frame: Any) -> None:
        if frame == page.main_frame:
            nav.append({"t": round(time.monotonic() - t0, 2), "url": safe_page_url(frame.url)})

    page.on("framenavigated", _on_nav)
    page.on("request", lambda r: reqs.append(safe_page_url(r.url)))

    record["after_bootstrap"] = safe_page_url(page.url)
    record["control"] = [await _visit_root(page, f"control_{i}", shots, timeout_s) for i in (1, 2)]
    if not all(c["landing_kind"] == "public" for c in record["control"]):
        record["verdict"] = (
            "DOES NOT REPRODUCE today — control did not land on /about both times. "
            "Settles NOTHING about the click."
        )
        return

    record["cookies_before"] = await _cookie_names(page)
    inventory = await page.evaluate(_INVENTORY_JS)
    for item in inventory:  # hrefs can carry ?continue= / authuser — keep the path only
        item["href"] = safe_page_url(item["href"]) if item["href"] else None
    record["inventory"] = inventory
    record["arm_counts"] = {
        sel: await page.locator(sel).count() for sel in (*STRUCTURAL_ARMS, TEXT_ARM)
    }
    step("arms", json.dumps(record["arm_counts"]))

    target = None
    for sel in STRUCTURAL_ARMS:
        loc = page.locator(sel)
        for i in range(await loc.count()):
            if await loc.nth(i).is_visible():
                target = (sel, i)
                break
        if target:
            break
    if target is None:
        record["verdict"] = (
            "NO STRUCTURAL ARM MATCHED a visible element — evidence about the "
            "selector, not the feature. Read `inventory`."
        )
        return

    record["clicked"] = {"selector": target[0], "index": target[1]}
    nav_mark = len(nav)
    await page.locator(target[0]).nth(target[1]).click()
    record["after_click"] = await _settle(page, timeout_s)
    await page.screenshot(path=str(shots / "after_click.png"))
    step("after_click", json.dumps(record["after_click"]))

    if record["after_click"]["landing_kind"] == "chooser" and follow_chooser:
        record["chooser_picked"] = await client._handle_account_chooser(page)  # noqa: SLF001
        record["after_chooser"] = await _settle(page, timeout_s)
        await page.screenshot(path=str(shots / "after_chooser.png"))
        step("after_chooser", json.dumps(record["after_chooser"]))

    record["click_nav_chain"] = nav[nav_mark:]
    record["cookies_after"] = await _cookie_names(page)
    record["persistence"] = await _visit_root(page, "persistence", shots, timeout_s)
    record["verdict"] = _verdict(record)


def _verdict(record: dict[str, Any]) -> str:
    """Map the measurements onto the docstring's pre-registered rows, in table order."""
    click_kind = record["after_click"]["landing_kind"]
    after = record["persistence"]
    if click_kind == "signin":
        return "NEEDS A HUMAN — the click landed on a Google sign-in surface."
    if after["landing_kind"] == "public":
        return "DOES NOT RECOVER — /about again on the follow-up visit."
    # `flow_landing_kind` None means "nothing recognised", never "this is the app"
    # (_common.py) — so recovery needs the app root actually rendered.
    if after["landing_kind"] is None and after["app_root"] > 0:
        via = " via chooser" if record.get("chooser_picked") else ""
        return f"RECOVERS{via} — follow-up visit renders the app."
    return "INCONCLUSIVE — follow-up visit is neither /about nor the app; read the screenshot."


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True, help="profile name, e.g. denon82")
    ap.add_argument("--timeout-s", type=float, default=30.0, help="max settle wait per step")
    ap.add_argument(
        "--no-follow-chooser",
        action="store_true",
        help="stop at the chooser instead of letting production code pick the account",
    )
    args = ap.parse_args()
    return asyncio.run(_run(args.profile, args.timeout_s, not args.no_follow_chooser))


if __name__ == "__main__":
    raise SystemExit(main())
