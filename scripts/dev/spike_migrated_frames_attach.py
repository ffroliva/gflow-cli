r"""How does the migrated flow.google.com editor take a start frame? ($0 recon)

The i2v predict (2026-09-05, CAUTION 7/10) named one blocker every persona shares: nothing
about getting an image INTO a migrated project has ever been observed. This spike answers
it without spending a credit, in one browser session, best-effort at every stage (a stage
that fails records the failure and the next one still runs):

  A. landing: the composer inventory and any dialog (post-handoff changelog modal)
  B. settings pane: the submode radios (Frames = `crop_free`, Ingredients =
     `chrome_extension`) and what the pane renders once Frames is checked
  C. composer with Frames active: measured 2026-09-05 — text chips "Start" / "End"
     (`button.empty-chip`) around a `swap_horiz` icon; no `input[type=file]` anywhere
  D. click the Start chip: it opens the "Select a frame image" picker (project dropdown,
     "Search assets" box, "Recent" sort, tile grid; library-only, no upload entry).
     Dump the picker DOM, optionally `--search <text>`, click the first tile, and record
     whether the chip now holds a thumbnail
  E. `--submit-abort`: with the frame bound, type a marker prompt and click submit; the
     marker request is ABORTED before it leaves the browser — captures the i2v model key
     (an unbound Frames submit was measured to carry the t2v key `veo_3_1_t2v_lite`)
  F. `--image-mode`: after a reload, switch the mode radio to image and dump the pane +
     model menu (t2i keys ride along for free)
  G. `--plus-menu` + `--probe-image`: the toolbar `+` (the only add affordance outside the
     composer): dump its menu, click its upload entry under `expect_file_chooser`, feed the
     image, and watch the wire for the upload RPC and the grid for the new tile; then
     reopen the picker and dump the tiles again (does the new asset expose its UUID?)

    python scripts/dev/spike_migrated_frames_attach.py --profile ffroliva --project <id> \
        --search cube --submit-abort --image-mode --plus-menu --probe-image tmp/some.png
"""

# ruff: noqa: E501

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
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    OVERLAY,
    RADIOGROUP,
    MigratedComposer,
)

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)
from spike_migrated_submit_capture import (  # noqa: E402, isort: skip
    _redact,
    _redact_body,
    _url_facts,
)

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

_COMPOSER_JS = r"""() => {
  const lig = (e) => (e.textContent || '').trim();
  const isLig = (t) => /^[a-z0-9_]{2,40}$/.test(t);
  const region = document.querySelector('flow-prompt-box') || document.body;
  const tags = [...region.querySelectorAll('*')].map(e => e.tagName.toLowerCase()).filter(t => t.includes('-'));
  const tally = (xs) => { const m = {}; for (const x of xs) m[x] = (m[x]||0)+1; return m; };
  const btns = [...region.querySelectorAll('button, [role=button]')].map(b => ({
    disabled: !!b.disabled, ligs: [...b.querySelectorAll('mat-icon, .google-symbols')].map(lig).filter(isLig),
    text: (b.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40), cls: (b.className || '').toString().slice(0, 80),
    imgs: b.querySelectorAll('img').length, bg: !!(getComputedStyle(b).backgroundImage || '').includes('url(')}));
  const files = [...document.querySelectorAll('input[type=file]')].map(i => ({accept: i.accept, multiple: i.multiple,
    hidden: !(i.offsetWidth || i.offsetHeight), id: i.id, name: i.name}));
  const imgs = [...region.querySelectorAll('img, video, canvas')].map(i => ({tag: i.tagName.toLowerCase(),
    src_host: (() => { try { return new URL(i.src).hostname } catch { return '' } })(),
    src_uuid: /[0-9a-f]{8}-[0-9a-f]{4}-/i.test(i.src || ''), alt: (i.alt || '').slice(0, 40), w: i.width, h: i.height}));
  const dialogs = [...document.querySelectorAll('[role=dialog], mat-dialog-container')].map(d => ({
    tag: d.tagName.toLowerCase(), cls: (d.className||'').toString().slice(0,80),
    text: (d.innerText||'').replace(/\s+/g,' ').trim().slice(0,160),
    btns: [...d.querySelectorAll('button')].map(b => (b.innerText||'').trim().slice(0,30))}));
  return {region: region.tagName.toLowerCase(), custom_tags: tally(tags), buttons: btns, file_inputs: files,
          media: imgs, dialogs, overlays: document.querySelectorAll('.cdk-overlay-pane').length};
}"""

