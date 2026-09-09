"""Does Google's sign-in accept a Playwright-driven REAL Chrome? — $0, no generation.

``RealChromeStrategy`` launches Chrome as a bare subprocess with no debugging port, so it
has no signal channel and must ask the user to close the window by hand. Sibling project
``notebooklm-py`` auto-closes instead, and its mechanism is not a trick: it *owns* the
browser (``page.wait_for_url(...)``, then ``context.close()``), accepting an automation
surface and mitigating it with ``--disable-blink-features=AutomationControlled`` plus
``ignore_default_args=["--enable-automation"]``.

gflow assumes that surface is fatal at sign-in — the G12 block, ``KNOWN_ISSUES.md:1516``.
That entry is about Playwright's **bundled Chromium**, and its stated resolution ("real
Chrome via Playwright's channel='chrome' with stealth flags") describes an implementation
that does not exist: ``real_chrome.py`` was born as passive capture (eb0de133, 2026-07-19)
and never used Playwright. Meanwhile ``channel="chrome"`` IS driven by Playwright daily —
``api/client.py:569``, ``auth/verification.py:287`` — but only ever on an ALREADY
authenticated profile, so nothing has ever tested the ``accounts.google.com`` gate itself.

So the question is open, not settled. This measures it directly.

    uv run python scripts/dev/spike_playwright_chrome_login.py --arm stealth
    uv run python scripts/dev/spike_playwright_chrome_login.py --arm bare      # control
    uv run python scripts/dev/spike_playwright_chrome_login.py --arm bundled   # control

Three arms, because one result cannot separate the causes:

  stealth  channel="chrome" + the anti-automation flags   (notebooklm-py's posture)
  bare     channel="chrome", Playwright defaults          (isolates: do the flags matter?)
  bundled  bundled Chromium + the flags                   (isolates: does the binary matter?)

A PASS on ``stealth`` with a BLOCK on ``bundled`` means the binary and flags are what save
you, and auto-close is reachable. A BLOCK on all three means the zero-automation-surface
constraint is still load-bearing and the honest answer to "why no auto-close" is "because
Google says so".

Each run uses a THROWAWAY profile (``--profile``, default ``spike-login-probe``) so a real
sign-in is actually required; an already-authenticated profile would skip the gate and
prove nothing. Delete it afterwards — the script prints the path.

COST: $0. Navigation, cookie reads and DOM only. Nothing is submitted, no generation
starts, no credit and no image quota is spent.

WHAT IS OBSERVED (never inferred):

* ``navigator.webdriver`` as the page actually sees it
* every main-frame URL the sign-in walks through, with timestamps
* whether ``accounts.google.com/v3/signin/rejected`` is EVER reached — the G12 marker,
  the same constant ``internal_chromium.py`` already watches for
* whether ``__Secure-next-auth.session-token`` appears in the context cookie jar, which
  is also a direct test of whether notebooklm-py's detect-and-close would work here

"It timed out" is not evidence of a block. The URL trail is: it says what WAS reached.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli import auth as _auth_mod  # noqa: E402
from gflow_cli.profile_lease import ProfileLease  # noqa: E402

from _spike_common import default_out_path  # noqa: E402, isort: skip

FLOW_URL = "https://labs.google/fx/tools/flow?hl=en"
# The same marker internal_chromium.py watches — a positive observation of the block.
G12_ROUTE = "accounts.google.com/v3/signin/rejected"
FLOW_SESSION_COOKIE = "__Secure-next-auth.session-token"  # noqa: S105 - a cookie NAME

STEALTH_ARGS = ["--disable-blink-features=AutomationControlled", "--password-store=basic"]


def _launch_kwargs(arm: str, profile_dir: Path) -> dict[str, Any]:
    """Launch options per arm. Only the arm differs; everything else is held constant."""
    # no_viewport is REQUIRED for a window a human drives. Passing an explicit
    # `viewport=` makes Playwright EMULATE that size independently of the real OS
    # window, so on any display smaller (or more scaled) than the emulated size the
    # sign-in form renders outside the visible area and zooming cannot recover it —
    # zoom changes CSS pixels, not the emulated viewport. Measured 2026-09-08: a
    # 1920x1080 viewport made Google's sign-in unusable on this machine.
    # chromium_sandbox defaults to False in Playwright, which injects --no-sandbox.
    # Observed 2026-09-08: Chrome then shows "You are using an unsupported command-line
    # flag: --no-sandbox". real_chrome.py's raw subprocess passes no such flag, so leaving
    # it in would make this arm strictly noisier than the path it is being compared
    # against — and --no-sandbox is itself an automation signal, which is the whole
    # variable under test.
    kw: dict[str, Any] = {
        "user_data_dir": str(profile_dir),
        "headless": False,
        "no_viewport": True,
        "chromium_sandbox": True,
    }
    if arm == "stealth":
        kw["channel"] = "chrome"
        kw["args"] = STEALTH_ARGS
        kw["ignore_default_args"] = ["--enable-automation"]
    elif arm == "bare":
        kw["channel"] = "chrome"
        kw["args"] = ["--password-store=basic"]
    elif arm == "bundled":
        kw["args"] = STEALTH_ARGS
        kw["ignore_default_args"] = ["--enable-automation"]
    else:  # pragma: no cover - argparse constrains this
        msg = f"unknown arm: {arm}"
        raise ValueError(msg)
    return kw


def _verdict_lines(arm: str, result: dict[str, Any], timeout_s: int) -> str:
    if result.get("pre_authenticated"):
        return (
            f"VERDICT [{arm}]: VOID — the profile was already authenticated, so the "
            "sign-in gate was never exercised.\nRe-run on a fresh profile."
        )
    if result.get("g12_block_observed"):
        return f"VERDICT [{arm}]: BLOCKED — reached {G12_ROUTE}"
    if result.get("session_cookie_detected"):
        return (
            f"VERDICT [{arm}]: PASS — signed in, and the cookie was detected from the "
            f"owned context at t={result['session_cookie_detected_at_s']}s.\n"
            "Auto-close works on this arm."
        )
    # A run nobody drove is a NULL RESULT, not a weak one. Distinguishing the two is the
    # whole point: the first run of this spike lapsed at 300s having never left the Flow
    # host, and "INCONCLUSIVE" undersold that — nothing was tested at all.
    if not any("accounts.google.com" in e.get("url", "") for e in result.get("url_trail", [])):
        return (
            f"VERDICT [{arm}]: NOT DRIVEN — the sign-in was never started (no "
            "accounts.google.com navigation in the trail).\n"
            "This run tested NOTHING about the block. Re-run and sign in by hand."
        )
    return (
        f"VERDICT [{arm}]: INCONCLUSIVE — sign-in was reached, but no block and no session "
        f"cookie within {timeout_s}s.\nThis is NOT evidence of a block; read url_trail for "
        "what was reached."
    )


async def _watch_for_session(
    context: Any, timeout_s: int, t0: float, state: dict[str, Any]
) -> None:
    """Poll the owned context's in-memory jar — notebooklm-py's mechanism, on our surface."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if state["blocked"]:
            return
        try:
            names = {c.get("name") for c in await context.cookies()}
        except Exception as exc:  # noqa: BLE001 - the context can close under us
            state["trail"].append(
                {"t": round(time.monotonic() - t0, 2), "cookie_read_error": repr(exc)}
            )
            return
        if FLOW_SESSION_COOKIE in names:
            state["detected_at"] = round(time.monotonic() - t0, 2)
            print(
                f"[spike] SESSION COOKIE DETECTED at t={state['detected_at']}s — closing now",
                file=sys.stderr,
                flush=True,
            )
            return
        await asyncio.sleep(1)


