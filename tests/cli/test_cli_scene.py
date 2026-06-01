from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli import cli_scene
from gflow_cli.api.scene import Scene, SceneWorkflow, SceneWorkflowMetadata
from gflow_cli.cli import main
from gflow_cli.cli_scene import ClipRef, _parse_clip_ref, _validate_trim


def _one_clip_scene(scene_id="s1", project_id="proj-1"):
    clip = SceneWorkflow("inst-1", SceneWorkflowMetadata(0, 0.0, 8.0, 8.0), media_id="m1")
    return Scene(scene_id=scene_id, project_id=project_id, workflows=(clip,))


class _FakeClient:
    """Async-CM stand-in for FlowApiClient covering the scene-compose calls."""

    def __init__(self, calls, **_kw):
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def create_scene(self, *, project_id, workflow_ids):
        self._calls["create"] = list(workflow_ids)
        return _one_clip_scene(project_id=project_id)

    async def get_scene_workflows(self, scene_id, *, project_id):
        return _one_clip_scene(scene_id=scene_id, project_id=project_id)

    async def concatenate_scene(self, inputs, *, out_path, **_kw):
        self._calls["concat_inputs"] = list(inputs)
        Path(out_path).write_bytes(b"\x00\x00\x00\x18ftypisom")
        return Path(out_path)


class _FakeRecorder:
    def __init__(self, calls):
        self._calls = calls

    def record_scene(self, **_kw):
        self._calls["record_scene"] = True
        return "row-1"

    def record_scene_output(self, *, scene_row_id, output_path):
        self._calls["record_output"] = (scene_row_id, output_path)

    def close(self):
        self._calls["closed"] = True


def _patch_run_create(monkeypatch, calls):
    monkeypatch.setattr(cli_scene, "FlowApiClient", lambda **kw: _FakeClient(calls, **kw))
    monkeypatch.setattr(
        cli_scene.OperationRecorder, "open", classmethod(lambda cls, _s: _FakeRecorder(calls))
    )
    monkeypatch.setattr(cli_scene, "get_settings", lambda: type("S", (), {"headless": True})())


async def test_run_create_with_output_records_before_render(tmp_path, monkeypatch):
    calls: dict = {}
    _patch_run_create(monkeypatch, calls)
    out = tmp_path / "extended.mp4"
    await cli_scene._run_create(
        profile_name="p",
        profile_dir=tmp_path,
        headless=True,
        project_id="proj-1",
        refs=[ClipRef("wf-1", None, None)],
        output=out,
    )
    # compose recorded, THEN extended rendered, THEN output attached to that row
    assert calls["record_scene"] is True
    assert calls["concat_inputs"] and calls["concat_inputs"][0].media_id == "m1"
    assert calls["record_output"] == ("row-1", str(out))
    assert out.exists() and calls["closed"] is True


async def test_run_create_without_output_skips_concat(tmp_path, monkeypatch):
    calls: dict = {}
    _patch_run_create(monkeypatch, calls)
    await cli_scene._run_create(
        profile_name="p",
        profile_dir=tmp_path,
        headless=True,
        project_id="proj-1",
        refs=[ClipRef("wf-1", None, None)],
        output=None,
    )
    assert calls["record_scene"] is True
    assert "concat_inputs" not in calls  # no render without --output
    assert "record_output" not in calls


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
