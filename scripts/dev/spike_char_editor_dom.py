#!/usr/bin/env python3
r"""T-A spike — capture the character-editor DOM (0 credits).

Purpose: navigate to the Flow character editor page and dump the interactive
DOM so we can derive the editor-ready anchor selector and the low-confidence
slot-add selector needed for Phase 2 UI automation.

The JS dumper (``dump_character_selectors.js``) is read from disk and
evaluated in-page via ``page.evaluate()``, matching the same snippet used
manually via Chrome DevTools Console (see scripts/dev/dump_character_selectors.js).
Both the JSON selector dump and the raw ``page.content()`` (outerHTML) are
written to ``--out``.

Credit cost: 0  (DOM navigation only, no generation).

Usage example:

    ! .venv\Scripts\python.exe scripts\dev\spike_char_editor_dom.py \
        --profile denon82 --project <existing-project-uuid>

    # With an existing entity (skip REST create):
    ! .venv\Scripts\python.exe scripts\dev\spike_char_editor_dom.py \
        --profile denon82 --project <pid> --entity <eid>

Outputs go to scripts/dev/_spike_out/ (gitignored) or OS temp dir.
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

# ---------------------------------------------------------------------------
# sys.path bootstrap (must come before gflow_cli imports)
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Import shared helpers from sibling _spike_common
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.client import FlowApiClient  # noqa: E402


def _unwrap_trpc(data: Any) -> dict[str, Any]:
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        msg = f"unexpected tRPC reply shape: {type(data).__name__}"
        raise ValueError(msg)
    result = data.get("result", {})
    inner = result.get("data", {}) if isinstance(result, dict) else {}
    payload = inner.get("json", inner) if isinstance(inner, dict) else {}
    if not isinstance(payload, dict):
        msg = "tRPC reply missing result.data.json object"
        raise ValueError(msg)
    return payload


async def _create_entity(client: FlowApiClient, project_id: str) -> str:
    """Mint a fresh throwaway CHARACTER entity. FREE (no reCAPTCHA/credit)."""
    body = {"json": {"projectId": project_id}}
    data = await client._post_json(  # noqa: SLF001
        routes.CREATE_ENTITY_URL,
        body,
        content_type="application/json",
        route_name="createEntity",
    )
    payload = _unwrap_trpc(data)
    entity_id = payload.get("entityId")
    if not entity_id:
        msg = f"createEntity returned no entityId; keys={sorted(payload)}"
        raise ValueError(msg)
    step("0 OK", f"minted entityId={entity_id}", prefix="T-A")
    return str(entity_id)


async def _run(
    *,
    profile_dir: Path,
    headless: bool,
    project_id: str,
    entity_id: str | None,
    locale: str,
    out_path: Path,
) -> int:
    # Load the JS dumper snippet from disk (avoids duplication / drift).
    js_file = Path(__file__).resolve().parent / "dump_character_selectors.js"
    if not js_file.exists():
        print(
            f"[T-A] ERROR: JS dumper not found at {js_file}. "
            "Ensure dump_character_selectors.js is present in scripts/dev/.",
            file=sys.stderr,
        )
        return 2

    # The JS snippet uses document.createElement('a').click() to trigger a
    # download — that is browser-only behaviour.  For programmatic use we strip
    # the download trigger and return the payload object directly.
    raw_js = js_file.read_text(encoding="utf-8")
    # Wrap the IIFE so it RETURNS the payload instead of triggering a download.
    evaluate_js = raw_js.replace(
        "const blob = new Blob",
        "return payload;\n  const _unused_blob = new Blob",  # short-circuit after return
    )

    async with build_client(profile_dir, headless=headless) as client:
        # Create a throwaway entity if none was provided.
        if not entity_id:
            step("0", "no --entity provided, creating throwaway entity…", prefix="T-A")
            entity_id = await _create_entity(client, project_id)
        else:
            step("0 SKIP", f"using provided entity={entity_id}", prefix="T-A")

        # Navigate the pooled page to the character editor.
        # We check out a page from the pool (like other spikes) via the client's
        # internal page pool.  FlowApiClient.__aenter__ has already warmed the pool.
        page = await client._checkout_page()  # noqa: SLF001
        try:
            editor_url = routes.character_editor_url(locale, project_id, entity_id)
            step("1", f"navigating to {editor_url}", prefix="T-A")
            await page.goto(editor_url, wait_until="domcontentloaded", timeout=30_000)
            # Wait for the editor to partially mount before dumping.
            await page.wait_for_timeout(3_000)

            current_url = page.url
            step("2", f"page landed at {current_url}", prefix="T-A")

            # Run the selector dumper and capture the returned payload object.
            selector_data: Any = await page.evaluate(f"(() => {{ {evaluate_js} }})()")

            # Also capture raw outerHTML for deeper manual analysis.
            outer_html: str = await page.content()
        finally:
            client._checkin_page(page)  # noqa: SLF001

    # Write outputs.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html_path = out_path.with_suffix(".html")

    result = {
        "spike": "T-A",
        "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "entityId": entity_id,
        "projectId": project_id,
        "locale": locale,
        "editorUrl": routes.character_editor_url(locale, project_id, entity_id),
        "landedUrl": current_url,
        "selectorDump": selector_data,
    }
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    html_path.write_text(outer_html, encoding="utf-8")

    print(f"[T-A] selector JSON  -> {out_path}", flush=True)
    print(f"[T-A] outerHTML      -> {html_path}", flush=True)
    print(f"[T-A] entityId       = {entity_id}", flush=True)
    element_count: Any = (
        (selector_data or {}).get("count", "?") if isinstance(selector_data, dict) else "?"
    )
    print(f"[T-A] elements found = {element_count}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="T-A spike: capture Flow character-editor DOM (0 credits)."
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("GFLOW_CLI_PROFILE", "denon82"),
        help="Chrome-strategy profile name. Default: denon82 / $GFLOW_CLI_PROFILE.",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Existing Flow project UUID.",
    )
    parser.add_argument(
        "--locale",
        default="pt",
        help="Flow UI locale (default: pt).",
    )
    parser.add_argument(
        "--entity",
        default=None,
        help="Existing entity UUID. If omitted, a fresh throwaway entity is created.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON path. Default: scripts/dev/_spike_out/spike_char_editor_dom_<ts>.json",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless (default: headed).",
    )
    args = parser.parse_args(argv)

    profile_dir = resolve_profile_dir(args.profile)
    out_path = Path(args.out) if args.out else default_out_path("spike_char_editor_dom", ".json")

    step(
        "--",
        f"profile={args.profile} project={args.project} locale={args.locale} "
        f"entity={args.entity or '(create)'} out={out_path}",
        prefix="T-A",
    )

    try:
        return asyncio.run(
            _run(
                profile_dir=profile_dir,
                headless=args.headless,
                project_id=args.project,
                entity_id=args.entity,
                locale=args.locale,
                out_path=out_path,
            )
        )
    except KeyboardInterrupt:
        print("[T-A] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
