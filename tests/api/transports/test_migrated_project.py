"""Project create / rename on flow.google.com (#864).

The fake models what `docs/superpowers/spikes/2026-09-17-project-create-on-flow-google-com.md`
measured: the projects page's `add` FAB fires `jHPbke` and answers `[id, [title]]`; the
project header's `flow-editable-text` input renames through `o8DA4`, answering `[title]`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from gflow_cli.api.transports import migrated_composer as mc
from gflow_cli.errors import TransportTimeoutError, UiSelectorDriftError, WireFormatError

PID = "57c099ee-a5d6-4e2d-83c9-0346f9644b09"


def _frame(rpcid: str, payload: Any) -> str:
    frame = [["wrb.fr", rpcid, json.dumps(payload), None, None, None, "generic"]]
    return ")]}'\n\n10\n" + json.dumps(frame) + "\n"


def _batch(rpcid: str) -> str:
    return f"https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids={rpcid}"


class _Response:
    def __init__(self, url: str, text: str) -> None:
        self.url, self._text = url, text

    async def text(self) -> str:
        if self._text is None:
            raise RuntimeError("response body unavailable")
        return self._text


class _Loc:
    def __init__(self, page: FakePage, sel: str) -> None:
        self.page, self.sel = page, sel

    @property
    def first(self) -> _Loc:
        return self

    async def wait_for(self, **_: Any) -> None:
        if not self.page.present.get(self.sel, False):
            raise mc.PlaywrightTimeoutError(f"waiting for {self.sel}")

    async def click(self, **_: Any) -> None:
        self.page.clicks.append(self.sel)
        await self.page.fire(self.page.on_click.get(self.sel))

    async def input_value(self) -> str:
        return self.page.current_title

    async def fill(self, value: str, **kw: Any) -> None:
        self.page.action_timeouts.append(kw.get("timeout"))
        if self.page.fill_times_out:
            raise mc.PlaywrightTimeoutError("Locator.fill: Timeout 30000ms exceeded")
        self.page.filled = value

    async def press(self, key: str, **kw: Any) -> None:
        self.page.action_timeouts.append(kw.get("timeout"))
        if key == "Enter":
            await self.page.fire(self.page.on_enter)

    async def is_visible(self) -> bool:
        return False

    async def count(self) -> int:
        return 1 if self.page.present.get(self.sel, False) else 0


class FakePage:
    def __init__(self, *, url: str = "about:blank") -> None:
        self.url = url
        self.gotos: list[str] = []
        self.clicks: list[str] = []
        self.filled: str | None = None
        self.current_title = ""
        self.present: dict[str, bool] = {mc.NEW_PROJECT_BUTTON: True, mc.PROJECT_TITLE_INPUT: True}
        self.on_click: dict[str, tuple[str, str] | None] = {}
        self.on_enter: tuple[str, str] | None = None
        self.navigate_on_create: str | None = None
        self.fill_times_out = False
        self.action_timeouts: list[Any] = []
        self.url_waits: list[Any] = []
        self._handlers: list[Any] = []
        self.keyboard = self

    async def wait_for_url(self, predicate: Any, **kw: Any) -> None:
        self.url_waits.append(kw.get("timeout"))
        if not predicate(self.url):
            raise mc.PlaywrightTimeoutError("waiting for navigation")

    async def goto(self, url: str, **_: Any) -> None:
        self.gotos.append(url)
        self.url = url

    def locator(self, sel: str) -> _Loc:
        return _Loc(self, sel)

    def on(self, event: str, handler: Any) -> None:
        if event == "response":
            self._handlers.append(handler)

    def remove_listener(self, event: str, handler: Any) -> None:
        if event == "response" and handler in self._handlers:
            self._handlers.remove(handler)

    async def fire(self, reply: tuple[str, str] | None) -> None:
        if reply is None:
            return
        for h in list(self._handlers):
            await h(_Response(*reply))
        if self.navigate_on_create:
            self.url = self.navigate_on_create


def _page_creating(title: str = "Sep 17 - 17:02", pid: str = PID) -> FakePage:
    page = FakePage()
    page.on_click[mc.NEW_PROJECT_BUTTON] = (
        _batch(mc.CREATE_PROJECT_RPC),
        _frame("jHPbke", [pid, [title]]),
    )
    page.navigate_on_create = f"https://flow.google.com/project/{pid}"
    return page


# --- create -----------------------------------------------------------------------------


async def test_create_reads_id_and_title_from_the_rpc_reply() -> None:
    page = _page_creating(title="Sep 17 - 17:02")
    page.on_enter = (_batch(mc.RENAME_PROJECT_RPC), _frame("o8DA4", ["my film"]))

    info = await mc.create_project(page, "my film")  # type: ignore[arg-type]

    assert info.project_id == PID
    assert info.title == "my film"
    assert page.gotos[0] == "https://flow.google.com/"
    assert page.clicks == [mc.NEW_PROJECT_BUTTON]
    assert page.filled == "my film"  # the requested title was applied by rename


async def test_create_skips_rename_when_the_reply_already_carries_the_title() -> None:
    page = _page_creating(title="my film")

    info = await mc.create_project(page, "my film")  # type: ignore[arg-type]

    assert info.title == "my film"
    assert page.filled is None


async def test_create_without_the_fab_is_drift_naming_the_anchor() -> None:
    page = _page_creating()
    page.present[mc.NEW_PROJECT_BUTTON] = False

    with pytest.raises(UiSelectorDriftError, match="new-project"):
        await mc.create_project(page, "x", timeout_s=0.1)  # type: ignore[arg-type]


async def test_create_with_no_reply_and_no_navigation_is_outcome_unknown() -> None:
    # #1037: the click was sent, so "no reply" is not "not created" -- never retryable.
    from gflow_cli.errors import RpcResponseUnavailableError, is_retryable
    from gflow_cli.json_output import exit_code_for

    page = FakePage()
    page.on_click[mc.NEW_PROJECT_BUTTON] = None

    with pytest.raises(RpcResponseUnavailableError, match=mc.CREATE_PROJECT_RPC) as exc:
        await mc.create_project(page, "x", timeout_s=0.1)  # type: ignore[arg-type]
    assert not isinstance(exc.value, TransportTimeoutError)
    assert not is_retryable(exc.value)
    assert exit_code_for(exc.value) == 7
    assert "--project" in exc.value.remediation_hint
    assert page.clicks == [mc.NEW_PROJECT_BUTTON]  # never clicked twice


async def test_create_reply_without_a_project_id_is_a_wire_format_error() -> None:
    page = _page_creating(pid="not-a-uuid")

    with pytest.raises(WireFormatError, match=mc.CREATE_PROJECT_RPC):
        await mc.create_project(page, "x", timeout_s=0.1)  # type: ignore[arg-type]


async def test_create_ignores_other_rpcs_while_waiting() -> None:
    page = FakePage()
    page.on_click[mc.NEW_PROJECT_BUTTON] = (_batch("UpteDb"), _frame("UpteDb", [[[PID, ["old"]]]]))

    with pytest.raises(WireFormatError, match=mc.CREATE_PROJECT_RPC):
        await mc.create_project(page, "x", timeout_s=0.1)  # type: ignore[arg-type]


# --- rename -----------------------------------------------------------------------------


async def test_rename_navigates_to_the_project_and_confirms_the_echo() -> None:
    page = FakePage()
    page.on_enter = (_batch(mc.RENAME_PROJECT_RPC), _frame("o8DA4", ["new title"]))

    await mc.rename_project(page, PID, "new title")  # type: ignore[arg-type]

    assert page.gotos == [f"https://flow.google.com/project/{PID}"]
    assert page.filled == "new title"


async def test_rename_does_not_reload_a_project_page_already_open() -> None:
    page = FakePage(url=f"https://flow.google.com/project/{PID}")
    page.on_enter = (_batch(mc.RENAME_PROJECT_RPC), _frame("o8DA4", ["t"]))

    await mc.rename_project(page, PID, "t")  # type: ignore[arg-type]

    assert page.gotos == []


async def test_rename_echoing_another_title_is_a_wire_format_error() -> None:
    page = FakePage()
    page.on_enter = (_batch(mc.RENAME_PROJECT_RPC), _frame("o8DA4", ["something else"]))

    with pytest.raises(WireFormatError, match="something else"):
        await mc.rename_project(page, PID, "wanted", timeout_s=0.1)  # type: ignore[arg-type]


async def test_rename_without_the_title_input_is_drift() -> None:
    page = FakePage()
    page.present[mc.PROJECT_TITLE_INPUT] = False

    with pytest.raises(UiSelectorDriftError, match="title"):
        await mc.rename_project(page, PID, "t", timeout_s=0.1)  # type: ignore[arg-type]


def test_anchors_are_structural_not_labels() -> None:
    """AGENTS.md locale-invariance: no display text in these selectors."""
    assert "flow-projects-page" in mc.NEW_PROJECT_BUTTON
    assert "'add'" in mc.NEW_PROJECT_BUTTON or '"add"' in mc.NEW_PROJECT_BUTTON
    assert mc.PROJECT_TITLE_INPUT.startswith("flow-editable-text")


@pytest.mark.parametrize("rpcid", [mc.CREATE_PROJECT_RPC, mc.RENAME_PROJECT_RPC])
async def test_unreadable_reply_reply_is_not_misreported_as_a_timeout(rpcid: str) -> None:
    from gflow_cli.errors import is_retryable

    page = FakePage(url=f"https://flow.google.com/project/{PID}")

    async def action() -> None:
        await page.fire((_batch(rpcid), None))

    with pytest.raises(WireFormatError, match="body was unavailable") as exc:
        await mc.rpc_reply(page, rpcid, action, timeout_s=0.02, write=True)  # type: ignore[arg-type]
    assert exc.value.route == f"batchexecute:{rpcid}"
    assert not is_retryable(exc.value)
    assert "outcome" in exc.value.remediation_hint
    assert not page._handlers


async def test_rename_recovers_persisted_title_without_resubmitting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    page = FakePage(url=f"https://flow.google.com/project/{PID}")
    page.on_enter = (_batch(mc.RENAME_PROJECT_RPC), None)
    readback = AsyncMock(return_value=("wanted", []))
    monkeypatch.setattr(mc, "_project_snapshot", readback, raising=False)
    await mc.rename_project(page, PID, "wanted", timeout_s=0.02)  # type: ignore[arg-type]
    readback.assert_awaited_once_with(page, PID)
    assert page.filled == "wanted"


async def test_create_recovers_created_url_without_another_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    page = _page_creating()
    page.on_click[mc.NEW_PROJECT_BUTTON] = (_batch(mc.CREATE_PROJECT_RPC), None)
    readback = AsyncMock(return_value=("wanted", []))
    monkeypatch.setattr(mc, "_project_snapshot", readback, raising=False)
    got = await mc.create_project(page, "wanted", timeout_s=0.02)  # type: ignore[arg-type]
    assert got.project_id == PID and got.title == "wanted"
    assert page.clicks == [mc.NEW_PROJECT_BUTTON]
    readback.assert_awaited_once_with(page, PID)


async def test_rename_does_not_guess_success_when_readback_disagrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    page = FakePage(url=f"https://flow.google.com/project/{PID}")
    page.on_enter = (_batch(mc.RENAME_PROJECT_RPC), None)
    monkeypatch.setattr(mc, "_project_snapshot", AsyncMock(return_value=("old", [])), raising=False)
    with pytest.raises(WireFormatError, match="body was unavailable"):
        await mc.rename_project(page, PID, "wanted", timeout_s=0.02)  # type: ignore[arg-type]


async def test_rename_to_the_current_title_needs_no_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    monkeypatch.setattr(mc, "_project_snapshot", AsyncMock(return_value=("already saved", [])))
    page = FakePage(url=f"https://flow.google.com/project/{PID}")
    page.current_title = "already saved"
    await mc.rename_project(page, PID, "already saved", timeout_s=0.01)  # type: ignore[arg-type]
    assert page.filled is None and not page._handlers


async def test_local_title_alone_does_not_prove_rename_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    page = FakePage(url=f"https://flow.google.com/project/{PID}")
    page.current_title = "wanted"
    monkeypatch.setattr(mc, "_project_snapshot", AsyncMock(return_value=("old", [])))
    page.on_enter = (_batch(mc.RENAME_PROJECT_RPC), _frame(mc.RENAME_PROJECT_RPC, ["wanted"]))
    await mc.rename_project(page, PID, "wanted", timeout_s=0.01)  # type: ignore[arg-type]
    assert page.filled == "wanted"


# --- #1009 failure branches -------------------------------------------------------------


def test_lost_body_remediation_says_check_before_rerunning() -> None:
    from gflow_cli.errors import RpcResponseUnavailableError

    hint = RpcResponseUnavailableError(detail="x").remediation_hint
    assert "outcome is unknown" in hint
    assert "before re-running" in hint
    assert "simpler prompt" not in hint  # not WireFormatError's generation default


async def test_a_read_rpc_with_a_lost_body_still_times_out() -> None:
    # Only a write's lost body means "outcome unknown"; a read just has no reply yet.
    page = FakePage(url=f"https://flow.google.com/project/{PID}")

    async def action() -> None:
        await page.fire((_batch(mc.RENAME_PROJECT_RPC), None))

    with pytest.raises(TransportTimeoutError, match="1 batchexecute bodies unreadable"):
        await mc.rpc_reply(page, mc.RENAME_PROJECT_RPC, action, timeout_s=0.02)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("url", "before", "expected"),
    [
        (f"https://flow.google.com/project/{PID}", mc.MIGRATED_ROOT_URL, PID),
        (f"https://flow.google.com/project/{PID}/edit?x=1", mc.MIGRATED_ROOT_URL, PID),
        # a redirect's query can carry another project's URL: only the path counts
        (f"https://flow.google.com/?continue=https://flow.google.com/project/{PID}", "", None),
        (f"https://evil.example/project/{PID}", mc.MIGRATED_ROOT_URL, None),
        (f"http://flow.google.com/project/{PID}", mc.MIGRATED_ROOT_URL, None),
        ("https://flow.google.com/project/not-a-uuid", mc.MIGRATED_ROOT_URL, None),
        # the page was already there before the click: nothing proves this create
        (f"https://flow.google.com/project/{PID}", f"https://flow.google.com/project/{PID}", None),
    ],
)
def test_navigated_project_id_reads_the_path_only(url: str, before: str, expected: Any) -> None:
    assert mc._navigated_project_id(url, before) == expected


async def test_create_without_navigation_keeps_the_unknown_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from gflow_cli.errors import RpcResponseUnavailableError

    page = FakePage()
    page.on_click[mc.NEW_PROJECT_BUTTON] = (_batch(mc.CREATE_PROJECT_RPC), None)
    readback = AsyncMock()
    monkeypatch.setattr(mc, "_project_snapshot", readback)
    with pytest.raises(RpcResponseUnavailableError):
        await mc.create_project(page, "wanted", timeout_s=0.02)  # type: ignore[arg-type]
    assert page.clicks == [mc.NEW_PROJECT_BUTTON]  # never clicked twice
    readback.assert_not_awaited()


async def test_create_ignores_a_project_url_carried_in_the_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from gflow_cli.errors import RpcResponseUnavailableError

    page = FakePage()
    page.on_click[mc.NEW_PROJECT_BUTTON] = (_batch(mc.CREATE_PROJECT_RPC), None)
    page.navigate_on_create = (
        f"https://accounts.google.com/x?continue=https://flow.google.com/project/{PID}"
    )
    readback = AsyncMock()
    monkeypatch.setattr(mc, "_project_snapshot", readback)
    with pytest.raises(RpcResponseUnavailableError):
        await mc.create_project(page, "wanted", timeout_s=0.02)  # type: ignore[arg-type]
    readback.assert_not_awaited()


@pytest.mark.parametrize("which", ["create", "rename"])
async def test_a_failing_readback_keeps_the_unknown_outcome(
    monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    from unittest.mock import AsyncMock

    from gflow_cli.errors import RpcResponseUnavailableError

    monkeypatch.setattr(mc, "_project_snapshot", AsyncMock(side_effect=RuntimeError("tab died")))
    if which == "create":
        page = _page_creating()
        page.on_click[mc.NEW_PROJECT_BUTTON] = (_batch(mc.CREATE_PROJECT_RPC), None)
        call = mc.create_project(page, "wanted", timeout_s=0.02)  # type: ignore[arg-type]
    else:
        page = FakePage(url=f"https://flow.google.com/project/{PID}")
        page.on_enter = (_batch(mc.RENAME_PROJECT_RPC), None)
        call = mc.rename_project(page, PID, "wanted", timeout_s=0.02)  # type: ignore[arg-type]
    with pytest.raises(RpcResponseUnavailableError, match="body was unavailable"):
        await call


# --- _project_snapshot itself -----------------------------------------------------------


class _Field:
    def __init__(self, probe: _Probe) -> None:
        self.probe = probe
        self.first = self

    async def wait_for(self, **_: Any) -> None:
        return None

    async def input_value(self) -> str:
        return self.probe.title


class _Probe:
    def __init__(self, reads: list[list[dict[str, str]]], title: str) -> None:
        self.reads, self.title = reads, title
        self.gotos: list[str] = []
        self.reloads = 0
        self.closed = False

    async def goto(self, url: str, **_: Any) -> None:
        self.gotos.append(url)

    def locator(self, _sel: str) -> _Field:
        return _Field(self)

    async def evaluate(self, _js: str) -> list[dict[str, str]]:
        return self.reads[min(self.reloads, len(self.reads) - 1)]

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    async def reload(self, **_: Any) -> None:
        self.reloads += 1

    async def close(self) -> None:
        self.closed = True


class _Context:
    def __init__(self, probe: _Probe) -> None:
        self.probe = probe

    async def new_page(self) -> _Probe:
        return self.probe


class _Owner:
    def __init__(self, probe: _Probe) -> None:
        self.context = _Context(probe)
        self.fronted = False

    async def bring_to_front(self) -> None:
        self.fronted = True


MEDIA = "11111111-2222-4333-8444-555555555555"


async def test_snapshot_polls_until_the_tile_has_its_media_id() -> None:
    named = [{"name": "u.png", "id": ""}]
    ready = [{"name": "u.png", "id": MEDIA}]
    probe = _Probe([named, named, ready], title="t")
    owner = _Owner(probe)
    title, tiles = await mc._project_snapshot(owner, PID, upload_name="u.png")  # type: ignore[arg-type]
    assert (title, tiles) == ("t", ready)
    assert probe.reloads == 2
    assert probe.gotos == [mc.MIGRATED_PROJECT_URL.format(project_id=PID)]
    assert probe.closed and owner.fronted


async def test_snapshot_gives_up_at_its_deadline_and_still_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter([0.0, 0.0, 100.0])
    monkeypatch.setattr(mc, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    probe = _Probe([[{"name": "other.png", "id": MEDIA}]], title="t")
    owner = _Owner(probe)
    _, tiles = await mc._project_snapshot(owner, PID, upload_name="u.png")  # type: ignore[arg-type]
    assert tiles == [{"name": "other.png", "id": MEDIA}]
    assert probe.closed and owner.fronted


async def test_snapshot_closes_the_probe_when_reading_fails() -> None:
    probe = _Probe([], title="t")

    async def boom(_js: str) -> Any:
        raise RuntimeError("target closed")

    probe.evaluate = boom  # type: ignore[method-assign]
    owner = _Owner(probe)
    with pytest.raises(RuntimeError):
        await mc._project_snapshot(owner, PID, upload_name="u.png")  # type: ignore[arg-type]
    assert probe.closed and owner.fronted


# --- #1037: a create/rename reply that times out after the click ------------------------


def _unrelated_reply() -> tuple[str, str]:
    # Some other rpc answers; ours never does. The fake navigates after any reply.
    return (_batch("UpteDb"), _frame("UpteDb", []))


async def test_create_timeout_then_navigation_recovers_the_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    page = _page_creating()
    page.on_click[mc.NEW_PROJECT_BUTTON] = _unrelated_reply()
    readback = AsyncMock(return_value=("wanted", []))
    monkeypatch.setattr(mc, "_project_snapshot", readback)

    got = await mc.create_project(page, "wanted", timeout_s=0.02)  # type: ignore[arg-type]

    assert got.project_id == PID and got.title == "wanted"
    assert page.clicks == [mc.NEW_PROJECT_BUTTON]
    readback.assert_awaited_once_with(page, PID)
    # The navigation may land after the reply ceiling: the look is never a 0 s glance.
    assert page.url_waits and page.url_waits[0] >= 5_000


async def test_create_timeout_ignores_a_project_url_carried_in_the_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from gflow_cli.errors import RpcResponseUnavailableError

    page = FakePage()
    page.on_click[mc.NEW_PROJECT_BUTTON] = _unrelated_reply()
    page.navigate_on_create = (
        f"https://accounts.google.com/x?continue=https://flow.google.com/project/{PID}"
    )
    monkeypatch.setattr(mc, "_project_snapshot", AsyncMock())
    with pytest.raises(RpcResponseUnavailableError):
        await mc.create_project(page, "wanted", timeout_s=0.02)  # type: ignore[arg-type]


async def test_create_lost_body_remediation_names_the_project_flag() -> None:
    page = FakePage()
    page.on_click[mc.NEW_PROJECT_BUTTON] = (_batch(mc.CREATE_PROJECT_RPC), None)
    with pytest.raises(WireFormatError, match="body was unavailable") as exc:
        await mc.create_project(page, "wanted", timeout_s=0.02)  # type: ignore[arg-type]
    assert "--project" in exc.value.remediation_hint


async def test_rename_timeout_recovers_by_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    page = FakePage(url=f"https://flow.google.com/project/{PID}")
    page.on_enter = None
    readback = AsyncMock(return_value=("wanted", []))
    monkeypatch.setattr(mc, "_project_snapshot", readback)

    await mc.rename_project(page, PID, "wanted", timeout_s=0.02)  # type: ignore[arg-type]

    readback.assert_awaited_once_with(page, PID)


@pytest.mark.parametrize("readback_says", ["old", RuntimeError("tab died")])
async def test_rename_timeout_without_proof_is_outcome_unknown(
    monkeypatch: pytest.MonkeyPatch, readback_says: Any
) -> None:
    from unittest.mock import AsyncMock

    from gflow_cli.errors import RpcResponseUnavailableError, is_retryable

    page = FakePage(url=f"https://flow.google.com/project/{PID}")
    page.on_enter = None
    side: dict[str, Any] = (
        {"side_effect": readback_says}
        if isinstance(readback_says, Exception)
        else {"return_value": (readback_says, [])}
    )
    monkeypatch.setattr(mc, "_project_snapshot", AsyncMock(**side))
    with pytest.raises(RpcResponseUnavailableError, match=mc.RENAME_PROJECT_RPC) as exc:
        await mc.rename_project(page, PID, "wanted", timeout_s=0.02)  # type: ignore[arg-type]
    assert not is_retryable(exc.value)
    assert PID in exc.value.remediation_hint


async def test_rename_fill_and_press_carry_explicit_timeouts() -> None:
    page = FakePage(url=f"https://flow.google.com/project/{PID}")
    page.on_enter = (_batch(mc.RENAME_PROJECT_RPC), _frame("o8DA4", ["t"]))
    await mc.rename_project(page, PID, "t")  # type: ignore[arg-type]
    assert len(page.action_timeouts) == 2 and all(page.action_timeouts)


async def test_rename_fill_timeout_is_typed_drift_not_a_raw_playwright_error() -> None:
    page = FakePage(url=f"https://flow.google.com/project/{PID}")
    page.fill_times_out = True
    with pytest.raises(UiSelectorDriftError, match="title"):
        await mc.rename_project(page, PID, "t", timeout_s=0.02)  # type: ignore[arg-type]


def test_page_rpc_ceiling_is_one_constant_and_selector_waits_stay_short() -> None:
    import inspect

    assert mc.PAGE_RPC_TIMEOUT_S >= 120
    for fn in (mc.create_project, mc.rename_project):
        assert inspect.signature(fn).parameters["timeout_s"].default == mc.PAGE_RPC_TIMEOUT_S
    # read_credits keeps its quick /about diagnosis.
    assert inspect.signature(mc.read_credits).parameters["timeout_s"].default == 30.0


async def test_create_anchor_wait_stays_at_the_selector_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[float] = []
    real = mc._wait_anchor

    async def spy(*a: Any, timeout_s: float, **kw: Any) -> Any:
        seen.append(timeout_s)
        return await real(*a, timeout_s=timeout_s, **kw)

    monkeypatch.setattr(mc, "_wait_anchor", spy)
    await mc.create_project(_page_creating(title="t"), "t")  # type: ignore[arg-type]
    assert seen == [mc.ANCHOR_TIMEOUT_S] and mc.ANCHOR_TIMEOUT_S == 30.0
class TestIsAt:
    """Whether a page is ON a route — not under it (#639 option C, live 2026-10-16).

    A prefix test accepted `/project/<id>/character/<eid>` as `/project/<id>`: after an
    import left the page on a character editor, image generation drove the editor's
    prompt box and misreported the account as agent-only (exit 25), and a project
    rename would have typed into the CHARACTER's title field (same `flow-editable-text`).
    """

    _P = "https://flow.google.com/project/p1"

    def test_the_route_itself_with_a_query_is_at_it(self) -> None:
        from gflow_cli.api.transports.migrated_composer import is_at

        assert is_at(self._P + "?pli=1", self._P)
        assert is_at(self._P + "/", self._P)

    def test_a_sub_route_is_not_at_it(self) -> None:
        from gflow_cli.api.transports.migrated_composer import is_at

        assert not is_at(self._P + "/character/e1", self._P)
        assert not is_at(self._P + "/character", self._P)

    def test_another_project_or_host_is_not_at_it(self) -> None:
        from gflow_cli.api.transports.migrated_composer import is_at

        assert not is_at("https://flow.google.com/project/p10", self._P)
        assert not is_at("https://labs.google/project/p1", self._P)
        assert not is_at("", self._P)
