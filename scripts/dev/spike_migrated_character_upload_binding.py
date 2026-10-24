r"""Task 0 of the character-portability plan: what does the editor offer, and what binds?

Costs no credits. ``inventory`` and ``export`` only read. ``bind`` WRITES: it creates a
project (unless ``--project`` is given) and two characters in it, and never deletes
them — run it on a probe profile and remove them in Flow afterwards.

Plan: docs/superpowers/plans/2026-10-16-character-portability/PLAN.md, Task 0.
Prior evidence: docs/superpowers/spikes/2026-10-16-migrated-character-lifecycle-wire.md.

Mode ``inventory`` (this file's first question, read-only): open an existing
character's editor on flow.google.com and record its STRUCTURE — every clickable
control with its tag, custom-element ancestors, ligature(s), role, aria-haspopup and
disabled state, plus every input/textarea/file input. Display text is recorded only
as a length (never as an anchor), because the account under test renders ``fr``.

Pre-registered reading:

* a control is a write anchor only if it is identified by tag / custom element /
  ligature / role — never by its label;
* a file input (``input[type=file]``) present in the editor means the Upload control
  can be driven with ``set_input_files`` / ``expect_file_chooser``;
* a control absent from the inventory is **unmeasured**, not absent — the editor may
  mount it lazily (menus, dialogs); settle by opening the parent and re-inventorying.

    uv run python scripts/dev/spike_migrated_character_upload_binding.py inventory \
        --profile sykamel --project <id> --entity <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

_ROOT = "https://flow.google.com"

_INVENTORY_JS = r"""() => {
  const ancestors = el => {
    const out = [];
    for (let p = el.parentElement; p && out.length < 6; p = p.parentElement) {
      const t = p.tagName.toLowerCase();
      if (t.includes('-')) out.push(t);
    }
    return out;
  };
  const ligs = el => [...el.querySelectorAll('mat-icon, .google-symbols')]
    .map(i => (i.textContent || '').trim()).filter(t => /^[a-z0-9_]+$/.test(t));
  const cls = el => (typeof el.className === 'string' ? el.className : '')
    .split(/\s+/).filter(c => c && !c.startsWith('ng-') && !c.startsWith('_ngcontent'))
    .slice(0, 6).join(' ');
  const vis = el => { const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0; };
  const controls = [...document.querySelectorAll(
      'button, [role="button"], [role="menuitem"], [role="option"], [role="radio"], a[href]')]
    .map(el => ({
      tag: el.tagName.toLowerCase(), role: el.getAttribute('role'), cls: cls(el),
      ligatures: ligs(el), ancestors: ancestors(el),
      haspopup: el.getAttribute('aria-haspopup'),
      disabled: el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true',
      visible: vis(el), text_len: (el.innerText || '').trim().length,
    }));
  const fields = [...document.querySelectorAll(
      'input, textarea, [contenteditable="true"]')]
    .map(el => ({
      tag: el.tagName.toLowerCase(), type: el.getAttribute('type'), cls: cls(el),
      accept: el.getAttribute('accept'), multiple: el.hasAttribute('multiple'),
      ancestors: ancestors(el), visible: vis(el),
      value_len: (el.value || el.innerText || '').length,
    }));
  const imgs = [...document.querySelectorAll('img')].map(i => ({
    host: (() => { try { return new URL(i.src).host; } catch (e) { return null; } })(),
    ancestors: ancestors(i), visible: vis(i)}));
  return {url: location.href, controls, fields, imgs};
}"""


#: What a click mounted: overlay panes (menus, dialogs) and their structural items.
_OVERLAY_JS = r"""() => {
  const ligs = el => [...el.querySelectorAll('mat-icon, .google-symbols')]
    .map(i => (i.textContent || '').trim()).filter(t => /^[a-z0-9_]+$/.test(t));
  const panes = [...document.querySelectorAll(
      '.cdk-overlay-pane, [role="menu"], [role="dialog"], [role="listbox"], mat-dialog-container')];
  return panes.map(p => ({
    tag: p.tagName.toLowerCase(), role: p.getAttribute('role'),
    custom: [...new Set([...p.querySelectorAll('*')].map(e => e.tagName.toLowerCase())
      .filter(t => t.includes('-') && !t.startsWith('mat-') && !t.startsWith('cdk-')))],
    items: [...p.querySelectorAll(
      'button, [role="menuitem"], [role="option"], [role="radio"], input')]
      .map(el => ({tag: el.tagName.toLowerCase(), role: el.getAttribute('role'),
                   type: el.getAttribute('type'), accept: el.getAttribute('accept'),
                   cls: (typeof el.className === 'string' ? el.className : '').slice(0, 60),
                   ligatures: ligs(el), text_len: (el.innerText || '').trim().length})),
  }));
}"""


async def _probe(profile: str, url: str, clicks: list[str], *, chain: bool = False) -> int:
    """Open ``url``, click each selector in turn, and record what each click mounted.

    Clicks only open menus/pickers; nothing here commits a write. Escape closes each
    overlay before the next click so the probes do not stack."""
    profile_dir = resolve_profile_dir(profile)
    found: dict[str, Any] = {"url": url, "clicks": []}
    async with build_client(profile_dir) as client:
        page = await client._context.new_page()  # noqa: SLF001 - spike reads the live context
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(6000)
        found["landed"] = page.url
        found["base"] = await page.evaluate(_INVENTORY_JS)
        for sel in clicks:
            loc = page.locator(sel)
            n = await loc.count()
            rec: dict[str, Any] = {"selector": sel, "matches": n}
            if n:
                try:
                    async with page.expect_file_chooser(timeout=4000) as fc:
                        await loc.first.click(timeout=10_000)
                    chooser = await fc.value
                    rec["file_chooser"] = {"multiple": chooser.is_multiple()}
                    # Never set files here: a chooser left unanswered commits nothing.
                except Exception as exc:  # noqa: BLE001 - no chooser is the common case
                    rec["file_chooser"] = None
                    rec["click_note"] = type(exc).__name__
                await page.wait_for_timeout(1500)
                rec["overlays"] = await page.evaluate(_OVERLAY_JS)
                rec["after"] = await page.evaluate(_INVENTORY_JS)
                rec["after_url"] = page.url
                if not chain:
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(800)
            found["clicks"].append(rec)
            step(
                "click",
                f"{sel} -> matches={n} overlays={len(rec.get('overlays', []))} "
                f"chooser={rec.get('file_chooser')}",
            )
        out = default_out_path("character_editor_probe")
        out.write_text(json.dumps(found, indent=2, ensure_ascii=False), encoding="utf-8")
        step("wrote", str(out))
    return 0


async def _inventory(profile: str, project: str, entity: str) -> int:
    profile_dir = resolve_profile_dir(profile)
    url = f"{_ROOT}/project/{project}/character/{entity}"
    async with build_client(profile_dir) as client:
        page = await client._context.new_page()  # noqa: SLF001 - spike reads the live context
        step("goto", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        try:
            await page.wait_for_selector(
                "input, textarea, div.ProseMirror", state="visible", timeout=30_000
            )
        except Exception as exc:  # noqa: BLE001 - absence is recorded, not concluded
            step("wait", f"no editor field visible in 30 s: {type(exc).__name__}")
        await page.wait_for_timeout(3000)
        inv = await page.evaluate(_INVENTORY_JS)
        shot = default_out_path("character_editor_inventory", ".png")
        await page.screenshot(path=str(shot), full_page=True)
        out = default_out_path("character_editor_inventory")
        out.write_text(json.dumps(inv, indent=2, ensure_ascii=False), encoding="utf-8")
        step("landed", inv["url"])
        step("counts", f"controls={len(inv['controls'])} fields={len(inv['fields'])}")
        step("wrote", f"{out} + {shot.name}")
    return 0


# ---------------------------------------------------------------------------
# Modes `export` and `bind` — the binding experiment (Task 0 steps 2-4).
#
# Pre-registered reading for `bind` (written before the first run):
#
# * C4BZMd fires on the header add-menu's `account_circle` item -> that item is
#   "new character" (else: unmeasured; the item is something else).
# * each editor Upload sends maseQ with a trailing `[null,null,[<entity>,[N]]]`:
#   N equal for portrait and body -> N is a kind flag, not a slot; N differs -> slot.
# * after all uploads, the entity's image refs on a FRESH Zzl0ze read list every
#   upload the server answered -> upload binds by itself, no follow-up rpc needed.
# * the third upload has its reply dropped AFTER the server got the request:
#   bound on re-read -> "lost reply, still bound" is real, so a blind retry would
#   double-bind; not bound -> a retry is safe. Either result is a finding.
# ---------------------------------------------------------------------------

from gflow_cli.api.transports.batchexecute import parse_frames  # noqa: E402


class _Wire:
    """Every batchexecute request body and reply frame on one page, by rpcid."""

    def __init__(self, page: Any) -> None:
        self.reqs: dict[str, list[str]] = {}
        self.replies: dict[str, list[Any]] = {}
        page.on("request", self._on_request)
        page.on("response", lambda r: asyncio.create_task(self._on_response(r)))

    @staticmethod
    def _rpc(url: str) -> str | None:
        if "batchexecute" not in url or "rpcids=" not in url:
            return None
        return url.split("rpcids=", 1)[1].split("&", 1)[0]

    def _on_request(self, req: Any) -> None:
        rid = self._rpc(req.url)
        if rid:
            from urllib.parse import parse_qs

            body = parse_qs(req.post_data or "").get("f.req", [""])[0]
            # Keep the head and the tail: an upload hides its binding after the base64.
            self.reqs.setdefault(rid, []).append(body[:400] + " … " + body[-600:])

    async def _on_response(self, resp: Any) -> None:
        if not self._rpc(resp.url):
            return
        try:
            text = await resp.text()
        except Exception:  # noqa: BLE001 - a dropped reply is part of the experiment
            return
        for rid, payload in parse_frames(text):
            self.replies.setdefault(rid, []).append(payload)

    async def next_reply(self, rpcid: str, before: int, timeout_s: float = 60) -> Any:
        for _ in range(int(timeout_s * 4)):
            got = self.replies.get(rpcid, [])
            if len(got) > before:
                return got[before]
            await asyncio.sleep(0.25)
        return None

    def count(self, rpcid: str) -> int:
        return len(self.replies.get(rpcid, []))


async def _read_project(page: Any, wire: _Wire, project: str) -> list[Any]:
    before = wire.count("Zzl0ze")
    await page.goto(f"{_ROOT}/project/{project}", wait_until="domcontentloaded", timeout=60_000)
    payload = await wire.next_reply("Zzl0ze", before, 45)
    if payload is None:
        raise RuntimeError("no Zzl0ze reply on project load")
    return payload


def _entity_row(project_read: list[Any], entity: str) -> Any:
    for row in project_read[5] or []:
        if row[1] == entity:
            return row
    return None


async def _export(profile: str, project: str, entity: str, out_dir: Path) -> int:
    profile_dir = resolve_profile_dir(profile)
    out_dir.mkdir(parents=True, exist_ok=True)
    async with build_client(profile_dir) as client:
        page = await client._context.new_page()  # noqa: SLF001 - spike reads the live context
        wire = _Wire(page)
        read = await _read_project(page, wire, project)
        row = _entity_row(read, entity)
        if row is None:
            step("export", "entity not in Zzl0ze slot 5")
            return 1
        info = row[3]
        refs = [r[0] for r in (info[2][0] if len(info) > 2 and info[2] else [])]
        voice = info[2][1][0][1] if len(info[2]) > 1 and info[2][1] else None
        personality = info[2][2] if len(info[2]) > 2 else None
        media_by_wf = {m[2]: m for m in (read[2] or [])}
        step(
            "export", f"name={info[1]!r} voice={voice} personality={personality!r} refs={len(refs)}"
        )
        for i, wf in enumerate(refs):
            media = media_by_wf.get(wf)
            url = None
            if media:
                urls = [u for u in json.dumps(media).split('"') if u.startswith("https://")]
                url = urls[0] if urls else None
            if not url:
                step("export", f"ref {i}: no download URL for workflow {wf[:8]}")
                continue
            base = url.encode().decode("unicode_escape")
            # FIFE serves a downscaled rendition unless asked otherwise: measure which
            # suffix returns the original (Flow reports 720x1280 for these uploads).
            for tag, suffix in (("plain", ""), ("s0", "=s0"), ("d", "=d")):
                resp = await page.request.get(base + suffix)
                dest = out_dir / f"ref{i}_{tag}.img"
                dest.write_bytes(await resp.body())
                step("export", f"ref {i} [{tag}]: HTTP {resp.status} {dest.stat().st_size} B")
    return 0


async def _upload(
    page: Any, wire: _Wire, path: Path, *, drop_reply: bool = False, footer: bool = False
) -> Any:
    before = wire.count("maseQ")
    sent_before = len(wire.reqs.get("maseQ", []))
    route = "**/batchexecute?rpcids=maseQ*"

    async def _drop(route_obj: Any) -> None:
        await route_obj.fetch()  # the server receives and processes the upload…
        await route_obj.abort()  # …and the page never sees the answer

    if drop_reply:
        await page.route(route, _drop)
    # A previous upload's popover can stay open over the add button.
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(600)
    # Measured: the prompt box's add-menu Upload sends NO entity binding (plain project
    # media). The binding gestures are the draft footer's Upload (slot 0, creates the
    # entity) and the selected slot's empty-state Upload in the preview panel ([N]=slot).
    target = (
        "flow-create-character-footer button:has(mat-icon:text-is('upload'))"
        if footer
        else "flow-character-image-preview button:has(mat-icon:text-is('upload'))"
    )
    async with page.expect_file_chooser(timeout=10_000) as fc:
        await page.locator(target).first.click()
    await (await fc.value).set_files(str(path))
    if drop_reply:
        for _ in range(120):
            if len(wire.reqs.get("maseQ", [])) > sent_before:
                break
            await asyncio.sleep(0.25)
        await page.wait_for_timeout(8000)
        await page.unroute(route)
        return None
    return await wire.next_reply("maseQ", before, 60)


def _dump(found: dict[str, Any], profile: str) -> None:
    out = default_out_path(f"character_bind_{profile}")
    out.write_text(json.dumps(found, indent=2, ensure_ascii=False), encoding="utf-8")
    step("wrote", str(out))


async def _bind(profile: str, project: str, name: str, images: list[Path], voice: str) -> int:
    profile_dir = resolve_profile_dir(profile)
    found: dict[str, Any] = {"profile": profile}
    async with build_client(profile_dir) as client:
        page = await client._context.new_page()  # noqa: SLF001 - spike reads the live context
        wire = _Wire(page)
        if project == "new":
            from gflow_cli.api.transports.migrated_composer import create_project

            info = await create_project(page, f"portability spike {name}")
            project = info.project_id
            step("project", f"created {project}")
        found["project"] = project
        await _read_project(page, wire, project)
        await page.wait_for_timeout(3000)

        # 1. open the draft editor through the header add-menu (`account_circle`).
        #    Measured 2026-10-16: the click only navigates to /project/<id>/character;
        #    the entity is created lazily, on the first commit.
        await page.locator(
            "flow-tile-view-header button[aria-haspopup='menu']:has(mat-icon:text-is('add'))"
        ).first.click()
        await page.locator(
            "[role='menu'] button[role='menuitem']:has(mat-icon:text-is('account_circle'))"
        ).first.click()
        await page.locator("flow-create-character-footer").wait_for(timeout=30_000)
        step("draft", page.url)

        # 2. Measured: the draft page has NO name field. A character comes into being
        #    with its first image, so the footer's Upload is the create gesture.
        c_before, m_before = wire.count("C4BZMd"), wire.count("maseQ")
        async with page.expect_file_chooser(timeout=10_000) as fc:
            await page.locator(
                "flow-create-character-footer button:has(mat-icon:text-is('upload'))"
            ).first.click()
        await (await fc.value).set_files(str(images[0]))
        created = await wire.next_reply("C4BZMd", c_before, 60)
        r0 = await wire.next_reply("maseQ", m_before, 60)
        entity = created[0][1] if created else None
        step("create", f"footer upload -> C4BZMd entity={entity} maseQ={'ok' if r0 else None}")
        if entity is None:
            step("create", "no C4BZMd after the footer upload — unmeasured")
            return 1
        await page.locator("flow-character-form input.name-input").wait_for(timeout=30_000)
        step("editor", page.url)
        for sel, value, label in (
            ("flow-character-form input.name-input", name, "name"),
            ("flow-character-form textarea.personality-textarea", "calm, precise", "personality"),
        ):
            before = wire.count("rzMKMb")
            field = page.locator(sel).first
            await field.fill(value)
            await field.press("Tab")
            reply = await wire.next_reply("rzMKMb", before, 20)
            step("patch", f"{label}: {json.dumps(reply)[:200] if reply else 'NO REPLY'}")
        found["entity"] = entity

        # 3. body: select the body slot chip, then that slot's own Upload
        await page.locator("flow-slot-chip-button button").nth(1).click()
        await page.wait_for_timeout(1500)
        r2 = await _upload(page, wire, images[-1])
        step("upload", f"body via slot upload: {'ok' if r2 else 'NO REPLY'}")
        found["maseQ_requests"] = wire.reqs.get("maseQ", [])
        _dump(found, profile)
        await page.keyboard.press("Escape")

        # 4. voice through the picker: search, exact option, confirm
        before = wire.count("rzMKMb")
        # Measured: with no voice yet the component renders one plain "add voice" button;
        # `voice-card-start-btn` only exists once a voice is set. Its first button covers both.
        await page.locator("flow-character-voice-button button").first.click()
        dialog = page.locator("mat-dialog-container").last
        await dialog.locator("input.search-input").fill(voice)
        await page.wait_for_timeout(1200)
        options = dialog.locator("[role='option']")
        texts = [(await options.nth(i).inner_text()).strip() for i in range(await options.count())]
        # Measured: an option's text is "<ligature>\n<Voice name>\n<description>"; the
        # name is an untranslated proper noun, the description is translated. Match the
        # name LINE exactly (case-insensitive), never a prefix of the whole text.
        exact = [
            i
            for i, t in enumerate(texts)
            if voice.casefold() in [ln.strip().casefold() for ln in t.splitlines()]
        ]
        step("voice", f"options after search={len(texts)} exact={exact} texts={texts!r}")
        if len(exact) == 1:
            await options.nth(exact[0]).click()
            await dialog.locator("button[class*='detail-add']").click()
            reply = await wire.next_reply("rzMKMb", before, 20)
            step("voice", f"{'rzMKMb ' + json.dumps(reply)[:200] if reply else 'NO REPLY'}")

        # 5. fresh read
        read = await _read_project(page, wire, project)
        row = _entity_row(read, entity)
        found["entity_row"] = row
        step("reread", json.dumps(row)[:400])

        # 6. lost reply: create a second character through the footer Upload while the
        #    page never sees the maseQ answer (the server does get the request).
        await page.goto(f"{_ROOT}/project/{project}/character", wait_until="domcontentloaded")
        await page.locator("flow-create-character-footer").wait_for(timeout=30_000)
        c_before = wire.count("C4BZMd")
        await _upload(page, wire, images[0], drop_reply=True, footer=True)
        lost = await wire.next_reply("C4BZMd", c_before, 10)
        lost_id = lost[0][1] if lost else None
        read = await _read_project(page, wire, project)
        lost_row = _entity_row(read, lost_id) if lost_id else None
        found["lost_reply"] = {"entity": lost_id, "row": lost_row}
        step("lost", f"C4BZMd entity={lost_id}; on re-read: {json.dumps(lost_row)[:300]}")
        out = default_out_path(f"character_bind_{profile}")
        out.write_text(json.dumps(found, indent=2, ensure_ascii=False), encoding="utf-8")
        step("wrote", str(out))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    inv = sub.add_parser("inventory")
    inv.add_argument("--profile", required=True)
    inv.add_argument("--project", required=True)
    inv.add_argument("--entity", required=True)
    pr = sub.add_parser("probe", help="click selectors (menus only) and record overlays")
    pr.add_argument("--profile", required=True)
    pr.add_argument("--url", required=True)
    pr.add_argument("--click", action="append", default=[])
    pr.add_argument("--chain", action="store_true", help="keep overlays open between clicks")
    ex = sub.add_parser("export", help="read the project and download one entity's images")
    ex.add_argument("--profile", required=True)
    ex.add_argument("--project", required=True)
    ex.add_argument("--entity", required=True)
    ex.add_argument("--out", required=True, type=Path)
    bd = sub.add_parser("bind", help="create via UI, upload portrait+body+dropped-reply, voice")
    bd.add_argument("--profile", required=True)
    bd.add_argument("--project", required=True, help="project id, or 'new'")
    bd.add_argument("--name", required=True)
    bd.add_argument("--image", action="append", required=True, type=Path)
    bd.add_argument("--voice", default="Aoede")
    args = ap.parse_args()
    if args.mode == "export":
        return asyncio.run(_export(args.profile, args.project, args.entity, args.out))
    if args.mode == "bind":
        return asyncio.run(_bind(args.profile, args.project, args.name, args.image, args.voice))
    if args.mode == "inventory":
        return asyncio.run(_inventory(args.profile, args.project, args.entity))
    if args.mode == "probe":
        return asyncio.run(_probe(args.profile, args.url, args.click, chain=args.chain))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
