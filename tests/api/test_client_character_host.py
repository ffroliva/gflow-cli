"""FlowApiClient character reads: which host does the work (#639, character-portability Task 4).

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

from gflow_cli.api.character import Character
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.migrated_character import ProjectRead, read_project
from gflow_cli.config import Settings
from gflow_cli.errors import (
    AisandboxAuthError,
    AuthExpiredError,
    ConfigurationError,
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
READ = ProjectRead(characters=(NORA,), voices=(), images={})
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


# --- read_project against a fake page ---------------------------------------------


class _Resp:
    def __init__(self, url: str, text: str) -> None:
        self.url, self._text = url, text

    async def text(self) -> str:
        return self._text


class _Page:
    """Delivers a Zzl0ze frame on navigation — to listeners attached at that moment."""

    def __init__(self, payload: list[Any] | None) -> None:
        self._payload = payload
        self._listeners: list[Callable[[Any], Any]] = []
        self.url = "about:blank"
        self.gotos: list[str] = []

    def on(self, event: str, fn: Callable[[Any], Any]) -> None:
        assert event == "response"
        self._listeners.append(fn)

    def remove_listener(self, event: str, fn: Callable[[Any], Any]) -> None:
        self._listeners.remove(fn)

    async def goto(self, url: str, **_: Any) -> None:
        self.gotos.append(url)
        self.url = url
        if self._payload is None:
            return
        frame = json.dumps([["wrb.fr", "Zzl0ze", json.dumps(self._payload), None, None, None]])
        body = f")]}}'\n\n{len(frame)}\n{frame}\n"
        resp = _Resp("https://flow.google.com/_/x/data/batchexecute?rpcids=Zzl0ze", body)
        for fn in list(self._listeners):
            await fn(resp)


def _fixture_payload() -> list[Any]:
    path = Path(__file__).parent / "fixtures" / "zzl0ze_reply.json"
    return json.loads(path.read_text(encoding="utf-8"))


async def test_read_project_observes_zzl0ze_on_navigation() -> None:
    page = _Page(_fixture_payload())

    read = await read_project(page, PID, timeout_s=2)  # type: ignore[arg-type]

    assert page.gotos == [f"https://flow.google.com/project/{PID}"]
    assert [c.voice for c in read.characters] == ["Aoede"]
    assert len(read.voices) == 30


async def test_read_project_without_a_reply_times_out_rather_than_reading_empty() -> None:
    from gflow_cli.errors import TransportTimeoutError

    with pytest.raises(TransportTimeoutError):
        await read_project(_Page(None), PID, timeout_s=0.2)  # type: ignore[arg-type]


# --- patch_entity: REST first, the editor on the measured refusal ------------------

REST_401 = AisandboxAuthError(detail="aisandbox-pa returned 401 after token refresh", status=401)


def _patch_client(tmp_path: Path, flow_host: str, rest: Any) -> FlowApiClient:
    c = FlowApiClient(profile_dir=tmp_path / "prof", settings=Settings(flow_host=flow_host))
    c._page = MagicMock()  # noqa: SLF001
    c._patch_json = (  # type: ignore[method-assign]
        AsyncMock(side_effect=rest) if isinstance(rest, Exception) else AsyncMock(return_value=rest)
    )
    return c


@pytest.fixture
def editor() -> Any:
    with patch(
        "gflow_cli.api.transports.migrated_character.patch_character", new=AsyncMock()
    ) as edit:
        yield edit


_PATCH = {
    "project_id": PID,
    "entity_id": NORA.entity_id,
    "display_name": "Nora",
    "workflow_ids": ["wf-portrait"],
    "voice": "Aoede",
    "personality": "calm",
}


async def test_patch_falls_back_to_the_editor_on_the_rest_401(tmp_path: Path, editor: Any) -> None:
    c = _patch_client(tmp_path, "auto", REST_401)

    await c.patch_entity(**_PATCH)

    editor.assert_awaited_once()
    kwargs = editor.await_args.kwargs
    assert (kwargs["name"], kwargs["voice"], kwargs["personality"]) == ("Nora", "Aoede", "calm")


async def test_pinned_flow_google_com_patches_in_the_editor(tmp_path: Path, editor: Any) -> None:
    c = _patch_client(tmp_path, "flow.google.com", {})

    await c.patch_entity(**_PATCH)

    c._patch_json.assert_not_awaited()  # type: ignore[attr-defined]
    editor.assert_awaited_once()


async def test_pinned_labs_keeps_the_rest_error(tmp_path: Path, editor: Any) -> None:
    c = _patch_client(tmp_path, "labs.google", REST_401)

    with pytest.raises(AisandboxAuthError):
        await c.patch_entity(**_PATCH)
    editor.assert_not_awaited()


async def test_a_waf_refusal_is_not_a_reason_to_switch(tmp_path: Path, editor: Any) -> None:
    c = _patch_client(tmp_path, "auto", WafRejectionError(detail="waf", status=403))

    with pytest.raises(WafRejectionError):
        await c.patch_entity(**_PATCH)
    editor.assert_not_awaited()
