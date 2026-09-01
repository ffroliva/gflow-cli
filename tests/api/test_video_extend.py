"""Tests for `api/video_extend` — model resolution + extend request body.

Both units are pure: no Playwright, no network, no credits. The fixture is a
sanitised slice of a real `flow.projectInitialData` response captured on
2026-08-31 from a `SERVICE_TIER_INTERMEDIATE` account
(`docs/superpowers/spikes/2026-08-31-veo-extend-route-recon.md`).

The resolver exists because hardcoding an extend key is a proven bug: the
third-party CLI that prompted this work pins `veo_3_1_extend_fast_*_ultra`,
and every one of those reads `UNAVAILABLE` on a non-ADVANCED account. The key
MUST come from the server's own tier-aware `creditMapping`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gflow_cli.api.video_extend import (
    DEFAULT_FRAME_RANGE,
    ExtendVideoRequest,
    FrameRange,
    resolve_extend_model,
)
from gflow_cli.errors import ExtendUnavailableError

_FIXTURE = Path(__file__).parent / "fixtures" / "project_initial_data_extend_models.json"


@pytest.fixture
def listing() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- resolver


def test_resolves_the_key_flow_itself_sends(listing: dict) -> None:
    """INTERMEDIATE + landscape must yield exactly what Flow's own UI sent."""
    assert (
        resolve_extend_model(listing, service_tier="SERVICE_TIER_INTERMEDIATE", aspect="16:9")
        == "veo_3_1_extension_lite"
    )


def test_resolves_for_portrait_too(listing: dict) -> None:
    """`veo_3_1_extension_lite` is the only aspect-agnostic entry; portrait is
    the parable pipeline's primary case (`--aspect 9:16`)."""
    assert (
        resolve_extend_model(listing, service_tier="SERVICE_TIER_INTERMEDIATE", aspect="9:16")
        == "veo_3_1_extension_lite"
    )


def test_never_returns_an_ultra_key_on_intermediate(listing: dict) -> None:
    """The exact bug in the third-party map: `_ultra` is ADVANCED-only."""
    key = resolve_extend_model(listing, service_tier="SERVICE_TIER_INTERMEDIATE", aspect="16:9")
    assert not key.endswith("_ultra")


def test_skips_unavailable_costs(listing: dict) -> None:
    """A `cost: "UNAVAILABLE"` entry must never be selected on that tier."""
    for tier in ("SERVICE_TIER_INTERMEDIATE", "SERVICE_TIER_ENTRY", "SERVICE_TIER_ADVANCED"):
        key = resolve_extend_model(listing, service_tier=tier, aspect="16:9")
        entry = next(m for m in listing["videoModels"] if m["key"] == key)
        assert isinstance(entry["creditMapping"][tier]["cost"], int)


def test_advanced_prefers_standard_over_low_priority(listing: dict) -> None:
    """`_low_priority` costs 0 on ADVANCED but trades away queue position, and
    Flow's own UI does not pick it. Cheapest must not mean free-but-unbounded."""
    key = resolve_extend_model(listing, service_tier="SERVICE_TIER_ADVANCED", aspect="16:9")
    assert key == "veo_3_1_extension_lite"


def test_requires_the_extension_capability(listing: dict) -> None:
    """Control models in the fixture lack VIDEO_REQUIREMENT_EXTENSION and must
    never be returned, however cheap they are."""
    key = resolve_extend_model(listing, service_tier="SERVICE_TIER_INTERMEDIATE", aspect="16:9")
    entry = next(m for m in listing["videoModels"] if m["key"] == key)
    assert any("VIDEO_REQUIREMENT_EXTENSION" in reqs for reqs in entry["requirements"])


def test_raises_when_nothing_is_orderable(listing: dict) -> None:
    """Refuse loudly. Falling back to a hardcoded key is how the third-party
    CLI ships a request that always 403s."""
    with pytest.raises(ExtendUnavailableError):
        resolve_extend_model(listing, service_tier="SERVICE_TIER_NONEXISTENT", aspect="16:9")


def test_rejects_square_aspect(listing: dict) -> None:
    """No SQUARE key exists in either extend family."""
    with pytest.raises(ExtendUnavailableError):
        resolve_extend_model(listing, service_tier="SERVICE_TIER_INTERMEDIATE", aspect="1:1")


