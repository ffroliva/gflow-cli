"""Live proof for #651 — `<html lang>` is read after hydration, not before.

Two measurements, deliberately kept distinct, because conflating them is the error
this whole line of work exists to correct (see the correction block atop
``docs/LIVE_VERIFICATION_v0.66.1.md``):

**A — the helper, exercised against a live page.** ``_settled_lang`` is called
directly on a real bootstrap page of a **pt** account. This proves the flip is
captured: the naive early read returns ``en``, the fixed read returns ``pt``. It is
a component measurement and is labelled as one.

**B — the real bootstrap, end to end.** ``FlowApiClient.__aenter__`` on a latched
profile whose locale *equals* the shell default, which is the case that pays the
settle timeout. This is the user-visible cost, on the path a user actually takes.

Zero credits: navigation only.

    uv run python scripts/dev/verify_lang_settle_fix.py denon82 ffroliva
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api import routes  # noqa: E402

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip


async def _component_check(profile: str) -> None:
    """A — does the helper capture the post-hydration value on a live page?"""
    async with build_client(resolve_profile_dir(profile)) as client:
        page: Any = client._page  # noqa: SLF001 — dev instrument
        await page.goto(routes.EDITOR_BOOTSTRAP_URL, wait_until="domcontentloaded", timeout=45_000)

        naive = await page.evaluate("() => document.documentElement.lang || ''")
        t0 = perf_counter()
        settled = await client._settled_lang(page)  # type: ignore[attr-defined]  # noqa: SLF001
        ms = round((perf_counter() - t0) * 1000)

        print(f"\n[A · COMPONENT — helper on a live page]  profile={profile}")
        print(f"    naive early read : {naive!r}")
        print(f"    _settled_lang    : {settled!r}   (+{ms} ms)")
        print(f"    verdict          : {'CAPTURED THE FLIP' if settled != naive else 'no flip seen'}")


async def _end_to_end(profile: str) -> None:
    """B — what a real bootstrap costs on the account that pays the timeout."""
    t0 = perf_counter()
    async with build_client(resolve_profile_dir(profile)) as client:
        ms = round((perf_counter() - t0) * 1000)
        print(f"\n[B · END-TO-END — real bootstrap]  profile={profile}")
        print(f"    resolved locale  : {client._account_locale!r}")  # noqa: SLF001
        print(f"    __aenter__ total : {ms} ms")


async def _main(flip_profile: str, shell_profile: str) -> int:
    await _component_check(flip_profile)
    await _end_to_end(shell_profile)
    print(
        "\nA is a component measurement; B is the user path. Reporting A's number as"
        "\nthe user-visible one is precisely the v0.66.1 mistake."
    )
    return 0


if __name__ == "__main__":
    args = sys.argv[1:] or ["denon82", "ffroliva"]
    raise SystemExit(asyncio.run(_main(args[0], args[1])))
