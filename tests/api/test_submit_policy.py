"""Tests for the pure submit policy domain module."""

from __future__ import annotations

import json
from typing import Any

import pytest

from gflow_cli.api.submit_policy import (
    MAX_IN_FLIGHT,
    Accepted,
    BrowserVisibility,
    Done,
    FallbackUi,
    Indeterminate,
    NotSent,
    RecaptchaTokenSource,
    RefusedNotBilled,
    RetryDirect,
    Stop,
    VisibilitySetting,
    classify_reply,
    launch_visibility,
    needs_rearm,
    next_step,
    starts_exceed,
)
from gflow_cli.errors import ContentPolicyError

WF = "11111111-1111-4111-8111-111111111111"
PROJ = "22222222-2222-4222-8222-222222222222"
MEDIA = "33333333-3333-4333-8333-333333333333"
PROMPT = "a red panda in autumn leaves"

REFUSAL_UNUSUAL_ACTIVITY = (
    ")]}'\n\n192\n"
    '[["wrb.fr","ogiZ0b",null,null,null,[7,null,[["type.googleapis.com/google.rpc.ErrorInfo",'
    '["PUBLIC_ERROR_UNUSUAL_ACTIVITY"]]]],"generic"],["di",240],'
    '["af.httprm",239,"-4624648772470899085",5]]\n25\n[["e",4,null,null,228]]\n'
)

BARE_STATUS_5 = ')]}\'\n[["wrb.fr","MZZa6b",null,null,null,[5],"generic"]]\n'

ACCEPTED_BODY = ')]}\'\n[["wrb.fr","ogiZ0b","[1,2,3]",null,null,null,"generic"]]\n'


def _video_record(status: int = 6) -> list[Any]:
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
            None,
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


def _envelope_with_error_and_record(rpcid: str, reason: str) -> str:
    err_frame = [
        "wrb.fr",
        rpcid,
        None,
        None,
        None,
        [7, None, [["type.googleapis.com/google.rpc.ErrorInfo", [reason]]]],
        "generic",
    ]
    rec_payload = [None, 881, [[_video_record()]]]
    rec_frame = [
        "wrb.fr",
        rpcid,
        json.dumps(rec_payload),
        None,
        None,
        None,
        "generic",
    ]
    frame = [err_frame, rec_frame]
    body = json.dumps(frame)
    return f")]}}'\n\n{len(body) + 1}\n{body}\n"


def test_refusal_requires_http_200_error_frame_reason_and_no_record() -> None:
    # HTTP 200 + error frame with UNUSUAL_ACTIVITY + no record -> RefusedNotBilled
    outcome = classify_reply("ogiZ0b", 200, REFUSAL_UNUSUAL_ACTIVITY)
    assert isinstance(outcome, RefusedNotBilled)
    assert outcome.rpcid == "ogiZ0b"
    assert outcome.reason == "PUBLIC_ERROR_UNUSUAL_ACTIVITY"

    # HTTP 200 + error frame with UNUSUAL_ACTIVITY + WITH a record -> Indeterminate
    body_with_record = _envelope_with_error_and_record("ogiZ0b", "PUBLIC_ERROR_UNUSUAL_ACTIVITY")
    outcome_with_rec = classify_reply("ogiZ0b", 200, body_with_record)
    assert isinstance(outcome_with_rec, Indeterminate)
    assert outcome_with_rec.rpcid == "ogiZ0b"

    # HTTP 500 + error frame with UNUSUAL_ACTIVITY -> Indeterminate (must be HTTP 200)
    outcome_500 = classify_reply("ogiZ0b", 500, REFUSAL_UNUSUAL_ACTIVITY)
    assert isinstance(outcome_500, Indeterminate)
    assert outcome_500.status == 500

    # HTTP 200 + error frame with another unrecognised reason -> Indeterminate
    other_err_body = (
        ')]}\'\n[["wrb.fr","ogiZ0b",null,null,null,'
        '[7,null,[["type.googleapis.com/google.rpc.ErrorInfo",["OTHER_UNEXPECTED_REASON"]]]],"generic"]]\n'
    )
    outcome_other = classify_reply("ogiZ0b", 200, other_err_body)
    assert isinstance(outcome_other, Indeterminate)

    # HTTP 200 + normal accepted frame -> Accepted
    outcome_accepted = classify_reply("ogiZ0b", 200, ACCEPTED_BODY)
    assert isinstance(outcome_accepted, Accepted)
    assert outcome_accepted.body == ACCEPTED_BODY


