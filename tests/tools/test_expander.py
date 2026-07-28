"""Unit tests for the provider-agnostic :mod:`gflow_cli.tools.expander`.

The client is exercised through an injected ``transport`` callable so no real
network traffic is made. The contract under test:

* a successful response yields a cleaned, expanded prompt;
* an unconfigured client degrades gracefully to the original prompt (no call);
* HTTP failures fall back to the original prompt — retryable statuses are
  retried up to ``max_retries`` first, non-retryable ones fail fast;
* over-long prompts are truncated *before* being sent to the API;
* the request is OpenAI Chat Completions shaped, with the tool's system
  instruction carried as a distinct ``system`` message rather than concatenated
  onto the user's text.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import structlog

from gflow_cli.tools.expander import (
    DEFAULT_BASE_URL,
    ExpansionResult,
    LlmHttpError,
    PromptExpander,
)


def _choices(text: str | None) -> dict[str, object]:
    """Build a minimal OpenAI ``chat/completions`` response envelope."""
    return {"choices": [{"message": {"content": text}}]}


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


def _messages(transport: _RecordingTransport) -> list[dict[str, Any]]:
    """The ``messages`` array from the last payload sent to the API."""
    payload = transport.calls[-1]["payload"]
    return payload["messages"]  # type: ignore[index,return-value]


def _sent_text(transport: _RecordingTransport) -> str:
    """The user-prompt text from the last payload.

    Handles both plain-string content and the multimodal content-parts list.
    """
    content = _messages(transport)[-1]["content"]
    if isinstance(content, str):
        return content
    return " ".join(part.get("text", "") for part in content if part.get("type") == "text")


class TestExpanderSuccess:
    def test_expander_success(self) -> None:
        transport = _RecordingTransport(returns=_choices('  "A fluffy cat drifting in space."  '))
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

    def test_request_targets_chat_completions(self) -> None:
        transport = _RecordingTransport(returns=_choices("ok"))
        expander = PromptExpander("key", base_url="https://gw.example/v1", transport=transport)

        expander.expand("cat")

        assert transport.calls[0]["url"] == "https://gw.example/v1/chat/completions"

    def test_trailing_slash_in_base_url_is_normalized(self) -> None:
        transport = _RecordingTransport(returns=_choices("ok"))
        expander = PromptExpander("key", base_url="https://gw.example/v1/", transport=transport)

        expander.expand("cat")

        assert transport.calls[0]["url"] == "https://gw.example/v1/chat/completions"


class TestExpanderNotConfigured:
    """No key AND no explicitly-set base_url ⇒ silent no-op, no network call."""

    def test_unconfigured_fallback(
        self,
        install_log_capture: structlog.testing.LogCapture,
    ) -> None:
        transport = _RecordingTransport(returns=_choices("never used"))
        expander = PromptExpander(None, transport=transport)

        result = expander.expand("cat in space")

        assert result == ExpansionResult(
            original="cat in space",
            expanded="cat in space",
            was_expanded=False,
        )
        # No network call attempted when nothing is configured.
        assert transport.calls == []
        events = {e["event"] for e in install_log_capture.entries}
        assert "prompt_expander_not_configured" in events

    def test_keyless_custom_base_url_is_configured(self) -> None:
        """A local gateway needs no key — an explicit base_url alone enables the tool."""
        transport = _RecordingTransport(returns=_choices("expanded"))
        expander = PromptExpander(None, base_url="http://127.0.0.1:3001/v1", transport=transport)

        result = expander.expand("cat in space")

        assert result.was_expanded is True
        assert len(transport.calls) == 1

    def test_key_with_default_base_url_is_configured(self) -> None:
        """A key alone is enough — base_url defaults to Google's compat endpoint."""
        transport = _RecordingTransport(returns=_choices("expanded"))
        expander = PromptExpander("key", transport=transport)

        result = expander.expand("cat in space")

        assert result.was_expanded is True
        assert transport.calls[0]["url"] == f"{DEFAULT_BASE_URL}/chat/completions"


