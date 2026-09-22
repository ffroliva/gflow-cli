"""A transient reset must not discard a clip Flow already generated and billed (#895).

The fault these tests inject is real, not imagined: `E4
<../../../docs/superpowers/spikes/2026-09-22-playwright-max-retries-econnreset.md>`_
measured ``APIRequestContext.get(max_retries=2)`` against a socket emitting a TCP RST and
showed (a) it retries ``ECONNRESET``, (b) a control with ``max_retries=0`` fails on the
same fault, and (c) all attempts share **one** timeout budget — so three attempts on our
180 s download stay inside ~180 s rather than stretching to nine minutes.

That is why these tests assert the *argument* rather than re-testing Playwright's retry
loop: the driver's behaviour is measured in the spike, and what can regress here is our
call dropping the argument or our error handling losing the clip.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Error as PwError

from gflow_cli.api.transports.batchexecute import STATUS_DONE, GenerationRecord
from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.api.transports.migrated_recover import _fetch_verified
from gflow_cli.errors import EXIT_CODE_MAP, NetworkError, is_retryable
from gflow_cli.exceptions import WireFormatError

MEDIA_ID = "9ad33c78-5762-4cbc-bcbe-07a4c3b061c7"
WORKFLOW_ID = "5354d486-d9b0-4c2b-b906-517249f09dc3"
PROJECT_ID = "339f65ee-fec3-436e-bdd1-fa2acdbf4afd"
# A signed CDN URL carries Google-issued query credentials. Any test that renders an error
# asserts these substrings are absent from it.
SIGNED = (
    f"https://flow-content.google/video/{WORKFLOW_ID}"
    "?Expires=1790000000&KeyName=labs-flow-prod-cdn-key&Signature=deadbeefcafe"
)
POSTER = f"https://flow-content.google/poster/{WORKFLOW_ID}?Expires=1790000000&Signature=abc"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x11" * 512


def PlaywrightError() -> PwError:  # noqa: N802 - reads as a constructor at call sites
    """The real class ``APIRequestContext.get`` raises, with a realistic message.

    Deliberately not a stand-in: production identifies this failure **by exception type**
    (`retryable_engine_errors()`), never by matching the message — a substring predicate
    would be the error-message analogue of the locale-dependent selectors AGENTS.md
    forbids. A fake exception class would let a broken type predicate pass.

    The message embeds a signed URL on purpose, because Playwright concatenates its
    server-side call log into it. Any test that renders this into user-facing text and
    does not strip it fails loudly.
    """
    return PwError(f"apiRequest.get: read ECONNRESET\n  url: {SIGNED}")


class _Response:
    def __init__(self, body: bytes = MP4, status: int = 200) -> None:
        self.status = status
        self._body = body

    async def body(self) -> bytes:
        return self._body


class _RequestApi:
    """Records every ``get`` kwarg and replays a scripted outcome per call."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        outcome = self._outcomes.pop(0) if self._outcomes else _Response()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakePage:
    def __init__(self, *outcomes: Any) -> None:
        self.request = _RequestApi(list(outcomes))


def _record(*, video_url: str | None = SIGNED, poster_url: str | None = None) -> GenerationRecord:
    return GenerationRecord(
        workflow_id=WORKFLOW_ID,
        project_id=PROJECT_ID,
        media_id=MEDIA_ID,
        status=STATUS_DONE,
        video_url=video_url,
        poster_url=poster_url,
        size_bytes=len(MP4),
    )


def _rendered(exc: NetworkError) -> str:
    """Everything the user or an MCP agent can see, as one string."""
    return " ".join(str(v) for v in exc.to_problem_details().values() if v is not None)


class TestRetryIsRequested:
    """The transfer must ask Playwright to survive a reset. Dropping this argument is the
    whole of #895, so it is asserted at every site rather than inferred from one."""

    async def test_composer_download_requests_retries(self) -> None:
        page = FakePage(_Response())
        await MigratedComposer._fetch_mp4(page, _record())  # pyright: ignore[reportPrivateUsage, reportArgumentType]
        assert page.request.calls[0]["max_retries"] >= 1

    async def test_recovery_download_requests_retries(self) -> None:
        page = FakePage(_Response())
        await _fetch_verified(
            page,  # pyright: ignore[reportArgumentType] - structural fake
            url=SIGNED,
            expected=len(MP4),
            media_id=MEDIA_ID,
        )
        assert page.request.calls[0]["max_retries"] >= 1

    async def test_the_open_redirect_posture_is_unchanged(self) -> None:
        """Retrying must not relax ``max_redirects=0``: a retried request rebounding
        through a CDN open redirect is the one shape that turns a transfer fix into a
        security regression."""
        page = FakePage(_Response())
        await MigratedComposer._fetch_mp4(page, _record())  # pyright: ignore[reportPrivateUsage, reportArgumentType]
        assert page.request.calls[0]["max_redirects"] == 0


