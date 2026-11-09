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

import pytest
from click.testing import CliRunner

from gflow_cli import __version__
from gflow_cli._cli_helpers import cli_leaves, spends_credits_decision
from gflow_cli.cli import main

REPO = Path(__file__).resolve().parents[2]

# Snapshot of every leaf and its `spends_credits` decision. Paths and the decision only,
# never help prose, so a wording edit does not break it. A new command fails here AND in
# the coverage test below until it carries `@spends_credits(True|False)`.
# Rule: True means the command submits a new Flow generation (video spends Veo credits,
# image spends the daily image quota). Reads, exports, upscales and local work are False.
EXPECTED_TREE: dict[str, bool] = {
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


def test_every_command_appears_in_docs() -> None:
    # User-facing pages only: the bundled top-level docs/*.md, minus release ledgers.
    pages = [p for p in (REPO / "docs").glob("*.md") if not p.name.startswith("LIVE_VERIFICATION")]
    text = "\n".join(p.read_text(encoding="utf-8") for p in pages)
    missing = [
        "gflow " + " ".join(p)
        for p, _, _ in cli_leaves(main)
        if not re.search(re.escape("gflow " + " ".join(p)) + r"(?![\w-])", text)
    ]
    assert not missing, f"Commands not documented in docs/*.md: {missing}"


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
