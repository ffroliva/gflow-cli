"""Step bindings for image.feature.

Scoped to this feature only — pytest-bdd uses module-scoped step registries
(per-conftest `scenarios()` call) so step phrases here don't leak into auth
or video scenarios.

Patching strategy: replace ``gflow_cli.cli_image._run_t2i`` with async stubs
that either write fake .png files (success scenarios) or raise typed
:class:`GFlowError` subclasses (error scenarios). This matches the seam in
``tests/cli/test_error_handling.py`` and avoids needing the real
:class:`FlowApiClient`.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli import config
from gflow_cli.cli import main
from gflow_cli.errors import ContentPolicyError, WireFormatError

scenarios("image.feature")


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_result_holder() -> dict[str, Any]:
    return {"result": None}


@pytest.fixture
def batch_state() -> dict[str, Any]:
    return {"prompts": []}


@pytest.fixture(autouse=True)
def _patch_image_profile_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Bypass profile resolution + provider-dir existence checks so image
    commands reach the patched ``_run_t2i`` instead of bailing out with
    exit 2 during profile discovery."""
    monkeypatch.setattr("gflow_cli.cli_image._resolve_profile", lambda profile: "test")
    monkeypatch.setattr("gflow_cli.cli_image._make_provider_dir", lambda name: tmp_path)


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Generator[None, None, None]:
    config.reset_settings()
    yield
    config.reset_settings()


# ---------------------------------------------------------------------------
# Given steps
# ---------------------------------------------------------------------------


