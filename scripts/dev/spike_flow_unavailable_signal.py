"""Spike: what signal identifies "this account has no Flow entitlement"? (2026-09-15)

QUESTION
    gflow must distinguish "the user abandoned the Flow sign-in" from "this account
    can never sign in to Flow". The session API cannot: both are HTTP 200 with an
    empty `user`. The browser lands on `flow.google.com/unavailable` (sometimes
    `/u/<n>/unavailable`) — but a URL path is the weakest anchor available, and the
    path shape has already been observed varying.

    So: is there a WIRE-level or DOM-level signal that says it outright?

PRE-REGISTERED READINGS — decided before running, so the data can refute them.

    R1  A response carries an explicit entitlement/eligibility/tier field
        -> anchor the fix there. Also fixes the non-browser paths: `gflow project
           list` currently exits 0 with an empty list on this account.

    R2  Only the redirect to /unavailable distinguishes it
        -> the path match is the best available. Record that it is a path match and
           why, rather than implying it is structural.

    R3  The navigation itself is an HTTP 3xx to /unavailable
        -> stronger than a settled-URL read: a status code, available before any DOM.

    R4  Nothing distinguishes it at all
        -> the fix cannot be made robust from here; say so and stop.

    A null reading is a real reading. "No entitlement field anywhere" is R2
    confirmed, not a failed run.

SAFETY
    Read-only: navigates, records, never submits. Costs nothing. Output goes to the
    gitignored scripts/dev/_spike_out/. Bodies are truncated and run through
    redact_sensitive_text before they touch disk — captures carry Bearer tokens and
    cookies, and this file must stay pasteable into an issue.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts" / "dev"))

from _spike_common import resolve_profile_dir  # noqa: E402

from gflow_cli.profile_lease import ProfileLease  # noqa: E402
from gflow_cli.redaction import redact_sensitive_text  # noqa: E402

# This spike needs a profile for an account with NO Flow entitlement, and which one
# that is cannot be checked in: it names a real account. Taken from argv, or from
# GFLOW_SPIKE_PROFILE, and resolved through gflow's own profile root rather than a
# typed-out path, which is both machine-specific and what `check_repo_hygiene.py`
# rejects. A brand-new free Google account reproduces it; so does any account whose
# browser lands on https://flow.google.com/unavailable.
PROFILE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GFLOW_SPIKE_PROFILE", "")
if not PROFILE:
    print(
        "[spike] usage: python scripts/dev/spike_flow_unavailable_signal.py <profile>",
        "(or set GFLOW_SPIKE_PROFILE). Use a profile whose account has NO Flow access",
        "- the one whose browser lands on flow.google.com/unavailable.",
        sep="\n",
        file=sys.stderr,
    )
    raise SystemExit(2)
PROFILE_DIR = resolve_profile_dir(PROFILE)
OUT_DIR = Path(__file__).resolve().parent / "_spike_out"
START_URL = "https://flow.google.com/"

# Keys worth noticing in any JSON body. Deliberately broad: the point is to find
# out whether such a field exists at all, not to confirm a guess about its name.
INTERESTING = (
    "entitle",
    "eligib",
    "tier",
    "subscription",
    "subscribed",
    "plan",
    "access",
    "allowed",
    "permission",
    "unavailable",
    "quota",
    "credit",
)


def _hits(text: str) -> list[str]:
    low = text.casefold()
    return [k for k in INTERESTING if k in low]


async def main() -> int:
    from playwright.async_api import async_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    findings: dict[str, Any] = {
        "question": "what identifies an unentitled Flow account?",
        "profile": PROFILE_DIR.name,
        "start_url": START_URL,
        "responses": [],
        "redirect_chain": [],
        "final_url": None,
        "dom": {},
    }

    # Two Chrome instances on one user_data_dir corrupt the profile, and an unleased
    # browser is indistinguishable from an orphan in another operator's process list —
    # which is how a 2026-09-07 session killed nine browsers belonging to a running e2e
    # suite. Gated by tests/scripts/test_spike_profile_lease.py.
    async with ProfileLease(PROFILE_DIR), async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        async def on_response(resp: Any) -> None:
            url = str(resp.url)
            if "google" not in url:
                return
            entry: dict[str, Any] = {"url": url.split("?")[0], "status": resp.status}
            ctype = (resp.headers or {}).get("content-type", "")
            if "json" in ctype.lower():
                try:
                    body = await resp.text()
                except Exception:  # noqa: BLE001 - a body we cannot read is still a row
                    body = ""
                hits = _hits(body)
                if hits:
                    entry["keyword_hits"] = hits
                    # Redact THEN truncate. The other order slices a Bearer token in half at
                    # byte 600, the pattern stops matching, and the fragment lands on disk.
                    entry["body_snippet"] = redact_sensitive_text(body)[:600]
            findings["responses"].append(entry)

        page.on("response", on_response)

        resp = await page.goto(START_URL, wait_until="domcontentloaded", timeout=45_000)
        if resp is not None:
            chain: list[dict[str, Any]] = []
            r: Any = resp
            while r is not None:
                chain.append({"url": str(r.url).split("?")[0], "status": r.status})
                # `Request.response()` is async. Un-awaited, `r` became a coroutine and
                # the next `r.url` raised -- latent because this capture had no 3xx at all,
                # so R3's refutation rests on `resp.status == 200`, never on this walk.
                prev = r.request.redirected_from
                r = await prev.response() if prev is not None else None
            findings["redirect_chain"] = list(reversed(chain))

        # Flow's hop is client-side, so the URL right after goto is read too early.
        await page.wait_for_timeout(6_000)
        findings["final_url"] = str(page.url).split("?")[0]

        # DOM: structure only. Any anchor we could key on must not be a text label.
        findings["dom"] = await page.evaluate(
            """() => {
              const el = document.querySelector('body');
              const tags = [...new Set([...document.querySelectorAll('body *')]
                    .map(n => n.tagName.toLowerCase()))].slice(0, 40);
              const ids = [...document.querySelectorAll('[id]')].map(n => n.id).slice(0, 25);
              const roles = [...new Set([...document.querySelectorAll('[role]')]
                    .map(n => n.getAttribute('role')))].slice(0, 20);
              const dataAttrs = [...new Set([].concat(...[...document.querySelectorAll('*')]
                    .map(n => [...n.attributes].map(a => a.name)
                    .filter(a => a.startsWith('data-')))))].slice(0, 25);
              return {
                title: document.title,
                tags, ids, roles, dataAttrs,
                anchors: [...document.querySelectorAll('a[href]')]
                          .map(a => a.getAttribute('href')).slice(0, 15),
                bodyTextLen: (el ? el.innerText || '' : '').length,
              };
            }"""
        )
        await ctx.close()

    out = OUT_DIR / "flow-unavailable-signal.json"
    out.write_text(json.dumps(findings, indent=2), encoding="utf-8")

    print(f"final_url      : {findings['final_url']}")
    print(f"redirect chain : {findings['redirect_chain']}")
    print(f"responses      : {len(findings['responses'])}")
    flagged = [r for r in findings["responses"] if "keyword_hits" in r]
    print(f"keyword hits   : {len(flagged)}")
    for r in flagged[:12]:
        print(f"   {r['status']}  {r['url']}   {r['keyword_hits']}")
    print(f"dom title      : {findings['dom'].get('title')!r}")
    print(f"dom ids        : {findings['dom'].get('ids')}")
    print(f"dom roles      : {findings['dom'].get('roles')}")
    print(f"dom data-attrs : {findings['dom'].get('dataAttrs')}")
    print(f"dom anchors    : {findings['dom'].get('anchors')}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
