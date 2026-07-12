"""Configurable anti-bot jitter (issue #241).

``resolve_jitter_range`` parses the ``--jitter`` flag / ``GFLOW_CLI_JITTER_RANGE``
env var, and ``run_sequential_batch`` paces multi-prompt submissions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.dto import ProjectInfo
from gflow_cli.errors import ConfigurationError
from gflow_cli.image_batch import (
    JITTER_MAX_SECONDS,
    JITTER_MIN_SECONDS,
    BatchOutcome,
    resolve_jitter_range,
    run_sequential_batch,
)

# ---------------------------------------------------------------------------
# resolve_jitter_range
# ---------------------------------------------------------------------------


def test_default_when_no_flag_and_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GFLOW_CLI_JITTER_RANGE", raising=False)
    assert resolve_jitter_range(None) == (JITTER_MIN_SECONDS, JITTER_MAX_SECONDS)


def test_range_spec_parses_min_max() -> None:
    assert resolve_jitter_range("10-30") == (10.0, 30.0)


def test_decimal_range_spec() -> None:
    assert resolve_jitter_range("2.5-7.5") == (2.5, 7.5)


def test_single_value_means_zero_to_n() -> None:
    # Mirrors `gflow video chain --jitter N` semantics: uniform [0, N).
    assert resolve_jitter_range("5") == (0.0, 5.0)


def test_zero_disables() -> None:
    assert resolve_jitter_range("0") == (0.0, 0.0)


def test_env_var_used_when_no_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_JITTER_RANGE", "10-30")
    assert resolve_jitter_range(None) == (10.0, 30.0)


def test_flag_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_JITTER_RANGE", "10-30")
    assert resolve_jitter_range("1-2") == (1.0, 2.0)


@pytest.mark.parametrize("bad", ["abc", "30-10", "-5", "1-2-3", "", "3-", "-"])
def test_invalid_specs_raise_configuration_error(bad: str) -> None:
    with pytest.raises(ConfigurationError):
        resolve_jitter_range(bad)


def test_invalid_env_raises_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_JITTER_RANGE", "banana")
    with pytest.raises(ConfigurationError):
        resolve_jitter_range(None)


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
    import asyncio

    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def recording_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0)

    async def _go() -> None:
        import unittest.mock

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


def test_sequential_batch_default_none_never_sleeps() -> None:
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
    from unittest.mock import patch

    from click.testing import CliRunner

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


def test_t2i_jitter_flag_passes_range_to_batch_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GFLOW_CLI_JITTER_RANGE", raising=False)
    result, kwargs = _invoke_cli(
        ["image", "t2i", "p1", "p2", "--jitter", "1-2", "--out", str(tmp_path / "out")],
        tmp_path,
    )
    assert result.exit_code == 0, result.output  # type: ignore[union-attr]
    assert kwargs["jitter_range"] == (1.0, 2.0)


def test_t2i_defaults_to_3_7_jitter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GFLOW_CLI_JITTER_RANGE", raising=False)
    result, kwargs = _invoke_cli(
        ["image", "t2i", "p1", "p2", "--out", str(tmp_path / "out")],
        tmp_path,
    )
    assert result.exit_code == 0, result.output  # type: ignore[union-attr]
    assert kwargs["jitter_range"] == (JITTER_MIN_SECONDS, JITTER_MAX_SECONDS)


def test_t2i_env_var_sets_jitter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_JITTER_RANGE", "10-30")
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


def test_batch_jitter_flag_passes_range_to_manifest_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GFLOW_CLI_JITTER_RANGE", raising=False)
    manifest = tmp_path / "prompts.tsv"
    manifest.write_text("a prompt\n", encoding="utf-8")
    result, kwargs = _invoke_cli(
        ["image", "batch", str(manifest), "--jitter", "0", "--out", str(tmp_path / "out")],
        tmp_path,
    )
    assert result.exit_code == 0, result.output  # type: ignore[union-attr]
    assert kwargs["jitter_range"] == (0.0, 0.0)