def test_bare_status_reply_is_indeterminate_not_refused() -> None:
    outcome = classify_reply("MZZa6b", 200, BARE_STATUS_5)
    assert isinstance(outcome, Indeterminate)
    assert outcome.rpcid == "MZZa6b"
    assert outcome.status == 200
    assert not isinstance(outcome, RefusedNotBilled)


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 429, 500, 502, 503])
def test_3xx_401_403_after_send_are_indeterminate(status: int) -> None:
    outcome = classify_reply("ogiZ0b", status, REFUSAL_UNUSUAL_ACTIVITY)
    assert isinstance(outcome, Indeterminate)
    assert outcome.rpcid == "ogiZ0b"
    assert outcome.status == status
    assert not isinstance(outcome, RefusedNotBilled)


def test_timeout_after_send_is_indeterminate() -> None:
    outcome = classify_reply("ogiZ0b", status=None, body=None, cause="TimeoutException on POST")
    assert isinstance(outcome, Indeterminate)
    assert outcome.rpcid == "ogiZ0b"
    assert outcome.status is None
    assert "TimeoutException" in outcome.cause


def test_refused_auto_retries_once_headless() -> None:
    outcome = RefusedNotBilled(rpcid="ogiZ0b", reason="PUBLIC_ERROR_UNUSUAL_ACTIVITY")
    step = next_step(outcome, setting=VisibilitySetting.AUTO, retried=False)
    assert step == RetryDirect(BrowserVisibility.HEADLESS)

    # String setting also accepted
    step_str = next_step(outcome, setting="auto", retried=False)
    assert step_str == RetryDirect(BrowserVisibility.HEADLESS)


def test_refused_retry_is_headed_only_under_explicit_headed() -> None:
    outcome = RefusedNotBilled(rpcid="ogiZ0b", reason="PUBLIC_ERROR_UNUSUAL_ACTIVITY")

    # Explicit HEADED -> HEADED
    step_headed = next_step(outcome, setting=VisibilitySetting.HEADED, retried=False)
    assert step_headed == RetryDirect(BrowserVisibility.HEADED)

    # Explicit HEADLESS -> HEADLESS
    step_headless = next_step(outcome, setting=VisibilitySetting.HEADLESS, retried=False)
    assert step_headless == RetryDirect(BrowserVisibility.HEADLESS)

    # AUTO -> HEADLESS
    step_auto = next_step(outcome, setting=VisibilitySetting.AUTO, retried=False)
    assert step_auto == RetryDirect(BrowserVisibility.HEADLESS)


def test_refused_after_retry_stops_waf_final() -> None:
    outcome = RefusedNotBilled(rpcid="ogiZ0b", reason="PUBLIC_ERROR_UNUSUAL_ACTIVITY")
    for setting in (VisibilitySetting.AUTO, VisibilitySetting.HEADLESS, VisibilitySetting.HEADED):
        step = next_step(outcome, setting=setting, retried=True)
        assert step == Stop("waf_final")


@pytest.mark.parametrize(
    "setting", [VisibilitySetting.AUTO, VisibilitySetting.HEADLESS, VisibilitySetting.HEADED]
)
@pytest.mark.parametrize("retried", [False, True])
def test_indeterminate_never_retries_in_any_state(
    setting: VisibilitySetting, retried: bool
) -> None:
    outcome = Indeterminate(rpcid="YhhmEf", cause="HTTP 500", status=500)
    step = next_step(outcome, setting=setting, retried=retried)
    assert step == Stop("reconcile")


def test_not_sent_auth_stops_reauth_without_retry() -> None:
    outcome = NotSent(reason="session expired", auth=True)
    for setting in (VisibilitySetting.AUTO, VisibilitySetting.HEADLESS, VisibilitySetting.HEADED):
        for retried in (False, True):
            step = next_step(outcome, setting=setting, retried=retried)
            assert step == Stop("reauth")


