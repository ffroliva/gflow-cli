from __future__ import annotations

import base64
import json
import sys
import types
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import structlog

from gflow_cli.errors import ConfigurationError, NetworkError, TransportTimeoutError
from gflow_cli.services.speech_ports import SynthesisRequest
from gflow_cli.services.speech_synthesizers import (
    EdgeTtsSynthesizer,
    GeminiTtsSynthesizer,
)


def test_edge_missing_extra_names_install_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "edge_tts", None)

    with pytest.raises(ConfigurationError, match="tts extra") as raised:
        EdgeTtsSynthesizer().synthesize(SynthesisRequest("hello", None))
    assert "tts]" in raised.value.remediation_hint


def test_edge_retries_transient_network_error_with_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class Communicate:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def stream(self) -> AsyncIterator[dict[str, Any]]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("connection dropped")
            yield {"type": "audio", "data": b"audio"}

    monkeypatch.setitem(
        sys.modules,
        "edge_tts",
        types.SimpleNamespace(
            Communicate=Communicate,
            exceptions=types.SimpleNamespace(
                NoAudioReceived=type("NoAudioReceived", (Exception,), {})
            ),
        ),
    )
    result = EdgeTtsSynthesizer().synthesize(SynthesisRequest("hello", "en-US-AndrewNeural"))
    assert result.audio_bytes == b"audio"
    assert calls == 2


def test_edge_returns_typed_error_after_retry_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    class Communicate:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def stream(self) -> AsyncIterator[dict[str, Any]]:
            raise ConnectionError("connection dropped")
            yield {}

    monkeypatch.setitem(
        sys.modules,
        "edge_tts",
        types.SimpleNamespace(
            Communicate=Communicate,
            exceptions=types.SimpleNamespace(
                NoAudioReceived=type("NoAudioReceived", (Exception,), {})
            ),
        ),
    )
    with pytest.raises(NetworkError, match="after retries"):
        EdgeTtsSynthesizer(attempts=2).synthesize(SynthesisRequest("hello", None))


def test_edge_preserves_text(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_text = ""

    class CaptureCommunicate:
        def __init__(self, text: str, *_: Any, **__: Any) -> None:
            nonlocal sent_text
            sent_text = text

        async def stream(self) -> AsyncIterator[dict[str, Any]]:
            yield {"type": "audio", "data": b"audio"}

    monkeypatch.setitem(
        sys.modules,
        "edge_tts",
        types.SimpleNamespace(
            Communicate=CaptureCommunicate,
            exceptions=types.SimpleNamespace(
                NoAudioReceived=type("NoAudioReceived", (Exception,), {})
            ),
        ),
    )
    raw_text = "‘hello’ — don’t 😊"
    EdgeTtsSynthesizer().synthesize(SynthesisRequest(raw_text, "en-US-AndrewNeural"))
    assert sent_text == raw_text


def test_gemini_uses_dedicated_key_and_parses_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "audio/wav",
                                "data": base64.b64encode(b"wav").decode(),
                            }
                        }
                    ]
                }
            }
        ]
    }
    captured: dict[str, Any] = {}

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            captured["client"] = kwargs

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def post(self, url: str, **kwargs: Any) -> httpx.Response:
            captured.update(url=url, **kwargs)
            return httpx.Response(200, json=payload)

    monkeypatch.setattr("gflow_cli.services.speech_synthesizers.gemini.httpx.Client", Client)
    result = GeminiTtsSynthesizer(api_key="private-key").synthesize(
        SynthesisRequest("private text", "Algieba")
    )
    assert result.audio_bytes == b"wav"
    assert captured["headers"]["x-goog-api-key"] == "private-key"
    assert "private text" not in json.dumps(captured["headers"])
    assert (
        captured["json"]["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"][
            "voiceName"
        ]
        == "Algieba"
    )


