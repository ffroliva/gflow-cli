"""Step bindings for the read-only credits command."""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner
from pytest_bdd import given, scenarios, then, when

from gflow_cli import cli_credits
from gflow_cli.cli import main

scenarios("credits.feature")


@pytest.fixture
def result_holder() -> dict[str, Any]:
    return {}


@given("a saved Flow profile with 12 credits")
def _saved_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    async def inspect(profile: str | None) -> dict[str, Any]:
        return {
            "status": "ok",
            "profile": profile,
            "authenticated": True,
            "credits": 12,
        }

    monkeypatch.setattr(cli_credits, "_resolve_profile", lambda profile: "demo")
    monkeypatch.setattr(cli_credits, "_make_provider_dir", lambda profile: None)
    monkeypatch.setattr(cli_credits, "inspect_profile", inspect)


@given("an all-profile balance response with one unavailable account")
def _partial_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    async def inspect() -> dict[str, Any]:
        return {
            "status": "partial",
            "profiles": [
                {"profile": "funded", "authenticated": True, "credits": 12},
                {"profile": "expired", "authenticated": False, "credits": None},
            ],
            "total_credits": 12,
            "count": 2,
        }

    monkeypatch.setattr(cli_credits, "inspect_all_profiles", inspect)


@when('I run "gflow credits user --json"')
def _run_user(result_holder: dict[str, Any]) -> None:
    result_holder["result"] = CliRunner().invoke(main, ["credits", "user", "--json"])


@when('I run "gflow credits list --json"')
def _run_list(result_holder: dict[str, Any]) -> None:
    result_holder["result"] = CliRunner().invoke(main, ["credits", "list", "--json"])


@then("the credits command exits successfully")
def _successful(result_holder: dict[str, Any]) -> None:
    result = result_holder["result"]
    assert result.exit_code == 0, result.output


@then("the credits JSON reports 12 credits")
def _single_balance(result_holder: dict[str, Any]) -> None:
    assert json.loads(result_holder["result"].output)["credits"] == 12


@then("the credits JSON reports a partial result totaling 12 credits")
def _partial_balance(result_holder: dict[str, Any]) -> None:
    payload = json.loads(result_holder["result"].output)
    assert payload["status"] == "partial"
    assert payload["total_credits"] == 12
