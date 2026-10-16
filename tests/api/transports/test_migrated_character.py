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

from gflow_cli.api.character import VOICE_NAMES, voice_from_wire, voice_to_wire
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
        assert voice_to_wire(given) == "aoede"
        assert voice_from_wire(voice_to_wire(given)) == "Aoede"

    def test_an_unknown_wire_voice_keeps_its_identity(self) -> None:
        # A voice Flow adds before gflow knows it must not be dropped or renamed.
        assert voice_from_wire("newvoice") == "Newvoice"


class TestImages:
    def test_each_character_image_maps_to_its_media_and_download_link(self) -> None:
        images = _read().images
        for wf in (_PORTRAIT_WF, _BODY_WF):
            assert wf in images
            assert images[wf].media_id
            assert images[wf].download_url.startswith("https://lh3.googleusercontent.com/asb/")

    def test_the_character_dto_still_carries_no_url(self) -> None:
        (char,) = _read().characters
        assert "https://" not in repr(char)


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

    def test_drift_detail_carries_no_capability_link(self) -> None:
        payload = _payload()
        payload[5] = "https://lh3.googleusercontent.com/asb/SECRET-PATH-xyz"
        with pytest.raises(WireFormatError) as exc:
            parse_project_read(payload)
        assert "SECRET-PATH" not in str(exc.value)
        assert "SECRET-PATH" not in json.dumps(exc.value.discovery)


class TestPatchVerification:
    """`rzMKMb` echoes the whole entity; the write is confirmed from that echo (scenario 8)."""

    @staticmethod
    def _reply(name: str = "Nora", voice: str | None = "aoede", pers: str | None = "calm") -> Any:
        parts: list[Any] = [[[_PORTRAIT_WF]], [[None, voice]] if voice else None]
        if pers is not None:
            parts.append(pers)
        return [[_PROJECT, _ENTITY, None, [1, name, parts], None, None, [1, 0], [2, 0]]]

    def test_an_echo_matching_every_requested_field_is_confirmed(self) -> None:
        from gflow_cli.api.transports.migrated_character import verify_patch

        char = verify_patch(self._reply(), entity_id=_ENTITY, name="Nora", voice="Aoede")
        assert (char.display_name, char.voice, char.personality) == ("Nora", "Aoede", "calm")

    @pytest.mark.parametrize(
        ("field", "value"),
        [("name", "Laura"), ("voice", "Charon"), ("personality", "loud")],
    )
    def test_an_echo_that_differs_is_wire_format_error(self, field: str, value: str) -> None:
        from gflow_cli.api.transports.migrated_character import verify_patch

        with pytest.raises(WireFormatError) as exc:
            verify_patch(self._reply(), entity_id=_ENTITY, **{field: value})
        assert exc.value.route == "batchexecute:rzMKMb"
        assert field in str(exc.value)

    def test_an_echo_for_another_entity_is_wire_format_error(self) -> None:
        from gflow_cli.api.transports.migrated_character import verify_patch

        with pytest.raises(WireFormatError):
            verify_patch(
                self._reply(), entity_id="00000000-0000-4000-8000-0000000000aa", name="Nora"
            )