class TestExpanderHttpErrorFallback:
    def test_non_retryable_status_fails_fast(self) -> None:
        transport = _RecordingTransport(raises=LlmHttpError(401, "unauthorized"))
        expander = PromptExpander("key", transport=transport, sleep=lambda _s: None)

        result = expander.expand("cat in space")

        assert result.was_expanded is False
        assert result.expanded == "cat in space"
        # 401 is not retryable — exactly one attempt.
        assert len(transport.calls) == 1

    def test_bad_key_400_also_fails_fast(self) -> None:
        """Google's OpenAI-compat endpoint answers 400 (not 401) for a bad key.

        Both must fail fast: what matters is that the failure is knowable from
        the status, and neither code is in the retryable set.
        """
        transport = _RecordingTransport(raises=LlmHttpError(400, "Please pass a valid API key"))
        expander = PromptExpander("key", transport=transport, sleep=lambda _s: None)

        result = expander.expand("cat in space")

        assert result.was_expanded is False
        assert len(transport.calls) == 1

    def test_retryable_status_retries_then_falls_back(self) -> None:
        transport = _RecordingTransport(raises=LlmHttpError(429, "rate limited"))
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

    def test_empty_choices_falls_back(self) -> None:
        transport = _RecordingTransport(returns={"choices": []})
        expander = PromptExpander("key", transport=transport)

        result = expander.expand("cat in space")

        assert result.was_expanded is False
        assert result.expanded == "cat in space"

    def test_null_content_falls_back(self) -> None:
        """A refusal returns ``content: null`` with HTTP 200.

        The defensive isinstance check in ``_extract_text`` is what keeps this
        from propagating ``None`` into the generation pipeline.
        """
        transport = _RecordingTransport(returns=_choices(None))
        expander = PromptExpander("key", transport=transport)

        result = expander.expand("cat in space")

        assert result.was_expanded is False
        assert result.expanded == "cat in space"

    def test_whitespace_only_candidate_falls_back(self) -> None:
        # A non-empty but whitespace/quote-only candidate cleans to "" — it must
        # NOT be returned as the prompt (that would abort a valid run), so the
        # expander falls back to the original. Guards the "never fatal" contract.
        transport = _RecordingTransport(returns=_choices('  "   "  '))
        expander = PromptExpander("key", transport=transport)

        result = expander.expand("cat in space")

        assert result.was_expanded is False
        assert result.expanded == "cat in space"


class TestExpanderCleaning:
    def test_preserves_internally_quoted_content(self) -> None:
        # The quote chars are real content, not a wrapping pair → leave intact.
        transport = _RecordingTransport(returns=_choices('"A" contrasted with "B"'))
        expander = PromptExpander("key", transport=transport)

        result = expander.expand("a vs b")

        assert result.was_expanded is True
        assert result.expanded == '"A" contrasted with "B"'

    def test_strips_simple_wrapping_quotes(self) -> None:
        transport = _RecordingTransport(returns=_choices('"a single wrapped prompt"'))
        expander = PromptExpander("key", transport=transport)

        result = expander.expand("prompt")

        assert result.expanded == "a single wrapped prompt"

    def test_strips_markdown_code_fence(self) -> None:
        """Gemini wraps in quotes; other models wrap in a markdown fence.

        Now that any provider can answer, the fence has to come off too or it
        would be submitted verbatim as part of the generation prompt.
        """
        transport = _RecordingTransport(returns=_choices("```\nA fenced prompt.\n```"))
        expander = PromptExpander("key", transport=transport)

        result = expander.expand("prompt")

        assert result.was_expanded is True
        assert result.expanded == "A fenced prompt."

    def test_strips_language_tagged_code_fence(self) -> None:
        transport = _RecordingTransport(returns=_choices("```text\nA tagged prompt.\n```"))
        expander = PromptExpander("key", transport=transport)

        result = expander.expand("prompt")

        assert result.expanded == "A tagged prompt."


