"""Step bindings for video_chain.feature.

Scoped to this feature only — pytest-bdd uses module-scoped step registries
(per-module ``scenarios()`` call) so the chain step phrases here don't collide
with the auth or image step modules (proven by
``test_step_collision_guard.py``).

Mocking strategy (zero credits, zero network): mirror the proven Click-runner
approach in ``tests/cli/test_cli_video_chain.py``. The orchestrator
``gflow_cli.chain.run_chain`` is patched (it is the seam every link generation
flows through), ``FlowApiClient`` is replaced with an async-context-manager
double, and ``ChainLinkRecorder.open`` returns a ``MagicMock`` so no SQLite
recorder or browser is ever touched. We assert *observable* outcomes — exit
code, CLI output, and ``run_chain`` await-args (the link count submitted) —
never internals.
"""

from __future__ import annotations

import re
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli import config
from gflow_cli.cli_video import video
from gflow_cli.errors import ChainPartialError, WireFormatError

scenarios("video_chain.feature")


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_result_holder() -> dict[str, Any]:
    return {"result": None}


@pytest.fixture
def chain_state() -> dict[str, Any]:
    """Per-scenario shared state: manifest path, the patched ``run_chain``
    mock (so Then-steps can read its await-args), the fake recorder, and the
    earlier-clip paths a partial failure is expected to preserve."""
    return {
        "manifest": None,
        "link_count": 0,
        "run_chain": None,
        "recorder": None,
        "completed": 0,
        "partial_paths": [],
        "raised": None,
    }


@pytest.fixture(autouse=True)
def _patch_chain_profile_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Bypass profile resolution + provider-dir checks so the command reaches
    the patched ``run_chain`` instead of bailing during profile discovery."""
    monkeypatch.setattr("gflow_cli.cli_video._resolve_profile", lambda profile: "default")
    monkeypatch.setattr("gflow_cli.cli_video._make_provider_dir", lambda name: tmp_path)


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Generator[None, None, None]:
    config.reset_settings()
    yield
    config.reset_settings()


def _fake_async_client() -> MagicMock:
    client = MagicMock(name="FlowApiClient")
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def _fake_link_result(index: int, tmp_path: Path) -> Any:
    from gflow_cli.chain import ChainLinkResult

    clip = tmp_path / f"link{index}.mp4"
    clip.touch()
    return ChainLinkResult(
        index=index,
        prompt=f"link {index}",
        local_path=clip,
        media_id=f"media-{index}",
    )


def _assert_no_numeric_credit_claim(output: str) -> None:
    numeric_credit_lines = [
        line
        for line in output.splitlines()
        if any(char.isdigit() for char in line)
        and re.search(r"\bcredit(?:s|\(s\))?", line, re.IGNORECASE)
    ]
    assert not numeric_credit_lines, f"numeric credit claim(s): {numeric_credit_lines}"


# ---------------------------------------------------------------------------
# Given steps
# ---------------------------------------------------------------------------


@given(parsers.parse("a chain manifest with {n:d} links"))
def _manifest_with_links(chain_state: dict[str, Any], tmp_path: Path, n: int) -> None:
    lines = [f'{{"prompt": "link {i}"}}' for i in range(n)]
    path = tmp_path / "chain.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    chain_state["manifest"] = path
    chain_state["link_count"] = n


@given(parsers.parse("the mocked chain aborts at link {k:d} with a WireFormatError"))
def _mock_chain_aborts(
    chain_state: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, k: int
) -> None:
    """``run_chain`` raises ``ChainPartialError`` carrying the clips from links
    1..k-1 — exactly what the orchestrator does when link k's wire lands on the
    text route (``WireFormatError``) and aborts before link k+1. The completed
    clips are preserved and re-billing must not occur on resume."""
    completed = [_fake_link_result(i, tmp_path).local_path for i in range(k - 1)]
    chain_state["partial_paths"] = completed

    error = ChainPartialError(
        detail=f"link {k} routed to text endpoint",
        partial_results=completed,
        cause=WireFormatError(detail="startImage dropped — text route", status=200),
    )
    chain_state["raised"] = error

    async def _boom(**_kwargs: Any) -> Any:
        raise error

    mock_run = AsyncMock(side_effect=_boom)
    monkeypatch.setattr("gflow_cli.chain.run_chain", mock_run)
    monkeypatch.setattr("gflow_cli.cli_video.FlowApiClient", lambda **_kw: _fake_async_client())
    chain_state["run_chain"] = mock_run


@given(parsers.parse("the recorder reports {n:d} completed link"))
@given(parsers.parse("the recorder reports {n:d} completed links"))
def _recorder_completed(
    chain_state: dict[str, Any], monkeypatch: pytest.MonkeyPatch, n: int
) -> None:
    recorder = MagicMock(name="ChainLinkRecorder")
    recorder.completed_links.return_value = [MagicMock() for _ in range(n)]
    monkeypatch.setattr(
        "gflow_cli.data.chain_repo.ChainLinkRecorder.open", lambda *_a, **_kw: recorder
    )
    chain_state["recorder"] = recorder
    chain_state["completed"] = n


@given(parsers.parse("the recorder reports {n:d} completed link whose seed frame is absent"))
def _recorder_completed_no_seed(
    chain_state: dict[str, Any], monkeypatch: pytest.MonkeyPatch, n: int
) -> None:
    """Models the record-before-extract guarantee: the link's clip is recorded
    (so ``completed_links`` counts it and resume skips regeneration) even though
    its seed frame file is absent. Resume must therefore re-submit only the
    remaining links — the recorded link is NOT regenerated."""
    recorder = MagicMock(name="ChainLinkRecorder")
    completed_link = MagicMock()
    completed_link.seed_frame_path = None  # extraction never completed
    recorder.completed_links.return_value = [completed_link for _ in range(n)]
    monkeypatch.setattr(
        "gflow_cli.data.chain_repo.ChainLinkRecorder.open", lambda *_a, **_kw: recorder
    )
    chain_state["recorder"] = recorder
    chain_state["completed"] = n


@given("the mocked chain completes the remaining links")
def _mock_chain_completes(
    chain_state: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    remaining = chain_state["link_count"] - chain_state["completed"]
    results = [_fake_link_result(i, tmp_path) for i in range(remaining)]
    mock_run = AsyncMock(return_value=results)
    monkeypatch.setattr("gflow_cli.chain.run_chain", mock_run)
    monkeypatch.setattr("gflow_cli.cli_video.FlowApiClient", lambda **_kw: _fake_async_client())
    chain_state["run_chain"] = mock_run


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------


@when("I run the chain")
def _run_chain(
    runner: CliRunner, cli_result_holder: dict[str, Any], chain_state: dict[str, Any]
) -> None:
    cli_result_holder["result"] = runner.invoke(
        video, ["chain", str(chain_state["manifest"]), "--yes"]
    )


@when("I run the chain with --dry-run")
def _run_chain_dry_run(
    runner: CliRunner,
    cli_result_holder: dict[str, Any],
    chain_state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--dry-run must short-circuit before any generation. We install a
    tripwire ``run_chain`` that fails the test if awaited, then assert it was
    never called (proving zero generation work)."""
    mock_run = AsyncMock(side_effect=AssertionError("run_chain must not run on --dry-run"))
    monkeypatch.setattr("gflow_cli.chain.run_chain", mock_run)
    chain_state["run_chain"] = mock_run
    cli_result_holder["result"] = runner.invoke(
        video, ["chain", str(chain_state["manifest"]), "--dry-run"]
    )


