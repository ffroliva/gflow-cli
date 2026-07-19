from __future__ import annotations

from types import SimpleNamespace

import pytest

from gflow_cli.errors import ConfigurationError
from gflow_cli.services.mentions import (
    AssetIndex,
    parse_mentions,
    resolve_mentions,
)


def _character(entity_id: str, name: str, workflow_ids: tuple[str, ...]) -> SimpleNamespace:
    """A minimal Character-shaped stub (entity_id/display_name/workflow_ids) —
    the shape AssetIndex reads from client.list_characters."""
    return SimpleNamespace(entity_id=entity_id, display_name=name, workflow_ids=workflow_ids)


def test_resolve_entity_without_reference_images_fails_early() -> None:
    # A bare, image-less character (empty workflow_ids) cannot stage as a
    # referenceEntity — resolution must fail here, not deep in the UI attach.
    index = AssetIndex(
        entities=[_character("e1-uuid-12345678901234567890123456", "Zoro", ())],
        media_assets=[],
    )
    tokens = parse_mentions("Hello @Zoro walks")
    with pytest.raises(ConfigurationError, match="no reference images"):
        resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @Zoro walks")


def test_resolve_entity_with_reference_images_ok() -> None:
    # Same entity but WITH a reference image (non-empty workflow_ids) resolves.
    index = AssetIndex(
        entities=[_character("e1-uuid-12345678901234567890123456", "Zoro", ("wf-1",))],
        media_assets=[],
    )
    tokens = parse_mentions("Hello @Zoro walks")
    res = resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @Zoro walks")
    assert len(res.mentions) == 1
    assert res.mentions[0].id == "e1-uuid-12345678901234567890123456"
    assert res.de_tagged_prompt == "Hello Zoro walks"


def test_resolve_empty_mentions() -> None:
    index = AssetIndex(entities=[], media_assets=[])
    tokens = parse_mentions("Hello world")
    res = resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello world")
    assert len(res.mentions) == 0
    assert res.de_tagged_prompt == "Hello world"


def test_resolve_unique_entity() -> None:
    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"},
        }
    ]
    index = AssetIndex(entities=entities, media_assets=[])
    tokens = parse_mentions("Hello @Zoro walks")
    res = resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @Zoro walks")
    assert len(res.mentions) == 1
    assert res.mentions[0].name == "Zoro"
    assert res.mentions[0].kind == "entity"
    assert res.mentions[0].id == "e1-uuid-12345678901234567890123456"
    assert res.de_tagged_prompt == "Hello Zoro walks"


def test_resolve_unique_media() -> None:
    media = [{"media_id": "m1-uuid-12345678901234567890123456", "display_name": "logo"}]
    index = AssetIndex(entities=[], media_assets=media)
    tokens = parse_mentions("Hello @logo on tee")
    res = resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @logo on tee")
    assert len(res.mentions) == 1
    assert res.mentions[0].name == "logo"
    assert res.mentions[0].kind == "media"
    assert res.mentions[0].id == "m1-uuid-12345678901234567890123456"
    assert res.de_tagged_prompt == "Hello logo on tee"


def test_resolve_greedy_longest_match() -> None:
    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Captain", "entityType": "CHARACTER"},
        },
        {
            "entityId": "e2-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Captain Zoro", "entityType": "CHARACTER"},
        },
    ]
    index = AssetIndex(entities=entities, media_assets=[])
    tokens = parse_mentions("Hello @Captain Zoro walks")
    res = resolve_mentions(
        tokens, index, path="image", model="nano2", prompt="Hello @Captain Zoro walks"
    )
    assert len(res.mentions) == 1
    assert res.mentions[0].name == "Captain Zoro"
    assert res.mentions[0].id == "e2-uuid-12345678901234567890123456"
    assert res.de_tagged_prompt == "Hello Captain Zoro walks"

    # Greedy match falls back to word prefix if longest doesn't match
    tokens = parse_mentions("Hello @Captain Nemo walks")
    res = resolve_mentions(
        tokens, index, path="image", model="nano2", prompt="Hello @Captain Nemo walks"
    )
    assert len(res.mentions) == 1
    assert res.mentions[0].name == "Captain"
    assert res.mentions[0].id == "e1-uuid-12345678901234567890123456"
    assert res.de_tagged_prompt == "Hello Captain Nemo walks"


