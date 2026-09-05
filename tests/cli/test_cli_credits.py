from __future__ import annotations

import json

from click.testing import CliRunner


def _one() -> dict[str, object]:
    return {
        "status": "ok",
        "profile": "demo",
        "is_default": True,
        "email": "demo@example.com",
        "authenticated": True,
        "credits": 5,
        "subscription_credits": 5,
        "user_paygate_tier": "PAYGATE_TIER_NOT_PAID",
        "service_tier": "SERVICE_TIER_ENTRY",
        "sku": "G1_FREEMIUM",
    }


def test_credits_user_json(monkeypatch) -> None:
    from gflow_cli import cli_credits
    from gflow_cli.cli import main

    async def fake(profile: str | None) -> dict[str, object]:
        assert profile == "demo"
        return _one()

    monkeypatch.setattr(cli_credits, "inspect_profile", fake)
    monkeypatch.setattr(cli_credits, "_resolve_profile", lambda profile: profile or "demo")
    monkeypatch.setattr(cli_credits, "_make_provider_dir", lambda profile: None)
    result = CliRunner().invoke(
        main, ["credits", "user", "--profile", "demo", "--json"], catch_exceptions=False
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == _one()


def test_credits_list_json(monkeypatch) -> None:
    from gflow_cli import cli_credits
    from gflow_cli.cli import main

    payload = {"status": "ok", "profiles": [_one()], "total_credits": 5, "count": 1}

    async def fake() -> dict[str, object]:
        return payload

    monkeypatch.setattr(cli_credits, "inspect_all_profiles", fake)
    result = CliRunner().invoke(main, ["credits", "list", "--json"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload


def test_credits_user_renders_human_output(monkeypatch) -> None:
    from gflow_cli import cli_credits
    from gflow_cli.cli import main

    async def fake(profile: str | None) -> dict[str, object]:
        assert profile == "demo"
        return _one()

    monkeypatch.setattr(cli_credits, "inspect_profile", fake)
    monkeypatch.setattr(cli_credits, "_resolve_profile", lambda profile: profile or "demo")
    monkeypatch.setattr(cli_credits, "_make_provider_dir", lambda profile: None)

    result = CliRunner().invoke(main, ["credits", "user", "--profile", "demo"])

    assert result.exit_code == 0, result.output
    assert "Profile: demo" in result.output
    assert "Google account: demo@example.com" in result.output
    assert "Credits: 5" in result.output
    assert "SKU: G1_FREEMIUM" in result.output


def test_credits_list_renders_human_output(monkeypatch) -> None:
    from gflow_cli import cli_credits
    from gflow_cli.cli import main

    payload = {"status": "ok", "profiles": [_one()], "total_credits": 5, "count": 1}

    async def fake() -> dict[str, object]:
        return payload

    monkeypatch.setattr(cli_credits, "inspect_all_profiles", fake)

    result = CliRunner().invoke(main, ["credits", "list"])

    assert result.exit_code == 0, result.output
    assert "demo" in result.output
    assert "demo@example.com" in result.output
    assert "G1_FREEMIUM" in result.output
    assert "Total credits: 5" in result.output


def test_credits_requires_an_explicit_subcommand() -> None:
    from gflow_cli.cli import main

    result = CliRunner().invoke(main, ["credits"])

    assert result.exit_code == 2
    assert "Show current Google Flow credit balances." in result.output
    assert "list" in result.output
    assert "user" in result.output


def test_credits_user_rejects_positional_profile() -> None:
    from gflow_cli.cli import main

    result = CliRunner().invoke(main, ["credits", "user", "demo"])

    assert result.exit_code == 2
    assert "unexpected extra argument" in result.output.lower()
