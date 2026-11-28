"""The phase-tracked batchexecute attempt: the outcome is decided by phase, never by type.

Before ``client.send`` is entered for the billed write, a failure is ``NotSent``; from the
moment it is entered, every failure is ``Indeterminate`` and nothing is resent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from gflow_cli.api import flow_wire
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.submit_policy import (
    DirectAttemptEnded,
    Indeterminate,
    NotSent,
    RefusedNotBilled,
)
from gflow_cli.api.transports.batchexecute import UNUSUAL_ACTIVITY_REASON
from gflow_cli.api.transports.direct_http import DirectHttpTransport, _session_headers, _shell
from gflow_cli.api.transports.direct_http._gateway import (
    WRITE_RPCIDS,
    SubmitAttempt,
    _client,
    _post_request,
)
from gflow_cli.errors import ContentPolicyError, NetworkError, WafRejectionError, WireFormatError
from tests.api.transports.test_migrated_images import image_payload

_SHELL_HTML = '<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
_TOKENS = flow_wire.Tokens("at-x", "sid", "boq_x")
_SPEC = ("ogiZ0b", "IMAGE_GENERATION", "image submit", "check the project")


class FakeTokenSource:
    """A ``RecaptchaTokenSource`` that records each action it minted."""

    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []

    async def mint(self, action: str) -> str:
        self.events.append(f"mint:{action}")
        return "fake-token"


@pytest.fixture(autouse=True)
def _clean_flow_wire(monkeypatch: pytest.MonkeyPatch) -> Any:
    flow_wire.reset_state()
    monkeypatch.setattr(flow_wire, "getproxies", dict)
    yield
    flow_wire.reset_state()


def _ogiz0b_reply() -> str:
    frame = [["wrb.fr", "ogiZ0b", json.dumps(image_payload()), None, None, None, "generic"]]
    return f")]}}'\n{json.dumps(frame)}"


def _page(*cookie_reads: list[dict[str, str]]) -> MagicMock:
    page = MagicMock()
    page.url = "https://flow.google.com/project/proj-123"
    page.goto = AsyncMock()
    page.context = MagicMock()
    reads = cookie_reads or ([{"name": "SID", "value": "sid-1", "domain": ".google.com"}],)
    page.context.cookies = AsyncMock(side_effect=[*reads, *([reads[-1]] * 8)])
    return page


def _request(rpcid: str = "ogiZ0b") -> httpx.Request:
    return _post_request(rpcid, "/project/proj-123", _TOKENS, {"cookie": "SID=x"}, "f.req=x")


def _transport(tmp_path: Path, handler: Any, page: MagicMock | None = None) -> DirectHttpTransport:
    t = DirectHttpTransport(http_transport=httpx.MockTransport(handler))
    t._setup_done = True
    t._profile_dir = tmp_path
    t._page = page or _page()
    t.park_deferred_page = AsyncMock()  # type: ignore[method-assign]
    return t


def _watch_ui(transport: DirectHttpTransport) -> list[bool]:
    calls: list[bool] = []

    async def ui(*_: Any, **__: Any) -> list[GeneratedImage]:
        calls.append(True)
        return []

    transport._generate_images_locked = ui  # type: ignore[method-assign]
    return calls


@pytest.fixture
def stub_mint_and_body(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []

    async def mint(_self: Any, action: str) -> str:
        events.append("mint")
        return "mock_token"

    monkeypatch.setattr("gflow_cli.api.recaptcha.TokenMinter.mint", mint)
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.build_ogiz0b_body",
        MagicMock(return_value="f.req=mock_body&at=at-x&"),
    )
    return events


@pytest.mark.asyncio
async def test_connect_error_before_send_is_not_sent_and_reconnects_once(tmp_path: Path) -> None:
    """Row 5: the connection never opened, so no byte reached Flow; one reconnect only."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("Connection refused")  # before the body is read

    attempt = SubmitAttempt(_SPEC, tmp_path, FakeTokenSource())
    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(DirectAttemptEnded) as ended:
            async with attempt:
                await attempt.send(client, _request())
    assert isinstance(ended.value.outcome, NotSent)
    assert ended.value.outcome.reason == "connect:ConnectError"
    assert attempt.sent is False  # zero bytes sent: the phase never reached the send
    assert attempts == 2  # the first try and exactly one reconnect


