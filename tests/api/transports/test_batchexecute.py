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


def test_a_token_cut_by_the_head_window_leaves_no_fragment() -> None:
    """The head was once sliced to 200 chars before the token regex ran, so a token
    starting near the cut survived as a fragment below that regex's 120-char floor.
    Placed so ~90 chars of it fall inside the window; none may reach the error."""
    from gflow_cli.api.transports.batchexecute import generation_record

    token = "0cAFcWe" + "Q7_x-" * 400
    payload = [None, 881, [["p" * 90, token]]]
    with pytest.raises(WireFormatError) as exc_info:
        generation_record("maseQ", payload)
    head = str(exc_info.value.discovery["payload_head"])
    assert "Q7_x-Q7_x-" not in head
    assert "0cAF" not in head


def test_a_long_bearer_token_in_a_head_is_redacted_whole() -> None:
    """CodeRabbit on #1302: the generic token rule ran before the Bearer rule, so it
    replaced the first run and the Bearer rule could not reach the tail after the dot."""
    from gflow_cli.api.transports.batchexecute import generation_record

    payload = [None, 881, [["Bearer " + "A" * 150 + "." + "B" * 80]]]
    with pytest.raises(WireFormatError) as exc_info:
        generation_record("maseQ", payload)
    assert "BBBB" not in str(exc_info.value.discovery["payload_head"])


def test_a_token_cut_at_the_scrub_window_cannot_slide_into_the_head() -> None:
    """CodeRabbit on #1302: the head scrubs only the first 4 KB. A token cut there was
    a fragment below every floor, and once the long runs before it collapsed to
    `<token>`, it moved into the 200-char head."""
    from gflow_cli.api.transports.batchexecute import generation_record

    filler = ["Z" * 2000] * 2  # two long runs: ~4 KB that collapse to ~30 chars
    secret = "0cAFcWe" + "Q7_x-" * 400
    payload = [None, 881, [[*filler, secret]]]
    with pytest.raises(WireFormatError) as exc_info:
        generation_record("maseQ", payload)
    head = str(exc_info.value.discovery["payload_head"])
    assert "Q7_x" not in head
    assert "0cAF" not in head


def test_an_at_value_cut_inside_its_timestamp_cannot_slide_into_the_head() -> None:
    """CodeRabbit on #1302: a cut through the 13-digit timestamp left `<28 chars>:` plus
    digits; stripping token chars stopped at the colon, so the XSRF prefix survived."""
    from gflow_cli.api.transports.batchexecute import generation_record

    secret = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"  # 28 chars, the measured `at` length
    # the 4 KB cut lands 12 digits into the timestamp
    payload = [None, 881, [["Z" * 2000, "Z" * 2032, secret + ":1730000000000"]]]
    with pytest.raises(WireFormatError) as exc_info:
        generation_record("maseQ", payload)
    assert secret[:12] not in str(exc_info.value.discovery["payload_head"])


def test_a_head_that_json_cannot_encode_is_still_scrubbed() -> None:
    from gflow_cli.api.transports.batchexecute import generation_record

    token = "0cAFcWe" + "Q7_x-" * 40
    payload = [None, 881, [[{1, 2}, token]]]  # a set: json.dumps raises, repr is used
    with pytest.raises(WireFormatError) as exc_info:
        generation_record("maseQ", payload)
    head = str(exc_info.value.discovery["payload_head"])
    assert "{1, 2}" in head
    assert "Q7_x-Q7_x-" not in head


def test_a_huge_unmatched_frame_builds_its_head_fast() -> None:
    """Review of slice 1: two status observers build a WireFormatError for every frame
    that does not match and drop it, so the head must not scrub a whole listing (459 KB
    measured)."""
    import time

    from gflow_cli.api.transports.batchexecute import generation_record

    payload = [None, 881, [["x@" + "a." * 20_000]]]  # HEAD before the fix: 7.6 s
    started = time.perf_counter()
    with pytest.raises(WireFormatError):
        generation_record("jwpduf", payload)
    assert time.perf_counter() - started < 0.5


def test_record_matches_by_shape_not_position() -> None:
    """A future wrapper that nests the record one level deeper must still resolve."""
    from gflow_cli.api.transports.batchexecute import generation_record

    rec = generation_record("YhhmEf", [[[[_record(2)]]]])
    assert rec.workflow_id == WF and rec.is_running