def test_provider_failures_are_typed_and_redacted(
    monkeypatch: pytest.MonkeyPatch, install_log_capture: structlog.testing.LogCapture
) -> None:
    class Client:
        def __init__(self, **_: Any) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def post(self, *_: object, **__: object) -> httpx.Response:
            return httpx.Response(500, text="private text private-key")

    monkeypatch.setattr("gflow_cli.services.speech_synthesizers.gemini.httpx.Client", Client)
    with pytest.raises(NetworkError) as raised:
        GeminiTtsSynthesizer(api_key="private-key").synthesize(
            SynthesisRequest("private text", "Algieba")
        )
    assert "private text" not in str(raised.value)
    assert "private-key" not in str(raised.value)
    assert "private text" not in str(install_log_capture.entries)
    assert "private-key" not in str(install_log_capture.entries)


@pytest.mark.parametrize("controls", [{"rate": "+10%"}, {"pitch": "+1Hz"}])
def test_gemini_rejects_non_default_rate_or_pitch_without_request(
    monkeypatch: pytest.MonkeyPatch, controls: dict[str, str]
) -> None:
    def unexpected_request(*_: Any, **__: Any) -> None:
        pytest.fail("Gemini request should not be made")

    monkeypatch.setattr(
        "gflow_cli.services.speech_synthesizers.gemini.httpx.Client", unexpected_request
    )
    with pytest.raises(ConfigurationError, match="custom rate or pitch"):
        GeminiTtsSynthesizer(api_key="secret").synthesize(
            SynthesisRequest("hello", "Algieba", **controls)
        )


def test_gemini_missing_key_names_dedicated_setting() -> None:
    with pytest.raises(ConfigurationError, match="GFLOW_CLI_GEMINI_TTS_API_KEY"):
        GeminiTtsSynthesizer(api_key=None).synthesize(SynthesisRequest("hello", None))


@pytest.mark.parametrize(
    "post",
    [
        lambda *_args, **_kwargs: httpx.Response(200, json={"candidates": []}),
        lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.ReadTimeout("secret text key")),
    ],
    ids=["malformed-audio", "timeout"],
)
def test_gemini_malformed_and_timeout_responses_are_typed_and_redacted(
    monkeypatch: pytest.MonkeyPatch, post: Any
) -> None:
    class Client:
        def __init__(self, **_: Any) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def post(self, *args: Any, **kwargs: Any) -> Any:
            return post(*args, **kwargs)

    monkeypatch.setattr("gflow_cli.services.speech_synthesizers.gemini.httpx.Client", Client)
    with pytest.raises((ConfigurationError, NetworkError, TransportTimeoutError)) as raised:
        GeminiTtsSynthesizer(api_key="secret-key").synthesize(
            SynthesisRequest("private text", "Algieba")
        )
    assert "private text" not in str(raised.value)
    assert "secret-key" not in str(raised.value)


def test_gemini_api_key_masked_in_repr() -> None:
    synthesizer = GeminiTtsSynthesizer(api_key="my-super-secret-key")
    rep = repr(synthesizer)
    assert "my-super-secret-key" not in rep


def test_gemini_converts_l16_pcm_to_wav_with_parsed_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import wave

    raw_pcm = b"\x00\x00" * 100
    b64_pcm = base64.b64encode(raw_pcm).decode("ascii")
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "audio/L16;codec=pcm;rate=24000",
                                "data": b64_pcm,
                            }
                        }
                    ]
                }
            }
        ]
    }

    class Client:
        def __init__(self, **_: Any) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def post(self, *_: Any, **__: Any) -> httpx.Response:
            return httpx.Response(200, json=payload)

    monkeypatch.setattr("gflow_cli.services.speech_synthesizers.gemini.httpx.Client", Client)
    synthesizer = GeminiTtsSynthesizer(api_key="test-key")
    result = synthesizer.synthesize(SynthesisRequest("hello", "Algieba"))
    assert result.media_type == "audio/wav"
    with wave.open(io.BytesIO(result.audio_bytes), "rb") as reader:
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getframerate() == 24000
        assert reader.getnframes() == 100


def test_edge_timeout_raises_transport_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class TimeoutCommunicate:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def stream(self) -> AsyncIterator[dict[str, Any]]:
            raise TimeoutError("stream read timed out")
            yield {}

    monkeypatch.setitem(
        sys.modules,
        "edge_tts",
        types.SimpleNamespace(
            Communicate=TimeoutCommunicate,
            exceptions=types.SimpleNamespace(
                NoAudioReceived=type("NoAudioReceived", (Exception,), {})
            ),
        ),
    )
    with pytest.raises(TransportTimeoutError, match="timed out"):
        EdgeTtsSynthesizer().synthesize(SynthesisRequest("hello", "en-US-AndrewNeural"))


