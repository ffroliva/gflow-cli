"""Gemini text-to-speech adapter."""

from __future__ import annotations

import base64
import binascii
import io
import re
import wave
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from gflow_cli.api.character import VOICES
from gflow_cli.errors import ConfigurationError, NetworkError, RateLimitError, TransportTimeoutError
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
        if not self.api_key:
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
                remediation_hint="Remove --rate and --pitch, or use --provider edge.",
            )
        voice = _canonical_voice(request.voice)
        try:
            with httpx.Client(follow_redirects=False, timeout=_HTTP_TIMEOUT) as client:
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
                raise ConfigurationError(
                    "Gemini TTS redirected; request refused.",
                    charged="no",
                    remediation_hint="Verify the API endpoint URL.",
                )
            if response.status_code == 429:
                raise RateLimitError(
                    "Gemini TTS rate limit reached.",
                    retry_after=None,
                    remediation_hint="Wait a moment before retrying, or switch to --provider edge.",
                )
            if response.status_code >= 500:
                raise NetworkError(
                    "Gemini TTS endpoint failed.",
                    charged="no",
                    remediation_hint="Retry after a moment or use --provider edge.",
                )
            if response.status_code >= 400:
                raise ConfigurationError(
                    "Gemini TTS request was rejected.",
                    charged="no",
                    remediation_hint="Check that your API key and voice parameter are valid.",
                )
            return _parse_audio(response.json())
        except (ConfigurationError, NetworkError, RateLimitError, TransportTimeoutError):
            raise
        except httpx.TimeoutException as exc:
            raise TransportTimeoutError(
                "Gemini TTS endpoint timed out while generating audio.",
                charged="no",
                remediation_hint="Check your network connection or try a shorter narration text.",
            ) from exc
        except httpx.HTTPError as exc:
            raise NetworkError(
                "Gemini TTS endpoint could not be reached.",
                charged="no",
                remediation_hint="Check connectivity to Google Generative Language API and retry.",
            ) from exc
        except (ValueError, KeyError, IndexError, TypeError, binascii.Error) as exc:
            raise ConfigurationError(
                "Gemini TTS returned an invalid audio response.",
                charged="no",
                remediation_hint="Verify that the model returned audio content or retry.",
            ) from exc


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
