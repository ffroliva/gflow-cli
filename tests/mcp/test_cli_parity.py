# SPDX-License-Identifier: MIT
"""MCP↔CLI parity contract (AGENTS.md "MCP & CLI Schema Symmetry").

Every CLI leaf command must carry an explicit MCP decision: either it maps to
a registered MCP tool (``CLI_TO_MCP``) or it is deliberately exempt with a
stated reason (``_MCP_EXEMPT``). A new CLI command with neither entry fails
``test_every_cli_leaf_has_an_mcp_decision`` — forcing the parity decision at
review time instead of letting the surfaces drift apart silently
(2026-07-09 skills-audit council, Task 6).

Note on ``gflow_generate_video`` and instructions: unlike the image path, the
video pipeline (``GenerateVideoRequest`` / the worker's ``_build_video_request``)
has no instructions support — agentic-video is a deliberate typed divergence
(``drivers/agentic.py`` raises ``FlowAgentUiError``). An ``instructions`` param
on the video tool would be silently dropped, so it is intentionally absent.

Note on ``--output``/``-o`` (#411): mirrored since the wiring landed — both
generate tools accept ``output`` (``tools.py``) and put ``output_file`` in the
task payload, which the daemon decodes and relocates the artifact to
(``worker/daemon.py``). A v0.48.0 pre-release audit removed an earlier dead
param that the queue never read; the docstring here claimed that state long
after the real wiring shipped (#495).

Note on ``image t2i --jitter`` (#241): intentionally NOT mirrored on
``gflow_generate_image``. The jitter paces submissions *between prompts* in a
multi-prompt run; the MCP tool is single-prompt, so the parameter would be a
silent no-op there. An MCP agent composing several calls owns its own cadence
(or sets ``GFLOW_CLI_JITTER_RANGE`` server-side, which the batch paths honour).
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import click

from gflow_cli.cli import main
from gflow_cli.mcp import tools as mcp_tools

# CLI leaf → MCP tool that covers it. One tool may cover several leaves when a
# parameter selects the behaviour (e.g. gflow_generate_video's ``mode``).
CLI_TO_MCP: dict[str, str] = {
    "character list": "gflow_character_list",
    "character voices": "gflow_character_voices",
    "character show": "gflow_character_show",
    "credits user": "gflow_get_credits",
    "credits list": "gflow_get_credits",
    "image t2i": "gflow_generate_image",
    "image i2i": "gflow_generate_image",  # reference_images param
    "video t2v": "gflow_generate_video",  # mode="t2v"
    "video i2v": "gflow_generate_video",  # mode="i2v"
    "video r2v": "gflow_generate_video",  # mode="r2v"
    "tools list": "gflow_list_tools",
    "tools show": "gflow_list_tools",  # list output carries the show detail
    "auth status": "gflow_auth_status",
    "data list projects": "gflow_list_projects",
    "project list": "gflow_list_projects",
    "instructions list": "gflow_instructions_list",
    "instructions add": "gflow_instructions_add",
    "instructions enable": "gflow_instructions_set_enabled",  # enabled=True
    "instructions disable": "gflow_instructions_set_enabled",  # enabled=False
    "instructions rm": "gflow_instructions_rm",
    "instructions apply": "gflow_instructions_apply",
    "instructions toggle-mode": "gflow_instructions_toggle_mode",
}

# CLI leaf → reason it deliberately has NO MCP surface. "not yet ported"
# entries are backlog, not policy — moving one to CLI_TO_MCP is the upgrade
# path. Everything else is a considered exclusion.
_MCP_EXEMPT: dict[str, str] = {
    # Deliberately deferred, not overlooked (2026-09-01). Three reasons, any one
    # of which would be enough on its own:
    #  1. A chained run is minutes long and is not wired to FlowWorker, so it
    #     would block an MCP client's tool call past its timeout — the exact
    #     hazard issue #481 exists to address.
    #  2. The feature has an open defect (a 7s segment padded into an 8s slot
    #     produces a frozen, silent second at each internal seam — KNOWN_ISSUES).
    #     Widening the surface before that is settled multiplies the blast radius.
    #  3. It spends credits per segment behind a confirmation prompt that has no
    #     MCP equivalent; an agent could not give informed consent on the user's
    #     behalf.
    # Revisit once extend is enqueued through the worker like the generate tools.
    "video extend": (
        "long-running billed chain; not worker-enqueued, and its cost "
        "confirmation has no MCP equivalent (#481)"
    ),
    "auth": "interactive session management — needs a human browser login flow",
    "auth list": "interactive session management",
    "auth login": "interactive session management",
    "auth logout": "interactive session management",
    "auth use": "interactive session management",
    "mcp run": "the MCP server bootstrap itself",
    "mcp setup": "client-config generator for the MCP server itself",
    "serve": "HTTP/SSE service bootstrap",
    "update": (
        "self-update of the running install — the manager would replace the venv "
        "under a live `gflow mcp run` and write its output onto the JSON-RPC stdout "
        "channel; the operator runs `gflow update`. A read-only twin of `--check` "
        "would be harmless and is the upgrade path if agents ever need it"
    ),
    "models": "informational; models are enumerated in the generate tools' descriptions",
    "run": "chain-manifest runner — not yet ported",
    "character create": (
        "GAP #691 — unported, not principled. 'Mutation' was never a reason: "
        "gflow_generate_image spends the same per-model image quota over MCP. "
        "The port is an adapter over services/character_create.py; what needs "
        "deciding is worker-enqueue vs inline (#481) and whether it should honour "
        "GFLOW_MCP_NO_SPEND"
    ),
    "character rm": (
        "irreversible deletion of a user-owned Flow entity — deliberately CLI-only, "
        "same rule as `data prune`/`data errors prune`. The CLI gates it behind a "
        "confirmation prompt that has no MCP equivalent, so an agent cannot take "
        "informed consent on the user's behalf (the #481 shape). Note it is FREE, "
        "so cost is NOT the reason — irreversibility is"
    ),
    "data errors export": "local catalog maintenance — deliberately CLI-only (#345)",
    "data errors prune": "destructive local retention — deliberately CLI-only (#345)",
    "data list errors": "local catalog maintenance — not yet ported",
    "data list images": "local catalog maintenance — not yet ported",
    "data list profiles": "local catalog maintenance — not yet ported",
    "data list videos": "local catalog maintenance — not yet ported",
    "data media": "local catalog maintenance — not yet ported",
    "data prune": "destructive local cleanup — deliberately CLI-only",
    "data sync": "browser-driving reconciliation; MCP exposure deferred (#543)",
    "doctor": "interactive diagnostic; MCP tool deferred (#542)",
    "image batch": "batch pipelines — not yet ported",
    "image upload": "asset upload — covered indirectly by reference_images paths",
    "image upscale": "not yet ported",
    "video chain": "chain pipeline — not yet ported",
    "movie run": "movie pipeline — not yet ported (skills-audit Task 7 backlog)",
    "movie template": "movie pipeline — not yet ported (skills-audit Task 7 backlog)",
    "project create": "project management — not yet ported",
    "project rename": "project management — not yet ported",
    "project show": "project management — not yet ported",
    "scene create": "scene tooling — not yet ported",
    "scene show": "scene tooling — not yet ported",
    "tools run": "standalone tool run — exercised via the `tools` param on the generate tools",
}


def _cli_leaves() -> set[str]:
    """Every invokable CLI path: plain commands + invoke_without_command groups."""
    leaves: set[str] = set()

    def _walk(group: click.Group, prefix: str) -> None:
        for name, cmd in group.commands.items():
            path = f"{prefix}{name}"
            if isinstance(cmd, click.Group):
                if cmd.invoke_without_command:
                    leaves.add(path)
                _walk(cmd, f"{path} ")
            else:
                leaves.add(path)

    _walk(main, "")
    return leaves


def test_every_cli_leaf_has_an_mcp_decision() -> None:
    leaves = _cli_leaves()
    undecided = leaves - set(CLI_TO_MCP) - set(_MCP_EXEMPT)
    assert not undecided, (
        f"CLI commands with no MCP decision: {sorted(undecided)}. "
        "Add each to CLI_TO_MCP (and register the tool) or to _MCP_EXEMPT "
        "with a reason — see AGENTS.md 'MCP & CLI Schema Symmetry'."
    )


def test_no_leaf_is_both_mapped_and_exempt() -> None:
    both = set(CLI_TO_MCP) & set(_MCP_EXEMPT)
    assert not both, f"Ambiguous parity decision (mapped AND exempt): {sorted(both)}"


def test_no_stale_parity_entries() -> None:
    # A renamed/removed CLI command must not leave a dangling decision behind.
    leaves = _cli_leaves()
    stale = (set(CLI_TO_MCP) | set(_MCP_EXEMPT)) - leaves
    assert not stale, f"Parity entries for CLI commands that no longer exist: {sorted(stale)}"


def test_mapped_tools_are_registered(mcp_server: Any) -> None:
    registered = set(mcp_server._tool_manager._tools)
    missing = set(CLI_TO_MCP.values()) - registered
    assert not missing, f"CLI_TO_MCP references unregistered MCP tools: {sorted(missing)}"


# --- Option-level parity ------------------------------------------------------
#
# The leaf-level checks above are satisfied by a *mapping*. They stay green while a
# CLI option never reaches the tool -- which is how `--reference-entity` came to exist
# on four leaves with no MCP equivalent anywhere (#689), leaving the identity axis
# silently CLI-only. AGENTS.md § "MCP parity is a law" is the rule; this is its teeth.

#: CLI concerns an MCP tool structurally cannot have -- presentation and local-file
#: plumbing. A tool returns structured data to a caller; it does not format or redirect.
_CLI_ONLY_PARAMS: frozenset[str] = frozenset(
    {
        "as_json",  # MCP always returns structured data
        "out",
        "out_dir",
        "output_file",  # the tool reports paths, it does not choose them
        "read_stdin",
        "prompts_file",  # shell-input plumbing
        "transport",  # operator concern
        "jitter_spec",  # pacing knob for shell loops (#241)
    }
)

#: CLI name -> MCP name where one concept is deliberately spelled differently. Checked
#: as "either spelling satisfies parity", because a rename is per-tool: `refs` means
#: `reference_images` on the generate tools but stays `refs` on instructions_add.
#: Keep this small -- a rename with no reason is drift, not translation.
_PARAM_ALIASES: dict[str, str] = {
    "project_id": "project",
    "refs": "reference_images",
    "tool_specs": "tools",
    "image": "initial_frame",
    "enable_mode": "enabled",  # CLI --enable-mode / tool `enabled`: same switch
    "disabled": "enabled",
}

#: "<leaf>:<cli param>" -> why the tool does not carry it. A GAP entry is a tracked
#: admission, NOT an exemption: it exists so a known hole cannot widen unnoticed.
_OPTION_EXEMPT: dict[str, str] = {
    "image t2i:prompts": "multi-prompt fan-out is a batch concern; `image batch` is exempt too",
    "image t2i:continue_on_error": "batch-only semantics — see `image t2i:prompts`",
    "instructions apply:file": "reads a local file; the tool takes the text directly",
    "tools show:name": "coarse mapping — one MCP tool lists and shows; the name is its filter",
    "video i2v:end_image_deprecated": "deprecated alias retained for CLI compatibility only",
}


def _leaf_params(path: str) -> set[str]:
    """Click parameter names for one CLI leaf."""
    cmd: Any = main
    for part in path.split():
        cmd = cmd.commands[part]
    return {p.name for p in cmd.params if p.name}


def _tool_params(tool_name: str) -> set[str]:
    """Declared parameter names of a registered MCP tool, unwrapping decorators."""
    fn: Any = getattr(mcp_tools, tool_name)
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return set(inspect.signature(fn).parameters) - {"self"}


def test_every_cli_option_reaches_its_mcp_tool() -> None:
    """A mapped leaf's options must exist on the tool, be CLI-only, or be recorded.

    This is the check command-level parity cannot make. Its first run found #689.
    """
    unmirrored: list[str] = []
    for leaf, tool in sorted(CLI_TO_MCP.items()):
        if not hasattr(mcp_tools, tool):
            continue  # test_mapped_tools_are_registered owns that failure
        tool_params = _tool_params(tool)
        for raw in sorted(_leaf_params(leaf) - _CLI_ONLY_PARAMS):
            candidates = {raw, _PARAM_ALIASES.get(raw, raw)}
            if candidates & tool_params or f"{leaf}:{raw}" in _OPTION_EXEMPT:
                continue
            unmirrored.append(f"{leaf} --{raw.replace('_', '-')} -> {tool}")
    assert not unmirrored, (
        "CLI options with no MCP equivalent and no recorded reason:\n  "
        + "\n  ".join(unmirrored)
        + "\n\nAGENTS.md: MCP parity is a law. Mirror the option onto the tool, or add it "
        "to _OPTION_EXEMPT with a reason (a GAP entry must carry an issue number)."
    )


def test_no_stale_option_exemptions() -> None:
    """An exemption that no longer describes reality is worse than none."""
    stale: list[str] = []
    for key in sorted(_OPTION_EXEMPT):
        leaf, _, param = key.rpartition(":")
        if leaf not in CLI_TO_MCP:
            stale.append(f"{key} (leaf is no longer mapped)")
        elif param not in _leaf_params(leaf):
            stale.append(f"{key} (CLI option no longer exists)")
    assert not stale, f"Stale option exemptions: {stale}"


def test_every_recorded_gap_carries_an_issue() -> None:
    """A GAP is an admission with an owner. Without an issue it is just an excuse."""
    untracked = [
        k for k, why in _OPTION_EXEMPT.items() if "GAP" in why and not re.search(r"#\d+", why)
    ]
    assert not untracked, f"GAP exemptions with no tracking issue: {untracked}"
