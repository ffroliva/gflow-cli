r"""What does flow.google.com show an account that cannot afford a video, and where? ($0)

Cost: $0. Navigation, DOM reads and an empty project are free. The prompt is typed and
never submitted, and every billed RPC (``YhhmEf``, ``eb1hJf``, ``MZZa6b``, ``nprQif``,
``fZytfe``, ``ogiZ0b``) is route-ABORTED before it can leave the browser, as is any
generation POST to ``aisandbox-pa.googleapis.com``. The route handler takes exactly two
parameters (a default argument let two "aborted" submits bill on 2026-11-27).

Phases, each recorded structurally (roles, aria, hrefs, ligatures, custom-element tags,
classes; never display text) with a screenshot under ``scripts/dev/_spike_out/``:

1. ``landing``: ``https://flow.google.com/`` before anything is dismissed: every
   ``[role=dialog]`` / ``mat-dialog-container`` / ``.cdk-overlay-pane``, and what
   ``elementFromPoint`` returns over the new-project button.
2. ``dismissed``: the same after the production ``_dismiss_dialog``.
3. ``create``: the production ``create_project`` (the path that raised exit 23 on this
   profile); its exception, if any, is the finding.
4. ``composer``: in the new project, the production ``apply_video_settings`` with
   ``--model`` (default veo_3_1_quality, priced above a short balance), then the prompt
   typed; ligature/button inventory before and after typing, and whether
   ``button.prompt-warning-button`` and ``arrow_forward`` are present.

Pre-registered readings (written before the first run):

| Observation | Reading |
|---|---|
| warning PRESENT, arrow_forward ABSENT after typing | Flow replaces submit for a model the balance cannot cover; the existing CREDITS_WARNING anchor holds |
| warning ABSENT, arrow_forward PRESENT | Flow does not gate this model on this balance in the composer; a refusal (if any) happens at submit, unmeasured here |
| a dialog on landing whose elementFromPoint covers the new-project button | that dialog explains exit 23 at create |
| no dialog, create succeeds | the 2026-11-27 exit 23 does not reproduce; settles nothing |

    uv run python scripts/dev/spike_out_of_credits.py --profile denon82 [--headless]
"""

# ruff: noqa: E501 - the in-page survey JS and the readings table are kept on one line each
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

from gflow_cli.api.transports import migrated_composer as mc  # noqa: E402
from gflow_cli.api.transports._mc_common import (  # noqa: E402
    CREDITS_WARNING,
    NEW_PROJECT_BUTTON,
)
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel  # noqa: E402

_BILLED = ("YhhmEf", "eb1hJf", "MZZa6b", "nprQif", "fZytfe", "ogiZ0b")
PROMPT = "A red cube on a white table, static camera."

