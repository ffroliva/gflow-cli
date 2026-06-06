"""Step bindings for character_read.feature.

Scoped to this feature only — pytest-bdd uses module-scoped step registries
(per-conftest ``scenarios()`` call) so step phrases here don't leak into other
features.

Patching strategy: replace ``gflow_cli.cli_character.FlowApiClient`` with a
typed async-CM stub whose ``get_character`` raises
:class:`~gflow_cli.errors.ConfigurationError` (ambiguous name). This mirrors
the unit test in ``tests/cli/test_cli_character.py`` and avoids any real
network calls. The ``_run_show`` seam passes kwargs so the stub signature
mirrors the runtime interface exactly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pytest_bdd import given, scenarios, then, when

from gflow_cli import cli_character
from gflow_cli.cli import main
from gflow_cli.errors import ConfigurationError

scenarios("character_read.feature")


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_result_holder() -> dict[str, Any]:
    # Fix 2: seed empty; "result" key is written before it is read.
    return {}


@pytest.fixture
def collision_ids() -> dict[str, list[str]]:
    """Holds the entity ids injected by the Given step so Then steps can verify them."""
    return {"ids": []}


# ---------------------------------------------------------------------------
# Given steps
# ---------------------------------------------------------------------------


@given('two characters named "Untitled Character" in the project')
def _two_colliding_characters(
    monkeypatch: pytest.MonkeyPatch,
    collision_ids: dict[str, list[str]],
) -> None:
    """Stub FlowApiClient so get_character raises ConfigurationError (ambiguous).

    We also bypass profile resolution and provider-dir so the CLI reaches the
    patched client instead of bailing during profile discovery (same pattern as
    ``tests/cli/test_cli_character.py::_patch``).
    """
    ids = ["eid-untitled-1", "eid-untitled-2"]
    collision_ids["ids"] = ids
    id_list = ", ".join(ids)
    exc = ConfigurationError(
        detail=(
            f"ambiguous character name 'Untitled Character' matches multiple entities: {id_list}"
        ),
        route="projectInitialData",
        remediation_hint="Use --id to disambiguate with --id and select one character.",
    )

    class _FakeClient:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_a: Any) -> bool:
            return False

        async def get_character(
            self,
            project_id: str,
            *,
            entity_id: str | None = None,
            name: str | None = None,
        ) -> None:
            raise exc

    monkeypatch.setattr(cli_character, "FlowApiClient", _FakeClient)
    monkeypatch.setattr(cli_character, "get_settings", lambda: type("S", (), {"headless": True})())
    monkeypatch.setattr(cli_character, "_resolve_profile", lambda p: p or "default")
    monkeypatch.setattr(
        cli_character,
        "_make_provider_dir",
        lambda name: Path("/fake"),
    )


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------


@when('I run "gflow character show --project proj-1 --name Untitled Character"')
def _run_character_show_collision(
    runner: CliRunner,
    cli_result_holder: dict[str, Any],
) -> None:
    cli_result_holder["result"] = runner.invoke(
        main,
        ["character", "show", "--project", "proj-1", "--name", "Untitled Character"],
    )


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------


@then("the exit code is 11")
def _check_exit_11(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    # Fix 1: assert the exception is SystemExit (not a raw ConfigurationError),
    # proving run_with_handlers caught the error, formatted it, and called
    # sys.exit(11) rather than letting the exception propagate as a crash/traceback.
    assert isinstance(result.exception, SystemExit), (
        f"expected SystemExit but got: {result.exception!r}\n{result.output}"
    )
    assert result.exit_code == 11, result.output
    # The production formatter (_handle_gflow_error) emits:
    #   "[red]{exc.title}:[/red] {exc.detail}"
    # so "Configuration error:" is a stable production-emitted token.
    assert "configuration error" in result.output.lower(), (
        f"expected 'Configuration error:' in output:\n{result.output}"
    )


@then("the output contains the colliding entity ids")
def _check_output_contains_ids(
    cli_result_holder: dict[str, Any],
    collision_ids: dict[str, list[str]],
) -> None:
    result = cli_result_holder["result"]
    for eid in collision_ids["ids"]:
        assert eid in result.output, f"expected entity id {eid!r} in output:\n{result.output}"


@then('the output contains "disambiguate"')
def _check_output_contains_disambiguate(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    # "disambiguate" originates from the stub's remediation_hint and is surfaced
    # verbatim by the production formatter (_handle_gflow_error:
    # `_console.print(f"[yellow]-> {exc.remediation_hint}[/yellow]")`).
    # There is no separate production-side word for this concept, so the hint
    # text itself is the stable assertion target.
    assert "disambiguate" in result.output.lower(), (
        f"expected 'disambiguate' in output:\n{result.output}"
    )
    # Also assert on the formatter's own prefix "-> " to confirm the hint was
    # rendered through the production path, not leaked raw.
    assert "-> " in result.output, f"expected remediation prefix '-> ' in output:\n{result.output}"
