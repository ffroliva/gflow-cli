# SPDX-License-Identifier: MIT
"""MCP reference-entity parity, asserted as a payload round trip (#689, #628).

`--reference-entity` / `--reference-entity-name` attach a saved Flow CHARACTER by
**id**. `docs/REFERENCE_STRATEGIES.md` calls this the "you have the id and want no name
ambiguity" route: `@Name` mentions reach the same wire (`referenceEntities`) and dedupe
against it, but display names are non-unique, which is the ambiguity the id form exists
to remove. Over MCP only the by-name route existed.

The interesting part is *where* it was missing. `worker/codec.py` already reads both
keys off the queue payload, and both request DTOs already carry them — the whole
downstream path was wired. Only the tool signature was absent, so the keys were never
written. That is the mirror image of #628: not a key written and never read, but a key
**read and never written**, and just as silent.

These tests therefore assert the round trip through the real tools, not the signature: a
tool that accepts the argument and drops it on the floor would satisfy a signature check
and still do nothing. Each captures the payload at the enqueue boundary and feeds it to
the same codec the worker uses.
"""

from __future__ import annotations

from typing import Any

import pytest

from gflow_cli.mcp import tools as mcp_tools
from gflow_cli.worker import codec

_ENTITY_A = "11111111-2222-3333-4444-555555555555"
_ENTITY_B = "66666666-7777-8888-9999-000000000000"


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Intercept the payload at the enqueue boundary; run no generation."""
    seen: dict[str, Any] = {}

    async def fake_run(
        *, profile: str, task_type: str, payload: dict[str, Any], wait: bool
    ) -> dict[str, Any]:
        seen["task_type"] = task_type
        seen["payload"] = payload
        return {"status": "queued", "task_id": "test"}

    async def always_allow() -> bool:
        return True

    monkeypatch.setattr(mcp_tools, "_run_generation_task", fake_run)
    monkeypatch.setattr(mcp_tools, "_resolve_and_validate_profile", lambda p: "testprofile")
    # The token bucket is module-level shared state. Without this, these tests drain it
    # and every later test in the session gets a `rate_limited` envelope instead of its
    # own result — a failure that looks like the code and is actually the fixture.
    monkeypatch.setattr(mcp_tools._rate_limiter, "acquire", always_allow)
    return seen


async def test_video_reference_entities_reach_the_worker_codec(captured: dict[str, Any]) -> None:
    """The id survives tool -> payload -> codec -> request without being dropped."""
    await mcp_tools.gflow_generate_video(
        prompt="a lone wolf on a ridge",
        mode="r2v",
        reference_entities=[_ENTITY_A, _ENTITY_B],
        reference_entity_names=["Aldous", "Mira"],
        wait=False,
    )
    payload = captured["payload"]
    assert payload["reference_entities"] == [_ENTITY_A, _ENTITY_B]

    req = codec.build_video_request(payload)
    assert req.reference_entities == (_ENTITY_A, _ENTITY_B)
    assert req.reference_entity_names == ("Aldous", "Mira")


async def test_video_omits_the_keys_when_unused(captured: dict[str, Any]) -> None:
    """An absent option must not write an empty key — the CLI omits, so MCP omits."""
    await mcp_tools.gflow_generate_video(prompt="a quiet street", wait=False)
    payload = captured["payload"]
    assert "reference_entities" not in payload
    assert "reference_entity_names" not in payload


async def test_image_reference_entities_reach_the_worker_codec(captured: dict[str, Any]) -> None:
    await mcp_tools.gflow_generate_image(
        prompt="a cliff at dawn",
        reference_entities=[_ENTITY_A],
        reference_entity_names=["Aldous"],
        wait=False,
    )
    payload = captured["payload"]
    assert payload["reference_entities"] == [_ENTITY_A]

    req = codec.build_image_request(payload)
    assert req.reference_entities == (_ENTITY_A,)
    assert req.reference_entity_names == ("Aldous",)


async def test_image_omits_the_keys_when_unused(captured: dict[str, Any]) -> None:
    await mcp_tools.gflow_generate_image(prompt="a cliff at dawn", wait=False)
    payload = captured["payload"]
    assert "reference_entities" not in payload
    assert "reference_entity_names" not in payload


@pytest.mark.parametrize("surface", ["video", "image"])
async def test_ids_alone_round_trip_without_paired_names(
    captured: dict[str, Any], surface: str
) -> None:
    """`--reference-entity` is usable without its paired name, exactly as on the CLI."""
    if surface == "video":
        await mcp_tools.gflow_generate_video(
            prompt="p", mode="r2v", reference_entities=[_ENTITY_A], wait=False
        )
        req: Any = codec.build_video_request(captured["payload"])
    else:
        await mcp_tools.gflow_generate_image(prompt="p", reference_entities=[_ENTITY_A], wait=False)
        req = codec.build_image_request(captured["payload"])
    assert req.reference_entities == (_ENTITY_A,)
    assert req.reference_entity_names == ()