# Everything inside the visible overlay panes: the frame picker lives there.
_PICKER_JS = r"""() => [...document.querySelectorAll('.cdk-overlay-pane')].filter(p => p.offsetWidth || p.offsetHeight).map(p => {
  const tally = (xs) => { const m = {}; for (const x of xs) m[x] = (m[x]||0)+1; return m; };
  const uuidAttrs = (e) => [...e.attributes].filter(a => /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}/i.test(a.value)).map(a => a.name + '=' + a.value.slice(0, 60));
  const tiles = [...p.querySelectorAll('flow-image-tile, flow-video-tile, [class*=tile], [role=option], [role=listitem], [role=gridcell]')].slice(0, 20).map(t => ({
    tag: t.tagName.toLowerCase(), role: t.getAttribute('role'), cls: (t.className||'').toString().slice(0, 60),
    uuid_attrs: uuidAttrs(t), inner_uuid_attrs: [...t.querySelectorAll('*')].flatMap(uuidAttrs).slice(0, 4),
    img_host: (() => { const i = t.querySelector('img'); try { return i ? new URL(i.src).hostname : '' } catch { return '' } })(),
    img_src_uuid: !!(t.querySelector('img') && /[0-9a-f]{8}-[0-9a-f]{4}-/i.test(t.querySelector('img').src)),
    alt: (t.querySelector('img')?.alt || '').slice(0, 60), aria: (t.getAttribute('aria-label') || '').slice(0, 60),
    text: (t.innerText || '').replace(/\s+/g,' ').trim().slice(0, 40)}));
  return {custom_tags: tally([...p.querySelectorAll('*')].map(e => e.tagName.toLowerCase()).filter(t => t.includes('-'))),
    inputs: [...p.querySelectorAll('input, textarea')].map(i => ({type: i.type, placeholder: i.placeholder, aria: i.getAttribute('aria-label')})),
    combos: [...p.querySelectorAll('[role=combobox], mat-select, select')].map(c => (c.innerText||'').replace(/\s+/g,' ').trim().slice(0,40)),
    buttons: [...p.querySelectorAll('button')].map(b => ({ligs: [...b.querySelectorAll('mat-icon')].map(i => (i.textContent||'').trim()), text: (b.innerText||'').replace(/\s+/g,' ').trim().slice(0,30)})).slice(0, 20),
    imgs: p.querySelectorAll('img').length, tiles, text: (p.innerText||'').replace(/\s+/g,' ').trim().slice(0, 200)};
})"""

_OVERLAY_ITEMS_JS = r"""() => [...document.querySelectorAll('.cdk-overlay-pane')].map(p => ({
  vis: !!(p.offsetWidth || p.offsetHeight),
  items: [...p.querySelectorAll('button, [role=menuitem], [role=option], [role=radio], a')].map(e => ({
    tag: e.tagName.toLowerCase(), role: e.getAttribute('role'),
    ligs: [...e.querySelectorAll('mat-icon, .google-symbols')].map(i => (i.textContent||'').trim()),
    text: (e.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 50)})).slice(0, 40)}))"""

_GRID_JS = r"""() => {
  const tiles = [...document.querySelectorAll('flow-image-tile, flow-video-tile')];
  return {count: tiles.length, first: tiles.slice(0, 5).map(t => ({tag: t.tagName.toLowerCase(),
    uuid_attrs: [...t.querySelectorAll('*'), t].flatMap(e => [...e.attributes].filter(a => /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}/i.test(a.value)).map(a => a.name)).slice(0, 4),
    img_host: (() => { const i = t.querySelector('img'); try { return i ? new URL(i.src).hostname : '' } catch { return '' } })(),
    alt: (t.querySelector('img')?.alt || '').slice(0, 60)}))};
}"""

_GROUPS_JS = r"""(gs) => gs.map(g => [...g.querySelectorAll('[role=radio]')].map(r => ({
  text: (r.innerText||'').replace(/\s+/g,' ').trim().slice(0,30),
  ligs: [...r.querySelectorAll('mat-icon')].map(i => (i.textContent||'').trim()),
  checked: r.getAttribute('aria-checked')})))"""


