"""Every Flow navigation settles before the next action (issues #580, #584).

`page.goto(wait_until="domcontentloaded")` returns BEFORE Flow's locale redirect
lands — measured 591-797 ms with the redirect after. Whatever runs next operates
on a page about to be navigated away: overlay clicks land on a leaving page, and
`page.evaluate` raises "Execution context was destroyed".

#580 settled three sites. An audit found four more, including
`evaluate_fetch.refresh_auth` — an auth path that reported success while the page
was still moving.

This test is a ratchet: it fails when a NEW unsettled navigation appears, so the
audit does not have to be redone by hand.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_TRANSPORTS = Path(__file__).resolve().parents[3] / "src" / "gflow_cli" / "api"

#: Sites whose existing wait already absorbs the redirect. `networkidle` waits for
#: the network to go quiet, which a redirect cannot do without being observed; the
#: bearer site additionally sleeps 5 s. Listed explicitly so the exemption is a
#: decision on the record rather than an oversight.
_ABSORBED_BY_EXISTING_WAIT = {
    ("ui_automation.py", "networkidle"),
    ("bearer.py", "networkidle"),
}


def _goto_calls(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, wait_until) for every `*.goto(...)` in a module."""
    out: list[tuple[int, str]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "goto"
        ):
            wait = ""
            for kw in node.keywords:
                if kw.arg == "wait_until" and isinstance(kw.value, ast.Constant):
                    wait = str(kw.value.value)
            out.append((node.lineno, wait))
    return out


_SETTLE_CALLS = frozenset({"await_url_settled", "_settle_if_redirecting"})


def _settle_lines(path: Path) -> set[int]:
    """Line numbers of every `await_url_settled(...)` call."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.lineno
        for n in ast.walk(tree)
        # `await_url_settled(page)` directly, or the `_settle_if_redirecting`
        # wrapper that gates it on "does this account actually redirect?" (#587).
        # The ratchet must know BOTH names: renaming the call site is exactly how
        # a settle silently disappears from under this test.
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id in _SETTLE_CALLS)
            or (isinstance(n.func, ast.Attribute) and n.func.attr in _SETTLE_CALLS)
        )
    }


@pytest.mark.parametrize(
    "relpath",
    [
        "transports/ui_automation.py",
        "transports/experimental/evaluate_fetch.py",
        "transports/experimental/sapisidhash.py",
        "transports/experimental/bearer.py",
    ],
)
def test_every_goto_is_followed_by_a_settle(relpath: str) -> None:
    """A `goto` must be followed by `await_url_settled` within a few lines.

    Proximity rather than exactness: the settle is the next statement in every
    current case, and a small window tolerates an intervening log line without
    letting an unsettled navigation hide further down the function.
    """
    path = _TRANSPORTS / relpath
    settles = _settle_lines(path)
    unsettled = [
        (lineno, wait)
        for lineno, wait in _goto_calls(path)
        if (path.name, wait) not in _ABSORBED_BY_EXISTING_WAIT
        and not any(lineno < s <= lineno + 12 for s in settles)
    ]
    assert not unsettled, (
        f"{relpath}: navigation(s) at line(s) "
        f"{[ln for ln, _ in unsettled]} are not followed by await_url_settled(). "
        "Flow's locale redirect lands after goto returns (#580/#584); whatever "
        "runs next operates on a page about to navigate away."
    )
