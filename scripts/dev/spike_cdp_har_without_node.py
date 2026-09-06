r"""Can Python + Playwright replace the Node/agent-browser HAR harness? ($0)

`scripts/dev/har-spike/` is 1691 lines across 9 PowerShell scripts driving a pinned
`agent-browser@0.27.0` over `npx`. It is Windows-only, which undercuts the point of
shipping it: contributors on macOS and Linux cannot produce evidence with it.

Only two of those scripts contain genuinely OS-bound code (Chrome discovery, profile
paths), and this repo already solves both in Python. Playwright — already a dependency —
also advertises `BrowserType.connect_over_cdp` and `record_har_path`. So the port looks
easy. **The one piece that is not obviously portable is the piece the harness exists
for**, and that is what this spike measures rather than assumes:

    `record_har_path` is a NEW-CONTEXT option. Attaching to a human's already-running
    Chrome gives you their EXISTING context, and you cannot retrofit HAR recording onto
    it. So can we still capture?

Three questions, answered in order. Any NO is a real finding — it is cheaper to learn it
here than after porting nine scripts.

  Q1  Can Playwright attach to a CDP Chrome running a real gflow profile, and SEE the
      page a human is driving?
  Q2  Does `browser.new_context(record_har_path=...)` work on a CDP-attached browser?
      (Expected: not for the persistent profile context — measure it.)
  Q3  Can a raw CDP session (`Network.*` events) be shaped into a HAR that the harness's
      OWN `extract_har_summary.summarize_har` consumes unchanged?

If Q1 and Q3 hold, the Node layer can go entirely and the port is mechanical.

Credit-free: launches Chrome, navigates to a Flow page, reads network metadata. Nothing
is typed, generated or submitted.

    python scripts/dev/spike_cdp_har_without_node.py --profile ci-probe
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    default_out_path,
    resolve_profile_dir,
    step,
)

_FLOW_URL = "https://labs.google/fx/tools/flow?hl=en"


def _load_summarizer() -> Any:
    """Import the harness's own summariser, by path (scripts/ is not a package)."""
    mod_path = Path(__file__).resolve().parent / "har-spike" / "extract_har_summary.py"
    spec = importlib.util.spec_from_file_location("extract_har_summary", mod_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _har_from_cdp(events: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Shape captured CDP Network events into the HAR subset the summariser reads.

    Deliberately minimal: `summarize_har` reads `log.entries[].request.{method,url,
    headers,postData}` and `.response.{status,headers,content}`. Producing more than it
    consumes would be inventing a format nobody validates.
    """
    entries: list[dict[str, Any]] = []
    for rec in events.values():
        req = rec.get("request") or {}
        resp = rec.get("response") or {}
        if not req.get("url"):
            continue
        entries.append(
            {
                "request": {
                    "method": req.get("method"),
                    "url": req.get("url"),
                    "headers": [
                        {"name": k, "value": v} for k, v in (req.get("headers") or {}).items()
                    ],
                    "postData": {"text": req.get("postData", "")} if req.get("postData") else {},
                },
                "response": {
                    "status": resp.get("status"),
                    "headers": [
                        {"name": k, "value": v} for k, v in (resp.get("headers") or {}).items()
                    ],
                    "content": {"mimeType": (resp.get("mimeType") or "")},
                },
            }
        )
    return {"log": {"version": "1.2", "creator": {"name": "gflow-cdp-spike"}, "entries": entries}}


async def _main(profile: str, port: int) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")
    findings: dict[str, Any] = {"profile": profile, "port": port}

    async with async_playwright() as pw:
        # Stand in for "a human has Chrome open": a real Chrome (channel), real profile,
        # CDP port. This is also the answer to Chrome discovery — no path hunting.
        step("launch", f"chrome channel, --remote-debugging-port={port}")
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=False,
            args=[f"--remote-debugging-port={port}"],
            no_viewport=True,
        )
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(_FLOW_URL, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(2500)
            step("human_page", page.url[:80])

            # --- Q1: attach a SECOND Playwright client over CDP -----------------
            async with async_playwright() as pw2:
                try:
                    browser = await pw2.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                    contexts = browser.contexts
                    pages = [p for c in contexts for p in c.pages]
                    findings["Q1_attach"] = {
                        "ok": True,
                        "contexts": len(contexts),
                        "pages": len(pages),
                        "sees_human_page": any(
                            "labs.google" in p.url or "flow.google" in p.url for p in pages
                        ),
                    }
                    step(
                        "Q1",
                        f"attached: contexts={len(contexts)} pages={len(pages)} "
                        f"sees_human_page={findings['Q1_attach']['sees_human_page']}",
                    )
                except Exception as exc:  # noqa: BLE001 - the failure IS the measurement
                    findings["Q1_attach"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    step("Q1", f"ATTACH FAILED — {type(exc).__name__}: {str(exc)[:120]}")
                    return 1

                # --- Q2: can we record a HAR on the attached browser? -----------
                try:
                    har_path = default_out_path("cdp_native_har", ".har")
                    probe_ctx = await browser.new_context(record_har_path=str(har_path))
                    p2 = await probe_ctx.new_page()
                    await p2.goto(_FLOW_URL, wait_until="domcontentloaded", timeout=45_000)
                    await p2.wait_for_timeout(2000)
                    await probe_ctx.close()  # flushes the HAR
                    size = har_path.stat().st_size if har_path.exists() else 0
                    findings["Q2_native_har"] = {"ok": size > 0, "bytes": size}
                    step("Q2", f"native record_har_path on a NEW context: {size} bytes")
                except Exception as exc:  # noqa: BLE001 - expected to be limited
                    findings["Q2_native_har"] = {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    step("Q2", f"native HAR unavailable — {type(exc).__name__}: {str(exc)[:110]}")

                # --- Q3: CDP session on the HUMAN's own page --------------------
                target = next(
                    (p for c in browser.contexts for p in c.pages if "google" in p.url), None
                )
                if target is None:
                    step("Q3", "no page to attach a CDP session to")
                    findings["Q3_cdp_har"] = {"ok": False, "error": "no target page"}
                else:
                    events: dict[str, dict[str, Any]] = {}
                    cdp = await target.context.new_cdp_session(target)
                    await cdp.send("Network.enable")

                    def _on_req(ev: dict[str, Any]) -> None:
                        events.setdefault(ev["requestId"], {})["request"] = ev.get("request", {})

                    def _on_resp(ev: dict[str, Any]) -> None:
                        events.setdefault(ev["requestId"], {})["response"] = ev.get("response", {})

                    cdp.on("Network.requestWillBeSent", _on_req)
                    cdp.on("Network.responseReceived", _on_resp)

                    await target.reload(wait_until="domcontentloaded", timeout=45_000)
                    await target.wait_for_timeout(4000)
                    await cdp.detach()

                    har = _har_from_cdp(events)
                    out_har = default_out_path("cdp_shaped", ".har")
                    out_har.write_text(json.dumps(har), encoding="utf-8")
                    step("Q3", f"captured {len(har['log']['entries'])} entries -> {out_har.name}")

                    # The real test: does the HARNESS's OWN summariser consume it?
                    summarizer = _load_summarizer()
                    summary = summarizer.summarize_har(out_har, host_filter="google")
                    n = len(summary.get("entries", summary.get("safe_entries", [])) or [])
                    findings["Q3_cdp_har"] = {
                        "ok": len(har["log"]["entries"]) > 0,
                        "entries": len(har["log"]["entries"]),
                        "summariser_accepted": True,
                        "summarised_entries": n,
                    }
                    step("Q3", f"extract_har_summary.summarize_har accepted it — {n} entries")

                await browser.close()
        finally:
            await ctx.close()
            out = default_out_path("cdp_har_without_node")
            out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
            step("wrote", str(out))

    q1 = findings.get("Q1_attach", {}).get("ok")
    q3 = findings.get("Q3_cdp_har", {}).get("ok")
    step(
        "verdict",
        "PORTABLE: Node/agent-browser can go" if (q1 and q3) else "NOT proven — see findings",
    )
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--port", type=int, default=9335)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.profile, args.port)))
