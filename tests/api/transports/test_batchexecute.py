"""batchexecute envelope + generation-record parser (migrated flow.google.com host).

Fixtures are the 2026-09-05 captures (spike_migrated_submit_capture.py) with ids
replaced by synthetic uuids and the signed CDN URLs by a placeholder — the SHAPE is
what the parser keys on, never the values.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gflow_cli.errors import WireFormatError

WF = "11111111-1111-4111-8111-111111111111"
PROJ = "22222222-2222-4222-8222-222222222222"
MEDIA = "33333333-3333-4333-8333-333333333333"
PROMPT = "a teal origami crane on a wooden table gflowcanary0000000000"
VIDEO_URL = (
    "https://flow-content.google/v/abc.mp4?Expires=1&KeyName=labs-flow-prod-cdn-key&Signature=s"
)
POSTER_URL = (
    "https://flow-content.google/p/abc.jpg?Expires=1&KeyName=labs-flow-prod-cdn-key&Signature=t"
)


def _record(status: int, *, done_urls: bool = False, size: int | None = None) -> list[Any]:
    """The record shared by YhhmEf / jwpduf / as29s, exactly as captured."""
    details: list[Any] = [
        [1788563121, 855319000],
        PROMPT,
        None,
        None,
        None,
        None,
        [None, [["abra_t2v_8s", 1, None, None, 2, 1]], [[None, None, [[[PROMPT]]]]], None, 1],
        None,
        [status],
        1,
    ]
    if done_urls:
        details += [POSTER_URL, [], None, size]
    elif size is not None:
        details += [None, None, None, size]
    media_info: list[Any] = [
        [
            None,
            926545,
            None,
            None,
            None,
            None,
            None,
            PROMPT,
            VIDEO_URL if done_urls else None,
            None,
            None,
            None,
            "abra_t2v_8s",
            "",
            None,
            False,
            2,
        ],
        [None, None, [8]],
        [WF],
    ]
    return [WF, PROJ, MEDIA, "CAE", None, details, None, media_info]


def _envelope(frames: list[tuple[str, Any]], *, total: int = 1042) -> str:
    """`)]}'` + chunk-length lines + wrb.fr frames, as the wire sends it."""
    chunks = []
    for rpcid, payload in frames:
        wrb = ["wrb.fr", rpcid, json.dumps(payload), None, None, None, "generic"]
        frame = [wrb, ["di", 4233]]
        body = json.dumps(frame)
        chunks.append(f"{len(body) + 1}\n{body}")
    tail = json.dumps([["e", 4, None, None, total]])
    return ")]}'\n\n" + "\n".join(chunks) + f"\n{len(tail) + 1}\n{tail}\n"


def submit_payload(status: int = 6) -> Any:
    """YhhmEf wraps the record: [null, 881, [[media, ..., project]], [[record]]]."""
    return [
        None,
        881,
        [
            [
                MEDIA,
                None,
                None,
                ["Teal origami crane on table", [1, 2], None, None, WF, "X", [1, 2]],
                PROJ,
            ]
        ],
        [[_record(status)]],
    ]


def poll_payload(status: int, **kw: Any) -> Any:
    """jwpduf: [null, 881|null, [[record]]]."""
    return [None, 881, [[_record(status, **kw)]]]


# --- frames ---------------------------------------------------------------


def test_parse_frames_returns_rpcid_and_decoded_payload() -> None:
    from gflow_cli.api.transports.batchexecute import parse_frames

    frames = parse_frames(_envelope([("YhhmEf", submit_payload())]))
    assert [r for r, _ in frames] == ["YhhmEf"]
    assert frames[0][1][1] == 881


def test_parse_frames_handles_multiple_frames_and_ignores_length_lines() -> None:
    from gflow_cli.api.transports.batchexecute import parse_frames

    text = _envelope([("jwpduf", poll_payload(2)), ("WuwhI", [])])
    assert [r for r, _ in parse_frames(text)] == ["jwpduf", "WuwhI"]


def test_parse_frames_on_non_envelope_text_is_empty() -> None:
    from gflow_cli.api.transports.batchexecute import parse_frames

    assert parse_frames("") == []
    assert parse_frames("<html>login</html>") == []


# --- record ---------------------------------------------------------------


def test_submit_record_carries_ids_and_submitted_status() -> None:
    from gflow_cli.api.transports.batchexecute import generation_record

    rec = generation_record("YhhmEf", submit_payload())
    assert (rec.workflow_id, rec.project_id, rec.media_id) == (WF, PROJ, MEDIA)
    assert rec.status == 6
    assert rec.is_running and not rec.is_done and not rec.is_failed
    assert rec.video_url is None


def test_poll_running_then_done_without_url() -> None:
    from gflow_cli.api.transports.batchexecute import generation_record

    running = generation_record("jwpduf", poll_payload(2))
    assert running.is_running and running.size_bytes is None
    done = generation_record("jwpduf", poll_payload(3, size=2213107))
    assert done.is_done and done.video_url is None and done.size_bytes == 2213107


def test_result_record_carries_signed_urls() -> None:
    from gflow_cli.api.transports.batchexecute import generation_record

    rec = generation_record("as29s", _record(3, done_urls=True, size=2213107))
    assert rec.is_done
    assert rec.video_url == VIDEO_URL
    assert rec.poster_url == POSTER_URL
    assert rec.size_bytes == 2213107


def test_unknown_status_is_failed_and_keeps_the_raw_value() -> None:
    from gflow_cli.api.transports.batchexecute import generation_record

    rec = generation_record("jwpduf", poll_payload(7))
    assert rec.is_failed and not rec.is_done and not rec.is_running
    assert rec.status == 7


def test_drift_raises_wire_format_error_with_redacted_discovery_head() -> None:
    from gflow_cli.api.transports.batchexecute import generation_record

    token = "0cAF" + "x" * 2400
    with pytest.raises(WireFormatError) as exc_info:
        generation_record("YhhmEf", [None, 881, [["not", "a", "record", token]]])
    msg = str(exc_info.value)
    assert "YhhmEf" in msg
    assert token not in msg
    assert len(msg) < 600


def test_record_matches_by_shape_not_position() -> None:
    """A future wrapper that nests the record one level deeper must still resolve."""
    from gflow_cli.api.transports.batchexecute import generation_record

    rec = generation_record("YhhmEf", [[[[_record(2)]]]])
    assert rec.workflow_id == WF and rec.is_running
