# SPDX-License-Identifier: MIT
"""`server.json` is the MCP Registry's copy of our metadata — keep it honest offline.

Publishing to the official MCP Registry uploads this file; the registry stores metadata
only and never runs the command, so a wrong entry point or a stale version is not caught
there — it is caught by a user whose client fails to start the server.

These tests pin the parts that can silently drift away from `pyproject.toml`, plus the two
constraints the published JSON-schema (`2025-12-11`) enforces at publish time and that are
cheap to get wrong: the one-slash name pattern and the 100-character description cap.

Deliberately offline. The schema lives at static.modelcontextprotocol.io, and a test that
reaches the network would fail in CI for reasons unrelated to this repo. What is asserted
here is exactly what a network fetch could not tell us anyway: agreement with *our* files.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SERVER_JSON = _REPO / "server.json"
_PYPROJECT = _REPO / "pyproject.toml"
_README = _REPO / "README.md"

#: From the published schema's `ServerDetail.name`.
_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$")
_DESCRIPTION_MAX = 100


@pytest.fixture(scope="module")
def server() -> dict[str, Any]:
    return json.loads(_SERVER_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pyproject() -> dict[str, Any]:
    return tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def package(server: dict[str, Any]) -> dict[str, Any]:
    packages = server["packages"]
    assert len(packages) == 1, "one PyPI package is the whole distribution story here"
    return packages[0]


def test_required_top_level_fields_are_present(server: dict[str, Any]) -> None:
    for field in ("name", "description", "version"):
        assert server.get(field), f"server.json is missing the required field {field!r}"


def test_name_matches_the_schema_pattern(server: dict[str, Any]) -> None:
    """Exactly one slash, and the namespace must be the GitHub one we authenticate as."""
    assert _NAME_PATTERN.match(server["name"]), server["name"]
    assert server["name"] == "io.github.ffroliva/gflow-cli"


def test_description_is_within_the_schema_cap(server: dict[str, Any]) -> None:
    """The schema caps this at 100; publish rejects a longer one."""
    assert len(server["description"]) <= _DESCRIPTION_MAX, len(server["description"])


def test_version_tracks_pyproject(server: dict[str, Any], pyproject: dict[str, Any]) -> None:
    """A release bumps pyproject; this catches the bump that forgot server.json."""
    assert server["version"] == pyproject["project"]["version"]


def test_package_version_tracks_pyproject(
    package: dict[str, Any], pyproject: dict[str, Any]
) -> None:
    assert package["version"] == pyproject["project"]["version"]
    assert package["version"] != "latest", "the schema rejects the literal 'latest'"


def test_package_identifies_the_published_pypi_distribution(
    package: dict[str, Any], pyproject: dict[str, Any]
) -> None:
    assert package["registryType"] == "pypi"
    assert package["identifier"] == pyproject["project"]["name"]
    assert package["transport"]["type"] == "stdio"


def test_the_advertised_command_is_actually_installed(
    package: dict[str, Any], pyproject: dict[str, Any]
) -> None:
    """`uvx <identifier> <args>` only works if a console script is named for the package.

    Without a `gflow-cli` entry point, uv answers "Use `uvx --from gflow-cli
    <EXECUTABLE-NAME>` instead" and every client built from this file fails to start the
    server. The schema has no field for an executable that differs from the identifier, so
    the entry point is the contract.
    """
    scripts = pyproject["project"]["scripts"]
    assert package["identifier"] in scripts, (
        f"server.json advertises `uvx {package['identifier']} ...` but "
        f"[project.scripts] defines only {sorted(scripts)}"
    )


def test_package_arguments_spell_the_stdio_subcommand(package: dict[str, Any]) -> None:
    values = [a["value"] for a in package["packageArguments"]]
    assert values == ["mcp", "run"], values


def test_readme_carries_the_ownership_token(server: dict[str, Any]) -> None:
    """PyPI ownership is proved by an `mcp-name:` token in the package description.

    The registry reads the PyPI long description — which is README.md — and looks for
    `mcp-name: <server name>`. No token, no verified publish.
    """
    readme = _README.read_text(encoding="utf-8")
    token = f"mcp-name: {server['name']}"
    assert token in readme, f"README.md must contain {token!r} for the registry to verify us"


def test_repository_points_at_this_project(server: dict[str, Any]) -> None:
    assert server["repository"]["source"] == "github"
    assert server["repository"]["url"] == "https://github.com/ffroliva/gflow-cli"


def test_declared_environment_variables_are_real_settings(package: dict[str, Any]) -> None:
    """Anything advertised here is a knob a client will offer to set — it must exist."""
    from gflow_cli.config import Settings

    aliases: set[str] = set()
    for name, field in Settings.model_fields.items():
        aliases.add(f"GFLOW_CLI_{name.upper()}")
        choices = getattr(field.validation_alias, "choices", None)
        if choices:
            aliases.update(str(c) for c in choices)

    declared = package.get("environmentVariables", [])
    assert declared, (
        "an empty list would make every assertion below vacuous; drop this test instead of "
        "letting it stand guard over nothing"
    )
    for env in declared:
        assert env["name"] in aliases, f"{env['name']} is advertised but is not a Settings field"
        assert not env.get("isSecret"), (
            f"{env['name']} is marked secret; a secret must not be advertised to clients here"
        )
