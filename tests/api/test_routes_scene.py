import pytest

from gflow_cli.api import routes


def test_scenes_url_interpolates_validated_project_id():
    url = routes.scenes_url("proj-123")
    assert url == "https://aisandbox-pa.googleapis.com/v1/flow/projects/proj-123/scenes"


def test_scenes_url_rejects_injection():
    with pytest.raises(ValueError):
        routes.scenes_url("../evil")


def test_scene_workflows_url():
    url = routes.scene_workflows_url("scene-abc")
    assert url == "https://aisandbox-pa.googleapis.com/v1/flow/scene/scene-abc/workflows"


def test_scene_workflows_update_url_is_constant():
    assert routes.SCENE_WORKFLOWS_UPDATE.endswith("/v1/flow/scene/sceneWorkflows:update")


def test_flow_workflow_url():
    assert (
        routes.flow_workflow_url("wf-1")
        == "https://aisandbox-pa.googleapis.com/v1/flowWorkflows/wf-1"
    )