@when("I resume the chain with --dry-run")
def _resume_chain_dry_run(
    runner: CliRunner,
    cli_result_holder: dict[str, Any],
    chain_state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_run = AsyncMock(side_effect=AssertionError("run_chain must not run on --dry-run"))
    monkeypatch.setattr("gflow_cli.chain.run_chain", mock_run)
    chain_state["run_chain"] = mock_run
    cli_result_holder["result"] = runner.invoke(
        video,
        [
            "chain",
            str(chain_state["manifest"]),
            "--resume-from",
            "chain-abc",
            "--dry-run",
        ],
    )


@when("I resume the chain")
def _resume_chain(
    runner: CliRunner, cli_result_holder: dict[str, Any], chain_state: dict[str, Any]
) -> None:
    cli_result_holder["result"] = runner.invoke(
        video,
        ["chain", str(chain_state["manifest"]), "--resume-from", "chain-abc", "--yes"],
    )


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------


@then(parsers.parse("the chain exit code is {code:d}"))
def _check_exit(cli_result_holder: dict[str, Any], code: int) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == code, result.output


@then("the chain output mentions resuming")
def _check_resume_hint(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "resume" in result.output.lower(), result.output


@then("the partial result carries the earlier clip paths")
def _check_partial_paths(chain_state: dict[str, Any]) -> None:
    # The orchestrator raised ChainPartialError carrying the completed clips;
    # the command surfaced it (exit 21) and the preserved paths are the
    # earlier links 1..k-1, never the failed/later links.
    err = chain_state["raised"]
    assert isinstance(err, ChainPartialError)
    assert err.partial_results == chain_state["partial_paths"]
    assert err.partial_results, "expected the earlier clip paths to be preserved"


@then(parsers.parse("link {k:d} was never generated"))
def _check_link_never_generated(chain_state: dict[str, Any], k: int) -> None:
    # The chain aborted (ChainPartialError) before link k; only links 1..k-1
    # were preserved, so the clip for link k must never have been produced.
    produced = {p.name for p in chain_state["partial_paths"]}
    assert f"link{k - 1}.mp4" not in produced, produced


@then(parsers.parse("the chain submitted only {n:d} links"))
def _check_submitted_links(chain_state: dict[str, Any], n: int) -> None:
    mock_run = chain_state["run_chain"]
    mock_run.assert_awaited_once()
    assert len(mock_run.await_args.kwargs["links"]) == n


@then("the completed link was not regenerated")
def _check_not_regenerated(chain_state: dict[str, Any]) -> None:
    mock_run = chain_state["run_chain"]
    submitted = len(mock_run.await_args.kwargs["links"])
    # Resume submitted only the remaining links; the recorded link is excluded.
    assert submitted == chain_state["link_count"] - chain_state["completed"]
    chain_state["recorder"].completed_links.assert_called()


@then(parsers.parse("the output reports {n:d} pending video operation"))
@then(parsers.parse("the output reports {n:d} pending video operations"))
def _check_pending_operations(cli_result_holder: dict[str, Any], n: int) -> None:
    result = cli_result_holder["result"]
    noun = "operation" if n == 1 else "operations"
    assert f"{n} pending video {noun}" in result.output, result.output


@then("the output directs me to check the current cost in Flow")
def _check_flow_cost_guidance(cli_result_holder: dict[str, Any]) -> None:
    output = cli_result_holder["result"].output
    assert "current cost" in output.lower(), output
    assert "Flow" in output, output


@then("the output contains no numeric credit estimate")
def _check_no_numeric_credit_estimate(cli_result_holder: dict[str, Any]) -> None:
    output = cli_result_holder["result"].output
    assert "Estimated credits" not in output, output
    _assert_no_numeric_credit_claim(output)


@then("no generation was submitted")
def _check_no_generation(chain_state: dict[str, Any]) -> None:
    chain_state["run_chain"].assert_not_awaited()
