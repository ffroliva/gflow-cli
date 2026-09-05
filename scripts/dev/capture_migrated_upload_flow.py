"""Can a LOCAL file be attached on the migrated host? — the `Upload media` flow, $0.

No submit exists in this probe, so no generation can occur. It **does** add one asset to
the account's Flow library, which is the point: `--ref <path>` has nowhere to go until a
local file can be got into the picker.

**Why this is the last gap.** Everything the attach recon settled — the `@` picker,
ArrowDown+Enter, the `MZZa6b` submit carrying the entity id — attaches an asset that
**already exists**. The reported command that started this work passes two local files
(`--ref me.jpg`, `--ref …png`), so without an upload path the port cannot serve it at all.
The library menu's `Upload` entry and the picker's `Upload media` button have both been
seen; neither has been driven, and no `input[type=file]` exists in the DOM until one is.

What it reports: whether clicking `Upload media` opens a native file chooser (Playwright's
`expect_file_chooser`) or reveals a hidden input, what rpc the upload fires, whether the
uploaded asset then appears in the picker, and whether it can be attached as a chip the
same way an existing asset can.

    uv run python scripts/dev/capture_migrated_upload_flow.py <profile> <project-id> <image>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    COMPOSER,
    MigratedComposer,
)

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip


def _stage(msg: str) -> None:
    print(f"[stage] {msg}", file=sys.stderr, flush=True)


def _rpcid(post_data: str | None) -> str | None:
    if not post_data:
        return None
    try:
        req = urllib.parse.parse_qs(post_data).get("f.req", [None])[0]
        return json.loads(req)[0][0][0] if req else None
    except Exception:  # noqa: BLE001 — observation only
        return None


async def _chips(page: Any) -> list[dict[str, Any]]:
    return await page.evaluate(
        """() => [...document.querySelectorAll('.mention-chip')].map(c => ({
            text: (c.textContent || '').trim(),
            entity_id: c.getAttribute('data-entity-id'),
            reference_type: c.getAttribute('data-reference-type'),
        }))"""
    )


async def _probe(page: Any, project_id: str, image: Path) -> dict[str, Any]:
    composer = MigratedComposer()
    phase = {"now": "startup"}
    calls: list[dict[str, Any]] = []

    def _on_request(request: Any) -> None:
        if request.method != "POST":
            return
        if "data/batchexecute" in request.url:
            calls.append({"phase": phase["now"], "rpcid": _rpcid(request.post_data)})
        elif "upload" in request.url.lower():
            calls.append({"phase": phase["now"], "upload_url": request.url[:140]})

    page.on("request", _on_request)

    await composer.ensure_editor(page, project_id)
    pane = await composer._open_pane(page)  # noqa: SLF001 — dev instrument
    await composer._select(page, pane, axis="mode", lig="videocam")  # noqa: SLF001
    await composer._select(page, pane, axis="submode", lig="chrome_extension")  # noqa: SLF001
    await composer._close_pane(page, strict=False)  # noqa: SLF001
    await page.wait_for_timeout(4000)

    report: dict[str, Any] = {"image": str(image), "size_bytes": image.stat().st_size}

    phase["now"] = "picker_open"
    await page.locator(COMPOSER).first.click(timeout=5000)
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(2500)
    report["assets_before"] = await page.locator("button.asset-item").count()

    upload = page.locator("button").filter(has_text="Upload media")
    report["upload_button_found"] = bool(await upload.count())
    report["file_inputs_before"] = await page.locator("input[type=file]").count()
    if not await upload.count():
        report["error"] = "no 'Upload media' button in the picker"
        return report

    phase["now"] = "upload"
    _stage("clicking 'Upload media' and expecting a file chooser")
    try:
        async with page.expect_file_chooser(timeout=15000) as fc_info:
            await upload.first.click(timeout=5000)
        chooser = await fc_info.value
        report["file_chooser"] = True
        await chooser.set_files(str(image))
        _stage("file set; waiting for the upload to land")
    except Exception as exc:  # noqa: BLE001 — the failure IS a result
        report["file_chooser"] = False
        report["chooser_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        # Fall back: a hidden input that appeared only after the click.
        hidden = page.locator("input[type=file]")
        if await hidden.count():
            _stage("no chooser, but a file input appeared — setting it directly")
            await hidden.first.set_input_files(str(image))
            report["used_hidden_input"] = True
        else:
            return report

    # The asset appears immediately as "Uploading<name>" and is not attachable yet — a
    # fixed 12 s wait caught it mid-flight and ArrowDown+Enter inserted nothing. Poll the
    # label instead, which is the only readback the picker offers.
    phase["now"] = "uploading"

    async def _uploading() -> bool:
        labels = await page.locator("button.asset-item").all_text_contents()
        return any(t.strip().startswith("Uploading") for t in labels)

    # Two waits, not one. The list does not update the instant the file is set, so a
    # single "is it done?" poll answers YES before the entry has even appeared — that is
    # why the previous run reported settling in 0 ms while the asset was still in flight.
    appeared_ms = 0
    while appeared_ms < 20_000 and not await _uploading():
        await page.wait_for_timeout(1000)
        appeared_ms += 1000
    waited_ms = 0
    while waited_ms < 180_000 and await _uploading():
        await page.wait_for_timeout(2000)
        waited_ms += 2000
    report["upload_appeared_ms"] = appeared_ms
    report["upload_settle_ms"] = waited_ms
    _stage(f"upload settled after ~{waited_ms} ms")
    phase["now"] = "after_upload"
    report["assets_after"] = await page.locator("button.asset-item").count()
    report["asset_names"] = [
        t.strip()[:40] for t in await page.locator("button.asset-item").all_text_contents()
    ][:8]

    # Attach as a SEPARATE mention rather than continuing the picker that did the upload:
    # the native file chooser costs keyboard focus, so ArrowDown lands nowhere and nothing
    # inserts. Close it, start a fresh `@`, and filter by the uploaded name — which is
    # also the shape a port wants (upload, then reference it by name).
    phase["now"] = "attach"
    for _ in range(3):
        if not await page.locator(".cdk-overlay-backdrop").count():
            break
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
    await page.locator(COMPOSER).first.click(timeout=5000)
    await page.keyboard.press("Control+a")
    await page.keyboard.press("Backspace")
    await page.wait_for_timeout(800)

    stem = image.stem[:12]
    _stage(f"fresh @ mention, filtering on {stem!r}")
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(2200)
    await page.keyboard.type(stem, delay=100)
    await page.wait_for_timeout(3000)
    report["assets_matching"] = [
        t.strip()[:40] for t in await page.locator("button.asset-item").all_text_contents()
    ][:5]
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(3000)
    report["chips"] = await _chips(page)

    # If it attached, take the payload too — the same in-page fetch/XHR block the submit
    # probe uses, so the request is never created and nothing can generate.
    if report["chips"]:
        phase["now"] = "submit"
        await page.locator(COMPOSER).first.click(timeout=5000)
        await page.keyboard.insert_text(" a woman holding the product")
        await page.wait_for_timeout(800)
        await page.evaluate(
            """() => {
                window.__cap = [];
                const t = (u) => String(u).includes('data/batchexecute');
                const of = window.fetch;
                window.fetch = function (i, init) {
                    const u = (i && i.url) || i;
                    const b = (init && init.body) || null;
                    if (t(u)) { window.__cap.push(b ? String(b) : null);
                                return Promise.reject(new Error('blocked')); }
                    return of.apply(this, arguments);
                };
                const os = XMLHttpRequest.prototype.send;
                const oo = XMLHttpRequest.prototype.open;
                XMLHttpRequest.prototype.open = function (m, u) {
                    this.__u = u; return oo.apply(this, arguments); };
                XMLHttpRequest.prototype.send = function (b) {
                    if (t(this.__u)) { window.__cap.push(b ? String(b) : null); return; }
                    return os.apply(this, arguments); };
            }"""
        )
        submit = page.locator("button").filter(
            has=page.locator("mat-icon", has_text="arrow_forward")
        ).first
        if await submit.count():
            _stage("in-page block armed; clicking submit")
            await submit.click(timeout=5000)
            await page.wait_for_timeout(6000)
        raw = await page.evaluate("() => window.__cap || []")
        decoded = []
        for body in raw:
            try:
                req = urllib.parse.parse_qs(body or "").get("f.req", [None])[0]
                inner = json.loads(req)[0][0]
                decoded.append({"rpcid": inner[0], "payload": json.loads(inner[1])})
            except Exception:  # noqa: BLE001, PERF203 — observation only
                decoded.append({"raw": (body or "")[:300]})
        report["submit_captures"] = decoded

    by_phase: dict[str, list[str]] = {}
    for c in calls:
        by_phase.setdefault(c["phase"], []).append(c.get("rpcid") or c.get("upload_url") or "?")
    report["traffic_by_phase"] = by_phase
    report["note"] = "NOTHING SUBMITTED — no generation, but one asset was uploaded"
    return report


async def _main(profile: str, project_id: str, image: str, out_path: str) -> int:
    path = Path(image).expanduser().resolve()
    if not path.is_file():
        print(f"no such image: {path}", file=sys.stderr)
        return 2
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        report = await _probe(page, project_id, path)
        Path(out_path).write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _stage(f"report written to {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("project_id")
    parser.add_argument("image")
    parser.add_argument("--out", default="upload_flow.json")
    args = parser.parse_args()
    return asyncio.run(_main(args.profile, args.project_id, args.image, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
