r"""Capture and decode the ogiZ0b f.req payload to see the full positional array structure.

This script opens a browser, navigates to a project, types a prompt, clicks Generate,
and route-aborts the ogiZ0b request ($0). It then decodes and prints the positional
array structure with slot labels.

Cost: $0 (route-aborted before Flow acts).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

_ORIGIN = "https://flow.google.com"
_PROMPT = "test capture prompt watercolor"


def _decode_freq(body: str) -> object:
    """Decode f.req from a URL-encoded batchexecute body."""
    for part in body.split("&"):
        if part.startswith("f.req="):
            raw = unquote(part[6:])
            return json.loads(raw)
    return None


def _truncate(val: object, maxlen: int = 80) -> str:
    s = json.dumps(val, ensure_ascii=False, default=str)
    if len(s) <= maxlen:
        return s
    return s[:maxlen] + f"...({len(s)} chars)"


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="denon82")
    parser.add_argument("--project", default=None)
    args = parser.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    captured: dict[str, str] = {}

    async with build_client(profile_dir, headless=False) as client:
        page = client._page  # noqa: SLF001
        if not page:
            step("ABORT", "no page")
            return

        # Discover project
        project_id = args.project
        if not project_id:
            await page.goto(f"{_ORIGIN}/", wait_until="networkidle", timeout=30_000)
            await asyncio.sleep(2)
            href = await page.locator('a[href*="/project/"]').first.get_attribute("href", timeout=10_000)
            if href and "/project/" in href:
                project_id = href.split("/project/", 1)[1].split("/")[0].split("?")[0]
            else:
                step("ABORT", "no project found")
                return
        step("PROJECT", project_id)

        # Navigate to project
        await page.goto(f"{_ORIGIN}/project/{project_id}", wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(3)

        # Set up route intercept for ogiZ0b
        async def on_route(route):  # type: ignore[no-untyped-def]
            url = route.request.url
            if "batchexecute" in url and "ogiZ0b" in url:
                body = route.request.post_data or ""
                captured["body"] = body
                captured["url"] = url
                step("CAPTURED", f"ogiZ0b request ({len(body)} bytes)")
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", on_route)

        # Type prompt into the contenteditable editor
        step("INPUT", "typing prompt into editor...")
        editor = page.locator('[contenteditable="true"]').first
        await editor.wait_for(state="visible", timeout=15_000)
        await editor.click(timeout=10_000)
        await asyncio.sleep(0.5)
        await page.keyboard.type(_PROMPT, delay=20)
        await asyncio.sleep(2)

        # Click the Generate button (arrow_forward ligature)
        step("INPUT", "clicking Generate (arrow_forward)...")
        gen_btn = page.locator("button").filter(
            has=page.locator("mat-icon").filter(has_text=re.compile(r"^arrow_forward$"))
        ).first
        await gen_btn.wait_for(state="visible", timeout=10_000)
        await gen_btn.click(timeout=5_000)

        # Wait for capture
        for _ in range(30):
            if "body" in captured:
                break
            await asyncio.sleep(1)

        await page.unroute("**/*", on_route)

        if "body" not in captured:
            step("ABORT", "no ogiZ0b request captured in 30s")
            return

        # Decode the f.req
        freq = _decode_freq(captured["body"])
        if not freq:
            step("ABORT", "could not decode f.req")
            return

        # The structure is [[[rpcid, inner_json, None, "generic"]]]
        outer = freq
        if isinstance(outer, list) and outer and isinstance(outer[0], list) and outer[0]:
            rpc_entry = outer[0][0]
            rpcid = rpc_entry[0]
            inner_json = rpc_entry[1]
            step("RPC", f"rpcid={rpcid}")

            inner = json.loads(inner_json)
            step("STRUCTURE", f"inner top-level: {len(inner)} slots")

            # Print top-level slots
            for i, slot in enumerate(inner):
                step(f"inner[{i}]", _truncate(slot, 120))

            # The entries are at inner[1]
            if isinstance(inner, list) and len(inner) > 1 and isinstance(inner[1], list):
                entries = inner[1]
                step("ENTRIES", f"{len(entries)} entry(ies)")
                for ei, entry in enumerate(entries):
                    if isinstance(entry, list):
                        step(f"entry[{ei}]", f"{len(entry)} slots")
                        for si, slot in enumerate(entry):
                            label = ""
                            if isinstance(slot, str) and len(slot) > 100:
                                label = " ← LIKELY TOKEN"
                            elif isinstance(slot, str) and slot in ("BELUGA", "NARWHAL", "GEM_PIX_2", "HARBOR_SEAL"):
                                label = " ← MODEL"
                            elif isinstance(slot, list) and slot and isinstance(slot[0], list):
                                label = " ← NESTED ARRAY"
                            step(f"  [{si}]", f"{_truncate(slot, 100)}{label}")

            # Save the full decoded structure
            out_path = default_out_path("capture_ogiz0b_structure", ".json")
            with open(out_path, "w") as f:
                json.dump({"inner": inner, "body_len": len(captured["body"])}, f, indent=2, default=str)
            step("SAVED", str(out_path))

        # Also extract the at token
        at_part = [p for p in captured["body"].split("&") if p.startswith("at=")]
        if at_part:
            at_val = unquote(at_part[0][3:])
            step("AT", f"at token: {at_val[:20]}...({len(at_val)} chars)")


if __name__ == "__main__":
    asyncio.run(main())
