"""Unit tests for the Gemini-backed :mod:`gflow_cli.api.prompt_expander`.

The client is exercised through an injected ``transport`` callable so no real
network traffic is made. The contract under test:

* a successful response yields a cleaned, expanded prompt;
* a missing API key degrades gracefully to the original prompt (no call);
* HTTP failures fall back to the original prompt — retryable statuses are
  retried up to ``max_retries`` first, non-retryable ones fail fast;
* over-long prompts are truncated *before* being sent to the API.
"""

from __future__ import annotations

import structlog

from gflow_cli.api.prompt_expander import (
    ExpansionResult,
    GeminiHttpError,
    PromptExpander,
)


def _candidates(text: str) -> dict[str, object]:
    """Build a minimal Gemini ``generateContent`` response envelope."""
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class _RecordingTransport:
    """Fake transport that records calls and returns/raises a scripted result."""

    def __init__(
        self,
        *,
        returns: dict[str, object] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self.returns = returns
        self.raises = raises
        self.calls: list[dict[str, object]] = []

    def __call__(self, url: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
        self.calls.append({"url": url, "payload": payload, "timeout": timeout})
        if self.raises is not None:
            raise self.raises
        assert self.returns is not None
        return self.returns


def _sent_text(transport: _RecordingTransport) -> str:
    """Extract the user-prompt text from the last payload sent to the API."""
    payload = transport.calls[-1]["payload"]
    contents = payload["contents"]  # type: ignore[index]
    return contents[0]["parts"][0]["text"]  # type: ignore[index]


class TestExpanderSuccess:
    def test_expander_success(self) -> None:
        transport = _RecordingTransport(
            returns=_candidates('  "A fluffy cat drifting in space."  ')
        )
        expander = PromptExpander("key", transport=transport)

        result = expander.expand("cat in space")

        assert isinstance(result, ExpansionResult)
        assert result.original == "cat in space"
        # Surrounding whitespace and quotes are cleaned off.
        assert result.expanded == "A fluffy cat drifting in space."
        assert result.was_expanded is True
        assert len(transport.calls) == 1
        # The user's raw prompt must reach the request payload.
        assert "cat in space" in _sent_text(transport)


class TestExpanderMissingKey:
    def test_expander_missing_key_fallback(
        self,
        install_log_capture: structlog.testing.LogCapture,
    ) -> None:
        transport = _RecordingTransport(returns=_candidates("never used"))
        expander = PromptExpander(None, transport=transport)

        result = expander.expand("cat in space")

        assert result == ExpansionResult(
            original="cat in space",
            expanded="cat in space",
            was_expanded=False,
        )
        # No network call attempted without a key.
        assert transport.calls == []
        events = {e["event"] for e in install_log_capture.entries}
        assert "prompt_expander_no_key" in events


class TestExpanderHttpErrorFallback:
    def test_non_retryable_status_fails_fast(self) -> None:
        transport = _RecordingTransport(raises=GeminiHttpError(401, "unauthorized"))
        expander = PromptExpander("key", transport=transport, sleep=lambda _s: None)

        result = expander.expand("cat in space")

        assert result.was_expanded is False
        assert result.expanded == "cat in space"
        # 401 is not retryable — exactly one attempt.
        assert len(transport.calls) == 1

    def test_retryable_status_retries_then_falls_back(self) -> None:
        transport = _RecordingTransport(raises=GeminiHttpError(429, "rate limited"))
        expander = PromptExpander(
            "key",
            transport=transport,
            max_retries=3,
            sleep=lambda _s: None,
        )

        result = expander.expand("cat in space")

        assert result.was_expanded is False
        assert result.expanded == "cat in space"
        # initial attempt + 3 retries.
        assert len(transport.calls) == 4

    def test_empty_candidates_falls_back(self) -> None:
        transport = _RecordingTransport(returns={"candidates": []})
        expander = PromptExpander("key", transport=transport)

        result = expander.expand("cat in space")

        assert result.was_expanded is False
        assert result.expanded == "cat in space"

    def test_whitespace_only_candidate_falls_back(self) -> None:
        # A non-empty but whitespace/quote-only candidate cleans to "" — it must
        # NOT be returned as the prompt (that would abort a valid run), so the
        # expander falls back to the original. Guards the "never fatal" contract.
        transport = _RecordingTransport(returns=_candidates('  "   "  '))
        expander = PromptExpander("key", transport=transport)

        result = expander.expand("cat in space")

        assert result.was_expanded is False
        assert result.expanded == "cat in space"


class TestExpanderCleaning:
    def test_preserves_internally_quoted_content(self) -> None:
        # The quote chars are real content, not a wrapping pair → leave intact.
        transport = _RecordingTransport(returns=_candidates('"A" contrasted with "B"'))
        expander = PromptExpander("key", transport=transport)

        result = expander.expand("a vs b")

        assert result.was_expanded is True
        assert result.expanded == '"A" contrasted with "B"'

    def test_strips_simple_wrapping_quotes(self) -> None:
        transport = _RecordingTransport(returns=_candidates('"a single wrapped prompt"'))
        expander = PromptExpander("key", transport=transport)

        result = expander.expand("prompt")

        assert result.expanded == "a single wrapped prompt"


class TestExpanderTruncation:
    def test_input_truncated_before_send(self) -> None:
        long_prompt = "x" * 5000
        transport = _RecordingTransport(returns=_candidates("ok"))
        expander = PromptExpander("key", transport=transport, max_input_chars=4000)

        expander.expand(long_prompt)

        # The user prompt is clipped to max_input_chars before being embedded in
        # the request (the system instruction prefix is not counted).
        sent = _sent_text(transport)
        assert ("x" * 4000) in sent
        assert ("x" * 4001) not in sent

    def test_output_truncated(self) -> None:
        transport = _RecordingTransport(returns=_candidates("y" * 5000))
        expander = PromptExpander("key", transport=transport, max_output_chars=3500)

        result = expander.expand("cat in space")

        assert result.was_expanded is True
        assert len(result.expanded) <= 3500
