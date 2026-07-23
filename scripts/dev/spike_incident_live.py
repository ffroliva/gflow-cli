"""Live verification harness for v0.43.0 private incident diagnostics.

Design §10.3, steps 1 + 3 — $0 credits:

  Phase A: open the REAL authenticated Flow editor, deliberately stage an
           incident from the live page WITHOUT submitting any generation, and
           verify the finalized bundle (artifacts, sanitization, har_state).
  Phase B: while the profile lease is held, run the REAL CLI from a second
           process and verify exit 11 + the metadata-only incident + that no
           Chrome was launched for the contender.

Usage:  .venv/Scripts/python.exe scripts/dev/spike_incident_live.py <profile>
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from gflow_cli.api.client import FlowApiClient
from gflow_cli.config import get_settings
from gflow_cli.errors import FlowAppError
from gflow_cli.paths import profile_subdir

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def bundles(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(b for day in root.iterdir() if day.is_dir() for b in day.iterdir() if b.is_dir())


def scan_for_leaks(bundle: Path, needles: list[str]) -> list[str]:
    hits: list[str] = []
    for artifact in bundle.rglob("*.json"):
        blob = artifact.read_text(encoding="utf-8")
        hits.extend(f"{artifact.name}:{n}" for n in needles if n and n in blob)
        if "?" in blob.replace('"?"', ""):  # query strings must never survive
            for line in blob.splitlines():
                if "http" in line and "?" in line:
                    hits.append(f"{artifact.name}:querystring:{line.strip()[:80]}")
    return hits


async def main(profile: str) -> int:
    settings = get_settings()
    incidents_root = settings.home / "incidents"
    before = set(bundles(incidents_root))
    profile_dir = profile_subdir(settings.home, profile)
    account = ""
    account_file = profile_dir / ".gflow_account"
    if account_file.exists():
        account = account_file.read_text(encoding="utf-8").strip()

    print(f"Phase A: real editor session on profile {profile!r} (no generation)")
    async with FlowApiClient(profile_dir, settings=settings) as client:
        rec = client._recorder  # noqa: SLF001 — dev spike drives internals
        assert rec is not None and rec.enabled, "recorder must be enabled"
        # Let real traffic flow through the journals for a few seconds.
        await asyncio.sleep(5)
        pre_records = len(rec.journal.snapshot().network)
        check("journal saw real network traffic", pre_records > 0, f"{pre_records} records")
        await client._capture_incident(  # noqa: SLF001
            FlowAppError("live-verify: deliberate capture, no generation submitted"),
            phase="live_verify",
        )

        print("Phase B: contention from a second REAL CLI process (lease held)")
        t0 = time.monotonic()
        contender = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "gflow_cli.cli",
                "image",
                "t2i",
                "contention probe - must never generate",
                "--profile",
                profile,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        elapsed = time.monotonic() - t0
        # Rich wraps at console width in a non-TTY subprocess — normalize
        # whitespace before substring checks.
        out = " ".join((contender.stdout + contender.stderr).split())
        check("contender exits 11 (ProfileLockedError)", contender.returncode == 11)
        check("contender fails fast (no Chrome launch)", elapsed < 45, f"{elapsed:.1f}s")
        check("human output names the incident bundle", "Incident bundle:" in out)
        check("human output warns review-before-sharing", "review before sharing" in out)

    after = bundles(incidents_root)
    new = [b for b in after if b not in before]
    print(f"\nNew bundles: {len(new)}")
    deliberate = [b for b in new if (b / "ui.json").exists()]
    metadata_only = [b for b in new if not (b / "ui.json").exists()]
    check("deliberate UI bundle created", len(deliberate) == 1)
    check("contender metadata-only bundle created", len(metadata_only) >= 1)

    if deliberate:
        b = deliberate[0]
        manifest = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
        ui = json.loads((b / "ui.json").read_text(encoding="utf-8"))
        net = json.loads((b / "network.json").read_text(encoding="utf-8"))
        check("manifest finalized (schema v1)", manifest.get("schema") == "gflow-incident-v1")
        check(
            "har_state honest",
            manifest.get("har_state") == "disabled",
            str(manifest.get("har_state")),
        )
        check("screenshot under sensitive/", (b / "sensitive" / "screenshot.png").exists())
        check(
            "screenshot marked sensitive",
            manifest.get("artifacts", {}).get("sensitive/screenshot.png") == "sensitive",
        )
        check(
            "real ligatures captured",
            bool(ui.get("ligatures")),
            f"{len(ui.get('ligatures', []))} unique",
        )
        check(
            "url reduced to category+route",
            isinstance(ui.get("url"), dict) and "?" not in str(ui.get("url", {}).get("route")),
            str(ui.get("url")),
        )
        check(
            "network journal in bundle",
            len(net.get("records", [])) > 0,
            f"{len(net.get('records', []))} records",
        )
        hosts = {r["host_category"] for r in net.get("records", [])}
        check(
            "hosts reduced to categories",
            hosts
            <= {
                "flow_app",
                "aisandbox",
                "google_auth",
                "google_cdn",
                "google_static",
                "google_web",
                "other",
            },
            str(hosts),
        )
        leaks = scan_for_leaks(b, [account, profile, "ya29", "SAPISID", "session-token"])
        check("no account/profile/token text in ANY json", not leaks, "; ".join(leaks[:3]))
    if metadata_only:
        m = metadata_only[-1]
        manifest = json.loads((m / "manifest.json").read_text(encoding="utf-8"))
        check(
            "contender bundle is ProfileLockedError exit 11",
            manifest["error"]["class"] == "ProfileLockedError"
            and manifest["error"]["exit_code"] == 11,
        )
        check("no screenshot in metadata-only bundle", not (m / "sensitive").exists())
        leaks = scan_for_leaks(m, [account, str(profile_dir)])
        check("metadata-only bundle leak-free", not leaks, "; ".join(leaks[:3]))

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{'=' * 60}\nRESULT: {len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    for name, _ok, detail in failed:
        print(f"  FAILED: {name} {detail}")
    if deliberate:
        print(f"\nReview the sensitive screenshot before any sharing:\n  {deliberate[0]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "denon82")))
