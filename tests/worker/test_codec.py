"""Task C2 — versioned worker-queue payload codec.

``schema_version`` is an ADDITIVE top-level key (design spec §3): a missing
version is legacy V0 and decodes through the same field lookups as V1; an
unknown version is a stable, typed failure raised BEFORE Playwright starts —
never interpreted optimistically. The codec maps ``(task_type, payload)``
onto the existing ``GenerateImageRequest`` / ``GenerateVideoRequest`` DTOs
rather than a parallel schema, so malformed-field cases below are really
exercising those DTOs' own validation, wrapped into one stable error type.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.video import GenerateVideoRequest
from gflow_cli.errors import EXIT_CODE_MAP, QueueSchemaError
from gflow_cli.worker.codec import CURRENT_SCHEMA_VERSION, decode_payload, encode_payload

# ---------------------------------------------------------------------------
# Legacy V0 / V1 decode
# ---------------------------------------------------------------------------


def test_missing_schema_version_decodes_as_legacy_v0() -> None:
    decoded = decode_payload("t2i", {"prompt": "sunrise"})
    assert decoded.schema_version == 0
    assert isinstance(decoded.request, GenerateImageRequest)
    assert decoded.request.prompt == "sunrise"


def test_v1_round_trip_preserves_top_level_fields() -> None:
    payload = {"schema_version": 1, "prompt": "sunrise", "count": 1}
    assert encode_payload("t2i", decode_payload("t2i", payload)) == payload


def test_v0_payload_upgrades_to_v1_on_encode() -> None:
    """A legacy payload with no version key encodes at the CURRENT version —
    this is the behaviour new enqueue sites rely on to stamp schema_version."""
    decoded = decode_payload("t2i", {"prompt": "sunrise"})
    assert encode_payload("t2i", decoded) == {
        "prompt": "sunrise",
        "schema_version": CURRENT_SCHEMA_VERSION,
    }


def test_video_payload_decodes_to_typed_request() -> None:
    decoded = decode_payload("t2v", {"prompt": "a river flows"})
    assert decoded.schema_version == 0
    assert isinstance(decoded.request, GenerateVideoRequest)
    assert decoded.request.prompt == "a river flows"


def test_i2v_uuid_picker_metadata_decodes_to_typed_request() -> None:
    decoded = decode_payload(
        "i2v",
        {
            "prompt": "pan",
            "mode": "i2v",
            "start_image_ref": "550e8400-e29b-41d4-a716-446655440000",
            "start_image_ref_display_name": "Brass key",
            "start_image_ref_local_path": "recorded.png",
            "start_image_ref_local_sha256": "a" * 64,
            "end_image_ref": "650e8400-e29b-41d4-a716-446655440000",
            "end_image_ref_display_name": "Wooden bench",
            "end_image_ref_local_path": "recorded-end.png",
            "end_image_ref_local_sha256": "b" * 64,
        },
    )
    request = decoded.request
    assert isinstance(request, GenerateVideoRequest)
    assert request.start_image_ref_id == "550e8400-e29b-41d4-a716-446655440000"
    assert request.start_image_ref_display_name == "Brass key"
    assert request.start_image_ref_local_path == Path("recorded.png")
    assert request.start_image_ref_local_sha256 == "a" * 64
    assert request.end_image_ref_id == "650e8400-e29b-41d4-a716-446655440000"
    assert request.end_image_ref_display_name == "Wooden bench"
    assert request.end_image_ref_local_path == Path("recorded-end.png")
    assert request.end_image_ref_local_sha256 == "b" * 64


# ---------------------------------------------------------------------------
# Unknown / malformed -> typed failure BEFORE execution
# ---------------------------------------------------------------------------


def test_unknown_schema_version_fails_before_execution() -> None:
    with pytest.raises(QueueSchemaError):
        decode_payload("t2i", {"schema_version": 99, "prompt": "sunrise"})


def test_unknown_task_type_fails() -> None:
    with pytest.raises(QueueSchemaError):
        decode_payload("bogus-type", {"prompt": "sunrise"})


def test_missing_required_field_fails() -> None:
    with pytest.raises(QueueSchemaError):
        decode_payload("t2i", {})


def test_invalid_enum_fails() -> None:
    with pytest.raises(QueueSchemaError):
        decode_payload("t2i", {"prompt": "sunrise", "aspect": "not-a-real-ratio"})


def test_video_invalid_mode_enum_fails() -> None:
    with pytest.raises(QueueSchemaError):
        decode_payload("t2v", {"prompt": "sunrise", "mode": "not-a-real-mode"})


def test_absurd_count_fails() -> None:
    with pytest.raises(QueueSchemaError):
        decode_payload("t2i", {"prompt": "sunrise", "count": 999})


def test_video_i2v_missing_required_frame_fails() -> None:
    """i2v requires a start frame — the DTO's own mode-symmetry validation is
    reused, not reimplemented, by the codec. ``mode`` is a payload field
    independent of ``task_type`` (mirrors daemon.py's existing behavior), so
    it must be set explicitly to exercise the i2v branch."""
    with pytest.raises(QueueSchemaError):
        decode_payload("i2v", {"prompt": "sunrise", "mode": "i2v"})


def test_malformed_path_field_fails() -> None:
    with pytest.raises(QueueSchemaError):
        decode_payload("i2v", {"prompt": "sunrise", "start_image": 12345})


def test_error_detail_never_echoes_prompt_text() -> None:
    with pytest.raises(QueueSchemaError) as exc_info:
        decode_payload("t2i", {"prompt": "TOP-SECRET-PROMPT-CONTENT", "count": 999})
    assert "TOP-SECRET-PROMPT-CONTENT" not in str(exc_info.value)
    assert "TOP-SECRET-PROMPT-CONTENT" not in exc_info.value.detail


def test_unknown_version_error_is_stable_rfc9457_shape() -> None:
    with pytest.raises(QueueSchemaError) as exc_info:
        decode_payload("t2i", {"schema_version": 99, "prompt": "sunrise"})
    problem = exc_info.value.to_problem_details()
    assert problem["type"] == "https://gflow-cli.dev/errors/queue-schema"
    assert problem["title"]
    assert EXIT_CODE_MAP[QueueSchemaError] == 30
