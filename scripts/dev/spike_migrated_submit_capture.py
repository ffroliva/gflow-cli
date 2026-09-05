r"""What does the migrated flow.google.com editor put on the wire at submit? ($0)

The predict for a flow.google.com driver (2026-09-04) said the DOM is the same
domain model but the WIRE is unknown: the migrated app talks batchexecute, and
gflow's result capture keys on aisandbox REST routes. This spike answers the
part that costs nothing, with the repo's route-abort pattern
(credit-free-route-abort-verification): the generation request is intercepted
by a unique prompt marker and ABORTED before it leaves the browser, so nothing
is billed and nothing is generated.

Per run it records:

* whether a DIRECT load of https://flow.google.com/project/<id> is served the
  Angular app (or bounced to labs.google) -- for a flagged account that is the
  bootstrap the driver should use; for an unflagged one it answers whether
  flow.google.com can be the default host for EVERYONE (--no-submit probe)
* every radiogroup the settings panel renders, and whether clicking flips
  aria-checked
* the submit control that actually fires the request
* the aborted request: rpcids, URL params, header names, body shape, whether a
  reCAPTCHA-Enterprise-shaped token rides along, and every batchexecute /
  aisandbox / recaptcha request in the seconds around the click

Selector notes for the future driver: Playwright's CSS `:text-matches('...')`
goes through CSS string escaping, which turns `\s` into `s` -- match radio
labels with a Python-side `filter(has_text=re.compile(...))` instead.

    python scripts/dev/spike_migrated_submit_capture.py --profile ffroliva --project <id>
    python scripts/dev/spike_migrated_submit_capture.py --profile denon82 --project <id> --no-submit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{200,}")
_AT_RE = re.compile(r"(at=)[^&]+")
_RADIO = "[role='radio']"
_OVERLAY = ".cdk-overlay-pane"
_BACKDROP = ".cdk-overlay-backdrop"

_DOM_JS = r"""() => {
  const c = (s) => document.querySelectorAll(s).length;
  return {
    host: location.host, path: location.pathname,
    lang: document.documentElement.getAttribute('lang'),
    mat_icon: c('mat-icon'), i_google_symbols: c('i.google-symbols'),
    settings_trigger: c('.settings-trigger-button'),
    textarea: c('textarea'), radiogroup: c("[role='radiogroup']"),
    title: document.title.slice(0, 80),
    body_head: (document.body.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 160),
  };
}"""

_GROUPS_JS = r"""gs => gs.map(g => [...g.querySelectorAll("[role='radio']")].map(r => ({
  text: (r.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 24),
  checked: r.getAttribute('aria-checked'),
  lig: (r.querySelector('mat-icon') || {}).textContent || null,
})))"""


_BODY_RPCS = {"YhhmEf", "jwpduf", "as29s", "WuwhI", "Zzl0ze", "HTrJv", "o30O0e", "nzlxg"}
_SIGNED_RE = re.compile(r"(https?://[A-Za-z0-9./_%-]+)\?[A-Za-z0-9=&%._+:-]*")


def _redact_body(text: str) -> str:
    """Response text with long tokens and signed-URL query strings stripped."""
    t = _TOKEN_RE.sub(lambda m: f"<{len(m.group(0))}-char-token>", text)
    t = _SIGNED_RE.sub(r"?<signed>", t)
    return t[:2500]


def tc_watch_from(t_click: float | None, t0: float) -> float:
    return 0.0 if t_click is None else round(t_click - t0, 2)


def _exact(label: str) -> re.Pattern[str]:
    return re.compile(r"^\s*" + re.escape(label) + r"\s*$")


def _redact(body: str | None) -> dict[str, Any]:
    if not body:
        return {"len": 0}
    tokens = _TOKEN_RE.findall(body)
    stripped = _TOKEN_RE.sub(lambda m: f"<{len(m.group(0))}-char-token>", body)
    stripped = _AT_RE.sub(r"\1<at>", stripped)
    return {
        "len": len(body),
        "token_like_runs": [len(t) for t in tokens],
        "has_recaptcha_shape": any(t.startswith(("0cAF", "03AF")) for t in tokens),
        "head": stripped[:900],
    }


def _url_facts(url: str) -> dict[str, Any]:
    p = urlsplit(url)
    q = parse_qs(p.query)
    keys = ("rpcids", "source-path", "hl", "rt", "_reqid", "bl")
    keep: dict[str, Any] = {k: q[k][0][:80] for k in keys if k in q}
    keep["has_f.sid"] = "f.sid" in q
    return {"path": p.path, **keep}


async def _select_radio(loc: Any, label: str, log: list[dict[str, Any]]) -> None:
    loc = loc.first
    n = await loc.count()
    entry: dict[str, Any] = {"label": label, "found": bool(n)}
    if n:
        try:
            before = await loc.get_attribute("aria-checked")
            await loc.click(timeout=4000)
            await asyncio.sleep(0.3)
            entry.update(before=before, after=await loc.get_attribute("aria-checked"))
        except Exception as e:  # noqa: BLE001 - record, never abort the capture
            entry["error"] = str(e)[:160]
    log.append(entry)
    step("radio", json.dumps(entry)[:160])


async def _close_panel(page: Any) -> str:
    """Escape, else toggle the trigger, else click the backdrop. Returns what worked."""
    await page.keyboard.press("Escape")
    for attempt in ("escape", "trigger", "backdrop"):
        await asyncio.sleep(0.6)
        pane = page.locator(_OVERLAY).first
        if not await pane.count() or not await pane.is_visible():
            return attempt
        if attempt == "escape":
            await page.locator(".settings-trigger-button").first.click(timeout=3000)
        elif attempt == "trigger":
            bd = page.locator(_BACKDROP).first
            if await bd.count():
                await bd.click(timeout=3000, force=True)
    return "still-open"


async def _main(
    profile: str,
    project: str,
    *,
    submit: bool,
    direct: bool,
    spend: bool = False,
    probe_models: bool = False,
) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")
    marker = "gflowcanary" + uuid.uuid4().hex[:10]
    prompt = f"a teal origami crane on a wooden table {marker}"
    out: dict[str, Any] = {
        "profile": profile,
        "project": project[:8] + "...",
        "mode": (
            "REAL generation (credits spent)"
            if spend
            else "submit-capture (aborted, $0)"
            if submit
            else "direct-load probe (no submit)"
        ),
        "direct": direct,
        "marker": marker,
    }
    net: list[dict[str, Any]] = []
    captured: list[dict[str, Any]] = []
    t_click: float | None = None
    seen = asyncio.Event()
    t0 = time.monotonic()

    def rel() -> float:
        return round(time.monotonic() - t0, 2)

    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike reads the live context
        assert context is not None

        async def on_route(route: Any) -> None:
            req = route.request
            body = req.post_data or ""
            if marker in body:
                captured.append(
                    {
                        "t": rel(),
                        "since_click": (
                            None if t_click is None else round(time.monotonic() - t_click, 2)
                        ),
                        "method": req.method,
                        "url": _url_facts(req.url),
                        "header_names": sorted(k.lower() for k in req.headers),
                        "body": _redact(body),
                    }
                )
                rpc = _url_facts(req.url).get("rpcids")
                step("ABORT", f"marker request rpcids={rpc} aborted at +{rel()}s")
                seen.set()
                if spend:
                    await route.continue_()
                    return
                await route.abort()
                return
            await route.continue_()

        def on_request(req: Any) -> None:
            u = req.url
            if "batchexecute" in u or "aisandbox" in u or "recaptcha" in u:
                net.append(
                    {"t": rel(), "m": req.method, **_url_facts(u), "host": urlsplit(u).hostname}
                )

        page = await context.new_page()
        step("stage", "page opened; installing page-level route + request log")
        await page.route("**/batchexecute**", on_route)
        page.on("request", on_request)
        responses: list[dict[str, Any]] = []
        media_hosts: set[str] = set()

        async def on_response(resp: Any) -> None:
            u = resp.url
            if "batchexecute" not in u:
                return
            try:
                text = await resp.text()
            except Exception:  # noqa: BLE001 - aborted/failed bodies are fine to skip
                return
            hosts = sorted(set(re.findall(r"https?://([a-z0-9.\-]+)", text)))
            hit = {
                "t": rel(),
                "status": resp.status,
                "rpcids": _url_facts(u).get("rpcids"),
                "len": len(text),
                "marker": marker in text,
                "mp4": ".mp4" in text or "video/mp4" in text,
                "hosts": [h for h in hosts if "google" in h or "gstatic" in h][:6],
            }
            media_hosts.update(h for h in hosts if "storage" in h or "googleusercontent" in h)
            rpc = hit["rpcids"] or ""
            if rpc in _BODY_RPCS or hit["marker"]:
                hit["body"] = _redact_body(text)
            responses.append(hit)

        page.on("response", on_response)
        try:
            url = (
                f"https://flow.google.com/project/{project}"
                if direct
                else f"https://labs.google/fx/tools/flow/project/{project}"
            )
            step("stage", f"goto {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            step("stage", f"goto returned at +{rel()}s url={page.url}")
            try:
                await page.wait_for_selector(
                    ".settings-trigger-button", state="visible", timeout=30_000
                )
                out["editor_ready_s"] = rel()
            except Exception as e:  # noqa: BLE001 - record, never abort
                out["editor_ready_error"] = str(e)[:160]
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:  # noqa: BLE001 - settle is best-effort
                pass
            step("stage", f"settled at +{rel()}s; reading DOM")
            out["landing"] = await page.evaluate(_DOM_JS)
            out["landing_url"] = page.url
            ld = out["landing"]
            step(
                "landing",
                f"{ld['host']}{ld['path']} mat-icon={ld['mat_icon']} "
                f"trigger={ld['settings_trigger']} textarea={ld['textarea']}",
            )
            shot = default_out_path(f"migrated_submit_{profile}_landing", ".png")
            await page.screenshot(path=str(shot))
            out["screenshot_landing"] = shot.name

            if probe_models and ld["settings_trigger"]:
                await page.locator(".settings-trigger-button").first.click(timeout=5000)
                pane = page.locator(_OVERLAY).last
                await pane.locator("[role='radiogroup']").first.wait_for(
                    state="visible", timeout=8000
                )
                btn = pane.locator("button:has(mat-icon:text-is('arrow_drop_down'))").first
                out["model_button_text"] = (await btn.text_content() or "").strip()[:60]
                await btn.click(timeout=4000)
                await asyncio.sleep(1.2)
                out["model_options"] = await page.evaluate(
                    "() => [...document.querySelectorAll('.cdk-overlay-pane')].map(p => ({"
                    " roles: [...p.querySelectorAll('[role]')].map(e => e.getAttribute('role'))"
                    ".reduce((m, r) => (m[r] = (m[r] || 0) + 1, m), {}),"
                    " options: [...p.querySelectorAll(\"[role='option'], [role='menuitem'], "
                    "[role='radio'], mat-option, button\")].map(e => ({tag: e.tagName.toLowerCase(),"
                    " role: e.getAttribute('role'), text: (e.textContent || '').replace(/\s+/g, ' ')"
                    ".trim().slice(0, 60), selected: e.getAttribute('aria-selected') ||"
                    " e.getAttribute('aria-checked')})).slice(0, 40)}))"
                )
                step("models", json.dumps(out["model_options"])[:600])
                shotm = default_out_path(f"migrated_submit_{profile}_models", ".png")
                await page.screenshot(path=str(shotm))
                out["screenshot_models"] = shotm.name
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.4)
                await page.keyboard.press("Escape")
            if not submit or not ld["settings_trigger"]:
                out["stopped"] = "probe only" if not submit else "no settings trigger on this host"
                return 0

            # --- settings: video / 16:9 / 8s / x1 via the cdk overlay radios ------
            radios: list[dict[str, Any]] = []
            await page.locator(".settings-trigger-button").first.click(timeout=5000)
            pane = page.locator(_OVERLAY).last
            await pane.locator("[role='radiogroup']").first.wait_for(state="visible", timeout=8000)
            out["radiogroups"] = await pane.locator("[role='radiogroup']").evaluate_all(_GROUPS_JS)
            step("groups", json.dumps([[r["text"] for r in g] for g in out["radiogroups"]])[:220])
            radio = pane.locator(_RADIO)
            lig = page.locator("mat-icon")
            await _select_radio(
                radio.filter(has=lig.filter(has_text=_exact("videocam"))), "mode=video", radios
            )
            await _select_radio(
                radio.filter(has=lig.filter(has_text=_exact("crop_16_9"))), "aspect=16:9", radios
            )
            await _select_radio(radio.filter(has_text=_exact("8s")), "duration=8s", radios)
            await _select_radio(radio.filter(has_text=_exact("x1")), "count=x1", radios)
            model_btn = pane.locator("button:has(mat-icon:text-is('arrow_drop_down'))").first
            model_text = (await model_btn.text_content() or "") if await model_btn.count() else ""
            out["model_button_text"] = model_text.strip()[:60] or None
            out["radios"] = radios
            out["panel_closed_by"] = await _close_panel(page)
            step("panel", f"closed by {out['panel_closed_by']}")

            # --- prompt + submit ---------------------------------------------------
            ta = page.locator("textarea").first
            try:
                await ta.click(timeout=4000)
                await ta.fill(prompt)
                out["composer"] = "textarea"
            except Exception as e:  # noqa: BLE001 - fall back to the contenteditable
                out["composer_textarea_error"] = str(e)[:120]
                ce = page.locator("[contenteditable='true']").first
                await ce.click(timeout=4000)
                await page.keyboard.type(prompt, delay=5)
                out["composer"] = "contenteditable"
            step("composer", f"typed into {out['composer']}")
            await asyncio.sleep(0.8)
            cands: list[dict[str, Any]] = []
            for css in (
                "button:has(mat-icon:text-is('arrow_forward'))",
                "button:has(mat-icon:text-is('send'))",
                "button:has(mat-icon:text-is('arrow_upward'))",
                "button:has(mat-icon:text-is('auto_awesome'))",
            ):
                loc = page.locator(css)
                k = await loc.count()
                if k:
                    vis = await loc.first.is_visible()
                    en = await loc.first.is_enabled()
                    cands.append({"css": css, "count": k, "visible": vis, "enabled": en})
            out["submit_candidates"] = cands
            step("submit", json.dumps(cands)[:200])
            target = next((c for c in cands if c["visible"] and c["enabled"]), None)
            if target is None:
                out["stopped"] = "no visible+enabled submit control found; nothing clicked"
                shot2 = default_out_path(f"migrated_submit_{profile}_typed", ".png")
                await page.screenshot(path=str(shot2))
                out["screenshot_typed"] = shot2.name
                return 0
            t_click = time.monotonic()
            out["click"] = {"css": target["css"], "t": rel()}
            await page.locator(target["css"]).first.click(timeout=5000)
            try:
                await asyncio.wait_for(seen.wait(), timeout=25)
            except TimeoutError:
                out["submit_request_seen"] = False
            else:
                out["submit_request_seen"] = True
            await asyncio.sleep(2.0)
            if spend:
                # Watch the result land: RPC traffic + DOM until a <video> appears.
                watch: list[dict[str, Any]] = []
                t_watch = time.monotonic()
                while time.monotonic() - t_watch < 300:
                    dom = await page.evaluate(
                        "() => ({video: document.querySelectorAll('video').length,"
                        " progress: document.querySelectorAll(\"[role='progressbar'], mat-progress-bar, mat-progress-spinner\").length,"
                        " marker_tiles: [...document.querySelectorAll('[aria-label]')]"
                        ".filter(e => (e.getAttribute('aria-label')||'').includes('gflowcanary')).length})"
                    )
                    rp = [r["rpcids"] for r in responses if r["t"] > tc_watch_from(t_click, t0)]
                    watch.append({"t": rel(), **dom, "rpc_since_click": len(rp)})
                    step("watch", json.dumps(watch[-1]))
                    done = [r for r in responses if r["rpcids"] == "as29s" and r["marker"]]
                    if done:
                        step("done", f"as29s carried the marker at +{done[0]['t']}s")
                        await asyncio.sleep(3)
                        break
                    await asyncio.sleep(5)
                out["watch"] = watch
                out["responses"] = responses[-120:]
                out["media_hosts"] = sorted(media_hosts)
                out["rpc_timeline_since_click"] = [
                    (r["t"], r["rpcids"], r["status"], r["len"], r["marker"], r["mp4"])
                    for r in responses
                    if r["t"] >= (out["click"]["t"] - 0.5)
                ][:80]
            shot3 = default_out_path(f"migrated_submit_{profile}_after", ".png")
            await page.screenshot(path=str(shot3))
            out["screenshot_after"] = shot3.name
            out["post_click_dom"] = await page.evaluate(_DOM_JS)
            return 0
        finally:
            out["responses"] = responses[-120:]
            out["captured"] = captured
            out["net"] = net[-80:]
            out["net_total"] = len(net)
            await page.close()
            path = default_out_path(f"migrated_submit_capture_{profile}")
            path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
            step("out", str(path))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--no-submit", action="store_true", help="landing probe only; never type/click")
    ap.add_argument("--via-labs", action="store_true", help="bootstrap via labs.google, not direct")
    ap.add_argument("--spend", action="store_true", help="REAL generation: do not abort (bills)")
    ap.add_argument("--probe-models", action="store_true", help="open the model picker, dump it")
    a = ap.parse_args()
    raise SystemExit(
        asyncio.run(
            _main(
                a.profile,
                a.project,
                submit=not a.no_submit,
                direct=not a.via_labs,
                spend=a.spend,
                probe_models=a.probe_models,
            )
        )
    )
