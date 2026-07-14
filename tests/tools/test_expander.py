"""Unit tests for the Gemini-backed :mod:`gflow_cli.tools.expander`.

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

from gflow_cli.tools.expander import (
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


class _FakeClock:
    """Deterministic monotonic clock: returns each scripted tick in turn,
    repeating the last value once the script is exhausted."""

    def __init__(self, ticks: list[float]) -> None:
        self._ticks = ticks
        self._i = 0

    def __call__(self) -> float:
        tick = self._ticks[min(self._i, len(self._ticks) - 1)]
        self._i += 1
        return tick


class TestExpanderTimeBudget:
    def test_default_per_attempt_timeout_is_20s(self) -> None:
        # The default per-attempt timeout was lowered from 30s to 20s to cut
        # worst-case blocking under sustained rate limiting.
        transport = _RecordingTransport(returns=_candidates("ok"))
        expander = PromptExpander("key", transport=transport)

        expander.expand("cat")

        assert transport.calls[0]["timeout"] == 20.0

    def test_attempt_timeout_clamped_to_remaining_budget(self) -> None:
        # When the total budget is smaller than the per-attempt timeout, the
        # attempt must not be allowed to run longer than the budget.
        transport = _RecordingTransport(returns=_candidates("ok"))
        expander = PromptExpander(
            "key",
            transport=transport,
            timeout=20.0,
            max_total_seconds=3.0,
            clock=_FakeClock([0.0]),
        )

        expander.expand("cat")

        assert transport.calls[0]["timeout"] == 3.0

    def test_total_budget_stops_retries_early(
        self,
        install_log_capture: structlog.testing.LogCapture,
    ) -> None:
        # Sustained 429s would otherwise retry max_retries+1 times. With the
        # budget exhausted after the first failure, the expander stops early and
        # falls back rather than blocking for the full retry schedule.
        transport = _RecordingTransport(raises=GeminiHttpError(429, "rate limited"))
        # clock reads: start=0, attempt-0 budget check=0 (so the first attempt
        # runs), then 100 at the pre-retry check so it blows the 5s budget.
        clock = _FakeClock([0.0, 0.0, 100.0])
        expander = PromptExpander(
            "key",
            transport=transport,
            max_retries=3,
            max_total_seconds=5.0,
            clock=clock,
            sleep=lambda _s: None,
        )

        result = expander.expand("cat in space")

        assert result.was_expanded is False
        assert result.expanded == "cat in space"
        # Only the first attempt ran — the budget gate cut the retries.
        assert len(transport.calls) == 1
        events = {e["event"] for e in install_log_capture.entries}
        assert "prompt_expander_budget_exhausted" in events

    def test_budget_skips_first_attempt_when_already_exhausted(
        self,
        install_log_capture: structlog.testing.LogCapture,
    ) -> None:
        # A non-positive remaining budget at the very first attempt means no call
        # is made at all — straight to the fallback.
        transport = _RecordingTransport(returns=_candidates("ok"))
        expander = PromptExpander(
            "key",
            transport=transport,
            max_total_seconds=0.0,
            clock=_FakeClock([0.0]),
        )

        result = expander.expand("cat")

        assert result.was_expanded is False
        assert transport.calls == []
        events = {e["event"] for e in install_log_capture.entries}
        assert "prompt_expander_budget_exhausted" in events


def test_custom_system_instruction_is_used() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
        captured["payload"] = payload
        return {"candidates": [{"content": {"parts": [{"text": "expanded"}]}}]}

    expander = PromptExpander("key", transport=transport, system_instruction="CINEMA MODE: ")
    result = expander.expand("a cat")
    assert result.was_expanded
    sent = captured["payload"]["contents"][0]["parts"][0]["text"]  # type: ignore[index]
    assert sent.startswith("CINEMA MODE: ")
    assert "a cat" in sent


def test_token_budget_derived_from_max_output_chars() -> None:
    """maxOutputTokens in the Gemini payload must scale with max_output_chars.

    Storyboard uses max_output_chars=8000 → budget 2000.
    Default (3500) → budget 875.
    Minimum floor is 512 regardless of how small max_output_chars is.
    """
    captured: list[dict[str, object]] = []

    def transport(url: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
        captured.append(payload)
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    # Storyboard-sized budget
    exp_large = PromptExpander("key", transport=transport, max_output_chars=8000)
    exp_large.expand("test")
    tokens_large = captured[-1]["generationConfig"]["maxOutputTokens"]  # type: ignore[index]
    assert tokens_large == 2000  # 8000 // 4

    # Default budget
    exp_default = PromptExpander("key", transport=transport, max_output_chars=3500)
    exp_default.expand("test")
    tokens_default = captured[-1]["generationConfig"]["maxOutputTokens"]  # type: ignore[index]
    assert tokens_default == 875  # 3500 // 4

    # Minimum floor: tiny max_output_chars should not go below 512
    exp_tiny = PromptExpander("key", transport=transport, max_output_chars=100)
    exp_tiny.expand("test")
    tokens_tiny = captured[-1]["generationConfig"]["maxOutputTokens"]  # type: ignore[index]
    assert tokens_tiny == 512


class TestExpanderMultimodal:
    """Tests for :meth:`PromptExpander.expand_multimodal` (shared retry path)."""

    def test_expand_multimodal_success(self, tmp_path: object) -> None:
        from pathlib import Path as _Path

        img = _Path(str(tmp_path)) / "frame.jpg"
        img.write_bytes(b"FAKEJPEG")

        transport = _RecordingTransport(returns=_candidates("expanded multimodal result"))
        expander = PromptExpander("key", transport=transport)

        result = expander.expand_multimodal("describe this", [str(img)])

        assert result.was_expanded is True
        assert result.expanded == "expanded multimodal result"
        assert len(transport.calls) == 1

    def test_expand_multimodal_missing_key(self) -> None:
        transport = _RecordingTransport(returns=_candidates("never called"))
        expander = PromptExpander(None, transport=transport)

        result = expander.expand_multimodal("describe this", [])

        assert result.was_expanded is False
        assert result.expanded == "describe this"
        assert transport.calls == []

    def test_expand_multimodal_bad_image_path_is_skipped(self) -> None:
        """An unreadable image path logs a warning and is skipped; expansion still runs."""
        transport = _RecordingTransport(returns=_candidates("expanded without image"))
        expander = PromptExpander("key", transport=transport)

        result = expander.expand_multimodal("describe this", ["/nonexistent/frame.jpg"])

        # The bad path is skipped; expand_multimodal still calls the API with just the text part.
        assert len(transport.calls) == 1
        assert result.was_expanded is True

    def test_expand_multimodal_network_error_falls_back(self) -> None:
        transport = _RecordingTransport(raises=OSError("connection refused"))
        expander = PromptExpander("key", transport=transport, sleep=lambda _s: None)

        result = expander.expand_multimodal("describe this", [])

        assert result.was_expanded is False
        assert result.expanded == "describe this"

    def test_expand_multimodal_empty_response_falls_back(self) -> None:
        transport = _RecordingTransport(returns={"candidates": []})
        expander = PromptExpander("key", transport=transport)

        result = expander.expand_multimodal("describe this", [])

        assert result.was_expanded is False