_SURVEY_JS = """
(newProjectSel) => {
  const vis = (el) => {
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
            visible: r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'};
  };
  const cls = (el) => (typeof el.className === 'string' ? el.className : '').trim().split(/\\s+/).slice(0, 6);
  const desc = (el) => el ? {tag: el.tagName.toLowerCase(), classes: cls(el), role: el.getAttribute('role'),
      aria_label_present: el.hasAttribute('aria-label'),
      ancestors: (() => { const a = []; let p = el.parentElement;
        while (p && a.length < 8) { a.push(p.tagName.toLowerCase() + (p.getAttribute('role') ? `[role=${p.getAttribute('role')}]` : '')); p = p.parentElement; }
        return a; })()} : null;
  const ligs = (root) => [...root.querySelectorAll('mat-icon, i.google-symbols, i.material-icons')]
      .map(i => (i.textContent || '').trim()).filter(Boolean);
  const overlays = [...document.querySelectorAll(
      "[role='dialog'], [role='alertdialog'], mat-dialog-container, .cdk-overlay-pane, .cdk-overlay-backdrop-showing")]
    .map(o => ({
      ...desc(o), ...vis(o),
      aria_modal: o.getAttribute('aria-modal'),
      custom_elements: [...new Set([...o.querySelectorAll('*')].map(e => e.tagName.toLowerCase()).filter(t => t.includes('-')))].slice(0, 30),
      hrefs: [...o.querySelectorAll('a[href]')].map(a => { try { const u = new URL(a.href); return u.host + u.pathname; } catch (e) { return a.getAttribute('href'); } }),
      ligatures: ligs(o),
      buttons: [...o.querySelectorAll('button, [role=button], a')].map(b => ({tag: b.tagName.toLowerCase(), classes: cls(b),
          ligatures: ligs(b), type: b.getAttribute('type'), has_href: b.hasAttribute('href'),
          disabled: b.disabled === true || b.getAttribute('aria-disabled') === 'true'})),
      images: [...o.querySelectorAll('img, video, lottie-player')].map(i => ({tag: i.tagName.toLowerCase(),
          src_host: (() => { try { const u = new URL(i.currentSrc || i.src); return u.host + u.pathname.slice(0, 60); } catch (e) { return null; } })()})),
    }));
  let np = null;
  const btn = newProjectSel;  // an element handle resolved by Playwright, or null
  if (btn) {
    const r = btn.getBoundingClientRect();
    const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    np = {...vis(btn), receives_events: !!top && (top === btn || btn.contains(top)), top: desc(top)};
  }
  const warn = [...document.querySelectorAll('button.prompt-warning-button')].map(b => ({...vis(b), classes: cls(b), ligatures: ligs(b)}));
  return {
    url: location.host + location.pathname,
    overlays,
    new_project_button: np,
    credits_warning: warn,
    all_ligatures: [...new Set(ligs(document))].sort(),
    arrow_forward_buttons: [...document.querySelectorAll('button')].filter(b => ligs(b).includes('arrow_forward')).map(b => ({...vis(b), disabled: b.disabled === true || b.getAttribute('aria-disabled') === 'true'})),
    visible_buttons: [...document.querySelectorAll('button')].filter(b => vis(b).visible).map(b => ({classes: cls(b), ligatures: ligs(b), disabled: b.disabled === true})).slice(0, 80),
  };
}
"""


def _guard(aborted: list[str]) -> Any:
    async def on_route(route: Any, _request: Any) -> None:
        r = route.request
        # the body is form-encoded: `"nprQif"` only appears after decoding
        data = unquote_plus((r.post_data_buffer or b"").decode("utf-8", errors="replace"))
        rid = (parse_qs(urlsplit(r.url).query).get("rpcids") or [""])[0]
        billed_rpc = any(s in rid for s in _BILLED) or any(f'"{s}"' in data for s in _BILLED)
        aisandbox_gen = (
            "aisandbox-pa.googleapis.com" in r.url
            and r.method == "POST"
            and ("generate" in r.url.lower() or "batchAsync" in r.url)
        )
        if billed_rpc or aisandbox_gen:
            await route.abort()  # first: nothing may stand between a submit and its abort
            aborted.append(rid or urlsplit(r.url).path)
        else:
            await route.fallback()

    return on_route