def test_resolve_entity_shadows_media() -> None:
    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"},
        }
    ]
    media = [{"media_id": "m1-uuid-12345678901234567890123456", "display_name": "Zoro"}]
    index = AssetIndex(entities=entities, media_assets=media)
    tokens = parse_mentions("Hello @Zoro")
    res = resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @Zoro")
    assert len(res.mentions) == 1
    assert res.mentions[0].name == "Zoro"
    assert res.mentions[0].kind == "entity"
    assert res.mentions[0].id == "e1-uuid-12345678901234567890123456"
    assert res.mentions[0].shadowed == "m1-uuid-12345678901234567890123456"


def test_resolve_ambiguous_entity() -> None:
    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"},
        },
        {
            "entityId": "e2-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"},
        },
    ]
    index = AssetIndex(entities=entities, media_assets=[])
    tokens = parse_mentions("Hello @Zoro")
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @Zoro")
    assert "Ambiguous mention" in str(exc_info.value)
    assert "e1-uuid-1234" in str(exc_info.value)
    assert "e2-uuid-1234" in str(exc_info.value)


def test_resolve_unknown() -> None:
    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"},
        }
    ]
    index = AssetIndex(entities=entities, media_assets=[])
    tokens = parse_mentions("Hello @nonexistent")
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @nonexistent")
    assert "Unknown mention" in str(exc_info.value)
    assert "Available assets: Zoro" in str(exc_info.value)


def test_resolve_me_refusal() -> None:
    index = AssetIndex(entities=[], media_assets=[])
    tokens = parse_mentions("Hello @me")
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @me")
    assert "avatar likeness is region-gated" in str(exc_info.value)


def test_resolve_video_media_refusal() -> None:
    media = [{"media_id": "m1-uuid-12345678901234567890123456", "display_name": "logo"}]
    index = AssetIndex(entities=[], media_assets=media)
    tokens = parse_mentions("Hello @logo")
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_mentions(tokens, index, path="video", model="veo2-blue", prompt="Hello @logo")
    assert "media mentions on the video path are Phase 3" in str(exc_info.value)


def test_resolve_cap_breach() -> None:
    entities = [
        {
            "entityId": f"e{i}-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": f"Zoro{i}", "entityType": "CHARACTER"},
        }
        for i in range(5)
    ]
    index = AssetIndex(entities=entities, media_assets=[])
    tokens = parse_mentions("Hello @Zoro0 @Zoro1 @Zoro2 @Zoro3")
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_mentions(
            tokens, index, path="image", model="imagen4", prompt="Hello @Zoro0 @Zoro1 @Zoro2 @Zoro3"
        )
    assert "reference cap" in str(exc_info.value)


def test_resolve_deduplication() -> None:
    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro", "entityType": "CHARACTER"},
        }
    ]
    index = AssetIndex(entities=entities, media_assets=[])
    tokens = parse_mentions("Hello @Zoro and @Zoro")
    res = resolve_mentions(
        tokens, index, path="image", model="nano2", prompt="Hello @Zoro and @Zoro"
    )
    assert len(res.mentions) == 1
    assert res.mentions[0].name == "Zoro"


def test_resolve_metacharacters_and_ansi() -> None:
    entities = [
        {
            "entityId": "e1-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "Zoro*", "entityType": "CHARACTER"},
        },
        {
            "entityId": "e2-uuid-12345678901234567890123456",
            "entityInfo": {"displayName": "\x1b[31mRed\x1b[0mName", "entityType": "CHARACTER"},
        },
    ]
    index = AssetIndex(entities=entities, media_assets=[])

    # Regex metacharacters match literally
    tokens = parse_mentions("Hello @Zoro*")
    res = resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @Zoro*")
    assert len(res.mentions) == 1
    assert res.mentions[0].name == "Zoro*"

    # ANSI escape sequences stripped in errors
    tokens = parse_mentions("Hello @nonexistent")
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_mentions(tokens, index, path="image", model="nano2", prompt="Hello @nonexistent")
    assert "RedName" in str(exc_info.value)
    assert "\x1b[31m" not in str(exc_info.value)
