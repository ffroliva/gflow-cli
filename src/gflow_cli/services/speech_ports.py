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


@dataclass(frozen=True)
class SynthesisRequest:
    text: str
    voice: str | None
    rate: str = "+0%"
    pitch: str = "+0Hz"


@dataclass(frozen=True)
class WordTiming:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class SynthesisResult:
    audio_bytes: bytes
    media_type: str
    words: tuple[WordTiming, ...] = ()


class SpeechSynthesizer(Protocol):
    def synthesize(self, request: SynthesisRequest) -> SynthesisResult: ...