async def _snap(page: Any, label: str, findings: dict[str, Any], stem: str) -> None:
    # NEW_PROJECT_BUTTON is a Playwright selector (`:text-is`), so Playwright resolves it.
    loc = page.locator(NEW_PROJECT_BUTTON).first
    handle = await loc.element_handle(timeout=2_000) if await loc.count() else None
    findings[label] = await page.evaluate(_SURVEY_JS, handle)
    shot = default_out_path(f"{stem}_{label}", ".png")
    await page.screenshot(path=str(shot), full_page=False)
    findings[label]["screenshot"] = shot.name
    f = findings[label]
    step(
        label,
        f"overlays={len(f['overlays'])} warning={len(f['credits_warning'])} "
        f"arrow_forward={len(f['arrow_forward_buttons'])} np={f['new_project_button']} shot={shot.name}",
    )


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--model", default=VideoModel.VEO_3_1_QUALITY.value)
    args = ap.parse_args()

    vis = "headless" if args.headless else "headed"
    stem = f"spike_out_of_credits_{vis}"
    findings: dict[str, Any] = {"profile": args.profile, "visibility": vis, "model": args.model}
    aborted: list[str] = []
    rpcs: list[str] = []
    async with build_client(resolve_profile_dir(args.profile), headless=args.headless) as client:
        page = client._page  # noqa: SLF001 - spike reads the live page
        await page.context.route("**/*", _guard(aborted))
        page.on(
            "request",
            lambda r: (
                rpcs.append(rid)
                if (rid := (parse_qs(urlsplit(r.url).query).get("rpcids") or [""])[0])
                else None
            ),
        )
        await page.goto(mc.MIGRATED_ROOT_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(8_000)
        await _snap(page, "landing", findings, stem)

        await mc.MigratedComposer._dismiss_dialog(page)  # noqa: SLF001
        await page.wait_for_timeout(2_000)
        await _snap(page, "dismissed", findings, stem)

        try:
            info = await mc.create_project(page, f"spike-out-of-credits-{vis}")
            findings["create"] = {"ok": True, "project_id": info.project_id}
        except Exception as exc:  # noqa: BLE001 - the failure is the finding
            findings["create"] = {
                "ok": False,
                "error": type(exc).__name__,
                "exit_code": getattr(exc, "exit_code", None),
                "detail": str(getattr(exc, "detail", exc))[:400],
            }
            await _snap(page, "create_failed", findings, stem)
            # Hypothesis test: production dismisses right after domcontentloaded, before the
            # promo dialog renders. Wait for the dialog, dismiss it, then click Create once
            # with the production click (no create request was sent above, so this is the
            # first one).
            try:
                await page.locator(mc.DIALOG).first.wait_for(state="visible", timeout=10_000)
            except Exception:  # noqa: BLE001 - absence is recorded by the next snapshot
                pass
            await mc.MigratedComposer._dismiss_dialog(page)  # noqa: SLF001
            await page.wait_for_timeout(1_500)
            await _snap(page, "redismissed", findings, stem)
            try:
                await mc._click_create(page, page.locator(NEW_PROJECT_BUTTON).first)  # noqa: SLF001
                await page.wait_for_url("**/project/**", timeout=30_000)
                findings["create_after_wait"] = {"ok": True, "url": page.url.split("?")[0]}
                findings["create"]["ok"] = True
            except Exception as exc:  # noqa: BLE001
                findings["create_after_wait"] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                }
        step("create", json.dumps(findings["create"]))
        step("create_after_wait", json.dumps(findings.get("create_after_wait")))

        if findings["create"]["ok"]:
            await page.wait_for_timeout(5_000)
            await _snap(page, "project", findings, stem)
            composer = mc.MigratedComposer()
            req = GenerateVideoRequest(
                prompt=PROMPT,
                mode=Mode.T2V,
                aspect=Aspect.LANDSCAPE,
                model=VideoModel(args.model),
            )
            try:
                findings["quote"] = await composer.apply_video_settings(page, req)
            except Exception as exc:  # noqa: BLE001
                findings["settings_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            await _snap(page, "settings_applied", findings, stem)
            try:
                await composer.send_prompt(page, PROMPT)
                await page.wait_for_timeout(3_000)
            except Exception as exc:  # noqa: BLE001
                findings["type_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            await _snap(page, "typed", findings, stem)
            findings["warning_visible"] = await page.locator(CREDITS_WARNING).first.is_visible()

    findings["billed_rpcs_aborted"] = aborted
    findings["rpcids_seen"] = sorted(set(rpcs))
    out = default_out_path(stem)
    out.write_text(json.dumps(findings, indent=2), encoding="utf-8")
    step("aborted", str(aborted) or "(none)")
    step("done", f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
