"""Provider selection and safe output handling for local narration audio."""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal

from gflow_cli.config import Settings, get_settings
from gflow_cli.errors import Charged, ConfigurationError, VideoAssemblyError
from gflow_cli.services.speech_ports import SpeechSynthesizer, SynthesisRequest
from gflow_cli.services.speech_synthesizers import EdgeTtsSynthesizer, GeminiTtsSynthesizer
from gflow_cli.services.video_assembly import clear_stale_reservation, publish_output

Provider = Literal["edge", "gemini"]

_WRITABLE_OUT_FILE_HINT = "Choose an output file path inside a writable directory."
_EXISTING_OUT_FILE_HINT = (
    "Choose an output file path that does not already exist, or remove the existing file."
)


@dataclass(frozen=True)
class NarrationResult:
    out_file: Path
    provider: Provider
    media_type: str

    def payload(self) -> dict[str, str]:
        """The success payload shared by the CLI and the MCP tool."""
        return {
            "status": "ok",
            "out_file": str(self.out_file),
            "provider": self.provider,
            "media_type": self.media_type,
        }


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
    validate_narration(script, provider, rate, pitch)
    _validate_output(out_file)

    adapter = synthesizer or _make_synthesizer(provider, settings or get_settings())
    temporary, staging = _open_staging(out_file)
    try:
        with staging:
            result = adapter.synthesize(SynthesisRequest(script, voice, rate, pitch))
            if not result.audio_bytes:
                raise ConfigurationError(
                    "Speech provider returned empty audio.",
                    charged="no",
                    remediation_hint="Check that the input text contains speakable words.",
                )
            # Gemini bills a completed synthesis even when the local write then fails.
            charged: Charged = "maybe" if provider == "gemini" else "no"
            with _output_errors(out_file, charged):
                staging.write(result.audio_bytes)
                staging.flush()
        with _output_errors(out_file, charged):
            publish_output(temporary, out_file)
        return NarrationResult(out_file, provider, result.media_type)
    finally:
        temporary.unlink(missing_ok=True)


def validate_narration(text: str, provider: str, rate: str, pitch: str) -> None:
    """Refuse bad input before any file, folder or provider call is made."""
    if not text.strip():
        raise ConfigurationError(
            "Narration text cannot be empty.",
            charged="no",
            remediation_hint="Provide non-empty text to narrate.",
        )
    if provider not in ("edge", "gemini"):
        raise ConfigurationError(
            "Provider must be edge or gemini.",
            charged="no",
            remediation_hint="Select a supported provider: edge or gemini.",
        )
    if not _valid_rate(rate) or not _valid_pitch(pitch):
        raise ConfigurationError(
            "Rate or pitch has an invalid format.",
            charged="no",
            remediation_hint=(
                "Use formats like +10% or -5% for rate, and +20Hz or -10Hz for pitch."
            ),
        )


@contextmanager
def _output_errors(out_file: Path, charged: Charged) -> Generator[None, None, None]:
    """Map a failed write or publication of the output file to an actionable error."""
    try:
        yield
    except VideoAssemblyError as exc:  # publish_output raises it only for an existing output
        raise ConfigurationError(
            f"Narration output already exists: {out_file}",
            charged=charged,
            remediation_hint=_EXISTING_OUT_FILE_HINT,
        ) from exc
    except OSError as exc:
        raise ConfigurationError(
            f"Could not write narration output: {out_file}",
            charged=charged,
            remediation_hint=_WRITABLE_OUT_FILE_HINT,
        ) from exc


def _read_script(text: str | None, text_file: Path | None) -> str:
    if (text is None) == (text_file is None):
        raise ConfigurationError(
            "Provide exactly one of text or text_file.",
            charged="no",
            remediation_hint=(
                "Pass either the narration text or a UTF-8 text file holding the script."
            ),
        )
    if text_file is not None:
        try:
            text = text_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ConfigurationError(
                f"Could not read UTF-8 narration text file: {text_file}",
                charged="no",
                remediation_hint=(
                    "Ensure the text file exists, is readable, and contains valid UTF-8 text."
                ),
            ) from exc
    assert text is not None
    return text


def _validate_output(path: Path) -> None:
    if not path.parent.is_dir():
        raise ConfigurationError(
            f"Narration output parent does not exist: {path.parent}",
            charged="no",
            remediation_hint=(
                "Create the parent directory or choose a path in an existing directory."
            ),
        )
    try:
        clear_stale_reservation(path)
    except VideoAssemblyError as exc:
        raise ConfigurationError(
            f"Narration {exc.detail}",
            charged="no",
            remediation_hint=_EXISTING_OUT_FILE_HINT,
        ) from exc
    if path.exists():
        raise ConfigurationError(
            f"Narration output already exists: {path}",
            charged="no",
            remediation_hint=_EXISTING_OUT_FILE_HINT,
        )


def _open_staging(path: Path) -> tuple[Path, BinaryIO]:
    """Create the staging file exclusively and keep its descriptor for the write."""
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    # O_EXCL refuses an existing name or symlink; mode 0o666 lets the OS apply the
    # umask, so the published file gets the default mode without touching the umask.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    try:
        return temporary, os.fdopen(os.open(temporary, flags, 0o666), "wb")
    except OSError as exc:
        raise ConfigurationError(
            f"Narration output parent is not writable: {path.parent}",
            charged="no",
            remediation_hint=_WRITABLE_OUT_FILE_HINT,
        ) from exc


def _make_synthesizer(provider: Provider, settings: Settings) -> SpeechSynthesizer:
    if provider == "edge":
        return EdgeTtsSynthesizer()
    key = settings.gemini_tts_api_key.get_secret_value() if settings.gemini_tts_api_key else None
    return GeminiTtsSynthesizer(api_key=key)


def _valid_rate(rate: str) -> bool:
    return _valid_adjustment(rate, "%")


def _valid_pitch(pitch: str) -> bool:
    return _valid_adjustment(pitch, "Hz")


def _valid_adjustment(value: str, unit: str) -> bool:
    match = re.fullmatch(rf"([+-])(\d{{1,3}}){re.escape(unit)}", value)
    return match is not None and int(match[2]) <= 100