def test_not_sent_falls_back_to_ui_headed_under_auto() -> None:
    outcome = NotSent(reason="connect timeout", auth=False)

    # AUTO -> HEADED
    step_auto = next_step(outcome, setting=VisibilitySetting.AUTO, retried=False)
    assert step_auto == FallbackUi(BrowserVisibility.HEADED)

    # HEADED -> HEADED
    step_headed = next_step(outcome, setting=VisibilitySetting.HEADED, retried=False)
    assert step_headed == FallbackUi(BrowserVisibility.HEADED)

    # HEADLESS -> HEADLESS
    step_headless = next_step(outcome, setting=VisibilitySetting.HEADLESS, retried=False)
    assert step_headless == FallbackUi(BrowserVisibility.HEADLESS)


def test_rearm_on_wall_clock_gap_with_monotonic_unchanged() -> None:
    last_mint = 1000.0

    # Gap <= 600 s -> False
    assert not needs_rearm(last_mint_wall=last_mint, now_wall=1500.0)
    assert not needs_rearm(last_mint_wall=last_mint, now_wall=1600.0)

    # Gap > 600 s -> True
    assert needs_rearm(last_mint_wall=last_mint, now_wall=1600.1)
    assert needs_rearm(last_mint_wall=last_mint, now_wall=2200.0)

    # First mint (None) -> False
    assert not needs_rearm(last_mint_wall=None, now_wall=1000.0)


def test_starts_exceed_true_for_the_ninth_start_in_120s() -> None:
    now = 1000.0
    history = [
        now - 100.0,
        now - 80.0,
        now - 60.0,
        now - 40.0,
        now - 30.0,
        now - 20.0,
        now - 10.0,
        now - 5.0,
    ]
    assert len(history) == 8

    # 8 starts within 120s window -> False (limit=8 not exceeded)
    assert not starts_exceed(history, now, limit=8, window_s=120)

    # 9th start within 120s window -> True (limit=8 exceeded)
    history_9 = [*history, now]
    assert starts_exceed(history_9, now, limit=8, window_s=120)

    # Starts older than 120s are not counted towards the window
    history_with_stale = [now - 200.0, now - 150.0, *history]
    assert not starts_exceed(history_with_stale, now, limit=8, window_s=120)


def test_accepted_outcome_next_step_is_done() -> None:
    outcome = Accepted(body="ok")
    step = next_step(outcome, setting=VisibilitySetting.AUTO, retried=False)
    assert step == Done()


def test_content_policy_refusal_raises_content_policy_error() -> None:
    safety_body = (
        ')]}\'\n[["wrb.fr","ogiZ0b",null,null,null,'
        '[7,null,[["type.googleapis.com/google.rpc.ErrorInfo",["PUBLIC_ERROR_UNSAFE_CONTENT"]]]],"generic"]]\n'
    )
    with pytest.raises(ContentPolicyError, match="PUBLIC_ERROR_UNSAFE_CONTENT"):
        classify_reply("ogiZ0b", 200, safety_body)


def test_unparseable_body_is_indeterminate() -> None:
    outcome = classify_reply("ogiZ0b", 200, "<html>not an envelope</html>")
    assert isinstance(outcome, Indeterminate)
    assert outcome.rpcid == "ogiZ0b"


def test_launch_visibility_mappings() -> None:
    assert launch_visibility(VisibilitySetting.AUTO) == BrowserVisibility.HEADLESS
    assert launch_visibility(VisibilitySetting.HEADLESS) == BrowserVisibility.HEADLESS
    assert launch_visibility(VisibilitySetting.HEADED) == BrowserVisibility.HEADED
    assert launch_visibility("auto") == BrowserVisibility.HEADLESS
    assert launch_visibility("headless") == BrowserVisibility.HEADLESS
    assert launch_visibility("headed") == BrowserVisibility.HEADED


def test_recaptcha_token_source_protocol() -> None:
    class DummySource:
        async def mint(self, action: str) -> str:
            return f"token_{action}"

    source: RecaptchaTokenSource = DummySource()
    assert isinstance(source, RecaptchaTokenSource)


def test_constants_and_types() -> None:
    assert MAX_IN_FLIGHT == 4
    # SubmitOutcome variants are frozen and do not carry both body and refusal
    refusal = RefusedNotBilled(rpcid="x", reason="y")
    assert not hasattr(refusal, "body")
    accepted = Accepted(body="z")
    assert not hasattr(accepted, "reason")
