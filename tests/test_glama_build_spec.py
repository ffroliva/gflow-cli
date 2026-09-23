# SPDX-License-Identifier: MIT
"""`glama.json` is the container build spec Glama runs — keep it honest offline.

Glama builds an image from this spec and starts the server inside it. If the container
cannot start, the server is listed but **excluded from search results**, and the
`awesome-mcp-servers` listing that gates on a Glama score stays blocked. None of that
surfaces as a test failure anywhere else, because the spec used to live only in a web
form on glama.ai — nothing in this repository described it, so nothing could check it.

That is not hypothetical. On 2026-09-15 two builds failed with::

    could not start the proxy Error: spawn gflow ENOENT

The image built fine. `uv sync` installs the project into a **project virtualenv**
(`/app/.venv`) and puts its console scripts in `/app/.venv/bin` — a directory that is
never added to `PATH`. So a `CMD` naming a bare `gflow` resolves against `PATH`, misses,
and the container exits before answering a single request.

These tests pin the spec to the two files it has to agree with (`pyproject.toml` for the
entry point, and the other distribution artifacts for the subcommand), and they encode
the venv/`PATH` rule that the failure taught us. Deliberately offline and Docker-free:
this is the layer that runs on every commit.

The end-to-end proof is Glama's own build test, which builds this spec, starts the
container and drives the protocol. Build ``01a0a3c1-c0a3-7462-bb4b-350a715ca63b``
succeeded in 31.2s and answered `initialize` plus `tools/list` with 15 tools, 2 prompts
and 3 resources — no browser, no credentials. Re-run it from the server's Glama admin
page after changing this file; these offline tests cannot tell you the image still boots.
"""

from __future__ import annotations

import json
import posixpath
import tomllib
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[1]
_GLAMA_JSON = _REPO / "glama.json"
_PYPROJECT = _REPO / "pyproject.toml"

#: Where `uv sync` puts console scripts, given `WORKDIR /app`. Not on `PATH`.
_UV_VENV_BINDIR = "/app/.venv/bin"


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return json.loads(_GLAMA_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pyproject() -> dict[str, Any]:
    return tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cmd(spec: dict[str, Any]) -> list[str]:
    value = spec["cmdArguments"]
    assert isinstance(value, list) and all(isinstance(a, str) for a in value), value
    return value


def _server_argv(cmd: list[str]) -> list[str]:
    """The part after mcp-proxy's `--` separator: the command Glama actually spawns."""
    assert "--" in cmd, f"cmdArguments must separate proxy args from ours with `--`: {cmd}"
    return cmd[cmd.index("--") + 1 :]


# --- the proxy that fronts us -------------------------------------------------------


def test_the_stdio_server_is_fronted_by_mcp_proxy(cmd: list[str]) -> None:
    """Glama pings over HTTP; our server speaks stdio. `mcp-proxy` is the bridge.

    The build image installs `mcp-proxy` globally and the successful run logged
    `starting server on port 8080` from it. Spawning the server without the proxy would
    still start gflow — and still fail the check, because nothing would be listening.
    That is the same shape as the ENOENT bug: a container that runs but never answers.
    """
    prefix = cmd[: cmd.index("--")] if "--" in cmd else cmd
    assert prefix == ["mcp-proxy"], f"expected the server to be fronted by mcp-proxy, got {prefix}"


# --- the entry point ----------------------------------------------------------------


def test_the_spawned_executable_is_a_console_script_this_project_defines(
    cmd: list[str], pyproject: dict[str, Any]
) -> None:
    """A renamed or dropped entry point must fail here, not in a container we never run."""
    executable = posixpath.basename(_server_argv(cmd)[0])
    scripts = pyproject["project"]["scripts"]
    assert executable in scripts, (
        f"glama.json starts {executable!r}, but [project.scripts] defines only {sorted(scripts)}"
    )


def test_the_executable_is_an_absolute_path_into_the_uv_venv(cmd: list[str]) -> None:
    """The regression test for `spawn gflow ENOENT`.

    `uv sync` never puts the console script on `PATH`. A bare name, or any path outside
    the project virtualenv, means the container exits before it answers a ping.
    """
    executable = _server_argv(cmd)[0]
    assert executable.startswith("/"), (
        f"{executable!r} is resolved against PATH, and `uv sync` does not put the "
        f"console script on PATH — spell the absolute path"
    )
    assert posixpath.dirname(executable) == _UV_VENV_BINDIR, (
        f"{executable!r} is not in {_UV_VENV_BINDIR}, which is where `uv sync` "
        f"installs console scripts under WORKDIR /app"
    )


def test_the_build_step_is_what_creates_that_venv(spec: dict[str, Any]) -> None:
    """The absolute path above is only correct because the build runs `uv sync`."""
    assert "uv sync" in spec["buildSteps"], (
        f"cmdArguments points into {_UV_VENV_BINDIR}, so a build step must create it; "
        f"buildSteps is {spec['buildSteps']}"
    )


# --- agreement with the other distribution artifacts --------------------------------


def test_the_subcommand_matches_every_other_distribution_artifact(cmd: list[str]) -> None:
    """`server.json` and the Claude Code plugin both spell it `mcp run`. Drift is a bug."""
    assert _server_argv(cmd)[1:] == ["mcp", "run"], _server_argv(cmd)


def test_the_server_starts_without_credentials(spec: dict[str, Any]) -> None:
    """Glama can only check servers that start unauthenticated.

    gflow needs a logged-in Chrome profile to *generate*, but not to enumerate its tools,
    so `placeholderArguments` stays empty on purpose. A non-empty value here would mean
    someone decided the server needs secrets to boot — which would take it out of Glama's
    checkable set entirely, and that deserves to be a deliberate, reviewed change.
    """
    assert spec["placeholderArguments"] == {}, spec["placeholderArguments"]
