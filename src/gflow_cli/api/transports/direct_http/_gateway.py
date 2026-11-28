# pyright: reportUnusedFunction=false, reportPrivateUsage=false
"""The batchexecute write path: a phase-tracked attempt that ends in a ``SubmitOutcome``.

The outcome is decided by phase, never by the type of the failure. Until ``client.send`` is
entered for the billed write, nothing reached Flow and a failure is ``NotSent``. From that
moment on, every failure (a reply, a parse, a later cookie read, a poll, a download) is
``Indeterminate``: Flow may have accepted and billed the request, so it is never resent.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from collections import deque
from collections.abc import AsyncGenerator, Callable
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import TYPE_CHECKING

import httpx
import structlog

from gflow_cli.api import flow_wire
from gflow_cli.api.submit_policy import (
    MAX_IN_FLIGHT,
    START_RATE_WINDOW_S,
    Accepted,
    DirectAttemptEnded,
    Indeterminate,
    NotSent,
    RefusedNotBilled,
    classify_reply,
    not_sent,
    starts_exceed,
)
from gflow_cli.api.transports._mc_common import _submit_refusal
from gflow_cli.errors import ContentPolicyError, GFlowError, NetworkError
from gflow_cli.redaction import redact_sensitive_text

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from gflow_cli.api.submit_policy import RecaptchaTokenSource

log = structlog.get_logger(__name__)

_ORIGIN = "https://flow.google.com"
_RpcSpec = tuple[str, str, str, str]  # rpcid, reCAPTCHA action, noun, reconcile hint

#: The billed or stored writes this transport may send, each measured on flow.google.com.
WRITE_RPCIDS = frozenset({"ogiZ0b", "maseQ", "YhhmEf", "eb1hJf", "p0UkFb"})
# Raised while the connection opens, before any byte of the request is written.
_CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def _first_line(exc: BaseException) -> str:
    # Redacted before the cut: trimming first can split a token below every pattern's floor.
    return redact_sensitive_text((str(exc).strip().splitlines() or [""])[0])[:160]


class SubmitAttempt:
    """One billed write and everything that follows it, used as ``async with attempt:``.

    ``sent`` flips to ``True`` the moment ``client.send`` is entered for the write. On exit,
    any failure after that point becomes ``DirectAttemptEnded(Indeterminate)``; a failure
    before it propagates as raised (the session, shell and mint steps raise ``not_sent``).
    """

    def __init__(
        self,
        spec: _RpcSpec,
        profile_dir: Path,
        token_source: RecaptchaTokenSource,
        *,
        on_before_submit: Callable[[], None] | None = None,
    ) -> None:
        self.spec = spec
        self.sent = False
        self._profile_dir = profile_dir
        self._token_source = token_source
        self._on_before_submit = on_before_submit

    async def __aenter__(self) -> SubmitAttempt:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self.sent or not isinstance(exc, Exception):
            return  # pre-send failures, cancellation and success pass through unchanged
        if isinstance(exc, ContentPolicyError):
            return  # Flow named a content refusal; it is final and is not resent either
        if isinstance(exc, DirectAttemptEnded) and not isinstance(exc.outcome, NotSent):
            return
        outcome = Indeterminate(
            rpcid=self.spec[0], cause=f"{type(exc).__name__} ({_first_line(exc)})"
        )
        if isinstance(exc, GFlowError):
            # A typed failure keeps its type; past the send it is never a retry.
            exc.retryable = False
            log.warning("direct.indeterminate", rpcid=outcome.rpcid, cause=outcome.cause)
            raise DirectAttemptEnded(outcome, error=exc) from exc
        raise self._ended(outcome) from exc

    async def mint(self) -> str:
        """The reCAPTCHA token for this write's action, from the injected source."""
        try:
            return await self._token_source.mint(self.spec[1])
        except DirectAttemptEnded:
            raise
        except Exception as exc:
            raise not_sent(f"mint_failure:{type(exc).__name__}") from exc

    async def send(self, client: httpx.AsyncClient, request: httpx.Request) -> httpx.Response:
        """Send the write once and classify the reply; return the response only if accepted.

        A connection that never opened sent nothing, so it is reopened once; a second
        failure to open is ``NotSent``. Every rejected reply drops the cached ``at`` so a
        stale one cannot stick in a long-lived process.
        """
        rpcid = request.url.params.get("rpcids", "")
        if rpcid not in WRITE_RPCIDS:
            msg = f"refusing to send {rpcid!r}: not a measured batchexecute write"
            raise ValueError(msg)
        if self._on_before_submit is not None:
            # Immediately before the billed POST; nothing is sent yet, so whatever it
            # raises (a cancel from the submit gate) passes ``__aexit__`` unchanged.
            self._on_before_submit()
        try:
            response = await self._open(client, request)
        except _CONNECT_ERRORS:
            try:
                response = await self._open(client, request)
            except _CONNECT_ERRORS as exc:
                raise not_sent(f"connect:{type(exc).__name__}") from exc
        outcome = classify_reply(rpcid, response.status_code, response.text)
        if isinstance(outcome, Accepted):
            return response
        flow_wire.forget_tokens(self._profile_dir)
        if isinstance(outcome, RefusedNotBilled):
            raise DirectAttemptEnded(outcome, error=_submit_refusal(response.text, (rpcid,)))
        if isinstance(outcome, Indeterminate):
            raise self._ended(outcome)
        raise DirectAttemptEnded(outcome)  # pragma: no cover - classify_reply never answers NotSent

    async def _open(self, client: httpx.AsyncClient, request: httpx.Request) -> httpx.Response:
        self.sent = True
        try:
            return await client.send(request)
        except _CONNECT_ERRORS:
            self.sent = False  # the connection never opened: no byte reached Flow
            raise

    def _ended(self, outcome: Indeterminate) -> DirectAttemptEnded:
        rpcid, _, noun, hint = self.spec
        log.warning("direct.indeterminate", rpcid=rpcid, cause=outcome.cause)
        return DirectAttemptEnded(
            outcome,
            error=NetworkError(
                detail=f"{outcome.cause} on an {noun}; not resent",
                status=outcome.status,
                route=f"batchexecute:{rpcid}",
                next_action="reconcile",
                remediation_hint=hint,
            ),
        )