class TestExhaustedTransferIsTyped:
    """A reset that outlives the retries must arrive as a gflow error, not as a raw
    Playwright exception rendered ``Unexpected error … retryable: False``."""

    async def test_it_raises_network_error(self) -> None:
        page = FakePage(PlaywrightError())
        with pytest.raises(NetworkError):
            await MigratedComposer._fetch_mp4(page, _record())  # pyright: ignore[reportPrivateUsage, reportArgumentType]

    async def test_it_exits_6_and_is_marked_retryable(self) -> None:
        page = FakePage(PlaywrightError())
        with pytest.raises(NetworkError) as caught:
            await MigratedComposer._fetch_mp4(page, _record())  # pyright: ignore[reportPrivateUsage, reportArgumentType]
        assert EXIT_CODE_MAP[NetworkError] == 6
        assert is_retryable(caught.value) is True

    async def test_it_leaks_no_signed_url_credentials(self) -> None:
        """``detail`` reaches stderr and ``--json`` stdout unredacted, and the Playwright
        message embeds the request URL — so the exception text must never be forwarded."""
        page = FakePage(PlaywrightError())
        with pytest.raises(NetworkError) as caught:
            await MigratedComposer._fetch_mp4(page, _record())  # pyright: ignore[reportPrivateUsage, reportArgumentType]
        rendered = _rendered(caught.value)
        assert "Expires=" not in rendered
        assert "Signature=" not in rendered
        assert "KeyName=" not in rendered

    async def test_it_tells_the_user_the_clip_survived(self) -> None:
        """The clip is generated and billed by the time this runs. An error that does not
        say so, with the id, sends the user to re-generate and pay twice."""
        page = FakePage(PlaywrightError())
        with pytest.raises(NetworkError) as caught:
            await MigratedComposer._fetch_mp4(page, _record())  # pyright: ignore[reportPrivateUsage, reportArgumentType]
        rendered = _rendered(caught.value)
        assert MEDIA_ID in rendered
        assert "gflow data download" in rendered

    async def test_the_recovery_path_is_typed_too(self) -> None:
        """``gflow data download`` is where the composer error sends people. If its own
        GET dies untyped, the escape hatch is as broken as the thing it rescues."""
        page = FakePage(PlaywrightError())
        with pytest.raises(NetworkError):
            await _fetch_verified(
                page,  # pyright: ignore[reportArgumentType] - structural fake
                url=SIGNED,
                expected=len(MP4),
                media_id=MEDIA_ID,
            )

    async def test_the_recovery_hint_is_not_circular(self) -> None:
        """Telling someone running ``gflow data download`` to run ``gflow data download``
        is not advice. It must say what a re-run changes."""
        page = FakePage(PlaywrightError())
        with pytest.raises(NetworkError) as caught:
            await _fetch_verified(
                page,  # pyright: ignore[reportArgumentType] - structural fake
                url=SIGNED,
                expected=len(MP4),
                media_id=MEDIA_ID,
            )
        hint = str(caught.value.to_problem_details().get("remediation_hint", ""))
        assert "nothing was billed" in hint.lower()


class TestWhatRetryMustNotChange:
    """Three behaviours that a careless retry silently breaks."""

    async def test_a_bad_status_is_not_retried(self) -> None:
        """A 403 is a returned response, not an exception, so it must reach the status
        guard on the first attempt and fail there."""
        page = FakePage(_Response(body=b"", status=403))
        with pytest.raises((WireFormatError, NetworkError)):
            await MigratedComposer._fetch_mp4(  # pyright: ignore[reportPrivateUsage]
                page,  # pyright: ignore[reportArgumentType] - structural fake
                _record(poster_url=None),
            )
        assert len(page.request.calls) == 1

    async def test_an_expired_link_is_not_blamed_on_the_prompt(self) -> None:
        """``WireFormatError``'s default remediation says *"retry with a simpler prompt
        text"* — on a path that carries no prompt. Same wrong-advice class #875 removed."""
        page = FakePage(_Response(body=b"", status=403))
        with pytest.raises((WireFormatError, NetworkError)) as caught:
            await _fetch_verified(
                page,  # pyright: ignore[reportArgumentType] - structural fake
                url=SIGNED,
                expected=len(MP4),
                media_id=MEDIA_ID,
            )
        rendered = " ".join(
            str(v) for v in caught.value.to_problem_details().values() if v is not None
        )
        assert "simpler prompt" not in rendered

    async def test_a_poster_jpeg_is_still_rejected(self) -> None:
        """The ``ftyp`` check stays outside the retry: a wrong-artifact body is a correct
        response, and retrying it would burn attempts on a result that will not change."""
        page = FakePage(
            _Response(body=b"\xff\xd8\xff\xe0JFIF"), _Response(body=b"\xff\xd8\xff\xe0")
        )
        with pytest.raises(WireFormatError):
            await MigratedComposer._fetch_mp4(  # pyright: ignore[reportPrivateUsage]
                page,  # pyright: ignore[reportArgumentType] - structural fake
                _record(poster_url=POSTER),
            )


class TestRetryIsObservable:
    async def test_no_log_event_carries_the_signed_url(
        self, caplog: pytest.LogCaptureFixture, tmp_path: Path
    ) -> None:
        """structlog kwargs are never redacted, and these paths log no URL today — so a
        per-attempt ``url=`` would be a new leak, not a multiplied one."""
        page = FakePage(PlaywrightError())
        with caplog.at_level("DEBUG"), pytest.raises(NetworkError):
            await MigratedComposer._fetch_mp4(page, _record())  # pyright: ignore[reportPrivateUsage, reportArgumentType]
        assert "Signature=" not in caplog.text
        assert "Expires=" not in caplog.text
