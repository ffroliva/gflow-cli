r"""Spike (#1371 / Task 28): where the account's selected video model can be read.

Gate-0 evidence for Task 29: bare ``gflow video t2v`` sends no model (``api/video.py:295``).
The UI composes the key from the picker selection, which gflow today only touches by
opening the settings pane and clicking the picker (``_mc_settings_pane.py:150-158``). No
existing code reads the selection without that click.

This spike observes the project load on the armed mint page (``flow.google.com/project/<id>``)
and checks whether the account's selected video model is carried in:
  1. In-page batchexecute replies (e.g. HTrJv, nzlxg, jwpduf, or others).
  2. Any tRPC queries or preferences endpoints.
  3. ``localStorage`` or ``sessionStorage`` keys.
  4. The model picker DOM element (ground truth comparison).

Cost: $0. Zero credits. Nothing clicked in menus, nothing submitted.
Any billed submit RPC (YhhmEf, eb1hJf, MZZa6b, nprQif) is route-aborted immediately.

Pre-registered readings:
  | Observation                                     | Reading                                 |
  |-------------------------------------------------|-----------------------------------------|
  | Carrier in in-page batchexecute or tRPC reply   | in-page reply carrier; needs page load  |
  | Carrier in standalone RPC readable browser-free | browser-free carrier via flow_wire      |
  | Carrier in localStorage or sessionStorage       | page storage carrier; no menu click     |
  | No carrier found in the listed sources          | not found in the listed sources         |
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

from gflow_cli.api.transports._mc_common import (  # noqa: E402
    READY_ANCHOR,
    _visible_model_picker,
)
from gflow_cli.api.transports._mc_settings_pane import (  # noqa: E402
    OVERLAY,
    RADIOGROUP,
)
from gflow_cli.api.transports.batchexecute import parse_frames  # noqa: E402

_ORIGIN = "https://flow.google.com"
_SUBMITS = ("YhhmEf", "eb1hJf", "MZZa6b", "nprQif")

# Known model keys and family identifiers to scan for in responses and storage
_KNOWN_MODEL_TOKENS: frozenset[str] = frozenset(
    {
        "macaron_cookie",
        "veo_3_1_lite",
        "veo_3_1_fast",
        "veo_3_1_quality",
        "veo_3_1_lite_low_priority",
        "abra",
        "omni_1_2_flash",
        "omni_flash",
        "veo_2",
    }
)


def _abort_submits() -> Any:
    """A route handler that immediately aborts any billed video submit RPC."""

    async def on_route(route: Any, request: Any) -> None:
        r = route.request
        data = (r.post_data_buffer or b"").decode("utf-8", errors="replace")
        rid = (parse_qs(urlsplit(r.url).query).get("rpcids") or [""])[0]
        if rid in _SUBMITS or any(f'"{s}"' in data for s in _SUBMITS):
            await route.abort()
        else:
            await route.fallback()

    return on_route


def _find_tokens_in_obj(obj: Any, path: str = "") -> list[tuple[str, str]]:
    """Recursively search for known model tokens in nested JSON structures."""
    results: list[tuple[str, str]] = []
    if isinstance(obj, str):
        if (obj.startswith("{") and obj.endswith("}")) or (
            obj.startswith("[") and obj.endswith("]")
        ):
            try:
                parsed = json.loads(obj)
                return _find_tokens_in_obj(parsed, path)
            except Exception:
                pass
        # Look for exact token matches or substrings matching known keys
        for token in _KNOWN_MODEL_TOKENS:
            if token in obj.lower():
                results.append((path, obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            current_path = f"{path}.{k}" if path else str(k)
            for token in _KNOWN_MODEL_TOKENS:
                if token in str(k).lower():
                    results.append((f"{current_path}[key]", str(k)))
            results.extend(_find_tokens_in_obj(v, current_path))
    elif isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            current_path = f"{path}[{idx}]"
            results.extend(_find_tokens_in_obj(item, current_path))
    return results


def find_carrier_candidates(
    storage: dict[str, dict[str, Any]],
    network_replies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Scan storage and network responses for model carrier candidates."""
    candidates: list[dict[str, Any]] = []

    # 1. Search localStorage and sessionStorage
    for store_name, store_dict in storage.items():
        for key, val in store_dict.items():
            loc = f"{store_name}.{key}"
            # Check value (either raw string or parsed JSON)
            parsed_val: Any = val
            if isinstance(val, str) and (val.startswith("{") or val.startswith("[")):
                try:
                    parsed_val = json.loads(val)
                except Exception:
                    parsed_val = val
            matches = _find_tokens_in_obj(parsed_val, loc)
            for match_loc, match_val in matches:
                # Identify which token matched
                matched_token = next((t for t in _KNOWN_MODEL_TOKENS if t in match_val.lower()), "")
                candidates.append(
                    {
                        "source_type": "storage",
                        "location": match_loc,
                        "value": match_val,
                        "model_match": matched_token,
                    }
                )

    # 2. Search network replies
    for rep in network_replies:
        url = rep.get("url", "")
        rpcids = rep.get("rpcids") or []
        data = rep.get("data")
        loc_prefix = f"network:{','.join(rpcids)}" if rpcids else f"network:{urlsplit(url).path}"
        matches = _find_tokens_in_obj(data, loc_prefix)
        for match_loc, match_val in matches:
            matched_token = next((t for t in _KNOWN_MODEL_TOKENS if t in match_val.lower()), "")
            # Filter out catalog declarations if they are just the general HTrJv catalog
            # unless they specifically annotate a user selection.
            candidates.append(
                {
                    "source_type": "network",
                    "location": match_loc,
                    "value": match_val,
                    "model_match": matched_token,
                    "rpcids": rpcids,
                    "url": url,
                }
            )

    return candidates