class TestExpanderTruncation:
    def test_input_truncated_before_send(self) -> None:
        long_prompt = "x" * 5000
        transport = _RecordingTransport(returns=_choices("ok"))
        expander = PromptExpander("key", transport=transport, max_input_chars=4000)

        expander.expand(long_prompt)

        # The user prompt is clipped to max_input_chars before being embedded in
        # the request (the system instruction is a separate message, not counted).
        sent = _sent_text(transport)
        assert ("x" * 4000) in sent
        assert ("x" * 4001) not in sent

    def test_output_truncated(self) -> None:
        transport = _RecordingTransport(returns=_choices("y" * 5000))
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
    """Canary suite — the retry/budget loop must survive the transport rewrite
    untouched. If anything here needs editing beyond the response-envelope
    helper, the control flow was changed when it should not have been."""

    def test_default_per_attempt_timeout_is_20s(self) -> None:
        # The default per-attempt timeout was lowered from 30s to 20s to cut
        # worst-case blocking under sustained rate limiting.
        transport = _RecordingTransport(returns=_choices("ok"))
        expander = PromptExpander("key", transport=transport)

        expander.expand("cat")

        assert transport.calls[0]["timeout"] == 20.0

    def test_attempt_timeout_clamped_to_remaining_budget(self) -> None:
        # When the total budget is smaller than the per-attempt timeout, the
        # attempt must not be allowed to run longer than the budget.
        transport = _RecordingTransport(returns=_choices("ok"))
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
        transport = _RecordingTransport(raises=LlmHttpError(429, "rate limited"))
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
        transport = _RecordingTransport(returns=_choices("ok"))
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


class TestExpanderSystemInstruction:
    def test_system_instruction_is_a_separate_message(self) -> None:
        """The instruction rides its own ``system`` message, not the user text.

        Under the Gemini-native shape the instruction was concatenated onto the
        user's prompt. Moving it to a distinct role is a real behaviour change,
        so both halves are asserted independently: the system message must carry
        the instruction, and the user message must carry the raw prompt *without*
        it.
        """
        transport = _RecordingTransport(returns=_choices("expanded"))
        expander = PromptExpander("key", transport=transport, system_instruction="CINEMA MODE: ")

        result = expander.expand("a cat")

        assert result.was_expanded is True
        messages = _messages(transport)
        assert messages[0]["role"] == "system"
        assert "CINEMA MODE: " in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "a cat"
        # The instruction must NOT be smuggled into the user turn as well.
        assert "CINEMA MODE" not in messages[1]["content"]


class TestExpanderTokenBudget:
    def test_max_tokens_derived_from_max_output_chars(self) -> None:
        """``max_tokens`` must scale with max_output_chars.

        Storyboard uses max_output_chars=8000 → budget 2000.
        Default (3500) → budget 875.
        Minimum floor is 512 regardless of how small max_output_chars is.
        """
        transport = _RecordingTransport(returns=_choices("ok"))

        PromptExpander("key", transport=transport, max_output_chars=8000).expand("test")
        assert transport.calls[-1]["payload"]["max_tokens"] == 2000  # type: ignore[index]

        PromptExpander("key", transport=transport, max_output_chars=3500).expand("test")
        assert transport.calls[-1]["payload"]["max_tokens"] == 875  # type: ignore[index]

        PromptExpander("key", transport=transport, max_output_chars=100).expand("test")
        assert transport.calls[-1]["payload"]["max_tokens"] == 512  # type: ignore[index]

    def test_temperature_is_sent(self) -> None:
        transport = _RecordingTransport(returns=_choices("ok"))
        PromptExpander("key", transport=transport).expand("test")

        assert transport.calls[-1]["payload"]["temperature"] == 0.7  # type: ignore[index]


