"""Unit tests for the robust agentic↔classic mode-control component.

A tiny fake composer state machine (media / agent / sidebar) drives the same
transitions the live round-trip proved, so the switch logic is verified without
a browser: aria-pressed is the source of truth, apps_spark_2 is never consulted,
and clicks are state-aware (never blind).
"""

from __future__ import annotations

import pytest

from gflow_cli.api.transports import mode_control as mc


class _FakeLocator:
    def __init__(self, page: _FakePage, sel: str) -> None:
        self._page = page
        self._sel = sel

    @property
    def first(self) -> _FakeLocator:
        return self

    async def count(self) -> int:
        return self._page.count(self._sel)

    async def get_attribute(self, name: str) -> str | None:
        return self._page.attr(self._sel, name)

    async def click(self, **kw: object) -> None:
        mode = self._page.raise_unforced_click
        if mode and not kw.get("force") and self._sel == mc.AGENT_TOGGLE_SELECTOR:
            self._page.raise_unforced_click = None  # raise once
            if mode == "after":
                self._page.click(self._sel, kw)
            raise RuntimeError(f"click failed ({mode}-dispatch)")
        self._page.click(self._sel, kw)


class _FakePage:
    """Models Flow's composer: state in {media, agent, sidebar, pinned, stuck}.

    - media:  crop_* present; toggle present with aria-pressed=false.
    - agent:  no crop; toggle present with aria-pressed=true; expand available.
    - sidebar: no crop; no in-composer toggle; the sidebar X (close) present.
    - sidebar_unscoped: the #493 cohort — sidebar open, but its edit_square-scoped
      X does NOT match; only the unscoped fallback can close it.
    - pinned: server-pinned agentic arm — toggle present (aria-pressed=true),
      clicking flips aria to false BUT the crop panel never mounts in place
      (state → pinned_off); only a reload after the persisted toggle-off
      brings classic back (pinned_off → media). Models the 2026-07-17 incident.
    """

    def __init__(
        self,
        state: str = "media",
        raise_unforced_click: str | None = None,
        blank_waits: int = 0,
    ) -> None:
        self.state = state
        self.clicks: list[str] = []
        self.click_kwargs: list[dict[str, object]] = []
        self.reloads = 0
        self.reload_kwargs: list[dict[str, object]] = []
        # "before": unforced toggle click raises WITHOUT dispatching;
        # "after": dispatches the state change, THEN raises (post-click
        # instability — Playwright can raise after the events fired).
        self.raise_unforced_click = raise_unforced_click
        # SPA render race: while > 0 the page is a blank shell (every selector
        # counts 0); each wait_for_timeout tick "renders" one step closer.
        self.blank_waits = blank_waits

    def locator(self, sel: str) -> _FakeLocator:
        return _FakeLocator(self, sel)

    async def wait_for_timeout(self, _ms: int) -> None:
        if self.blank_waits > 0:
            self.blank_waits -= 1
        return None

    async def reload(self, **kw: object) -> None:
        self.reloads += 1
        self.reload_kwargs.append(kw)
        if self.state == "pinned_off":
            # The toggle click persisted isAgentModeToggled=false server-side;
            # the fresh load mounts the classic composer.
            self.state = "media"

    def count(self, sel: str) -> int:
        if self.blank_waits > 0:
            return 0  # nothing has rendered yet
        if sel in mc.CROP_SELECTORS:
            return 1 if self.state == "media" else 0
        if sel == mc.AGENT_TOGGLE_SELECTOR:
            return 1 if self.state in ("media", "agent", "pinned", "pinned_off") else 0
        if sel == mc.SIDEBAR_CLOSE_SELECTOR:
            # "sidebar_unscoped" = the #493 cohort: the sidebar exists but its
            # edit_square-scoped X never matches, so only the fallback can find it.
            return 1 if self.state == "sidebar" else 0
        if sel == mc.SIDEBAR_CLOSE_FALLBACK_SELECTOR:
            return 1 if self.state in ("sidebar", "sidebar_unscoped") else 0
        return 0

    def attr(self, sel: str, name: str) -> str | None:
        if sel == mc.AGENT_TOGGLE_SELECTOR and name == "aria-pressed":
            if self.state in ("agent", "pinned"):
                return "true"
            if self.state in ("media", "pinned_off"):
                return "false"
        return None

    def click(self, sel: str, kw: dict[str, object] | None = None) -> None:
        self.clicks.append(sel)
        self.click_kwargs.append(kw or {})
        if sel == mc.SIDEBAR_CLOSE_FALLBACK_SELECTOR and self.state == "sidebar_unscoped":
            self.state = "media"
            return
        if sel == mc.AGENT_TOGGLE_SELECTOR:
            if self.state == "pinned":
                self.state = "pinned_off"
            else:
                self.state = "agent" if self.state == "media" else "media"
        elif sel == mc.SIDEBAR_CLOSE_SELECTOR:
            # Closing the sidebar returns to the composer, still agent-on
            # (aria-pressed=true) — matches the live round-trip (03_after_close).
            self.state = "agent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected"),
    [("media", "media"), ("agent", "agent"), ("stuck", "unknown")],
)
async def test_read_mode(state: str, expected: str) -> None:
    page = _FakePage(state)
    assert await mc.read_mode(page) == expected  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_ensure_media_noop_when_already_media() -> None:
    page = _FakePage("media")
    acted = await mc.ensure_media_mode(page)  # type: ignore[arg-type]
    assert acted is False
    assert page.clicks == []  # no blind click
    assert page.state == "media"