def _post_request(
    rpcid: str, source_path: str, tokens: flow_wire.Tokens, headers: dict[str, str], body: str
) -> httpx.Request:
    query = {
        "rpcids": rpcid,
        "source-path": source_path,
        "bl": tokens.bl,
        "f.sid": tokens.fsid,
        "hl": "en",
        "_reqid": str(1000 + secrets.randbelow(9000)),
        "rt": "c",
    }
    return httpx.Request(
        "POST",
        f"{_ORIGIN}/_/AiSandboxAngularFrontend/data/batchexecute",
        params={k: v for k, v in query.items() if v is not None},
        content=body,
        headers={
            **headers,
            "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            "x-same-domain": "1",
            "origin": _ORIGIN,
            "referer": f"{_ORIGIN}{source_path}",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        },
    )


class InFlightGate:
    """Admission for one transport's direct attempts, used as ``async with gate.slot():``.

    At most ``MAX_IN_FLIGHT`` attempts run at once (``GFLOW_CLI_CONCURRENCY`` sizes the UI
    page pool only and never raises it). ``quiesce()`` blocks new starts and waits for the
    in-flight ones, for a browser relaunch; entering it from inside a slot deadlocks.
    Every start is recorded, and a start rate above the measured limit is logged once per
    window; nothing waits or refuses on it. ``clock`` is wall time, like the re-arm check.
    """

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._slots = asyncio.Semaphore(MAX_IN_FLIGHT)
        self._open = asyncio.Event()
        self._open.set()
        self._idle = asyncio.Event()
        self._idle.set()
        self._in_flight = 0
        self._quiescing = 0
        self._clock = clock
        self._starts: deque[float] = deque()
        self._warned_at: float | None = None

    @contextlib.asynccontextmanager
    async def slot(self) -> AsyncGenerator[None]:
        # The slot first, then the open check: an attempt queued on a slot when a quiesce
        # begins must not run (mint, POST) once the slot frees; it hands the slot back.
        while True:
            await self._slots.acquire()
            if self._open.is_set():
                break
            self._slots.release()
            await self._open.wait()
        self._in_flight += 1
        self._idle.clear()
        try:
            self._record_start()
            yield
        finally:
            self._slots.release()
            self._in_flight -= 1
            if self._in_flight == 0:
                self._idle.set()

    @contextlib.asynccontextmanager
    async def quiesce(self) -> AsyncGenerator[None]:
        # Counted: the gate reopens only when the last of several holders exits.
        self._quiescing += 1
        self._open.clear()
        try:
            await self._idle.wait()
            yield
        finally:
            self._quiescing -= 1
            if self._quiescing == 0:
                self._open.set()

    def _record_start(self) -> None:
        now = self._clock()
        self._starts.append(now)
        while self._starts and now - self._starts[0] > START_RATE_WINDOW_S:
            self._starts.popleft()
        recent = self._warned_at is not None and now - self._warned_at <= START_RATE_WINDOW_S
        if not recent and starts_exceed(self._starts, now, window_s=START_RATE_WINDOW_S):
            self._warned_at = now
            log.warning(
                "direct.start_rate_high", count=len(self._starts), window_s=START_RATE_WINDOW_S
            )


def _client(http_transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,  # nothing env-derived (proxies, netrc) reaches this client
        timeout=httpx.Timeout(120.0, connect=10.0),  # a generation takes 16-24 s
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        transport=http_transport,  # None = httpx's default; a mock under test
    )
