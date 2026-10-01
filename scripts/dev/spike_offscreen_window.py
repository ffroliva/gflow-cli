r"""Does an off-screen headed Chrome still drive Flow? (PR #923, $0)

PR #923 launches the generation browser with
``--window-position=-30000,-30000 --no-focus-on-init`` so it stops popping up in
front of the user. Headed Chrome is kept because reCAPTCHA rejects headless. The
open risk is Chrome's **occlusion tracking** (Windows/macOS): a window nobody can
see may be marked hidden, and a hidden page gets its timers throttled to 1 Hz and
its requestAnimationFrame stopped. Flow's own polling and rendering run on those,
and ``ui_automation_video.py`` already carries a ``bring_to_front()`` stall nudge,
so a hidden-page stall is not hypothetical here.

Three arms, same profile, same page, launched through ``FlowApiClient`` (so the
profile lease is held):

  control     no extra args (today's behaviour)
  offscreen   --window-position=-30000,-30000 --no-focus-on-init  (PR default)
  positioned  --window-position=100,100 --no-focus-on-init        (configurable case)

Per arm it records: the window bounds Chrome reports (CDP), the foreground window
before/after launch (Win32), ``document.visibilityState`` / ``hasFocus``, how
many rAF frames and 100 ms interval ticks fire in 5 s, and whether a reCAPTCHA
Enterprise token mints. Minting is free; no generation is submitted.

Pre-registered reading (written before the first run):

  offscreen: visible + rAF ~= control + ticks ~= 50 + mint OK
      -> occlusion is NOT throttling it; the flags are safe on this OS.
  offscreen: hidden, or rAF ~0, or ticks ~5
      -> Chrome treats it as occluded; the PR needs
         --disable-backgrounding-occluded-windows (or equivalent) before merge.
  offscreen: mint fails while control mints
      -> reCAPTCHA scores the window state; the PR is unsafe as-is.
  positioned: bounds.left/top == 100 -> a configurable position works.
  foreground after launch == foreground before -> no focus steal.
  control shows hidden/throttled too -> the probe is broken, not the PR; settles nothing.

NOT measured here: Chrome's *intensive* throttling (kicks in after ~5 min hidden),
macOS/Linux, and an actual generation — run the PR's CLI for that.

    python scripts/dev/spike_offscreen_window.py --profile ci-probe
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api._engine import mint_evaluate_kwargs  # noqa: E402
from gflow_cli.api.client import FlowApiClient  # noqa: E402
from gflow_cli.api.recaptcha import TokenMinter  # noqa: E402

from _spike_common import default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip

_FLOW = "https://flow.google.com"
_ARMS: dict[str, list[str]] = {
    "control": [],
    "offscreen": ["--window-position=-30000,-30000", "--no-focus-on-init"],
    "positioned": ["--window-position=100,100", "--no-focus-on-init"],
}

_THROTTLE_JS = r"""() => new Promise(resolve => {
  let raf = 0, ticks = 0;
  const loop = () => { raf++; requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
  const iv = setInterval(() => ticks++, 100);
  setTimeout(() => {
    clearInterval(iv);
    resolve({
      visibility: document.visibilityState,
      hidden: document.hidden,
      has_focus: document.hasFocus(),
      raf_frames_5s: raf,
      interval_ticks_5s: ticks,
      screen_x: window.screenX, screen_y: window.screenY,
    });
  }, 5000);
})"""


def _foreground() -> str:
    if sys.platform != "win32":
        return "n/a"
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


async def _arm(profile_dir: Path, name: str, extra: list[str]) -> dict[str, Any]:
    step(name, f"args={extra}")
    orig = FlowApiClient._persistent_context_kwargs

    def patched(self: FlowApiClient) -> Any:
        kw = orig(self)
        kw["args"] = [*kw["args"], *extra]
        return kw

    FlowApiClient._persistent_context_kwargs = patched  # type: ignore[method-assign]
    result: dict[str, Any] = {"arm": name, "args": extra, "fg_before": _foreground()}
    try:
        async with FlowApiClient(profile_dir=profile_dir, headless=False) as client:
            ctx = client._context  # noqa: SLF001 - spike reads the live context
            assert ctx is not None
            result["fg_after_launch"] = _foreground()
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(_FLOW, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(4000)
            cdp = await ctx.new_cdp_session(page)
            win = await cdp.send("Browser.getWindowForTarget")
            result["bounds"] = win.get("bounds")
            result.update(await page.evaluate(_THROTTLE_JS))
            try:
                token = await TokenMinter(page, mint_evaluate_kwargs=mint_evaluate_kwargs()).mint(
                    "image_generation"
                )
                result["mint"] = f"OK ({len(token)} chars, discarded)"
            except Exception as exc:  # noqa: BLE001 - the failure IS the measurement
                result["mint"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            result["fg_end"] = _foreground()
    finally:
        FlowApiClient._persistent_context_kwargs = orig  # type: ignore[method-assign]
    step(name, json.dumps({k: v for k, v in result.items() if k != "args"}))
    return result


async def _main(profile: str, arms: list[str]) -> int:
    profile_dir = resolve_profile_dir(profile)
    findings = [await _arm(profile_dir, a, _ARMS[a]) for a in arms]
    out = default_out_path("offscreen_window")
    out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
    step("wrote", str(out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="ci-probe")
    ap.add_argument("--arms", nargs="+", default=list(_ARMS), choices=list(_ARMS))
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.profile, args.arms)))
