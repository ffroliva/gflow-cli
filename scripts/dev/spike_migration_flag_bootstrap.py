r"""Where does labs.google decide to hand a migrated account to flow.google.com? ($0)

Established by `spike_migrated_host_trigger.py` (2026-09-04, profile ffroliva):

    200  https://labs.google/fx/tools/flow?hl=en      <- NOT a redirect
         settles to https://flow.google.com/?hl=en    <- client-side hop
         has_labs_next_auth=True, /fx/api/auth/session -> 200 authenticated
         5/5 samples, flapped=False

So labs.google serves us a normal page with a fully valid session, and the app
itself navigates away. The decision is therefore made by app code AFTER load,
which means the signal has to arrive in the bootstrap: the initial HTML, or an
early XHR carrying account config. It is not a header, not a cookie, and not
DNS.

Finding it converts migration detection from "navigate, wait ~4 s, re-read
page.url" into a deterministic read -- the same wait-and-hope shape as the #639
locale bug.

TWO CAPTURES
------------
1. `context.request.get(...)` -- the raw HTML with our cookies and NO JS. This
   is exactly the 200 the browser received, without the race against the hop.
2. A live navigation, recording early JSON/XHR bodies before the handoff.

Both are searched for the handoff target and for flag-shaped keys near it.

REDACTION: only short context windows are kept, and every window is scrubbed of
token-shaped runs, emails and long base64. No cookie values are read at all.

    python scripts/dev/spike_migration_flag_bootstrap.py --profile ffroliva
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.routes import EDITOR_BOOTSTRAP_URL  # noqa: E402

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

TARGET = "flow.google.com"

# The 2026-09-04 run found no destination URL anywhere in labs.google payloads,
# but the bundle ships i18n keys for a migration programme codenamed "pinhole",
# tracked per product with a pending/in_progress/complete lifecycle. The URL is
# compiled in; what must be TRANSMITTED is this account's status value.
EXTRA_NEEDLES = ("pinhole", "migrationStatus", "migration_status", "MIGRATION_")

# Flag-shaped keys worth reporting even when they sit far from the target string.
FLAG_HINTS = (
    "migrat",
    "redirect",
    "rollout",
    "experiment",
    "isEnabled",
    "featureFlag",
    "newFrontend",
    "angular",
    "aisandbox",
    "shouldUse",
    "destination",
)

_SCRUB = (
    (re.compile(r"[\w.\-]+@[\w\-]+\.\w+"), "<email>"),
    (re.compile(r"\b(?:ya29|AIza|0cAFcW|03AFcWeA)[\w\-]{8,}"), "<token>"),
    (re.compile(r"\b[A-Za-z0-9_\-]{60,}\b"), "<long-opaque>"),
)


def scrub(text: str) -> str:
    for pattern, repl in _SCRUB:
        text = pattern.sub(repl, text)
    return text


def windows(body: str, needle: str, *, span: int = 220, cap: int = 6) -> list[str]:
    """Short scrubbed context windows around each occurrence of *needle*."""
    out: list[str] = []
    for m in re.finditer(re.escape(needle), body):
        lo = max(0, m.start() - span)
        hi = min(len(body), m.end() + span)
        out.append(scrub(body[lo:hi].replace("\n", " ")))
        if len(out) >= cap:
            break
    return out


def flag_keys(body: str) -> list[str]:
    """Distinct flag-shaped identifiers present in the payload."""
    found: set[str] = set()
    for hint in FLAG_HINTS:
        for m in re.finditer(rf'["\':]([A-Za-z0-9_]*{hint}[A-Za-z0-9_]*)["\'\s:]', body, re.I):
            token = m.group(1)
            if 3 < len(token) < 60:
                found.add(token)
    return sorted(found)[:40]


async def _fetch_bootstrap_html(context: Any) -> dict[str, Any]:
    """Capture 1: the raw 200 with our cookies, no JS, no race."""
    resp = await context.request.get(EDITOR_BOOTSTRAP_URL)
    body = await resp.text()
    return {
        "status": resp.status,
        "bytes": len(body),
        "content_type": (resp.headers or {}).get("content-type"),
        "mentions_target": TARGET in body,
        "target_count": body.count(TARGET),
        "target_windows": windows(body, TARGET),
        "flag_keys": flag_keys(body),
        "has_next_data": "__NEXT_DATA__" in body,
        "has_rsc_push": "self.__next_f" in body,
        "needle_hits": {
            n: {"count": body.count(n), "windows": windows(body, n, cap=4)}
            for n in EXTRA_NEEDLES
            if n in body
        },
    }


async def _scan_js_chunks(context: Any, html: str) -> dict[str, Any]:
    """The destination is not in the HTML or any XHR, so it is compiled into the
    bundle. Fetch the app's JS chunks and find the redirect code plus the
    condition guarding it -- that condition IS the flag."""
    srcs = sorted(set(re.findall(r'src="(/_next/static/[^"]+\.js)"', html)))
    scanned, hits = 0, []
    for src in srcs[:40]:
        try:
            resp = await context.request.get("https://labs.google" + src)
            body = await resp.text()
        except Exception:  # noqa: BLE001 - a spike records, never aborts
            continue
        scanned += 1
        if TARGET in body:
            hits.append(
                {
                    "chunk": src[-70:],
                    "bytes": len(body),
                    "count": body.count(TARGET),
                    "windows": windows(body, TARGET, span=320, cap=4),
                }
            )
    return {"chunks_found": len(srcs), "chunks_scanned": scanned, "chunk_hits": hits[:6]}


async def _capture_navigation(context: Any) -> dict[str, Any]:
    """Capture 2: early JSON/XHR bodies, before the client-side hop."""
    page = await context.new_page()
    hits: list[dict[str, Any]] = []
    pending: list[Any] = []

    def _on_response(resp: Any) -> None:
        ctype = (resp.headers or {}).get("content-type", "")
        if "json" in ctype or resp.request.resource_type in ("xhr", "fetch"):
            pending.append(resp)

    page.on("response", _on_response)
    try:
        await page.goto(EDITOR_BOOTSTRAP_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)
        settled = page.url
    finally:
        for resp in pending[:60]:
            try:
                body = await resp.text()
            except Exception:  # noqa: BLE001 - body gone after navigation; expected
                continue
            if TARGET in body or any(n in body for n in EXTRA_NEEDLES):
                hits.append(
                    {
                        "url": resp.url[:160],
                        "status": resp.status,
                        "bytes": len(body),
                        "mentions_target": TARGET in body,
                        "target_windows": windows(body, TARGET, cap=3),
                        "flag_keys": flag_keys(body),
                        "needle_hits": {
                            n: windows(body, n, cap=3) for n in EXTRA_NEEDLES if n in body
                        },
                    }
                )
        await page.close()
    return {"url_settled": settled[:160], "xhr_hits": hits[:12], "xhr_seen": len(pending)}


async def _main(profile: str) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")

    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike reads the live context
        html = await _fetch_bootstrap_html(context)
        step(
            "html",
            f"{html['status']}  {html['bytes']}B  mentions {TARGET}={html['mentions_target']} "
            f"(x{html['target_count']})  next_data={html['has_next_data']}",
        )
        raw = await (await context.request.get(EDITOR_BOOTSTRAP_URL)).text()
        # Dump once, analyse offline many times: every re-guess at a regex
        # otherwise costs a full browser round trip. Gitignored (_spike_out/).
        raw_path = default_out_path("bootstrap_raw", ".html")
        raw_path.write_text(raw, encoding="utf-8")
        step("raw", f"{len(raw)}B -> {raw_path}")
        js = await _scan_js_chunks(context, raw)
        step(
            "js",
            f"chunks={js['chunks_found']} scanned={js['chunks_scanned']} "
            f"hits={len(js['chunk_hits'])}",
        )
        nav = await _capture_navigation(context)
        step(
            "nav",
            f"settled={nav['url_settled']}  xhr={nav['xhr_seen']}  hits={len(nav['xhr_hits'])}",
        )

    payload = {
        "profile": profile,
        "note": "credit-free: reads only, nothing submitted; windows scrubbed of tokens/emails",
        "question": "which bootstrap field flags the account as migrated?",
        "bootstrap_html": html,
        "js_chunks": js,
        "navigation": nav,
    }
    out = default_out_path("migration_flag_bootstrap")
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    step("out", str(out))

    if html["mentions_target"]:
        step("FOUND", f"the handoff target appears in the bootstrap HTML x{html['target_count']}")
    elif nav["xhr_hits"]:
        step("FOUND", f"the target appears in {len(nav['xhr_hits'])} XHR payload(s), not the HTML")
    else:
        step("MISS", "target not in HTML or captured XHRs - it is computed, not transmitted")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ffroliva")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.profile)))