def test_picks_lowest_cost_among_orderable(listing: dict) -> None:
    """extension_lite (10) must win over extend_fast_* (20) and extend_* (100)."""
    tier = "SERVICE_TIER_INTERMEDIATE"
    key = resolve_extend_model(listing, service_tier=tier, aspect="16:9")
    chosen = next(m for m in listing["videoModels"] if m["key"] == key)
    orderable = [
        m
        for m in listing["videoModels"]
        if isinstance((m.get("creditMapping") or {}).get(tier, {}).get("cost"), int)
        and any("VIDEO_REQUIREMENT_EXTENSION" in r for r in m.get("requirements") or [])
    ]
    assert chosen["creditMapping"][tier]["cost"] == min(
        m["creditMapping"][tier]["cost"] for m in orderable
    )


# ---------------------------------------------------------------- body


def test_frame_range_default_is_one_second_at_24fps() -> None:
    """Captured value. The source clip is 24fps, so 1..24 is exactly 1.0s —
    not the whole 8s (192 frame) clip."""
    assert DEFAULT_FRAME_RANGE == FrameRange(start=1, end=24)


def test_to_wire_reproduces_the_captured_body() -> None:
    """Byte-shape parity with the request Flow's own UI emitted, which is the
    only body proven to return 200."""
    req = ExtendVideoRequest(
        media_id="11111111-1111-1111-1111-111111111111",
        project_id="22222222-2222-2222-2222-222222222222",
        scene_id="33333333-3333-3333-3333-333333333333",
        position=1,
        prompt="the wave recedes",
        model_key="veo_3_1_extension_lite",
        aspect="16:9",
        seed=2164,
    )
    body = req.to_wire(
        session_id=";1788200574949", token="TOK", batch_id="44444444-4444-4444-4444-444444444444"
    )

    assert body["useV2ModelConfig"] is True
    ctx = body["mediaGenerationContext"]
    assert ctx["batchId"] == "44444444-4444-4444-4444-444444444444"
    assert ctx["audioFailurePreference"] == "RETURN_SILENCED_VIDEOS"
    assert ctx["sceneContext"] == {
        "sceneId": "33333333-3333-3333-3333-333333333333",
        "position": 1,
    }

    client_ctx = body["clientContext"]
    assert client_ctx["tool"] == "PINHOLE"
    assert client_ctx["userPaygateTier"] == "PAYGATE_TIER_ONE"
    assert client_ctx["sessionId"] == ";1788200574949"
    assert client_ctx["recaptchaContext"] == {
        "token": "TOK",
        "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
    }

    (r,) = body["requests"]
    assert r["aspectRatio"] == "VIDEO_ASPECT_RATIO_LANDSCAPE"
    assert r["videoModelKey"] == "veo_3_1_extension_lite"
    assert r["seed"] == 2164
    assert r["metadata"] == {"sceneId": "33333333-3333-3333-3333-333333333333"}
    assert r["videoInput"] == {
        "mediaId": "11111111-1111-1111-1111-111111111111",
        "startFrameIndex": 1,
        "endFrameIndex": 24,
    }
    assert r["textInput"]["structuredPrompt"]["parts"] == [{"text": "the wave recedes"}]


def test_to_wire_maps_portrait_aspect() -> None:
    req = ExtendVideoRequest(
        media_id="11111111-1111-1111-1111-111111111111",
        project_id="22222222-2222-2222-2222-222222222222",
        scene_id="33333333-3333-3333-3333-333333333333",
        position=2,
        prompt="p",
        model_key="veo_3_1_extension_lite",
        aspect="9:16",
    )
    body = req.to_wire(session_id=";1", token="T", batch_id="b")
    assert body["requests"][0]["aspectRatio"] == "VIDEO_ASPECT_RATIO_PORTRAIT"


def test_request_rejects_malformed_ids() -> None:
    """Validate before minting — a bad id must not cost a reCAPTCHA token."""
    with pytest.raises(ValueError):
        ExtendVideoRequest(
            media_id="not-a-uuid",
            project_id="22222222-2222-2222-2222-222222222222",
            scene_id="33333333-3333-3333-3333-333333333333",
            position=1,
            prompt="p",
            model_key="veo_3_1_extension_lite",
            aspect="16:9",
        )


def test_request_rejects_empty_prompt() -> None:
    """`requirements` is [TEXT, EXTENSION] — text is mandatory on the wire."""
    with pytest.raises(ValueError):
        ExtendVideoRequest(
            media_id="11111111-1111-1111-1111-111111111111",
            project_id="22222222-2222-2222-2222-222222222222",
            scene_id="33333333-3333-3333-3333-333333333333",
            position=1,
            prompt="   ",
            model_key="veo_3_1_extension_lite",
            aspect="16:9",
        )
