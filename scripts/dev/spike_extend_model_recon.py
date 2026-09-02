"""Does Flow have a Veo *extend* route, and what model keys does it use?

Evidence question raised by hurara210/google-flow-cli, which hardcodes:

    veo_3_1_extend_fast_{landscape,portrait,square}_ultra

That is a THIRD-PARTY claim — this repo has never captured an extend request
(`samples/captured/` has no extend file). Before any long-video plan is written
we need to know whether those keys are real, whether the `_ultra` suffix is
tier-specific (their author is on Ultra), and whether an extend affordance even
renders for this account.

**Zero credits, zero submissions.** Method is a read of Flow's own shipped
JavaScript: the client must contain every model key it is able to send, so the
bundle is ground truth for the enum without spending a single generation. No
prompt is typed, no Generate is clicked, nothing is submitted.

Follows the trap documented in #539 / the 2026-08-14 capability-matrix spike:
an empty read is INSTRUMENT FAILURE, never proof of absence. If we harvest zero
bundles, or zero `veo` hits of any kind, that is reported as INCONCLUSIVE — the
`veo_3_1_lite` etc. keys we already ship are the positive control that proves
the instrument works.

Usage:
    python scripts/dev/spike_extend_model_recon.py --profile ffroliva
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts" / "dev"))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402

# Keys we already ship and have seen live — the positive control. If NONE of
# these appear in the harvested bundles, the instrument is broken and every
# other conclusion in this run is void.
KNOWN_GOOD = ("veo_3_1_lite", "veo_3_1_fast", "veo_3_1_quality")

# Harvest every model-key-shaped token, then classify. Deliberately wider than
# "veo_.*extend" so we also learn what Flow calls the feature if it is not
# spelled the way the third-party repo assumes.
_HARVEST_JS = r"""
async (maxBytes) => {
    const urls = new Set();
    for (const e of performance.getEntriesByType('resource')) {
        if (/\.js(\?|$)/.test(e.name)) urls.add(e.name);
    }
    for (const s of document.querySelectorAll('script[src]')) {
        urls.add(new URL(s.getAttribute('src'), location.href).href);
    }

    const patterns = {
        veo:     /\bveo[a-z0-9_]{2,60}\b/gi,
        extend:  /\b[a-z0-9_]{0,40}extend[a-z0-9_]{0,40}\b/gi,
        ultra:   /\b[a-z0-9_]{2,50}_ultra\b/gi,
        challenge:   /\b(?:imagen|narwhal|pinhole)[a-z0-9_]{0,40}\b/gi,
    };

    const hits = {};
    for (const k of Object.keys(patterns)) hits[k] = {};
    const fetched = [], failed = [];
    let bytes = 0;

    for (const u of urls) {
        if (bytes > maxBytes) break;
        let text;
        try {
            const r = await fetch(u, { credentials: 'same-origin' });
            if (!r.ok) { failed.push([u, r.status]); continue; }
            text = await r.text();
        } catch (err) { failed.push([u, String(err)]); continue; }
        bytes += text.length;
        fetched.push([u, text.length]);
        for (const [k, re] of Object.entries(patterns)) {
            for (const m of text.matchAll(re)) {
                const tok = m[0];
                (hits[k][tok] ||= { count: 0, sources: [] });
                hits[k][tok].count++;
                const short = u.split('/').pop().slice(0, 60);
                if (!hits[k][tok].sources.includes(short)) hits[k][tok].sources.push(short);
            }
        }
    }
    return { hits, fetched, failed, bytes, url_count: urls.size };
}
"""


async def _run(profile: str, project: str, max_bytes: int) -> int:
    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # Run 1 harvested the LANDING page and the positive control failed —
        # the composer's model list lives in a route-split chunk that only
        # loads once the editor mounts. Navigate to a real project editor
        # first, or the control will fail again and the run is void.
        step("nav", f"goto editor for project {project}")
        await page.goto(
            routes.project_editor_url("en", project),
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        for _ in range(30):
            if await page.locator('div[role="textbox"], textarea').count() > 0:
                break
            await page.wait_for_timeout(1000)
        else:
            step("nav", "editor never became ready — harvest would repeat run 1's failure")
            return 1
        step("nav", f"editor ready at {page.url}")

        # Let lazily-imported composer/settings chunks land.
        await page.wait_for_timeout(5000)

        step("harvest", "fetching same-origin JS bundles from page context...")
        res: dict[str, Any] = await page.evaluate(_HARVEST_JS, max_bytes)

        step("harvest", f"{len(res['fetched'])}/{res['url_count']} bundles, {res['bytes']:,} bytes")
        if res["failed"]:
            step("harvest", f"  {len(res['failed'])} fetch failures (first: {res['failed'][:2]})")

        veo = res["hits"]["veo"]
        control_hits = [k for k in KNOWN_GOOD if k in veo]

        # ---- Positive control gate -------------------------------------
        if not control_hits:
            step("VERDICT", "INCONCLUSIVE — instrument failure")
            step("VERDICT", f"  none of {KNOWN_GOOD} found; a null extend result proves nothing")
            verdict = "INCONCLUSIVE_INSTRUMENT_FAILURE"
        else:
            step("control", f"OK — found {control_hits} (instrument works)")
            extend_keys = sorted(k for k in veo if "extend" in k.lower())
            if extend_keys:
                step("VERDICT", f"EXTEND MODEL KEYS EXIST: {extend_keys}")
                verdict = "EXTEND_KEYS_FOUND"
            else:
                step("VERDICT", "NO veo_*extend* key in Flow's shipped client")
                step("VERDICT", "  → third-party keys unconfirmed for this account/cohort")
                verdict = "NO_EXTEND_KEYS"

        out = default_out_path("spike_extend_model_recon")
        out.write_text(
            json.dumps(
                {
                    "verdict": verdict,
                    "profile": profile,
                    "project": project,
                    "page_url": page.url,
                    "control_hits": control_hits,
                    "veo_keys": sorted(veo),
                    "extend_tokens": sorted(res["hits"]["extend"]),
                    "ultra_tokens": sorted(res["hits"]["ultra"]),
                    "challenge_tokens": sorted(res["hits"]["challenge"]),
                    "raw": res,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        step("out", str(out))

        print("\n--- veo_* keys Flow's client knows ---")
        for k in sorted(veo):
            print(f"  {k}  (x{veo[k]['count']})")
        print("\n--- any *extend* token ---")
        for k in sorted(res["hits"]["extend"])[:40]:
            print(f"  {k}")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="ffroliva")
    ap.add_argument("--project", required=True)
    ap.add_argument("--max-bytes", type=int, default=40_000_000)
    a = ap.parse_args()
    return asyncio.run(_run(a.profile, a.project, a.max_bytes))


if __name__ == "__main__":
    raise SystemExit(main())
