"""A finished command must not leave its name bound for the next one.

`run_with_handlers` binds `cli_command` into structlog's contextvars so downstream
consumers (the incident manifest's `command`, log correlation) can read it. Nothing
unbound it. Only `cli.main()` clears contextvars, and only long-lived processes run more
than one command through this boundary without passing through `main()` again — the MCP
server, `gflow serve`, the worker, and a pytest session.

The result is a stale label on someone else's logs. In the 2026-09-07 e2e sweep, six
`FlowHostMigratedError` captures were emitted with `cli_command="project rename"` while
their own request bodies showed `project.createProject`: an earlier in-process
`runner.invoke(main, ["project", "rename", ...])` had left the binding behind, and later
tests that construct `FlowApiClient` directly inherited it. That is worse than a missing
field — a *wrong* one sends a reader to the wrong command when they are trying to work
out which call failed.

The module's own comment beneath the bind already names this exact hazard, for a
different variable: *"Long-lived processes (MCP server, `gflow serve`, the worker, a test
session) run many commands. Without this, a finished `video extend` leaves its resume id
behind."* The reasoning was applied to the interrupt context and not to `cli_command`.
"""

from __future__ import annotations

import pytest
import structlog

from gflow_cli._cli_helpers import run_with_handlers


@pytest.fixture(autouse=True)
def _clean_contextvars():  # noqa: ANN202
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


def test_a_successful_command_unbinds_its_own_name() -> None:
    async def _ok() -> None:
        return None

    run_with_handlers(_ok, cli_command="project rename")

    assert "cli_command" not in structlog.contextvars.get_contextvars(), (
        "cli_command stayed bound after the command finished; the next command run in "
        "this process (MCP server, gflow serve, the worker, a test session) will emit "
        "logs labelled with this one's name"
    )


def test_a_failing_command_also_unbinds_its_own_name() -> None:
    """The leak matters MORE on the failure path: that is when someone reads the logs."""
    from gflow_cli.errors import ConfigurationError

    async def _boom() -> None:
        raise ConfigurationError(detail="nope")

    with pytest.raises(SystemExit):
        run_with_handlers(_boom, cli_command="video extend")

    assert "cli_command" not in structlog.contextvars.get_contextvars()


def test_the_name_is_still_bound_while_the_command_runs() -> None:
    """Unbinding afterwards must not break what the binding is FOR — the incident
    manifest and log correlation read it mid-run."""
    seen: list[object] = []

    async def _peek() -> None:
        seen.append(structlog.contextvars.get_contextvars().get("cli_command"))

    run_with_handlers(_peek, cli_command="image t2i")

    assert seen == ["image t2i"]
