r"""Capture the migrated composer's image submit/result wire (#639).

The default run aborts the marker-bearing submit before it leaves Chrome, so it costs
nothing. ``--spend`` lets Flow own the request and records the resulting batchexecute
traffic; it consumes image quota. ``--ref PATH`` uploads and mentions a local image before
the submit so the I2I body can be compared with T2I.

Raw output is gitignored and aggressively redacted: no cookies, auth headers, reCAPTCHA
tokens, or signed URL query strings are written.
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
from urllib.parse import parse_qs, unquote_plus, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

from gflow_cli.api.transports.batchexecute import parse_frames  # noqa: E402
from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    RADIOGROUP,
    MigratedComposer,
    _ligature,
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{120,}")
_SIGNED_URL_RE = re.compile(r"(https://[^\s\"\\]+)\?[^\s\"\\]+")
_AT_RE = re.compile(r"([?&]at=)[^&\s]+")
_UUID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)


class ProbeFailedError(RuntimeError):
    """The surface was not reached; never reinterpret this as an absence."""


def _url_facts(url: str) -> dict[str, Any]:
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    return {
        "host": parsed.hostname,
        "path": parsed.path,
        "rpcids": query.get("rpcids", [None])[0],
    }


def _redact(text: str, *, limit: int = 8000) -> str:
    decoded = unquote_plus(text)
    decoded = _TOKEN_RE.sub(lambda m: f"<{len(m.group(0))}-char-token>", decoded)
    decoded = _SIGNED_URL_RE.sub(r"\1?<signed>", decoded)
    decoded = _AT_RE.sub(r"\1<at>", decoded)
    return decoded[:limit]


def _https_urls(node: object) -> list[str]:
    if isinstance(node, str):
        return [node] if node.startswith("https://") else []
    if isinstance(node, list):
        return [url for item in node for url in _https_urls(item)]
    return []


async def _groups(pane: Any) -> list[list[dict[str, Any]]]:
    return await pane.locator(RADIOGROUP).evaluate_all(
        """groups => groups.map(g => [...g.querySelectorAll('[role=radio]')].map(r => ({
          text: (r.textContent || '').replace(/\\s+/g, ' ').trim(),
          checked: r.getAttribute('aria-checked'),
          icons: [...r.querySelectorAll('mat-icon')].map(i => (i.textContent || '').trim())
        })))"""
    )


async def _run(profile: str, project: str, ref: Path | None, spend: bool) -> int:
    marker = "gflowimagecanary" + uuid.uuid4().hex[:10]
    prompt = f"a cobalt ceramic cup on a plain white table {marker}"
    report: dict[str, Any] = {
        "profile": profile,
        "project": project[:8] + "...",
        "mode": "real" if spend else "aborted",
        "kind": "i2i" if ref else "t2i",
        "marker": marker,
        "requests": [],
        "responses": [],
    }
    t0 = time.monotonic()
    marker_seen = asyncio.Event()
    signed_image_urls: list[str] = []

    def rel() -> float:
        return round(time.monotonic() - t0, 2)

    async with build_client(resolve_profile_dir(profile)) as client:
        page = await client._context.new_page()  # noqa: SLF001 - deliberate spike
        composer = MigratedComposer()

        async def on_route(route: Any) -> None:
            req = route.request
            body = str(req.post_data or "")
            facts = _url_facts(req.url)
            if "batchexecute" in req.url:
                report["requests"].append(
                    {
                        "t": rel(),
                        **facts,
                        "body_len": len(body),
                        "marker": marker in unquote_plus(body),
                        "uuid_sample": _UUID_RE.findall(unquote_plus(body))[:8],
                        "body": _redact(body, limit=5000),
                    }
                )
            if marker in unquote_plus(body):
                marker_seen.set()
                step("submit", f"rpc={facts['rpcids']} {'continued' if spend else 'aborted'}")
                if not spend:
                    await route.abort()
                    return
            await route.continue_()

        async def on_response(response: Any) -> None:
            if "batchexecute" not in response.url:
                return
            try:
                body = await response.text()
            except Exception:  # noqa: BLE001 - an aborted response is expected in dry mode
                return
            for _, payload in parse_frames(body):
                signed_image_urls.extend(
                    url
                    for url in _https_urls(payload)
                    if urlsplit(url).hostname == "flow-content.google"
                    and "/image/" in urlsplit(url).path
                )
            report["responses"].append(
                {
                    "t": rel(),
                    **_url_facts(response.url),
                    "status": response.status,
                    "body_len": len(body),
                    "uuid_sample": _UUID_RE.findall(body)[:8],
                    "body": _redact(body),
                }
            )

        await page.route("**/batchexecute**", on_route)
        page.on("response", on_response)
        try:
            # This account persists Agent mode. Collapse its expanded chat panel, then
            # switch the locale-independent aria-pressed control off so the classic
            # settings trigger can become visible. Presence of the hidden trigger is
            # not editor readiness.
            await page.goto(
                f"https://flow.google.com/project/{project}",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(3_000)
            chat_close = page.locator(
                "flow-agent-panel button:has(mat-icon:text-is('close'))"
            ).first
            if await chat_close.count() and await chat_close.is_visible():
                await chat_close.click(timeout=5_000)
            agent = page.locator("button.agent-mode-chip").first
            if await agent.count() and await agent.get_attribute("aria-pressed") == "true":
                await agent.click(timeout=5_000)
                await page.wait_for_timeout(500)
            await composer.ensure_editor(page, project)
            pane = await composer._open_pane(page)  # noqa: SLF001 - spike drives exact seam
            await composer._select(page, pane, axis="mode", lig="image")  # noqa: SLF001
            await asyncio.sleep(0.8)
            report["settings_groups"] = await _groups(pane)
            report["model_buttons"] = [
                t.strip()
                for t in await pane.locator("button")
                .filter(has=_ligature(page, "arrow_drop_down"))
                .all_text_contents()
            ]
            # Count is a persisted billing axis. Pin x1 even for the spike.
            await composer._select(page, pane, axis="count", text="x1")  # noqa: SLF001
            await composer._close_pane(page, strict=True)  # noqa: SLF001

            reference_ids: tuple[str, ...] = ()
            if ref is not None:
                reference_ids = await composer.attach_references(page, project, (ref,))
                report["reference_ids"] = list(reference_ids)

            await composer.send_prompt(page, prompt, append=bool(ref))
            chips = await composer.read_chips(page)
            report["chips"] = chips
            if ref is not None and not chips:
                raise ProbeFailedError("reference upload completed but no mention chip was bound")

            submit = page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
            await submit.wait_for(state="visible", timeout=10_000)
            deadline = time.monotonic() + 5
            while not await submit.is_enabled() and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            if not await submit.is_enabled():
                raise ProbeFailedError("image submit stayed disabled after the prompt was typed")
            await submit.click(timeout=5000)
            try:
                await asyncio.wait_for(marker_seen.wait(), timeout=30)
            except TimeoutError as exc:
                raise ProbeFailedError("no marker-bearing image request was observed") from exc

            if spend:
                # Let Flow's own polling finish. The response log is the measurement;
                # no guessed rpcid or record parser decides when this window ends.
                await page.wait_for_timeout(120_000)
                report["dom_after"] = await page.evaluate(
                    """() => ({
                      images: [...document.querySelectorAll('flow-image-tile img')].map(i => ({
                        src: (i.currentSrc || i.src || '').split('?')[0],
                        w: i.naturalWidth, h: i.naturalHeight
                      })).slice(0, 12),
                      composer: document.querySelectorAll("[contenteditable='true']").length
                    })"""
                )
                report["downloads"] = []
                for url in dict.fromkeys(signed_image_urls):
                    response = await page.request.get(url, timeout=120_000, max_redirects=0)
                    body = await response.body()
                    report["downloads"].append(
                        {
                            "host": urlsplit(url).hostname,
                            "path": urlsplit(url).path,
                            "status": response.status,
                            "bytes": len(body),
                            "magic": body[:12].hex(),
                            "is_image": body.startswith(b"\x89PNG\r\n\x1a\n")
                            or body.startswith(b"\xff\xd8\xff")
                            or (body.startswith(b"RIFF") and body[8:12] == b"WEBP"),
                        }
                    )
            report["reached"] = {
                "editor": True,
                "image_mode": True,
                "submit": True,
                "result_window": spend,
            }
        finally:
            page.remove_listener("response", on_response)
            await page.unroute("**/batchexecute**", on_route)
            out = default_out_path(f"migrated_image_submit_{'i2i' if ref else 't2i'}")
            out.write_text(json.dumps(report, indent=2), encoding="utf-8")
            step("wrote", str(out))
            await page.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--ref", type=Path)
    parser.add_argument("--spend", action="store_true")
    args = parser.parse_args()
    if args.ref is not None and not args.ref.is_file():
        parser.error(f"--ref is not a file: {args.ref}")
    try:
        return asyncio.run(_run(args.profile, args.project, args.ref, args.spend))
    except ProbeFailedError as exc:
        print(f"[spike] PROBE FAILED: {exc}", file=sys.stderr)
        print("[spike] This is a failed measurement, NOT evidence of absence.", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
