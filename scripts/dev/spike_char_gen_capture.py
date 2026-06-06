#!/usr/bin/env python3
r"""T-B spike — passive-capture batchGenerateImages WITH entityContext (~1 CREDIT).

Purpose: navigate to the Flow character editor, attach a passive response
listener, submit a face prompt, and capture the raw ``batchGenerateImages``
response to prove:

  workflows[0].parentEntityId == entityId   (Option-B binding assumption)

The captured response is sanitised (signed URLs / tokens redacted) and written
to ``--out``.  The intended destination of a passing capture is:

  tests/api/fixtures/character_gen_response.json

Credit cost: ~1 credit (one image generation). Everything else is free.

SAFETY GATE: pass ``--yes`` to actually submit.  Without it the script exits 0
after printing a dry-run message.

Usage example:

    ! .venv\Scripts\python.exe scripts\dev\spike_char_gen_capture.py \
        --profile denon82 --project <pid> \
        --face-prompt "portrait of a fictional adult" --yes

    # With an existing entity:
    ! .venv\Scripts\python.exe scripts\dev\spike_char_gen_capture.py \
        --profile denon82 --project <pid> --entity <eid> \
        --face-prompt "portrait of a fictional adult" --yes

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
# sys.path bootstrap
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402
from playwright.async_api import Page  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.client import FlowApiClient  # noqa: E402
from gflow_cli.api.transports.ui_automation import (  # noqa: E402
    PROMPT_INPUT_SELECTORS,
    SUBMIT_BUTTON_SELECTORS,
    UiAutomationTransport,
)
from gflow_cli.data.redaction import redact_metadata  # noqa: E402

# ---------------------------------------------------------------------------
# Minimal prompt-submit helper (avoids needing a full UiAutomationTransport
# instance — _send_prompt is an instance method with no spike-relevant state).
# Mirrors the essential keyboard path from ui_automation._send_prompt.
# ---------------------------------------------------------------------------


async def _submit_prompt(page: Page, prompt_text: str) -> None:
    """Type *prompt_text* into the Flow editor and click submit."""
    input_box = None
    for selector in PROMPT_INPUT_SELECTORS:
        try:
            loc = page.locator(selector).first
            await loc.wait_for(state="visible", timeout=10_000)
            input_box = loc
            break
        except Exception:
            continue

    if input_box is None:
        msg = f"Prompt input not found. URL: {page.url}"
        raise RuntimeError(msg)

    await input_box.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Delete")
    await page.keyboard.insert_text(prompt_text)
    await page.wait_for_timeout(500)

    for sel in SUBMIT_BUTTON_SELECTORS:
        try:
            btn = page.locator(sel).first
            await btn.wait_for(state="visible", timeout=2_000)
            await btn.click()
            return
        except Exception:
            continue

    await page.keyboard.press("Enter")


# ---------------------------------------------------------------------------
# tRPC helper (reused from character_create_spike_v2)
# ---------------------------------------------------------------------------


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
    step("0 OK", f"minted entityId={entity_id}", prefix="T-B")
    return str(entity_id)


async def _run(
    *,
    profile_dir: Path,
    headless: bool,
    project_id: str,
    entity_id: str | None,
    locale: str,
    face_prompt: str,
    out_path: Path,
) -> int:
    async with build_client(profile_dir, headless=headless) as client:
        # Step 0 — ensure we have an entity id.
        if not entity_id:
            step("0", "no --entity provided, creating throwaway entity…", prefix="T-B")
            entity_id = await _create_entity(client, project_id)
        else:
            step("0 SKIP", f"using provided entity={entity_id}", prefix="T-B")

        page = await client._checkout_page()  # noqa: SLF001
        try:
            # Step 1 — navigate to character editor.
            editor_url = routes.character_editor_url(locale, project_id, entity_id)
            step("1", f"navigating to {editor_url}", prefix="T-B")
            await page.goto(editor_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(3_000)
            step("1 OK", f"page at {page.url}", prefix="T-B")

            # Step 2 — attach listener BEFORE submit (eliminates race).
            captured, detach = UiAutomationTransport._attach_batch_response_listener(  # noqa: SLF001
                page,
                project_id=project_id,
            )
            step("2 OK", "batchGenerateImages listener attached", prefix="T-B")

            # Step 3 — submit face prompt via UI (passive capture path).
            # _send_prompt is an instance method on UiAutomationTransport; for this
            # standalone spike we replicate its minimal keyboard path directly so we
            # avoid constructing a full transport instance.
            submit_time = time.monotonic()
            step("3", f"submitting face prompt: {face_prompt!r}", prefix="T-B")
            await _submit_prompt(page, face_prompt)
            step("3 OK", "prompt submitted; waiting for batchGenerateImages…", prefix="T-B")

            # Step 4 — wait for exactly one response (the face-slot generation).
            try:
                results = await UiAutomationTransport._await_captured(  # noqa: SLF001
                    captured,
                    timeout_s=180.0,
                    expected_count=1,
                    submit_time=submit_time,
                )
            finally:
                detach()

            step("4 OK", f"captured {len(results)} response(s)", prefix="T-B")
        finally:
            client._checkin_page(page)  # noqa: SLF001

    if not results:
        print("[T-B] FAIL  no batchGenerateImages response captured (timeout)", flush=True)
        return 1

    raw = results[0]
    body: Any = raw.get("body", raw)

    # Step 5 — assert Option-B binding.
    workflows: list[Any] = body.get("workflows", []) if isinstance(body, dict) else []
    wf0: dict[str, Any] = workflows[0] if workflows and isinstance(workflows[0], dict) else {}
    returned_parent = wf0.get("parentEntityId")
    returned_entity = wf0.get("name", "")

    binding_ok = returned_parent == entity_id

    print("\n[T-B] --- Option-B entityContext binding assertions ---", flush=True)
    print(f"[T-B]   entity_id (requested)          = {entity_id}", flush=True)
    print(f"[T-B]   workflows[0].parentEntityId    = {returned_parent}", flush=True)
    print(f"[T-B]   workflows[0].name (workflowId) = {returned_entity}", flush=True)
    print(
        f"\n[T-B] {'PASS' if binding_ok else 'FAIL'}  parentEntityId == entity_id -> {binding_ok}",
        flush=True,
    )
    if not binding_ok:
        print(
            "[T-B] WARNING: Option-B passive-capture binding assumption may be WRONG.\n"
            "         The UI-driven generation did not stamp parentEntityId on workflows[0].\n"
            "         Do NOT copy this fixture to tests/; investigate the protocol first.",
            flush=True,
        )

    # Step 6 — redact and write fixture.
    redacted_body = redact_metadata(body)
    # Extra pass: redact any remaining signed-URL strings the generic walker may miss
    # (e.g. direct fifeUrl values that appear under non-standard key names).
    redacted_body = _deep_redact_signed_strings(redacted_body)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(redacted_body, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n[T-B] captured response written to:\n      {out_path}", flush=True)
    print(
        "[T-B] To use as a fixture:\n"
        f"      copy {out_path}\n"
        "            tests\\api\\fixtures\\character_gen_response.json",
        flush=True,
    )
    return 0 if binding_ok else 1


_SIGNED_URL_MARKERS = ("signature=", "x-goog-signature=", "expires=", "x-goog-credential=")


def _deep_redact_signed_strings(obj: Any) -> Any:
    """Extra pass: replace any string value that looks like a signed CDN URL."""
    if isinstance(obj, dict):
        return {k: _deep_redact_signed_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_redact_signed_strings(item) for item in obj]
    if isinstance(obj, str) and any(m in obj.lower() for m in _SIGNED_URL_MARKERS):
        return "<REDACTED_SIGNED_URL>"
    return obj


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="T-B spike: passive-capture batchGenerateImages with entityContext (~1 credit)."
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
        "--face-prompt",
        required=True,
        help="Face prompt text to submit in the character editor.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON path. Default: scripts/dev/_spike_out/spike_char_gen_capture_<ts>.json",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually submit (COSTS ~1 CREDIT). Without this flag, exit 0 after dry-run message.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless (default: headed).",
    )
    args = parser.parse_args(argv)

    if not args.yes:
        print(
            "[T-B] DRY RUN: would submit face prompt to Flow character editor (~1 credit).\n"
            "      Pass --yes to actually run and spend a credit.",
            flush=True,
        )
        return 0

    profile_dir = resolve_profile_dir(args.profile)
    out_path = Path(args.out) if args.out else default_out_path("spike_char_gen_capture", ".json")

    step(
        "--",
        f"profile={args.profile} project={args.project} locale={args.locale} "
        f"entity={args.entity or '(create)'} out={out_path}",
        prefix="T-B",
    )
    print("[T-B] NOTE: this run spends ~1 credit on the face image generation.", flush=True)

    try:
        return asyncio.run(
            _run(
                profile_dir=profile_dir,
                headless=args.headless,
                project_id=args.project,
                entity_id=args.entity,
                locale=args.locale,
                face_prompt=args.face_prompt,
                out_path=out_path,
            )
        )
    except KeyboardInterrupt:
        print("[T-B] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
