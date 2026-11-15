"""FlowApiClient character reads: which host does the work (#639, #875).

labs.google's ``flow.projectInitialData`` answers 404 "Flow RPCs have been deprecated"
(401 without a labs token), measured on the projects route on 2026-09-17 and on the
character read on 2026-09-20 (KNOWN_ISSUES.md). The client falls back to flow.google.com
on THAT observed refusal — the same rule as ``create_project`` (#864): never on host
membership, never when the operator pinned labs.google, never for an unrelated failure.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.character import Character, Voice
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.migrated_character import ProjectRead, read_project
from gflow_cli.config import Settings
from gflow_cli.errors import (
    AuthExpiredError,
    ConfigurationError,
    FlowAppError,
    TransportTimeoutError,
    WafRejectionError,
    WireFormatError,
)

PID = "00000000-0000-4000-8000-000000000003"
NORA = Character(
    entity_id="00000000-0000-4000-8000-000000000004",
    display_name="Nora",
    project_id=PID,
    workflow_ids=("wf-portrait", "wf-body"),
    voice="Aoede",
    personality="calm",
    thumbnail_media_id=None,
)
LIVE_VOICE = Voice("Enceladus", "Male, breathy, lower pitch")
READ = ProjectRead(characters=(NORA,), voices=(LIVE_VOICE,))
RETIRED = WireFormatError(detail="Flow RPCs have been deprecated and disabled.", status=404)
NO_TOKEN = AuthExpiredError(detail="UNAUTHORIZED", status=401)
LABS_EMPTY: dict[str, Any] = {"result": {"data": {"json": {"projectContents": {}}}}}


def _client(tmp_path: Path, flow_host: str, labs: Any) -> FlowApiClient:
    c = FlowApiClient(profile_dir=tmp_path / "prof", settings=Settings(flow_host=flow_host))
    c._page = MagicMock()  # noqa: SLF001 - checkout affordance for unentered clients
    c._get_json = (  # type: ignore[method-assign]
        AsyncMock(side_effect=labs) if isinstance(labs, Exception) else AsyncMock(return_value=labs)
    )
    return c


@pytest.fixture
def migrated() -> Any:
    with patch(
        "gflow_cli.api.transports.migrated_character.read_project",
        new=AsyncMock(return_value=READ),
    ) as read:
        yield read


async def test_pinned_flow_google_com_reads_there_and_never_calls_labs(
    tmp_path: Path, migrated: Any
) -> None:
    c = _client(tmp_path, "flow.google.com", LABS_EMPTY)

    chars = await c.list_characters(PID)

    assert chars == [NORA]
    c._get_json.assert_not_awaited()  # type: ignore[attr-defined]
    assert migrated.await_args.args[1] == PID


@pytest.mark.parametrize("refusal", [RETIRED, NO_TOKEN], ids=["404-retired", "401-no-token"])
async def test_auto_falls_back_on_the_observed_refusal(
    tmp_path: Path, migrated: Any, refusal: Exception
) -> None:
    c = _client(tmp_path, "auto", refusal)

    assert await c.list_characters(PID) == [NORA]
    migrated.assert_awaited_once()


async def test_auto_keeps_a_working_labs_read(tmp_path: Path, migrated: Any) -> None:
    c = _client(tmp_path, "auto", LABS_EMPTY)

    assert await c.list_characters(PID) == []
    migrated.assert_not_awaited()


async def test_pinned_labs_never_falls_back(tmp_path: Path, migrated: Any) -> None:
    c = _client(tmp_path, "labs.google", RETIRED)

    with pytest.raises(WireFormatError):
        await c.list_characters(PID)
    migrated.assert_not_awaited()


@pytest.mark.parametrize(
    "other",
    [WafRejectionError(detail="waf", status=403), WireFormatError(detail="bad", status=400)],
    ids=["403", "400"],
)
async def test_other_failures_are_not_a_reason_to_switch_host(
    tmp_path: Path, migrated: Any, other: Exception
) -> None:
    c = _client(tmp_path, "auto", other)

    with pytest.raises(type(other)):
        await c.list_characters(PID)
    migrated.assert_not_awaited()


async def test_show_by_name_works_through_the_migrated_read(tmp_path: Path, migrated: Any) -> None:
    c = _client(tmp_path, "flow.google.com", LABS_EMPTY)

    assert await c.get_character(PID, name="Nora") == NORA
    with pytest.raises(ConfigurationError):
        await c.get_character(PID, name="Nobody")


@pytest.mark.parametrize("host", ["auto", "flow.google.com"])
async def test_migrated_read_false_does_not_fall_back_to_flow_google_com(
    tmp_path: Path, migrated: Any, host: str
) -> None:
    c = _client(tmp_path, host, RETIRED)

    with pytest.raises(WireFormatError):
        await c.list_characters(PID, migrated_read=False)
    migrated.assert_not_awaited()


# --- read_project against a fake page ---------------------------------------------


class _Resp:
    def __init__(self, url: str, text: str) -> None:
        self.url, self._text = url, text

    async def text(self) -> str:
        return self._text


class _Page:
    """Delivers Zzl0ze frames on navigation — to listeners attached at that moment."""

    def __init__(
        self,
        payload: list[Any] | None,
        *,
        extra_payloads: list[list[Any]] | None = None,
        url: str = "about:blank",
        landing_url: str | None = None,
        drop_first: bool = False,
        goto_error: Exception | None = None,
    ) -> None:
        self._payload = payload
        self._extra_payloads = list(extra_payloads or [])
        self._drop_first = drop_first
        self._goto_error = goto_error
        self._landing_url = landing_url
        self._listeners: list[Callable[[Any], Any]] = []
        self.url = url
        self.gotos: list[str] = []
        self.goto_kwargs: list[dict[str, Any]] = []
        self.reloads: int = 0

    def on(self, event: str, fn: Callable[[Any], Any]) -> None:
        assert event == "response"
        self._listeners.append(fn)

    def remove_listener(self, event: str, fn: Callable[[Any], Any]) -> None:
        self._listeners.remove(fn)

    async def _emit(self, payload: list[Any]) -> None:
        frame = json.dumps([["wrb.fr", "Zzl0ze", json.dumps(payload), None, None, None]])
        body = f")]}}'\n\n{len(frame)}\n{frame}\n"
        resp = _Resp("https://flow.google.com/_/x/data/batchexecute?rpcids=Zzl0ze", body)
        for fn in list(self._listeners):
            await fn(resp)

    async def _deliver(self) -> None:
        if self._drop_first:
            self._drop_first = False
            return
        for extra in self._extra_payloads:
            await self._emit(extra)
        if self._payload is None:
            return
        await self._emit(self._payload)

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.gotos.append(url)
        self.goto_kwargs.append(kwargs)
        self.url = self._landing_url or url
        if self._goto_error is not None:
            raise self._goto_error
        await self._deliver()

    async def reload(self, **_: Any) -> None:
        self.reloads += 1
        await self._deliver()


def _fixture_payload(*, project_id: str = PID) -> list[Any]:
    path = Path(__file__).parent / "fixtures" / "zzl0ze_reply.json"
    raw = path.read_text(encoding="utf-8")
    if project_id != PID:
        raw = raw.replace(PID, project_id)
    return json.loads(raw)


async def test_read_project_observes_zzl0ze_on_navigation() -> None:
    page = _Page(_fixture_payload())

    read = await read_project(page, PID, timeout_s=2)  # type: ignore[arg-type]

    assert page.gotos == [f"https://flow.google.com/project/{PID}"]
    assert [c.voice for c in read.characters] == ["Aoede"]
    assert len(read.voices) == 30


async def test_read_project_navigates_when_page_is_on_character_subroute() -> None:
    page = _Page(
        _fixture_payload(),
        url=f"https://flow.google.com/project/{PID}/character/00000000-0000-4000-8000-000000000004",
    )

    await read_project(page, PID, timeout_s=2)  # type: ignore[arg-type]

    assert page.gotos == [f"https://flow.google.com/project/{PID}"]
    assert page.reloads == 0


async def test_read_project_reloads_when_already_at_project_url() -> None:
    page = _Page(
        _fixture_payload(),
        url=f"https://flow.google.com/project/{PID}",
    )

    read = await read_project(page, PID, timeout_s=2)  # type: ignore[arg-type]

    assert page.gotos == []
    assert page.reloads == 1
    assert [c.entity_id for c in read.characters] == [NORA.entity_id]


@pytest.mark.parametrize(
    ("landing_url", "expected_exc"),
    [
        ("https://accounts.google.com/v3/signin/identifier", AuthExpiredError),
        ("https://flow.google.com/about", FlowAppError),
    ],
    ids=["signin", "about"],
)
async def test_read_project_raises_at_once_on_signin_or_about_landing(
    monkeypatch: pytest.MonkeyPatch, landing_url: str, expected_exc: type[Exception]
) -> None:
    from gflow_cli.api.transports import _mc_common

    waits: list[float] = []

    async def fake_wait_for(fut: Any, timeout: float) -> Any:
        waits.append(timeout)
        if not fut.done():
            fut.cancel()
            raise TimeoutError
        return await fut

    monkeypatch.setattr(_mc_common.asyncio, "wait_for", fake_wait_for)
    page = _Page(None, landing_url=landing_url)

    with pytest.raises(expected_exc) as exc:
        await read_project(page, PID, timeout_s=45.0)  # type: ignore[arg-type]

    assert not isinstance(exc.value, TransportTimeoutError)
    assert waits == [45.0]
    assert len(page.gotos) == 1
    assert page.reloads == 0


@pytest.mark.parametrize(
    "landing_url",
    [
        "https://flow.google.com/about",
        "https://accounts.google.com/v3/signin/identifier",
    ],
    ids=["about", "signin"],
)
async def test_read_project_does_not_abort_reply_when_landing_seen_before_reply(
    landing_url: str,
) -> None:
    page = _Page(_fixture_payload(), landing_url=landing_url)

    read = await read_project(page, PID, timeout_s=2)  # type: ignore[arg-type]

    assert [c.entity_id for c in read.characters] == [NORA.entity_id]
    assert len(page.gotos) == 1


async def test_read_project_ignores_other_project_reply_and_waits_for_requested_project() -> None:
    other_pid = "00000000-0000-4000-8000-000000000099"
    other_payload = _fixture_payload(project_id=other_pid)

    page = _Page(_fixture_payload(), extra_payloads=[other_payload])
    read = await read_project(page, PID, timeout_s=0.5)  # type: ignore[arg-type]
    assert [c.project_id for c in read.characters] == [PID]

    wrong_only = _Page(other_payload)
    with pytest.raises(TransportTimeoutError):
        await read_project(wrong_only, PID, timeout_s=0.1)  # type: ignore[arg-type]


async def test_read_project_retries_with_goto_target_and_45s_timeout_not_reload() -> None:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    target = f"https://flow.google.com/project/{PID}"
    page = _Page(_fixture_payload(), drop_first=True)

    read = await read_project(page, PID, timeout_s=0.1)  # type: ignore[arg-type]

    assert page.gotos == [target, target]
    assert page.reloads == 0
    assert [kw.get("timeout") for kw in page.goto_kwargs] == [45_000, 45_000]
    assert len(read.characters) == 1

    timeout_page = _Page(None, goto_error=PlaywrightTimeoutError("Timeout 45000ms exceeded"))
    with pytest.raises(TransportTimeoutError):
        await read_project(timeout_page, PID, timeout_s=0.1)  # type: ignore[arg-type]
    assert timeout_page.gotos == [target, target]


async def test_read_project_without_a_reply_times_out_rather_than_reading_empty() -> None:
    with pytest.raises(TransportTimeoutError):
        await read_project(_Page(None), PID, timeout_s=0.1)  # type: ignore[arg-type]
