from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_credits_single_uses_shared_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.mcp import tools

    expected = {"status": "ok", "profile": "demo", "credits": 5}

    async def fake(profile: str | None, *, refresh: bool = False) -> dict[str, object]:
        assert profile == "demo"
        assert refresh is False
        return expected

    monkeypatch.setattr(tools, "inspect_credit_profile", fake)
    monkeypatch.setattr(tools, "_resolve_and_validate_profile", lambda profile: "demo")
    assert await tools.gflow_get_credits(profile="demo") == expected


@pytest.mark.asyncio
async def test_get_credits_all_uses_shared_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.mcp import tools

    expected = {"status": "ok", "profiles": [], "total_credits": 0, "count": 0}

    async def fake(*, refresh: bool = False) -> dict[str, object]:
        assert refresh is False
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


@pytest.mark.asyncio
@pytest.mark.parametrize("all_profiles", [False, True])
async def test_get_credits_forwards_refresh(
    monkeypatch: pytest.MonkeyPatch, all_profiles: bool
) -> None:
    """MCP twin of `credits user|list --refresh`."""
    from gflow_cli.mcp import tools

    seen: list[bool] = []

    async def one(profile: str | None, *, refresh: bool = False) -> dict[str, object]:
        seen.append(refresh)
        return {"status": "ok"}

    async def many(*, refresh: bool = False) -> dict[str, object]:
        seen.append(refresh)
        return {"status": "ok"}

    monkeypatch.setattr(tools, "inspect_credit_profile", one)
    monkeypatch.setattr(tools, "inspect_all_credit_profiles", many)
    monkeypatch.setattr(tools, "_resolve_and_validate_profile", lambda profile: "demo")

    await tools.gflow_get_credits(all_profiles=all_profiles, refresh=True)

    assert seen == [True]
