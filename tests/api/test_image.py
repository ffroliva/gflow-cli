"""Tests for `gflow_cli.api.image` — pure value objects + body builder.

Tests load the captured samples in `samples/captured/` and assert structural
equality (modulo the four variable fields: recaptcha token, projectId,
batchId, sessionId).
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.image import (
    MAX_IMAGE_REFERENCES,
    AgentInstruction,
    Aspect,
    GenerateImageRequest,
    ImageRef,
    Model,
    ProjectBrief,
    _build_batch_generate_images_body,
    build_agent_brief_cards,
    reference_cap_for,
)

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples" / "captured"

# Variable fields that are swapped to sentinels before comparison.
_PROJECT_SENTINEL = "<PROJECT_SENTINEL>"
_BATCH_SENTINEL = "<BATCH_SENTINEL>"
_SESSION_SENTINEL = "<SESSION_SENTINEL>"
_TOKEN_SENTINEL = "<TOKEN_SENTINEL>"


def _normalize(body: dict[str, Any]) -> dict[str, Any]:
    """Replace the four variable fields with sentinels everywhere they occur."""
    out = deepcopy(body)

    def _walk_client_context(cc: dict[str, Any]) -> None:
        if "projectId" in cc:
            cc["projectId"] = _PROJECT_SENTINEL
        if "sessionId" in cc:
            cc["sessionId"] = _SESSION_SENTINEL
        rc = cc.get("recaptchaContext")
        if isinstance(rc, dict) and "token" in rc:
            rc["token"] = _TOKEN_SENTINEL

    cc_root = out.get("clientContext")
    if isinstance(cc_root, dict):
        _walk_client_context(cc_root)
    mgc = out.get("mediaGenerationContext")
    if isinstance(mgc, dict) and "batchId" in mgc:
        mgc["batchId"] = _BATCH_SENTINEL
    for req in out.get("requests", []):
        cc_req = req.get("clientContext")
        if isinstance(cc_req, dict):
            _walk_client_context(cc_req)
    return out


def _load_sample(name: str) -> dict[str, Any]:
    return json.loads((SAMPLES_DIR / name).read_text(encoding="utf-8"))


class TestImageAspect:
    def test_portrait(self) -> None:
        assert Aspect.from_cli("9:16") is Aspect.PORTRAIT
        assert Aspect.PORTRAIT.value == "IMAGE_ASPECT_RATIO_PORTRAIT"

    def test_landscape(self) -> None:
        assert Aspect.from_cli("16:9") is Aspect.LANDSCAPE
        assert Aspect.LANDSCAPE.value == "IMAGE_ASPECT_RATIO_LANDSCAPE"

    def test_square(self) -> None:
        assert Aspect.from_cli("1:1") is Aspect.SQUARE
        assert Aspect.SQUARE.value == "IMAGE_ASPECT_RATIO_SQUARE"

    def test_landscape_four_three(self) -> None:
        assert Aspect.from_cli("4:3") is Aspect.LANDSCAPE_FOUR_THREE
        assert Aspect.LANDSCAPE_FOUR_THREE.value == "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE"

    def test_portrait_three_four(self) -> None:
        assert Aspect.from_cli("3:4") is Aspect.PORTRAIT_THREE_FOUR
        assert Aspect.PORTRAIT_THREE_FOUR.value == "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR"

    def test_default_when_none(self) -> None:
        assert Aspect.from_cli(None) is Aspect.PORTRAIT

    def test_garbage_raises(self) -> None:
        with pytest.raises(ValueError):
            Aspect.from_cli("garbage")


class TestImageModel:
    def test_nano2_alias(self) -> None:
        assert Model.from_cli("nano2") is Model.NARWHAL

    def test_nano_banana_2_alias(self) -> None:
        assert Model.from_cli("nano-banana-2") is Model.NARWHAL

    def test_nano_pro_alias(self) -> None:
        assert Model.from_cli("nano-pro") is Model.GEM_PIX_2

    def test_image4_alias(self) -> None:
        assert Model.from_cli("image4") is Model.IMAGEN_3_5

    def test_default_when_none(self) -> None:
        assert Model.from_cli(None) is Model.NARWHAL

    def test_wire_value(self) -> None:
        assert Model.NARWHAL.value == "NARWHAL"
        assert Model.GEM_PIX_2.value == "GEM_PIX_2"
        assert Model.IMAGEN_3_5.value == "IMAGEN_3_5"

    def test_unknown_alias_raises(self) -> None:
        with pytest.raises(ValueError):
            Model.from_cli("totally-unknown")


class TestImageRef:
    def test_to_wire(self) -> None:
        assert ImageRef("uuid-here").to_wire() == {
            "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE",
            "name": "uuid-here",
        }

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            ImageRef("")

    def test_whitespace_raises(self) -> None:
        with pytest.raises(ValueError):
            ImageRef("   ")

    def test_whitespace_padded_raises(self) -> None:
        # Padded values would emit garbage on the wire — reject explicitly.
        with pytest.raises(ValueError):
            ImageRef("  real-uuid  ")
        with pytest.raises(ValueError):
            ImageRef("real-uuid ")
        with pytest.raises(ValueError):
            ImageRef(" real-uuid")


class TestGenerateImageRequest:
    def test_empty_prompt_raises(self) -> None:
        with pytest.raises(ValueError):
            GenerateImageRequest(prompt="", aspect=Aspect.PORTRAIT, model=Model.NARWHAL)

    def test_whitespace_only_prompt_raises(self) -> None:
        with pytest.raises(ValueError):
            GenerateImageRequest(prompt="   ", aspect=Aspect.PORTRAIT, model=Model.NARWHAL)

    def test_carries_recaptcha_token(self) -> None:
        req = GenerateImageRequest(
            prompt="test",
            model=Model.NARWHAL,
            aspect=Aspect.PORTRAIT,
            recaptcha_token="abc123_recaptcha",
        )
        assert req.recaptcha_token == "abc123_recaptcha"

    def test_recaptcha_token_defaults_to_empty(self) -> None:
        req = GenerateImageRequest(prompt="test", model=Model.NARWHAL, aspect=Aspect.PORTRAIT)
        assert req.recaptcha_token == ""

    def test_accepts_reference_entities(self) -> None:
        req = GenerateImageRequest(prompt="x", reference_entities=("ent-1", "ent-2"))
        assert req.reference_entities == ("ent-1", "ent-2")

    def test_accepts_reference_entity_names(self) -> None:
        req = GenerateImageRequest(prompt="x", reference_entity_names=("Stacky", "Drako"))
        assert req.reference_entity_names == ("Stacky", "Drako")

    def test_reference_entities_default_empty(self) -> None:
        req = GenerateImageRequest(prompt="x")
        assert req.reference_entities == ()
        assert req.reference_entity_names == ()


class TestReferenceCap:
    def test_cap_values(self) -> None:
        assert reference_cap_for(Model.NARWHAL) == 10
        assert reference_cap_for(Model.GEM_PIX_2) == 10
        assert reference_cap_for(Model.IMAGEN_3_5) == 3

    def test_at_cap_is_allowed(self) -> None:
        # Imagen 4 at exactly its 3-ref cap must build cleanly.
        req = GenerateImageRequest(
            prompt="ok",
            model=Model.IMAGEN_3_5,
            refs=tuple(ImageRef(f"ref-{i}") for i in range(3)),
        )
        assert len(req.refs) == 3

    def test_over_cap_raises(self) -> None:
        with pytest.raises(ValueError, match="at most 3"):
            GenerateImageRequest(
                prompt="too many",
                model=Model.IMAGEN_3_5,
                refs=tuple(ImageRef(f"ref-{i}") for i in range(4)),
            )

    def test_cap_counts_refs_and_ref_paths_together(self) -> None:
        # The cap is on TOTAL refs (uploaded UUIDs + local files combined).
        with pytest.raises(ValueError, match="at most 3"):
            GenerateImageRequest(
                prompt="mix",
                model=Model.IMAGEN_3_5,
                refs=(ImageRef("a"), ImageRef("b")),
                ref_paths=(Path("c.png"), Path("d.png")),
            )

    def test_nano_allows_ten(self) -> None:
        req = GenerateImageRequest(
            prompt="ten refs",
            model=Model.NARWHAL,
            refs=tuple(ImageRef(f"ref-{i}") for i in range(MAX_IMAGE_REFERENCES)),
        )
        assert len(req.refs) == MAX_IMAGE_REFERENCES

    def test_cap_counts_entities_with_image_refs(self) -> None:
        # Character entities count toward the SAME per-model cap as image refs.
        with pytest.raises(ValueError, match="at most 3"):
            GenerateImageRequest(
                prompt="mix",
                model=Model.IMAGEN_3_5,
                refs=(ImageRef("a"), ImageRef("b")),
                reference_entities=("ent-1", "ent-2"),
            )

    def test_entities_within_cap_ok(self) -> None:
        req = GenerateImageRequest(
            prompt="ok",
            model=Model.IMAGEN_3_5,
            reference_entities=("ent-1", "ent-2", "ent-3"),
        )
        assert len(req.reference_entities) == 3


class TestBuildBatchGenerateImagesBody:
    def test_includes_reference_entities_when_present(self) -> None:
        # Shape confirmed by the 2026-06-08 live capture: the image submit
        # carries `referenceEntities: [{"entityId": <id>}]`.
        built = _build_batch_generate_images_body(
            GenerateImageRequest(prompt="x", reference_entities=("ent-1", "ent-2")),
            project_id="P",
            batch_id="B",
            seed=1,
            session_id="S",
        )
        assert built["requests"][0]["referenceEntities"] == [
            {"entityId": "ent-1"},
            {"entityId": "ent-2"},
        ]

    def test_omits_reference_entities_when_absent(self) -> None:
        # No entities → the key must not appear (keeps plain t2i/i2i bodies
        # byte-identical to the captured samples).
        built = _build_batch_generate_images_body(
            GenerateImageRequest(prompt="x"),
            project_id="P",
            batch_id="B",
            seed=1,
            session_id="S",
        )
        assert "referenceEntities" not in built["requests"][0]

    def test_matches_sample_06_t2i(self) -> None:
        sample = _load_sample("06_batchGenerateImages.json")
        sample_body = sample["request_body_parsed"]
        prompt = sample_body["requests"][0]["structuredPrompt"]["parts"][0]["text"]
        seed = sample_body["requests"][0]["seed"]

        req = GenerateImageRequest(
            prompt=prompt,
            aspect=Aspect.PORTRAIT,
            model=Model.NARWHAL,
            refs=(),
        )
        built = _build_batch_generate_images_body(
            dc_replace(req, recaptcha_token="any-token"),
            project_id="any-project",
            batch_id="any-batch",
            seed=seed,
            session_id="any-session",
        )
        assert _normalize(built) == _normalize(sample_body)

    def test_matches_sample_07_i2i_4_3(self) -> None:
        sample = _load_sample("07_batchGenerateImages_seeded.json")
        sample_body = sample["request_body_parsed"]
        prompt = sample_body["requests"][0]["structuredPrompt"]["parts"][0]["text"]
        seed = sample_body["requests"][0]["seed"]
        ref_uuid = sample_body["requests"][0]["imageInputs"][0]["name"]

        req = GenerateImageRequest(
            prompt=prompt,
            aspect=Aspect.LANDSCAPE_FOUR_THREE,
            model=Model.NARWHAL,
            refs=(ImageRef(ref_uuid),),
        )
        built = _build_batch_generate_images_body(
            dc_replace(req, recaptcha_token="any-token"),
            project_id="any-project",
            batch_id="any-batch",
            seed=seed,
            session_id="any-session",
        )
        assert _normalize(built) == _normalize(sample_body)

    def test_clientcontext_duplicated_at_root_and_per_request(self) -> None:
        req = GenerateImageRequest(
            prompt="hello",
            aspect=Aspect.PORTRAIT,
            model=Model.NARWHAL,
        )
        body = _build_batch_generate_images_body(
            dc_replace(req, recaptcha_token="tok-1"),
            project_id="proj-1",
            batch_id="batch-1",
            seed=42,
            session_id=";1234567890",
        )
        root_cc = body["clientContext"]
        req_cc = body["requests"][0]["clientContext"]
        assert root_cc["projectId"] == "proj-1" == req_cc["projectId"]
        assert root_cc["tool"] == "PINHOLE" == req_cc["tool"]
        assert root_cc["sessionId"] == ";1234567890" == req_cc["sessionId"]
        # Same recaptcha token in both places — confirmed by samples 06/07.
        assert root_cc["recaptchaContext"]["token"] == "tok-1"
        assert req_cc["recaptchaContext"]["token"] == "tok-1"
        assert root_cc["recaptchaContext"]["token"] == req_cc["recaptchaContext"]["token"]

    def test_use_new_media_flag_set_true(self) -> None:
        req = GenerateImageRequest(prompt="hello", aspect=Aspect.PORTRAIT, model=Model.NARWHAL)
        body = _build_batch_generate_images_body(
            dc_replace(req, recaptcha_token="t"),
            project_id="p",
            batch_id="b",
            seed=1,
            session_id="s",
        )
        assert body["useNewMedia"] is True

    def test_image_inputs_empty_for_t2i(self) -> None:
        req = GenerateImageRequest(
            prompt="hello",
            aspect=Aspect.PORTRAIT,
            model=Model.NARWHAL,
            refs=(),
        )
        body = _build_batch_generate_images_body(
            dc_replace(req, recaptcha_token="t"),
            project_id="p",
            batch_id="b",
            seed=1,
            session_id="s",
        )
        # Must be the empty list — NOT missing, NOT null.
        assert "imageInputs" in body["requests"][0]
        assert body["requests"][0]["imageInputs"] == []


# ---------------------------------------------------------------------------
# AgentInstruction.resolved_title + build_agent_brief_cards
# ---------------------------------------------------------------------------


def test_resolved_title_uses_explicit_title_when_present() -> None:
    inst = AgentInstruction(text="anything at all", title="  My Card  ")
    assert inst.resolved_title() == "My Card"


def test_resolved_title_derives_from_first_line_when_blank() -> None:
    inst = AgentInstruction(text="Cinematic lighting\nsecond line ignored")
    assert inst.resolved_title() == "Cinematic lighting"


def test_resolved_title_truncates_long_text() -> None:
    long = "x" * 100
    title = AgentInstruction(text=long).resolved_title()
    assert title.endswith("…")
    assert len(title) == 58  # 57 chars + ellipsis


def test_build_agent_brief_cards_serializes_all_fields() -> None:
    cards = build_agent_brief_cards(
        (
            AgentInstruction(
                text="crayon",
                enabled=True,
                image_media_ids=("m1", "m2"),
                character_ids=("c1",),
                title="Crayon",
            ),
            AgentInstruction(text="noir", enabled=False),
        ),
        project_id="proj-9",
    )
    assert len(cards) == 2
    first = cards[0]
    assert first["title"] == "Crayon"
    assert first["description"] == "crayon"
    assert first["enabled"] is True
    assert first["imageReferenceMediaIds"] == ["m1", "m2"]
    assert first["characterReferenceEntityNames"] == ["projects/proj-9/entities/c1"]
    assert "id" in first  # a generated uuid
    # The plain card omits the optional reference keys entirely.
    assert cards[1]["enabled"] is False
    assert "imageReferenceMediaIds" not in cards[1]
    assert "characterReferenceEntityNames" not in cards[1]
    assert cards[1]["title"] == "noir"


# ---------------------------------------------------------------------------
# AgentInstruction.from_wire / id preservation / relaxed validation
# ---------------------------------------------------------------------------


def test_agent_instruction_from_wire_parses_server_card() -> None:
    inst = AgentInstruction.from_wire(
        {
            "id": "srv-id-1",
            "title": "Style",
            "description": "crayon",
            "enabled": True,
            "imageReferenceMediaIds": ["m1", "m2"],
            "characterReferenceEntityNames": ["projects/p9/entities/char-7"],
        }
    )
    assert inst.id == "srv-id-1"
    assert inst.title == "Style"
    assert inst.text == "crayon"
    assert inst.enabled is True
    assert inst.image_media_ids == ("m1", "m2")
    assert inst.character_ids == ("char-7",)  # resource name stripped to bare id


def test_agent_instruction_image_only_card_is_valid() -> None:
    # A reference with no text is a valid card (the reference IS the instruction).
    inst = AgentInstruction(text="", image_media_ids=("m1",))
    assert inst.image_media_ids == ("m1",)


def test_agent_instruction_empty_text_and_no_ref_raises() -> None:
    with pytest.raises(ValueError, match="text or at least one reference"):
        AgentInstruction(text="   ")


def test_build_cards_preserves_existing_id_and_mints_for_new() -> None:
    cards = build_agent_brief_cards(
        (
            AgentInstruction(text="keep", id="server-abc"),
            AgentInstruction(text="fresh"),  # no id → minted
        ),
        project_id="p1",
    )
    assert cards[0]["id"] == "server-abc"
    assert cards[1]["id"] and cards[1]["id"] != "server-abc"


# ---------------------------------------------------------------------------
# ProjectBrief.from_agent_info / find
# ---------------------------------------------------------------------------

_AGENT_INFO = {
    "projectBrief": {
        "enabled": True,
        "cards": [
            {"id": "a", "title": "Crayon", "description": "crayon", "enabled": True},
            {"id": "b", "title": "Noir", "description": "noir", "enabled": False},
        ],
    },
    "agentToggleState": "AGENT_TOGGLE_STATE_ENABLED",
}


def test_project_brief_from_agent_info() -> None:
    brief = ProjectBrief.from_agent_info(_AGENT_INFO)
    assert brief.enabled is True
    assert brief.agent_toggle_state == "AGENT_TOGGLE_STATE_ENABLED"
    assert tuple(c.id for c in brief.cards) == ("a", "b")
    assert brief.cards[1].enabled is False


def test_project_brief_from_none_is_empty() -> None:
    brief = ProjectBrief.from_agent_info(None)
    assert brief.enabled is False
    assert brief.cards == ()


def test_project_brief_find_by_title_case_insensitive() -> None:
    brief = ProjectBrief.from_agent_info(_AGENT_INFO)
    assert brief.find(title="crayon").id == "a"
    assert brief.find(title="  NOIR ").id == "b"


def test_project_brief_find_by_id() -> None:
    brief = ProjectBrief.from_agent_info(_AGENT_INFO)
    assert brief.find(card_id="b").title == "Noir"


def test_project_brief_find_not_found_raises() -> None:
    brief = ProjectBrief.from_agent_info(_AGENT_INFO)
    with pytest.raises(ValueError, match="no instruction card matches"):
        brief.find(title="missing")


def test_project_brief_find_ambiguous_title_raises() -> None:
    info = {
        "projectBrief": {
            "cards": [
                {"id": "a", "title": "Dup", "description": "x", "enabled": True},
                {"id": "b", "title": "dup", "description": "y", "enabled": True},
            ]
        }
    }
    brief = ProjectBrief.from_agent_info(info)
    with pytest.raises(ValueError, match="ambiguous"):
        brief.find(title="dup")


def test_project_brief_find_requires_exactly_one_selector() -> None:
    brief = ProjectBrief.from_agent_info(_AGENT_INFO)
    with pytest.raises(ValueError, match="exactly one"):
        brief.find()
    with pytest.raises(ValueError, match="exactly one"):
        brief.find(title="Crayon", card_id="a")
