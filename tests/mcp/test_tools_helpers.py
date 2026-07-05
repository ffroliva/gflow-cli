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


class _FakeRepo:
    """Minimal stand-in for DataRepository's two methods _resolve_ref_name uses."""

    def __init__(self, asset=None, seed=None):
        self._asset = asset
        self._seed = seed

    def get_asset_by_any_id(self, profile, ref_id):
        return self._asset

    def resolve_seed_image(self, profile, flow_media_id):
        return self._seed


class _Asset:
    def __init__(self, metadata_json, flow_media_id="m"):
        self.metadata_json = metadata_json
        self.flow_media_id = flow_media_id


class _Seed:
    def __init__(self, prompt):
        self.prompt = prompt


_A_UUID = "550e8400-e29b-41d4-a716-446655440000"


def test_resolve_ref_name_uses_display_name() -> None:
    from gflow_cli.mcp.tools import _resolve_ref_name

    repo = _FakeRepo(asset=_Asset({"display_name": "A cozy cabin"}))
    name, err = _resolve_ref_name(repo, "default", _A_UUID)
    assert err is None
    assert name == "A cozy cabin"


def test_resolve_ref_name_falls_back_to_seed_prompt() -> None:
    from gflow_cli.mcp.tools import _resolve_ref_name

    repo = _FakeRepo(asset=_Asset({}), seed=_Seed("an old prompt"))
    name, err = _resolve_ref_name(repo, "default", _A_UUID)
    assert err is None
    assert name == "an old prompt"


def test_resolve_ref_name_unresolvable_uuid_returns_clear_error() -> None:
    """PR #237 review #7: a UUID not in the catalog must fail fast with a clear
    'not found' error instead of being passed downstream as a search term
    (which surfaced as a ~120s Playwright timeout)."""
    from gflow_cli.mcp.tools import _resolve_ref_name

    repo = _FakeRepo(asset=None)
    name, err = _resolve_ref_name(repo, "default", _A_UUID)
    assert name is None
    assert err is not None
    assert _A_UUID in str(err)
    assert "catalog" in str(err).lower()


def test_resolve_ref_name_passes_through_a_literal_display_name() -> None:
    """A non-UUID string is a display name the user typed directly — keep it."""
    from gflow_cli.mcp.tools import _resolve_ref_name

    repo = _FakeRepo(asset=None)
    name, err = _resolve_ref_name(repo, "default", "A cozy cabin")
    assert err is None
    assert name == "A cozy cabin"
