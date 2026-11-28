"""#1542 — the worker marks catalogued i2v frame UUIDs ``in_project`` for their own project.

The MCP layer records each i2v frame's catalog project in ``start_image_ref_flow_project_id``
and ``end_image_ref_flow_project_id`` (see ``mcp.tools._resolve_payload_refs``);
``build_video_request`` compares each with the task's ``project_id`` via
``recorded_in_project`` so CLI and MCP route the same UUID frame identically.
"""

from __future__ import annotations

from gflow_cli.worker.codec import build_video_request

_START_UUID = "11111111-1111-4111-8111-111111111111"
_END_UUID = "22222222-2222-4222-8222-222222222222"


def _payload(
    *,
    project_id: str | None,
    start_recorded: str | None,
    end_recorded: str | None = None,
    with_end: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "prompt": "slow camera push",
        "mode": "i2v",
        "aspect": "9:16",
        "start_image_ref": _START_UUID,
        "start_image_ref_display_name": "Stone corridor",
    }
    if start_recorded is not None:
        payload["start_image_ref_flow_project_id"] = start_recorded
    if with_end:
        payload["end_image_ref"] = _END_UUID
        payload["end_image_ref_display_name"] = "Warm lamp"
        if end_recorded is not None:
            payload["end_image_ref_flow_project_id"] = end_recorded
    if project_id is not None:
        payload["project_id"] = project_id
    return payload


def test_start_and_end_refs_recorded_in_task_project_are_in_project() -> None:
    request = build_video_request(
        _payload(
            project_id="proj-1",
            start_recorded="proj-1",
            end_recorded="proj-1",
            with_end=True,
        )
    )
    assert request.start_image_ref_in_project is True
    assert request.end_image_ref_in_project is True


def test_ref_from_another_project_is_not_in_project() -> None:
    request = build_video_request(
        _payload(
            project_id="proj-1",
            start_recorded="proj-1",
            end_recorded="proj-2",
            with_end=True,
        )
    )
    assert request.start_image_ref_in_project is True
    assert request.end_image_ref_in_project is False


def test_task_without_project_is_not_in_project() -> None:
    request = build_video_request(_payload(project_id=None, start_recorded="proj-1"))
    assert request.start_image_ref_in_project is False


def test_ref_without_recorded_project_is_not_in_project() -> None:
    request = build_video_request(_payload(project_id="proj-1", start_recorded=None))
    assert request.start_image_ref_in_project is False
