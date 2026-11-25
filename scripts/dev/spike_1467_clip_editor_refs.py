r"""#1467: how does Flow's clip editor take an image reference for a video edit? ($0)

Live e2e on 2026-11-25: in the clip editor (``/project/<pid>/edit/<media>``) the ``@`` picker
listed NO option for a reference image uploaded into the project seconds earlier (90 s of
polling, ``offered: <nothing>``), while the main composer's ``@`` picker listed the uploaded
clip. This probe records what the clip editor's prompt box actually offers, through the
production helpers, so the reference strategy is chosen on evidence.

What it records (structure, not labels; both sides of each transition):

(a) every control inside and around ``flow-edit-video-prompt-box`` and its panel: tag, role,
    aria-* attributes, ``mat-icon`` / ``.google-symbols`` ligatures, custom-element
    ancestors, visible / enabled, and what ``elementFromPoint`` returns at its centre;
(b) the overlay after typing ``@`` alone: each picker option's role, structure, data-*
    attributes, icons, media children (``img`` / ``video``) and truncated visible text;
(c) the same after ``@`` plus the first 6 characters of the image's display name;
(d) every add-media-looking control in the editor (ligatures ``add``, ``add_photo_alternate``,
    ``image``, ``attach_file``, ``upload``, ``add_circle``, ``photo_library``, ``perm_media``):
    clicked one at a time, recording the overlay / dialog before and after and whether a
    file-chooser event fired, then closed with Escape (no file is ever set);
(e) every ``batchexecute`` rpcid requested or answered during the run.

Cost: $0. Navigation, DOM reads and typing only. A route aborts every billed submit
(``jIps6``, ``fZytfe``, ``ogiZ0b`` and the composer's video submits) for the whole run and
records any attempt in ``submits_aborted``; nothing is uploaded or created.

PRE-REGISTERED READING:
  * images are listed under ``@`` (b or c)              -> the clip editor does take ``@``
    image references; gflow's name match or ingest wait is what is wrong
  * only videos are listed, and an add-reference control in (d) opens a project-asset
    picker or a file chooser                            -> references attach through that
    control, not ``@``
  * neither                                             -> the clip editor does not take
    image references: route references through the main composer path, or refuse them
  * ``submits_aborted`` is non-empty                    -> a probe gesture submits; the run
    is invalid for (b)-(d) and the gesture must be named before anything else
  * the editor or the picker never renders              -> UNMEASURED; settles nothing

``--upload-fresh PNG`` (second question, after the 2026-11-25 run of a339823a found nothing
for an image uploaded in the same page session): uploads the image the way the edit path
does (``MigratedComposer.upload`` on the project page), opens the clip editor in the SAME
page, and records the asset list (items, count, whether the new name is present) and where
the typed text went (prompt box text, the popover's own inputs and their values, the focused
element) for: (1) ``@`` + full stem; (2) ``@`` + 6-character prefix; (3) after clicking the
browser's ``image`` tab and opening and closing its sort ``mat-select``; (4) after closing and
re-opening the browser (``@`` + stem); (5) after reloading the same clip-editor URL
(``@`` + stem). The upload is free and stays in the project.

PRE-REGISTERED READING (--upload-fresh):
  * absent in (1)-(4), present in (5)            -> H1: the browser does not list an asset
    uploaded in this page session until a reload; the edit path must reload (or upload
    elsewhere) before attaching
  * absent in (1)-(2), present after (3) or (4)  -> H1, refreshed by that gesture; drive it
  * absent in (1), present in (2)                -> H2: the filter does not take the full stem;
    type a prefix
  * the typed text lands in the prompt box and the popover has its own search input with an
    empty value                                  -> H2 (where the text goes); type into the input
  * present in (1)                               -> neither; the attach failure is elsewhere
    (the click, the add button or the chip check) and is unmeasured here
  * absent everywhere, including (5)             -> the upload did not reach this project's
    browser; UNMEASURED for H1/H2

    uv run python scripts/dev/spike_1467_clip_editor_refs.py --profile P \
        --project PROJECT_ID --clip CLIP_MEDIA_ID --image IMAGE_MEDIA_ID [--image-name NAME] \
        [--upload-fresh PATH_TO_PNG]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip

from gflow_cli.api.transports._mc_common import (  # noqa: E402
    _CARET_TO_END_JS,  # pyright: ignore[reportPrivateUsage]
    IMAGE_SUBMIT_RPC,
    SUBMIT_RPCS,
    request_body,
)
from gflow_cli.api.transports.batchexecute import parse_frames  # noqa: E402
from gflow_cli.api.transports.migrated_extend import (  # noqa: E402
    EXTEND_PROMPT,
    EXTEND_SUBMIT_RPC,
    _anchor,  # pyright: ignore[reportPrivateUsage]
    _source_clip,  # pyright: ignore[reportPrivateUsage]
    batch_rpcids,
    url_rpcids,
)
from gflow_cli.api.transports.migrated_video_edit import EDIT_SUBMIT_RPC  # noqa: E402

_HELD = frozenset(SUBMIT_RPCS) | {IMAGE_SUBMIT_RPC, EXTEND_SUBMIT_RPC, EDIT_SUBMIT_RPC}
_PROMPT_BOX = "flow-edit-video-prompt-box"
_ADD_LIGATURES = (
    "add",
    "add_photo_alternate",
    "image",
    "attach_file",
    "upload",
    "add_circle",
    "photo_library",
    "perm_media",
)

#: Controls in and around the prompt box: its own subtree, and its ancestors' subtrees up to
#: ``levels`` above it (the clip editor panel), deduplicated.
_CONTROLS_JS = r"""([box, levels]) => {
  const root = document.querySelector(box);
  if (!root) return {found: false, controls: []};
  let scope = root;
  for (let i = 0; i < levels && scope.parentElement; i++) scope = scope.parentElement;
  const seen = new Set();
  const out = [];
  for (const el of scope.querySelectorAll(
      'button, [role], input, textarea, a, [contenteditable="true"], mat-icon, .google-symbols')) {
    const target = el.closest('button, [role], a, input, textarea') || el;
    if (seen.has(target)) continue;
    seen.add(target);
    const r = target.getBoundingClientRect();
    const style = getComputedStyle(target);
    const visible = r.width > 0 && r.height > 0 && style.visibility !== 'hidden'
                    && style.display !== 'none';
    let hit = null;
    if (visible) {
      const at = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      hit = at === null ? 'none' : (target.contains(at) ? 'self' :
            at.tagName.toLowerCase() + (at.className && typeof at.className === 'string'
            ? '.' + at.className.trim().split(/\s+/).slice(0, 2).join('.') : ''));
    }
    const ancestors = [];
    for (let p = target.parentElement; p && ancestors.length < 8; p = p.parentElement) {
      if (p.tagName.includes('-')) ancestors.push(p.tagName.toLowerCase());
    }
    out.push({
      tag: target.tagName.toLowerCase(),
      role: target.getAttribute('role'),
      aria: [...target.attributes].filter(a => a.name.startsWith('aria-'))
              .map(a => a.name + '=' + a.value.slice(0, 40)),
      data: [...target.attributes].filter(a => a.name.startsWith('data-'))
              .map(a => a.name + '=' + a.value.slice(0, 40)),
      classes: (typeof target.className === 'string' ? target.className : '')
                 .trim().split(/\s+/).slice(0, 4),
      ligatures: [...target.querySelectorAll('mat-icon, .google-symbols, i')]
                   .map(x => x.textContent.trim()).filter(Boolean)
                   .concat(target.matches('mat-icon, .google-symbols')
                           ? [target.textContent.trim()] : []),
      in_prompt_box: root.contains(target),
      custom_ancestors: ancestors,
      visible,
      enabled: !(target.disabled === true || target.getAttribute('aria-disabled') === 'true'),
      element_from_point: hit,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    });
  }
  return {found: true, scope: scope.tagName.toLowerCase(), controls: out};
}"""

#: The overlay layer: picker options, menus and dialogs, with what kind of media each shows.
_OVERLAY_JS = r"""() => {
  const items = [...document.querySelectorAll(
    '.cdk-overlay-container [role], .cdk-overlay-container button, [role=dialog], dialog')];
  return items.map((el, i) => ({
    index: i,
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute('role'),
    classes: (typeof el.className === 'string' ? el.className : '')
               .trim().split(/\s+/).slice(0, 4),
    data: [...el.attributes].filter(a => a.name.startsWith('data-'))
            .map(a => a.name + '=' + a.value.slice(0, 40)),
    aria: [...el.attributes].filter(a => a.name.startsWith('aria-'))
            .map(a => a.name + '=' + a.value.slice(0, 40)),
    icons: [...el.querySelectorAll('mat-icon, .google-symbols, i')]
             .map(x => x.textContent.trim()).filter(Boolean),
    media: [...el.querySelectorAll('img, video')].map(m => ({
      tag: m.tagName.toLowerCase(),
      src_host: (() => { try { return new URL(m.currentSrc || m.src).host; }
                          catch (e) { return null; } })(),
    })),
    children: [...el.children].map(c => c.tagName.toLowerCase()),
    custom_ancestors: (() => { const a = []; for (let p = el.parentElement; p && a.length < 6;
                         p = p.parentElement) if (p.tagName.includes('-'))
                         a.push(p.tagName.toLowerCase()); return a; })(),
    text: el.textContent.trim().replace(/\s+/g, ' ').slice(0, 80),
  }));
}"""


#: The clip editor's asset browser (spike 2026-11-25): its items and where typed text lands.
_ASSET_LIST_JS = r"""(stem) => {
  const items = [...document.querySelectorAll(
    "flow-add-menu-asset-list button[role='option'].asset-item")]
    .map(el => el.textContent.trim().replace(/\s+/g, ' ').slice(0, 80));
  const pop = document.querySelector('flow-add-menu-popover-content');
  const box = document.querySelector('flow-edit-video-prompt-box .ProseMirror');
  const active = document.activeElement;
  return {
    popover_open: pop !== null,
    count: items.length,
    items: items.slice(0, 20),
    new_name_present: items.some(t => t.toLowerCase().startsWith(stem.toLowerCase())),
    prompt_box_text: box ? box.textContent.slice(0, 120) : null,
    popover_inputs: pop ? [...pop.querySelectorAll('input, textarea, [contenteditable="true"]')]
      .map(i => ({tag: i.tagName.toLowerCase(), type: i.getAttribute('type'),
                  value: (i.value ?? i.textContent ?? '').slice(0, 80),
                  aria: [...i.attributes].filter(a => a.name.startsWith('aria-'))
                          .map(a => a.name + '=' + a.value.slice(0, 40)),
                  custom_ancestors: (() => { const a = []; for (let p = i.parentElement;
                      p && a.length < 5; p = p.parentElement) if (p.tagName.includes('-'))
                      a.push(p.tagName.toLowerCase()); return a; })()})) : [],
    focused: active ? {tag: active.tagName.toLowerCase(),
                       classes: (typeof active.className === 'string' ? active.className : '')
                                  .trim().split(/\s+/).slice(0, 3),
                       in_popover: pop !== null && pop.contains(active)} : null,
  };
}"""


_PROSEMIRROR_HIT_JS = r"""(sel) => {
  const el = document.querySelector(sel);
  if (!el) return {found: false};
  const r = el.getBoundingClientRect();
  const at = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
  return {found: true, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width),
          Math.round(r.height)],
          hit: at === null ? 'none' : (el.contains(at) ? 'self' :
               at.tagName.toLowerCase() + (typeof at.className === 'string' && at.className
               ? '.' + at.className.trim().split(/\s+/).slice(0, 2).join('.') : ''))};
}"""


async def _clear_prompt(page: Any) -> None:
    await page.locator(EXTEND_PROMPT).first.click(timeout=5000)
    await page.keyboard.press("ControlOrMeta+a")
    await page.keyboard.press("Backspace")
    await page.wait_for_timeout(400)


async def _settle_popover(page: Any) -> bool:
    """Escape twice and wait for the asset browser to detach; whether it did."""
    await _close_overlays(page)
    try:
        await page.locator("flow-add-menu-popover-content").first.wait_for(
            state="detached", timeout=5000
        )
    except Exception:  # noqa: BLE001 - recorded by the caller
        return False
    return True


async def _focus_prompt(page: Any, record: dict[str, Any]) -> None:
    """Click the prompt box; if ``.ProseMirror`` is occluded, its container instead."""
    record["prosemirror_hit"] = await page.evaluate(_PROSEMIRROR_HIT_JS, EXTEND_PROMPT)
    try:
        await page.locator(EXTEND_PROMPT).first.click(timeout=5000)
        record["focused_via"] = "prosemirror"
    except Exception as exc:  # noqa: BLE001 - a probe: try the container
        record["prosemirror_click"] = f"failed: {type(exc).__name__}"
        await page.locator(_PROMPT_BOX).first.click(timeout=5000)
        record["focused_via"] = "container"
    await page.evaluate(_CARET_TO_END_JS, EXTEND_PROMPT)


async def _fresh_upload_probe(  # noqa: PLR0915
    page: Any, args: Any, report: dict[str, Any], flush: Any
) -> None:
    """``--upload-fresh``: H1 (no refresh in this page session) against H2 (the filter).

    Every step is recorded and flushed to disk as soon as it ends, and a failing step is
    recorded and skipped rather than ending the run.
    """
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    composer = MigratedComposer()
    step("fresh", "upload on the project page, as the edit path does")
    report["fresh_surface"] = await composer.ensure_editor_for(
        page, args.project, needs_classic=True
    )
    media_id, name = await composer.upload(page, args.project, Path(args.upload_fresh))
    stem = Path(name).stem
    report["fresh_upload"] = {"media_id": media_id, "name": name, "stem": stem}
    clip = await _source_clip(page, project_id=args.project, media_id=args.clip)
    report["fresh_clip"] = None if clip is None else {"kind": clip.kind, "ready": clip.ready}
    await _anchor(page, EXTEND_PROMPT, named="clip editor prompt box")
    steps: dict[str, Any] = {}
    report["fresh_steps"] = steps
    flush()

    async def run(label: str, body: Any) -> None:
        record: dict[str, Any] = {}
        steps[label] = record
        step(f"fresh-{label}", "")
        try:
            record["popover_detached_before"] = await _settle_popover(page)
            await body(record)
        except Exception as exc:  # noqa: BLE001 - keep this step's evidence, go on
            record["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        flush()

    async def type_and_snapshot(record: dict[str, Any], text: str) -> None:
        await _focus_prompt(page, record)
        await page.keyboard.type(text, delay=100)
        await page.wait_for_timeout(2500)
        record["typed"] = text
        record["list"] = await page.evaluate(_ASSET_LIST_JS, stem)

    async def full_stem(record: dict[str, Any]) -> None:
        await type_and_snapshot(record, "@" + stem)

    async def prefix6(record: dict[str, Any]) -> None:
        await _settle_popover(page)
        await _clear_prompt(page)
        await type_and_snapshot(record, "@" + stem[:6])

    async def tab_and_sort(record: dict[str, Any]) -> None:
        await type_and_snapshot(record, "@")
        tab = page.locator(
            "flow-add-menu-popover-content button:has(mat-icon:text-is('image')), "
            "flow-add-menu-popover-content [role='tab']:has(mat-icon:text-is('image'))"
        ).first
        try:
            await tab.click(timeout=4000)
            record["tab_clicked"] = True
        except Exception as exc:  # noqa: BLE001
            record["tab_clicked"] = f"failed: {type(exc).__name__}"
        await page.wait_for_timeout(2000)
        record["after_image_tab"] = await page.evaluate(_ASSET_LIST_JS, stem)
        sort = page.locator("flow-add-menu-popover-content mat-select").last
        try:
            await sort.click(timeout=4000)
            await page.wait_for_timeout(1000)
            record["sort_overlay"] = await page.evaluate(_OVERLAY_JS)
            await page.keyboard.press("Escape")
            record["sort_clicked"] = True
        except Exception as exc:  # noqa: BLE001
            record["sort_clicked"] = f"failed: {type(exc).__name__}"
        await page.wait_for_timeout(1500)
        record["after_sort"] = await page.evaluate(_ASSET_LIST_JS, stem)

    async def reopened(record: dict[str, Any]) -> None:
        await _settle_popover(page)
        record["prompt_box_after_close"] = await page.evaluate(_PROSEMIRROR_HIT_JS, EXTEND_PROMPT)
        await _focus_prompt(page, record)
        await page.keyboard.press("ControlOrMeta+a")
        await page.keyboard.press("Backspace")
        await type_and_snapshot(record, "@" + stem)

    async def reloaded(record: dict[str, Any]) -> None:
        await page.reload(wait_until="domcontentloaded")
        await _anchor(page, EXTEND_PROMPT, named="clip editor prompt box")
        await page.wait_for_timeout(3000)
        await type_and_snapshot(record, "@" + stem)

    await run("1_full_stem", full_stem)
    await run("2_prefix6", prefix6)
    await run("3_tab_and_sort", tab_and_sort)
    await run("4_reopened_full_stem", reopened)
    await run("5_reloaded_full_stem", reloaded)
    await _settle_popover(page)


def _rpcids_of(url: str, body: str) -> list[str]:
    return batch_rpcids(url_rpcids(url), body)


async def _image_name(profile: str, image: str, override: str | None) -> str | None:
    """The image's display name: the flag, else the local catalog's file name."""
    if override:
        return override
    from gflow_cli.config import get_settings
    from gflow_cli.data.repository import DataRepository
    from gflow_cli.data.store import DataStore

    with DataStore.open(get_settings().resolved_db_path()) as store:
        asset = DataRepository(store).get_asset_by_flow_media_id(profile, image)
    if asset is None:
        return None
    name = asset.metadata_json.get("display_name") if asset.metadata_json else None
    if isinstance(name, str) and name:
        return name
    for local in asset.local_files:
        if local.path is not None:
            return local.path.name
    return None


async def _type_in_prompt(page: Any, text: str) -> None:
    await page.locator(EXTEND_PROMPT).first.click(timeout=5000)
    await page.evaluate(_CARET_TO_END_JS, EXTEND_PROMPT)
    await page.keyboard.type(text, delay=100)
    await page.wait_for_timeout(2500)


async def _close_overlays(page: Any) -> None:
    for _ in range(2):
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)


async def _probe_add_controls(page: Any, controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """(d): click each add-media-looking control once and record what opens."""
    results: list[dict[str, Any]] = []
    candidates = [
        c
        for c in controls
        if c["visible"] and c["tag"] == "button" and set(c["ligatures"]) & set(_ADD_LIGATURES)
    ]
    for control in candidates[:4]:
        lig = next(x for x in control["ligatures"] if x in _ADD_LIGATURES)
        choosers: list[str] = []

        def on_chooser(chooser: Any, sink: list[str] = choosers) -> None:
            sink.append("multiple" if chooser.is_multiple() else "single")

        before = await page.evaluate(_OVERLAY_JS)
        page.on("filechooser", on_chooser)
        try:
            button = page.locator(
                f"{_PROMPT_BOX} button:has(mat-icon:text-is('{lig}')), "
                f"button:has(mat-icon:text-is('{lig}'))"
            ).first
            clicked = True
            try:
                await button.click(timeout=4000)
            except Exception as exc:  # noqa: BLE001 - a probe: record and go on
                clicked = f"failed: {type(exc).__name__}"
            await page.wait_for_timeout(2500)
            after = await page.evaluate(_OVERLAY_JS)
        finally:
            page.remove_listener("filechooser", on_chooser)
        results.append(
            {
                "ligature": lig,
                "control": control,
                "clicked": clicked,
                "file_chooser": choosers,
                "overlay_before": before,
                "overlay_after": after,
            }
        )
        await _close_overlays(page)
    return results


async def main() -> None:  # noqa: PLR0915
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--clip", required=True, help="media id of a finished clip in the project")
    ap.add_argument("--image", required=True, help="media id of an image in the project")
    ap.add_argument("--image-name", default=None, help="its display name (else the catalog's)")
    ap.add_argument(
        "--upload-fresh",
        default=None,
        help="a local image: upload it in this page session and probe H1/H2 (see docstring)",
    )
    args = ap.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    name = await _image_name(args.profile, args.image, args.image_name)
    report: dict[str, Any] = {
        "args": {k: v for k, v in vars(args).items() if k != "profile"},
        "image_name": name,
        "rpcids_requested": [],
        "rpcids_answered": [],
        "submits_aborted": [],
        "image_record_in_listing": None,
    }

    async def hold(route: Any, request: Any) -> None:
        body = request_body(request)
        rpcids = _rpcids_of(str(request.url), body)
        report["rpcids_requested"].extend(rpcids)
        held = sorted(set(rpcids) & _HELD)
        if held:
            report["submits_aborted"].extend(held)
            await route.abort()
            return
        await route.fallback()

    async def on_response(response: Any) -> None:
        if "batchexecute" not in str(response.url):
            return
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001 - an aborted body is not evidence
            return
        for rid, payload in parse_frames(text):
            report["rpcids_answered"].append(rid)
            if args.image in json.dumps(payload) and report["image_record_in_listing"] is None:
                report["image_record_in_listing"] = {"rpcid": rid, "payload": payload}

    out = default_out_path("spike_1467_clip_editor_refs")

    def flush() -> None:
        """Write what has been captured so far, so a later failure loses nothing."""
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    async with build_client(profile_dir) as client:
        page = await client._checkout_page()  # noqa: SLF001 - dev instrument
        await page.route("**/batchexecute*", hold)
        page.on("response", on_response)
        try:
            if args.upload_fresh:
                await _fresh_upload_probe(page, args, report, flush)
            else:
                step("nav", f"clip editor for {args.clip}")
                clip = await _source_clip(page, project_id=args.project, media_id=args.clip)
                report["clip"] = None if clip is None else {"kind": clip.kind, "ready": clip.ready}
                report["url_path"] = str(page.url).split("?")[0].split("flow.google.com")[-1]
                await _anchor(page, EXTEND_PROMPT, named="clip editor prompt box")

                step("a", "controls in and around the prompt box")
                report["a_controls"] = await page.evaluate(_CONTROLS_JS, [_PROMPT_BOX, 4])

                step("b", "@ alone")
                report["b_overlay_before"] = await page.evaluate(_OVERLAY_JS)
                await _type_in_prompt(page, "@")
                report["b_overlay_after"] = await page.evaluate(_OVERLAY_JS)
                await _close_overlays(page)

                if name:
                    prefix = name[:6]
                    step("c", f"@ + {prefix!r}")
                    report["c_query"] = prefix
                    report["c_overlay_before"] = await page.evaluate(_OVERLAY_JS)
                    await _type_in_prompt(page, "@" + prefix)
                    report["c_overlay_after"] = await page.evaluate(_OVERLAY_JS)
                    await _close_overlays(page)
                else:
                    report["c_skipped"] = "no display name (pass --image-name)"

                step("d", "add-media controls")
                controls = report["a_controls"].get("controls", [])
                report["d_add_controls"] = await _probe_add_controls(page, controls)
                report["a_controls_after_probe"] = await page.evaluate(
                    _CONTROLS_JS, [_PROMPT_BOX, 4]
                )
        except Exception as exc:  # noqa: BLE001 - keep what was captured
            report["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        finally:
            page.remove_listener("response", on_response)
            try:
                await page.unroute("**/batchexecute*", hold)
            finally:
                client._checkin_page(page)  # noqa: SLF001

    report["rpcids_requested"] = sorted(set(report["rpcids_requested"]))
    report["rpcids_answered"] = sorted(set(report["rpcids_answered"]))
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    step("done", f"{out}  submits_aborted={report['submits_aborted']}")


if __name__ == "__main__":
    asyncio.run(main())
