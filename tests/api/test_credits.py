from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from gflow_cli.api import routes
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import CreditsInfo
from gflow_cli.errors import WireFormatError


def test_credits_dto_parses_full_wire_shape() -> None:
    info = CreditsInfo.from_response(
        {
            "credits": 245,
            "subscriptionCredits": 300,
            "userPaygateTier": "PAYGATE_TIER_ONE",
            "serviceTier": "SERVICE_TIER_PREMIUM",
            "sku": "G1_PRO",
        }
    )
    assert info.credits == 245
    assert info.subscription_credits == 300
    assert info.user_paygate_tier == "PAYGATE_TIER_ONE"
    assert info.service_tier == "SERVICE_TIER_PREMIUM"
    assert info.sku == "G1_PRO"


@pytest.mark.parametrize("value", [True, "5", -1, None])
def test_credits_dto_rejects_invalid_balance(value: object) -> None:
    with pytest.raises(ValueError, match="credits"):
        CreditsInfo.from_response({"credits": value})


async def test_client_get_credits_uses_authenticated_get(tmp_path) -> None:
    client = FlowApiClient(profile_dir=tmp_path / "profile")
    client._get_json = AsyncMock(return_value={"credits": 5})  # type: ignore[method-assign]

    result = await client.get_credits()

    assert result == CreditsInfo(credits=5)
    client._get_json.assert_awaited_once_with(routes.CREDITS, route_name="credits")  # type: ignore[attr-defined]


async def test_client_get_credits_translates_shape_drift(tmp_path) -> None:
    client = FlowApiClient(profile_dir=tmp_path / "profile")
    client._get_json = AsyncMock(return_value={"remaining": 5})  # type: ignore[method-assign]

    with pytest.raises(WireFormatError, match="credits"):
        await client.get_credits()