@pytest.mark.asyncio
async def test_ensure_media_from_agent_toggles_off() -> None:
    page = _FakePage("agent")
    acted = await mc.ensure_media_mode(page)  # type: ignore[arg-type]
    assert acted is True
    assert page.clicks == [mc.AGENT_TOGGLE_SELECTOR]
    assert page.state == "media"


@pytest.mark.asyncio
async def test_ensure_media_from_sidebar_closes_then_toggles() -> None:
    page = _FakePage("sidebar")
    acted = await mc.ensure_media_mode(page)  # type: ignore[arg-type]
    assert acted is True
    # X first (sidebar → agent), then toggle off (agent → media).
    assert page.clicks == [mc.SIDEBAR_CLOSE_SELECTOR, mc.AGENT_TOGGLE_SELECTOR]
    assert page.state == "media"


@pytest.mark.asyncio
async def test_ensure_media_gives_up_when_nothing_actionable() -> None:
    page = _FakePage("stuck")
    acted = await mc.ensure_media_mode(page)  # type: ignore[arg-type]
    assert acted is False
    assert page.clicks == []
    # No toggle was clicked, so the reload rescue must not fire either.
    assert page.reloads == 0


@pytest.mark.asyncio
async def test_pinned_arm_recovers_via_reload() -> None:
    # 2026-07-17 incident shape: the toggle flips aria-pressed but the classic
    # panel never mounts in place — only a reload (which re-rolls the arm AND
    # mounts the server-persisted isAgentModeToggled=false) restores classic.
    page = _FakePage("pinned")
    acted = await mc.ensure_media_mode(page, allow_reload=True)  # type: ignore[arg-type]
    assert acted is True
    assert page.reloads == 1
    assert page.state == "media"
    assert mc.AGENT_TOGGLE_SELECTOR in page.clicks


@pytest.mark.asyncio
async def test_reload_requires_opt_in() -> None:
    # Mid-flow callers (image/video mode switches after driver binding) must
    # NEVER get a surprise navigation — reload is opt-in for the pre-bind
    # get_ui_driver path only.
    page = _FakePage("pinned")
    await mc.ensure_media_mode(page)  # type: ignore[arg-type]
    assert page.reloads == 0
    assert page.state == "pinned_off"  # toggle still clicked (old semantics)


@pytest.mark.asyncio
async def test_clean_toggle_does_not_reload() -> None:
    page = _FakePage("agent")
    await mc.ensure_media_mode(page, allow_reload=True)  # type: ignore[arg-type]
    assert page.reloads == 0


