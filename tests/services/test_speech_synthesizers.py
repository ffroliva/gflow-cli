from __future__ import annotations

import base64
import json
import sys
import types
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from gflow_cli.services.speech_synthesizers import (
    EdgeTtsSynthesizer,
    GeminiTtsSynthesizer,
    sanitize_for_edge_tts,
)

from gflow_cli.errors import ConfigurationError, NetworkError
from gflow_cli.services.speech_ports import SynthesisRequest


def test_edge_missing_extra_names_install_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "edge_tts", None)

    with pytest.raises(ConfigurationError, match="gflow-cli\\[tts\\]"):
        EdgeTtsSynthesizer().synthesize(SynthesisRequest("hello", None))


def test_edge_retries_no_audio_received_with_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoAudioReceivedError(Exception):
        pass

    calls = 0

    class Communicate:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def stream(self) -> AsyncIterator[dict[str, Any]]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise NoAudioReceivedError
            yield {"type": "audio", "data": b"audio"}

    monkeypatch.setitem(
        sys.modules,
        "edge_tts",
        types.SimpleNamespace(
            Communicate=Communicate,
            exceptions=types.SimpleNamespace(NoAudioReceived=NoAudioReceivedError),
        ),
    )
    result = EdgeTtsSynthesizer().synthesize(SynthesisRequest("hello", "en-US-AndrewNeural"))
    assert result.audio_bytes == b"audio"
    assert calls == 2


def test_edge_normalizes_typographic_punctuation_and_emoji() -> None:
    assert sanitize_for_edge_tts("‘hello’ — don’t 😊") == "'hello' - don't "


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


def test_provider_failures_are_typed_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
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


