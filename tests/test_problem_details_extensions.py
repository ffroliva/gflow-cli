"""`charged`, `next_action` and `operation_id` on every error envelope (#1201).

Two questions an automation pipeline must answer without reading prose: *was I
charged?* and *what do I do next?* Every class in ``EXIT_CODE_MAP`` declares its own
answer; raise sites may override it; ``retryable`` must never contradict
``next_action`` — not on a class, and not on an instance whose raise site flipped
``retryable`` either way.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.errors import (
    CHARGED_VALUES,
    EXIT_CODE_MAP,
    NEXT_ACTION_VALUES,
    RETRY_ACTIONS,
    GFlowError,
    MediaDownloadError,
    RateLimitError,
    TransportTimeoutError,
    is_retryable,
)
from gflow_cli.json_output import error_payload, unexpected_payload

_DOC = Path(__file__).resolve().parents[1] / "docs" / "ERRORS.md"


def _all_error_classes() -> list[type[GFlowError]]:
    seen: list[type[GFlowError]] = []
    stack: list[type[GFlowError]] = [GFlowError]
    while stack:
        cls = stack.pop()
        if cls not in seen:
            seen.append(cls)
            stack.extend(cls.__subclasses__())
    return seen


def _bare(cls: type[GFlowError], retryable: bool | None) -> GFlowError:
    """An instance of *cls* built through the base constructor, so classes with their own
    required arguments (``BatchPartialError``…) can be checked the same way."""
    exc = cls.__new__(cls)
    GFlowError.__init__(exc, "detail", retryable=retryable)
    return exc


@pytest.mark.parametrize("cls", list(EXIT_CODE_MAP), ids=lambda c: c.__name__)
def test_every_mapped_class_declares_its_own_defaults(cls: type[GFlowError]) -> None:
    # Own __dict__, not inheritance: a class added to the map without a decision fails here.
    assert cls.__dict__.get("charged") in CHARGED_VALUES, cls.__name__
    assert cls.__dict__.get("next_action") in NEXT_ACTION_VALUES, cls.__name__


@pytest.mark.parametrize("cls", _all_error_classes(), ids=lambda c: c.__name__)
@pytest.mark.parametrize("retryable", [None, True, False])
def test_retryable_agrees_with_next_action(cls: type[GFlowError], retryable: bool | None) -> None:
    exc = _bare(cls, retryable)
    pd = exc.to_problem_details()
    assert pd["charged"] in CHARGED_VALUES
    assert (pd["next_action"] in RETRY_ACTIONS) is is_retryable(exc)


def test_agreement_survives_a_post_construction_flip() -> None:
    """``client.py`` sets ``exc.retryable = False`` after building the error."""
    exc = TransportTimeoutError("poll")
    assert exc.to_problem_details()["next_action"] == "retry"
    exc.retryable = False
    assert exc.to_problem_details()["next_action"] not in RETRY_ACTIONS


def test_a_raise_site_override_is_emitted() -> None:
    exc = TransportTimeoutError("x", charged="yes", next_action="reconcile", operation_id="op-1")
    pd = exc.to_problem_details()
    assert (pd["charged"], pd["next_action"], pd["operation_id"]) == ("yes", "reconcile", "op-1")
    assert is_retryable(exc) is False


def test_operation_id_is_absent_when_there_is_none() -> None:
    assert "operation_id" not in TransportTimeoutError("x").to_problem_details()


def test_rate_limit_says_retry_after() -> None:
    assert RateLimitError("quota").to_problem_details()["next_action"] == "retry_after"


class TestMediaDownload:
    def test_billed_clip_download_is_charged_and_reconciled(self) -> None:
        """The issue's own example: a download that fails after a successful poll."""
        pd = MediaDownloadError(
            media_id="m-1", detail="HTTP 403", retryable=False
        ).to_problem_details()
        assert (pd["charged"], pd["next_action"], pd["operation_id"]) == ("yes", "reconcile", "m-1")

    def test_free_recovery_read_stays_a_plain_retry(self) -> None:
        """`gflow data download` builds it without `retryable`: nothing is billed there."""
        pd = MediaDownloadError(media_id="m-1", detail="reset").to_problem_details()
        assert (pd["charged"], pd["next_action"], pd["operation_id"]) == ("no", "retry", "m-1")


class TestCliJson:
    def test_error_payload_carries_the_members(self) -> None:
        err = error_payload(RateLimitError("quota"))["error"]
        assert err["charged"] == "no"
        assert err["next_action"] == "retry_after"
        assert err["retryable"] is True

    def test_unexpected_payload_carries_the_members(self) -> None:
        err = unexpected_payload()["error"]
        assert (err["charged"], err["next_action"]) == ("maybe", "none")


class TestMcp:
    def test_gflow_error_dict_carries_the_same_members(self) -> None:
        from gflow_cli.mcp.tools import _gflow_error_dict  # pyright: ignore[reportPrivateUsage]

        exc = MediaDownloadError(media_id="m-1", retryable=False)
        mcp = _gflow_error_dict(exc)
        cli = error_payload(exc)["error"]
        for key in ("charged", "next_action", "operation_id", "retryable"):
            assert mcp[key] == cli[key], key

    def test_masked_unexpected_dict_carries_the_members(self) -> None:
        from gflow_cli.mcp.tools import (
            _masked_unexpected_dict,  # pyright: ignore[reportPrivateUsage]
        )

        masked: dict[str, Any] = _masked_unexpected_dict(RuntimeError("boom"))
        assert (masked["charged"], masked["next_action"]) == ("maybe", "none")


def test_errors_doc_lists_every_type_uri() -> None:
    text = _DOC.read_text(encoding="utf-8")
    missing = [
        c.__name__
        for c in _all_error_classes()
        if c.problem_type != "about:blank" and c.problem_type not in text
    ]
    assert not missing, f"docs/ERRORS.md is missing: {missing}"


def test_errors_doc_defaults_match_the_code() -> None:
    """Each table row ``| `type` | exit | `charged` | `next_action` |`` matches its class."""
    rows = {
        m[1]: (m[2], m[3])
        for m in re.finditer(
            r"`(https://gflow-cli\.dev/errors/[a-z0-9-]+)`[^\n]*?\| `(\w+)` \| `(\w+)` \|",
            _DOC.read_text(encoding="utf-8"),
        )
    }
    for cls in _all_error_classes():
        if cls.problem_type in rows:
            assert rows[cls.problem_type] == (cls.charged, cls.next_action), cls.__name__
