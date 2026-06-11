"""Step bindings for image_upscale.feature (issue #171).

Patching strategy mirrors test_image_steps.py: replace
``gflow_cli.cli_image._run_upscale`` with an async stub that writes a fake file
(success) or raises a typed error, and stub the catalog lookup +
profile-resolution shims so the command reaches the seam without touching a real
browser, DB, or profile store.
"""

from __future__ import annotations

import shlex
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli import config
from gflow_cli.cli import main
from gflow_cli.errors import UpscaleUnavailableError

scenarios("image_upscale.feature")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_result_holder() -> dict[str, Any]:
    return {"result": None}


@pytest.fixture(autouse=True)
def _patch_profile_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("gflow_cli.cli_image._resolve_profile", lambda profile: "test")
    monkeypatch.setattr("gflow_cli.cli_image._make_provider_dir", lambda name: tmp_path)
    # Default: catalog finds nothing (overridden by the "catalog resolves" step).
    monkeypatch.setattr(
        "gflow_cli.cli_image._lookup_project_in_catalog", lambda media, profile: None
    )


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Generator[None, None, None]:
    config.reset_settings()
    yield
    config.reset_settings()


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


def _fake_upscale_writes(tmp_path: Path):
    async def _run_upscale(**kwargs: Any) -> None:
        out_dir = kwargs.get("out_dir") or tmp_path
        media_id = kwargs["media_id"]
        scale_label = kwargs["scale_label"]
        target = Path(out_dir) / f"{media_id}_{scale_label}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

    return _run_upscale


@given("the mocked upscale writes a file")
def _given_upscale_writes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("gflow_cli.cli_image._run_upscale", _fake_upscale_writes(tmp_path))


@given("the catalog resolves the project")
def _given_catalog_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "gflow_cli.cli_image._lookup_project_in_catalog",
        lambda media, profile: "ffb768fb-cf2d-48b7-a135-92978667c37d",
    )


@given("the mocked upscale raises UpscaleUnavailableError")
def _given_upscale_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _raise(**_kwargs: Any) -> None:
        raise UpscaleUnavailableError(detail="4K requires Ultra", status=403)

    monkeypatch.setattr("gflow_cli.cli_image._run_upscale", _raise)


@given("the catalog has no record")
def _given_catalog_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "gflow_cli.cli_image._lookup_project_in_catalog", lambda media, profile: None
    )


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when(parsers.parse('I run "{command}"'))
def _when_run(command: str, runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    args = shlex.split(command)[1:]  # drop the leading "gflow"
    cli_result_holder["result"] = runner.invoke(main, args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then(parsers.parse("the exit code is {code:d}"))
def _then_exit_code(code: int, cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == code, result.output


@then("one upscaled file is created")
def _then_one_file(tmp_path: Path) -> None:
    files = list(tmp_path.rglob("*.jpg"))
    assert len(files) == 1, f"expected 1 upscaled file, found {files}"


@then(parsers.parse('the upscale output contains "{text}"'))
def _then_output_contains(text: str, cli_result_holder: dict[str, Any]) -> None:
    assert text in cli_result_holder["result"].output
