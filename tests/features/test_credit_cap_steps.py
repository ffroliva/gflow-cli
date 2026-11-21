from __future__ import annotations

from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.api.credit_cap import CreditBudget, enforce_credit_cap, parse_credit_quote
from gflow_cli.errors import CreditCapExceededError, exit_code_for

scenarios("credit_cap.feature")


@pytest.fixture
def state() -> dict[str, Any]:
    return {}


@given(parsers.parse('a credit label containing "{label}"'))
def credit_label(state: dict[str, Any], label: str) -> None:
    state["label"] = label


@when("gflow parses the credit quote")
def parse_quote(state: dict[str, Any]) -> None:
    state["quoted"] = parse_credit_quote(state["label"])


@then(parsers.parse("the parsed quote is {quoted:d}"))
def parsed_quote(state: dict[str, Any], quoted: int) -> None:
    assert state["quoted"] == quoted


@given(parsers.parse("an unreadable credit quote and a cap of {cap:d}"))
def unreadable_quote(state: dict[str, Any], cap: int) -> None:
    state.update(quoted=None, cap=cap)


@when("gflow checks the cap")
def check_cap(state: dict[str, Any]) -> None:
    with pytest.raises(CreditCapExceededError) as caught:
        enforce_credit_cap(quoted=state["quoted"], max_credits=state["cap"])
    state["error"] = caught.value


@then("the request is refused without charge")
def refused_without_charge(state: dict[str, Any]) -> None:
    assert state["error"].charged == "no"
    assert state["error"].quoted is None


@given(parsers.parse("a quote of {quoted:d} against a cap of {cap:d}"))
def quote_over_cap(state: dict[str, Any], quoted: int, cap: int) -> None:
    state["error"] = CreditCapExceededError(quoted=quoted, max_credits=cap)


@when("the credit cap error is formatted in both exit code modes")
def format_exit_codes(monkeypatch: pytest.MonkeyPatch, state: dict[str, Any]) -> None:
    codes = []
    for mode in (None, "2"):
        if mode is None:
            monkeypatch.delenv("GFLOW_CLI_EXIT_CODES", raising=False)
        else:
            monkeypatch.setenv("GFLOW_CLI_EXIT_CODES", mode)
        codes.append(exit_code_for(state["error"]))
    state["codes"] = codes


@then("both exit codes are 45")
def both_exit_codes(state: dict[str, Any]) -> None:
    assert state["codes"] == [45, 45]


@given(parsers.parse("a command budget of {cap:d} and a link quote of {quote:d}"))
def budget_and_quote(state: dict[str, Any], cap: int, quote: int) -> None:
    state.update(budget=CreditBudget(max_credits=cap), quote=quote)


@when(parsers.parse("the completed link is recorded and the next quote is {quote:d}"))
def record_link(state: dict[str, Any], quote: int) -> None:
    state["budget"].record_spend(state["quote"])
    with pytest.raises(CreditCapExceededError) as caught:
        enforce_credit_cap(quoted=quote, max_credits=state["budget"].max_credits)
    state["error"] = caught.value


@then("the second link is refused against the remaining budget")
def second_link_refused(state: dict[str, Any]) -> None:
    assert state["budget"].remaining == 2
    assert state["error"].max_credits == 2


@given(parsers.parse("a queued video request with max_credits {cap:d}"))
def queued_video(state: dict[str, Any], cap: int) -> None:
    state["payload"] = {"prompt": "test", "max_credits": cap}


@when("the worker decodes the request")
def decode_request(state: dict[str, Any]) -> None:
    from gflow_cli.worker.codec import build_video_request

    state["request"] = build_video_request(state["payload"])


@then(parsers.parse("the request cap is {cap:d}"))
def request_cap(state: dict[str, Any], cap: int) -> None:
    assert state["request"].max_credits == cap
