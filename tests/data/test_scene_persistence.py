import uuid

from gflow_cli.api.scene import Scene, SceneWorkflow, SceneWorkflowMetadata
from gflow_cli.data.models import OperationKind, SceneClipRecord, SceneRecord
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore

# PromptMode is a Literal["store", "redacted"], not a StrEnum, so there is no
# PromptMode.HASH member; use the hashing/redacting variant directly.
_PROMPT_MODE = "redacted"


def test_migration_0003_creates_scene_tables(tmp_path):
    store = DataStore.open(tmp_path / "t.db")
    try:
        cur = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('scenes','scene_clips')"
        )
        assert {r[0] for r in cur.fetchall()} == {"scenes", "scene_clips"}
    finally:
        store.close()


def _repo(tmp_path):
    repo = DataRepository(DataStore.open(tmp_path / "t.db"))
    repo.upsert_profile("p", tmp_path)
    return repo


def test_upsert_scene_and_replace_clips_roundtrip(tmp_path):
    repo = _repo(tmp_path)
    sid = str(uuid.uuid4())
    repo.upsert_scene(
        SceneRecord(
            id=sid,
            profile_name="p",
            flow_project_id="proj-1",
            flow_scene_id="scene-x",
            total_duration=13.2,
            source="composed",
        )
    )
    clips = [
        SceneClipRecord(
            id=str(uuid.uuid4()),
            scene_id=sid,
            position=0,
            flow_instance_workflow_id="inst-1",
            flow_source_workflow_id="wf-a",
            flow_media_id="m1",
            start_time=0.0,
            end_time=8.0,
            total_duration=8.0,
        ),
        SceneClipRecord(
            id=str(uuid.uuid4()),
            scene_id=sid,
            position=1,
            flow_instance_workflow_id="inst-2",
            flow_source_workflow_id="wf-b",
            flow_media_id="m2",
            start_time=3.2,
            end_time=5.2,
            total_duration=8.0,
        ),
    ]
    repo.replace_scene_clips(sid, clips)
    got = repo.get_scene_by_flow_scene_id("p", "scene-x")
    assert got is not None and got.id == sid
    back = repo.get_scene_clips(sid)
    assert [c.position for c in back] == [0, 1]
    assert back[0].flow_source_workflow_id == "wf-a" and back[0].flow_media_id == "m1"
    repo.replace_scene_clips(sid, back)  # idempotent
    assert len(repo.get_scene_clips(sid)) == 2
    repo.store.close()


def test_record_scene_persists_scene_clips_and_operation(tmp_path):
    rec = OperationRecorder(
        DataRepository(DataStore.open(tmp_path / "t.db")), prompt_mode=_PROMPT_MODE
    )
    scene = Scene(
        scene_id="scene-x",
        project_id="proj-1",
        workflows=(
            SceneWorkflow("inst-1", SceneWorkflowMetadata(0, 0.0, 8.0, 8.0), media_id="m1"),
            SceneWorkflow("inst-2", SceneWorkflowMetadata(1, 3.2, 5.2, 8.0), media_id="m2"),
        ),
    )
    rec.record_scene(
        profile_name="p",
        profile_dir=tmp_path,
        scene=scene,
        operation_kind=OperationKind.SCENE_CREATE,
        source_workflow_ids=["wf-a", "wf-b"],
    )
    repo = rec.repository
    got = repo.get_scene_by_flow_scene_id("p", "scene-x")
    assert got is not None
    saved = repo.get_scene_clips(got.id)
    assert len(saved) == 2
    assert saved[0].flow_source_workflow_id == "wf-a"
    assert saved[0].flow_media_id == "m1"
    rec.close()