@pytest.mark.asyncio
async def test_a_reconnect_that_opens_sends_once(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectTimeout("connect timed out")
        return httpx.Response(200, text=_ogiz0b_reply())

    attempt = SubmitAttempt(_SPEC, tmp_path, FakeTokenSource())
    async with _client(httpx.MockTransport(handler)) as client, attempt:
        response = await attempt.send(client, _request())
    assert response.status_code == 200
    assert attempt.sent is True
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
async def test_post_send_403_is_indeterminate_never_ui_fallback(
    tmp_path: Path, stub_mint_and_body: list[str], status: int
) -> None:
    """Row 4: a non-200 after the send is not a refusal; the old UI fallback is gone."""
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.method == "GET":
            return httpx.Response(200, text=_SHELL_HTML)
        posts += 1
        return httpx.Response(status, text="Forbidden")

    transport = _transport(tmp_path, handler)
    ui = _watch_ui(transport)
    with pytest.raises(DirectAttemptEnded) as ended:
        await transport.generate_images(
            project_id="proj-123", request=GenerateImageRequest(prompt="x")
        )
    assert ended.value.outcome == Indeterminate("ogiZ0b", f"HTTP {status}", status)
    assert isinstance(ended.value.error, NetworkError)
    assert ended.value.error.next_action == "reconcile"
    assert ended.value.error.status == status
    assert "not resent" in ended.value.error.detail
    assert ui == []
    assert posts == 1
    assert tmp_path not in flow_wire._tokens  # the cached `at` is dropped


def _error_reply(reason: str) -> str:
    frame = [["wrb.fr", "ogiZ0b", None, None, None, [3, None, [[reason]]], "generic"]]
    return f")]}}'\n{json.dumps(frame)}"


@pytest.mark.asyncio
async def test_an_unusual_activity_reply_is_refused_not_billed_never_ui(
    tmp_path: Path, stub_mint_and_body: list[str]
) -> None:
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.method == "GET":
            return httpx.Response(200, text=_SHELL_HTML)
        posts += 1
        return httpx.Response(200, text=_error_reply(UNUSUAL_ACTIVITY_REASON))

    transport = _transport(tmp_path, handler)
    ui = _watch_ui(transport)
    with pytest.raises(DirectAttemptEnded) as ended:
        await transport.generate_images(
            project_id="proj-123", request=GenerateImageRequest(prompt="x")
        )
    assert ended.value.outcome == RefusedNotBilled("ogiZ0b", UNUSUAL_ACTIVITY_REASON)
    assert isinstance(ended.value.error, WafRejectionError)
    assert ui == []
    assert posts == 1
    assert tmp_path not in flow_wire._tokens


@pytest.mark.asyncio
async def test_any_other_error_reason_stays_indeterminate(tmp_path: Path) -> None:
    """An unmeasured refusal (a credits refusal among them) is never read as not billed."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_error_reply("PUBLIC_ERROR_SOMETHING_NEW"))

    attempt = SubmitAttempt(_SPEC, tmp_path, FakeTokenSource())
    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(DirectAttemptEnded) as ended:
            async with attempt:
                await attempt.send(client, _request())
    assert isinstance(ended.value.outcome, Indeterminate)


@pytest.mark.asyncio
async def test_a_named_content_refusal_passes_through_unchanged(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_error_reply("PUBLIC_ERROR_UNSAFE_CONTENT"))

    attempt = SubmitAttempt(_SPEC, tmp_path, FakeTokenSource())
    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(ContentPolicyError):
            async with attempt:
                await attempt.send(client, _request())


@pytest.mark.asyncio
async def test_read_timeout_after_send_is_not_resent(tmp_path: Path) -> None:
    """Row 3: the body left; a timeout waiting for the reply is indeterminate."""
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        posts += 1
        raise httpx.ReadTimeout("Request timed out")

    attempt = SubmitAttempt(_SPEC, tmp_path, FakeTokenSource())
    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(DirectAttemptEnded) as ended:
            async with attempt:
                await attempt.send(client, _request())
    outcome = ended.value.outcome
    assert isinstance(outcome, Indeterminate)
    assert outcome.rpcid == "ogiZ0b"
    assert "ReadTimeout" in outcome.cause
    assert posts == 1
    assert isinstance(ended.value.error, NetworkError)
    assert ended.value.error.next_action == "reconcile"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["parse", "cookie"])
async def test_any_failure_after_send_is_indeterminate(tmp_path: Path, failure: str) -> None:
    """Row 3: whatever raises after the POST, a parse error or a later cookie read."""
    page = _page()
    page.context.cookies = AsyncMock(side_effect=RuntimeError("context closed"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_ogiz0b_reply())

    attempt = SubmitAttempt(_SPEC, tmp_path, FakeTokenSource())
    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(DirectAttemptEnded) as ended:
            async with attempt:
                await attempt.send(client, _request())
                if failure == "parse":
                    raise WireFormatError(detail="no frame", route="batchexecute:ogiZ0b")
                await _session_headers(page, tmp_path)
    assert isinstance(ended.value.outcome, Indeterminate)
    assert ended.value.outcome.rpcid == "ogiZ0b"


@pytest.mark.asyncio
async def test_a_failure_before_send_is_not_sent(tmp_path: Path) -> None:
    page = _page()
    page.context.cookies = AsyncMock(side_effect=RuntimeError("context closed"))
    attempt = SubmitAttempt(_SPEC, tmp_path, FakeTokenSource())
    with pytest.raises(DirectAttemptEnded) as ended:
        async with attempt:
            await _session_headers(page, tmp_path)
    assert isinstance(ended.value.outcome, NotSent)
    assert ended.value.outcome.reason == "cookie_error:RuntimeError"


@pytest.mark.asyncio
async def test_the_attempt_mints_its_action_from_the_injected_token_source(tmp_path: Path) -> None:
    source = FakeTokenSource()
    attempt = SubmitAttempt(_SPEC, tmp_path, source)
    async with attempt:
        assert await attempt.mint() == "fake-token"
    assert source.events == ["mint:IMAGE_GENERATION"]


@pytest.mark.asyncio
async def test_a_failing_token_source_is_not_sent(tmp_path: Path) -> None:
    source = MagicMock()
    source.mint = AsyncMock(side_effect=TimeoutError())
    attempt = SubmitAttempt(_SPEC, tmp_path, source)
    with pytest.raises(DirectAttemptEnded) as ended:
        async with attempt:
            await attempt.mint()
    assert ended.value.outcome == NotSent("mint_failure:TimeoutError")


@pytest.mark.asyncio
async def test_shell_401_refreshes_at_before_mint(
    tmp_path: Path, stub_mint_and_body: list[str]
) -> None:
    """Row 18: a 401 on the shell GET drops `at` and fetches it once more, before the mint."""
    events = stub_mint_and_body

    def handler(request: httpx.Request) -> httpx.Response:
        events.append(request.method)
        if request.method == "GET":
            gets = events.count("GET")
            return httpx.Response(401) if gets == 1 else httpx.Response(200, text=_SHELL_HTML)
        return httpx.Response(200, text=_ogiz0b_reply())

    transport = _transport(tmp_path, handler)
    ui = _watch_ui(transport)
    images = await transport.generate_images(
        project_id="proj-123", request=GenerateImageRequest(prompt="x")
    )
    assert events == ["GET", "GET", "mint", "POST"]
    assert ui == []
    assert images[0].transport == "direct_http"


@pytest.mark.asyncio
async def test_a_second_shell_401_is_not_sent_and_names_the_session(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(DirectAttemptEnded) as ended:
            await _shell(client, _page(), tmp_path)
    assert ended.value.outcome == NotSent("http_401", auth=True)


@pytest.mark.asyncio
async def test_cookies_read_live_before_each_request(
    tmp_path: Path, stub_mint_and_body: list[str]
) -> None:
    """Row 18: cookies rotate in a long process; each request reads the live context."""
    page = _page(
        [{"name": "SID", "value": "sid-1", "domain": ".google.com"}],
        [{"name": "SID", "value": "sid-2", "domain": ".google.com"}],
    )
    sent: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.method, request.headers["cookie"]))
        if request.method == "GET":
            return httpx.Response(200, text=_SHELL_HTML)
        return httpx.Response(200, text=_ogiz0b_reply())

    transport = _transport(tmp_path, handler, page)
    _watch_ui(transport)
    await transport.generate_images(project_id="proj-123", request=GenerateImageRequest(prompt="x"))
    assert sent == [("GET", "SID=sid-1"), ("POST", "SID=sid-2")]


@pytest.mark.asyncio
async def test_httpx_exception_with_fsid_url_is_scrubbed_in_detail(
    tmp_path: Path, stub_mint_and_body: list[str]
) -> None:
    """Row 29: an httpx message can carry the request URL with its session fields."""
    secret = "SECRETSESSIONID1234567890"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_SHELL_HTML)
        raise httpx.ReadError(f"peer reset on POST /batchexecute?f.sid={secret}&at={secret}")

    transport = _transport(tmp_path, handler)
    _watch_ui(transport)
    with pytest.raises(DirectAttemptEnded) as ended:
        await transport.generate_images(
            project_id="proj-123", request=GenerateImageRequest(prompt="x")
        )
    assert ended.value.outcome == Indeterminate("ogiZ0b", ended.value.outcome.cause, None)
    assert ended.value.outcome.cause.startswith("ReadError (")  # type: ignore[union-attr]
    assert isinstance(ended.value.error, NetworkError)
    assert secret not in ended.value.error.detail
    assert "ReadError" in ended.value.error.detail
    assert ended.value.error.next_action == "reconcile"


@pytest.mark.asyncio
@pytest.mark.parametrize("rpcid", ["XyzAbc", "jwpduf"])
async def test_write_rpcid_outside_allowlist_refused_before_send(
    tmp_path: Path, rpcid: str
) -> None:
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        posts += 1
        return httpx.Response(200)

    attempt = SubmitAttempt(_SPEC, tmp_path, FakeTokenSource())
    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match=rpcid):
            async with attempt:
                await attempt.send(client, _request(rpcid))
    assert posts == 0
    assert attempt.sent is False


def test_the_write_allowlist_is_measured() -> None:
    assert frozenset({"ogiZ0b", "maseQ", "YhhmEf", "eb1hJf", "p0UkFb"}) == WRITE_RPCIDS


def test_post_request_carries_the_source_path() -> None:
    request = _post_request("p0UkFb", "/project/p/edit/m", _TOKENS, {}, "f.req=x")
    assert request.url.params["source-path"] == "/project/p/edit/m"
    assert request.headers["referer"] == "https://flow.google.com/project/p/edit/m"


@pytest.mark.asyncio
async def test_a_shell_redirect_is_not_sent_without_a_refetch(tmp_path: Path) -> None:
    gets = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets
        gets += 1
        return httpx.Response(302, headers={"location": "https://accounts.google.com/"})

    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(DirectAttemptEnded) as ended:
            await _shell(client, _page(), tmp_path)
    assert ended.value.outcome == NotSent("redirect", auth=True)
    assert gets == 1


@pytest.mark.asyncio
async def test_signed_out_shell_is_not_sent_auth(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>no tokens</html>")

    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(DirectAttemptEnded) as ended:
            await _shell(client, _page(), tmp_path)
    assert ended.value.outcome == NotSent("signed_out_shell", auth=True)


@pytest.mark.asyncio
async def test_file_read_signed_out_mark_does_not_block_live_direct_session(
    tmp_path: Path,
) -> None:
    """A browser-free read of the on-disk cookies (credits, the pre-launch balance check) can
    mark the profile signed out and cache an ``at`` for those cookies. The direct path sends
    the live browser's cookies, so it asks the shell again rather than inherit either."""
    flow_wire._signed_out[tmp_path] = flow_wire.WireFallbackReason.SIGNED_OUT_SHELL
    flow_wire._tokens[tmp_path] = flow_wire.Tokens("at-from-disk", "sid", "boq_x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_SHELL_HTML)

    async with _client(httpx.MockTransport(handler)) as client:
        tokens = await _shell(client, _page(), tmp_path)
    assert tokens == _TOKENS
    assert tmp_path not in flow_wire._signed_out


@pytest.mark.asyncio
async def test_401_then_signed_out_shell_is_auth(tmp_path: Path) -> None:
    gets = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets
        gets += 1
        return httpx.Response(401) if gets == 1 else httpx.Response(200, text="<html></html>")

    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(DirectAttemptEnded) as ended:
            await _shell(client, _page(), tmp_path)
    assert ended.value.outcome == NotSent("signed_out_shell", auth=True)
    assert gets == 2


@pytest.mark.asyncio
async def test_typed_error_after_send_keeps_type_and_is_not_retryable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_ogiz0b_reply())

    typed = WafRejectionError(detail="blocked", route="batchexecute:ogiZ0b", retryable=True)
    attempt = SubmitAttempt(_SPEC, tmp_path, FakeTokenSource())
    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(DirectAttemptEnded) as ended:
            async with attempt:
                await attempt.send(client, _request())
                raise typed
    assert isinstance(ended.value.outcome, Indeterminate)
    assert ended.value.error is typed
    assert typed.retryable is False
    assert typed.resolved_next_action() == "reconcile"


@pytest.mark.asyncio
async def test_untyped_error_after_send_is_reconcile_network_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_ogiz0b_reply())

    attempt = SubmitAttempt(_SPEC, tmp_path, FakeTokenSource())
    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(DirectAttemptEnded) as ended:
            async with attempt:
                await attempt.send(client, _request())
                raise KeyError("media")
    assert isinstance(ended.value.outcome, Indeterminate)
    assert type(ended.value.error) is NetworkError
    assert ended.value.error.next_action == "reconcile"


