#!/usr/bin/env python3
r"""Patchright engine spike — the Phase-0 kill-gate for the opt-in browser engine.

Plan: docs/superpowers/plans/2026-06-12-patchright-engine/{PLAN.md,SCENARIO.md}
Predict verdict: CAUTION 6/10 — this spike must prove the premise before any
production file is touched.

Touches ZERO production files. It swaps the browser engine at RUNTIME (monkeypatch
of the module-level ``async_playwright`` that ``client.py`` / ``ui_automation.py``
import) and reuses the real ``FlowApiClient.generate_image`` path so the 403→200
comparison is faithful — not a hand-rolled request that could 403 for the wrong
reason. Image generation is credit-free (memory: flow-credits-videos-only), so the
decisive hot-profile leg costs $0.

Two probe phases per engine (``playwright`` baseline vs ``patchright``):

  A. PRIMITIVE PROBE (owns its own page; full control)
     - launch the real profile with the EXACT production launch kwargs
       (replicates client._persistent_context_kwargs + the navigator.webdriver mask)
     - record channel (scenario 7), navigator.webdriver (scenario 3 baseline)
     - discover_site_key (scenario 8 / Leg 2)
     - MINT A/B (scenario 1 / Leg 1, the make-or-break): under patchright, call the
       reCAPTCHA execute JS with the default (isolated) context AND with
       isolated_context=False — record which returns a token. This directly
       validates/invalidates the "isolated_context default breaks grecaptcha"
       hypothesis from predict.
     - listener smoke (Legs 3/4 lite): page.on("response")/page.on("request") fire.

  B. FAITHFUL GENERATION (scenario 2 / Leg 6 — DECISIVE, + real Leg 3)
     - swap engine on client.py + ui_automation.py
     - patch recaptcha.TokenMinter.mint to inject isolated_context=False (patchright)
     - attach a context-page response listener capturing the batchGenerateImages
       HTTP status + whether response.json() parses
     - run the real generate_image and classify the outcome (200 / 403-WAF /
       mint-broken / other)

Run it on a HOT profile (one currently 403ing under playwright) for the decisive
comparison. Pass-bar = patchright measurably flips 403→200 AND mint works AND
listeners parse. Any red leg → STOP (write SPIKE-RESULTS.md, recommend
cadence-shaping). See PLAN.md Phase 0.

Usage (headed, supervised; PYTHONUTF8=1 on Windows):

    .venv\Scripts\python.exe scripts\dev\spike_patchright.py ^
        --profile denon82 --engines playwright,patchright ^
        --prompt "a red ceramic mug on a wooden table, studio light"

    # primitives only (no generation), e.g. on a clean profile:
    .venv\Scripts\python.exe scripts\dev\spike_patchright.py ^
        --profile ffroliva --skip-generation

Outputs (gitignored): scripts/dev/_spike_out/spike_patchright_<ts>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import default_out_path, resolve_profile_dir, step  # noqa: E402

# Production surfaces under test (worktree src/ via the bootstrap above).
import gflow_cli.api.client as client_mod  # noqa: E402
import gflow_cli.api.transports.ui_automation as ui_mod  # noqa: E402
from gflow_cli.api import recaptcha as recaptcha_mod  # noqa: E402
from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.client import FlowApiClient  # noqa: E402
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model  # noqa: E402
from gflow_cli.api.recaptcha import RecaptchaError, discover_site_key  # noqa: E402
from gflow_cli.browser_manager import channel_for_profile  # noqa: E402

_EXECUTE_JS = recaptcha_mod._EXECUTE_JS  # noqa: SLF001 — spike reuses the production mint JS

# Original references so each engine run restores a clean module state.
_PW_ASYNC = client_mod.async_playwright
_ORIG_MINT = recaptcha_mod.TokenMinter.mint


def _engine_async_playwright(engine: str) -> Any:
    """Return the async_playwright factory for *engine* (imports patchright lazily)."""
    if engine == "playwright":
        return _PW_ASYNC
    if engine == "patchright":
        from patchright.async_api import async_playwright as patch_async

        return patch_async
    msg = f"unknown engine {engine!r}"
    raise ValueError(msg)


def _launch_kwargs(profile_dir: Path) -> dict[str, Any]:
    """Replicate client._persistent_context_kwargs verbatim (headed, identical for
    both engines so ENGINE is the only variable in the comparison)."""
    return {
        "user_data_dir": str(profile_dir),
        "headless": False,
        "viewport": {"width": 1280, "height": 720},
        "locale": "en-US",
        "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
        "channel": channel_for_profile(profile_dir),
        "ignore_default_args": ["--enable-automation", "--no-sandbox", "--password-store=basic"],
        "args": ["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
    }


async def _eval_token(page: Any, site_key: str, action: str, *, isolated: bool | None) -> Any:
    """Run the production reCAPTCHA execute JS. ``isolated=None`` → engine default
    (no kwarg, valid for both engines); ``isolated=False`` → patchright main-world."""
    if isolated is None:
        return await page.evaluate(_EXECUTE_JS, [site_key, action])
    return await page.evaluate(_EXECUTE_JS, [site_key, action], isolated_context=isolated)


async def probe_primitives(engine: str, profile_dir: Path, action: str) -> dict[str, Any]:
    """Phase A — primitives on a self-owned page. No FlowApiClient, full control."""
    out: dict[str, Any] = {"engine": engine, "channel": channel_for_profile(profile_dir)}
    apw = _engine_async_playwright(engine)
    req_count = {"request": 0, "response": 0}
    try:
        async with apw() as pw:
            try:
                ctx = await pw.chromium.launch_persistent_context(**_launch_kwargs(profile_dir))
            except Exception as exc:  # noqa: BLE001
                out["launch_error"] = f"{type(exc).__name__}: {exc}"
                out["hint"] = (
                    "patchright may need `patchright install chromium` if channel='chrome' "
                    "is not honoured — scenario 7."
                )
                return out
            out["launch_ok"] = True
            await ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})",
            )
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            page.on("request", lambda *_: req_count.__setitem__("request", req_count["request"] + 1))
            page.on(
                "response", lambda *_: req_count.__setitem__("response", req_count["response"] + 1)
            )
            await page.goto(routes.EDITOR_BOOTSTRAP_URL, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(4000)

            out["navigator_webdriver"] = await page.evaluate("() => navigator.webdriver")
            try:
                site_key = await discover_site_key(page)
                out["site_key_prefix"] = site_key[:12]
                out["discover_site_key"] = "ok"
            except RecaptchaError as exc:
                out["discover_site_key"] = f"FAIL: {exc}"
                site_key = ""

            # MINT A/B — the make-or-break (Leg 1 / scenario 1).
            if site_key:
                # Engine default (playwright: main world; patchright: isolated world).
                try:
                    tok = await _eval_token(page, site_key, action, isolated=None)
                    out["mint_default"] = "ok" if (isinstance(tok, str) and tok) else "empty"
                except Exception as exc:  # noqa: BLE001
                    out["mint_default"] = f"FAIL: {type(exc).__name__}: {str(exc)[:80]}"
                # Patchright-only: force main world.
                if engine == "patchright":
                    try:
                        tok2 = await _eval_token(page, site_key, action, isolated=False)
                        out["mint_isolated_false"] = (
                            "ok" if (isinstance(tok2, str) and tok2) else "empty"
                        )
                    except Exception as exc:  # noqa: BLE001
                        out["mint_isolated_false"] = f"FAIL: {type(exc).__name__}: {str(exc)[:80]}"

            out["listener_counts"] = dict(req_count)
            await ctx.close()
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc(limit=4)
    return out


def _install_engine(engine: str) -> None:
    """Runtime engine swap on the production modules + mint fix for patchright."""
    apw = _engine_async_playwright(engine)
    client_mod.async_playwright = apw
    ui_mod.async_playwright = apw
    if engine == "patchright":

        async def _patched_mint(self: Any, action: str) -> str:
            site_key = await self.site_key()
            token = await self._page.evaluate(  # noqa: SLF001
                _EXECUTE_JS, [site_key, action], isolated_context=False
            )
            if not isinstance(token, str) or not token:
                msg = f"empty token (patched main-world mint) for action={action!r}"
                raise RecaptchaError(msg)
            return token

        recaptcha_mod.TokenMinter.mint = _patched_mint  # type: ignore[method-assign]
    else:
        recaptcha_mod.TokenMinter.mint = _ORIG_MINT  # type: ignore[method-assign]


def _restore_engine() -> None:
    client_mod.async_playwright = _PW_ASYNC
    ui_mod.async_playwright = _PW_ASYNC
    recaptcha_mod.TokenMinter.mint = _ORIG_MINT  # type: ignore[method-assign]


async def probe_generation(
    engine: str, profile_dir: Path, *, prompt: str, aspect: str, model: str, action: str
) -> dict[str, Any]:
    """Phase B — faithful generate_image under *engine*; classify 200 vs 403 (Leg 6)."""
    out: dict[str, Any] = {"engine": engine}
    batch_hits: list[dict[str, Any]] = []
    _install_engine(engine)
    try:
        req = GenerateImageRequest(
            prompt=prompt, aspect=Aspect.from_cli(aspect), model=Model.from_cli(model)
        )
        async with FlowApiClient(profile_dir=profile_dir, headless=False) as client:
            # Attach a response listener to every pooled page (scenario 4 — assert
            # the batchGenerateImages body parses, not just that a callback fires).
            def _on_response(resp: Any) -> None:
                if "batchGenerateImages" in resp.url:
                    asyncio.create_task(_record(resp))

            async def _record(resp: Any) -> None:
                entry: dict[str, Any] = {"status": resp.status}
                try:
                    body = await resp.json()
                    entry["json_ok"] = True
                    entry["has_media"] = bool(
                        isinstance(body, dict) and (body.get("media") or body.get("results"))
                    )
                except Exception as exc:  # noqa: BLE001
                    entry["json_ok"] = False
                    entry["json_err"] = type(exc).__name__
                batch_hits.append(entry)

            for p in getattr(client, "_pages", []):
                p.on("response", _on_response)

            t0 = time.time()
            try:
                img = await client.generate_image(req=req, recaptcha_action=action)
                out["result"] = "OK_200"
                out["media_url_present"] = bool(getattr(img, "url", None) or getattr(img, "name", None))
            except RecaptchaError as exc:
                out["result"] = "MINT_BROKEN"
                out["detail"] = str(exc)[:160]
            except client_mod.WafRejectionError as exc:
                out["result"] = "WAF_403"
                out["detail"] = str(exc)[:160]
            except Exception as exc:  # noqa: BLE001
                out["result"] = f"OTHER:{type(exc).__name__}"
                out["detail"] = str(exc)[:160]
            out["elapsed_s"] = round(time.time() - t0, 1)
            await asyncio.sleep(0.5)  # let any in-flight _record tasks settle
            out["batch_responses"] = batch_hits
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc(limit=4)
    finally:
        _restore_engine()
    return out


async def _run(args: argparse.Namespace, profile_dir: Path, out_path: Path) -> int:
    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    results: dict[str, Any] = {
        "spike": "patchright-engine-phase0",
        "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profile": args.profile,
        "engines": engines,
        "action": args.action,
        "primitives": [],
        "generation": [],
    }
    for engine in engines:
        step("A", f"[{engine}] primitive probe", prefix="patch")
        results["primitives"].append(await probe_primitives(engine, profile_dir, args.action))
        if not args.skip_generation:
            step("B", f"[{engine}] faithful generation (credit-free image)", prefix="patch")
            results["generation"].append(
                await probe_generation(
                    engine,
                    profile_dir,
                    prompt=args.prompt,
                    aspect=args.aspect,
                    model=args.model,
                    action=args.action,
                )
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    # Console summary — the at-a-glance gate readout.
    print("\n================ PATCHRIGHT SPIKE SUMMARY ================", flush=True)
    for p in results["primitives"]:
        eng = p["engine"]
        print(
            f"[A {eng:10}] launch={p.get('launch_ok', p.get('launch_error', '?'))} "
            f"channel={p.get('channel')} webdriver={p.get('navigator_webdriver')} "
            f"discover={p.get('discover_site_key')} mint_default={p.get('mint_default')} "
            f"mint_iso_false={p.get('mint_isolated_false', 'n/a')} "
            f"listeners={p.get('listener_counts')}",
            flush=True,
        )
    for g in results["generation"]:
        eng = g["engine"]
        print(
            f"[B {eng:10}] result={g.get('result', g.get('error'))} "
            f"elapsed={g.get('elapsed_s')}s batch={g.get('batch_responses')}",
            flush=True,
        )
    print(f"\n[patch] full results -> {out_path}", flush=True)
    print(
        "[patch] GATE: pass = patchright mint works (mint_iso_false=ok) AND "
        "(on a hot profile) patchright result=OK_200 while playwright result=WAF_403.",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Patchright engine Phase-0 spike (credit-free).")
    p.add_argument("--profile", default="ffroliva", help="gflow profile name (use a HOT one for the decisive leg)")
    p.add_argument("--engines", default="playwright,patchright", help="comma list")
    p.add_argument("--prompt", default="a red ceramic mug on a wooden table, soft studio light")
    p.add_argument("--aspect", default="9:16")
    p.add_argument("--model", default="nano2")
    p.add_argument("--action", default="imageGeneration", help="reCAPTCHA action (memory: action mismatch → silent 403)")
    p.add_argument("--skip-generation", action="store_true", help="primitives only (no credit-free gen)")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    profile_dir = resolve_profile_dir(args.profile)
    out_path = Path(args.out) if args.out else default_out_path("spike_patchright", ".json")
    step("--", f"profile={args.profile} engines={args.engines} gen={not args.skip_generation}", prefix="patch")
    try:
        return asyncio.run(_run(args, profile_dir, out_path))
    except KeyboardInterrupt:
        print("[patch] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
