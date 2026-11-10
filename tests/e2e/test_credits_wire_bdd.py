"""E2E for #1229: the credits balance read without a browser, and its fallback.

Binds ``tests/features/credits_wire_live.feature`` (``@e2e @e2e_auth``). Runs in-process so
the test can see what a subprocess cannot: whether ``FlowApiClient`` (which launches Chrome
on entry) was ever constructed, and how many HTTP requests the direct read sent.

**The cookie-free arm** forces the fallback without touching the real cookie jar: it
replaces ``flow_wire.read_cookies_for`` for this test only. No production hook exists.

**Cost: zero.** Shell GETs and ``nzlxg`` balance reads; the fallback and the last scenario
open flow.google.com in the profile's browser. Nothing is generated.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.api import flow_wire
from gflow_cli.cli import main
from gflow_cli.config import reset_settings
from gflow_cli.services import credits

scenarios("../features/credits_wire_live.feature")


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("GFLOW_CLI_WIRE_READS", "1")
    reset_settings()
    # A process-wide disable from an earlier scenario must not leak into this one.
    monkeypatch.setattr(flow_wire, "_tokens", {})
    monkeypatch.setattr(flow_wire, "_disabled", None)
    monkeypatch.setattr(flow_wire, "_lock", None)
    state: dict[str, Any] = {"pages": 0, "requests": [], "reasons": [], "mp": monkeypatch}

    real_client = credits.FlowApiClient

    def counting_client(*args: Any, **kwargs: Any) -> Any:
        state["pages"] += 1
        return real_client(*args, **kwargs)

    monkeypatch.setattr(credits, "FlowApiClient", counting_client)

    real_send = httpx.AsyncClient.send

    async def counting_send(self: httpx.AsyncClient, request: httpx.Request, **kw: Any) -> Any:
        state["requests"].append((request.method, request.url.host, request.url.path))
        return await real_send(self, request, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "send", counting_send)

    real_read = flow_wire.read_credits

    async def recording_read(profile_dir: Path) -> Any:
        try:
            return await real_read(profile_dir)
        except flow_wire.WireReadUnavailable as exc:
            state["reasons"].append(exc.reason.value)
            raise

    monkeypatch.setattr(flow_wire, "read_credits", recording_read)
    return state


@given("the e2e profile is signed in to Flow")
def _profile(e2e_profile_dir: Path, ctx: dict[str, Any]) -> None:
    ctx["dir"] = e2e_profile_dir
    ctx["profile"] = e2e_profile_dir.name.removeprefix("profile_")


@given("the wire read is forced to send no cookies")
def _no_cookies(ctx: dict[str, Any]) -> None:
    ctx["mp"].setattr(flow_wire, "read_cookies_for", lambda _p: [])


@given(parsers.parse("I have read the balance over the wire {n:d} times"))
def _wire_reads(ctx: dict[str, Any], n: int) -> None:
    for _ in range(n):
        result = asyncio.run(credits.inspect_profile(ctx["profile"]))
        assert result["status"] == "ok", result
    assert ctx["pages"] == 0, "a wire read fell back to the browser"
    assert ctx["reasons"] == []


@when('I run "gflow credits user --json"')
def _run(ctx: dict[str, Any]) -> None:
    ctx["result"] = CliRunner().invoke(
        main, ["credits", "user", "--profile", ctx["profile"], "--json"]
    )


@when('I run "gflow auth status"')
def _auth_status(ctx: dict[str, Any]) -> None:
    ctx["result"] = CliRunner().invoke(main, ["auth", "status", "--profile", ctx["profile"]])


@then("the balance is a non-negative integer")
def _balance(ctx: dict[str, Any]) -> None:
    result = ctx["result"]
    assert result.exit_code == 0, result.output
    out = result.output
    lines = out.splitlines(keepends=True)
    first = next(i for i, ln in enumerate(lines) if ln.strip() == "{")
    payload, _ = json.JSONDecoder().raw_decode(out[sum(len(ln) for ln in lines[:first]) :])
    assert payload["status"] == "ok", payload
    credits_value = payload["credits"]
    assert isinstance(credits_value, int) and not isinstance(credits_value, bool)
    assert credits_value >= 0


@then("no Chrome process was started for the profile")
def _no_chrome(ctx: dict[str, Any]) -> None:
    assert ctx["pages"] == 0, "FlowApiClient was constructed: the read used the browser"
    assert ctx["reasons"] == []


@then("at most one page GET and one batchexecute POST were sent")
def _budget(ctx: dict[str, Any]) -> None:
    sent = ctx["requests"]
    print(f"G5 requests: {sent}")  # noqa: T201 - the evidence pasted into the PR
    assert [m for m, _h, _p in sent] == ["GET", "POST"], sent
    assert all(h == "flow.google.com" for _m, h, _p in sent)


@then(parsers.parse('the wire fell back with reason "{reason}"'))
def _fell_back(ctx: dict[str, Any], reason: str) -> None:
    assert ctx["reasons"] == [reason]
    assert ctx["pages"] == 1


@then("the profile's Flow session is reported valid")
def _valid(ctx: dict[str, Any]) -> None:
    result = ctx["result"]
    assert result.exit_code == 0, result.output
    assert "Flow session verified" in result.output


@then("a browser read of the balance still succeeds")
def _page_read(ctx: dict[str, Any]) -> None:
    """`auth status` alone proves little on this host (its cookie fallback); the browser
    read is the evidence that the session survived (spike 2026-11-10, Q4)."""
    ctx["mp"].setenv("GFLOW_CLI_WIRE_READS", "0")
    reset_settings()
    result = asyncio.run(credits.inspect_profile(ctx["profile"]))
    assert result["status"] == "ok" and result["authenticated"] is True, result
    assert ctx["pages"] == 1
