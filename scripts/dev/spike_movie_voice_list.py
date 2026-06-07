#!/usr/bin/env python3
r"""Movie-consistency spike — voice list recon (0 credits).

Opens the Flow video composer, switches to Elementos (references) sub-mode,
opens the resource picker, clicks the "Vozes" (voice_selection) tab, and dumps
the available voice tiles (their visible names = referenceAudio / presetVoiceId
candidates, e.g. "alnilam", "vega"). NO generation. Credit cost: 0.

Usage (headed, supervised):

    ! set PYTHONUTF8=1 && .venv\Scripts\python.exe scripts\dev\spike_movie_voice_list.py \
        --profile denon82 --project 6ba50219-0fb5-4471-a96e-83257784dfd8 --locale pt
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

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.transports.ui_automation_video import (  # noqa: E402
    ADD_MEDIA_BUTTON,
    VideoGenerationMixin,
)

_VOZES_TAB = (
    "[role='tab']:has(i.google-symbols:text-is('voice_selection')), "
    "button:has(i.google-symbols:text-is('voice_selection'))"
)

# Dump every voice-ish tile in the open picker dialog.
_DUMP_JS = r"""
(() => {
  const norm = (s) => (s || '').trim().replace(/\s+/g, ' ').slice(0, 60);
  const dialog = document.querySelector("[role='dialog']");
  const root = dialog || document.body;
  const tiles = [...root.querySelectorAll("button,[role='option'],[role='listitem'],li")]
    .map(el => ({
      text: norm(el.innerText || el.textContent),
      aria: el.getAttribute('aria-label'),
    }))
    .filter(t => t.text || t.aria);
  return {
    url: location.href,
    dialogPresent: !!dialog,
    tileCount: tiles.length,
    tiles,
  };
})()
"""


async def _shot(page: Any, out_dir: Path, name: str) -> None:
    try:
        await page.screenshot(path=str(out_dir / f"{name}.png"))
        step("shot", name, prefix="voices")
    except Exception as e:  # noqa: BLE001
        step("shot-fail", f"{name}: {e}", prefix="voices")


async def _run(*, profile_dir: Path, project_id: str, locale: str, out_path: Path) -> int:
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001
        try:
            url = routes.project_editor_url(locale, project_id)
            step("1", f"goto {url}", prefix="voices")
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(4_000)
            await page.keyboard.press("Escape")
            try:
                await VideoGenerationMixin._exit_agent_mode(page)  # noqa: SLF001
                await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)  # noqa: SLF001
                await VideoGenerationMixin._switch_video_sub_mode(page, "references", out_dir=None)  # noqa: SLF001
            except Exception as e:  # noqa: BLE001
                step("nav-warn", str(e), prefix="voices")
            # Open picker, switch to Vozes.
            btn = page.locator(ADD_MEDIA_BUTTON).first
            await btn.wait_for(state="visible", timeout=8000)
            await btn.click()
            await page.wait_for_timeout(1200)
            await _shot(page, out_dir, "01_picker")
            try:
                await page.locator(_VOZES_TAB).first.click()
                await page.wait_for_timeout(1000)
            except Exception as e:  # noqa: BLE001
                step("vozes-warn", f"could not click Vozes tab: {e}", prefix="voices")
            await _shot(page, out_dir, "02_vozes")
            dump: Any = await page.evaluate(_DUMP_JS)
            outer_html: str = await page.content()
        finally:
            client._checkin_page(page)  # noqa: SLF001

    out_path.write_text(
        json.dumps(
            {
                "spike": "movie-voice-list",
                "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "projectId": project_id,
                "dump": dump,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out_path.with_suffix(".html").write_text(outer_html, encoding="utf-8")
    print(f"[voices] json -> {out_path}", flush=True)
    print(f"[voices] tiles found: {dump.get('tileCount')}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Movie voice-list recon (0 credits).")
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_PROFILE", "denon82"))
    p.add_argument("--project", required=True)
    p.add_argument("--locale", default="pt")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    profile_dir = resolve_profile_dir(args.profile)
    out_path = Path(args.out) if args.out else default_out_path("spike_movie_voice_list", ".json")
    step("--", f"profile={args.profile} project={args.project}", prefix="voices")
    try:
        return asyncio.run(
            _run(
                profile_dir=profile_dir,
                project_id=args.project,
                locale=args.locale,
                out_path=out_path,
            )
        )
    except KeyboardInterrupt:
        print("[voices] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
