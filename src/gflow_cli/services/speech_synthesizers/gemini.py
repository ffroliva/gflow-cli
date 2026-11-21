"""Gemini text-to-speech adapter."""

from __future__ import annotations

import base64
import io
import re
import wave
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from gflow_cli.api._retry import parse_retry_after
from gflow_cli.api.character import VOICES
from gflow_cli.errors import (
    Charged,
    ConfigurationError,
    NetworkError,
    RateLimitError,
    TransportTimeoutError,
)
from gflow_cli.services.speech_ports import SynthesisRequest, SynthesisResult

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_MODEL = "gemini-3.8-flash-tts"
_DEFAULT_RATE = "+0%"
_DEFAULT_PITCH = "+0Hz"
_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


@dataclass(frozen=True)
class GeminiTtsSynthesizer:
    api_key: str | None = field(default=None, repr=False)
    model: str = _MODEL

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        _validate_request(self.api_key, request)
        voice = _canonical_voice(request.voice)
        try:
            with httpx.Client(follow_redirects=False, timeout=_HTTP_TIMEOUT) as client:
                response = client.post(
                    f"{_BASE_URL}/models/{self.model}:generateContent",
                    headers={"x-goog-api-key": cast(str, self.api_key)},
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
            _check_response_status(response)
            return _parse_audio(response.json())
        except (ConfigurationError, NetworkError, RateLimitError, TransportTimeoutError):
            raise
        except httpx.TimeoutException as exc:
            raise TransportTimeoutError(
                "Gemini TTS endpoint timed out while generating audio.",
                charged=_charged_after(exc),
                remediation_hint="Check your network connection or try a shorter narration text.",
            ) from exc
        except httpx.HTTPError as exc:
            raise NetworkError(
                "Gemini TTS endpoint could not be reached.",
                charged=_charged_after(exc),
                remediation_hint="Check connectivity to Google Generative Language API and retry.",
            ) from exc
        # ValueError also covers binascii.Error from the base64 decode.
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ConfigurationError(
                "Gemini TTS returned an invalid audio response.",
                charged="maybe",
                remediation_hint="Verify that the model returned audio content or retry.",
            ) from exc


def _validate_request(api_key: str | None, request: SynthesisRequest) -> None:
    if not api_key:
        raise ConfigurationError(
            "Gemini TTS requires GFLOW_CLI_GEMINI_TTS_API_KEY.",
            charged="no",
            remediation_hint=(
                "Set GFLOW_CLI_GEMINI_TTS_API_KEY with your Google AI Studio API key."
            ),
        )
    if request.rate != _DEFAULT_RATE or request.pitch != _DEFAULT_PITCH:
        raise ConfigurationError(
            "Gemini TTS does not support custom rate or pitch; use the edge provider.",
            charged="no",
            remediation_hint="Leave rate and pitch at their defaults, or use the edge provider.",
        )


def _check_response_status(response: httpx.Response) -> None:
    if 300 <= response.status_code < 400:
        raise ConfigurationError(
            "Gemini TTS redirected; request refused.",
            charged="no",
            remediation_hint="Verify the API endpoint URL.",
        )
    status = response.status_code
    if status == 429:
        raise RateLimitError(
            "Gemini TTS rate limit reached.",
            retry_after=parse_retry_after(response),
            remediation_hint="Wait a moment before retrying, or use the edge provider.",
        )
    if status >= 500:
        raise NetworkError(
            f"Gemini TTS endpoint failed (HTTP {status}).",
            charged="maybe",
            remediation_hint="Retry after a moment or use the edge provider.",
        )
    if status >= 400:
        raise ConfigurationError(
            f"Gemini TTS request was rejected (HTTP {status}).",
            charged="no",
            remediation_hint=_REJECTION_HINTS.get(status, _KEY_HINT),
        )


_KEY_HINT = "Check that GFLOW_CLI_GEMINI_TTS_API_KEY is a valid API key with Gemini API access."
_REJECTION_HINTS = {
    400: "Check the voice name, or try a shorter narration text.",
    404: f"The TTS model {_MODEL} is not available to this API key; use the edge provider.",
}


def _charged_after(exc: httpx.HTTPError) -> Charged:
    """``no`` only when the request never left: Google bills a request it received."""
    return "no" if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)) else "maybe"


def _canonical_voice(value: str | None) -> str:
    requested = value or "Algieba"
    for voice in VOICES:
        if voice.name.casefold() == requested.casefold():
            return voice.name
    raise ConfigurationError(
        "Unknown Gemini TTS voice.",
        charged="no",
        remediation_hint="Run `gflow character voices` to see supported Gemini voice names.",
    )


def _parse_audio(payload: Any) -> SynthesisResult:
    part = cast(dict[str, Any], payload)["candidates"][0]["content"]["parts"][0]
    audio = part["inlineData"]
    data = base64.b64decode(audio["data"], validate=True)
    media_type = audio.get("mimeType", "audio/wav")
    if not data or not isinstance(media_type, str) or not media_type.startswith("audio/"):
        raise ValueError("invalid audio payload")
    if media_type.lower().startswith("audio/l16"):
        rate_match = re.search(r"rate=(\d+)", media_type, re.IGNORECASE)
        sample_rate = int(rate_match.group(1)) if rate_match else 24000
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(data)
        data = output.getvalue()
        media_type = "audio/wav"
    return SynthesisResult(data, media_type)