@pytest.mark.asyncio
async def test_force_fallback_does_not_arm_reload() -> None:
    # Unforced click fails WITHOUT dispatching → force fallback lands the DOM
    # flip, but nothing was persisted server-side — the reload premise is
    # false, so no navigation may fire even with allow_reload=True.
    page = _FakePage("pinned", raise_unforced_click="before")
    await mc.ensure_media_mode(page, allow_reload=True)  # type: ignore[arg-type]
    assert any(kw.get("force") for kw in page.click_kwargs)  # fallback used
    assert page.reloads == 0
    assert page.state == "pinned_off"


@pytest.mark.asyncio
async def test_waits_for_composer_render_before_probing() -> None:
    # LIVE_VERIFICATION_v0.38.1 finding: on a fresh navigation the composer
    # renders a beat later — probing the blank shell read as "nothing
    # actionable" and the whole rescue no-op'd in ~100ms without ever clicking
    # the toggle. The initial readiness wait must absorb the render race.
    page = _FakePage("pinned", blank_waits=3)
    acted = await mc.ensure_media_mode(page, allow_reload=True)  # type: ignore[arg-type]
    assert acted is True
    assert mc.AGENT_TOGGLE_SELECTOR in page.clicks  # toggle WAS reached
    assert page.reloads == 1
    assert page.state == "media"


@pytest.mark.asyncio
async def test_post_dispatch_click_failure_never_double_clicks() -> None:
    # Playwright can raise AFTER the click events dispatched. A blind force
    # fallback would click the now-OFF toggle and re-enable agent mode; the
    # fallback must re-read aria-pressed first.
    page = _FakePage("agent", raise_unforced_click="after")
    await mc.ensure_media_mode(page)  # type: ignore[arg-type]
    assert page.state == "media"
    assert page.clicks == [mc.AGENT_TOGGLE_SELECTOR]  # exactly one dispatch
    assert not any(kw.get("force") for kw in page.click_kwargs)


@pytest.mark.asyncio
async def test_toggle_click_is_unforced() -> None:
    # force=True bypasses actionability AND can skip the React handler that
    # fires the persisting tRPC mutation — the toggle must get a real click.
    page = _FakePage("agent")
    await mc.ensure_media_mode(page)  # type: ignore[arg-type]
    toggle_kwargs = [
        kw
        for sel, kw in zip(page.clicks, page.click_kwargs, strict=True)
        if sel == mc.AGENT_TOGGLE_SELECTOR
    ]
    assert toggle_kwargs and all(not kw.get("force") for kw in toggle_kwargs)


# ---------------------------------------------------------------------------
# ensure_agent_mode (#299 PR-B) — the symmetric agentic direction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_agent_noop_when_already_agent() -> None:
    page = _FakePage(state="agent")
    assert await mc.ensure_agent_mode(page) is False  # type: ignore[arg-type]
    assert page.clicks == []


@pytest.mark.asyncio
async def test_ensure_agent_noop_when_sidebar_open() -> None:
    # The expanded chat sidebar IS the agent surface — no clicking needed.
    page = _FakePage(state="sidebar")
    assert await mc.ensure_agent_mode(page) is False  # type: ignore[arg-type]
    assert page.clicks == []


@pytest.mark.asyncio
async def test_ensure_agent_real_click_from_media() -> None:
    page = _FakePage(state="media")
    assert await mc.ensure_agent_mode(page) is True  # type: ignore[arg-type]
    assert page.state == "agent"
    # REAL click first — never force-first (a forced click can flip the DOM
    # without firing the React handler that persists the server pref).
    assert page.click_kwargs[0].get("force") is None


@pytest.mark.asyncio
async def test_ensure_agent_force_fallback_only_after_rereading_aria() -> None:
    # Unforced click raises BEFORE dispatch -> aria still "false" -> force retry.
    page = _FakePage(state="media", raise_unforced_click="before")
    assert await mc.ensure_agent_mode(page) is True  # type: ignore[arg-type]
    assert page.state == "agent"
    assert any(kw.get("force") for kw in page.click_kwargs)


