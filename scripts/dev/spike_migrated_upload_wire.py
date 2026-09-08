r"""What actually goes out when the migrated editor is handed a local file? ($0, #719)

`gflow video i2v --initial-frame <local>` and `r2v --ref <local>` both die on the migrated
host with ``MediaUploadRejectedError`` (exit 27) — *"no maseQ reply within 60s of choosing
the file"*. #719 lists four candidates and says the logs cannot separate them.

**Three of the four are already ruled out by the error class**, and that is worth stating
before spending a run on them. ``_upload_via_toolbar`` raises a DISTINCT
``UiSelectorDriftError`` when the toolbar ``+`` is missing, when the menu renders no
``upload`` entry, and when that entry opens no file chooser. A reporter holding
``MediaUploadRejectedError`` therefore already knows the affordance was found, the menu
opened, the chooser fired, and ``set_files`` was called. What is left is narrower:

  (a) the upload went out under a rpcid that is no longer ``maseQ``
  (b) it went out on ``maseQ`` and Flow did not answer inside the budget
  (c) nothing went out at all — the app took the file and never asked the network,
      or it uploads over something that is not ``batchexecute`` in the first place

Only (a) is fixed by changing a constant, and today all three present identically.

This drives **the real driver method**, not a re-implementation of it — the point is to
see what the shipped code sees — with a listener on EVERY request, not just
``batchexecute``. A parallel probe that uploaded some other way could disagree with the
product and teach us nothing.

**Cost: zero.** An upload is not a generation; no submit is clicked and no Veo credit can
be reached from here. Run it against a funded profile AND a drained one: #721 established
that a credit shortfall changes what this host renders, and whether that CAUSES the upload
failure or merely travelled with it is still open.

    uv run python scripts/dev/spike_migrated_upload_wire.py <profile> <project-id> [--image PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    UPLOAD_RPC,
    MigratedComposer,
)

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402, isort: skip

#: A 1x1 PNG. #719 already ruled the file out with three of them, including a 4.3 KB
#: synthetic solid colour, so this spike does not re-litigate the file — it just needs
#: SOMETHING valid to hand the chooser.
_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _rpcid_of(url: str) -> str | None:
    from urllib.parse import parse_qs

    return (parse_qs(urlsplit(url).query).get("rpcids") or [None])[0]


async def _main(profile: str, project_id: str, image: Path | None, out: Path) -> int:
    events: list[dict[str, Any]] = []
    t0 = time.monotonic()
    #: Off during the noisy project load, on for the attach window — where a rpcid that is
    #: NOT the upload may be the one refusing it, and its body is the only thing that says so.
    capture_all_bodies = {"on": False}

    if image is None:
        image = default_out_path("spike_upload_1px", ".png")
        image.write_bytes(_PNG_1PX)
        step("file", f"synthesised {len(_PNG_1PX)} B PNG -> {image}")

    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 - dev instrument
        assert page is not None

        def on_request(request: Any) -> None:
            url = str(getattr(request, "url", ""))
            method = str(getattr(request, "method", ""))
            host = urlsplit(url).netloc
            if method == "GET" and "batchexecute" not in url:
                return  # asset traffic; an upload is never a bare GET
            if host.endswith("google-analytics.com") or host.endswith("doubleclick.net"):
                return
            # `post_data` DECODES as utf-8 and an upload body is binary, so reading it
            # raises inside the listener. pyee then re-emits that into Playwright's own
            # error handler, which has a different arity, and the whole thing surfaces on
            # the awaited call as `TypeError: function takes exactly 5 arguments` —
            # nowhere near the line at fault. The size is all this needs anyway.
            try:
                size = len(request.post_data_buffer or b"")
            except Exception:  # noqa: BLE001 - a body we cannot size is still an event
                size = -1
            events.append(
                {
                    "t": round(time.monotonic() - t0, 2),
                    "dir": "req",
                    "method": method,
                    "rpcid": _rpcid_of(url),
                    "host": host,
                    "path": urlsplit(url).path[:120],
                    "post_bytes": size,
                }
            )

        async def on_response(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            if "batchexecute" not in url:
                return
            row: dict[str, Any] = {
                "t": round(time.monotonic() - t0, 2),
                "dir": "res",
                "status": int(getattr(response, "status", 0) or 0),
                "rpcid": _rpcid_of(url),
                "path": urlsplit(url).path[:120],
            }
            # The upload reply is the whole question: a 200 the driver cannot read is not
            # the same bug as a 200 it can. Body kept only for UPLOAD_RPC, and only as a
            # length, a UUID count and a head — enough to tell "Flow refused this file"
            # from "the reply shape moved", without parking a full payload on disk.
            if row["rpcid"] == UPLOAD_RPC or capture_all_bodies["on"]:
                try:
                    text = await response.text()
                except Exception as exc:  # noqa: BLE001 - an unreadable body IS a finding
                    row["body_error"] = str(exc)[:200]
                else:
                    row["body_len"] = len(text)
                    row["uuids"] = len(_UUID_RE.findall(text))
                    row["body_head"] = text[:400]
            events.append(row)

        page.on("request", on_request)
        page.on("response", on_response)

        composer = MigratedComposer()
        await composer.ensure_editor(page, project_id)
        step("editor", "ready; baseline traffic recorded")
        baseline = len(events)
        capture_all_bodies["on"] = True

        outcome: dict[str, Any] = {}
        try:
            media_id = await composer._upload_via_toolbar(page, project_id, image)  # noqa: SLF001
            outcome = {"result": "uploaded", "media_id": media_id}
            step("upload", f"OK media_id={media_id}")
        except Exception as exc:  # noqa: BLE001 - the failure IS the measurement
            outcome = {"result": type(exc).__name__, "detail": str(exc)[:400]}
            step("upload", f"{type(exc).__name__}: {str(exc)[:200]}")

        # Whatever happened, give the wire a few seconds to show a late reply.
        await page.wait_for_timeout(5_000)

        # What is the APP saying? If it declined to upload, it very likely told the user
        # something the driver never reads — it only ever watches the wire. Roles and
        # component tags only: `aria-label` and visible copy are translated on this host.
        outcome["announcements"] = await page.evaluate(
            """() => {
                const sel = "[role='alert'],[role='status'],[role='dialog'],"
                          + "mat-snack-bar-container,.mat-mdc-snack-bar-label,"
                          + ".prompt-warning-button,[aria-live]";
                return [...document.querySelectorAll(sel)]
                    .filter(e => e.offsetParent !== null || e.getClientRects().length)
                    .map(e => (e.innerText || "").trim().slice(0, 300))
                    .filter(Boolean);
            }"""
        )
        # Structural anchors for whatever is on screen — for a FIX to bind to, since the
        # copy is translated. Read-only on purpose: this dialog is a rights affirmation and
        # a probe must never click it on an account owner's behalf.
        outcome["dialog_controls"] = await page.evaluate(
            """() => {
                const d = document.querySelector("[role='dialog']");
                if (!d) return null;
                return {
                    tag: d.tagName.toLowerCase(),
                    cls: d.className,
                    buttons: [...d.querySelectorAll("button")].map(b => ({
                        cls: b.className,
                        lig: [...b.querySelectorAll("mat-icon,i")].map(i => i.textContent.trim()),
                        attrs: b.getAttributeNames().filter(a => a.startsWith("data-")
                               || a === "type" || a === "aria-pressed"),
                    })),
                };
            }"""
        )
        step("dialog", str(outcome["dialog_controls"])[:400])
        shot = out.with_name(out.stem + "_after.png")
        await page.screenshot(path=str(shot))
        outcome["screenshot"] = shot.name
        step("dom", f"announcements={outcome['announcements']} shot={shot.name}")

    during = events[baseline:]
    posts = [e for e in during if e.get("dir") == "req" and e["method"] == "POST"]
    findings = {
        "profile": profile,
        "project_id": project_id,
        "image": str(image),
        "image_bytes": image.stat().st_size,
        "outcome": outcome,
        "upload_rpc_expected": UPLOAD_RPC,
        "saw_expected_rpc": any(e.get("rpcid") == UPLOAD_RPC for e in during),
        "post_count_after_attach": len(posts),
        "rpcids_after_attach": dict(Counter(str(e.get("rpcid")) for e in during).most_common()),
        "hosts_after_attach": dict(Counter(e["host"] for e in during if "host" in e).most_common()),
        "events_after_attach": during,
    }
    out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")

    step("verdict", f"expected rpc {UPLOAD_RPC} seen: {findings['saw_expected_rpc']}")
    step("verdict", f"POSTs after the file was chosen: {len(posts)}")
    step("verdict", f"rpcids: {findings['rpcids_after_attach']}")
    step("out", str(out))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("project_id")
    parser.add_argument("--image", type=Path, default=None, help="defaults to a 1x1 PNG")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    out = args.out or default_out_path("spike_migrated_upload_wire", ".json")
    return asyncio.run(_main(args.profile, args.project_id, args.image, out))


if __name__ == "__main__":
    raise SystemExit(main())
