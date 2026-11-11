# SPDX-License-Identifier: MIT
"""`gflow capabilities` and its MCP twin `capabilities://self` (#1197).

The document has one source of truth (``cli_capabilities.build_capabilities``); the
tests below hold the two surfaces to the same JSON document, keep the values honest,
and make a new CLI command fail until someone decides which capability it belongs to.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from gflow_cli import cli_capabilities as caps
from gflow_cli.cli import main
from tests.mcp._cli_tree import cli_leaves

_ENUM = {"yes", "no", "partial", "unknown"}
_HOSTS = ("auto", "flow.google.com", "labs.google")
_REPO = Path(__file__).resolve().parents[2]

_IMAGE = "generate.image"
_VIDEO = "generate.video"

#: Every CLI leaf -> the capability key it exercises, or None when the command is outside
#: this generation-lifecycle vocabulary (sessions, the local catalog, configuration, docs,
#: the servers, prompt tooling, credit-free composition and post-processing such as
#: upscale). A new command fails test_every_cli_command_has_a_capability_entry until it
#: is added here, which forces the question "does the capability document still hold?".
COMMAND_CAPABILITY: dict[str, str | None] = {
    "image t2i": _IMAGE,
    "image i2i": _IMAGE,
    "image batch": _IMAGE,
    "run": _IMAGE,
    "character create": _IMAGE,  # generates face/body images (per-model image quota)
    "video t2v": _VIDEO,
    "video i2v": _VIDEO,
    "video r2v": _VIDEO,
    "video chain": _VIDEO,
    "video extend": _VIDEO,
    "movie run": _VIDEO,
    "data download": "download",
    "image upload": "upload",
    "data sync": "reconcile",
    "credits user": "credits.balance",
    "credits list": "credits.balance",
    "auth": None,
    "auth list": None,
    "auth login": None,
    "auth logout": None,
    "auth renew": None,
    "auth status": None,
    "auth use": None,
    "capabilities": None,
    "character list": None,
    "character rm": None,
    "character show": None,
    "character voices": None,
    "data errors export": None,
    "data errors prune": None,
    "data list errors": None,
    "data list images": None,
    "data list profiles": None,
    "data list projects": None,
    "data list videos": None,
    "data media": None,
    "data prune": None,
    "data stats": None,
    "docs": None,
    "doctor": None,
    "image upscale": None,
    "instructions add": None,
    "instructions apply": None,
    "instructions disable": None,
    "instructions enable": None,
    "instructions list": None,
    "instructions rm": None,
    "instructions toggle-mode": None,
    "mcp run": None,
    "mcp setup": None,
    "models": None,
    "models list": None,
    "models show": None,
    "movie template": None,
    "project create": None,
    "project list": None,
    "project rename": None,
    "project show": None,
    "scene create": None,  # credit-free server-side composition of existing clips
    "scene show": None,
    "serve": None,
    "telemetry off": None,
    "telemetry on": None,
    "telemetry show": None,
    "telemetry status": None,
    "tools list": None,
    "tools run": None,
    "tools show": None,
    "update": None,
    "video upscale": None,
}


@pytest.mark.parametrize("host", _HOSTS)
@pytest.mark.parametrize("no_spend", [False, True])
def test_every_value_is_in_the_enum_and_every_partial_has_a_note(host: str, no_spend: bool) -> None:
    doc = caps.build_capabilities(host, no_spend=no_spend)
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
        _IMAGE,
        _VIDEO,
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


def test_download_is_partial_because_recovery_refuses_images() -> None:
    doc = caps.build_capabilities("auto")
    assert doc["capabilities"]["download"] == "partial"
    assert "#877" in doc["notes"]["download"]


def test_upload_note_depends_on_the_host() -> None:
    labs = caps.build_capabilities("labs.google")["notes"]["upload"]
    migrated = caps.build_capabilities("flow.google.com")["notes"]["upload"]
    assert labs != migrated
    # On flow.google.com an uploaded id is not taken as a video frame/reference (exit
    # 36), and as an image reference only when the catalog records it in this project.
    assert "exit 36" in migrated
    assert "--project" in migrated


def test_generation_is_partial_or_unknown_never_a_bare_yes() -> None:
    # labs.google: gflow's labs driver takes every mode, but whether Flow serves the
    # account labs at all is not checkable offline (exit 36 otherwise).
    labs = caps.build_capabilities("labs.google")["capabilities"]
    assert labs[_IMAGE] == labs[_VIDEO] == "unknown"
    for host in ("auto", "flow.google.com"):
        doc = caps.build_capabilities(host)
        assert doc["capabilities"][_IMAGE] == "partial"
        assert doc["capabilities"][_VIDEO] == "partial"
        assert "#799" in doc["notes"][_VIDEO]


@pytest.mark.parametrize("host", ("auto", "flow.google.com"))
def test_partial_generation_notes_name_what_is_missing(host: str) -> None:
    """A `partial` says what is missing, not only what works (migrated_composer)."""
    notes = caps.build_capabilities(host)["notes"]
    image = notes[_IMAGE]
    for missing in ("Agent instructions", "image4", "`image batch`", "#995"):
        assert missing in image, f"{_IMAGE} note does not name {missing!r}"
    assert "exit 36" in image
    video = notes[_VIDEO]
    for missing in ("UUID", "@Name", "exit 36"):
        assert missing in video, f"{_VIDEO} note does not name {missing!r}"


def test_no_note_asserts_unmeasured_billing() -> None:
    for host in _HOSTS:
        for key, note in caps.build_capabilities(host)["notes"].items():
            assert "is charged" not in note, f"{key}: say 'may still be charged'"


def test_task_notes_name_the_tasks_extension() -> None:
    notes = caps.build_capabilities("auto")["notes"]
    for key in ("submit.no_wait", "status", "abort.local"):
        assert "SEP-2663" in notes[key], f"{key} must say tasks/* needs a SEP-2663 client"


def test_no_note_claims_what_a_host_can_do() -> None:
    """AGENTS.md Host-Membership Discipline: report what gflow drives, not what a host can."""
    banned = ("labs-only", "labs only", "flow.google.com cannot", "migrated account")
    for host in _HOSTS:
        for note in caps.build_capabilities(host)["notes"].values():
            assert not any(phrase in note.lower() for phrase in banned), note


def test_host_follows_the_flow_host_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", "flow.google.com")
    assert caps.current_capabilities()["host"] == "flow.google.com"


# --- the two surfaces -----------------------------------------------------------------


async def _read_resource(mcp_server: Any) -> dict[str, Any]:
    from mcp import Client

    async with Client(mcp_server, mode="2026-07-28") as client:
        read = await client.read_resource("capabilities://self")
    return json.loads(read.contents[0].text)  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_cli_json_and_mcp_resource_serialize_identically(mcp_server: Any) -> None:
    from mcp import Client

    result = CliRunner().invoke(main, ["capabilities", "--json"])
    assert result.exit_code == 0, result.output

    async with Client(mcp_server, mode="2026-07-28") as client:
        read = await client.read_resource("capabilities://self")
    text = read.contents[0].text  # pyright: ignore[reportAttributeAccessIssue]

    assert result.stdout.strip() == text.strip()


@pytest.mark.asyncio
async def test_no_spend_server_reports_generation_as_no(
    mcp_server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#495 rule: a resource names only registered tools. No-spend registers no generate."""
    monkeypatch.setenv("GFLOW_MCP_NO_SPEND", "1")
    doc = await _read_resource(mcp_server)
    for key in (_IMAGE, _VIDEO, "submit.no_wait"):
        assert doc["capabilities"][key] == "no", key
        assert "no-spend" in doc["notes"][key], key
    assert not any("gflow_generate_" in note for note in doc["notes"].values())


