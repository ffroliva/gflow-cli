"""Gemini text-to-speech adapter."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any, cast

import httpx

from gflow_cli.api.character import VOICES
from gflow_cli.errors import ConfigurationError, NetworkError, RateLimitError
from gflow_cli.services.speech_ports import SynthesisRequest, SynthesisResult

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_MODEL = "gemini-3.8-flash-tts"
_DEFAULT_RATE = "+0%"
_DEFAULT_PITCH = "+0Hz"


@dataclass(frozen=True)
class GeminiTtsSynthesizer:
    api_key: str | None
    model: str = _MODEL

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        if not self.api_key:
            raise ConfigurationError(
                "Gemini TTS requires GFLOW_CLI_GEMINI_TTS_API_KEY.", charged="no"
            )
        if request.rate != _DEFAULT_RATE or request.pitch != _DEFAULT_PITCH:
            raise ConfigurationError(
                "Gemini TTS does not support custom rate or pitch; use the edge provider.",
                charged="no",
            )
        voice = _canonical_voice(request.voice)
        try:
            with httpx.Client(follow_redirects=False, timeout=60.0) as client:
                response = client.post(
                    f"{_BASE_URL}/models/{self.model}:generateContent",
                    headers={"x-goog-api-key": self.api_key},
                    json={
                        "contents": [{"parts": [{"text": request.text}]}],
                        "generationConfig": {
                            "responseModalities": ["AUDIO"],
                            "speechConfig": {
                                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
                            },
                        },
                    },
                )
            if 300 <= response.status_code < 400:
                raise ConfigurationError("Gemini TTS redirected; request refused.", charged="no")
            if response.status_code == 429:
                raise RateLimitError("Gemini TTS rate limit reached.", retry_after=None)
            if response.status_code >= 500:
                raise NetworkError("Gemini TTS endpoint failed.", charged="no")
            if response.status_code >= 400:
                raise ConfigurationError("Gemini TTS request was rejected.", charged="no")
            return _parse_audio(response.json())
        except (ConfigurationError, NetworkError, RateLimitError):
            raise
        except httpx.HTTPError as exc:
            raise NetworkError("Gemini TTS endpoint could not be reached.", charged="no") from exc
        except (ValueError, KeyError, IndexError, TypeError, binascii.Error) as exc:
            raise ConfigurationError(
                "Gemini TTS returned an invalid audio response.", charged="no"
            ) from exc


def _canonical_voice(value: str | None) -> str:
    requested = value or "Algieba"
    for voice in VOICES:
        if voice.name.casefold() == requested.casefold():
            return voice.name
    raise ConfigurationError("Unknown Gemini TTS voice.", charged="no")


def _parse_audio(payload: Any) -> SynthesisResult:
    part = cast(dict[str, Any], payload)["candidates"][0]["content"]["parts"][0]
    audio = part["inlineData"]
    data = base64.b64decode(audio["data"], validate=True)
    media_type = audio.get("mimeType", "audio/wav")
    if not data or not isinstance(media_type, str) or not media_type.startswith("audio/"):
        raise ValueError("invalid audio payload")
    return SynthesisResult(data, media_type)
