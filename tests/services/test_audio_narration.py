from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.errors import ConfigurationError
from gflow_cli.services.audio_narration import narrate
from gflow_cli.services.speech_ports import SynthesisRequest, SynthesisResult


class FakeSynthesizer:
    def __init__(self, result: SynthesisResult | Exception | None = None) -> None:
        self.result = result or SynthesisResult(b"audio", "audio/mpeg")
        self.requests: list[SynthesisRequest] = []

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_empty_text_is_rejected_before_provider_call(tmp_path: Path) -> None:
    synthesizer = FakeSynthesizer()
    with pytest.raises(ConfigurationError, match="empty"):
        narrate(text="  ", out_file=tmp_path / "voice.mp3", synthesizer=synthesizer)
    assert synthesizer.requests == []


def test_text_file_reads_utf8_and_preserves_unicode(tmp_path: Path) -> None:
    source = tmp_path / "script ü.txt"
    source.write_text("Hello, 世界!", encoding="utf-8")
    synthesizer = FakeSynthesizer()
    target = tmp_path / "voice.mp3"
    narrate(text_file=source, out_file=target, synthesizer=synthesizer)
    assert synthesizer.requests[0].text == "Hello, 世界!"
    assert target.read_bytes() == b"audio"


def test_existing_output_is_not_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "voice.mp3"
    target.write_bytes(b"keep me")
    synthesizer = FakeSynthesizer()
    with pytest.raises(ConfigurationError, match="exists"):
        narrate(text="hello", out_file=target, synthesizer=synthesizer)
    assert target.read_bytes() == b"keep me"
    assert synthesizer.requests == []


def test_failed_synthesis_leaves_no_output_file(tmp_path: Path) -> None:
    target = tmp_path / "voice.mp3"
    synthesizer = FakeSynthesizer(RuntimeError("private provider failure"))
    with pytest.raises(RuntimeError):
        narrate(text="hello", out_file=target, synthesizer=synthesizer)
    assert not target.exists()


def test_provider_error_is_not_reported_as_success(tmp_path: Path) -> None:
    target = tmp_path / "voice.mp3"
    error = ConfigurationError("provider rejected request", charged="no")
    with pytest.raises(ConfigurationError, match="provider rejected"):
        narrate(text="hello", out_file=target, synthesizer=FakeSynthesizer(error))
    assert not target.exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"text": "hello", "text_file": Path("script.txt")},
        {"text": None, "text_file": None},
    ],
)
def test_text_and_text_file_are_required_exclusive_inputs(
    tmp_path: Path, kwargs: dict[str, object]
) -> None:
    with pytest.raises(ConfigurationError, match="(?i)provide exactly one"):
        narrate(out_file=tmp_path / "voice.mp3", synthesizer=FakeSynthesizer(), **kwargs)


def test_unreadable_text_file_is_rejected_before_provider_call(tmp_path: Path) -> None:
    synthesizer = FakeSynthesizer()
    with pytest.raises(ConfigurationError, match="read"):
        narrate(
            text_file=tmp_path / "missing.txt",
            out_file=tmp_path / "voice.mp3",
            synthesizer=synthesizer,
        )
    assert synthesizer.requests == []


def test_missing_output_parent_is_rejected_before_provider_call(tmp_path: Path) -> None:
    synthesizer = FakeSynthesizer()
    with pytest.raises(ConfigurationError, match="parent"):
        narrate(
            text="hello",
            out_file=tmp_path / "missing" / "voice.mp3",
            synthesizer=synthesizer,
        )
    assert synthesizer.requests == []


def test_invalid_provider_options_are_rejected_before_provider_call(tmp_path: Path) -> None:
    synthesizer = FakeSynthesizer()
    with pytest.raises(ConfigurationError, match="Provider must be"):
        narrate(
            text="hello",
            out_file=tmp_path / "voice.mp3",
            provider="unknown",  # type: ignore[arg-type]
            synthesizer=synthesizer,
        )
    with pytest.raises(ConfigurationError, match="invalid format"):
        narrate(
            text="hello",
            out_file=tmp_path / "voice.mp3",
            rate="fast",
            synthesizer=synthesizer,
        )
    assert synthesizer.requests == []
