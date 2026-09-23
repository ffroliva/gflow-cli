r"""How does flow.google.com create a project? ($0 — creates one empty project per --click run)

Context (#864): labs.google's ``project.createProject`` now answers
``404 "Flow RPCs have been deprecated and disabled. Flow has migrated to
https://flow.google.com."`` (or 401 before that, for sessions without a labs token).
Every no-``--project`` generation path starts there, so project creation has to be
driven on flow.google.com. This spike records what that surface actually is.

Mode 1 (default, read-only): land on ``flow.google.com/`` and inventory every
clickable control — tag, ARIA role/label presence, ``mat-icon`` ligature, href —
plus the custom elements present. Nothing is clicked.

Mode 2 (``--click "<css>"``): same inventory, then click that selector and record
  * every ``batchexecute`` request (rpcids + raw ``f.req``) and its response head
  * any other non-static request to a Flow/Google API host
  * the URL before and after, and the ``/project/<id>`` landed on, if any
  * the page title and a title-ish input inventory after landing (for rename)

Pre-registered reading:

| Outcome                                              | Reading                                    |
|------------------------------------------------------|--------------------------------------------|
| click lands on /project/<uuid> and one rpcid fires   | creation = that rpc; the UI path is viable |
| lands on /project/<uuid>, no rpc before navigation   | project is created lazily (on first submit)|
| no navigation                                        | wrong control, or a dialog intervenes      |
| profile lands on /about                              | unmeasured for that profile                |

Raw captures go to ``scripts/dev/_spike_out/`` (gitignored): they carry cookies-adjacent
request bodies and must not be pasted anywhere unredacted.

    python scripts/dev/spike_migrated_create_project.py --profile ci-probe
    python scripts/dev/spike_migrated_create_project.py --profile ci-probe --click "<css>"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

MIGRATED_ROOT = "https://flow.google.com/"

_INVENTORY_JS = """
() => {
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const items = [...document.querySelectorAll(
      'button, a[href], [role="button"], [role="menuitem"], [tabindex="0"]')]
    .filter(vis)
    .slice(0, 120)
    .map((e) => ({
      tag: e.tagName.toLowerCase(),
      role: e.getAttribute('role'),
      has_aria_label: e.hasAttribute('aria-label'),
      href: e.getAttribute('href'),
      icons: [...e.querySelectorAll('mat-icon, i.google-symbols')].map(i => i.textContent.trim()),
      classes: (e.className && e.className.baseVal === undefined ? e.className : '').slice(0, 120),
      parent_ce: (() => { let p = e.parentElement; while (p) { if (p.tagName.includes('-')) return p.tagName.toLowerCase(); p = p.parentElement; } return null; })(),
      text_len: (e.innerText || '').trim().length,
    }));
  const ces = [...new Set([...document.querySelectorAll('*')].map(e => e.tagName.toLowerCase()).filter(t => t.includes('-')))];
  return {
    url: location.href,
    project_links: document.querySelectorAll('a[href*="/project/"]').length,
    inputs: [...document.querySelectorAll('input, textarea, [contenteditable="true"]')].filter(vis).map(e => ({tag: e.tagName.toLowerCase(), type: e.getAttribute('type'), has_aria_label: e.hasAttribute('aria-label'), value_len: (e.value || e.innerText || '').length})),
    title: document.title,
    custom_elements: ces,
    clickables: items,
  };
}
"""


async def _rename(args: argparse.Namespace) -> None:
    """Mode 3: rename through the project header's ``flow-editable-text`` and record
    which rpc carries it. Structural anchor only; the typed title is ours."""
    rpcs: list[dict[str, Any]] = []
    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = await client._context.new_page()  # noqa: SLF001
        await page.goto(
            f"https://flow.google.com/project/{args.rename_project}",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        await page.wait_for_timeout(int(args.settle * 1000))
        box = page.locator("flow-editable-text").first
        step(
            "rename",
            f"editable-text count={await page.locator('flow-editable-text').count()} "
            f"inner_inputs={await box.locator('input').count()}",
        )

        async def on_response(resp: Any) -> None:
            parts = urlsplit(resp.request.url)
            if "batchexecute" in parts.path:
                rpcs.append(
                    {
                        "rpcids": parse_qs(parts.query).get("rpcids"),
                        "status": resp.status,
                        "f_req": parse_qs(resp.request.post_data or "").get("f.req"),
                        "head": (await resp.text())[:400],
                    }
                )

        page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        target = box.locator("input").first if await box.locator("input").count() else box
        await target.click()
        await page.wait_for_timeout(500)
        inp = box.locator("input").first
        step("rename", f"inputs after click={await box.locator('input').count()}")
        await inp.fill(args.rename_to)
        await inp.press("Enter")
        await page.wait_for_timeout(5_000)
        for r in rpcs:
            step(
                "rpc",
                f"{r['rpcids']} {r['status']} f.req={(r['f_req'] or [''])[0][:300]} "
                f"head={r['head'][:250]!r}",
            )
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(int(args.settle * 1000))
        value = await page.locator("flow-editable-text input").first.input_value()
        step("rename", f"after reload value_matches={value == args.rename_to}")


async def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--profile", required=True)
    ap.add_argument("--click", default=None, help="CSS selector to click (mode 2)")
    ap.add_argument("--settle", type=float, default=8.0)
    ap.add_argument("--rename-project", default=None, help="mode 3: project id to rename")
    ap.add_argument("--rename-to", default="gflow-spike-864-renamed")
    args = ap.parse_args()
    if args.rename_project:
        await _rename(args)
        return

    profile_dir = resolve_profile_dir(args.profile)
    out: dict[str, Any] = {"profile": args.profile, "click": args.click, "requests": []}

    async with build_client(profile_dir) as client:
        page = await client._context.new_page()  # noqa: SLF001 - spike reads the live context
        capturing = False

        async def on_response(resp: Any) -> None:
            if not capturing:
                return
            req = resp.request
            if req.resource_type in {"image", "font", "stylesheet", "media", "script"}:
                return
            parts = urlsplit(req.url)
            entry: dict[str, Any] = {
                "method": req.method,
                "host": parts.hostname,
                "path": parts.path,
                "status": resp.status,
            }
            if "batchexecute" in parts.path:
                entry["rpcids"] = parse_qs(parts.query).get("rpcids")
                entry["f_req"] = parse_qs(req.post_data or "").get("f.req")
                try:
                    entry["response_head"] = (await resp.text())[:1500]
                except Exception as exc:  # noqa: BLE001
                    entry["response_head"] = f"<{type(exc).__name__}>"
            out["requests"].append(entry)

        page.on("response", lambda r: asyncio.ensure_future(on_response(r)))

        await page.goto(MIGRATED_ROOT, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(int(args.settle * 1000))
        out["before"] = await page.evaluate(_INVENTORY_JS)
        step("before", f"url={out['before']['url']} projects={out['before']['project_links']}")
        for i, c in enumerate(out["before"]["clickables"]):
            step(
                "ctl",
                f"{i:02d} {c['tag']} role={c['role']} icons={c['icons']} href={c['href']} "
                f"ce={c['parent_ce']} cls={c['classes'][:60]}",
            )

        if args.click:
            capturing = True
            await page.locator(args.click).first.click(timeout=15_000)
            for _ in range(30):
                await page.wait_for_timeout(1_000)
                if "/project/" in page.url:
                    break
            await page.wait_for_timeout(int(args.settle * 1000))
            capturing = False
            out["after"] = await page.evaluate(_INVENTORY_JS)
            step("after", f"url={out['after']['url']}")
            step("after", f"inputs={out['after']['inputs']}")
            for r in out["requests"]:
                if r.get("rpcids") or r["host"] not in {"flow.google.com"}:
                    step(
                        "net",
                        f"{r['method']} {r['host']}{r['path']} {r['status']} rpcids={r.get('rpcids')}",
                    )

    path = default_out_path(f"spike_migrated_create_project_{args.profile}")
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    step("done", f"wrote {path}")


if __name__ == "__main__":
    asyncio.run(main())