@pytest.mark.asyncio
async def test_an_accepted_upload_frame_without_a_media_id_is_reconciled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="t"))
    frame = [["wrb.fr", "maseQ", json.dumps([["no id here"]]), None, None, None, "generic"]]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_SHELL_HTML)
        return httpx.Response(200, text=")]}'\n" + json.dumps(frame))

    image = tmp_path / "face.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    transport = _transport(tmp_path, handler)
    with pytest.raises(DirectAttemptEnded) as ended:
        await transport.upload_reference(project_id="proj-123", path=image)
    assert ended.value.outcome == Indeterminate("maseQ", ended.value.outcome.cause, None)
    assert ended.value.outcome.cause.startswith("WireFormatError (")  # type: ignore[union-attr]
    assert isinstance(ended.value.error, WireFormatError)
    assert ended.value.error.retryable is False
    assert ended.value.error.resolved_next_action() == "reconcile"


@pytest.mark.asyncio
async def test_a_token_source_that_ends_the_attempt_keeps_its_outcome(tmp_path: Path) -> None:
    """A source that already decided the outcome (a signed-out shell) is not re-labelled
    as a mint failure."""
    signed_out = DirectAttemptEnded(NotSent("signed_out_shell", auth=True))
    source = MagicMock()
    source.mint = AsyncMock(side_effect=signed_out)
    attempt = SubmitAttempt(_SPEC, tmp_path, source)
    with pytest.raises(DirectAttemptEnded) as ended:
        async with attempt:
            await attempt.mint()
    assert ended.value is signed_out


@pytest.mark.asyncio
async def test_a_typed_error_after_send_is_logged_as_indeterminate(tmp_path: Path) -> None:
    """The past-send failure leaves a ``direct.indeterminate`` line naming the rpc."""
    from structlog.testing import capture_logs

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_ogiz0b_reply())

    attempt = SubmitAttempt(_SPEC, tmp_path, FakeTokenSource())
    with capture_logs() as logs:
        async with _client(httpx.MockTransport(handler)) as client:
            with pytest.raises(DirectAttemptEnded):
                async with attempt:
                    await attempt.send(client, _request())
                    raise WafRejectionError(detail="blocked", route="batchexecute:ogiZ0b")
    assert [e["rpcid"] for e in logs if e["event"] == "direct.indeterminate"] == ["ogiZ0b"]