def test_gemini_timeout_raises_transport_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Client:
        def __init__(self, **_: Any) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def post(self, *_: Any, **__: Any) -> Any:
            raise httpx.ReadTimeout("Gemini read timed out")

    monkeypatch.setattr("gflow_cli.services.speech_synthesizers.gemini.httpx.Client", Client)
    with pytest.raises(TransportTimeoutError, match="timed out"):
        GeminiTtsSynthesizer(api_key="secret-key").synthesize(SynthesisRequest("hello", "Algieba"))


def test_edge_value_error_raises_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingCommunicate:
        def __init__(self, *_: Any, **__: Any) -> None:
            raise ValueError("Invalid voice format")

    monkeypatch.setitem(
        sys.modules,
        "edge_tts",
        types.SimpleNamespace(
            Communicate=FailingCommunicate,
            exceptions=types.SimpleNamespace(
                NoAudioReceived=type("NoAudioReceived", (Exception,), {})
            ),
        ),
    )
    with pytest.raises(ConfigurationError, match="Invalid voice"):
        EdgeTtsSynthesizer().synthesize(SynthesisRequest("hello", "bad-shape"))


def _no_audio_edge(
    monkeypatch: pytest.MonkeyPatch, *, failures: int
) -> tuple[type[Exception], list[int]]:
    """Install a fake edge_tts whose first `failures` streams raise NoAudioReceived."""

    class NoAudioReceivedError(Exception):
        pass

    calls = [0]

    class Communicate:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def stream(self) -> AsyncIterator[dict[str, Any]]:
            calls[0] += 1
            if calls[0] <= failures:
                raise NoAudioReceivedError("No audio was received.")
            yield {"type": "audio", "data": b"audio"}

    monkeypatch.setitem(
        sys.modules,
        "edge_tts",
        types.SimpleNamespace(
            Communicate=Communicate,
            exceptions=types.SimpleNamespace(NoAudioReceived=NoAudioReceivedError),
        ),
    )
    return NoAudioReceivedError, calls


def test_edge_retries_transient_no_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    _, calls = _no_audio_edge(monkeypatch, failures=1)

    result = EdgeTtsSynthesizer(attempts=3).synthesize(SynthesisRequest("hello", None))

    assert result.audio_bytes == b"audio"
    assert calls == [2]


def test_edge_persistent_no_audio_refuses_after_retry_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, calls = _no_audio_edge(monkeypatch, failures=99)

    with pytest.raises(ConfigurationError) as exc_info:
        EdgeTtsSynthesizer(attempts=3).synthesize(SynthesisRequest("hello", "xx-XX-NobodyNeural"))

    assert calls == [3]
    assert "voice name" in exc_info.value.remediation_hint
    assert "edge-tts --list-voices" in exc_info.value.remediation_hint


def test_edge_empty_stream_retries_then_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class Communicate:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def stream(self) -> AsyncIterator[dict[str, Any]]:
            nonlocal calls
            calls += 1
            for chunk in ():
                yield chunk

    monkeypatch.setitem(
        sys.modules,
        "edge_tts",
        types.SimpleNamespace(
            Communicate=Communicate,
            exceptions=types.SimpleNamespace(
                NoAudioReceived=type("NoAudioReceived", (Exception,), {})
            ),
        ),
    )
    with pytest.raises(ConfigurationError, match="after 2 attempts"):
        EdgeTtsSynthesizer(attempts=2).synthesize(SynthesisRequest("hello", None))

    assert calls == 2


def _failing_edge(
    monkeypatch: pytest.MonkeyPatch, error: BaseException, *, failures: int
) -> list[int]:
    """Install a fake edge_tts whose first `failures` streams raise `error`."""
    calls = [0]

    class Communicate:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def stream(self) -> AsyncIterator[dict[str, Any]]:
            calls[0] += 1
            if calls[0] <= failures:
                raise error
            yield {"type": "audio", "data": b"audio"}

    monkeypatch.setitem(
        sys.modules,
        "edge_tts",
        types.SimpleNamespace(
            Communicate=Communicate,
            exceptions=types.SimpleNamespace(
                NoAudioReceived=type("NoAudioReceived", (Exception,), {})
            ),
        ),
    )
    return calls


