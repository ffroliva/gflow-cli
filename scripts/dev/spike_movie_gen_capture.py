#!/usr/bin/env python3
r"""Movie-consistency spike — Tier 2 (credits): capture the r2v dialogue wire.

Capture-harness + human-drive hybrid. Opens the Flow video composer headed and
attaches a network capture for the generation + entity/voice attach wire. YOU
perform the generation in the browser (attach character via Personagens →
"Incluir no comando", attach a voice via Vozes, type the dialogue line, click
generate, solve reCAPTCHA). The script records the request/response bodies so we
can verify whether `referenceEntities` / `audioReferences` ride the wire and how
voice is carried. The result clip lands in YOUR gallery for identity/voice review.

Credit cost: whatever YOU generate (~1-3 per gen). The script never clicks
"generate" itself.

Usage (headed, supervised, background):

    ! .venv\Scripts\python.exe scripts\dev\spike_movie_gen_capture.py \
        --profile denon82 --project 6ba50219-0fb5-4471-a96e-83257784dfd8 \
        --locale pt --window 480

Outputs (gitignored, flushed continuously): scripts/dev/_spike_out/spike_movie_gen_capture_<ts>.json
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

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402

# Route fragments we care about (POST bodies / responses).
_CAPTURE_FRAGMENTS = (
    "batchAsyncGenerateVideo",
    "GenerateVideo",
    "flow/entities",
    "createEntity",
    "VideoFx",
    "videofx",
    "presetVoice",
    "voice",
)
_GENERATE_MARKER = "batchAsyncGenerateVideo"
_SIGNED = ("signature=", "x-goog-signature=", "expires=", "x-goog-credential=", "authorization", "bearer ")


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("<REDACTED>" if k.lower() in ("authorization", "cookie") else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    if isinstance(obj, str) and any(m in obj.lower() for m in _SIGNED):
        return "<REDACTED>"
    return obj


def _keys_summary(body: Any) -> dict[str, Any]:
    """Pull the fields we're verifying out of a generate request body."""
    out: dict[str, Any] = {}
    try:
        # tRPC/json wrappers: dig for the first 'requests' list or relevant keys
        def walk(o: Any) -> None:
            if isinstance(o, dict):
                for key in ("referenceEntities", "referenceImages", "audioReferences",
                            "videoGenerationMode", "videoModelKey", "presetVoiceId",
                            "textInput", "structuredPrompt", "audioFailurePreference"):
                    if key in o and key not in out:
                        v = o[key]
                        out[key] = (len(v) if isinstance(v, list) else (v if isinstance(v, (str, int, bool)) else "present"))
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for x in o:
                    walk(x)
        walk(body)
    except Exception as e:  # noqa: BLE001
        out["_walk_error"] = str(e)
    return out


async def _run(*, profile_dir: Path, project_id: str, locale: str, window: int, line: str, out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []
    state = {"generate_seen_at": 0.0}

    def flush() -> None:
        out_path.write_text(
            json.dumps(
                {
                    "spike": "movie-gen-capture",
                    "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "projectId": project_id,
                    "events": events,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001

        def on_request(req: Any) -> None:
            try:
                url = req.url
                if req.method != "POST" or not any(f in url for f in _CAPTURE_FRAGMENTS):
                    return
                raw = req.post_data
                body: Any = None
                if raw:
                    try:
                        body = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        body = raw[:2000]
                ev = {
                    "dir": "request",
                    "t": round(time.monotonic(), 2),
                    "method": req.method,
                    "url": url.split("?")[0][-90:],
                    "key_fields": _keys_summary(body) if body is not None else {},
                    "body": _redact(body) if isinstance(body, (dict, list)) else body,
                }
                events.append(ev)
                if _GENERATE_MARKER in url:
                    state["generate_seen_at"] = time.monotonic()
                    step("WIRE", f"GENERATE request captured! key_fields={ev['key_fields']}", prefix="cap")
                else:
                    step("wire", f"{ev['url']} fields={ev['key_fields']}", prefix="cap")
                flush()
            except Exception as e:  # noqa: BLE001
                step("on_request-err", str(e), prefix="cap")

        async def on_response(resp: Any) -> None:
            try:
                url = resp.url
                if _GENERATE_MARKER not in url:
                    return
                try:
                    rbody = await resp.json()
                except Exception:  # noqa: BLE001
                    rbody = (await resp.text())[:2000]
                events.append({
                    "dir": "response",
                    "t": round(time.monotonic(), 2),
                    "status": resp.status,
                    "url": url.split("?")[0][-90:],
                    "key_fields": _keys_summary(rbody),
                    "body": _redact(rbody) if isinstance(rbody, (dict, list)) else rbody,
                })
                step("WIRE", f"GENERATE response status={resp.status}", prefix="cap")
                flush()
            except Exception as e:  # noqa: BLE001
                step("on_response-err", str(e), prefix="cap")

        page.on("request", on_request)
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        url = routes.project_editor_url(locale, project_id)
        step("1", f"goto {url}", prefix="cap")
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(2500)

        print("\n" + "=" * 72, flush=True)
        print("  TIER-2 CAPTURE READY — do the following IN THE BROWSER now:", flush=True)
        print("  1. Switch to  Vídeo  +  Elementos  (references) sub-mode.", flush=True)
        print("  2. Choose a model that supports audio (Veo 3.1).", flush=True)
        print("  3. + Add Media -> Personagens -> pick a 'Stickman' -> 'Incluir no comando'.", flush=True)
        print("  4. + Add Media -> Vozes -> pick a voice -> 'Incluir no comando'.", flush=True)
        print(f"  5. Type the prompt incl. dialogue, e.g.:  Stickman says: \"{line}\"", flush=True)
        print("  6. Click generate, solve reCAPTCHA.", flush=True)
        print("     (For 2-speaker: add a 2nd Stickman + 2nd voice + 2 lines.)", flush=True)
        print(f"  Capturing for up to {window}s; exits ~20s after a generate response.", flush=True)
        print("=" * 72 + "\n", flush=True)

        deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            await asyncio.sleep(3)
            flush()  # continuous flush so the orchestrator can read mid-run
        step("done", f"capture window ({window}s) elapsed — exiting", prefix="cap")
        flush()
        client._checkin_page(page)  # noqa: SLF001

    n_gen = sum(1 for e in events if _GENERATE_MARKER in e.get("url", ""))
    print(f"\n[cap] events captured: {len(events)} (generate-related: {n_gen})", flush=True)
    print(f"[cap] out -> {out_path}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Movie spike Tier-2: capture r2v dialogue wire (you generate).")
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_PROFILE", "denon82"))
    p.add_argument("--project", required=True)
    p.add_argument("--locale", default="pt")
    p.add_argument("--window", type=int, default=480, help="Capture window seconds (default 480).")
    p.add_argument("--line", default="We finally made it to the top!", help="Dialogue line to speak.")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    profile_dir = resolve_profile_dir(args.profile)
    out_path = Path(args.out) if args.out else default_out_path("spike_movie_gen_capture", ".json")
    step("--", f"profile={args.profile} project={args.project} window={args.window}s", prefix="cap")
    try:
        return asyncio.run(_run(profile_dir=profile_dir, project_id=args.project, locale=args.locale,
                                window=args.window, line=args.line, out_path=out_path))
    except KeyboardInterrupt:
        print("[cap] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
