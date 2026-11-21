"""Microsoft Edge Read Aloud speech adapter."""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from typing import Any, cast

import structlog

from gflow_cli.errors import ConfigurationError, GFlowError, NetworkError, TransportTimeoutError
from gflow_cli.services.speech_ports import SynthesisRequest, SynthesisResult

log = structlog.get_logger(__name__)

_VOICE_HINT = "Verify the voice name. Run `edge-tts --list-voices` to see all available voices."


@dataclass(frozen=True)
class EdgeTtsSynthesizer:
    connect_timeout: int = 10
    receive_timeout: int = 60
    attempts: int = 3

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        edge_tts = _load_edge_tts()
        voice = request.voice or "en-US-AndrewNeural"
        retryable = _retryable_errors(edge_tts)
        failure: BaseException | None = None
        for attempt in range(1, self.attempts + 1):
            communicator = self._communicator(edge_tts, request, voice)
            try:
                audio = asyncio.run(_collect_audio(communicator))
            except retryable as exc:
                log.warning("edge_tts.attempt_failed", attempt=attempt, error=type(exc).__name__)
                failure = exc
                continue
            if audio:
                return SynthesisResult(audio, "audio/mpeg")
            failure = None
        raise _final_error(failure, edge_tts, voice, self.attempts) from failure

    def _communicator(self, edge_tts: Any, request: SynthesisRequest, voice: str) -> Any:
        try:
            return edge_tts.Communicate(
                request.text,
                voice=voice,
                rate=request.rate,
                pitch=request.pitch,
                connect_timeout=self.connect_timeout,
                receive_timeout=self.receive_timeout,
            )
        except ValueError as exc:
            raise ConfigurationError(
                f"Invalid voice or speech parameters for Edge TTS: {exc}",
                charged="no",
                remediation_hint=_VOICE_HINT,
            ) from exc


async def _collect_audio(communicator: Any) -> bytes:
    audio: list[bytes] = []
    async for chunk in communicator.stream():
        if chunk.get("type") == "audio":
            audio.append(cast(bytes, chunk["data"]))
    return b"".join(audio)


def _retryable_errors(edge_tts: Any) -> tuple[type[BaseException], ...]:
    """Connection-class failures; anything else is a bug and must reach the unexpected funnel."""
    errors: list[type[BaseException]] = [OSError]  # includes TimeoutError and ConnectionError
    for name in ("NoAudioReceived", "WebSocketError"):
        found = getattr(edge_tts.exceptions, name, None)
        if isinstance(found, type) and issubclass(found, BaseException):
            errors.append(found)
    try:
        errors.append(importlib.import_module("aiohttp").ClientError)
    except ImportError:
        pass
    return tuple(errors)


def _final_error(
    failure: BaseException | None, edge_tts: Any, voice: str, attempts: int
) -> GFlowError:
    if isinstance(failure, TimeoutError):
        return TransportTimeoutError(
            "Edge TTS stream timed out while waiting for audio data.",
            charged="no",
            remediation_hint="Check your network connection or try a shorter narration text.",
        )
    if failure is None or isinstance(failure, edge_tts.exceptions.NoAudioReceived):
        # An unknown voice and a stream closed early raise the same error.
        return ConfigurationError(
            f"Edge TTS returned no audio for voice {voice!r} after {attempts} attempts.",
            charged="no",
            remediation_hint=f"{_VOICE_HINT} If the voice is listed, retry later.",
        )
    return NetworkError(
        "Edge TTS connection failed after retries.",
        charged="no",
        remediation_hint="Check connectivity to Microsoft Edge TTS and retry.",
    )


def _load_edge_tts() -> Any:
    try:
        return importlib.import_module("edge_tts")
    except ImportError as exc:
        from gflow_cli.update_check import extra_install_hint

        raise ConfigurationError(
            "Edge narration requires the optional tts extra.",
            charged="no",
            remediation_hint=f"Install it with {extra_install_hint('tts')}.",
        ) from exc
