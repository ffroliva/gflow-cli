"""Probe live Flow for selector drift. $0 — navigate and read only.

Auth is ONE cookie: __Secure-next-auth.session-token. Measured sufficient AT
MINT TIME; an aged token is unverified (spec §2.2), so exit 2 exists to keep an
expired credential from ever being reported as drift.

Exit: 0 clean · 1 drift · 2 infrastructure.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from gflow_cli.api.transports import mode_control
from gflow_cli.api.transports.drivers import factory
from gflow_cli.config import UiMode
from gflow_cli.flow_selectors import registry
from gflow_cli.flow_selectors.grading import Grade, Outcome, grade
from gflow_cli.flow_selectors.model import Selector

SESSION_COOKIE = "__Secure-next-auth.session-token"
_LABEL = {
    Grade.HIT: "ok",
    Grade.FALLBACK: "FALLBACK",
    Grade.AMBIGUOUS: "AMBIGUOUS",
    Grade.MISS: "DRIFT",
    Grade.EXPECTED_ABSENT: "n/a",
}


def _cell(outcome: Outcome) -> str:
    """FALLBACK names WHICH candidate held.

    `crop_control` carries six candidates; "a later one held" is not actionable
    without the index, and the index is what says how far down the cascade the
    page has drifted. Safe to publish — it is an ordinal, not a selector.
    """
    label = _LABEL[outcome.grade]
    if outcome.grade is Grade.FALLBACK:
        return f"{label}[{outcome.resolved_index}]"
    return label


def render_report(outcomes: list[Outcome]) -> str:
    """Publication-safe: keys, grades and candidate ordinals — never selectors or DOM."""
    lines = ["| selector | result |", "| --- | --- |"]
    lines += [f"| `{o.selector_key}` | {_cell(o)} |" for o in outcomes]
    bad = [o.selector_key for o in outcomes if o.is_failure]
    lines += ["", f"**{len(bad)} need attention**" if bad else "**0 need attention.**"]
    return "\n".join(lines)


# Known ZERO-CLICK alternate states. Neither is drift, and both hide the
# composer, so grading through them would report drift that did not happen.
#   mode_control.py:61-84  — an expanded chat sidebar "removes the classic
#                            composer entirely... no crop_* trigger AND no Agent pill"
#   ui_automation_video.py:149-153 — a chat panel "appears on some project opens
#                            and not others"; while up "the in-composer pill is
#                            NOT in the DOM at all"
# NOTE: mode_control.SIDEBAR_CLOSE_SELECTOR and
# ui_automation_video.AGENT_CHAT_PANEL_CLOSE_SELECTOR are BYTE-EQUAL — two names
# for one selector. Listing both would make the second entry unreachable. One
# scoped candidate plus the genuinely-different unscoped fallback, mirroring
# production's two-tier close: the edit_square scoping was #493's single point
# of failure, so relying on it alone would let a drifted scope disable this gate.
_ALTERNATE_STATE_CANDIDATES: tuple[str, ...] = (
    mode_control.SIDEBAR_CLOSE_SELECTOR,
    mode_control.SIDEBAR_CLOSE_FALLBACK_SELECTOR,
)
_ALTERNATE_STATE_LABEL = "expanded chat sidebar / agent chat panel"


async def alternate_state(page: object) -> str | None:
    """Name the known alternate state the editor is in, if any.

    This is the difference between "the composer is gone because Google moved
    it" (drift, exit 1) and "the composer is gone because a panel is covering
    it" (inconclusive, exit 2). Three of the four registered selectors are
    absent in these states, so without this gate a cohort flap reads as drift.
    """
    for selector in _ALTERNATE_STATE_CANDIDATES:
        if await page.locator(selector).count():  # type: ignore[attr-defined]
            return _ALTERNATE_STATE_LABEL
    return None


async def resolve(page: object, entries: Sequence[Selector], mode: UiMode) -> list[Outcome]:
    """Resolve each entry against the LIVE page and grade it.

    Against the live page, not a re-parsed copy: set_content() drops external
    CSS/JS and page.content() omits shadow roots, so a static round-trip is
    strictly lower fidelity than the page already open here.
    """
    results: list[Outcome] = []
    for entry in entries:
        index: int | None = None
        count = 0
        for i, candidate in enumerate(entry.candidates):
            count = await page.locator(candidate).count()  # type: ignore[attr-defined]
            if count:
                index = i
                break
        results.append(grade(entry, index, count, mode))
    return results


async def run(token: str, project_id: str, surface_key: str) -> int:
    surface = registry.SURFACES[surface_key]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(
                viewport={"width": surface.viewport[0], "height": surface.viewport[1]}
            )
            await ctx.add_cookies(
                [
                    {
                        "name": SESSION_COOKIE,
                        "value": token,
                        "domain": "labs.google",
                        "path": "/",
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "Lax",
                    }
                ]
            )
            page = await ctx.new_page()
            try:
                await page.goto(
                    surface.url_template.format(locale="en", project_id=project_id),
                    wait_until="domcontentloaded",
                    timeout=90_000,
                )
            except PlaywrightError as exc:
                # A raw traceback is neither exit 1 nor exit 2, and its message
                # embeds the project id. Keep the contract intact.
                print(f"::error::navigation failed: {type(exc).__name__}")
                return 2
            for _ in range(25):
                if await page.locator("i.google-symbols").count():
                    break
                await page.wait_for_timeout(1000)
            else:
                # Expired token or missing project — NOT drift. Never conflate them.
                print("::error::surface never hydrated (expired token or missing project)")
                return 2
            # Production's OWN detector, over all six ratio variants. Checking
            # candidates[0] alone misreads a 9:16 classic editor as agentic, and a
            # drifted crop_control then grades EXPECTED_ABSENT — hidden forever.
            mode = (
                UiMode.CLASSIC
                if await factory._any_present(page, factory._CLASSIC_CROP_SELECTORS)  # noqa: SLF001
                else UiMode.AGENTIC
            )
            blocked = await alternate_state(page)
            if blocked is not None:
                # NOT drift: a known cohort/load state hides the composer.
                print(f"::warning::editor is in a known alternate state ({blocked})")
                return 2
            outcomes = await resolve(page, registry.for_surface(surface_key), mode)
        finally:
            await browser.close()

    print(f"observed mode: {mode.value}")
    print(render_report(outcomes))
    return 1 if any(o.is_failure for o in outcomes) else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--surface", default="editor")
    args = p.parse_args()
    token = os.environ.get("GFLOW_CI_SESSION_TOKEN", "")
    project = os.environ.get("GFLOW_CI_PROJECT_ID", "")
    if not token or not project:
        print("::error::GFLOW_CI_SESSION_TOKEN and GFLOW_CI_PROJECT_ID are required")
        return 2
    return asyncio.run(run(token, project, args.surface))


if __name__ == "__main__":
    sys.exit(main())
