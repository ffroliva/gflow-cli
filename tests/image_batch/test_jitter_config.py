"""Configurable anti-bot jitter (issue #241).

``parse_jitter_range`` (config) parses the spec; ``resolve_jitter_range``
applies flag > settings (env/.env) > small-default precedence; and
``run_sequential_batch`` paces multi-prompt submissions.
"""

from __future__ import annotations

import asyncio
import unittest.mock
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gflow_cli.api.dto import ProjectInfo
from gflow_cli.config import Settings, parse_jitter_range, reset_settings
from gflow_cli.errors import ConfigurationError
from gflow_cli.image_batch import (
    JITTER_MAX_SECONDS,
    JITTER_MIN_SECONDS,
    BatchOutcome,
    resolve_jitter_range,
    run_sequential_batch,
)


@pytest.fixture(autouse=True)
def _clean_jitter_env(monkeypatch: pytest.MonkeyPatch):
    """Isolate each test from ambient env and the cached Settings singleton."""
    monkeypatch.delenv("GFLOW_CLI_JITTER_RANGE", raising=False)
    reset_settings()
    yield
    reset_settings()


# ---------------------------------------------------------------------------
# parse_jitter_range / resolve_jitter_range
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("10-30", (10.0, 30.0)),
        ("2.5-7.5", (2.5, 7.5)),
        # Single value mirrors `gflow video chain --jitter N`: uniform [0, N).
        ("5", (0.0, 5.0)),
        ("0", (0.0, 0.0)),
    ],
)
def test_valid_specs_parse(spec: str, expected: tuple[float, float]) -> None:
    assert parse_jitter_range(spec) == expected
    assert resolve_jitter_range(spec) == expected


@pytest.mark.parametrize(
    "bad",
    ["abc", "30-10", "-5", "1-2-3", "", "3-", "-", "5-inf", "nan", "5000", "10-99999"],
)
def test_invalid_specs_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="[Ii]nvalid jitter"):
        parse_jitter_range(bad)
    with pytest.raises(ConfigurationError):
        resolve_jitter_range(bad)


def test_default_when_no_flag_and_no_env() -> None:
    assert resolve_jitter_range(None) == (JITTER_MIN_SECONDS, JITTER_MAX_SECONDS)


def test_env_var_used_when_no_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_JITTER_RANGE", "10-30")
    reset_settings()
    assert resolve_jitter_range(None) == (10.0, 30.0)


def test_flag_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_JITTER_RANGE", "10-30")
    reset_settings()
    assert resolve_jitter_range("1-2") == (1.0, 2.0)


def test_invalid_env_fails_at_settings_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_JITTER_RANGE", "banana")
    with pytest.raises(Exception, match="[Ii]nvalid jitter"):
        Settings(_env_file=None)


def test_empty_env_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_JITTER_RANGE", "")
    reset_settings()
    assert resolve_jitter_range(None) == (JITTER_MIN_SECONDS, JITTER_MAX_SECONDS)


# ---------------------------------------------------------------------------
# run_sequential_batch pacing
# ---------------------------------------------------------------------------


class _FakeClient:
    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def create_project(self, *, title: str) -> ProjectInfo:
        return ProjectInfo(project_id="proj-1", title=title)


def _fake_factory(**_kwargs: object) -> _FakeClient:
    return _FakeClient()


async def _ok_worker(_client: object, _project_id: str, idx: int, item: object) -> BatchOutcome:
    return BatchOutcome(index=idx, prompt=item, status="ok")


def _run_batch(jitter_range: tuple[float, float] | None, n_items: int = 3) -> list[float]:
    """Run a fake batch, returning the recorded sleep durations."""
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def recording_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0)

    async def _go() -> None:
        with unittest.mock.patch("gflow_cli.image_batch.asyncio.sleep", recording_sleep):
            await run_sequential_batch(
                profile_dir=Path("unused"),
                headless=True,
                transport=None,
                items=tuple(f"p{i}" for i in range(n_items)),
                continue_on_error=True,
                project_title="t",
                worker=_ok_worker,
                client_factory=_fake_factory,
                jitter_range=jitter_range,
            )

    asyncio.run(_go())
    return sleeps


