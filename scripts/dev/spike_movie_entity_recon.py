#!/usr/bin/env python3
r"""Movie-consistency spike — Tier 1 (0 credits): video-composer resource-picker recon.

Goal: discover the selectors needed for the P2 `_attach_character_entities`
transport (resource picker → "Personagens" → "Incluir no comando") and check
whether the video composer exposes an audio/voice toggle for dialogue.

NO generation is performed — this only navigates the composer and opens the
"Add Media" resource picker, then dumps DOM + screenshots. Credit cost: 0.

Usage (headed, supervised):

    ! .venv\Scripts\python.exe scripts\dev\spike_movie_entity_recon.py \
        --profile denon82 --project 6ba50219-0fb5-4471-a96e-83257784dfd8 --locale pt

Outputs (gitignored): scripts/dev/_spike_out/spike_movie_entity_recon_<ts>.{json,html,*.png}
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

# In-page collector: dialogs, tabs, buttons, and any audio/voice-ish control.
_DUMP_JS = r"""
(() => {
  const norm = (s) => (s || '').trim().replace(/\s+/g, ' ').slice(0, 80);
  const pick = (el) => ({
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute('role'),
    id: el.id || null,
    aria: el.getAttribute('aria-label'),
    title: el.getAttribute('title'),
    text: norm(el.innerText || el.textContent),
    cls: norm(el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className),
  });
  const dialogs = [...document.querySelectorAll("[role='dialog']")].map(d => d.outerHTML.slice(0, 6000));
  const tabs = [...document.querySelectorAll("[role='tab']")].map(pick);
  const buttons = [...document.querySelectorAll("button,[role='button']")].map(pick)
      .filter(b => b.text || b.aria || b.title);
  const symbols = [...document.querySelectorAll("i.google-symbols,span.material-symbols-outlined,i.material-symbols-outlined")]
      .map(i => norm(i.innerText || i.textContent)).filter(Boolean);
  // audio/voice candidates by text/aria/symbol
  const audioRe = /(audio|som|voz|voice|sound|mudo|mute|volume|narra)/i;
  const audio = [...document.querySelectorAll("button,[role='button'],[role='switch'],[role='checkbox'],label,i")]
      .map(pick).filter(e => audioRe.test([e.text,e.aria,e.title,e.cls].join(' ')));
  // resource-picker candidates by text (locale pt: Personagens/Imagens/Videos/Uploads/Pesquisar/Incluir)
  const pickRe = /(personagen|imagens|v[ií]deos|uploads|pesquisar recursos|incluir no comando|recentes)/i;
  const picker = [...document.querySelectorAll("button,[role='button'],[role='tab'],input,div")]
      .map(pick).filter(e => pickRe.test([e.text,e.aria,e.title].join(' ')));
  return {
    url: location.href,
    count: document.querySelectorAll('*').length,
    dialogCount: dialogs.length,
    dialogs, tabs, buttons, symbols: [...new Set(symbols)].sort(), audio, picker,
  };
})()
"""


async def _shot(page: Any, out_dir: Path, name: str) -> None:
    try:
        await page.screenshot(path=str(out_dir / f"{name}.png"))
        step("shot", name, prefix="recon")
    except Exception as e:  # noqa: BLE001
        step("shot-fail", f"{name}: {e}", prefix="recon")


async def _try(label: str, coro: Any) -> bool:
    try:
        await coro
        step("ok", label, prefix="recon")
        return True
    except Exception as e:  # noqa: BLE001
        step("FAIL", f"{label}: {type(e).__name__}: {e}", prefix="recon")
        return False


async def _run(*, profile_dir: Path, project_id: str, locale: str, out_path: Path) -> int:
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stages: dict[str, Any] = {}

    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001
        try:
            url = routes.project_editor_url(locale, project_id)
            step("1", f"goto {url}", prefix="recon")
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(4_000)
            await page.keyboard.press("Escape")
            await _shot(page, out_dir, "01_editor")

            await _try("exit_agent_mode", VideoGenerationMixin._exit_agent_mode(page))  # noqa: SLF001
            stages["video_mode"] = await _try(
                "switch_to_video_mode",
                VideoGenerationMixin._switch_to_video_mode(page, out_dir=None),  # noqa: SLF001
            )
            await _shot(page, out_dir, "02_video_mode")

            stages["references"] = await _try(
                "switch_sub_mode_references",
                VideoGenerationMixin._switch_video_sub_mode(page, "references", out_dir=None),  # noqa: SLF001
            )
            await page.wait_for_timeout(800)
            await _shot(page, out_dir, "03_references")

            # Open the resource picker via the "Add Media" button.
            clicked = False
            try:
                btn = page.locator(ADD_MEDIA_BUTTON).first
                await btn.wait_for(state="visible", timeout=8000)
                await btn.click()
                clicked = True
                await page.wait_for_timeout(1500)
            except Exception as e:  # noqa: BLE001
                step("FAIL", f"add_media click: {type(e).__name__}: {e}", prefix="recon")
            stages["picker_opened"] = clicked
            await _shot(page, out_dir, "04_picker")

            # Dump DOM (whether or not the picker opened — we learn from both).
            dump: Any = await page.evaluate(_DUMP_JS)
            outer_html: str = await page.content()
        finally:
            client._checkin_page(page)  # noqa: SLF001

    result = {
        "spike": "movie-entity-recon",
        "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "projectId": project_id,
        "locale": locale,
        "stages": stages,
        "dump": dump,
    }
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    out_path.with_suffix(".html").write_text(outer_html, encoding="utf-8")
    print(f"[recon] json -> {out_path}", flush=True)
    print(f"[recon] html -> {out_path.with_suffix('.html')}", flush=True)
    print(f"[recon] stages = {stages}", flush=True)
    print(
        f"[recon] dump: tabs={len(dump.get('tabs',[]))} "
        f"picker-hits={len(dump.get('picker',[]))} audio-hits={len(dump.get('audio',[]))} "
        f"dialogs={dump.get('dialogCount')}",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Movie spike Tier-1 recon (0 credits).")
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_PROFILE", "denon82"))
    p.add_argument("--project", required=True)
    p.add_argument("--locale", default="pt")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    profile_dir = resolve_profile_dir(args.profile)
    out_path = Path(args.out) if args.out else default_out_path("spike_movie_entity_recon", ".json")
    step("--", f"profile={args.profile} project={args.project} locale={args.locale}", prefix="recon")
    try:
        return asyncio.run(
            _run(profile_dir=profile_dir, project_id=args.project, locale=args.locale, out_path=out_path)
        )
    except KeyboardInterrupt:
        print("[recon] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
