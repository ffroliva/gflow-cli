from __future__ import annotations

import pytest
from pydantic import ValidationError

from gflow_cli.api.credit_cap import CreditBudget, enforce_credit_cap, parse_credit_quote
from gflow_cli.api.video import GenerateVideoRequest
from gflow_cli.config import Settings
from gflow_cli.errors import CreditCapExceededError


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Generating will use 4 credits", 4),
        ("A geração vai usar 12 créditos", 12),
        ("Generating will use 0 credits", 0),
        ("", None),
        ("Generating video...", None),
        ("4s 12 credits", None),
    ],
)
def test_parse_credit_quote(label: str, expected: int | None) -> None:
    assert parse_credit_quote(label) == expected


def test_enforce_credit_cap_boundaries_and_fail_closed() -> None:
    assert enforce_credit_cap(quoted=4, max_credits=4) == 4
    assert enforce_credit_cap(quoted=0, max_credits=0) == 0
    assert enforce_credit_cap(quoted=None, max_credits=None) is None
    for quoted, limit in ((4, 3), (None, 10), (4, 0)):
        with pytest.raises(CreditCapExceededError) as caught:
            enforce_credit_cap(quoted=quoted, max_credits=limit, route="settings pane")
        assert caught.value.quoted == quoted
        assert caught.value.max_credits == limit
        assert caught.value.charged == "no"
        assert caught.value.next_action == "fix_input"


def test_credit_budget_tracks_spend_and_rejects_unknown_quote() -> None:
    budget = CreditBudget(max_credits=6)
    assert budget.remaining == 6
    budget.record_spend(4)
    assert budget.remaining == 2
    assert budget.max_credits == 2
    with pytest.raises(CreditCapExceededError) as caught:
        budget.record_spend(None)
    assert budget.remaining == 0
    assert caught.value.charged == "maybe"
    assert caught.value.next_action == "reconcile"


def test_generate_video_request_rejects_negative_cap() -> None:
    with pytest.raises(ValueError, match="max_credits must be >= 0"):
        GenerateVideoRequest(prompt="test", max_credits=-1)


def test_missing_quote_remediation_says_to_run_without_a_cap() -> None:
    error = CreditCapExceededError(quoted=None, max_credits=4)
    assert "run without a credit cap" in error.remediation_hint


def test_agents_doc_and_e2e_refusal_pin_credit_cap_exit_and_omni_1_2() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    agents_md = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "3–45 by default" in agents_md
    assert "45 `CreditCapExceededError`" in agents_md
    e2e_src = (root / "tests" / "e2e" / "test_credit_cap_refusal_bdd.py").read_text(
        encoding="utf-8"
    )
    assert "omni_flash" not in e2e_src
    assert 'model="omni-1.2"' in e2e_src


def test_preserve_credit_cap_partials_and_mcp_docstring_preserved() -> None:
    from gflow_cli.api import credit_cap
    from gflow_cli.mcp.tools import gflow_generate_video

    preserve = getattr(credit_cap, "preserve_credit_cap_partials", None)
    assert callable(preserve)
    err = CreditCapExceededError(quoted=8, max_credits=4, completed_media_ids=["m2"])
    err.charged = "maybe"
    assert preserve(err, ["m1"]) == ["m2"]
    assert err.completed_media_ids == ["m1", "m2"]
    assert err.charged == "maybe"
    assert err.next_action == "reconcile"

    pre_submit = CreditCapExceededError(quoted=8, max_credits=4)
    assert preserve(pre_submit, ["m1"]) == []
    assert pre_submit.completed_media_ids == ["m1"]
    assert pre_submit.charged == "yes"
    assert pre_submit.next_action == "reconcile"

    doc = gflow_generate_video.__doc__ or ""
    assert "Optional Veo model — 'veo_lite', 'veo_fast', 'veo_quality'" in doc
    assert "Optional clip length in seconds (mirrors the CLI ``--duration``):" in doc


def test_max_credits_setting_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(max_credits=10).max_credits == 10
    monkeypatch.setenv("GFLOW_CLI_MAX_CREDITS", "10")
    assert Settings(_env_file=None).max_credits == 10
    monkeypatch.setenv("GFLOW_CLI_MAX_CREDITS", "-1")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