def test_cli_ignores_the_mcp_no_spend_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_MCP_NO_SPEND", "1")
    result = CliRunner().invoke(main, ["capabilities", "--json"])
    assert json.loads(result.stdout)["capabilities"][_IMAGE] == "partial"


def test_cli_table_lists_every_key() -> None:
    result = CliRunner().invoke(main, ["capabilities"])
    assert result.exit_code == 0, result.output
    for key in caps.build_capabilities("auto")["capabilities"]:
        assert key in result.output


def test_capabilities_doc_json_block_matches_the_code() -> None:
    """docs/CAPABILITIES.md hand-copies the default document; keep it true."""
    text = (_REPO / "docs" / "CAPABILITIES.md").read_text(encoding="utf-8")
    block = re.search(r"```json\n(.*?)\n```", text, re.DOTALL)
    assert block, "docs/CAPABILITIES.md lost its JSON block"
    doc = json.loads(re.sub(r',\s*"notes":.*\}\s*$', "}", block.group(1), flags=re.DOTALL))
    expected = caps.build_capabilities("auto")
    assert doc["capabilities"] == expected["capabilities"]
    assert {k: doc[k] for k in ("schema", "adapter", "host")} == {
        k: expected[k] for k in ("schema", "adapter", "host")
    }


# --- notes that say "not built yet" must not go stale --------------------------------


def test_lifecycle_verbs_the_notes_call_unbuilt_still_do_not_exist() -> None:
    """When #1194/#1198/#1199/#1200 land, this fails: update the capability values."""
    built = {"status", "wait", "cancel", "reconcile", "upload"} & set(main.commands)
    no_wait = [
        path
        for path in ("image t2i", "video t2v", "video i2v", "video r2v")
        if "no_wait" in {p.name for p in _command(path).params}
    ]
    assert not built and not no_wait, (
        f"Now built: {sorted(built) + no_wait}. Update the capability value and note in "
        "cli_capabilities.py (and docs/CAPABILITIES.md), then this list."
    )


def _command(path: str) -> click.Command:
    cmd: Any = main
    for part in path.split():
        cmd = cmd.commands[part]
    return cmd


# --- every CLI command has a capability decision -------------------------------------


def _undecided(group: click.Group) -> set[str]:
    return cli_leaves(group) - set(COMMAND_CAPABILITY)


def test_every_cli_command_has_a_capability_entry() -> None:
    undecided = _undecided(main)
    assert not undecided, (
        f"CLI commands with no entry in COMMAND_CAPABILITY (this file): "
        f"{sorted(undecided)}. Map each to the capability key it exercises, or to None "
        "when it is outside the generation-lifecycle vocabulary, then check that "
        "cli_capabilities.py and docs/CAPABILITIES.md still describe it."
    )


def test_coverage_check_bites_on_a_new_command() -> None:
    probe = click.Group(commands=dict(main.commands))
    probe.add_command(click.Command("brand-new"))
    assert _undecided(probe) == {"brand-new"}


def test_no_stale_command_entries() -> None:
    stale = set(COMMAND_CAPABILITY) - cli_leaves(main)
    assert not stale, f"Capability entries for commands that no longer exist: {sorted(stale)}"


def test_every_mapped_key_exists_in_the_document() -> None:
    keys = set(caps.build_capabilities("auto")["capabilities"])
    unknown = {k for k in COMMAND_CAPABILITY.values() if k is not None} - keys
    assert not unknown, f"COMMAND_CAPABILITY names keys the document lacks: {sorted(unknown)}"
