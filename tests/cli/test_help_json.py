"""`gflow [<group> [<cmd>]] --help --json` — the machine-readable command tree (#1203)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from gflow_cli import __version__
from gflow_cli._cli_helpers import (
    cli_leaves,
    command_tree,
    spends_credits,
    spends_credits_decision,
)
from gflow_cli.cli import _RootGroup, main

REPO = Path(__file__).resolve().parents[2]

# Snapshot of every leaf and its `spends_credits` decision. Paths and the decision only,
# never help prose, so a wording edit does not break it. A new command fails here AND in
# the coverage test below until it carries `@spends_credits(True|False)`.
# Rule: True means the command submits a new Flow generation (video spends Veo credits,
# image spends the daily image quota). Reads, exports, upscales and local work are False.
EXPECTED_TREE: dict[str, bool] = {
    "auth": False,
    "auth login": False,
    "auth status": False,
    "auth renew": False,
    "auth list": False,
    "auth use": False,
    "auth logout": False,
    "character create": True,
    "character list": False,
    "character show": False,
    "character rm": False,
    "character voices": False,
    "credits user": False,
    "credits list": False,
    "data media": False,
    "data download": False,
    "data list projects": False,
    "data list images": False,
    "data list videos": False,
    "data list errors": False,
    "data list profiles": False,
    "data stats": False,
    "data prune": False,
    "data errors export": False,
    "data errors prune": False,
    "data sync": False,
    "doctor": False,
    "docs": False,
    "update": False,
    "movie template": False,
    "movie run": True,
    "project list": False,
    "project show": False,
    "project create": False,
    "project rename": False,
    "video t2v": True,
    "video i2v": True,
    "video r2v": True,
    "video chain": True,
    "video extend": True,
    "video upscale": False,
    "image upload": False,
    "image upscale": False,
    "image t2i": True,
    "image batch": True,
    "image i2i": True,
    "instructions add": False,
    "instructions list": False,
    "instructions enable": False,
    "instructions disable": False,
    "instructions rm": False,
    "instructions apply": False,
    "instructions toggle-mode": False,
    "run": True,
    "models": False,
    "scene create": False,
    "scene show": False,
    "telemetry status": False,
    "telemetry on": False,
    "telemetry off": False,
    "telemetry show": False,
    "tools list": False,
    "tools show": False,
    "tools run": False,
    "mcp run": False,
    "mcp setup": False,
    "serve": False,
}


def _tree(*args: str) -> dict[str, Any]:
    result = CliRunner().invoke(main, list(args), catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _paths(tree: dict[str, Any]) -> list[str]:
    return [" ".join(c["path"]) for c in tree["commands"]]


def test_root_help_json_is_the_whole_tree() -> None:
    tree = _tree("--help", "--json")
    assert (tree["schema"], tree["name"], tree["version"]) == (1, "gflow", __version__)
    t2v = next(c for c in tree["commands"] if c["path"] == ["video", "t2v"])
    assert t2v["spends_credits"] is True
    assert t2v["aliases"] == []
    assert t2v["summary"]
    json_flag = next(p for p in t2v["params"] if p["name"] == "--json")
    assert json_flag["type"] == "flag"
    assert json_flag["default"] is False
    assert json_flag["required"] is False
    assert json_flag["help"]
    prompt = next(p for p in t2v["params"] if p["name"] == "PROMPT")
    assert prompt["required"] is True


def test_group_and_leaf_help_json_narrow_the_tree() -> None:
    video = _paths(_tree("video", "--help", "--json"))
    assert video and all(p.startswith("video ") for p in video)
    assert _paths(_tree("video", "t2v", "--json", "--help")) == ["video t2v"]
    assert _paths(_tree("data", "list", "--help", "--json")) == [
        k for k in EXPECTED_TREE if k.startswith("data list ")
    ]


def test_unknown_command_is_still_a_usage_error() -> None:
    result = CliRunner().invoke(main, ["vdeo", "--help", "--json"])
    assert result.exit_code == 2
    assert "No such command" in result.output


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["-v", "video", "t2v", "--help", "--json"], ["video t2v"]),
        (["--verbose", "auth", "list", "--help", "--json"], ["auth list"]),
    ],
)
def test_root_flags_before_the_command(args: list[str], expected: list[str]) -> None:
    assert _paths(_tree(*args)) == expected


def test_invoke_without_command_group_is_a_command() -> None:
    assert _paths(_tree("auth", "--help", "--json"))[0] == "auth"


def test_an_option_value_is_not_a_help_request() -> None:
    # `--search` takes "--help" as its term, so this is a docs search, not a help request.
    result = CliRunner().invoke(main, ["docs", "--search", "--help", "--json"])
    assert '"spends_credits"' not in result.output


def test_shell_completion_does_not_print_the_tree() -> None:
    # Completing the empty word after `--json`, so both flags are already on the line.
    env = {"_GFLOW_COMPLETE": "bash_complete", "COMP_WORDS": "gflow video --help --json "}
    env["COMP_CWORD"] = "4"
    result = CliRunner().invoke(main, [], env=env, prog_name="gflow")
    assert '"spends_credits"' not in result.output


def test_params_carry_opts_choices_and_repeatability() -> None:
    def param(path: str, name: str) -> dict[str, Any]:
        cmd = next(c for c in _tree("--help", "--json")["commands"] if " ".join(c["path"]) == path)
        return next(p for p in cmd["params"] if p["name"] == name)

    assert "--off" in param("instructions toggle-mode", "--on")["opts"]
    assert param("run", "--continue-on-error")["opts"] == ["--continue-on-error", "--fail-fast"]
    assert param("project create", "--name")["opts"] == ["--name", "--title", "--project-name"]
    assert param("video t2v", "--aspect")["choices"] == ["9:16", "16:9"]
    assert param("video t2v", "--tool")["multiple"] is True
    assert param("video t2v", "PROMPT")["nargs"] == 1


def test_every_command_has_a_summary() -> None:
    assert all(c["summary"] for c in _tree("--help", "--json")["commands"])


def _toy_root() -> click.Group:
    @spends_credits(False)
    @click.command()
    def ping() -> None:
        """Ping."""

    @click.group(hidden=True)
    def secret() -> None:
        """Hidden group."""

    secret.add_command(spends_credits(False)(click.Command("inner", callback=lambda: None)))

    @click.group(cls=_RootGroup)
    def root() -> None:
        """Toy root."""

    root.add_command(ping, "ping")
    root.add_command(ping, "pong")
    root.add_command(secret, "secret")
    return root


def test_one_command_under_two_names_is_one_entry_with_an_alias() -> None:
    tree = command_tree(_toy_root())
    assert [(c["path"], c["aliases"]) for c in tree["commands"]] == [(["ping"], ["pong"])]


def test_a_prefix_with_no_visible_command_is_a_usage_error() -> None:
    result = CliRunner().invoke(_toy_root(), ["secret", "--help", "--json"])
    assert result.exit_code == 2
    assert '"commands"' not in result.output


def test_plain_help_is_unchanged() -> None:
    result = CliRunner().invoke(main, ["video", "--help"], catch_exceptions=False)
    assert result.exit_code == 0
    assert result.output.startswith("Usage:")


def test_every_leaf_has_a_spends_credits_decision() -> None:
    undecided = [" ".join(p) for p, c, _ in cli_leaves(main) if spends_credits_decision(c) is None]
    assert not undecided, (
        "Decorate these commands with @spends_credits(True) if they submit a Flow "
        f"generation, else @spends_credits(False): {undecided}"
    )


def test_tree_snapshot() -> None:
    tree = _tree("--help", "--json")
    assert {" ".join(c["path"]): c["spends_credits"] for c in tree["commands"]} == EXPECTED_TREE


def test_every_command_appears_in_the_cli_reference() -> None:
    # docs/USAGE.md is the command reference. Other pages mention commands in passing,
    # which is not documentation, so they do not count.
    text = (REPO / "docs" / "USAGE.md").read_text(encoding="utf-8")
    missing = [
        "gflow " + " ".join(p)
        for p, _, _ in cli_leaves(main)
        if not re.search(re.escape("gflow " + " ".join(p)) + r"(?![\w-])", text)
    ]
    assert not missing, f"Commands not documented in docs/USAGE.md: {missing}"


def test_real_entrypoint_prints_parseable_json() -> None:
    exe = shutil.which("gflow", path=str(Path(sys.executable).parent))
    if exe is None:
        pytest.skip("gflow console script not installed in this venv")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUTF8"}
    out = subprocess.run(
        [exe, "video", "--help", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=True,
    )
    tree = json.loads(out.stdout)
    assert tree["name"] == "gflow"
    assert ["video", "t2v"] in [c["path"] for c in tree["commands"]]