@pytest.mark.asyncio
async def test_ensure_agent_no_double_click_on_post_dispatch_raise() -> None:
    # Click dispatches (media->agent) THEN raises: aria now reads "true" — a
    # blind force retry would toggle agent OFF again (the classic-direction
    # hazard, mirrored).
    page = _FakePage(state="media", raise_unforced_click="after")
    assert await mc.ensure_agent_mode(page) is True  # type: ignore[arg-type]
    assert page.state == "agent"
    assert not any(kw.get("force") for kw in page.click_kwargs)


@pytest.mark.asyncio
async def test_ensure_agent_unknown_cohort_noops() -> None:
    # #493-shaped variant: no crop, no toggle, no sidebar -> never loop, never
    # click; the factory's verify turns this into the clean exit-28/25 abort.
    page = _FakePage(state="void")
    assert await mc.ensure_agent_mode(page) is False  # type: ignore[arg-type]
    assert page.clicks == []


@pytest.mark.asyncio
async def test_reload_carries_explicit_timeout() -> None:
    # code-review finding: a bare page.reload() rides Playwright's 30s default
    # OUTSIDE every mode_control budget — the sanctioned reload must be bounded.
    page = _FakePage(state="pinned")
    await mc.ensure_media_mode(page, allow_reload=True)  # type: ignore[arg-type]
    assert page.reloads == 1
    assert page.reload_kwargs[0].get("timeout") == mc._RELOAD_TIMEOUT_MS


@pytest.mark.asyncio
async def test_ensure_agent_survives_render_race() -> None:
    # #267 regression lock (carried over from the deleted force-mode tests):
    # an instant probe on the blank SPA shell must not no-op — the composer
    # wait absorbs the deferred mount before any probing.
    page = _FakePage(state="media", blank_waits=3)
    assert await mc.ensure_agent_mode(page) is True  # type: ignore[arg-type]
    assert page.state == "agent"


class TestSidebarCloseFallback:
    """#493: expanding the chat sidebar removes the classic composer entirely —
    no crop_* trigger AND no Agent pill, which is exactly the reported
    fingerprint. Recovery hinges on SIDEBAR_CLOSE_SELECTOR, which is scoped to
    the sidebar's `edit_square` affordance; a cohort whose sidebar lacks that
    ligature never finds the X, so the composer never returns and the run dies
    with exit 23.

    Reproduced live 2026-08-14, and A/B-proven: with the scoped selector
    neutered the fallback recovers; with BOTH neutered it does not."""

    @pytest.mark.asyncio
    async def test_recovers_when_the_scoped_selector_misses(self) -> None:
        page = _FakePage(state="sidebar_unscoped")
        acted = await mc.ensure_media_mode(page)
        assert acted is True
        assert await mc._crop_present(page) is True  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_does_not_recover_when_no_close_affordance_exists(self) -> None:
        """Negative control — the loop alone does NOT rescue. With no close
        button of either shape, ensure_media_mode must give up and leave the
        caller's own probe to fail loudly (the documented contract)."""
        page = _FakePage(state="stuck")  # no crop, no pill, no close of any kind
        await mc.ensure_media_mode(page)
        assert await mc._crop_present(page) is False  # noqa: SLF001
        assert mc.SIDEBAR_CLOSE_FALLBACK_SELECTOR not in page.clicks

    @pytest.mark.asyncio
    async def test_fallback_is_not_used_when_the_agent_pill_is_present(self) -> None:
        """The unscoped fallback is safe ONLY in the stuck state. With a pill on
        screen the composer still exists, so the normal toggle path must run."""
        page = _FakePage(state="agent")  # pill present, aria-pressed=true
        await mc.ensure_media_mode(page)
        assert mc.AGENT_TOGGLE_SELECTOR in page.clicks, page.clicks
        assert mc.SIDEBAR_CLOSE_FALLBACK_SELECTOR not in page.clicks, page.clicks
