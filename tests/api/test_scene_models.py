import json
import pathlib

from gflow_cli.api.scene import Scene, SceneWorkflow, SceneWorkflowMetadata

_FIXTURES = pathlib.Path(__file__).parents[2] / "samples" / "captured"

def test_duration_to_wire_whole_seconds():
    m = SceneWorkflowMetadata(position=0, start_time=0.0, end_time=8.0, total_duration=8.0)
    w = m.to_wire()
    assert w["startTime"] == "0s"
    assert w["endTime"] == "8s"
    assert w["position"] == 0
    assert w["totalDuration"] == "8s"

def test_duration_to_wire_fractional_keeps_nano_precision():
    m = SceneWorkflowMetadata(position=1, start_time=3.22666687, end_time=5.0, total_duration=8.0)
    assert m.to_wire()["startTime"] == "3.226666870s"

def test_scene_workflow_to_wire_nests_instance_id():
    sw = SceneWorkflow(
        workflow_id="inst-1",
        metadata=SceneWorkflowMetadata(
            position=0, start_time=0.0, end_time=8.0, total_duration=8.0
        ),
    )
    wire = sw.to_wire(scene_id="scene-x")
    assert wire["sceneId"] == "scene-x"
    assert wire["workflow"]["name"] == "inst-1"
    assert wire["sceneWorkflowMetadata"]["endTime"] == "8s"

def _load(fixture_name):
    raw = json.loads((_FIXTURES / fixture_name).read_text())
    return json.loads(raw["response_body"])

def test_scene_from_create_response_parses_sceneid_and_instances():
    data = _load("12_create_scene.json")
    scene = Scene.from_create_response(data, project_id="proj-1")
    assert scene.scene_id
    assert scene.project_id == "proj-1"
    assert len(scene.workflows) >= 1
    assert all(w.workflow_id for w in scene.workflows)

def test_scene_from_get_response_parses_order_and_trims():
    data = _load("14_get_scene_workflows.json")
    scene = Scene.from_get_response(data, scene_id="scene-x", project_id="proj-1")
    positions = [w.metadata.position for w in scene.workflows]
    assert positions == sorted(positions)