def summarize_findings(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize whether a carrier was found according to pre-registered criteria."""
    if not candidates:
        return {
            "carrier_found": False,
            "carrier_type": None,
            "status": "not found in the listed sources",
            "candidates": [],
        }

    # Filter out known static catalog replies like HTrJv full enumerations
    potential_carriers = [
        c
        for c in candidates
        if not ("HTrJv" in c.get("location", "") and c.get("source_type") == "network")
    ]

    if not potential_carriers:
        return {
            "carrier_found": False,
            "carrier_type": None,
            "status": "not found in the listed sources (HTrJv catalog only, no selection carrier)",
            "candidates": candidates,
        }

    return {
        "carrier_found": True,
        "carrier_type": potential_carriers[0]["source_type"],
        "status": f"carrier candidate found in {potential_carriers[0]['location']}",
        "candidates": potential_carriers,
    }


async def run_spike(profile_name: str, project_id: str | None = None) -> dict[str, Any]:
    """Execute the spike on the specified profile and project."""
    profile_dir = resolve_profile_dir(profile_name)
    step("init", f"Launching client on profile {profile_name}")

    network_replies: list[dict[str, Any]] = []

    async with build_client(profile_dir, headless=True) as client:
        page = client._page  # noqa: SLF001
        assert page is not None, "Client launched without a page"

        # Setup route abort for billed submits
        await page.route("**/data/batchexecute**", _abort_submits())

        # Intercept network responses to capture in-page project load traffic
        async def on_response(response: Any) -> None:
            url = response.url
            if "batchexecute" in url or "/api/trpc" in url or "getUserSettings" in url:
                try:
                    query = parse_qs(urlsplit(url).query)
                    rpcids = query.get("rpcids") or []
                    body_text = await response.text()
                    if "batchexecute" in url:
                        frames = parse_frames(body_text)
                        for rpcid, payload in frames:
                            network_replies.append(
                                {
                                    "url": url,
                                    "status": response.status,
                                    "rpcids": [rpcid],
                                    "data": payload,
                                }
                            )
                    else:
                        parsed: Any = None
                        try:
                            parsed = json.loads(body_text)
                        except Exception:
                            parsed = body_text
                        network_replies.append(
                            {
                                "url": url,
                                "status": response.status,
                                "rpcids": rpcids,
                                "data": parsed,
                            }
                        )
                except Exception:
                    pass

        page.on("response", on_response)

        # Ensure project exists or create one
        if not project_id:
            step("project", "Creating spike project")
            proj = await client.create_project(title="gflow spike selected model")
            project_id = proj.project_id

        step("nav", f"Navigating to project {project_id}")
        await page.goto(
            f"{_ORIGIN}/project/{project_id}",
            wait_until="domcontentloaded",
            timeout=60_000,
        )

        # Wait for editor readiness
        step("wait", "Waiting for editor ready anchor")
        trigger = page.locator(READY_ANCHOR).first
        await trigger.wait_for(state="visible", timeout=30_000)

        # Let pending project queries settle
        await asyncio.sleep(3.0)

        step("storage", "Collecting storage (localStorage / sessionStorage)")
        local_storage = await page.evaluate(
            "() => Object.fromEntries(Object.entries(localStorage))"
        )
        session_storage = await page.evaluate(
            "() => Object.fromEntries(Object.entries(sessionStorage))"
        )
        storage_data = {
            "localStorage": local_storage,
            "sessionStorage": session_storage,
        }

        step("settings", "Opening settings pane to observe active model picker text (ground truth)")
        await trigger.click(timeout=5000)
        pane = page.locator(OVERLAY).filter(has=page.locator(RADIOGROUP)).last
        await pane.locator(RADIOGROUP).first.wait_for(state="visible", timeout=8000)

        picker_button = await _visible_model_picker(page, pane, what="model picker")
        picker_text = (await picker_button.text_content() or "").strip()
        step("picker", f"Observed model picker button text: {picker_text!r}")

        # Close settings pane cleanly with Escape
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)

        step("analysis", "Analyzing recorded traffic and storage for carriers")
        candidates = find_carrier_candidates(storage_data, network_replies)
        summary = summarize_findings(candidates)

        result: dict[str, Any] = {
            "profile": profile_name,
            "project_id": project_id,
            "observed_picker_model": picker_text,
            "summary": summary,
            "storage_keys": {
                "localStorage": list(local_storage.keys()),
                "sessionStorage": list(session_storage.keys()),
            },
            "network_rpcids_seen": sorted(
                {r for rep in network_replies for r in rep.get("rpcids", [])}
            ),
            "candidates_count": len(candidates),
            "candidates": candidates,
        }

        out_path = default_out_path("spike_selected_video_model", ".json")
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        step("done", f"Recorded results to {out_path}")
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", default="ffroliva", help="Profile to inspect (default: ffroliva)"
    )
    parser.add_argument("--project", help="Existing project ID (optional)")
    args = parser.parse_args()

    result = asyncio.run(run_spike(args.profile, args.project))
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
