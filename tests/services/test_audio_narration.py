from __future__ import annotations

import errno
import os
import stat
import time
from pathlib import Path

import pytest

from gflow_cli.errors import ConfigurationError
from gflow_cli.services import video_assembly
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


def test_narration_succeeds_when_os_link_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_link(src: Path | str, dst: Path | str) -> None:
        raise OSError("Operation not supported (cross-device or FAT)")

    monkeypatch.setattr("gflow_cli.services.video_assembly.os.link", failing_link)
    target = tmp_path / "voice.mp3"
    synthesizer = FakeSynthesizer()
    result = narrate(text="hello", out_file=target, synthesizer=synthesizer)
    assert target.exists()
    assert target.read_bytes() == b"audio"
    assert result.out_file == target


def test_narration_errors_carry_actionable_remediation_hints(tmp_path: Path) -> None:
    target = tmp_path / "voice.mp3"
    synthesizer = FakeSynthesizer()
    with pytest.raises(ConfigurationError) as exc_info:
        narrate(text="   ", out_file=target, synthesizer=synthesizer)
    assert exc_info.value.remediation_hint is not None
    assert "make_transport" not in exc_info.value.remediation_hint

    target.write_bytes(b"existing")
    with pytest.raises(ConfigurationError) as exc_info:
        narrate(text="hello", out_file=target, synthesizer=synthesizer)
    assert exc_info.value.remediation_hint is not None
    assert "make_transport" not in exc_info.value.remediation_hint


def test_rate_with_thousands_of_digits_does_not_crash(tmp_path: Path) -> None:
    synthesizer = FakeSynthesizer()
    oversized_rate = "+" + "1" * 5000 + "%"
    with pytest.raises(ConfigurationError, match="invalid format"):
        narrate(
            text="hello",
            out_file=tmp_path / "voice.mp3",
            rate=oversized_rate,
            synthesizer=synthesizer,
        )


def test_narration_never_changes_the_process_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(_mask: int) -> int:
        raise AssertionError("os.umask is process-wide state; narration must not set it")

    monkeypatch.setattr(os, "umask", forbidden)
    target = tmp_path / "voice.mp3"

    narrate(text="hello", out_file=target, synthesizer=FakeSynthesizer())

    assert target.read_bytes() == b"audio"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_published_narration_gets_the_default_file_mode(tmp_path: Path) -> None:
    mask = os.umask(0o022)
    try:
        target = tmp_path / "voice.mp3"
        narrate(text="hello", out_file=target, synthesizer=FakeSynthesizer())
    finally:
        os.umask(mask)

    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_narration_leaves_no_staging_file_behind(tmp_path: Path) -> None:
    ok = tmp_path / "ok.mp3"
    narrate(text="hello", out_file=ok, synthesizer=FakeSynthesizer())
    with pytest.raises(RuntimeError):
        narrate(
            text="hello",
            out_file=tmp_path / "failed.mp3",
            synthesizer=FakeSynthesizer(RuntimeError("provider failure")),
        )

    assert [p.name for p in tmp_path.iterdir()] == ["ok.mp3"]


def test_provider_os_error_is_not_reported_as_an_output_failure(tmp_path: Path) -> None:
    synthesizer = FakeSynthesizer(FileNotFoundError("CA bundle missing"))

    with pytest.raises(FileNotFoundError, match="CA bundle"):
        narrate(text="hello", out_file=tmp_path / "voice.mp3", synthesizer=synthesizer)


def test_publish_failures_map_to_output_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.errors import VideoAssemblyError
    from gflow_cli.services import audio_narration

    def exists(*_: object) -> None:
        raise VideoAssemblyError(detail="Output already exists: x", route="video.assemble")

    def denied(*_: object) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(audio_narration, "publish_output", exists)
    with pytest.raises(ConfigurationError, match="already exists"):
        narrate(text="hello", out_file=tmp_path / "a.mp3", synthesizer=FakeSynthesizer())

    monkeypatch.setattr(audio_narration, "publish_output", denied)
    with pytest.raises(ConfigurationError, match="Could not write"):
        narrate(text="hello", out_file=tmp_path / "b.mp3", synthesizer=FakeSynthesizer())
    assert list(tmp_path.iterdir()) == []


def test_result_payload_reports_status_ok(tmp_path: Path) -> None:
    result = narrate(text="hello", out_file=tmp_path / "v.mp3", synthesizer=FakeSynthesizer())

    assert result.payload() == {
        "status": "ok",
        "out_file": str(tmp_path / "v.mp3"),
        "provider": "edge",
        "media_type": "audio/mpeg",
    }


