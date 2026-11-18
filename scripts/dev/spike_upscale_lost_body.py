r"""Is a 2K image upscale recoverable when Chrome loses the ``SPrCad`` reply body? ($0, #1027)

#1027: ``upscale_image_migrated`` fails with ``WireFormatError: Failed to parse SPrCad
response: ... Network.getResponseBody: No data found for resource with given identifier``.
Unlike the video export (#1014), the lost body IS the payload: the upscaled image travels
base64 inside it. So the question is not "can we ignore it" but "where else is the image".

The app itself reads the reply with ``fetch`` (2026-10-20 spike), which a DevTools eviction
does not touch, and then hands the image to an ``<a download>`` that it ``click()``s. The
shipped code already stubs ``HTMLAnchorElement.prototype.click`` so no file lands in the
user's Downloads. This spike makes that stub RECORD the anchor it swallowed, then reads the
anchor's ``href`` from page JS. Nothing is patched that the shipped path does not patch.

**Revised after run 1:** the ``href`` read failed (the app revokes the ``blob:`` URL at
once), the "unreadable from page JS" row below. The script then also hooks
``URL.createObjectURL``, as ``migrated_video_upscale`` already did, and records each blob's
size, MIME and SHA-256. That hook is what the 12-run measurement used.

Per run it records: whether the DevTools read of the ``SPrCad`` body succeeded (and the
exact error), the reply size, every anchor the app tried to click (scheme, ``download``
name), and for each one the bytes behind the ``href`` (size, magic, and whether they equal
the DevTools copy when that one was readable).

**Pre-registered readings** (written before the first run):

| Observation | Reading |
|---|---|
| every run's anchor ``href`` yields a JPEG/PNG equal to the DevTools bytes | the anchor is a faithful second carrier; fall back to it |
| DevTools read fails on N>0 runs AND the anchor still yields an image on those runs | recovery proven on the failure itself; ship the fallback |
| DevTools never fails in this run | the fallback is shown equal on success only; fix stays "fallback, then typed error" |
| no anchor is clicked, or its ``href`` is unreadable from page JS | the anchor is not a carrier; next option is a re-click (``SPrCad`` is $0) |
| anchor bytes differ from the DevTools bytes | do not use the anchor; report what differs |

**Cost:** 2K image upscale only — zero credits. Uses the newest catalogued image of the
profile (same row choice as ``tests/e2e/test_migrated_upscale_e2e.py``).

    uv run python scripts/dev/spike_upscale_lost_body.py <profile> [--runs 10]
"""

# ruff: noqa: E501 - the outcome table and the injected JS read better unwrapped
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.transports.migrated_upscale import (  # noqa: E402
    UPSCALE_RPCID,
    _sprcad_b64,  # pyright: ignore[reportPrivateUsage]
    find_download_menu_item,
    open_tile_download_menu,
)
from gflow_cli.config import get_settings  # noqa: E402
from gflow_cli.data.queries import list_images  # noqa: E402

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip

_STUB_JS = """() => {
    window._spikeAnchors = [];
    window._spikeBlobs = [];
    window._origAnchorClick = HTMLAnchorElement.prototype.click;
    window._origCreateObjectURL = URL.createObjectURL;
    URL.createObjectURL = function(blob) {
        if (blob && blob.size > 1000) {
            const entry = {size: blob.size, type: blob.type, sha256: null};
            window._spikeBlobs.push(entry);
            blob.arrayBuffer().then(async (ab) => {
                const d = new Uint8Array(await crypto.subtle.digest('SHA-256', ab));
                entry.sha256 = Array.from(d).map(b => b.toString(16).padStart(2, '0')).join('');
                entry.head = Array.from(new Uint8Array(ab).slice(0, 4)).map(b => b.toString(16).padStart(2, '0')).join('');
            });
        }
        return window._origCreateObjectURL.call(URL, blob);
    };
    HTMLAnchorElement.prototype.click = function() {
        window._spikeAnchors.push({href: this.href, download: this.download});
    };
}"""

_RESTORE_JS = """() => {
    if (window._origAnchorClick) {
        HTMLAnchorElement.prototype.click = window._origAnchorClick;
        delete window._origAnchorClick;
    }
    if (window._origCreateObjectURL) {
        URL.createObjectURL = window._origCreateObjectURL;
        delete window._origCreateObjectURL;
    }
}"""

