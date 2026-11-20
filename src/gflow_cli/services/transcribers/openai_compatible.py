"""OpenAI-compatible audio transcription adapter."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, cast

import httpx

from gflow_cli.config import DEFAULT_LLM_BASE_URL, Settings, get_settings
from gflow_cli.data.redaction import redact_error_detail
from gflow_cli.errors import ConfigurationError, NetworkError, RateLimitError
from gflow_cli.services.speech_ports import Transcript, TranscriptionRequest, TranscriptSegment

_TIMESTAMP = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


@dataclass(frozen=True)
class OpenAICompatibleTranscriber:
    base_url: str
    api_key: str | None

    def transcribe(self, request: TranscriptionRequest) -> Transcript:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data: dict[str, str] = {"model": request.model, "response_format": "verbose_json"}
        if request.language:
            data["language"] = request.language
        try:
            with httpx.Client(follow_redirects=False, timeout=120) as client:
                response = client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers=headers,
                    data=data,
                    files={"file": ("audio.wav", request.audio_bytes, "audio/wav")},
                )
            return _parse_transcription_response(response)
        except (ConfigurationError, RateLimitError, NetworkError):
            raise
        except httpx.HTTPError as exc:
            raise NetworkError(
                detail="Transcription endpoint could not be reached.", charged="no"
            ) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ConfigurationError(
                detail="Transcription endpoint returned an invalid response.",
                charged="no",
                remediation_hint=(
                    "Configure the endpoint to return valid verbose_json segments or SRT."
                ),
            ) from exc


def make_transcriber(settings: Settings | None = None) -> OpenAICompatibleTranscriber:
    settings = settings or get_settings()
    base_url = (settings.llm_base_url or DEFAULT_LLM_BASE_URL).rstrip("/")
    if base_url == DEFAULT_LLM_BASE_URL.rstrip("/"):
        raise ConfigurationError(
            detail=(
                "Google's default endpoint does not offer /audio/transcriptions; "
                "audio was not sent. "
                "Configure GFLOW_CLI_LLM_BASE_URL for a transcription provider or use --srt."
            ),
            charged="no",
            remediation_hint=(
                "Configure GFLOW_CLI_LLM_BASE_URL, GFLOW_CLI_LLM_API_KEY, and "
                "GFLOW_CLI_TRANSCRIBE_MODEL for an OpenAI, Groq, or local Whisper server; "
                "or pass --srt to video captions."
            ),
        )
    key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    return OpenAICompatibleTranscriber(base_url, key)


def _parse_transcription_response(response: httpx.Response) -> Transcript:
    _raise_for_transcription_status(response)
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return Transcript(_parse_srt(response.text))
    raw = cast(dict[str, Any], payload).get("segments")
    if not isinstance(raw, list):
        raise ConfigurationError(
            detail="Transcription response has no timed segments; request verbose_json.",
            charged="no",
            remediation_hint=(
                "Configure the transcription endpoint to return verbose_json segments."
            ),
        )
    return Transcript(_parse_json_segments(cast(list[object], raw)))


def _raise_for_transcription_status(response: httpx.Response) -> None:
    if 300 <= response.status_code < 400:
        raise ConfigurationError(
            detail="Transcription endpoint redirected; request refused.",
            charged="no",
            remediation_hint="Configure the exact transcription endpoint URL without redirects.",
        )
    if response.status_code == 429:
        raise RateLimitError(detail="Transcription endpoint rate limit reached.", retry_after=None)
    if response.status_code >= 500:
        raise NetworkError(
            detail=f"Transcription endpoint failed: {redact_error_detail(response.text)}",
            charged="no",
        )
    if response.status_code >= 400:
        raise ConfigurationError(
            detail=f"Transcription request rejected: {redact_error_detail(response.text)}",
            charged="no",
            remediation_hint="Check the transcription endpoint URL, model, and API key.",
        )


def _parse_json_segments(raw: list[object]) -> tuple[TranscriptSegment, ...]:
    entries: list[TranscriptSegment] = []
    for segment in raw:
        if not isinstance(segment, dict):
            continue
        parsed = cast(dict[str, Any], segment)
        try:
            start = float(parsed["start"])
            end = float(parsed["end"])
            text = str(parsed["text"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if text and math.isfinite(start) and math.isfinite(end) and 0 <= start < end:
            entries.append(TranscriptSegment(start, end, text))
    if entries:
        return tuple(entries)
    raise ConfigurationError(
        detail="Transcription completed but no speech found.",
        charged="no",
        remediation_hint="Use a clip with speech and retry.",
    )


def _seconds(value: str) -> float:
    match = _TIMESTAMP.fullmatch(value)
    assert match
    hours, minutes, seconds, millis = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def _parse_srt(content: str) -> tuple[TranscriptSegment, ...]:
    content = content.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    entries: list[TranscriptSegment] = []
    for block_number, block in enumerate(re.split(r"\n\s*\n", content), 1):
        lines = block.splitlines()
        match = re.fullmatch(
            r"\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})[^\n]*",
            lines[1] if len(lines) >= 2 and lines[0].strip().isdigit() else "",
        )
        if not match:
            raise ConfigurationError(
                detail=f"Invalid SubRip file 'transcription response' at block {block_number}.",
                charged="no",
                remediation_hint="Correct the SRT block index and timestamp lines, then retry.",
            )
        text = "\n".join(lines[2:]).strip()
        if not text:
            continue
        start, end = _seconds(match.group(1)), _seconds(match.group(2))
        if end <= start:
            raise ConfigurationError(
                detail=f"Invalid SubRip file 'transcription response' at block {block_number}.",
                charged="no",
                remediation_hint="Correct the SRT timestamps so the end follows the start.",
            )
        entries.append(TranscriptSegment(start, end, text))
    if not entries:
        raise ConfigurationError(
            detail="Invalid SubRip file 'transcription response' at block 1.",
            charged="no",
            remediation_hint="Provide an SRT file with at least one timed caption cue.",
        )
    return tuple(entries)
