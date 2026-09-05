from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_credits_single_uses_shared_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.mcp import tools

    expected = {"status": "ok", "profile": "demo", "credits": 5}

    async def fake(profile: str | None) -> dict[str, object]:
        assert profile == "demo"
        return expected

    monkeypatch.setattr(tools, "inspect_credit_profile", fake)
    monkeypatch.setattr(tools, "_resolve_and_validate_profile", lambda profile: "demo")
    assert await tools.gflow_get_credits(profile="demo") == expected


@pytest.mark.asyncio
async def test_get_credits_all_uses_shared_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.mcp import tools

    expected = {"status": "ok", "profiles": [], "total_credits": 0, "count": 0}

    async def fake() -> dict[str, object]:
        return expected

    monkeypatch.setattr(tools, "inspect_all_credit_profiles", fake)
    assert await tools.gflow_get_credits(all_profiles=True) == expected


@pytest.mark.asyncio
async def test_get_credits_rejects_missing_profile_before_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.mcp import tools

    error = {"status": "error", "error": {"title": "No Profile Found"}}
    monkeypatch.setattr(tools, "_resolve_and_validate_profile", lambda profile: error)

    assert await tools.gflow_get_credits() == error
