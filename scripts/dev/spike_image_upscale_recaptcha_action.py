#!/usr/bin/env python3
r"""Image-upscale spike — capture the reCAPTCHA ACTION Flow uses for upscale (#171).

Two live smokes of `gflow image upscale` 403'd even after the request body was
made a byte-faithful replica of the working UI call. The last remaining
difference is the reCAPTCHA token: our `_mint_recaptcha_token` mints on the
bootstrap page with a GUESSED `action="upsampleImage"`, while the UI mints on the
editor page with Flow's real action. This spike hooks
`grecaptcha.enterprise.execute(siteKey, {action})` BEFORE page scripts run, then
drives the gallery -> image -> download -> 2K flow and dumps every captured
(siteKey, action) pair.

The action is recorded CLIENT-SIDE at execute() time, so it is captured even if
the server 403s — heat on the profile does not block this recon. Image upscale
is FREE (no credits).

Usage (headed, supervised):

    ! set PYTHONUTF8=1 && .venv\Scripts\python.exe ^
        scripts\dev\spike_image_upscale_recaptcha_action.py ^
        --profile ffroliva ^
        --project ffb768fb-cf2d-48b7-a135-92978667c37d ^
        --media 9d5e015c-0162-4b63-9ec4-bd6bf7c68200 --locale pt

Outputs (gitignored): scripts/dev/_spike_out/spike_image_upscale_recaptcha_action_<ts>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

# Init script: wrap grecaptcha[.enterprise].execute as soon as it appears and
# record every (siteKey, action) into window.__gcap_calls. Runs before page JS.
_HOOK_JS = r"""
(() => {
  window.__gcap_calls = window.__gcap_calls || [];
  const wrap = (obj, label) => {
    if (!obj || obj.__gcap_hooked || typeof obj.execute !== 'function') return;
    obj.__gcap_hooked = true;
    const orig = obj.execute;
    obj.execute = function (...args) {
      try {
        const sk = args[0];
        const opts = (args[1] && typeof args[1] === 'object') ? args[1] : {};
        window.__gcap_calls.push({
          via: label,
          siteKey: typeof sk === 'string' ? sk : '(non-string)',
          action: opts.action !== undefined ? opts.action : null,
          t: Date.now(),
        });
      } catch (e) { /* never break the real call */ }
      return orig.apply(this, args);
    };
  };
  let tries = 0;
  const iv = setInterval(() => {
    tries++;
    try {
      if (window.grecaptcha) {
        wrap(window.grecaptcha, 'grecaptcha');
        if (window.grecaptcha.enterprise) wrap(window.grecaptcha.enterprise, 'enterprise');
      }
    } catch (e) { /* ignore */ }
    if (tries > 900) clearInterval(iv);  // ~90s
  }, 100);
})()
"""


def _gallery_url(locale: str | None, project_id: str) -> str:
    seg = f"/{locale.strip().split('-', 1)[0].lower()}" if locale else ""
    return f"https://labs.google/fx{seg}/tools/flow/project/{project_id}"


async def _run(
    *, profile_dir: Path, project_id: str, media_id: str | None, locale: str | None, out_path: Path
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001
        # Install the hook on EVERY navigation, before page scripts execute.
        await page.context.add_init_script(_HOOK_JS)

        gurl = _gallery_url(locale, project_id)
        step("1", f"goto gallery {gurl}", prefix="gcap")
        await page.goto(gurl, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(4500)

        tile = f"a[href*='{media_id}']" if media_id else "a[href*='/edit/']"
        try:
            await page.locator(tile).first.click(timeout=10_000)
            await page.wait_for_function("() => location.href.includes('/edit/')", timeout=15_000)
            step("2", f"editor open {page.url[-50:]}", prefix="gcap")
        except Exception as e:  # noqa: BLE001
            step("2-warn", str(e), prefix="gcap")
        await page.wait_for_timeout(3000)

        try:
            await page.locator("button:has(i.google-symbols:text-is('download'))").first.click(
                timeout=8000
            )
            await page.wait_for_timeout(1200)
            await page.locator("[role='menuitem']:has-text('2K')").first.click(timeout=8000)
            step("3", "clicked download -> 2K; reCAPTCHA should fire now", prefix="gcap")
        except Exception as e:  # noqa: BLE001
            step("3-warn", str(e), prefix="gcap")

        # Let the upscale's grecaptcha.execute fire + the call settle.
        await page.wait_for_timeout(6000)
        calls: Any = await page.evaluate("() => window.__gcap_calls || []")
        try:
            await page.screenshot(path=str(out_path.parent / "gcap_after.png"))
        except Exception:  # noqa: BLE001
            pass
        client._checkin_page(page)  # noqa: SLF001

    out_path.write_text(
        json.dumps(
            {
                "spike": "image-upscale-recaptcha-action",
                "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "projectId": project_id,
                "mediaId": media_id,
                "grecaptchaCalls": calls,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\n[gcap] grecaptcha.execute calls captured: {len(calls)}", flush=True)
    for c in calls:
        sk = str(c.get("siteKey"))[:16]
        print(f"  via={c.get('via')} action={c.get('action')!r} siteKey={sk}...", flush=True)
    print(f"[gcap] out -> {out_path}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Capture Flow's reCAPTCHA action for image upscale (0 credits)."
    )
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_PROFILE", "ffroliva"))
    p.add_argument("--project", required=True)
    p.add_argument("--media", default=None)
    p.add_argument("--locale", default="pt")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    profile_dir = resolve_profile_dir(args.profile)
    out_path = (
        Path(args.out)
        if args.out
        else default_out_path("spike_image_upscale_recaptcha_action", ".json")
    )
    step("--", f"profile={args.profile} project={args.project}", prefix="gcap")
    try:
        return asyncio.run(
            _run(
                profile_dir=profile_dir,
                project_id=args.project,
                media_id=args.media,
                locale=args.locale,
                out_path=out_path,
            )
        )
    except KeyboardInterrupt:
        print("[gcap] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
