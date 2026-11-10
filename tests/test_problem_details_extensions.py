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
from types import SimpleNamespace
from typing import Any

import pytest

from gflow_cli.errors import (
    CHARGED_VALUES,
    EXIT_CODE_MAP,
    NEXT_ACTION_VALUES,
    RETRY_ACTIONS,
    RETRYABLE_ERRORS,
    ChainPartialError,
    GFlowError,
    MediaAttributionError,
    MediaDownloadError,
    RateLimitError,
    TransportTimeoutError,
    is_retryable,
)
from gflow_cli.json_output import error_payload, exit_code_for, unexpected_payload

_DOC = Path(__file__).resolve().parents[1] / "docs" / "ERRORS.md"


def _all_error_classes() -> list[type[GFlowError]]:
    seen: list[type[GFlowError]] = []
    stack: list[type[GFlowError]] = [GFlowError]
    while stack:
        cls = stack.pop()
        if cls not in seen:
            seen.append(cls)
            stack.extend(cls.__subclasses__())
    # Shipped classes only: other test modules define throwaway subclasses.
    return [c for c in seen if c.__module__.startswith("gflow_cli")]


def _bare(cls: type[GFlowError], retryable: bool | None) -> GFlowError:
    """An instance of *cls* built through the base constructor, so classes with their own
    required arguments (``BatchPartialError``…) can be checked the same way."""
    exc = cls.__new__(cls)
    GFlowError.__init__(exc, "detail", retryable=retryable)
    return exc


_SUBCLASSES = [c for c in _all_error_classes() if c is not GFlowError]


@pytest.mark.parametrize("cls", _SUBCLASSES, ids=lambda c: c.__name__)
def test_every_subclass_declares_its_own_defaults(cls: type[GFlowError]) -> None:
    # Own __dict__, not inheritance: a class added without a decision fails here.
    assert cls.__dict__.get("charged") in CHARGED_VALUES, cls.__name__
    assert cls.__dict__.get("next_action") in NEXT_ACTION_VALUES, cls.__name__


def test_every_mapped_class_is_covered() -> None:
    assert set(EXIT_CODE_MAP) <= set(_SUBCLASSES)


@pytest.mark.parametrize("cls", _SUBCLASSES, ids=lambda c: c.__name__)
def test_declared_next_action_agrees_with_retryable_errors(cls: type[GFlowError]) -> None:
    """The resolver cannot hide a bad declaration: the class default itself must agree."""
    assert (cls.next_action in RETRY_ACTIONS) is issubclass(cls, RETRYABLE_ERRORS), cls.__name__


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


def test_rate_limit_emits_its_retry_after_seconds() -> None:
    assert RateLimitError("quota", retry_after=30.0).to_problem_details()["retry_after"] == 30.0
    assert "retry_after" not in RateLimitError("quota").to_problem_details()


class TestChainPartial:
    def test_a_chain_that_failed_before_any_clip_is_not_proven_billed(self) -> None:
        pd = ChainPartialError("link 0 failed", partial_results=[]).to_problem_details()
        assert (pd["charged"], pd["next_action"]) == ("maybe", "reconcile")

    def test_completed_links_were_billed(self, tmp_path: Path) -> None:
        pd = ChainPartialError("x", partial_results=[tmp_path / "a.mp4"]).to_problem_details()
        assert (pd["charged"], pd["next_action"]) == ("yes", "reconcile")


def test_media_attribution_is_not_assumed_billed() -> None:
    """Its canonical raise sites are image paths, which spend no credits."""
    assert MediaAttributionError("ambiguous").to_problem_details()["charged"] == "maybe"


def test_profile_lock_says_wait() -> None:
    from gflow_cli.errors import ProfileLockedError

    assert ProfileLockedError.next_action == "wait"


@pytest.mark.asyncio
async def test_a_failed_asset_write_after_generation_is_reconciled(tmp_path: Path) -> None:
    from gflow_cli.storage import write_asset_async

    blocker = tmp_path / "file"
    blocker.write_bytes(b"")
    with pytest.raises(GFlowError) as info:
        await write_asset_async(blocker / "sub" / "out.mp4", b"data")  # parent is a file
    pd = info.value.to_problem_details()
    assert (pd["charged"], pd["next_action"]) == ("maybe", "reconcile")


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


