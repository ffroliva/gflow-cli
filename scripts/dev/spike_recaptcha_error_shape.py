r"""What does a reCAPTCHA mint failure look like on the real page pool, and is it transient? ($0)

#915: `RecaptchaError` is a `RuntimeError`, so it exits 1 with no Problem Details and
aborts a batch past `--continue-on-error`. Making it a `GFlowError` needs one decision
the code cannot answer: is it RETRYABLE? A retry flag that invites a doomed loop is worse
than none (see `FlowHostMigratedError`, #639). This measures each shape gflow can hit:

  A. a page parked at about:blank (#891/#914: the pool page after a migrated image run),
     through the client's real mint path            -> which error, and does it repeat?
  B. the flow.google.com root grid, client path      -> expected exit 36 (guarded), not this
  C. a flow.google.com /project/<id>, N steady mints -> how often does a mint fail at all?
  D. a mint racing a navigation on that project page -> the transient shape, then a
     re-mint once the page settles                   -> does a retry recover?

Arms E/F were added after a council review of #939 (D only raced the execute step); they
falsified "missing site key is deterministic" for an https page -- see the spike doc.

Pre-registered reading (A-D):
  A fails identically every time, C mints N/N, D fails then the re-mint succeeds
    -> a missing site key is deterministic for that page (not retryable); an execute
       failure is a page-state race that a re-run clears (retryable).
  C fails intermittently -> mint failures are transient everywhere; retryable as a class.
  D's re-mint also fails -> nothing about this is transient; not retryable.

Minting is free: no credits, no quota, no generation. Tokens are discarded.

    python scripts/dev/spike_recaptcha_error_shape.py --profile ci-probe --project <uuid>
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
from gflow_cli.api.recaptcha import TokenMinter, discover_site_key  # noqa: E402

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip

_ROOT = "https://flow.google.com"

_KEY_AND_STATE_JS = r"""() => {
  const s = [...document.querySelectorAll('script[src*="recaptcha/enterprise.js"]')];
  const m = s.map(e => (e.getAttribute('src') || '').match(/[?&]render=([^&]+)/)).find(Boolean);
  return {ready: document.readyState, key: m ? m[1] : null, url: location.protocol};
}"""


def _shape(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:160]}"


async def _settle(page: Any, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:  # noqa: BLE001 - settle is best-effort
        pass
    await page.wait_for_timeout(3000)


async def _client_mint(client: Any) -> str:
    """The production path: `_mint_recaptcha_token` on the page the pool hands out."""
    try:
        token = await client._mint_recaptcha_token("IMAGE_GENERATION")  # noqa: SLF001
        return f"OK ({len(token)} chars)"
    except Exception as exc:  # noqa: BLE001 - the failure IS the measurement
        return _shape(exc)


async def _minter(page: Any) -> str:
    try:
        token = await TokenMinter(page, mint_evaluate_kwargs=mint_evaluate_kwargs()).mint(
            "IMAGE_GENERATION"
        )
        return f"OK ({len(token)} chars)"
    except Exception as exc:  # noqa: BLE001
        return _shape(exc)


async def _main(profile: str, project: str, steady: int) -> int:
    findings: dict[str, Any] = {"profile": profile, "project": project}
    async with build_client(resolve_profile_dir(profile)) as client:
        page = await client._checkout_page()  # noqa: SLF001 - the real pool page
        client._checkin_page(page)  # noqa: SLF001 - the client mint checks it out again
        step("pool", f"pool page at {page.url}")

        await page.goto("about:blank")
        findings["A_about_blank"] = [await _client_mint(client) for _ in range(3)]
        step("A", json.dumps(findings["A_about_blank"]))

        await _settle(page, _ROOT)
        findings["B_root_grid"] = await _client_mint(client)
        step("B", findings["B_root_grid"])

        proj = f"{_ROOT}/project/{project}"
        await _settle(page, proj)
        findings["C_project_steady"] = [await _minter(page) for _ in range(steady)]
        ok = sum(r.startswith("OK") for r in findings["C_project_steady"])
        failed = [r for r in findings["C_project_steady"] if not r.startswith("OK")]
        step("C", f"{ok}/{steady} OK; failures={failed}")

        races: list[dict[str, str]] = []
        for _ in range(3):
            mint = asyncio.create_task(_minter(page))
            await asyncio.sleep(0.05)
            nav = asyncio.create_task(
                page.goto(proj, wait_until="domcontentloaded", timeout=45_000)
            )
            raced = await mint
            try:
                await nav
            except Exception:  # noqa: BLE001
                pass
            await _settle(page, proj)
            races.append({"raced": raced, "re_mint": await _minter(page)})
        findings["D_race"] = races
        step("D", json.dumps(races))

        # E (council #939): D's failures came from the EXECUTE evaluate. The site-key READ
        # is a separate evaluate; race it alone, at a few offsets into the navigation.
        reads: list[dict[str, str]] = []
        for delay in (0.0, 0.0, 0.02, 0.05, 0.1, 0.2):
            nav = asyncio.create_task(
                page.goto(proj, wait_until="domcontentloaded", timeout=45_000)
            )
            await asyncio.sleep(delay)
            try:
                raced = f"OK ({await discover_site_key(page)!s:.6}…)"
            except Exception as exc:  # noqa: BLE001
                raced = _shape(exc)
            try:
                await nav
            except Exception:  # noqa: BLE001
                pass
            await _settle(page, proj)
            try:
                again = f"OK ({await discover_site_key(page)!s:.6}…)"
            except Exception as exc:  # noqa: BLE001
                again = _shape(exc)
            reads.append({"delay_s": str(delay), "raced": raced, "re_read": again})
        findings["E_site_key_read_race"] = reads
        step("E", json.dumps(reads))

        # F: E showed a "missing" key 100-200 ms into a navigation that a re-read clears.
        # Hypothesis: document.readyState separates that (still loading) from a page that
        # finished loading with no script (about:blank). Read both at the failure point.
        states: list[dict[str, str]] = []
        for delay in (0.1, 0.15, 0.2, 0.3, 0.5):
            nav = asyncio.create_task(
                page.goto(proj, wait_until="domcontentloaded", timeout=45_000)
            )
            await asyncio.sleep(delay)
            try:
                key = await page.evaluate(_KEY_AND_STATE_JS)
            except Exception as exc:  # noqa: BLE001
                key = {"error": type(exc).__name__}
            try:
                await nav
            except Exception:  # noqa: BLE001
                pass
            await _settle(page, proj)
            states.append({"delay_s": str(delay), **{k: str(v)[:8] for k, v in key.items()}})
        await page.goto("about:blank")
        await page.wait_for_timeout(1000)
        blank = await page.evaluate(_KEY_AND_STATE_JS)
        states.append({"delay_s": "about:blank", **{k: str(v)[:8] for k, v in blank.items()}})
        findings["F_ready_state"] = states
        step("F", json.dumps(states))

    out = default_out_path("recaptcha_error_shape")
    out.write_text(json.dumps(findings, indent=2), encoding="utf-8")
    step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--project", required=True)
    ap.add_argument("--steady", type=int, default=5)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(_main(a.profile, a.project, a.steady)))
