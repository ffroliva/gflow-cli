"""Tests for `FlowApiClient.extend_video` — the direct-wire extend transport.

`fetch_project_listing`, `_mint_recaptcha_token` and `_post_json` are all
monkey-patched, so these need no Playwright, no reCAPTCHA and no credits.

The behaviours pinned here are the ones the predict council flagged as the
difference between a safe implementation and an expensive one: resolve before
minting, validate before minting, cache the capability listing, and refuse
rather than send a key the account cannot order.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from gflow_cli.api.client import FlowApiClient
from gflow_cli.errors import ExtendUnavailableError

_FIXTURE = Path(__file__).parent / "fixtures" / "project_initial_data_extend_models.json"

MEDIA = "b9458021-fc2d-4d95-ab53-cf844c6f1079"
PROJECT = "7d3d6bd9-a39f-4c2d-b772-146e73e539cf"
SCENE = "d7d1cc78-7a31-4924-a4aa-0669141a1ed8"
NEW_MEDIA = "37930141-ee54-4fe2-9f60-9eb959ca11ff"


def _listing() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _generate_response() -> dict[str, Any]:
    """The captured 200 body, trimmed."""
    return {
        "remainingCredits": 1015,
        "workflows": [{"name": "c83c6aa6-be52-4b67-8eed-dd753f381854"}],
        "media": [{"name": NEW_MEDIA, "workflowId": "c83c6aa6-be52-4b67-8eed-dd753f381854"}],
    }


def _client(
    tmp_path: Path,
    *,
    listing: dict[str, Any] | None = None,
    post_return: Any = None,
    post_side_effect: Any = None,
) -> FlowApiClient:
    c = FlowApiClient(profile_dir=tmp_path / "prof")
    c.fetch_project_listing = AsyncMock(  # type: ignore[method-assign]
        return_value=listing if listing is not None else _listing()
    )
    c._mint_recaptcha_token = AsyncMock(return_value="tok-abc")  # type: ignore[method-assign]
    c._post_json = AsyncMock(  # type: ignore[method-assign]
        return_value=post_return if post_return is not None else _generate_response(),
        side_effect=post_side_effect,
    )
    return c


@pytest.mark.asyncio
async def test_happy_path_returns_new_media(tmp_path: Path) -> None:
    c = _client(tmp_path)
    started = await c.extend_video(
        media_id=MEDIA, project_id=PROJECT, scene_id=SCENE, position=1, prompt="the wave recedes"
    )
    assert started.media_id == NEW_MEDIA
    assert started.model_key == "veo_3_1_extension_lite"
    assert started.remaining_credits == 1015


@pytest.mark.asyncio
async def test_sends_the_resolved_key_not_a_pinned_one(tmp_path: Path) -> None:
    c = _client(tmp_path)
    await c.extend_video(media_id=MEDIA, project_id=PROJECT, scene_id=SCENE, position=1, prompt="p")
    body = c._post_json.await_args[0][1]  # type: ignore[attr-defined]
    assert body["requests"][0]["videoModelKey"] == "veo_3_1_extension_lite"
    assert body["clientContext"]["projectId"] == PROJECT
    assert body["mediaGenerationContext"]["sceneContext"] == {"sceneId": SCENE, "position": 1}


@pytest.mark.asyncio
async def test_validates_before_minting(tmp_path: Path) -> None:
    """A malformed id must not cost a reCAPTCHA token — tokens are single-use,
    ~2 min TTL, and minting is itself a scored action."""
    c = _client(tmp_path)
    with pytest.raises(ValueError):
        await c.extend_video(
            media_id="not-a-uuid", project_id=PROJECT, scene_id=SCENE, position=1, prompt="p"
        )
    c._mint_recaptcha_token.assert_not_awaited()  # type: ignore[attr-defined]
    c._post_json.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_refuses_when_no_model_is_orderable(tmp_path: Path) -> None:
    """Never fall back to a pinned key: one the account cannot order 403s every
    time, which is exactly how the third-party CLI fails."""
    c = _client(tmp_path, listing={"result": {"data": {"json": {"modelConfig": {}}}}})
    with pytest.raises(ExtendUnavailableError):
        await c.extend_video(
            media_id=MEDIA, project_id=PROJECT, scene_id=SCENE, position=1, prompt="p"
        )
    c._mint_recaptcha_token.assert_not_awaited()  # type: ignore[attr-defined]
    c._post_json.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_rejects_square_aspect_before_any_call(tmp_path: Path) -> None:
    c = _client(tmp_path)
    with pytest.raises(ExtendUnavailableError):
        await c.extend_video(
            media_id=MEDIA,
            project_id=PROJECT,
            scene_id=SCENE,
            position=1,
            prompt="p",
            aspect="1:1",
        )
    c._mint_recaptcha_token.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_caches_the_capability_listing(tmp_path: Path) -> None:
    """It cannot change mid-run. Uncached at N=15 this is 15 extra requests at a
    WAF-scored host for a constant."""
    c = _client(tmp_path)
    for pos in (1, 2, 3):
        await c.extend_video(
            media_id=MEDIA, project_id=PROJECT, scene_id=SCENE, position=pos, prompt="p"
        )
    assert c.fetch_project_listing.await_count == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_mints_after_resolving(tmp_path: Path) -> None:
    """Ordering matters: the token has a ~2 min TTL, so it must be the last
    thing acquired before the POST, never before a slow capability fetch."""
    order: list[str] = []

    c = _client(tmp_path)

    async def _listing_call(*_a: Any, **_k: Any) -> dict[str, Any]:
        order.append("listing")
        return _listing()

    async def _mint(*_a: Any, **_k: Any) -> str:
        order.append("mint")
        return "tok"

    async def _post(*_a: Any, **_k: Any) -> dict[str, Any]:
        order.append("post")
        return _generate_response()

    c.fetch_project_listing = _listing_call  # type: ignore[method-assign]
    c._mint_recaptcha_token = _mint  # type: ignore[method-assign]
    c._post_json = _post  # type: ignore[method-assign]

    await c.extend_video(media_id=MEDIA, project_id=PROJECT, scene_id=SCENE, position=1, prompt="p")
    assert order == ["listing", "mint", "post"]


@pytest.mark.asyncio
async def test_logs_the_resolved_model(tmp_path: Path) -> None:
    """When Flow moves the family again, this one line is the diagnosis."""
    c = _client(tmp_path)
    with capture_logs() as logs:
        await c.extend_video(
            media_id=MEDIA, project_id=PROJECT, scene_id=SCENE, position=1, prompt="p"
        )
    resolved = [e for e in logs if e.get("event") == "extend_model_resolved"]
    assert resolved, [e.get("event") for e in logs]
    assert resolved[0]["model_key"] == "veo_3_1_extension_lite"
    assert resolved[0]["service_tier"] == "SERVICE_TIER_INTERMEDIATE"
    assert resolved[0]["unit_cost"] == 10


@pytest.mark.asyncio
async def test_never_logs_the_recaptcha_token(tmp_path: Path) -> None:
    c = _client(tmp_path)
    with capture_logs() as logs:
        await c.extend_video(
            media_id=MEDIA, project_id=PROJECT, scene_id=SCENE, position=1, prompt="p"
        )
    assert "tok-abc" not in json.dumps(logs)
