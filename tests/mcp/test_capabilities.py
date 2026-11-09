# SPDX-License-Identifier: MIT
"""`gflow capabilities` and its MCP twin `capabilities://self` (#1197).

The document has one source of truth (``cli_capabilities.build_capabilities``); the
tests below hold the two surfaces to it byte for byte, keep the values honest, and
make a new CLI command fail until someone decides which capability it belongs to.
"""

from __future__ import annotations

from typing import Any

import click
import pytest
from click.testing import CliRunner

from gflow_cli import cli_capabilities as caps
from gflow_cli.cli import main

_ENUM = {"yes", "no", "partial", "unknown"}
_HOSTS = ("auto", "flow.google.com", "labs.google")


@pytest.mark.parametrize("host", _HOSTS)
def test_every_value_is_in_the_enum_and_every_partial_has_a_note(host: str) -> None:
    doc = caps.build_capabilities(host)
    assert doc["schema"] == 1
    assert doc["adapter"] == "flow-browser"
    assert doc["host"] == host
    for key, value in doc["capabilities"].items():
        assert value in _ENUM, f"{key}={value!r} is not in {sorted(_ENUM)}"
        if value == "partial":
            assert doc["notes"].get(key), f"{key} is partial but carries no note"
    assert set(doc["notes"]) <= set(doc["capabilities"]), "a note for an unknown key"


def test_issue_vocabulary_is_present_and_server_cancel_is_honest() -> None:
    doc = caps.build_capabilities("auto")["capabilities"]
    assert set(doc) == {
        "generate.image",
        "generate.video",
        "submit.no_wait",
        "status",
        "wait",
        "download",
        "upload",
        "abort.local",
        "cancel.server",
        "idempotent_submit",
        "reconcile",
        "credits.balance",
    }
    assert doc["credits.balance"] == "partial"  # Veo credits only; image quotas unread
    assert doc["cancel.server"] == "no"
    assert doc["idempotent_submit"] == "no"
    # #1194 is not built: no CLI --no-wait / status / wait. The MCP task path covers
    # part of it, which is what `partial` says — never a bare `yes`.
    for key in ("submit.no_wait", "status", "wait"):
        assert doc[key] == "partial"


def test_generation_is_partial_or_unknown_never_a_bare_yes() -> None:
    # labs.google: gflow's labs driver takes every mode, but whether Flow serves the
    # account labs at all is not checkable offline (exit 36 otherwise).
    labs = caps.build_capabilities("labs.google")["capabilities"]
    assert labs["generate.image"] == labs["generate.video"] == "unknown"
    for host in ("auto", "flow.google.com"):
        doc = caps.build_capabilities(host)
        assert doc["capabilities"]["generate.image"] == "partial"
        assert doc["capabilities"]["generate.video"] == "partial"
        assert "#799" in doc["notes"]["generate.video"]


def test_no_note_claims_what_a_host_can_do() -> None:
    """AGENTS.md Host-Membership Discipline: report what gflow drives, not what a host can."""
    banned = ("labs-only", "labs only", "flow.google.com cannot", "migrated account")
    for host in _HOSTS:
        for note in caps.build_capabilities(host)["notes"].values():
            assert not any(phrase in note.lower() for phrase in banned), note


def test_host_follows_the_flow_host_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", "flow.google.com")
    reset_settings()
    try:
        assert caps.current_capabilities()["host"] == "flow.google.com"
    finally:
        monkeypatch.delenv("GFLOW_CLI_FLOW_HOST")
        reset_settings()


@pytest.mark.asyncio
async def test_cli_json_and_mcp_resource_serialize_identically(mcp_server: Any) -> None:
    from mcp import Client

    result = CliRunner().invoke(main, ["capabilities", "--json"])
    assert result.exit_code == 0, result.output

    async with Client(mcp_server, mode="2026-07-28") as client:
        read = await client.read_resource("capabilities://self")
    text = read.contents[0].text  # pyright: ignore[reportAttributeAccessIssue]

    assert result.stdout.strip() == text.strip()


def test_cli_table_lists_every_key() -> None:
    result = CliRunner().invoke(main, ["capabilities"])
    assert result.exit_code == 0, result.output
    for key in caps.build_capabilities("auto")["capabilities"]:
        assert key in result.output


# --- every CLI command has a capability decision -------------------------------------


def _leaves(group: click.Group, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for name, cmd in group.commands.items():
        path = f"{prefix}{name}"
        if isinstance(cmd, click.Group):
            if cmd.invoke_without_command:
                out.add(path)
            out |= _leaves(cmd, f"{path} ")
        else:
            out.add(path)
    return out


def _undecided(group: click.Group) -> set[str]:
    return _leaves(group) - set(caps.COMMAND_CAPABILITY)


def test_every_cli_command_has_a_capability_entry() -> None:
    undecided = _undecided(main)
    assert not undecided, (
        f"CLI commands with no entry in cli_capabilities.COMMAND_CAPABILITY: "
        f"{sorted(undecided)}. Map each to the capability key it exercises, or to None "
        "when it is outside the generation-lifecycle vocabulary — then update "
        "docs/CAPABILITIES.md."
    )


def test_coverage_check_bites_on_a_new_command() -> None:
    probe = click.Group(commands=dict(main.commands))
    probe.add_command(click.Command("brand-new"))
    assert _undecided(probe) == {"brand-new"}


def test_no_stale_command_entries() -> None:
    stale = set(caps.COMMAND_CAPABILITY) - _leaves(main)
    assert not stale, f"Capability entries for commands that no longer exist: {sorted(stale)}"


def test_every_mapped_key_exists_in_the_document() -> None:
    keys = set(caps.build_capabilities("auto")["capabilities"])
    unknown = {k for k in caps.COMMAND_CAPABILITY.values() if k is not None} - keys
    assert not unknown, f"COMMAND_CAPABILITY names keys the document lacks: {sorted(unknown)}"
