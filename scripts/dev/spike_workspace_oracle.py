r"""Does the visible New-project button prove a live session? (PR #1144) $0, read-only.

PR #1144 accepts a migrated session when My Account cannot answer and the button renders
on https://flow.google.com/. #835 retired a DOM oracle after an /about-state account broke
it, so this runs the real fallback on every saved profile, in the PR's own tree:

    PYTHONPATH=<pr-1144>/src python scripts/dev/spike_workspace_oracle.py a b c

Each profile runs twice through ``_verify_migrated_host_fallback``:
  control   the real My Account URL: today's path
  forced    My Account pointed at an unresolvable host, so ``ctx.request.get`` raises

A third read, ``_verify_migrated_workspace`` alone, shows what the new oracle says even
where the cookie gate stops the fallback earlier.

Pre-registered readings (2026-11-04, before the run):

| control | forced | Reading |
|---|---|---|
| authenticated | authenticated | the fallback does its job |
| authenticated | None | a false negative: the PR does not rescue this profile, no regression |
| None or unauthenticated | authenticated | FALSE POSITIVE: the oracle accepts a session My Account rejects, block the PR |
| None | None | agrees, settles nothing about the button |
| the oracle alone is authenticated on a profile `gflow auth status` calls signed out | | FALSE POSITIVE, block the PR |
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gflow_cli.auth.verification as v  # noqa: E402
from gflow_cli.auth.strategies import async_playwright  # noqa: E402
from gflow_cli.browser_manager import channel_for_profile  # noqa: E402
from gflow_cli.profile_lease import ProfileLease  # noqa: E402

from _spike_common import default_out_path, resolve_profile_dir  # noqa: E402, isort: skip


def brief(status: Any) -> dict[str, Any] | None:
    if status is None:
        return None
    return {"outcome": str(status.outcome), "has_email": bool(status.user_email)}


async def oracle_alone(profile_dir: Path) -> dict[str, Any]:
    async with ProfileLease(profile_dir), async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(profile_dir),
            channel=channel_for_profile(profile_dir),
            headless=True,
            args=["--password-store=basic"],
        )
        try:
            status = await v._verify_migrated_workspace(ctx, "spike")  # noqa: SLF001
            pages = ctx.pages
            return {"status": brief(status), "landed": [p.url[:70] for p in pages][-1:]}
        finally:
            await ctx.close()


async def probe(name: str) -> dict[str, Any]:
    profile_dir = resolve_profile_dir(name)
    real = v._MYACCOUNT_URL  # noqa: SLF001
    row: dict[str, Any] = {"profile": name}
    for arm, url in (("control", real), ("forced", "https://myaccount.invalid/")):
        v._MYACCOUNT_URL = url  # noqa: SLF001
        try:
            row[arm] = brief(
                await asyncio.wait_for(
                    v._verify_migrated_host_fallback(profile_dir, "spike"),  # noqa: SLF001
                    timeout=100,
                )
            )
        except Exception as exc:  # noqa: BLE001 - a failed arm is data here
            row[arm] = f"{type(exc).__name__}: {str(exc)[:120]}"
        finally:
            v._MYACCOUNT_URL = real  # noqa: SLF001
        print(name, arm, row[arm], flush=True)
    try:
        row["oracle_alone"] = await asyncio.wait_for(oracle_alone(profile_dir), timeout=100)
    except Exception as exc:  # noqa: BLE001 - a locked profile is data here
        row["oracle_alone"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return row


async def main() -> None:
    rows = []
    for name in sys.argv[1:]:
        rows.append(await probe(name))
        print(json.dumps(rows[-1], ensure_ascii=True)[:420], flush=True)
    path = default_out_path("spike_workspace_oracle", ".json")
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"report -> {path}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
