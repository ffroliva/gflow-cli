"""Unit tests for gflow_cli.api.character — DTOs and projectInitialData parser."""

from __future__ import annotations

import dataclasses
import re

import pytest

from gflow_cli.api.character import Character, CharacterImageRequest, parse_characters

# ---------------------------------------------------------------------------
# parse_characters
# ---------------------------------------------------------------------------


def test_parse_characters_filters_to_character_entities() -> None:
    payload: dict = {
        "projectContents": {
            "entities": [
                {
                    "projectId": "p",
                    "entityId": "e1",
                    "entityInfo": {
                        "entityType": "CHARACTER",
                        "displayName": "Ana",
                        "characterInfo": {
                            "imageReferences": [{"workflowId": "w1"}],
                            "audioReferences": [{"presetVoiceId": "gacrux"}],
                            "personalityNotes": "brave",
                        },
                    },
                    "thumbnailMediaId": "m1",
                },
                {
                    "projectId": "p",
                    "entityId": "e2",
                    "entityInfo": {"entityType": "SCENE"},
                },
            ]
        }
    }
    chars = parse_characters(payload)
    assert [c.entity_id for c in chars] == ["e1"]
    assert chars[0].display_name == "Ana"
    assert chars[0].voice == "gacrux"
    assert chars[0].workflow_ids == ("w1",)


def test_parse_characters_empty_entities() -> None:
    assert parse_characters({}) == []
    assert parse_characters({"projectContents": {}}) == []
    assert parse_characters({"projectContents": {"entities": []}}) == []


def test_parse_characters_no_voice() -> None:
    payload: dict = {
        "projectContents": {
            "entities": [
                {
                    "projectId": "p",
                    "entityId": "e1",
                    "entityInfo": {
                        "entityType": "CHARACTER",
                        "displayName": "Bob",
                        "characterInfo": {},
                    },
                }
            ]
        }
    }
    chars = parse_characters(payload)
    assert chars[0].voice is None
    assert chars[0].personality is None
    assert chars[0].thumbnail_media_id is None
    assert chars[0].workflow_ids == ()


def test_parse_characters_multiple_workflow_ids() -> None:
    payload: dict = {
        "projectContents": {
            "entities": [
                {
                    "projectId": "p",
                    "entityId": "e1",
                    "entityInfo": {
                        "entityType": "CHARACTER",
                        "displayName": "Multi",
                        "characterInfo": {
                            "imageReferences": [
                                {"workflowId": "w1"},
                                {"workflowId": "w2"},
                                # entry without workflowId should be skipped
                                {"someOtherKey": "ignored"},
                            ],
                        },
                    },
                }
            ]
        }
    }
    chars = parse_characters(payload)
    assert chars[0].workflow_ids == ("w1", "w2")


def test_parse_characters_skips_missing_entity_id() -> None:
    """A CHARACTER entity without entityId is skipped; valid entities are kept."""
    payload: dict = {
        "projectContents": {
            "entities": [
                {
                    "projectId": "p",
                    "entityId": "e1",
                    "entityInfo": {
                        "entityType": "CHARACTER",
                        "displayName": "Good",
                        "characterInfo": {},
                    },
                },
                {
                    # Missing entityId — malformed wire data
                    "projectId": "p",
                    "entityInfo": {
                        "entityType": "CHARACTER",
                        "displayName": "Bad",
                        "characterInfo": {},
                    },
                },
            ]
        }
    }
    chars = parse_characters(payload)
    assert len(chars) == 1
    assert chars[0].entity_id == "e1"


def test_parse_characters_skips_missing_entity_info() -> None:
    """A CHARACTER-typed entity that has no entityInfo key at all is skipped."""
    payload: dict = {
        "projectContents": {
            "entities": [
                {
                    "projectId": "p",
                    "entityId": "e1",
                    "entityInfo": {
                        "entityType": "CHARACTER",
                        "displayName": "Good",
                        "characterInfo": {},
                    },
                },
                {
                    # No entityInfo at all — parser must not raise
                    "projectId": "p",
                    "entityId": "e2",
                },
            ]
        }
    }
    chars = parse_characters(payload)
    # e2 has no entityInfo so entityType != "CHARACTER" → silently skipped
    assert len(chars) == 1
    assert chars[0].entity_id == "e1"