def test_edge_retries_a_timeout_and_logs_the_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _failing_edge(monkeypatch, TimeoutError("read timed out"), failures=1)

    with structlog.testing.capture_logs() as logs:
        result = EdgeTtsSynthesizer(attempts=3).synthesize(SynthesisRequest("hello", None))

    assert result.audio_bytes == b"audio"
    assert calls == [2]
    assert [entry.get("error") for entry in logs] == ["TimeoutError"]


@pytest.mark.parametrize("error", [KeyError("audio"), TypeError("api changed")])
def test_edge_unexpected_error_is_not_retried_or_called_a_network_failure(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    calls = _failing_edge(monkeypatch, error, failures=99)

    with pytest.raises(type(error)):
        EdgeTtsSynthesizer(attempts=3).synthesize(SynthesisRequest("hello", None))

    assert calls == [1]


def test_edge_value_error_mid_stream_is_not_an_invalid_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _failing_edge(monkeypatch, ValueError("bad frame"), failures=99)

    with pytest.raises(ValueError, match="bad frame"):
        EdgeTtsSynthesizer(attempts=3).synthesize(SynthesisRequest("hello", None))


def _gemini_replies(monkeypatch: pytest.MonkeyPatch, reply: httpx.Response | Exception) -> None:
    class Client:
        def __init__(self, **_: Any) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def post(self, *_: object, **__: object) -> httpx.Response:
            if isinstance(reply, Exception):
                raise reply
            return reply

    monkeypatch.setattr("gflow_cli.services.speech_synthesizers.gemini.httpx.Client", Client)


@pytest.mark.parametrize(("status", "hint"), [(404, "model"), (400, "shorter"), (403, "API key")])
def test_gemini_rejection_names_the_status_and_a_matching_hint(
    monkeypatch: pytest.MonkeyPatch, status: int, hint: str
) -> None:
    _gemini_replies(monkeypatch, httpx.Response(status, text="private text private-key"))

    with pytest.raises(ConfigurationError) as raised:
        GeminiTtsSynthesizer(api_key="private-key").synthesize(SynthesisRequest("hi", None))

    assert f"(HTTP {status})" in str(raised.value)
    assert hint in raised.value.remediation_hint
    assert "private" not in str(raised.value)


def test_gemini_rate_limit_carries_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.errors import RateLimitError

    _gemini_replies(monkeypatch, httpx.Response(429, headers={"Retry-After": "7"}))

    with pytest.raises(RateLimitError) as raised:
        GeminiTtsSynthesizer(api_key="k").synthesize(SynthesisRequest("hi", None))

    assert raised.value.retry_after == 7.0


@pytest.mark.parametrize(
    ("reply", "charged"),
    [
        (httpx.ConnectError("refused"), "no"),
        (httpx.ConnectTimeout("connect"), "no"),
        (httpx.ReadTimeout("read"), "maybe"),
        (httpx.Response(503), "maybe"),
        (httpx.Response(200, json={"candidates": []}), "maybe"),
    ],
)
def test_gemini_charged_is_no_only_when_nothing_was_sent(
    monkeypatch: pytest.MonkeyPatch, reply: httpx.Response | Exception, charged: str
) -> None:
    _gemini_replies(monkeypatch, reply)

    with pytest.raises(Exception) as raised:
        GeminiTtsSynthesizer(api_key="k").synthesize(SynthesisRequest("hi", None))

    assert getattr(raised.value, "charged", None) == charged


def test_edge_missing_extra_hint_follows_the_installer(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli import update_check

    monkeypatch.setitem(sys.modules, "edge_tts", None)
    monkeypatch.setattr(update_check, "_installed_extras", lambda: ("patchright",))
    monkeypatch.setattr(
        update_check,
        "installer",
        lambda: update_check.Installer("uv", ("uv", "tool", "upgrade", "gflow-cli")),
    )

    with pytest.raises(ConfigurationError) as raised:
        EdgeTtsSynthesizer().synthesize(SynthesisRequest("hello", None))

    assert 'uv tool install --force "gflow-cli[patchright,tts]"' in raised.value.remediation_hint
