"""Microsoft Edge Read Aloud speech adapter."""

from __future__ import annotations

import asyncio
import importlib
import re
from dataclasses import dataclass
from typing import Any, cast

from gflow_cli.errors import ConfigurationError, NetworkError
from gflow_cli.services.speech_ports import SynthesisRequest, SynthesisResult, WordTiming

_EMOJI = re.compile(r"[\U0001f300-\U0001faff\u2600-\u27bf\ufe0f\u200d]")
_PUNCTUATION = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "‑": "-", "—": "-"})


def sanitize_for_edge_tts(text: str) -> str:
    return _EMOJI.sub("", text.translate(_PUNCTUATION))


@dataclass(frozen=True)
class EdgeTtsSynthesizer:
    timeout: float = 60.0
    attempts: int = 3

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        try:
            edge_tts = importlib.import_module("edge_tts")
        except ImportError as exc:
            raise ConfigurationError(
                "Edge narration requires the optional dependency; install with "
                "pip install 'gflow-cli[tts]'.",
                charged="no",
            ) from exc

        voice = request.voice or "en-US-AndrewNeural"
        for attempt in range(self.attempts):
            try:
                audio, words = asyncio.run(
                    asyncio.wait_for(self._stream(edge_tts, request, voice), timeout=self.timeout)
                )
                if not audio:
                    raise NetworkError("Edge TTS returned no audio.", charged="no")
                return SynthesisResult(audio, "audio/mpeg", tuple(words))
            except edge_tts.exceptions.NoAudioReceived as exc:
                if attempt + 1 == self.attempts:
                    raise NetworkError(
                        "Edge TTS returned no audio after retries.", charged="no"
                    ) from exc
            except NetworkError:
                raise
            except Exception as exc:
                raise NetworkError("Edge TTS request failed.", charged="no") from exc
        raise AssertionError("bounded retry loop exhausted")

    async def _stream(
        self, edge_tts: Any, request: SynthesisRequest, voice: str
    ) -> tuple[bytes, list[WordTiming]]:
        communicator = edge_tts.Communicate(
            sanitize_for_edge_tts(request.text),
            voice=voice,
            rate=request.rate,
            pitch=request.pitch,
            boundary="WordBoundary",
        )
        audio: list[bytes] = []
        words: list[WordTiming] = []
        async for chunk in communicator.stream():
            if chunk.get("type") == "audio":
                audio.append(cast(bytes, chunk["data"]))
            elif chunk.get("type") == "WordBoundary":
                offset = float(chunk.get("offset", 0)) / 10_000_000
                duration = float(chunk.get("duration", 0)) / 10_000_000
                words.append(WordTiming(str(chunk.get("text", "")), offset, offset + duration))
        return b"".join(audio), words
