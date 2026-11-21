"""Microsoft Edge Read Aloud speech adapter."""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from typing import Any, cast

from gflow_cli.errors import ConfigurationError, NetworkError, TransportTimeoutError
from gflow_cli.services.speech_ports import SynthesisRequest, SynthesisResult


@dataclass(frozen=True)
class EdgeTtsSynthesizer:
    connect_timeout: int = 10
    receive_timeout: int = 60
    attempts: int = 3

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        _validate_request(request)
        edge_tts = _load_edge_tts()
        voice = request.voice or "en-US-AndrewNeural"
        for attempt in range(self.attempts):
            audio = self._attempt(edge_tts, request, voice, last=attempt + 1 == self.attempts)
            if audio:
                return SynthesisResult(audio, "audio/mpeg")
        raise _no_audio(voice, self.attempts)

    def _attempt(
        self, edge_tts: Any, request: SynthesisRequest, voice: str, *, last: bool
    ) -> bytes:
        """Stream once; an empty result means no audio arrived and the caller may retry."""
        try:
            return asyncio.run(self._stream(edge_tts, request, voice))
        except edge_tts.exceptions.NoAudioReceived:
            # An unknown voice and a stream closed early raise the same error: retry both.
            return b""
        except ValueError as exc:
            raise ConfigurationError(
                f"Invalid voice or speech parameters for Edge TTS: {exc}",
                charged="no",
                remediation_hint=(
                    "Verify the voice name passed to --voice. "
                    "Run `edge-tts --list-voices` to see all available voices."
                ),
            ) from exc
        except TimeoutError as exc:
            raise TransportTimeoutError(
                "Edge TTS stream timed out while waiting for audio data.",
                charged="no",
                remediation_hint="Check your network connection or try a shorter narration text.",
            ) from exc
        except Exception as exc:
            if last:
                raise NetworkError(
                    "Edge TTS connection failed after retries.",
                    charged="no",
                    remediation_hint="Check connectivity to Microsoft Edge TTS and retry.",
                ) from exc
            return b""

    async def _stream(self, edge_tts: Any, request: SynthesisRequest, voice: str) -> bytes:
        communicator = edge_tts.Communicate(
            request.text,
            voice=voice,
            rate=request.rate,
            pitch=request.pitch,
            connect_timeout=self.connect_timeout,
            receive_timeout=self.receive_timeout,
        )
        audio: list[bytes] = []
        async for chunk in communicator.stream():
            if chunk.get("type") == "audio":
                audio.append(cast(bytes, chunk["data"]))
        return b"".join(audio)


def _no_audio(voice: str, attempts: int) -> ConfigurationError:
    return ConfigurationError(
        f"Edge TTS returned no audio for voice {voice!r} after {attempts} attempts.",
        charged="no",
        remediation_hint=(
            "Verify the voice name passed to --voice. "
            "Run `edge-tts --list-voices` to see all available voices. "
            "If the voice is listed, retry later."
        ),
    )


def _validate_request(request: SynthesisRequest) -> None:
    if not request.text or not request.text.strip():
        raise ConfigurationError(
            "Narration text cannot be empty.",
            charged="no",
            remediation_hint="Provide non-empty text to narrate.",
        )


def _load_edge_tts() -> Any:
    try:
        return importlib.import_module("edge_tts")
    except ImportError as exc:
        raise ConfigurationError(
            "Edge narration requires the optional dependency; install with "
            "pip install 'gflow-cli[tts]'.",
            charged="no",
            remediation_hint="Install the tts extra with pip install 'gflow-cli[tts]'.",
        ) from exc