def test_record_with_null_marker_is_still_a_record_948() -> None:
    """Measured 2026-10-06: Flow now sends null where "CAE" was, on submit and status."""
    from gflow_cli.api.transports.batchexecute import generation_record

    rec = _record(2)
    rec[3] = None
    decoy = [WF, PROJ, MEDIA, None, None, "not details"]  # three uuids, no DETAILS list
    parsed = generation_record("jwpduf", [None, None, [[decoy], [rec]]])
    assert (parsed.workflow_id, parsed.media_id, parsed.status) == (WF, MEDIA, 2)

    rec[3] = "CAF"  # the marker's next value must not strand a billed run either
    assert generation_record("jwpduf", [rec]).media_id == MEDIA


def test_a_wanted_id_skips_another_clips_record_listed_first() -> None:
    """A project-wide poll can list another clip first; first-match hid ours (#948 timeout)."""
    from gflow_cli.api.transports.batchexecute import generation_record

    other = _record(2)
    other[0] = "99999999-9999-4999-8999-999999999999"
    other[2] = "88888888-8888-4888-8888-888888888888"
    payload = [None, None, [[other], [_record(3)]]]
    assert generation_record("jwpduf", payload, workflow_id=WF).status == 3
    assert generation_record("as29s", payload, media_id=MEDIA).workflow_id == WF
    assert generation_record("jwpduf", payload).workflow_id == other[0]  # no filter: first


def test_missing_record_warns_that_the_submit_may_be_billed_948() -> None:
    """A blind retry of a billed submit bills twice; the remediation must not invite it."""
    from gflow_cli.api.transports.batchexecute import generation_record

    with pytest.raises(WireFormatError) as exc_info:
        generation_record("YhhmEf", [None, 881, [[MEDIA, None, None, ["t"], PROJ]]])
    hint = exc_info.value.remediation_hint
    assert "billed" in hint and "simpler prompt" not in hint


# --- error envelopes: a refusal is a frame with a null payload -----------------------
#
# Captured 2026-09-27 on ``ogiZ0b`` with a tampered reCAPTCHA token (spike
# 2026-09-27-migrated-refusal-is-on-the-wire). HTTP 200; the reason rides in slot 5.
REFUSAL = (
    ")]}'\n\n192\n"
    '[["wrb.fr","ogiZ0b",null,null,null,[7,null,[["type.googleapis.com/google.rpc.ErrorInfo",'
    '["PUBLIC_ERROR_UNUSUAL_ACTIVITY"]]]],"generic"],["di",240],'
    '["af.httprm",239,"-4624648772470899085",5]]\n25\n[["e",4,null,null,228]]\n'
)


def test_rpc_errors_reads_the_status_and_reason_of_a_refusal() -> None:
    from gflow_cli.api.transports.batchexecute import RpcError, rpc_errors

    assert rpc_errors(REFUSAL) == [RpcError("ogiZ0b", 7, ("PUBLIC_ERROR_UNUSUAL_ACTIVITY",))]


def test_a_refusal_yields_no_payload_frame() -> None:
    from gflow_cli.api.transports.batchexecute import parse_frames

    assert parse_frames(REFUSAL) == []


def test_rpc_errors_on_a_bare_status_has_no_reason() -> None:
    """The #723 shape: Flow queued the job, the reply still says [5]."""
    from gflow_cli.api.transports.batchexecute import RpcError, rpc_errors

    body = ')]}\'\n[["wrb.fr","MZZa6b",null,null,null,[5],"generic"]]\n'
    assert rpc_errors(body) == [RpcError("MZZa6b", 5, ())]


def test_rpc_errors_ignores_payload_frames_and_non_envelopes() -> None:
    from gflow_cli.api.transports.batchexecute import rpc_errors

    ok = ')]}\'\n[["wrb.fr","YhhmEf","[1]",null,null,null,"generic"]]\n'
    assert rpc_errors(ok) == []
    assert rpc_errors("<html>login</html>") == []
    assert rpc_errors("") == []


# --- records carrying an uploaded id (#1112) ------------------------------

REF = "44444444-4444-4444-8444-444444444444"


