"""The ``Zzl0ze`` project read on flow.google.com -> characters, voices, image media.

Fixture: ``tests/api/fixtures/zzl0ze_reply.json`` — a real reply captured 2026-10-16
(docs/superpowers/spikes/2026-10-16-migrated-character-lifecycle-wire.md), every URL
replaced by a placeholder and every UUID by a deterministic fake. Edge rows are built
in the tests from the measured shapes.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.character import VOICE_NAMES, voice_from_wire
from gflow_cli.api.transports.migrated_character import ProjectRead, parse_project_read
from gflow_cli.errors import WireFormatError

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "zzl0ze_reply.json"
_PROJECT = "00000000-0000-4000-8000-000000000003"
_ENTITY = "00000000-0000-4000-8000-000000000004"
_PORTRAIT_WF = "00000000-0000-4000-8000-000000000005"
_BODY_WF = "00000000-0000-4000-8000-000000000001"


def _payload() -> list[Any]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _read() -> ProjectRead:
    return parse_project_read(_payload())


class TestCharacters:
    def test_the_character_row_becomes_a_character(self) -> None:
        (char,) = _read().characters
        assert char.entity_id == _ENTITY
        assert char.project_id == _PROJECT
        assert char.display_name == "Personnage sans titre"
        assert char.workflow_ids == (_PORTRAIT_WF, _BODY_WF)
        assert char.personality == "soft"

    def test_the_voice_is_canonical_not_wire_case(self) -> None:
        (char,) = _read().characters
        assert char.voice == "Aoede"

    def test_a_fresh_character_has_no_images_voice_or_personality(self) -> None:
        payload = _payload()
        row = copy.deepcopy(payload[5][0])
        row[1] = "00000000-0000-4000-8000-0000000000ff"
        row[3] = [1, "Personnage sans titre", []]
        row[4] = None
        payload[5].append(row)
        fresh = next(c for c in parse_project_read(payload).characters if c.entity_id == row[1])
        assert fresh.workflow_ids == ()
        assert fresh.voice is None
        assert fresh.personality is None
        assert fresh.thumbnail_media_id is None

    def test_a_null_voice_slot_with_a_personality(self) -> None:
        payload = _payload()
        payload[5][0][3] = [1, "Nora", [[[_PORTRAIT_WF]], None, "calm, precise"]]
        (char,) = parse_project_read(payload).characters
        assert char.voice is None
        assert char.personality == "calm, precise"
        assert char.workflow_ids == (_PORTRAIT_WF,)

    def test_a_project_with_no_characters_reads_as_empty_not_as_drift(self) -> None:
        payload = _payload()
        payload[5] = None
        assert parse_project_read(payload).characters == ()

    def test_parse_project_read_keeps_only_requested_projects_rows(self) -> None:
        payload = _payload()
        other_row = copy.deepcopy(payload[5][0])
        other_row[0] = "00000000-0000-4000-8000-000000000099"
        other_row[1] = "00000000-0000-4000-8000-0000000000aa"
        other_row[3][1] = "Wrong Project Character"
        payload[5].insert(0, other_row)

        chars = parse_project_read(payload, project_id=_PROJECT).characters
        assert [c.entity_id for c in chars] == [_ENTITY]

    def test_non_character_entity_kind_is_skipped(self) -> None:
        payload = _payload()
        scene_row = copy.deepcopy(payload[5][0])
        scene_row[1] = "00000000-0000-4000-8000-0000000000bb"
        scene_row[3] = [2, "A Scene", []]
        payload[5].append(scene_row)

        chars = parse_project_read(payload).characters
        assert [c.display_name for c in chars] == ["Personnage sans titre"]

    def test_the_character_dto_still_carries_no_url(self) -> None:
        (char,) = _read().characters
        assert "https://" not in repr(char)


class TestVoices:
    def test_the_live_catalog_is_read(self) -> None:
        voices = _read().voices
        assert len(voices) == 30
        names = [v.name for v in voices]
        assert "Enceladus" in names
        aoede = next(v for v in voices if v.name == "Aoede")
        assert aoede.description == "Female, breezy, mid pitch"

    def test_the_offline_list_has_caught_up_with_the_live_one(self) -> None:
        live = {v.name for v in _read().voices}
        assert live <= set(VOICE_NAMES)

    @pytest.mark.parametrize("given", ["Aoede", "aoede", "AOEDE"])
    def test_voice_case_round_trips(self, given: str) -> None:
        assert voice_from_wire(given) == "Aoede"

    def test_an_unknown_wire_voice_keeps_its_identity(self) -> None:
        # A voice Flow adds before gflow knows it must be kept verbatim, never renamed.
        assert voice_from_wire("myCustomVoice") == "myCustomVoice"


class TestDrift:
    def test_a_non_list_reply_is_wire_drift(self) -> None:
        with pytest.raises(WireFormatError) as exc:
            parse_project_read({"not": "a list"})
        assert exc.value.route == "batchexecute:Zzl0ze"

    def test_a_malformed_character_row_is_drift_not_silently_skipped(self) -> None:
        payload = _payload()
        payload[5][0] = ["only", "two"]
        with pytest.raises(WireFormatError) as exc:
            parse_project_read(payload)
        assert "Zzl0ze" in str(exc.value)

    def test_non_list_character_slot_is_wire_drift(self) -> None:
        payload = _payload()
        payload[5] = "not-a-list"
        with pytest.raises(WireFormatError, match=r"slot 5 \(characters\) is not a list"):
            parse_project_read(payload)

    def test_bool_entity_kind_is_wire_drift(self) -> None:
        payload = _payload()
        payload[5][0][3][0] = True
        with pytest.raises(WireFormatError, match="a character row has no"):
            parse_project_read(payload)

    def test_non_list_character_parts_is_wire_drift_while_none_parses(self) -> None:
        drifted = _payload()
        drifted[5][0][3] = [1, "Nora", "oops"]
        with pytest.raises(WireFormatError, match="character parts") as exc:
            parse_project_read(drifted)
        assert exc.value.route == "batchexecute:Zzl0ze"

        none_parts = _payload()
        none_parts[5][0][3] = [1, "Nora", None]
        (char,) = parse_project_read(none_parts).characters
        assert char.display_name == "Nora"
        assert char.workflow_ids == ()
        assert char.voice is None
        assert char.personality is None

    def test_malformed_image_refs_are_wire_drift(self) -> None:
        """CodeRabbit on #1306: a bad images container or ref read as "no workflows"."""
        bad_container = _payload()
        bad_container[5][0][3][2][0] = "oops"
        with pytest.raises(WireFormatError, match="character images are not"):
            parse_project_read(bad_container)

        bad_ref = _payload()
        bad_ref[5][0][3][2][0] = ["not-a-ref"]
        with pytest.raises(WireFormatError, match="character image ref is not"):
            parse_project_read(bad_ref)

        no_images = _payload()
        no_images[5][0][3][2][0] = None
        (char,) = parse_project_read(no_images).characters
        assert char.workflow_ids == ()

    def test_non_list_voice_refs_cell_is_wire_drift(self) -> None:
        payload = _payload()
        payload[5][0][3][2][1] = "aoede"
        with pytest.raises(WireFormatError, match="character voice refs are not"):
            parse_project_read(payload)

    def test_drifted_single_element_voice_ref_raises_wire_format_error(self) -> None:
        payload = _payload()
        payload[0] = "https://lh3.googleusercontent.com/asb/SECRET-VOICE-CAPABILITY"
        payload[5][0][3][2][1] = [["aoede"]]
        with pytest.raises(WireFormatError) as exc:
            parse_project_read(payload)
        assert exc.value.route == "batchexecute:Zzl0ze"
        assert "SECRET-VOICE-CAPABILITY" not in str(exc.value)
        assert "SECRET-VOICE-CAPABILITY" not in json.dumps(exc.value.discovery)

    def test_drift_detail_carries_no_capability_link(self) -> None:
        # Planted at index 0 so it sits inside the first 200 chars of discovery_head.
        payload = ["https://lh3.googleusercontent.com/asb/SECRET-PATH-xyz"]
        with pytest.raises(WireFormatError) as exc:
            parse_project_read(payload)
        head = (exc.value.discovery or {}).get("payload_head", "")
        assert "<redacted:url>" in head
        assert "SECRET-PATH" not in str(exc.value)
        assert "SECRET-PATH" not in json.dumps(exc.value.discovery)


def test_character_doc_matches_zzl0ze_row_and_voice_layout() -> None:
    doc = (Path(__file__).resolve().parents[3] / "docs" / "CHARACTER.md").read_text(
        encoding="utf-8"
    )
    assert "[project, entity_id, _, [1, name, [images, voices, personality]]" in doc
    assert '[[null, "<wire id>"]]' in doc
    assert "voiceRefs[0][0]" not in doc
    assert "[entityId, projectId," not in doc