def test_parse_characters_personality_and_thumbnail() -> None:
    payload: dict = {
        "projectContents": {
            "entities": [
                {
                    "projectId": "proj1",
                    "entityId": "ent1",
                    "entityInfo": {
                        "entityType": "CHARACTER",
                        "displayName": "Hero",
                        "characterInfo": {"personalityNotes": "fierce"},
                    },
                    "thumbnailMediaId": "thumb99",
                }
            ]
        }
    }
    chars = parse_characters(payload)
    assert chars[0].personality == "fierce"
    assert chars[0].thumbnail_media_id == "thumb99"
    assert chars[0].project_id == "proj1"


# ---------------------------------------------------------------------------
# Redaction: scenario #16
# No signed URLs (signature= / Expires=) must appear in any Character attribute
# ---------------------------------------------------------------------------

_SIGNED_URL_RE = re.compile(r"signature=|Expires=", re.IGNORECASE)


def _has_signed_url(value: object) -> bool:
    """Recursively check whether *value* contains a signed-URL fragment."""
    if isinstance(value, str):
        return bool(_SIGNED_URL_RE.search(value))
    if isinstance(value, (list, tuple)):
        return any(_has_signed_url(v) for v in value)
    if isinstance(value, dict):
        return any(_has_signed_url(v) for v in value.values())
    return False


def test_parse_characters_no_signed_urls_in_output() -> None:
    """Even when the wire payload contains fifeUrl signed URLs, the Character
    DTO must NOT expose them — only ids (workflow_ids, thumbnail_media_id)."""
    signed = "https://lh3.googleusercontent.com/someimage?signature=ABCDEF1234&Expires=9999999999"
    payload: dict = {
        "projectContents": {
            "entities": [
                {
                    "projectId": "p",
                    "entityId": "e1",
                    "entityInfo": {
                        "entityType": "CHARACTER",
                        "displayName": "Leaky",
                        "characterInfo": {
                            "imageReferences": [
                                {
                                    "workflowId": "w1",
                                    # fifeUrl is present on the wire but must NOT leak
                                    "fifeUrl": signed,
                                }
                            ],
                            "audioReferences": [{"presetVoiceId": "aoede"}],
                        },
                    },
                    "thumbnailMediaId": "m1",
                    # top-level signed url that might be present in the raw payload
                    "thumbnailUrl": signed,
                }
            ]
        }
    }
    chars = parse_characters(payload)
    assert chars, "Expected one character to be parsed"
    char = chars[0]

    # Collect all attribute values from the frozen dataclass
    all_values = [getattr(char, f.name) for f in dataclasses.fields(char)]
    for val in all_values:
        assert not _has_signed_url(val), f"Character attribute contains a signed URL: {val!r}"


# ---------------------------------------------------------------------------
# Character — frozen dataclass properties
# ---------------------------------------------------------------------------


def test_character_is_frozen() -> None:
    char = Character(
        entity_id="e1",
        display_name="Ana",
        project_id="p",
        workflow_ids=("w1",),
        voice="gacrux",
        personality="brave",
        thumbnail_media_id="m1",
    )
    with pytest.raises((AttributeError, TypeError)):
        char.display_name = "Mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CharacterImageRequest — input DTO
# ---------------------------------------------------------------------------


def test_character_image_request_defaults() -> None:
    req = CharacterImageRequest(prompt="A warrior woman")
    assert req.prompt == "A warrior woman"
    assert req.aspect == "9:16"
    assert req.model == "narwhal"
    assert req.image_reference_index == 0


def test_character_image_request_custom_fields() -> None:
    req = CharacterImageRequest(
        prompt="Mystic mage",
        aspect="9:16",
        model="narwhal",
        image_reference_index=2,
    )
    assert req.prompt == "Mystic mage"
    assert req.aspect == "9:16"
    assert req.model == "narwhal"
    assert req.image_reference_index == 2


def test_character_image_request_is_frozen() -> None:
    req = CharacterImageRequest(prompt="x")
    with pytest.raises((AttributeError, TypeError)):
        req.prompt = "mutated"  # type: ignore[misc]