# Read one anchor href back from page JS: size, first bytes, and a sha256 for equality.
_READ_HREF_JS = """async (href) => {
    try {
        const r = await fetch(href);
        const buf = new Uint8Array(await r.arrayBuffer());
        const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', buf));
        const hex = Array.from(digest).map(b => b.toString(16).padStart(2, '0')).join('');
        const head = Array.from(buf.slice(0, 8)).map(b => b.toString(16).padStart(2, '0')).join('');
        return {ok: true, size: buf.length, head, sha256: hex, type: r.headers.get('content-type')};
    } catch (e) {
        return {ok: false, error: String(e)};
    }
}"""


async def _one_run(page: Any, project_id: str, media_id: str, idx: int) -> dict[str, Any]:
    rec: dict[str, Any] = {"run": idx, "devtools": None, "anchors": []}
    replies: list[dict[str, Any]] = []

    async def on_response(response: Any) -> None:
        if "batchexecute" not in response.url or UPSCALE_RPCID not in response.url:
            return
        entry: dict[str, Any] = {"status": response.status}
        try:
            text = await response.text()
            b64 = _sprcad_b64(text)
            entry["read"] = "ok"
            entry["text_len"] = len(text)
            if b64:
                raw = base64.b64decode(b64)
                entry["image_size"] = len(raw)
                entry["sha256"] = hashlib.sha256(raw).hexdigest()
        except Exception as exc:  # noqa: BLE001 - the error text is the measurement
            entry["read"] = "error"
            entry["error"] = str(exc)[:200]
        replies.append(entry)

    await open_tile_download_menu(
        page,
        project_id=project_id,
        media_id=media_id,
        tile_selector=f'img[data-media-id="{media_id}"]',
        tile_timeout_ms=30_000,
        kind="image",
        prefix="spike",
        route="image_upscale",
    )
    item = await find_download_menu_item(page, "2K", route="image_upscale", accepted=("2K",))
    page.on("response", on_response)
    t0 = time.monotonic()
    try:
        await page.evaluate(_STUB_JS)
        await item.click()
        deadline = t0 + 90
        while time.monotonic() < deadline:
            anchors = await page.evaluate("() => window._spikeAnchors || []")
            if replies and anchors:
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(2)  # late anchors
        anchors = await page.evaluate("() => window._spikeAnchors || []")
        rec["blobs"] = await page.evaluate("() => window._spikeBlobs || []")
    finally:
        page.remove_listener("response", on_response)
        await page.evaluate(_RESTORE_JS)
    rec["elapsed_s"] = round(time.monotonic() - t0, 1)
    rec["devtools"] = replies
    for a in anchors:
        href = str(a.get("href", ""))
        scheme = href.split(":", 1)[0]
        read = (
            await page.evaluate(_READ_HREF_JS, href)
            if scheme in ("blob", "data")
            else {"ok": False, "error": "scheme not read"}
        )
        rec["anchors"].append(
            {"scheme": scheme, "download": a.get("download"), "href_len": len(href), "read": read}
        )
    return rec


async def _run(profile: str, runs: int) -> list[dict[str, Any]]:
    rows = list_images(
        db_path=get_settings().resolved_db_path(), profile=profile, limit=20, offset=0
    )
    usable = [r for r in rows if r.project_id]
    if not usable:
        raise SystemExit(f"no catalogued images for profile {profile!r}")
    row = usable[0]
    assert row.project_id is not None
    step("target", f"project={row.project_id} media={row.media_id}")
    results: list[dict[str, Any]] = []
    async with build_client(resolve_profile_dir(profile)) as client:
        page = await client._checkout_page()  # pyright: ignore[reportPrivateUsage]
        try:
            for i in range(1, runs + 1):
                try:
                    rec = await _one_run(page, row.project_id, row.media_id, i)
                except Exception as exc:  # noqa: BLE001 - a failed run is still a row
                    rec = {"run": i, "run_error": f"{type(exc).__name__}: {exc}"[:300]}
                results.append(rec)
                step(f"run {i}", json.dumps(rec)[:400])
        finally:
            client._checkin_page(page)  # pyright: ignore[reportPrivateUsage]
    return results


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("profile")
    ap.add_argument("--runs", type=int, default=10)
    args = ap.parse_args()
    results = asyncio.run(_run(args.profile, args.runs))
    out = default_out_path("spike_upscale_lost_body")
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    lost = sum(1 for r in results for d in r.get("devtools") or [] if d.get("read") == "error")
    step("done", f"{len(results)} runs, {lost} lost bodies -> {out}")


if __name__ == "__main__":
    main()