@pytest.mark.parametrize(("provider", "charged"), [("edge", "no"), ("gemini", "maybe")])
def test_output_failure_after_synthesis_reports_the_provider_charge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, charged: str
) -> None:
    from gflow_cli.services import audio_narration

    def denied(*_: object) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(audio_narration, "publish_output", denied)
    with pytest.raises(ConfigurationError) as raised:
        narrate(
            text="hello",
            out_file=tmp_path / "v.wav",
            provider=provider,  # pyright: ignore[reportArgumentType]
            synthesizer=FakeSynthesizer(),
        )

    assert raised.value.charged == charged


def test_narrate_over_stale_zero_byte_file_proceeds_over_fresh_refuses_with_message(
    tmp_path: Path,
) -> None:
    stale_out = tmp_path / "stale.mp3"
    stale_out.touch()
    past = time.time() - 120.0
    os.utime(stale_out, (past, past))

    result = narrate(text="hello", out_file=stale_out, synthesizer=FakeSynthesizer())
    assert result.out_file == stale_out
    assert stale_out.read_bytes() == b"audio"

    fresh_out = tmp_path / "fresh.mp3"
    fresh_out.touch()
    with pytest.raises(
        ConfigurationError,
        match=(
            "it is empty and may be another run's in-progress publish or an "
            "interrupted one; retry after 60 s or delete it"
        ),
    ):
        narrate(text="hello", out_file=fresh_out, synthesizer=FakeSynthesizer())


def test_narrate_fresh_zero_byte_message_has_lowercase_output(tmp_path: Path) -> None:
    fresh_out = tmp_path / "fresh.mp3"
    fresh_out.touch()
    with pytest.raises(ConfigurationError) as exc_info:
        narrate(text="hello", out_file=fresh_out, synthesizer=FakeSynthesizer())
    assert "Narration output already exists: " in str(exc_info.value)
    assert "Narration Output already exists: " not in str(exc_info.value)


def test_narrate_provider_setup_failure_keeps_stale_leftover_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_out = tmp_path / "stale.mp3"
    stale_out.touch()
    past = time.time() - 120.0
    os.utime(stale_out, (past, past))

    def broken_synthesizer(*_args: object, **_kwargs: object) -> object:
        raise ConfigurationError("provider setup failed", charged="no")

    monkeypatch.setattr("gflow_cli.services.audio_narration._make_synthesizer", broken_synthesizer)

    with pytest.raises(ConfigurationError, match="provider setup failed"):
        narrate(text="hello", out_file=stale_out)

    assert stale_out.exists()


def test_narrate_succeeds_even_if_staging_cleanup_raises_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_file = tmp_path / "narration.mp3"
    real_unlink = Path.unlink

    def failing_staging_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self.name.startswith(f".{out_file.name}."):
            raise PermissionError("file locked by scanner")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_staging_unlink)

    result = narrate(text="hello", out_file=out_file, synthesizer=FakeSynthesizer())

    assert result.out_file == out_file
    assert out_file.read_bytes() == b"audio"


def test_narrate_stale_unlink_os_error_maps_to_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_out = tmp_path / "stale.mp3"
    stale_out.touch()
    past = time.time() - 120.0
    os.utime(stale_out, (past, past))

    def denied_rename(src: Path, dst: Path) -> None:
        raise OSError(errno.EPERM, "stale file locked by antivirus")

    monkeypatch.setattr(video_assembly, "_platform_no_replace_rename", denied_rename)

    with pytest.raises(ConfigurationError) as exc_info:
        narrate(text="hello", out_file=stale_out, synthesizer=FakeSynthesizer())

    assert exc_info.value.charged == "no"
    assert (
        "leftover could not be removed" in str(exc_info.value)
        or "could not remove stale output reservation" in str(exc_info.value).lower()
    )


def test_narrate_restore_failure_names_hidden_reservation_in_remediation_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "target.mp3"
    output.touch()
    past = time.time() - 120.0
    os.utime(output, (past, past))

    calls: list[tuple[Path, Path]] = []
    real_rename = video_assembly._platform_no_replace_rename

    def failing_restore(src: Path, dst: Path) -> None:
        calls.append((src, dst))
        if len(calls) == 1:
            real_rename(src, dst)
            dst.write_bytes(b"winner content")
        else:
            raise OSError(errno.EPERM, "restore permission denied")

    monkeypatch.setattr(video_assembly, "_platform_no_replace_rename", failing_restore)

    with pytest.raises(ConfigurationError) as exc_info:
        narrate(text="hello", out_file=output, synthesizer=FakeSynthesizer())

    assert exc_info.value.charged == "no"
    stale_path = calls[0][1]
    assert exc_info.value.remediation_hint is not None
    assert str(stale_path) in exc_info.value.remediation_hint