def _doc_rows() -> dict[str, tuple[str, str, str]]:
    """``type`` -> (exit, charged, next_action) for every row of the table."""
    return {
        m[1]: (m[2], m[3], m[4])
        for m in re.finditer(
            r"^\| `(https://gflow-cli\.dev/errors/[a-z0-9-]+)` \| [^|]+ \| (\S+) \| "
            r"`(\w+)` \| `(\w+)` \|$",
            _DOC.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
    }


def test_errors_doc_lists_every_type_uri() -> None:
    rows = _doc_rows()
    missing = [
        c.__name__
        for c in _all_error_classes()
        if c.problem_type != "about:blank" and c.problem_type not in rows
    ]
    assert not missing, f"docs/ERRORS.md is missing: {missing}"


@pytest.mark.parametrize(
    "cls", [c for c in _SUBCLASSES if c.problem_type != "about:blank"], ids=lambda c: c.__name__
)
def test_errors_doc_row_matches_what_the_class_emits(cls: type[GFlowError]) -> None:
    exc = _bare(cls, None)
    pd = exc.to_problem_details()
    expected = (str(exit_code_for(exc)), pd["charged"], pd["next_action"])
    assert _doc_rows()[cls.problem_type] == expected, cls.__name__


def _hand_built_envelopes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, Any]]:
    """Every envelope built by hand instead of from a GFlowError (MCP, worker queue)."""
    from gflow_cli.mcp import tools
    from gflow_cli.profile_store import NoDefaultProfileError, NoProfilesError
    from gflow_cli.worker.queue import _recovery_error  # pyright: ignore[reportPrivateUsage]

    out: list[dict[str, Any]] = [
        tools._masked_unexpected_dict(RuntimeError("x")),  # pyright: ignore[reportPrivateUsage]
        tools._bad_param("Bad", "bad")["error"],  # pyright: ignore[reportPrivateUsage]
        _recovery_error("indeterminate", "crash"),
        _recovery_error("failed", "crash"),
    ]
    for exc in (NoProfilesError(), NoDefaultProfileError(["a", "b"])):

        def _raise(_flag: Any, exc: Exception = exc) -> str:
            raise exc

        monkeypatch.setattr(tools, "resolve_profile", _raise)
        resolved: Any = tools._resolve_and_validate_profile("default")  # pyright: ignore[reportPrivateUsage]
        out.append(resolved["error"])
    monkeypatch.setattr(tools, "resolve_profile", lambda _flag: "ghost")
    monkeypatch.setattr(
        tools,
        "get_settings",
        lambda: SimpleNamespace(profile_subdir=lambda name: tmp_path / "missing" / name),
    )
    resolved = tools._resolve_and_validate_profile("ghost")  # pyright: ignore[reportPrivateUsage]
    out.append(resolved["error"])
    return out


_EXPECTED_HAND_BUILT = {
    "https://gflow-cli.dev/errors/unknown": ("maybe", "none"),
    "https://gflow-cli.dev/errors/bad-parameter": ("no", "fix_input"),
    "https://gflow-cli.dev/errors/indeterminate-outcome": ("maybe", "reconcile"),
    "https://gflow-cli.dev/errors/daemon-recovery": ("no", "retry"),
    "https://gflow-cli.dev/errors/no-profile": ("no", "reauth"),
    "https://gflow-cli.dev/errors/no-default-profile": ("no", "fix_input"),
}


def test_hand_built_envelopes_carry_the_members_and_their_doc_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rows = _doc_rows()
    envelopes = _hand_built_envelopes(monkeypatch, tmp_path)
    assert {e["type"] for e in envelopes} == set(_EXPECTED_HAND_BUILT)
    for env in envelopes:
        got = (env.get("charged"), env.get("next_action"))
        assert got == _EXPECTED_HAND_BUILT[env["type"]], env["type"]
        assert env["retryable"] is (env["next_action"] in RETRY_ACTIONS), env["type"]
        assert env["type"] in rows, env["type"]
        assert rows[env["type"]][1:] == got, env["type"]


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("https://gflow-cli.dev/errors/profile-marker-missing", ("no", "reauth")),
        ("https://gflow-cli.dev/errors/cookie-store-unreadable", ("no", "reauth")),
        ("https://gflow-cli.dev/errors/verification-error", ("no", "retry")),
    ],
)
def test_auth_status_uris_are_documented(uri: str, expected: tuple[str, str]) -> None:
    assert _doc_rows()[uri][1:] == expected