class TestExpanderModelSelection:
    def test_model_is_sent_when_set(self) -> None:
        transport = _RecordingTransport(returns=_choices("ok"))
        PromptExpander("key", model="gpt-4o-mini", transport=transport).expand("test")

        assert transport.calls[-1]["payload"]["model"] == "gpt-4o-mini"  # type: ignore[index]

    def test_model_omitted_when_unset(self) -> None:
        """No model anywhere ⇒ omit the key entirely so the gateway picks.

        A hardcoded vendor model name is exactly what stops a non-Google gateway
        from working, so ``None`` must mean 'omit', not 'send a default'.
        """
        transport = _RecordingTransport(returns=_choices("ok"))
        PromptExpander("key", model=None, transport=transport).expand("test")

        assert "model" not in transport.calls[-1]["payload"]  # type: ignore[operator]


class TestExpanderMultimodal:
    """Tests for :meth:`PromptExpander.expand_multimodal` (shared retry path)."""

    def test_expand_multimodal_success(self, tmp_path: object) -> None:
        from pathlib import Path as _Path

        img = _Path(str(tmp_path)) / "frame.jpg"
        img.write_bytes(b"FAKEJPEG")

        transport = _RecordingTransport(returns=_choices("expanded multimodal result"))
        expander = PromptExpander("key", transport=transport)

        result = expander.expand_multimodal("describe this", [str(img)])

        assert result.was_expanded is True
        assert result.expanded == "expanded multimodal result"
        assert len(transport.calls) == 1

    def test_multimodal_payload_carries_image_data_uri(self, tmp_path: object) -> None:
        """The image bytes must actually reach the payload, round-trip intact.

        Every previous multimodal test asserted only call counts and returned
        text, so a transport that silently dropped the image would still pass.
        """
        from pathlib import Path as _Path

        raw = b"\xff\xd8\xff\xe0FAKEJPEGBYTES"
        img = _Path(str(tmp_path)) / "frame.jpg"
        img.write_bytes(raw)

        transport = _RecordingTransport(returns=_choices("described"))
        expander = PromptExpander("key", transport=transport)

        expander.expand_multimodal("describe this", [str(img)])

        content = _messages(transport)[-1]["content"]
        assert isinstance(content, list), "multimodal content must be a parts list"

        image_parts = [p for p in content if p.get("type") == "image_url"]
        assert len(image_parts) == 1

        url = image_parts[0]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        assert base64.b64decode(url.split(",", 1)[1]) == raw

        text_parts = [p for p in content if p.get("type") == "text"]
        assert any("describe this" in p["text"] for p in text_parts)

    def test_multimodal_sends_one_part_per_image(self, tmp_path: object) -> None:
        from pathlib import Path as _Path

        base = _Path(str(tmp_path))
        paths = []
        for i in range(3):
            p = base / f"frame{i}.jpg"
            p.write_bytes(f"IMG{i}".encode())
            paths.append(str(p))

        transport = _RecordingTransport(returns=_choices("described"))
        expander = PromptExpander("key", transport=transport)

        expander.expand_multimodal("describe", paths)

        content = _messages(transport)[-1]["content"]
        assert len([p for p in content if p.get("type") == "image_url"]) == 3

    def test_expand_multimodal_unconfigured(self) -> None:
        transport = _RecordingTransport(returns=_choices("never called"))
        expander = PromptExpander(None, transport=transport)

        result = expander.expand_multimodal("describe this", [])

        assert result.was_expanded is False
        assert result.expanded == "describe this"
        assert transport.calls == []

    def test_expand_multimodal_bad_image_path_is_skipped(self) -> None:
        """An unreadable image path logs a warning and is skipped; expansion still runs."""
        transport = _RecordingTransport(returns=_choices("expanded without image"))
        expander = PromptExpander("key", transport=transport)

        result = expander.expand_multimodal("describe this", ["/nonexistent/frame.jpg"])

        # The bad path is skipped; expand_multimodal still calls the API with just the text part.
        assert len(transport.calls) == 1
        assert result.was_expanded is True
        content = _messages(transport)[-1]["content"]
        assert [p for p in content if p.get("type") == "image_url"] == []

    def test_expand_multimodal_network_error_falls_back(self) -> None:
        transport = _RecordingTransport(raises=OSError("connection refused"))
        expander = PromptExpander("key", transport=transport, sleep=lambda _s: None)

        result = expander.expand_multimodal("describe this", [])

        assert result.was_expanded is False
        assert result.expanded == "describe this"

    def test_expand_multimodal_empty_response_falls_back(self) -> None:
        transport = _RecordingTransport(returns={"choices": []})
        expander = PromptExpander("key", transport=transport)

        result = expander.expand_multimodal("describe this", [])

        assert result.was_expanded is False