def _make_fake_t2i(file_count: int):
    """Build an async stub for ``_run_t2i`` that writes ``file_count`` fake
    PNG files into ``out`` (if provided) or ``output_root``."""

    async def _fake_t2i(
        *,
        profile_name: str,
        profile_dir: Path,
        headless: bool,
        req: Any,
        count: int,
        out: Path | None,
        output_root: Path,
        transport: str | None = None,
        project_id: str | None = None,
        project_name: str | None = None,
        as_json: bool = False,
        tool_specs: tuple[str, ...] = (),
        **kwargs: Any,
    ) -> None:
        # Honor ``count`` if the scenario set it; otherwise fall back to the
        # builder-bound ``file_count`` (lets us write exactly the right
        # number of files for "one image" vs. "4 images" scenarios).
        n = count if count > 0 else file_count
        base = out if out is not None else output_root / "images"
        base.mkdir(parents=True, exist_ok=True)
        for i in range(1, n + 1):
            (base / f"fake_{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    return _fake_t2i


@given("the mocked FlowApiClient returns a successful image")
def _mock_success_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """Single-image happy path. ``count`` will be 1 (CLI default), so the
    stub writes one .png."""
    monkeypatch.setattr("gflow_cli.cli_image._run_t2i", _make_fake_t2i(1))


@given("the mocked FlowApiClient returns successful images")
def _mock_success_images(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multi-image happy path. The CLI invocation will pass ``-n 4`` so
    ``count`` arrives as 4; the stub writes 4 .pngs."""
    monkeypatch.setattr("gflow_cli.cli_image._run_t2i", _make_fake_t2i(4))


@given("the mocked FlowApiClient raises ContentPolicyError")
def _mock_content_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _raise(*args: Any, **kwargs: Any) -> None:
        raise ContentPolicyError(detail="empty media[]")

    monkeypatch.setattr("gflow_cli.cli_image._run_t2i", _raise)


@given("the mocked FlowApiClient raises WireFormatError")
def _mock_wire_format(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _raise(*args: Any, **kwargs: Any) -> None:
        raise WireFormatError(detail="unknown shape", status=200)

    monkeypatch.setattr("gflow_cli.cli_image._run_t2i", _raise)


@given("the mocked t2i batch runner writes one image per prompt")
def _mock_t2i_batch_runner(monkeypatch: pytest.MonkeyPatch, batch_state: dict[str, Any]) -> None:
    async def _fake_batch(**kwargs: Any) -> list[Any]:
        from gflow_cli.image_batch import BatchOutcome

        prompts = list(kwargs["prompts"])
        batch_state["prompts"] = prompts
        output_dir = kwargs["output_dir"]
        output_dir.mkdir(parents=True, exist_ok=True)
        outcomes = []
        for prompt in prompts:
            path = output_dir / f"{prompt.output_filename}_0.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n")
            outcomes.append(
                BatchOutcome(
                    index=prompt.index,
                    prompt=prompt,
                    status="ok",
                    saved_paths=[path],
                    error=None,
                    exit_code=0,
                )
            )
        return outcomes

    monkeypatch.setattr("gflow_cli.cli_image.run_image_batch", _fake_batch)


@given("a prompt file with 3 valid prompts, 1 blank line, and 1 comment")
def _prompt_file_with_comments(tmp_path: Path) -> None:
    (tmp_path / "prompts.txt").write_text(
        "p1\n\n# skipped\np2\np3\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------


@when('I run "gflow image t2i a peaceful lake"')
def _run_t2i_lake(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(main, ["image", "t2i", "a peaceful lake"])


@when('I run "gflow image t2i mountains -n 4"')
def _run_t2i_mountains_n4(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(main, ["image", "t2i", "mountains", "-n", "4"])


@when('I run "gflow image t2i something rejected"')
def _run_t2i_rejected(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(main, ["image", "t2i", "something rejected"])


@when('I run "gflow image t2i wire-fail"')
def _run_t2i_wire_fail(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(main, ["image", "t2i", "wire-fail"])


@when('I run "gflow image t2i p1 p2 p3 --aspect 16:9 --model image4"')
def _run_t2i_multi_positional(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(
        main,
        ["image", "t2i", "p1", "p2", "p3", "--aspect", "16:9", "--model", "image4"],
    )


@when('I run "gflow image t2i --prompts-file prompts.txt"')
def _run_t2i_prompt_file(
    runner: CliRunner, cli_result_holder: dict[str, Any], tmp_path: Path
) -> None:
    cli_result_holder["result"] = runner.invoke(
        main,
        ["image", "t2i", "--prompts-file", str(tmp_path / "prompts.txt")],
    )


@when('I run "gflow image t2i p1 --prompts-file prompts.txt"')
def _run_t2i_multiple_sources(
    runner: CliRunner, cli_result_holder: dict[str, Any], tmp_path: Path
) -> None:
    cli_result_holder["result"] = runner.invoke(
        main,
        ["image", "t2i", "p1", "--prompts-file", str(tmp_path / "prompts.txt")],
    )


@when('I pipe 3 prompts into "gflow image t2i --stdin"')
def _run_t2i_stdin(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(
        main,
        ["image", "t2i", "--stdin"],
        input="p1\np2\np3\n",
    )


@when('I run "gflow image t2i" with 51 positional prompts')
def _run_t2i_51_prompts(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(
        main,
        ["image", "t2i", *[f"p{i}" for i in range(51)]],
    )


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------


@then("the exit code is 0")
def _check_exit_0(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == 0, result.output


@then("the exit code is 5")
def _check_exit_5(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == 5, result.output


@then("the exit code is 7")
def _check_exit_7(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == 7, result.output


@then("the exit code is 2")
def _check_exit_2(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == 2, result.output


@then("one image file is created")
def _check_one_image(tmp_path: Path) -> None:
    files = list(tmp_path.rglob("*.png"))
    assert len(files) >= 1, f"expected >=1 .png file under {tmp_path}, found {files}"


@then(parsers.parse("{n:d} image files are created"))
def _check_n_images(tmp_path: Path, n: int) -> None:
    files = list(tmp_path.rglob("*.png"))
    assert len(files) == n, f"expected {n} .png files, got {len(files)}: {files}"


@then('the output contains "content policy"')
def _check_content_policy_output(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "content policy" in result.output.lower()


@then('the output contains "File a bug"')
def _check_file_bug_output(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "File a bug" in result.output


@then('the output contains "mutually exclusive"')
def _check_mutually_exclusive_output(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "mutually exclusive" in result.output.lower()


@then('the output contains "between 1 and 50"')
def _check_between_1_and_50_output(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "between 1 and 50" in result.output


@then(parsers.parse('every batch prompt used aspect "{aspect}" and model "{model}"'))
def _check_batch_prompt_options(batch_state: dict[str, Any], aspect: str, model: str) -> None:
    prompts = batch_state["prompts"]
    assert prompts
    assert all(prompt.aspect_ratio == aspect for prompt in prompts)
    assert all(prompt.model == model for prompt in prompts)
