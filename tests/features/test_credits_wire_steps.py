"""Offline bindings for `credits_wire.feature`: the REAL wire against a scripted Flow.

Each reason is produced the way Flow (or the jar) produces it, through the real
`flow_wire.read_credits`; only the HTTP transport, the jar and the browser read are fakes.
"""

from __future__ import annotations

import asyncio
import functools
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.api import flow_wire
from gflow_cli.api.dto import CreditsInfo
from gflow_cli.auth.cookies import CookieStoreUnreadable
from gflow_cli.config import get_settings, reset_settings
from gflow_cli.profile_store import ProfileMeta
from gflow_cli.services import credits

scenarios("credits_wire.feature")

_SHELL = '<script>{"SNlM0e":"AFAKEAT","FdrFJe":"-1","cfb2h":"boq_x"}</script>'
_REPLY = ')]}\'\n[["wrb.fr","nzlxg","[42]",null,null,null,"generic"]]\n'


def _respond(reason: str, request: httpx.Request) -> httpx.Response:
    get = request.method == "GET"
    if reason == "signed_out_shell" and get:
        return httpx.Response(200, text="<script>{}</script>")
    if reason == "redirect" and get:
        return httpx.Response(302, headers={"location": "https://accounts.google.com/"})
    if reason == "network":
        raise httpx.ConnectError("refused")
    if get:
        return httpx.Response(200, text=_SHELL)
    status = {"http_401": 401, "http_400": 400}.get(reason, 200)
    return httpx.Response(status, text="<html></html>" if reason == "unparseable" else _REPLY)


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch, fresh_wire: None) -> dict[str, Any]:
    monkeypatch.setenv("GFLOW_CLI_WIRE_READS", "1")
    reset_settings()
    monkeypatch.setattr(flow_wire, "profile_last_version", lambda _p: "150.0.1.2")
    monkeypatch.setattr(flow_wire, "read_cookies_for", lambda _p: [("SID", "fake")])
    state: dict[str, Any] = {"page_reads": 0}

    class Page:
        def __init__(self, profile_dir: Path, *, headless: bool = False) -> None:
            pass

        async def __aenter__(self) -> Page:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def get_credits(self) -> CreditsInfo:
            state["page_reads"] += 1
            return CreditsInfo(credits=11)

    monkeypatch.setattr(credits, "FlowApiClient", Page)
    state["monkeypatch"] = monkeypatch
    return state


def _route(ctx: dict[str, Any], reason: str) -> None:
    real = httpx.AsyncClient
    transport = httpx.MockTransport(functools.partial(_respond, reason))
    ctx["monkeypatch"].setattr(httpx, "AsyncClient", functools.partial(real, transport=transport))


@given(parsers.parse('the direct read fails with "{reason}"'))
def _fails(ctx: dict[str, Any], reason: str) -> None:
    _route(ctx, reason)
    if reason == "store_unreadable":

        def locked(_p: Path) -> list[tuple[str, str]]:
            raise CookieStoreUnreadable("PermissionError")

        ctx["monkeypatch"].setattr(flow_wire, "read_cookies_for", locked)


@given(parsers.parse("the direct read succeeds with {n:d} credits"))
def _succeeds(ctx: dict[str, Any], n: int) -> None:
    assert n == 42  # the scripted reply's balance
    _route(ctx, "ok")


@when("I read the credits of the profile")
def _read(ctx: dict[str, Any]) -> None:
    meta = ProfileMeta(
        name="one",
        profile_dir=get_settings().home / "profiles" / "profile_one",
        cookies_present=True,
        last_used_at=None,
        is_default=True,
    )
    with structlog.testing.capture_logs() as logs:
        ctx["result"] = asyncio.run(credits._fetch_once(meta))  # pyright: ignore[reportPrivateUsage]
    ctx["logs"] = logs


@then("the balance comes from the browser read")
def _from_page(ctx: dict[str, Any]) -> None:
    assert ctx["result"]["credits"] == 11
    assert ctx["page_reads"] == 1


@then(parsers.parse('the log records a wire fallback with reason "{reason}"'))
def _logged(ctx: dict[str, Any], reason: str) -> None:
    events = [e for e in ctx["logs"] if e["event"] == "credits.wire_fallback"]
    assert [e["reason"] for e in events] == [reason]


@then(parsers.parse("the balance is {n:d}"))
def _balance(ctx: dict[str, Any], n: int) -> None:
    assert ctx["result"]["credits"] == n


@then("the browser read never ran")
def _no_page(ctx: dict[str, Any]) -> None:
    assert ctx["page_reads"] == 0
