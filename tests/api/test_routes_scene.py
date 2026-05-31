import pytest

from gflow_cli.api import routes


def test_scenes_url_interpolates_validated_project_id():
    url = routes.scenes_url("proj-123")
    assert url == "https://aisandbox-pa.googleapis.com/v1/flow/projects/proj-123/scenes"


def test_scenes_url_rejects_injection():
    with pytest.raises(ValueError):
        routes.scenes_url("../evil")


def test_scene_workflows_url():
    # Flow requires BOTH sceneId + projectId query params or the GET returns {}.
    url = routes.scene_workflows_url("scene-abc", "proj-123")
    assert url == (
        "https://aisandbox-pa.googleapis.com/v1/flow/scene/scene-abc/workflows"
        "?sceneId=scene-abc&projectId=proj-123"
    )


def test_scene_workflows_url_rejects_injection():
    with pytest.raises(ValueError):
        routes.scene_workflows_url("../evil", "proj-123")
    with pytest.raises(ValueError):
        routes.scene_workflows_url("scene-abc", "../evil")


def test_scene_workflows_update_url_is_constant():
    assert routes.SCENE_WORKFLOWS_UPDATE.endswith("/v1/flow/scene/sceneWorkflows:update")


def test_concatenation_urls_are_top_level_v1_methods():
    assert routes.RUN_VIDEO_FX_CONCATENATION.endswith("/v1:runVideoFxConcatenation")
    assert routes.RUN_VIDEO_FX_CHECK_CONCATENATION_STATUS.endswith(
        "/v1:runVideoFxCheckConcatenationStatus"
    )


def test_flow_workflow_url():
    assert (
        routes.flow_workflow_url("wf-1")
        == "https://aisandbox-pa.googleapis.com/v1/flowWorkflows/wf-1"
    )
