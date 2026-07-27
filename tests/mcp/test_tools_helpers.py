# SPDX-License-Identifier: MIT
"""Unit tests for the mcp/tools.py validation helpers.

These cover the payload-building / path-validation helpers directly (extracted
to keep the tool functions under the cognitive-complexity threshold), so the
i2v/r2v/mutual-exclusion/path branches are exercised without a worker round-trip.
"""

from __future__ import annotations

from pathlib import Path

from gflow_cli.mcp.tools import (
    _bad_param,
    _build_video_media_inputs,
    _resolve_image_path,
    _resolve_image_references,
    _validate_project,
)


def test_validate_project_none_is_ok() -> None:
    assert _validate_project(None) is None


def test_validate_project_valid_id_is_ok() -> None:
    assert _validate_project("PROJ-123abc") is None


def test_validate_project_rejects_bad_id() -> None:
    err = _validate_project("bad/id")
    assert err is not None
    assert err["error"]["title"] == "Invalid Project Id"
    assert err["error"]["status"] == 400


def test_bad_param_envelope_shape() -> None:
    err = _bad_param("Some Title", "some detail")
    assert err["status"] == "error"
    assert err["error"]["type"] == "https://gflow-cli.dev/errors/bad-parameter"
    assert err["error"]["title"] == "Some Title"
    assert err["error"]["status"] == 400
    assert err["error"]["detail"] == "some detail"


def test_resolve_image_path_ok(tmp_path: Path) -> None:
    f = tmp_path / "img.png"
    f.write_bytes(b"x")
    resolved, err = _resolve_image_path(str(f), title="T", label="L")
    assert err is None
    assert resolved == str(f.resolve())


def test_resolve_image_path_missing(tmp_path: Path) -> None:
    resolved, err = _resolve_image_path(str(tmp_path / "nope.png"), title="Bad", label="Path")
    assert resolved is None
    assert err is not None
    assert err["error"]["title"] == "Bad"
    assert "does not exist or is not a file" in err["error"]["detail"]


def test_resolve_image_references_uuid_and_path(tmp_path: Path) -> None:
    f = tmp_path / "ref.png"
    f.write_bytes(b"x")
    uuid = "12345678-1234-1234-1234-123456789abc"
    data, err = _resolve_image_references([uuid, str(f)])
    assert err is None
    assert data == {"refs": [uuid], "ref_paths": [str(f.resolve())]}


def test_resolve_image_references_bad_path(tmp_path: Path) -> None:
    data, err = _resolve_image_references([str(tmp_path / "missing.png")])
    assert data is None
    assert err is not None
    assert err["error"]["title"] == "Invalid Reference Image"


def test_video_media_t2v_empty() -> None:
    media, err = _build_video_media_inputs(
        mode="t2v", initial_frame=None, end_frame=None, reference_images=None
    )
    assert err is None
    assert media == {}


def test_video_media_i2v_requires_initial_frame() -> None:
    media, err = _build_video_media_inputs(
        mode="i2v", initial_frame=None, end_frame=None, reference_images=None
    )
    assert media is None
    assert err is not None
    assert err["error"]["title"] == "Missing Start Image"


def test_video_media_r2v_requires_reference_images() -> None:
    media, err = _build_video_media_inputs(
        mode="r2v", initial_frame=None, end_frame=None, reference_images=None
    )
    assert media is None
    assert err is not None
    assert err["error"]["title"] == "Missing Reference Images"


def test_video_media_mutually_exclusive() -> None:
    media, err = _build_video_media_inputs(
        mode="i2v", initial_frame="a.png", end_frame=None, reference_images=["r.png"]
    )
    assert media is None
    assert err is not None
    assert err["error"]["title"] == "Mutually Exclusive Arguments"


def test_video_media_i2v_resolves_frames(tmp_path: Path) -> None:
    start = tmp_path / "start.png"
    end = tmp_path / "end.png"
    start.write_bytes(b"s")
    end.write_bytes(b"e")
    media, err = _build_video_media_inputs(
        mode="i2v", initial_frame=str(start), end_frame=str(end), reference_images=None
    )
    assert err is None
    assert media is not None
    assert media["start_image"] == str(start.resolve())
    assert media["end_image"] == str(end.resolve())


def test_video_media_r2v_resolves_reference_images(tmp_path: Path) -> None:
    r1 = tmp_path / "r1.png"
    r2 = tmp_path / "r2.png"
    r1.write_bytes(b"1")
    r2.write_bytes(b"2")
    media, err = _build_video_media_inputs(
        mode="r2v", initial_frame=None, end_frame=None, reference_images=[str(r1), str(r2)]
    )
    assert err is None
    assert media is not None
    assert media["reference_images"] == [str(r1.resolve()), str(r2.resolve())]