def test_sequential_batch_sleeps_between_prompts_not_before_first() -> None:
    sleeps = _run_batch((0.5, 0.5), n_items=3)
    assert sleeps == [0.5, 0.5]


def test_sequential_batch_zero_range_never_sleeps() -> None:
    assert _run_batch((0.0, 0.0), n_items=3) == []


def test_sequential_batch_none_resolves_configured_default() -> None:
    # None = "use the configured range" — the small built-in default here.
    # This is the contract that keeps every caller (incl. `gflow run`) paced.
    sleeps = _run_batch(None, n_items=3)
    assert len(sleeps) == 2
    assert all(JITTER_MIN_SECONDS <= s <= JITTER_MAX_SECONDS for s in sleeps)


def test_sequential_batch_none_honours_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_JITTER_RANGE", "0")
    reset_settings()
    assert _run_batch(None, n_items=3) == []


def test_sequential_batch_sleep_within_range() -> None:
    sleeps = _run_batch((0.1, 0.9), n_items=2)
    assert len(sleeps) == 1
    assert 0.1 <= sleeps[0] <= 0.9


# ---------------------------------------------------------------------------
# CLI --jitter wiring
# ---------------------------------------------------------------------------


def _invoke_cli(args: list[str], tmp_path: Path) -> tuple[object, dict[str, object]]:
    """Invoke the CLI with the batch runners patched; return (result, runner kwargs)."""
    from gflow_cli.cli import main

    async def _fake_run(**_kwargs: object) -> list[object]:
        return []

    captured: dict[str, object] = {}
    with (
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "profile"),
        patch("gflow_cli.cli_image.run_image_batch", side_effect=_fake_run) as run_t2i,
        patch(
            "gflow_cli.cli_image.run_manifest_image_batch", side_effect=_fake_run
        ) as run_manifest,
        patch("gflow_cli.cli_image.render_image_batch_summary", return_value=0),
    ):
        result = CliRunner().invoke(main, args, catch_exceptions=False)
    for mock in (run_t2i, run_manifest):
        if mock.call_args is not None:
            captured.update(mock.call_args.kwargs)
    return result, captured


def test_t2i_jitter_flag_passes_range_to_batch_runner(tmp_path: Path) -> None:
    result, kwargs = _invoke_cli(
        ["image", "t2i", "p1", "p2", "--jitter", "1-2", "--out", str(tmp_path / "out")],
        tmp_path,
    )
    assert result.exit_code == 0, result.output  # type: ignore[union-attr]
    assert kwargs["jitter_range"] == (1.0, 2.0)


def test_t2i_defaults_to_small_jitter(tmp_path: Path) -> None:
    result, kwargs = _invoke_cli(
        ["image", "t2i", "p1", "p2", "--out", str(tmp_path / "out")],
        tmp_path,
    )
    assert result.exit_code == 0, result.output  # type: ignore[union-attr]
    assert kwargs["jitter_range"] == (JITTER_MIN_SECONDS, JITTER_MAX_SECONDS)


def test_t2i_env_var_sets_jitter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_JITTER_RANGE", "10-30")
    reset_settings()
    result, kwargs = _invoke_cli(
        ["image", "t2i", "p1", "p2", "--out", str(tmp_path / "out")],
        tmp_path,
    )
    assert result.exit_code == 0, result.output  # type: ignore[union-attr]
    assert kwargs["jitter_range"] == (10.0, 30.0)


def test_t2i_invalid_jitter_flag_is_usage_error(tmp_path: Path) -> None:
    result, kwargs = _invoke_cli(
        ["image", "t2i", "p1", "p2", "--jitter", "banana", "--out", str(tmp_path / "out")],
        tmp_path,
    )
    assert result.exit_code == 2  # type: ignore[union-attr]
    assert kwargs == {}


def test_batch_jitter_flag_passes_range_to_manifest_runner(tmp_path: Path) -> None:
    manifest = tmp_path / "prompts.tsv"
    manifest.write_text("a prompt\n", encoding="utf-8")
    result, kwargs = _invoke_cli(
        ["image", "batch", str(manifest), "--jitter", "0", "--out", str(tmp_path / "out")],
        tmp_path,
    )
    assert result.exit_code == 0, result.output  # type: ignore[union-attr]
    assert kwargs["jitter_range"] == (0.0, 0.0)