async def run(arm: str, profile: str, timeout_s: int) -> int:
    from playwright.async_api import async_playwright

    profile_dir = _auth_mod.profile_dir(profile)
    profile_dir.mkdir(parents=True, exist_ok=True)
    print(f"[spike] arm={arm}  profile_dir={profile_dir}", file=sys.stderr, flush=True)

    t0 = time.monotonic()
    state: dict[str, Any] = {"blocked": False, "detected_at": None, "trail": []}
    result: dict[str, Any]

    # Chrome must never start on a profile this process does not own (spike SKILL.md).
    async with ProfileLease(profile_dir), async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(**_launch_kwargs(arm, profile_dir))
        try:
            page = context.pages[0] if context.pages else await context.new_page()

            def _on_nav(frame: Any) -> None:
                if frame is not page.main_frame:
                    return
                url = frame.url
                state["trail"].append({"t": round(time.monotonic() - t0, 2), "url": url})
                if G12_ROUTE in url:
                    state["blocked"] = True
                    print(f"[spike] G12 BLOCK OBSERVED at {url}", file=sys.stderr, flush=True)

            page.on("framenavigated", _on_nav)
            await page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=60_000)

            # Read-validity check, not a gate: if the session cookie is ALREADY here, this
            # profile was authenticated before the run and the sign-in gate is never
            # exercised. The arm would then report PASS without having tested anything.
            pre_auth = FLOW_SESSION_COOKIE in {c.get("name") for c in await context.cookies()}
            if pre_auth:
                print(
                    "[spike] WARNING: profile is ALREADY authenticated — this arm cannot "
                    "test the sign-in gate. Use a fresh --profile.",
                    file=sys.stderr,
                    flush=True,
                )

            webdriver = await page.evaluate("() => navigator.webdriver")
            user_agent = await page.evaluate("() => navigator.userAgent")
            print(f"[spike] navigator.webdriver = {webdriver!r}", file=sys.stderr, flush=True)
            print(
                "\n[spike] Sign in by hand in the window that opened.\n"
                "        Continue until the Flow editor loads (prompt box / your projects).\n"
                f"        The script watches for {FLOW_SESSION_COOKIE} and closes the window\n"
                "        ITSELF the moment it appears — that is the thing under test.\n",
                file=sys.stderr,
                flush=True,
            )

            await _watch_for_session(context, timeout_s, t0, state)

            result = {
                "arm": arm,
                "profile_dir": str(profile_dir),
                "navigator_webdriver": webdriver,
                "user_agent": user_agent,
                "pre_authenticated": pre_auth,
                "g12_block_observed": state["blocked"],
                "session_cookie_detected": state["detected_at"] is not None,
                "session_cookie_detected_at_s": state["detected_at"],
                "final_url": page.url,
                "elapsed_s": round(time.monotonic() - t0, 2),
                "url_trail": state["trail"],
            }
        finally:
            await context.close()

    out = default_out_path(f"spike_pw_chrome_login_{arm}", ".json")
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print("\n" + "=" * 68, file=sys.stderr)
    print(_verdict_lines(arm, result, timeout_s), file=sys.stderr)
    print(f"evidence: {out}", file=sys.stderr)
    print(f"throwaway profile (delete when done): {result['profile_dir']}", file=sys.stderr)
    print("=" * 68, file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Playwright real-Chrome login probe (see module docstring)."
    )
    ap.add_argument("--arm", choices=("stealth", "bare", "bundled"), default="stealth")
    # Per-arm by default. A control arm re-using the arm-1 profile would start ALREADY
    # authenticated, sail past accounts.google.com without touching the gate, and report
    # PASS — a fake result that looks exactly like a real one.
    ap.add_argument(
        "--profile",
        default=None,
        help="THROWAWAY profile name (default: spike-login-<arm>; must be UNAUTHENTICATED)",
    )
    ap.add_argument("--timeout", type=int, default=300, help="seconds to wait for sign-in")
    args = ap.parse_args()
    return asyncio.run(run(args.arm, args.profile or f"spike-login-{args.arm}", args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
