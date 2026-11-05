"""The login window opens on screen even when the profile remembers an off-screen spot.

Chrome saves the position of the off-screen generation window (#923) in the profile. On
the next launch it pulls that spot back only until about 30 px shows (measured: x=-1268,
y=0), so `gflow auth login` opened as a sliver at the screen's edge. Only a real Chrome
can show that. $0: a throwaway profile, no account, nothing signed in.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.auth.internal_chromium import login_launch_kwargs
from gflow_cli.auth.strategies import async_playwright

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

_OFF_SCREEN = {
    "left": -21845,
    "top": -21845,
    "right": -20547,
    "bottom": -21027,
    "maximized": False,
    "work_area_left": 0,
    "work_area_top": 0,
    "work_area_right": 1280,
    "work_area_bottom": 752,
}


async def _opened_at(profile: Path, kwargs: dict[str, Any]) -> tuple[int, int]:
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Preferences").write_text(
        json.dumps({"browser": {"window_placement": _OFF_SCREEN}}), encoding="utf-8"
    )
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(**kwargs)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            return tuple(await page.evaluate("() => [window.screenX, window.screenY]"))
        finally:
            await ctx.close()


async def test_login_overrides_a_saved_off_screen_position(tmp_path: Path) -> None:
    profile = tmp_path / "login"
    x, y = await _opened_at(profile, login_launch_kwargs(profile, False, channel="chrome"))
    assert x >= 0 and y >= 0, (x, y)


async def test_control_without_the_flag_chrome_leaves_it_mostly_off_screen(tmp_path: Path) -> None:
    """Without this arm the test above would pass on a Chrome that ignores saved positions."""
    profile = tmp_path / "control"
    kwargs = login_launch_kwargs(profile, False, channel="chrome")
    kwargs["args"] = [a for a in kwargs["args"] if not a.startswith("--window-position")]
    x, y = await _opened_at(profile, kwargs)
    assert x < 0, (x, y)