class TestDefaultTransportRequest:
    """The real :func:`_default_transport` — header and redirect behaviour.

    These cannot go through the injected seam because the seam is what they
    replace, so ``urlopen`` is patched and the built ``Request`` inspected.
    """

    @staticmethod
    def _capture_request(monkeypatch: Any, response_body: bytes = b'{"choices":[]}') -> list[Any]:
        import urllib.request

        from gflow_cli.tools import expander as expander_mod

        captured: list[Any] = []

        class _FakeResponse:
            status = 200

            def read(self) -> bytes:
                return response_body

            def __enter__(self) -> _FakeResponse:
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

        def _fake_urlopen(request: Any, timeout: float = 0.0) -> _FakeResponse:
            captured.append(request)
            return _FakeResponse()

        # The opener is built inside the module; patch both entry points so the
        # test is agnostic to which one the implementation ends up calling.
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen, raising=False)
        monkeypatch.setattr(expander_mod, "_urlopen", _fake_urlopen, raising=False)
        return captured

    def test_bearer_header_sent_when_key_present(self, monkeypatch: Any) -> None:
        from gflow_cli.tools.expander import _default_transport

        captured = self._capture_request(monkeypatch)
        _default_transport("https://gw.example/v1/chat/completions", {"a": 1}, 5.0, "sk-abc")

        headers = {k.lower(): v for k, v in captured[0].headers.items()}
        assert headers["authorization"] == "Bearer sk-abc"

    def test_authorization_header_omitted_when_keyless(self, monkeypatch: Any) -> None:
        """A local gateway needs no credential — do not send an empty Bearer."""
        from gflow_cli.tools.expander import _default_transport

        captured = self._capture_request(monkeypatch)
        _default_transport("http://127.0.0.1:3001/v1/chat/completions", {"a": 1}, 5.0, None)

        headers = {k.lower(): v for k, v in captured[0].headers.items()}
        assert "authorization" not in headers

    def test_payload_is_json_encoded(self, monkeypatch: Any) -> None:
        from gflow_cli.tools.expander import _default_transport

        captured = self._capture_request(monkeypatch)
        _default_transport("https://gw.example/v1/chat/completions", {"model": "m"}, 5.0, "k")

        assert json.loads(captured[0].data.decode("utf-8")) == {"model": "m"}
        headers = {k.lower(): v for k, v in captured[0].headers.items()}
        assert headers["content-type"] == "application/json"

    def test_redirects_are_not_followed(self) -> None:
        """urllib re-sends ``Authorization`` across hosts on a 302.

        A hostile gateway could 302 the first request and harvest the key, so
        the opener must be built without a redirect handler.
        """
        import urllib.request

        from gflow_cli.tools.expander import _build_opener

        opener = _build_opener()
        assert not any(
            isinstance(h, urllib.request.HTTPRedirectHandler) for h in opener.handlers
        ), "opener must not follow redirects — Authorization would leak cross-host"
