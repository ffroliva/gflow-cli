from dataclasses import FrozenInstanceError

import pytest

from gflow_cli.services.speech_ports import (
    SpeechSynthesizer,
    SynthesisRequest,
    SynthesisResult,
)


def test_synthesis_values_are_frozen() -> None:
    request = SynthesisRequest(text="Hello", voice="en-US-AndrewNeural")
    result = SynthesisResult(audio_bytes=b"audio", media_type="audio/mpeg")

    with pytest.raises(FrozenInstanceError):
        request.text = "Changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.audio_bytes = b"changed"  # type: ignore[misc]


def test_synthesizer_returns_audio() -> None:
    class FakeSynthesizer:
        def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
            assert request.text == "Hello world"
            return SynthesisResult(
                audio_bytes=b"audio",
                media_type="audio/mpeg",
            )

    synthesizer: SpeechSynthesizer = FakeSynthesizer()
    result = synthesizer.synthesize(SynthesisRequest(text="Hello world", voice=None))

    assert result.audio_bytes == b"audio"
    assert result.media_type == "audio/mpeg"
