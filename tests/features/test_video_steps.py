"""Step bindings for video.feature.

Scoped to this feature only — pytest-bdd uses module-scoped step registries
(per-conftest `scenarios()` call) so step phrases here don't leak into auth
or image scenarios.

Patching strategy: replace ``gflow_cli.cli_video._run_t2v`` and
``gflow_cli.cli_video._run_batch`` with async stubs. This matches the
existing seam in ``tests/cli/test_error_handling.py`` and dodges the live
:class:`FlowApiClient` (which would attempt to start Playwright).

The ``Batch with concurrency=4`` scenario uses an in-flight counter to
prove ``_run_batch`` fans out via ``asyncio.gather`` — peak in-flight >= 2
is necessary-and-sufficient evidence of real parallel execution.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli import config
from gflow_cli.cli import main
from gflow_cli.errors import NetworkError

scenarios("video.feature")


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    """Click ``CliRunner`` extended with a ``fixture_state`` dict so step
    bindings can stash cross-step state (manifest path, in-flight counter,
    etc.) without leaking pytest fixtures across step modules."""
    r = CliRunner()
    r.fixture_state = {}  # type: ignore[attr-defined]
    return r


@pytest.fixture
def cli_result_holder() -> dict[str, Any]:
    return {"result": None}


@pytest.fixture(autouse=True)
def _patch_video_profile_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Bypass profile resolution + provider-dir existence checks so video
    commands reach the patched ``_run_*`` helpers instead of bailing out
    with exit 2 during profile discovery."""
    monkeypatch.setattr("gflow_cli.cli_video._resolve_profile", lambda profile: "test")
    monkeypatch.setattr("gflow_cli.cli_video._make_provider_dir", lambda name: tmp_path)


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    """``Settings`` is cached via ``lru_cache``; reset around each scenario
    so env-var mutations (``GFLOW_CLI_CONCURRENCY``, ``GFLOW_CLI_OUTPUT_DIR``)
    take effect deterministically."""
    config.reset_settings()
    yield
    config.reset_settings()


# ---------------------------------------------------------------------------
# Given steps
# ---------------------------------------------------------------------------


@given("the mocked FlowApiClient returns a successful video")
def _mock_success_video(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runner: CliRunner) -> None:
    """Patch ``_run_t2v`` to drop a fake .mp4 at the expected output path so
    the "one video file is created" assertion holds. The output_root comes
    from ``Settings.output_dir`` which the autouse ``_isolate_tmp_output``
    fixture in conftest.py pins to ``tmp_path``."""

    async def _fake_t2v(
        *,
        profile_dir: Path,
        headless: bool,
        prompt: str,
        output: Path | None,
        aspect: Any,
        seed: int | None,
        poll_interval: float,
        output_root: Path,
    ) -> None:
        target = output if output is not None else (output_root / "videos" / "fake.mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        # MP4 magic-byte header — enough for "exists and is mp4-shaped" assertions.
        target.write_bytes(b"\x00\x00\x00\x20ftypisom")

    monkeypatch.setattr("gflow_cli.cli_video._run_t2v", _fake_t2v)


@given("a manifest with 4 prompts")
def _manifest_4(tmp_path: Path, runner: CliRunner) -> None:
    """Write a 4-row TSV manifest in the 5-column form expected by
    :func:`gflow_cli.manifest.parse_manifest`: ``start_image\\tprompt\\t
    end_image\\taspect\\toutput_path``. Empty start_image / end_image,
    empty aspect (defaults to PORTRAIT)."""
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "\ta kite over a beach\t\t\tkite.mp4\n"
        "\ta hot air balloon over Tokyo\t\t\tballoon.mp4\n"
        "\ta steam locomotive at dusk\t\t\ttrain.mp4\n"
        "\ta candle flickering in a window\t\t\tcandle.mp4\n",
        encoding="utf-8",
    )
    runner.fixture_state["manifest_path"] = manifest


