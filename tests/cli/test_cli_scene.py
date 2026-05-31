import pytest
from click.testing import CliRunner

from gflow_cli.cli import main
from gflow_cli.cli_scene import ClipRef, _parse_clip_ref, _validate_trim


def test_parse_clip_ref_no_trim():
    assert _parse_clip_ref("wf-123") == ClipRef("wf-123", None, None)


def test_parse_clip_ref_with_trim():
    assert _parse_clip_ref("wf-123:3.2-5.2") == ClipRef("wf-123", 3.2, 5.2)


def test_parse_clip_ref_bad_trim_raises():
    with pytest.raises(ValueError):
        _parse_clip_ref("wf-123:5-3")


def test_validate_trim_rejects_out_of_range():
    with pytest.raises(ValueError):
        _validate_trim(start=0.0, end=9.0, total=8.0)


def test_validate_trim_accepts_valid():
    _validate_trim(start=0.0, end=8.0, total=8.0)


def test_scene_group_registered():
    res = CliRunner().invoke(main, ["scene", "--help"])
    assert res.exit_code == 0
    assert "create" in res.output and "show" in res.output


def test_create_bad_clip_ref_is_usage_error_not_traceback():
    # A malformed clipRef must surface as a Click usage error (exit 2), not an
    # uncaught ValueError traceback (exit 1). Parse fails before any Flow work.
    res = CliRunner().invoke(main, ["scene", "create", "--project", "p-1", "wf-123:5-3"])
    assert res.exit_code == 2
    assert "CLIP_REFS" in res.output
    assert not isinstance(res.exception, ValueError)


def test_show_help_lists_option_descriptions():
    res = CliRunner().invoke(main, ["scene", "show", "--help"])
    assert res.exit_code == 0
    assert "Scene id to read back." in res.output
    assert "Flow project id." in res.output


def test_create_help_documents_output_render():
    res = CliRunner().invoke(main, ["scene", "create", "--help"])
    assert res.exit_code == 0
    assert "--output" in res.output and "extended" in res.output
    assert "--force" in res.output


def test_create_output_overwrite_guard(tmp_path):
    existing = tmp_path / "extended.mp4"
    existing.write_bytes(b"old")
    res = CliRunner().invoke(
        main, ["scene", "create", "--project", "p-1", "wf-1", "--output", str(existing)]
    )
    # overwrite guard fires BEFORE any network work -> Click usage error, exit 2
    assert res.exit_code == 2
    assert "already exists" in res.output
    assert existing.read_bytes() == b"old"  # untouched
