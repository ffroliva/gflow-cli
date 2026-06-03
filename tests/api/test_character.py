"""Unit tests for gflow_cli.api.character — DTOs and projectInitialData parser."""

from __future__ import annotations

import dataclasses
import re

import pytest

from gflow_cli.api.character import (
    VOICE_NAMES,
    VOICES,
    Character,
    CharacterImageRequest,
    Voice,
    parse_characters,
)

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
    assert req.model == "nano2"
    assert req.image_reference_index == 0
    # Characters have NO aspect-ratio control — the field must not exist.
    assert not hasattr(req, "aspect")


def test_character_image_request_custom_fields() -> None:
    req = CharacterImageRequest(
        prompt="Mystic mage",
        model="nanopro",
        image_reference_index=2,
    )
    assert req.prompt == "Mystic mage"
    assert req.model == "nanopro"
    assert req.image_reference_index == 2


def test_character_models_mapping() -> None:
    from gflow_cli.api.character import CHARACTER_MODELS

    assert CHARACTER_MODELS == {
        "nano2": "Nano Banana 2",
        "nanopro": "Nano Banana Pro",
    }


def test_character_image_request_is_frozen() -> None:
    req = CharacterImageRequest(prompt="x")
    with pytest.raises((AttributeError, TypeError)):
        req.prompt = "mutated"  # type: ignore[misc]


def test_character_image_request_carries_face_media_id() -> None:
    from gflow_cli.api.character import CharacterImageRequest

    req = CharacterImageRequest(prompt="a knight", image_reference_index=1, face_media_id="m-face")
    assert req.face_media_id == "m-face"
    assert req.image_reference_index == 1


def test_character_image_request_face_media_id_defaults_none() -> None:
    from gflow_cli.api.character import CharacterImageRequest

    assert CharacterImageRequest(prompt="x").face_media_id is None


def test_character_create_result_fields() -> None:
    from gflow_cli.api.character import CharacterCreateResult

    r = CharacterCreateResult(
        entity_id="e1",
        project_id="p",
        workflow_ids=("w1",),
        primary_media_ids=("m1",),
        name="Ana",
        voice="gacrux",
    )
    assert r.entity_id == "e1"
    assert r.workflow_ids == ("w1",)
    assert r.primary_media_ids == ("m1",)
    assert r.voice == "gacrux"


def test_character_create_result_voice_defaults_none() -> None:
    from gflow_cli.api.character import CharacterCreateResult

    r = CharacterCreateResult(
        entity_id="e",
        project_id="p",
        workflow_ids=(),
        primary_media_ids=(),
        name="Ana",
    )
    assert r.voice is None


# ---------------------------------------------------------------------------
# Voice catalog — 29 named Gemini voices
# ---------------------------------------------------------------------------


def test_voices_has_29_entries() -> None:
    assert len(VOICES) == 29


def test_voice_sample_url_pattern() -> None:
    assert (
        Voice("Sulafat").sample_url
        == "https://gstatic.com/aitestkitchen/voices/samples/Sulafat.wav"
    )


def test_voice_names_contains_known_voices() -> None:
    assert "Charon" in VOICE_NAMES
    assert "Sulafat" in VOICE_NAMES
    assert "Achernar" in VOICE_NAMES


def test_voice_names_are_capitalized() -> None:
    for name in VOICE_NAMES:
        assert name[0].isupper(), f"voice name not Capitalized: {name!r}"
        assert name == name.strip()


def test_voice_names_match_voices() -> None:
    assert VOICE_NAMES == tuple(v.name for v in VOICES)


def test_voices_each_have_a_sample_url() -> None:
    for v in VOICES:
        assert v.sample_url == f"https://gstatic.com/aitestkitchen/voices/samples/{v.name}.wav"


def test_voice_description_optional() -> None:
    assert Voice("Solo").description is None


def test_voice_is_frozen() -> None:
    v = Voice("Charon", "Male, informative, lower pitch")
    with pytest.raises((AttributeError, TypeError)):
        v.name = "Mutated"  # type: ignore[misc]