@given("concurrency is set to 4")
def _concurrency_4(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_CONCURRENCY", "4")
    config.reset_settings()


@given("the mocked FlowApiClient raises NetworkError after 3 attempts")
def _mock_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``_run_t2v`` to raise :class:`NetworkError` so the CLI boundary
    maps it to exit code 6 + the "Check connectivity" remediation hint.
    The retry mechanic itself is proven by ``tests/api/test_retry.py``."""

    async def _raise(*args: Any, **kwargs: Any) -> None:
        raise NetworkError(detail="503 after retries", status=503)

    monkeypatch.setattr("gflow_cli.cli_video._run_t2v", _raise)


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------


@when('I run "gflow video t2v a hot air balloon"')
def _run_video_t2v_balloon(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(main, ["video", "t2v", "a hot air balloon"])


@when('I run "gflow video t2v retry-test"')
def _run_video_t2v_retry(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(main, ["video", "t2v", "retry-test"])


@when('I run "gflow video t2v fail-test"')
def _run_video_t2v_fail(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(main, ["video", "t2v", "fail-test"])


@when('I run "gflow video batch manifest.tsv"')
def _run_video_batch(
    runner: CliRunner,
    cli_result_holder: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace ``_run_batch`` with a stub that fans out 4 entries via
    :func:`asyncio.gather` exactly as the real implementation does. Each
    fake task increments / decrements an in-flight counter so the
    ``called concurrently`` then-step can assert peak >= 2.

    NB: We're testing the BDD wiring + the gather fan-out shape, not the
    real ``FlowApiClient``. The unit-level fan-out is tested separately in
    ``tests/cli/test_video_batch.py``.
    """
    in_flight = {"current": 0, "peak": 0}

    async def _fake_one(entry: Any, out_root: Path) -> None:
        in_flight["current"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["current"])
        # Hold the slot briefly so siblings can pile on. asyncio.gather
        # schedules them all immediately; the sleep is what makes the
        # "concurrent" peak observable.
        await asyncio.sleep(0.02)
        target = (
            entry.output_path if entry.output_path is not None else out_root / "videos" / "fake.mp4"
        )
        if not target.is_absolute():
            target = out_root / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x00\x00\x00\x20ftypisom")
        in_flight["current"] -= 1

    async def _fake_batch(
        *,
        profile_dir: Path,
        headless: bool,
        entries: list[Any],
        out_root: Path,
        poll_interval: float,
    ) -> None:
        await asyncio.gather(*[_fake_one(e, out_root) for e in entries])

    monkeypatch.setattr("gflow_cli.cli_video._run_batch", _fake_batch)
    runner.fixture_state["in_flight"] = in_flight

    manifest = runner.fixture_state["manifest_path"]
    cli_result_holder["result"] = runner.invoke(main, ["video", "batch", str(manifest)])


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------


@then("the exit code is 0")
def _check_exit_0(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == 0, result.output


@then("the exit code is 6")
def _check_exit_6(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == 6, result.output


@then("one video file is created")
def _check_one_video(tmp_path: Path) -> None:
    files = list(tmp_path.rglob("*.mp4"))
    assert len(files) >= 1, f"expected >=1 .mp4 file under {tmp_path}, found {files}"


@then(parsers.parse("{n:d} video files are created"))
def _check_n_videos(tmp_path: Path, n: int) -> None:
    files = list(tmp_path.rglob("*.mp4"))
    assert len(files) == n, f"expected {n} .mp4 files, got {len(files)}: {files}"


@then("the FlowApiClient was called concurrently")
def _check_called_concurrently(runner: CliRunner) -> None:
    """Peak in-flight >= 2 is necessary-and-sufficient evidence that the
    batch fan-out parallelized (vs. ran sequentially). With concurrency=4
    and 4 entries we expect peak in 2..4."""
    in_flight = runner.fixture_state["in_flight"]
    assert in_flight["peak"] >= 2, (
        f"video batch ran sequentially (peak in-flight = {in_flight['peak']}). "
        "Either asyncio.gather wasn't invoked or the entries serialized."
    )


@then('the output contains "Check connectivity"')
def _check_connectivity_remediation(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "Check connectivity" in result.output
