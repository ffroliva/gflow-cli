"""Step bindings for locale_picker_include.feature (issue #170).

Scoped to this feature only — pytest-bdd uses module-scoped step registries.

Patching strategy: replace ``gflow_cli.cli_image._run_t2i`` with async stubs
(same seam as test_image_steps.py). The selector-tier mechanics are unit-tested
in tests/api/transports/test_ui_automation_video.py; these scenarios pin the
CLI contract: entity pass-through, typed locale-neutral failure (exit 9), and
the submit backstop (exit 7).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pytest_bdd import given, scenarios, then, when

from gflow_cli import config
from gflow_cli.cli import main
from gflow_cli.errors import TransportTimeoutError, WireFormatError

scenarios("locale_picker_include.feature")


_T2I_CMD = [
    "image",
    "t2i",
    "a knight",
    "--project",
    "proj-1",
    "--reference-entity",
    "ent-123",
    "--reference-entity-name",
    "Lukas",
]


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
def runner_state() -> dict[str, Any]:
    return {"req": None}


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


@given("the mocked t2i runner records the request and writes one image")
def _mock_recording_runner(monkeypatch: pytest.MonkeyPatch, runner_state: dict[str, Any]) -> None:
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
        as_json: bool = False,
        original_prompt: str | None = None,
    ) -> None:
        runner_state["req"] = req
        base = out if out is not None else output_root / "images"
        base.mkdir(parents=True, exist_ok=True)
        (base / "fake_1.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setattr("gflow_cli.cli_image._run_t2i", _fake_t2i)


@given("the mocked t2i runner raises the include-action timeout")
def _mock_include_timeout_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """The transport's locale-neutral failure when no include-selector tier
    matched (issue #170: previously an untyped RuntimeError embedding the
    pt-BR caption, surfaced only as a privacy-hashed 'Unexpected error.')."""

    async def _fake_t2i(**_kwargs: Any) -> None:
        raise TransportTimeoutError(
            "character 'Lukas' (ent-123) include action did not appear "
            "in the right-click context menu",
            remediation_hint=(
                "Flow's context menu may have changed or your account language "
                "is not yet covered — open the menu manually and report its "
                "captions on https://github.com/ffroliva/gflow-cli/issues."
            ),
        )

    monkeypatch.setattr("gflow_cli.cli_image._run_t2i", _fake_t2i)


@given("the mocked t2i runner raises the reference-entity submit backstop")
def _mock_backstop_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """The image-side submit backstop: the staged entity never rode the wire,
    so the run must fail loudly instead of reporting a text-only success."""

    async def _fake_t2i(**_kwargs: Any) -> None:
        # Mirror the real raise site (ui_automation._assert_image_entities_attached):
        # since issue #174 it carries the library-UI drift hint + discovery payload.
        from gflow_cli.api.transports.ui_automation_video import ENTITY_ATTACH_DRIFT_HINT

        raise WireFormatError(
            "captured batchGenerateImages submit is missing referenceEntities ['ent-123']",
            route="flowMedia:batchGenerateImages",
            remediation_hint=ENTITY_ATTACH_DRIFT_HINT,
            discovery={"entity_attach_context": "image"},
        )

    monkeypatch.setattr("gflow_cli.cli_image._run_t2i", _fake_t2i)


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------


@when(
    'I run "gflow image t2i a knight --project proj-1 '
    '--reference-entity ent-123 --reference-entity-name Lukas"'
)
def _run_t2i_with_entity(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(main, _T2I_CMD)


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------


@then("the exit code is 0")
def _check_exit_0(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == 0, result.output


@then("the exit code is 9")
def _check_exit_9(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == 9, f"exit={result.exit_code}\n{result.output}"


@then("the exit code is 7")
def _check_exit_7(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == 7, f"exit={result.exit_code}\n{result.output}"


@then('the runner received reference entity "ent-123" named "Lukas"')
def _check_entity_passthrough(runner_state: dict[str, Any]) -> None:
    req = runner_state["req"]
    assert req is not None, "the t2i runner was never invoked"
    assert req.reference_entities == ("ent-123",)
    assert req.reference_entity_names == ("Lukas",)


@then("one image file is created")
def _check_one_image(tmp_path: Path) -> None:
    files = list(tmp_path.rglob("*.png"))
    assert len(files) == 1, f"expected 1 png, found {files}"


@then('the output contains "include action"')
def _check_output_include_action(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "include action" in result.output, result.output


@then('the output does not contain "Incluir no comando"')
def _check_output_locale_neutral(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "Incluir no comando" not in result.output, result.output


@then('the output contains "issues/174"')
def _check_output_issue_174(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "issues/174" in result.output, result.output