def test_video_media_i2v_bad_frame(tmp_path: Path) -> None:
    media, err = _build_video_media_inputs(
        mode="i2v",
        initial_frame=str(tmp_path / "missing.png"),
        end_frame=None,
        reference_images=None,
    )
    assert media is None
    assert err is not None
    assert err["error"]["title"] == "Invalid Start Image"


class _LocalFile:
    """Stand-in for LocalFileRecord (only the fields the resolver reads)."""

    def __init__(self, path, storage_provider=None):
        self.path = path
        self.storage_provider = storage_provider


class _Asset:
    def __init__(self, local_files, flow_media_id="m", metadata_json=None):
        self.local_files = list(local_files)
        self.flow_media_id = flow_media_id
        self.metadata_json = metadata_json or {}


class _FakeRepo:
    """Minimal stand-in for the single DataRepository method the resolver uses."""

    def __init__(self, asset=None):
        self._asset = asset

    def get_asset_by_any_id(self, profile, ref_id):
        return self._asset


_A_UUID = "550e8400-e29b-41d4-a716-446655440000"


def test_resolve_ref_local_path_returns_on_disk_file(tmp_path: Path) -> None:
    """v0.25.0 #237 fix: a catalogued UUID resolves to its on-disk local file so
    the video attach reuses the proven local-upload path (generated media do not
    surface in Flow's picker search)."""
    from gflow_cli.mcp.tools import _resolve_ref_local_path

    img = tmp_path / "gen.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    repo = _FakeRepo(asset=_Asset([_LocalFile(img)]))
    path, err = _resolve_ref_local_path(repo, "default", _A_UUID)
    assert err is None
    assert path == str(img)


def test_resolve_ref_local_path_unresolvable_uuid_returns_clear_error() -> None:
    """PR #237 review #7: a UUID not in the catalog fails fast with a clear
    'not found' error (no browser round-trip / picker timeout)."""
    from gflow_cli.mcp.tools import _resolve_ref_local_path

    repo = _FakeRepo(asset=None)
    path, err = _resolve_ref_local_path(repo, "default", _A_UUID)
    assert path is None
    assert err is not None
    assert _A_UUID in str(err)
    assert "catalog" in str(err).lower()


def test_resolve_ref_local_path_asset_without_local_file_errors(tmp_path: Path) -> None:
    """An in-catalog asset whose local file is missing on disk fails fast with a
    clear 'not on disk' error (auto-download-by-id is a planned follow-up)."""
    from gflow_cli.mcp.tools import _resolve_ref_local_path

    missing = tmp_path / "pruned.png"  # never written
    repo = _FakeRepo(asset=_Asset([_LocalFile(missing)]))
    path, err = _resolve_ref_local_path(repo, "default", _A_UUID)
    assert path is None
    assert err is not None
    assert _A_UUID in str(err)
    assert "disk" in str(err).lower()


def test_resolve_ref_local_path_skips_cloud_only_files(tmp_path: Path) -> None:
    """Cloud-only rows (storage_provider set, path None/remote) are not usable as
    a local upload; a real on-disk file later in the list is chosen."""
    from gflow_cli.mcp.tools import _resolve_ref_local_path

    img = tmp_path / "gen.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    repo = _FakeRepo(asset=_Asset([_LocalFile(None, storage_provider="gcs"), _LocalFile(img)]))
    path, err = _resolve_ref_local_path(repo, "default", _A_UUID)
    assert err is None
    assert path == str(img)


def test_resolve_payload_refs_i2v_frames_become_local_paths(tmp_path: Path) -> None:
    """i2v: a start/end frame UUID is replaced by its local path (not a *_ref_name),
    so the existing local-upload attach runs and no picker search is attempted."""
    from gflow_cli.mcp.tools import _resolve_payload_refs

    start = tmp_path / "start.png"
    start.write_bytes(b"\x89PNG\r\n\x1a\n")
    repo = _FakeRepo(asset=_Asset([_LocalFile(start)]))
    payload = {"start_image_ref": _A_UUID}
    err = _resolve_payload_refs(repo, "default", payload, task_type="i2v")
    assert err is None
    assert payload["start_image"] == str(start)
    assert "start_image_ref" not in payload
    assert "start_image_ref_name" not in payload


