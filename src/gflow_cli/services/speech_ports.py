from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TranscriptionRequest:
    """Audio request; audio_bytes contains 16 kHz mono 16-bit PCM WAV."""

    audio_bytes: bytes
    language: str | None
    model: str


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Transcript:
    segments: tuple[TranscriptSegment, ...]


class Transcriber(Protocol):
    def transcribe(self, request: TranscriptionRequest) -> Transcript: ...
