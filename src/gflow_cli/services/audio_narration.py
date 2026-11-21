"""Provider selection and safe output handling for local narration audio."""

from __future__ import annotations

import io
import os
import re
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gflow_cli.config import Settings, get_settings
from gflow_cli.errors import ConfigurationError
from gflow_cli.services.speech_ports import SpeechSynthesizer, SynthesisRequest, SynthesisResult
from gflow_cli.services.speech_synthesizers import EdgeTtsSynthesizer, GeminiTtsSynthesizer

Provider = Literal["edge", "gemini"]


@dataclass(frozen=True)
class NarrationResult:
    out_file: Path
    provider: Provider
    media_type: str


def narrate(
    *,
    out_file: Path,
    text: str | None = None,
    text_file: Path | None = None,
    provider: Provider = "edge",
    voice: str | None = None,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    synthesizer: SpeechSynthesizer | None = None,
    settings: Settings | None = None,
) -> NarrationResult:
    script = _read_script(text, text_file)
    _validate_output(out_file)
    if provider not in ("edge", "gemini"):
        raise ConfigurationError("Provider must be edge or gemini.", charged="no")
    if not _valid_rate(rate) or not _valid_pitch(pitch):
        raise ConfigurationError("Rate or pitch has an invalid format.", charged="no")

    adapter = synthesizer or _make_synthesizer(provider, settings or get_settings())
    temporary = _temporary_path(out_file)
    try:
        result = adapter.synthesize(SynthesisRequest(script, voice, rate, pitch))
        if not result.audio_bytes:
            raise ConfigurationError("Speech provider returned empty audio.", charged="no")
        audio_bytes, media_type = _container_audio(result)
        temporary.write_bytes(audio_bytes)
        try:
            os.link(temporary, out_file)
        except FileExistsError as exc:
            raise ConfigurationError(
                f"Narration output already exists: {out_file}", charged="no"
            ) from exc
        return NarrationResult(out_file, provider, media_type)
    except OSError as exc:
        raise ConfigurationError(
            f"Could not write narration output: {out_file}", charged="no"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def _read_script(text: str | None, text_file: Path | None) -> str:
    if (text is None) == (text_file is None):
        raise ConfigurationError("Provide exactly one of text or text_file.", charged="no")
    if text_file is not None:
        try:
            text = text_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ConfigurationError(
                f"Could not read UTF-8 narration text file: {text_file}", charged="no"
            ) from exc
    assert text is not None
    if not text.strip():
        raise ConfigurationError("Narration text cannot be empty.", charged="no")
    return text


def _validate_output(path: Path) -> None:
    if not path.parent.is_dir():
        raise ConfigurationError(
            f"Narration output parent does not exist: {path.parent}", charged="no"
        )
    if path.exists():
        raise ConfigurationError(f"Narration output already exists: {path}", charged="no")


def _temporary_path(path: Path) -> Path:
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as temporary:
            return Path(temporary.name)
    except OSError as exc:
        raise ConfigurationError(
            f"Narration output parent is not writable: {path.parent}", charged="no"
        ) from exc


def _make_synthesizer(provider: Provider, settings: Settings) -> SpeechSynthesizer:
    if provider == "edge":
        return EdgeTtsSynthesizer()
    key = settings.gemini_tts_api_key.get_secret_value() if settings.gemini_tts_api_key else None
    return GeminiTtsSynthesizer(api_key=key)


def _container_audio(result: SynthesisResult) -> tuple[bytes, str]:
    if result.media_type.lower().startswith("audio/l16"):
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(result.audio_bytes)
        return output.getvalue(), "audio/wav"
    return result.audio_bytes, result.media_type


def _valid_rate(rate: str) -> bool:
    return _valid_adjustment(rate, "%")


def _valid_pitch(pitch: str) -> bool:
    return _valid_adjustment(pitch, "Hz")


def _valid_adjustment(value: str, unit: str) -> bool:
    match = re.fullmatch(rf"([+-])(\d+){re.escape(unit)}", value)
    return match is not None and int(match[2]) <= 100