def test_resolve_payload_refs_r2v_refs_merge_into_reference_images(tmp_path: Path) -> None:
    """r2v: UUID refs resolve to local paths appended to reference_images; the raw
    'refs' key is consumed and no 'ref_names' is produced."""
    from gflow_cli.mcp.tools import _resolve_payload_refs

    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n")
    existing = tmp_path / "already_local.png"
    existing.write_bytes(b"\x89PNG\r\n\x1a\n")
    repo = _FakeRepo(asset=_Asset([_LocalFile(ref)]))
    payload = {"refs": [_A_UUID], "reference_images": [str(existing)]}
    err = _resolve_payload_refs(repo, "default", payload, task_type="r2v")
    assert err is None
    assert payload["reference_images"] == [str(existing), str(ref)]
    assert "refs" not in payload
    assert "ref_names" not in payload


def test_resolve_payload_refs_image_uncatalogued_uuid_passes_through() -> None:
    """PR #245: an image 'refs' UUID not in the local catalog must pass through
    without error (still a valid media id to attach in place); video errors."""
    from gflow_cli.mcp.tools import _resolve_payload_refs

    repo = _FakeRepo(asset=None)  # UUID not in local catalog
    payload = {"refs": [_A_UUID]}
    # video task type: resolves (and would error on the missing UUID)
    err_video = _resolve_payload_refs(repo, "default", dict(payload), task_type="r2v")
    assert err_video is not None
    # image task type: passes through, no error, no ref_meta (nothing to enrich)
    p_image = dict(payload)
    err_image = _resolve_payload_refs(repo, "default", p_image, task_type="i2i")
    assert err_image is None
    assert p_image["refs"] == [_A_UUID]
    assert "reference_images" not in p_image
    assert "ref_meta" not in p_image


def test_resolve_payload_refs_image_enriches_found_ref(tmp_path: Path) -> None:
    """An image 'refs' UUID that IS in the catalog is enriched with its
    display_name and on-disk local_path (ref_meta) so the transport can select
    the existing asset in the picker (prefer-existing), refs left in place."""
    from gflow_cli.mcp.tools import _resolve_payload_refs

    img = tmp_path / "gen.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    repo = _FakeRepo(asset=_Asset([_LocalFile(img)], metadata_json={"display_name": "Cozy cabin"}))
    payload = {"refs": [_A_UUID]}
    err = _resolve_payload_refs(repo, "default", payload, task_type="i2i")
    assert err is None
    assert payload["refs"] == [_A_UUID]  # not rewritten
    assert payload["ref_meta"] == {_A_UUID: {"display_name": "Cozy cabin", "local_path": str(img)}}


def test_resolve_payload_refs_image_enrich_partial_meta_only() -> None:
    """Enrichment records only what's available — a catalogued asset with no
    on-disk file contributes just its display_name (picker-select still works;
    no local-upload fallback for that ref)."""
    from gflow_cli.mcp.tools import _resolve_payload_refs

    repo = _FakeRepo(asset=_Asset([], metadata_json={"display_name": "Named but pruned"}))
    payload = {"refs": [_A_UUID]}
    err = _resolve_payload_refs(repo, "default", payload, task_type="i2i")
    assert err is None
    assert payload["ref_meta"] == {_A_UUID: {"display_name": "Named but pruned"}}


def test_format_mcp_error_with_detail_and_remediation() -> None:
    from gflow_cli.errors import ContentPolicyError
    from gflow_cli.mcp.tools import _format_mcp_error, _gflow_error_dict

    exc = ContentPolicyError("Prompt violates policy")
    formatted = _format_mcp_error(exc)
    expected = f"[ContentPolicyError] Prompt violates policy (Remediation: {exc.remediation_hint})"
    assert formatted == expected

    err_dict = _gflow_error_dict(exc)
    assert err_dict["message"] == formatted
    assert err_dict["remediation_hint"] == exc.remediation_hint


def test_format_mcp_error_fallback_to_title_when_detail_empty() -> None:
    from gflow_cli.errors import WireFormatError
    from gflow_cli.mcp.tools import _format_mcp_error

    exc = WireFormatError("")
    formatted = _format_mcp_error(exc)
    expected = f"[WireFormatError] {exc.title} (Remediation: {exc.remediation_hint})"
    assert formatted == expected


def test_format_mcp_error_without_remediation_hint() -> None:
    from gflow_cli.errors import GFlowError
    from gflow_cli.mcp.tools import _format_mcp_error

    exc = GFlowError("Something failed", remediation_hint="")
    formatted = _format_mcp_error(exc)
    assert formatted == "[GFlowError] Something failed"
    assert "(Remediation:" not in formatted
