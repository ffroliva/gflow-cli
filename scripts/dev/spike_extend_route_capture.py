"""Extend route recon, part 2 — where the model keys actually come from.

Part 1 (`spike_extend_model_recon.py`) bundle-grepped Flow's JS and its positive
control FAILED TWICE: `veo_3_1_lite` / `_fast` / `_quality` — keys we provably
send in production — appear in NONE of the 41 bundles (14.5 MB) on either the
landing page or a mounted project editor. That is not instrument misconfiguration,
it is the wrong instrument: **Flow's video model keys are server-supplied**
(per-cohort backend config), not hardcoded in the client. Bundle-grep can never
answer "what is the extend model key".

Part 1 did establish, from the bundle, that extend exists and what it is called:

    batchAsyncGenerateVideoExtendVideo   <- RPC method name
    VIDEO_MODEL_CAPABILITY_EXTEND        <- model-conditional capability flag
    PINHOLE_EXTEND_VIDEO / VIDEO_EDITOR_EXTEND / SCENE_BUILDER_EXTEND_SUBMITTED

So this part uses two instruments that CAN see server data, both zero-credit:

  A. **Response logger** over editor load. The documented-working tool (HAR
     comes back empty when Playwright attaches; a response listener does not).
     Greps every JSON/text response for lowercase `veo_*` keys and for extend
     capability flags. This is where the real model inventory lives.

  B. **Source slice** around `batchAsyncGenerateVideoExtendVideo` in the bundle.
     The request-body construction is client code, so it IS in the bundle even
     though the key strings are not. Gets the field shape without submitting.

Zero credits: navigation and reads only. No prompt typed, no Generate clicked.
An empty result from either instrument is INCONCLUSIVE, not proof of absence.

Usage:
    python scripts/dev/spike_extend_route_capture.py --profile ffroliva --project <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts" / "dev"))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402

# Same positive control as part 1. If a *response* carries these, instrument A
# works and a null extend finding is real. If not, INCONCLUSIVE again.
KNOWN_GOOD = ("veo_3_1_lite", "veo_3_1_fast", "veo_3_1_quality")

_VEO_LOWER = re.compile(r"\bveo_[a-z0-9_]{2,60}\b")
_CAPABILITY = re.compile(r"\bVIDEO_MODEL_CAPABILITY_[A-Z_]{2,40}\b")

_SLICE_JS = r"""
async ([needle, window_]) => {
    const urls = new Set();
    for (const e of performance.getEntriesByType('resource')) {
        if (/\.js(\?|$)/.test(e.name)) urls.add(e.name);
    }
    const slices = [];
    for (const u of urls) {
        let text;
        try {
            const r = await fetch(u, { credentials: 'same-origin' });
            if (!r.ok) continue;
            text = await r.text();
        } catch (err) { continue; }
        let i = -1;
        while ((i = text.indexOf(needle, i + 1)) !== -1) {
            slices.push({
                bundle: u.split('/').pop().slice(0, 60),
                offset: i,
                src: text.slice(Math.max(0, i - window_), i + window_),
            });
            if (slices.length >= 12) return slices;
        }
    }
    return slices;
}
"""


async def _run(profile: str, project: str) -> int:
    captured: list[dict[str, Any]] = []

    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # ---- Instrument A: response logger, armed BEFORE navigation -------
        async def _on_response(resp: Any) -> None:
            try:
                ctype = (resp.headers or {}).get("content-type", "")
                if "json" not in ctype and "text" not in ctype:
                    return
                if resp.status >= 400:
                    return
                body = await resp.text()
            except Exception:  # noqa: BLE001
                return
            veo = sorted(set(_VEO_LOWER.findall(body)))
            caps = sorted(set(_CAPABILITY.findall(body)))
            if veo or caps:
                captured.append(
                    {
                        "url": resp.url[:180],
                        "status": resp.status,
                        "veo_keys": veo,
                        "capabilities": caps,
                        "len": len(body),
                    }
                )

        page.on("response", lambda r: asyncio.create_task(_on_response(r)))

        step("nav", f"goto editor for project {project} (listener armed)")
        await page.goto(
            routes.project_editor_url("en", project),
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        for _ in range(30):
            if await page.locator('div[role="textbox"], textarea').count() > 0:
                break
            await page.wait_for_timeout(1000)
        step("nav", f"editor ready at {page.url}")
        await page.wait_for_timeout(8000)  # let deferred config calls land

        all_veo = sorted({k for c in captured for k in c["veo_keys"]})
        all_caps = sorted({k for c in captured for k in c["capabilities"]})
        control_hits = [k for k in KNOWN_GOOD if k in all_veo]

        step("A", f"{len(captured)} responses carried veo/capability tokens")
        if control_hits:
            step("A", f"control OK {control_hits} — instrument works")
        else:
            step("A", "control MISS — instrument A inconclusive, do not read null as absence")

        # ---- Instrument B: source slice around the extend RPC ------------
        step("B", "slicing bundle source around batchAsyncGenerateVideoExtendVideo...")
        slices: list[dict[str, Any]] = await page.evaluate(
            _SLICE_JS, ["batchAsyncGenerateVideoExtendVideo", 700]
        )
        step("B", f"{len(slices)} call sites found")

        out = default_out_path("spike_extend_route_capture")
        out.write_text(
            json.dumps(
                {
                    "profile": profile,
                    "project": project,
                    "instrument_a": {
                        "control_hits": control_hits,
                        "veo_keys": all_veo,
                        "capabilities": all_caps,
                        "responses": captured,
                    },
                    "instrument_b_slices": slices,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        step("out", str(out))

        print("\n=== A: server-supplied veo model keys ===")
        for k in all_veo:
            print(f"  {k}")
        print("\n=== A: model capabilities seen ===")
        for k in all_caps:
            print(f"  {k}")
        print("\n=== B: extend RPC call-site source ===")
        for s in slices[:3]:
            print(f"\n--- {s['bundle']} @{s['offset']} ---\n{s['src']}")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="ffroliva")
    ap.add_argument("--project", required=True)
    a = ap.parse_args()
    return asyncio.run(_run(a.profile, a.project))


if __name__ == "__main__":
    raise SystemExit(main())