def _r2v_record(status: int, *, wf: str, media: str, ref: str | None) -> list[Any]:
    """An r2v record as Zzl0ze / jwpduf list it: the request at [5][6][1] holds the
    uploaded reference as ``[null, 4, id]`` and the prompt chips repeat it at [5][6][2]."""
    rec = _record(status)
    rec[0], rec[2] = wf, media
    refs = [[None, 4, ref]] if ref else []
    chips = [[None, [[ref, "blue_card.png"]]]] if ref else []
    rec[5][6] = [
        None,
        [["abra_r2v_8s", 3, [5], None, 2, 1], refs, None, None, None, None, None, None, []],
        [[None, None, [[*chips, [PROMPT]]]]],
        None,
        1,
    ]
    return rec


def test_records_carrying_an_uploaded_id_skip_another_clip_and_the_image_1112() -> None:
    """A poll right after a lost submit listed an older clip and the uploaded image's own
    record beside ours (spike 2026-10-27); only ours carries our id in its request."""
    from gflow_cli.api.transports.batchexecute import generation_records_carrying

    older = _r2v_record(3, wf="99999999-9999-4999-8999-999999999999", media=MEDIA, ref=None)
    ours = _r2v_record(2, wf=WF, media="55555555-5555-4555-8555-555555555555", ref=REF)
    image = [REF, PROJ, "66666666-6666-4666-8666-666666666666", None, None, [[1, 2], None]]
    payload = [None, None, [[older], [image], [ours]]]
    got = generation_records_carrying("jwpduf", payload, (REF,))
    assert [(r.workflow_id, r.status) for r in got] == [(WF, 2)]


def test_an_id_only_in_the_prompt_chips_is_not_a_match_1112() -> None:
    from gflow_cli.api.transports.batchexecute import generation_records_carrying

    rec = _r2v_record(2, wf=WF, media=MEDIA, ref=REF)
    rec[5][6][1][1] = []  # the request no longer names it; the chip text still does
    assert generation_records_carrying("jwpduf", [None, None, [[rec]]], (REF,)) == []


def test_no_ids_match_nothing_1112() -> None:
    from gflow_cli.api.transports.batchexecute import generation_records_carrying

    rec = _r2v_record(2, wf=WF, media=MEDIA, ref=REF)
    assert generation_records_carrying("jwpduf", [None, None, [[rec]]], ()) == []


# --- #1354: an /asb/ tile names its record by the poster token ------------------------------


@pytest.mark.parametrize(
    ("url", "token"),
    [
        ("https://flow.google.com/asb/ANqvTOKEN", "ANqvTOKEN"),
        ("https://flow.google.com/asb/ANqvTOKEN=s512-rw", "ANqvTOKEN"),
        ("https://lh3.googleusercontent.com/asb/ANqv_x-Y1=w286-h512", "ANqv_x-Y1"),
        ("https://flow-content.google/video/33333333-3333-4333-8333-333333333333", None),
        ("https://flow.google.com/asb/", None),
        ("", None),
        (None, None),
        ("https://[::1/asb/ANqvTOKEN", None),  # urlsplit raises on the unclosed bracket
    ],
)
def test_asb_token_is_the_id_without_its_size_spec(url: str | None, token: str | None) -> None:
    from gflow_cli.api.transports.batchexecute import asb_token

    assert asb_token(url) == token


def _poster_record(media: str, token: str, created: int, status: int = 3) -> list[Any]:
    rec = _record(status, done_urls=True)
    rec[2] = media
    rec[5][0][0] = created
    rec[5][10] = f"https://lh3.googleusercontent.com/asb/{token}=s512"
    return rec


def test_the_record_whose_poster_shares_the_tile_token_is_found_1354() -> None:
    from gflow_cli.api.transports.batchexecute import generation_record_by_poster

    other = _poster_record("44444444-4444-4444-8444-444444444444", "ANqvOLD", 100)
    ours = _poster_record(MEDIA, "ANqvNEW", 200)
    found = generation_record_by_poster("Zzl0ze", [None, [[other], [ours]]], "ANqvNEW")
    assert found is not None
    rec, created = found
    assert (rec.media_id, rec.is_done, created) == (MEDIA, True, 200)


def test_no_record_with_the_token_is_none_1354() -> None:
    from gflow_cli.api.transports.batchexecute import generation_record_by_poster

    rec = _poster_record(MEDIA, "ANqvOLD", 100)
    assert generation_record_by_poster("Zzl0ze", [None, [[rec]]], "ANqvNEW") is None
