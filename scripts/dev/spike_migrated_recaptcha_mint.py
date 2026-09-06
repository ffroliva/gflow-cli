r"""Can flow.google.com mint a reCAPTCHA Enterprise token? ($0)

`gflow image` exits 36 on the migrated host at `raise_if_migrated(at=
"mint_recaptcha_token")` — a guard, not an observed failure. Its own comment
states the mechanism precisely:

    the pool page is the flow.google.com GRID (client-side handoff) with no
    recaptcha/enterprise.js ... (/project/<id> on that host DOES carry the
    script, but no path that mints is ported there yet)

So the mint may be failing because it runs on the **root grid**, not because
the origin cannot mint. Issue #692 corroborates: the reporter's bundle showed
`route: "/"`, and `diagnostics.py` maps both hosts to `host_category:
flow_app`, so the route is the only tell. Recon on the migrated character
editor separately found a live reCAPTCHA Enterprise anchor iframe keyed to
`https://flow.` — the origin is configured.

This measures the difference directly, on the SAME page pool gflow uses:

  A. the migrated ROOT grid      -> script present? site key? mint?
  B. a migrated /project/<id>    -> script present? site key? mint?

If B mints and A does not, the guard is over-broad: the fix is to mint on a
project route rather than to refuse the host. If NEITHER mints, the guard is
correct and says so for the right reason — which is worth knowing too, since
it is currently justified by an assumption.

Minting is free: no credits, no quota, no generation. Tokens are discarded.

    python scripts/dev/spike_migrated_recaptcha_mint.py \
        --profile ci-probe --project <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api._engine import mint_evaluate_kwargs  # noqa: E402
from gflow_cli.api.recaptcha import TokenMinter  # noqa: E402

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

_MIGRATED_ROOT = "https://flow.google.com"

# Does the page carry the enterprise script, and does the global exist?
_PROBE_JS = r"""() => {
  const scripts = [...document.querySelectorAll('script[src]')]
    .map(s => s.getAttribute('src') || '')
    .filter(s => s.includes('recaptcha'));
  return {
    url: location.href,
    path: location.pathname,
    recaptcha_scripts: scripts.slice(0, 4),
    render_keys: scripts
      .map(s => (s.match(/[?&]render=([^&]+)/) || [])[1])
      .filter(Boolean),
    grecaptcha_present: typeof window.grecaptcha !== 'undefined',
    enterprise_present: !!(window.grecaptcha && window.grecaptcha.enterprise),
    anchor_iframes: [...document.querySelectorAll('iframe[src*="recaptcha"]')]
      .map(f => (f.getAttribute('src') || '').slice(0, 80)).slice(0, 3),
  };
}"""


async def _probe(page: Any, label: str, url: str) -> dict[str, Any]:
    step("goto", f"{label}: {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:  # noqa: BLE001 - settle is best-effort
        pass
    await page.wait_for_timeout(3000)

    result: dict[str, Any] = {"label": label, "requested": url}
    result.update(await page.evaluate(_PROBE_JS))
    step(
        label,
        f"landed={result['path']} scripts={len(result['recaptcha_scripts'])} "
        f"keys={result['render_keys']} grecaptcha={result['grecaptcha_present']} "
        f"enterprise={result['enterprise_present']}",
    )

    # The real question: does a mint SUCCEED here? Free — no generation follows.
    minter = TokenMinter(page, mint_evaluate_kwargs=mint_evaluate_kwargs())
    try:
        token = await minter.mint("image_generation")
        result["mint"] = "OK"
        result["token_len"] = len(token)
        step(label, f"MINT OK — token {len(token)} chars (discarded)")
    except Exception as exc:  # noqa: BLE001 - the failure IS the measurement
        result["mint"] = f"{type(exc).__name__}: {str(exc)[:180]}"
        step(label, f"MINT FAILED — {type(exc).__name__}: {str(exc)[:110]}")
    return result


async def _main(profile: str, project: str) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")
    findings: dict[str, Any] = {"profile": profile, "project": project, "probes": []}

    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike reads the live context
        page = await context.new_page()
        try:
            findings["probes"].append(await _probe(page, "root_grid", _MIGRATED_ROOT))
            findings["probes"].append(
                await _probe(page, "project_route", f"{_MIGRATED_ROOT}/project/{project}")
            )
            root, proj = findings["probes"]
            step(
                "verdict",
                f"root={root['mint']}  project={proj['mint']}  -> "
                + (
                    "GUARD IS OVER-BROAD: mint on a project route"
                    if proj["mint"] == "OK" and root["mint"] != "OK"
                    else "guard justified on both routes"
                    if proj["mint"] != "OK"
                    else "both routes mint"
                ),
            )
        finally:
            out = default_out_path("migrated_recaptcha_mint")
            out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
            step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.profile, args.project)))
