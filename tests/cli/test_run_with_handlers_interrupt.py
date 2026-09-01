"""Ctrl+C during a billed run must say what was spent and how to resume.

`run_with_handlers` exited 130 silently. For a one-shot command that is fine;
for a run that submits several paid segments over tens of minutes it is a
data-loss-shaped bug — the user is left with no idea whether anything was
billed, and no resume handle.

This is fixed once at the boundary rather than per-command, because
`video chain`, `movie run` and `video extend` all share it.
"""

from __future__ import annotations

import pytest

from gflow_cli._cli_helpers import (
    clear_interrupt_context,
    run_with_handlers,
    set_interrupt_context,
)


@pytest.fixture(autouse=True)
def _clean():  # noqa: ANN202
    clear_interrupt_context()
    yield
    clear_interrupt_context()


def test_interrupt_still_exits_130(capsys: pytest.CaptureFixture[str]) -> None:
    async def _boom() -> None:
        raise KeyboardInterrupt

    with pytest.raises(SystemExit) as exc:
        run_with_handlers(_boom, cli_command="video extend")
    assert exc.value.code == 130


def test_interrupt_reports_spend_and_resume(capsys: pytest.CaptureFixture[str]) -> None:
    """The two facts a user needs at 3am: what did that cost, and how do I
    pick it back up."""

    # Set INSIDE the coroutine, mirroring production: `_on_submitted` publishes
    # progress per segment while the run is in flight. run_with_handlers clears
    # stale context on entry, so a value seeded before the call is (correctly)
    # discarded — that is the cross-command bleed guard doing its job.
    async def _boom() -> None:
        set_interrupt_context(credits_spent=30, resume_id="run-abc123", segments_done=3)
        raise KeyboardInterrupt

    with pytest.raises(SystemExit):
        run_with_handlers(_boom, cli_command="video extend")

    out = capsys.readouterr().out + capsys.readouterr().err
    assert "30" in out
    assert "run-abc123" in out


def test_silent_when_nothing_was_spent(capsys: pytest.CaptureFixture[str]) -> None:
    """A one-shot command that was interrupted before spending anything should
    not grow a spurious 'you spent 0 credits' banner."""

    async def _boom() -> None:
        raise KeyboardInterrupt

    with pytest.raises(SystemExit):
        run_with_handlers(_boom, cli_command="video t2v")

    out = capsys.readouterr().out
    assert "credits" not in out.lower()
