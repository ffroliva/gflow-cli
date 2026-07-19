"""Unit tests for the shared ``resolve_and_apply`` helper and ``_model_str``.

``resolve_and_apply`` is the single block that the t2i / i2i / video CLI paths
and the worker daemon (image + video) all route through. These tests exercise
its branches directly (entity + media append, video path, --tool expansion, the
project guard, and the no-op passthrough) so the shared logic is covered once.
"""

from __future__ import annotations

from typing import Any

import pytest

from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.video import GenerateVideoRequest, VideoModel
from gflow_cli.errors import ConfigurationError
from gflow_cli.services import mentions
from gflow_cli.services.mentions import AssetIndex, _model_str, resolve_and_apply

_ENTITY = {
    "entityId": "e1-uuid-12345678901234567890123456",
    "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"},
}
_MEDIA = {"media_id": "m1-uuid-12345678901234567890123456", "display_name": "logo"}


@pytest.fixture
def patch_index(monkeypatch: pytest.MonkeyPatch) -> None:
    index = AssetIndex(entities=[_ENTITY], media_assets=[_MEDIA])

    async def _fake_build(cls: Any, client: Any, project_id: str) -> AssetIndex:
        return index

    monkeypatch.setattr(
        "gflow_cli.services.mentions.AssetIndex.build_for_project",
        classmethod(_fake_build),
    )


def test_model_str() -> None:
    assert _model_str(None) == ""
    assert _model_str("nano2") == "nano2"
    assert _model_str(VideoModel.VEO_3_1_FAST) == VideoModel.VEO_3_1_FAST.value


async def test_image_path_appends_entity_and_media(patch_index: None) -> None:
    req = GenerateImageRequest(prompt="A @Zoro holding the @logo")
    out = await resolve_and_apply(None, req, path="image", project_id="proj-1", tool_specs=())
    assert out.prompt == "A Zoro holding the logo"
    assert out.reference_entities == ("e1-uuid-12345678901234567890123456",)
    assert out.reference_entity_names == ("Zoro",)
    assert [r.name for r in out.refs] == ["m1-uuid-12345678901234567890123456"]


async def test_video_path_appends_entity_only(patch_index: None) -> None:
    req = GenerateVideoRequest(prompt="A @Zoro walks", model=VideoModel.VEO_3_1_FAST)
    out = await resolve_and_apply(None, req, path="video", project_id="proj-1", tool_specs=())
    assert out.prompt == "A Zoro walks"
    assert out.reference_entities == ("e1-uuid-12345678901234567890123456",)


async def test_missing_project_raises_when_mentions_present(patch_index: None) -> None:
    req = GenerateImageRequest(prompt="A @Zoro")
    with pytest.raises(ConfigurationError, match="explicit --project"):
        await resolve_and_apply(None, req, path="image", project_id=None, tool_specs=())


async def test_no_mentions_no_tools_is_passthrough(patch_index: None) -> None:
    req = GenerateImageRequest(prompt="plain prompt, no mentions")
    out = await resolve_and_apply(None, req, path="image", project_id=None, tool_specs=())
    assert out is req


async def test_tool_specs_rewrite_prompt(
    patch_index: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel_tool = object()

    def _fake_apply(
        text: str, specs: tuple[str, ...], *, category: str, quiet: bool
    ) -> tuple[str, str, Any]:
        assert category == "image"
        return f"EXPANDED::{text}", text, sentinel_tool

    monkeypatch.setattr("gflow_cli._cli_helpers.apply_tool_option", _fake_apply)

    req = GenerateImageRequest(prompt="A @Zoro")
    out = await resolve_and_apply(
        None, req, path="image", project_id="proj-1", tool_specs=("creative-director",)
    )
    # Mentions resolve first, then --tool rewrites the de-tagged prompt.
    assert out.prompt == "EXPANDED::A Zoro"
    assert out.original_prompt == "A Zoro"
    assert out.tool is sentinel_tool


def test_mentions_module_exports_helper() -> None:
    # Guard against an accidental rename breaking the 5 call sites.
    assert hasattr(mentions, "resolve_and_apply")