def _post_len(req: Any) -> int:
    if req.method != "POST":
        return 0
    try:
        return len(req.post_data_buffer or b"")
    except Exception:  # noqa: BLE001 - a body Playwright cannot expose
        return -1


def _lig(page: Any, name: str) -> Any:
    return page.locator("mat-icon, .google-symbols").filter(has_text=re.compile(rf"^\s*{name}\s*$"))


async def _main(  # noqa: PLR0912, PLR0915 - a recon script is a linear log of stages
    profile: str,
    project: str,
    *,
    probe_image: Path | None,
    search: str | None,
    submit_abort: bool,
    image_mode: bool,
    plus_menu: bool,
) -> int:
    profile_dir = resolve_profile_dir(profile)
    step("profile", f"{profile} -> {profile_dir}")
    marker = "gflowcanary" + uuid.uuid4().hex[:10]
    out: dict[str, Any] = {
        "profile": profile,
        "project": project[:8] + "...",
        "marker": marker,
        "probe_image": probe_image.name if probe_image else None,
        "search": search,
    }
    net: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    captured: list[dict[str, Any]] = []
    chooser_events: list[dict[str, Any]] = []
    seen = asyncio.Event()
    t0 = time.monotonic()

    def rel() -> float:
        return round(time.monotonic() - t0, 2)

    async with build_client(profile_dir) as client:
        context = client._context  # noqa: SLF001 - spike reads the live context
        assert context is not None
        page = await context.new_page()

        async def on_route(route: Any) -> None:
            req = route.request
            try:
                body = req.post_data or ""
            except UnicodeDecodeError:  # binary multipart body — never the marker
                body = ""
            if marker in body:
                captured.append(
                    {
                        "t": rel(),
                        "method": req.method,
                        "url": _url_facts(req.url),
                        "body": _redact(body),
                    }
                )
                step("ABORT", f"marker request rpcids={_url_facts(req.url).get('rpcids')} aborted")
                seen.set()
                await route.abort()
                return
            await route.continue_()

        def on_request(req: Any) -> None:
            host = urlsplit(req.url).hostname or ""
            if "google" in host or "gstatic" in host:
                net.append(
                    {
                        "t": rel(),
                        "m": req.method,
                        "host": host,
                        "path": urlsplit(req.url).path[:90],
                        **_url_facts(req.url),
                        "ct": req.headers.get("content-type", "")[:40],
                        "post_len": _post_len(req),
                    }
                )

        async def on_response(resp: Any) -> None:
            host = urlsplit(resp.url).hostname or ""
            if not ("google" in host or "gstatic" in host):
                return
            if resp.request.method != "POST" and "batchexecute" not in resp.url:
                return
            try:
                text = await resp.text()
            except Exception:  # noqa: BLE001 - aborted / binary bodies
                return
            hit: dict[str, Any] = {
                "t": rel(),
                "status": resp.status,
                "host": host,
                "path": urlsplit(resp.url).path[-40:],
                "rpcids": _url_facts(resp.url).get("rpcids"),
                "len": len(text),
                "uuids": sorted(set(m.lower() for m in _UUID.findall(text)))[:6],
                "hosts": sorted(
                    {h for h in re.findall(r"https?://([a-z0-9.\-]+)", text) if "google" in h}
                )[:5],
            }
            if hit["rpcids"] not in ("jwpduf",):
                hit["head"] = _redact_body(text)[:300]
            responses.append(hit)

        await page.route("**/batchexecute**", on_route)
        page.on("request", on_request)
        page.on("response", on_response)
        page.on(
            "filechooser",
            lambda fc: chooser_events.append({"t": rel(), "multiple": fc.is_multiple()}),
        )

        composer = MigratedComposer()
        shots: list[str] = []

        async def shot(name: str) -> None:
            p = default_out_path(f"migrated_frames_{profile}_{name}", ".png")
            await page.screenshot(path=str(p))
            shots.append(p.name)

        def responses_since(t: float) -> list[dict[str, Any]]:
            return [r for r in responses if r["t"] >= t - 0.1][:30]

        try:
            # ---- A. landing -------------------------------------------------------------
            await composer.ensure_editor(page, project)
            await asyncio.sleep(2.0)
            out["landing"] = await page.evaluate(_COMPOSER_JS)
            out["grid_landing"] = await page.evaluate(_GRID_JS)
            step(
                "landing",
                f"dialogs={len(out['landing']['dialogs'])} grid_tiles={out['grid_landing']['count']}",
            )
            await shot("landing")

            # ---- B. Frames submode ------------------------------------------------------
            pane = await composer._open_pane(page)  # noqa: SLF001
            out["groups_default"] = await pane.locator(RADIOGROUP).evaluate_all(_GROUPS_JS)
            await composer._select(page, pane, axis="mode", lig="videocam")  # noqa: SLF001
            try:
                await composer._select(page, pane, axis="submode", lig="crop_free")  # noqa: SLF001
                out["frames_selected"] = True
            except Exception as e:  # noqa: BLE001
                out["frames_selected"] = str(e)[:200]
            await asyncio.sleep(0.6)
            out["groups_frames"] = await pane.locator(RADIOGROUP).evaluate_all(_GROUPS_JS)
            await composer._close_pane(page)  # noqa: SLF001
            await asyncio.sleep(0.8)

            # ---- C. composer with Frames ------------------------------------------------
            out["composer_frames"] = await page.evaluate(_COMPOSER_JS)
            region = page.locator("flow-prompt-box").first
            chips = region.locator("button.empty-chip")
            out["chips"] = await chips.all_inner_texts()
            step("chips", str(out["chips"]))

            # ---- D. the picker ----------------------------------------------------------
            bound = False
            if await chips.count():
                t_open = rel()
                await chips.first.click(timeout=4000)
                await asyncio.sleep(2.0)
                out["picker"] = await page.evaluate(_PICKER_JS)
                out["picker_open_rpcs"] = [
                    (r["t"], r["rpcids"], r["status"], r["len"], len(r["uuids"]))
                    for r in responses_since(t_open)
                ]
                pk = out["picker"][0] if out["picker"] else {}
                step(
                    "picker",
                    f"inputs={pk.get('inputs')} combos={pk.get('combos')} tiles={len(pk.get('tiles', []))} imgs={pk.get('imgs')}",
                )
                await shot("picker")
                if search and pk.get("inputs"):
                    box = page.locator(OVERLAY).locator("input").first
                    await box.click(timeout=3000)
                    t_s = rel()
                    await page.keyboard.type(search, delay=30)
                    await asyncio.sleep(2.5)
                    out["picker_after_search"] = await page.evaluate(_PICKER_JS)
                    out["search_rpcs"] = [
                        (r["t"], r["rpcids"], r["status"], r["len"], len(r["uuids"]))
                        for r in responses_since(t_s)
                    ]
                    pk = out["picker_after_search"][0] if out["picker_after_search"] else {}
                    step(
                        "search",
                        f"tiles after search={len(pk.get('tiles', []))} rpcs={out['search_rpcs'][:4]}",
                    )
                    await shot("picker_search")
                tiles = page.locator(OVERLAY).locator(
                    "flow-image-tile, [class*='tile'], [role='option'], [role='gridcell']"
                )
                clickable = tiles.filter(has=page.locator("img"))
                n_tiles = await clickable.count()
                out["picker_tiles_with_img"] = n_tiles
                if n_tiles:
                    t_c = rel()
                    await clickable.first.click(timeout=4000)
                    await asyncio.sleep(2.0)
                    out["tile_click_rpcs"] = [
                        (r["t"], r["rpcids"], r["status"], r["len"], len(r["uuids"]))
                        for r in responses_since(t_c)
                    ]
                    out["composer_after_bind"] = await page.evaluate(_COMPOSER_JS)
                    cb = out["composer_after_bind"]
                    bound = any(b["imgs"] or b["bg"] for b in cb["buttons"]) or any(
                        m["tag"] == "img" and m["w"] for m in cb["media"]
                    )
                    out["frame_bound"] = bound
                    out["chips_after_bind"] = await region.locator(
                        "button.empty-chip"
                    ).all_inner_texts()
                    step(
                        "bind",
                        f"bound={bound} chips_left={out['chips_after_bind']} media={cb['media'][:2]}",
                    )
                    await shot("after_bind")
                else:
                    out["frame_bound"] = False
                    step("bind", "no tile with an image in the picker — nothing bound")
                if await page.locator(OVERLAY).filter(has=page.locator("input")).count():
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.5)

            # ---- E. aborted submit with the frame bound -----------------------------------
            if submit_abort:
                await composer.send_prompt(page, f"a teal origami crane on a wooden table {marker}")
                submit = page.locator("button").filter(has=_lig(page, "arrow_forward")).first
                for _ in range(30):
                    if await submit.count() and await submit.is_enabled():
                        break
                    await asyncio.sleep(0.1)
                out["submit_enabled"] = bool(await submit.count() and await submit.is_enabled())
                out["submit_with_frame_bound"] = bound
                if out["submit_enabled"]:
                    await submit.click(timeout=5000)
                    try:
                        await asyncio.wait_for(seen.wait(), timeout=25)
                        out["submit_request_seen"] = True
                    except TimeoutError:
                        out["submit_request_seen"] = False
                    await asyncio.sleep(1.5)
                    await shot("after_submit_abort")
                    if captured:
                        head = captured[-1]["body"].get("head", "")
                        keys = re.findall(
                            r"%5C%22([a-z0-9_]+_(?:t2v|i2v|r2v|t2i|i2i)[a-z0-9_]*)%5C%22", head
                        )
                        out["submit_model_keys"] = keys
                        out["submit_body_uuids"] = sorted(
                            set(m.lower() for m in _UUID.findall(head))
                        )
                        step(
                            "key",
                            f"model keys in YhhmEf body: {keys}; uuids={len(out['submit_body_uuids'])}",
                        )

            # ---- F. image mode (after a reload so the composer state is clean) --------------
            if image_mode:
                await page.goto(
                    f"https://flow.google.com/project/{project}",
                    wait_until="domcontentloaded",
                    timeout=45_000,
                )
                await composer.ensure_editor(page, project)
                await asyncio.sleep(1.5)
                pane = await composer._open_pane(page)  # noqa: SLF001
                try:
                    await composer._select(page, pane, axis="mode", lig="image")  # noqa: SLF001
                    await asyncio.sleep(0.6)
                    out["groups_image"] = await pane.locator(RADIOGROUP).evaluate_all(_GROUPS_JS)
                    out["pane_image_text"] = (await pane.inner_text())[:400]
                    btn = pane.locator("button").filter(has=_lig(page, "arrow_drop_down")).first
                    out["image_model_button"] = (
                        (await btn.text_content() or "").strip()[:60] if await btn.count() else None
                    )
                    if await btn.count():
                        await btn.click(timeout=4000)
                        await asyncio.sleep(1.0)
                        out["image_model_menu"] = await page.evaluate(_OVERLAY_ITEMS_JS)
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.3)
                    await shot("pane_image")
                    step(
                        "image",
                        f"button={out['image_model_button']} groups={len(out['groups_image'])}",
                    )
                except Exception as e:  # noqa: BLE001
                    out["image_mode_error"] = str(e)[:200]
                finally:
                    await composer._close_pane(page)  # noqa: SLF001
                    await asyncio.sleep(0.5)
                # image composer: does the ingredient bar / add menu offer an upload?
                out["composer_image"] = await page.evaluate(_COMPOSER_JS)

            # ---- G. the toolbar `+` and the upload path ----------------------------------
            if plus_menu:
                if image_mode:
                    pass  # already reloaded above
                else:
                    await page.goto(
                        f"https://flow.google.com/project/{project}",
                        wait_until="domcontentloaded",
                        timeout=45_000,
                    )
                    await composer.ensure_editor(page, project)
                    await asyncio.sleep(1.5)
                outside = (
                    page.locator("button")
                    .filter(has=_lig(page, "add"))
                    .filter(has_not=page.locator("flow-prompt-box"))
                )
                plus = None
                for i in range(await outside.count()):
                    cand = outside.nth(i)
                    inside_prompt = await cand.evaluate("e => !!e.closest('flow-prompt-box')")
                    if not inside_prompt and await cand.is_visible():
                        plus = cand
                        break
                out["plus_button"] = plus is not None
                chooser = None
                if plus is not None:
                    try:
                        async with page.expect_file_chooser(timeout=4000) as fc_info:
                            await plus.click(timeout=4000)
                        chooser = await fc_info.value
                        out["plus_click"] = "filechooser"
                    except Exception:  # noqa: BLE001 - a menu, dump it
                        await asyncio.sleep(0.8)
                        out["plus_click"] = "menu"
                        out["plus_menu"] = await page.evaluate(_OVERLAY_ITEMS_JS)
                        await shot("plus_menu")
                        step("plus", json.dumps(out["plus_menu"])[:500])
                        items = page.locator(OVERLAY).locator(
                            "button, [role='menuitem'], [role='option']"
                        )
                        up = (
                            items.filter(has=_lig(page, "upload"))
                            .or_(items.filter(has=_lig(page, "file_upload")))
                            .or_(
                                items.filter(
                                    has_text=re.compile(
                                        r"upload|computer|carregar|enviar|subir", re.I
                                    )
                                )
                            )
                        )
                        out["plus_upload_entry"] = await up.count()
                        if await up.count():
                            try:
                                async with page.expect_file_chooser(timeout=4000) as fc_info:
                                    await up.first.click(timeout=4000)
                                chooser = await fc_info.value
                                out["plus_upload_click"] = "filechooser"
                            except Exception as e:  # noqa: BLE001
                                out["plus_upload_click"] = f"no-chooser: {str(e)[:100]}"
                                await shot("after_plus_upload_click")
                if chooser is not None and probe_image is not None:
                    t_up = rel()
                    n_before = len(net)
                    await chooser.set_files(str(probe_image))
                    step("upload", "set_files done; watching 60 s for the upload + a new grid tile")
                    grid_before = out["grid_landing"]["count"]
                    for _ in range(120):
                        g = await page.evaluate(_GRID_JS)
                        if g["count"] > grid_before:
                            out["upload_tile_after_s"] = round(rel() - t_up, 2)
                            out["grid_after_upload"] = g
                            step(
                                "upload",
                                f"new tile after {out['upload_tile_after_s']}s: {g['first'][:1]}",
                            )
                            break
                        await asyncio.sleep(0.5)
                    await asyncio.sleep(2.0)
                    out["upload_requests"] = [n for n in net[n_before:] if n["m"] == "POST"][:30]
                    out["upload_responses"] = responses_since(t_up)
                    await shot("after_upload")
                    # reopen the picker: does the new asset expose a UUID?
                    try:
                        pane = await composer._open_pane(page)  # noqa: SLF001
                        await composer._select(page, pane, axis="mode", lig="videocam")  # noqa: SLF001
                        await composer._select(page, pane, axis="submode", lig="crop_free")  # noqa: SLF001
                        await composer._close_pane(page)  # noqa: SLF001
                        await asyncio.sleep(0.8)
                        chip = page.locator("flow-prompt-box button.empty-chip").first
                        await chip.click(timeout=4000)
                        await asyncio.sleep(2.5)
                        out["picker_after_upload"] = await page.evaluate(_PICKER_JS)
                        await shot("picker_after_upload")
                        await page.keyboard.press("Escape")
                    except Exception as e:  # noqa: BLE001
                        out["picker_after_upload_error"] = str(e)[:200]
                elif chooser is not None:
                    out["plus_chooser"] = "opened; no --probe-image, cancelled"
            return 0
        finally:
            out["chooser_events"] = chooser_events
            out["captured"] = captured
            out["responses_tail"] = responses[-80:]
            out["net_tail"] = net[-100:]
            out["screenshots"] = shots
            await page.close()
            path = default_out_path(f"migrated_frames_attach_{profile}")
            path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
            step("out", str(path))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--probe-image", type=Path, default=None)
    ap.add_argument("--search", default=None, help="type this into the picker's search box")
    ap.add_argument("--submit-abort", action="store_true", help="marker submit, aborted ($0)")
    ap.add_argument("--image-mode", action="store_true", help="dump the image-mode pane + models")
    ap.add_argument("--plus-menu", action="store_true", help="probe the toolbar + / upload path")
    a = ap.parse_args()
    raise SystemExit(
        asyncio.run(
            _main(
                a.profile,
                a.project,
                probe_image=a.probe_image,
                search=a.search,
                submit_abort=a.submit_abort,
                image_mode=a.image_mode,
                plus_menu=a.plus_menu,
            )
        )
    )
