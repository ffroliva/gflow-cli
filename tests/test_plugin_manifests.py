# SPDX-License-Identifier: MIT
"""The plugin ships to strangers — pin what it contains and what it must never contain.

Three channels install from this repo: Claude Code (`.claude-plugin/marketplace.json`),
Codex (`.codex-plugin/plugin.json`) and ChatGPT desktop (`.agents/plugins/marketplace.json`).
All three used to point at the repo root or at `skills/`, which holds this project's own
development lifecycle — `release`, `check`, `pr-council-review`, `sonar`, `doc-review`. Those
are useless to a user driving Google Flow and actively misleading to their agent, and two of
the three channels were shipping them already.

So the curated payload under `plugins/gflow/` is generated (never hand-edited) by
`scripts/ci/generate_plugin_skills.py`, and these tests pin the properties a green
`--check` alone does not:

* no maintainer-only skill can reach a user through *any* of the three manifests;
* the version a user sees matches the package they get;
* the command the plugin declares is a console script that actually exists;
* links that pointed out of the repo were rewritten, and links that still resolve inside
  the installed plugin were left alone;
* the plugin cannot silently start a server that spends the user's money.

That last one is not hypothetical. Claude Code's docs are explicit: "When you enable a
plugin, Claude Code starts its MCP servers automatically" — there is no separate consent
prompt for a plugin-bundled server. `defaultEnabled: false` is the guard, so it is a test.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[1]
_MARKETPLACE = _REPO / ".claude-plugin" / "marketplace.json"
_PLUGIN_DIR = _REPO / "plugins" / "gflow"
_PLUGIN_MANIFEST = _PLUGIN_DIR / ".claude-plugin" / "plugin.json"
_MCP_JSON = _PLUGIN_DIR / ".mcp.json"
_CODEX = _REPO / ".codex-plugin" / "plugin.json"
_AGENTS = _REPO / ".agents" / "plugins" / "marketplace.json"

#: Kept in step with generate_plugin_skills.SHIPPED — asserted below, not assumed.
_SHIPPED = {"gflow-cli", "video-production"}

#: Names reserved by Anthropic for official marketplaces; a third party using one is blocked.
_RESERVED_MARKETPLACE_NAMES = {
    "claude-code-marketplace",
    "claude-code-plugins",
    "claude-plugins-official",
    "claude-plugins-community",
    "claude-community",
    "anthropic-marketplace",
    "anthropic-plugins",
    "agent-skills",
    "anthropic-agent-skills",
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def marketplace() -> dict[str, Any]:
    return _load(_MARKETPLACE)


@pytest.fixture(scope="module")
def plugin() -> dict[str, Any]:
    return _load(_PLUGIN_MANIFEST)


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def version() -> str:
    return str(_pyproject()["project"]["version"])


# --- the marketplace manifest ------------------------------------------------------


def test_marketplace_has_the_required_fields(marketplace: dict[str, Any]) -> None:
    for field in ("name", "owner", "plugins"):
        assert field in marketplace, f"marketplace.json is missing {field!r}"
    assert marketplace["owner"].get("name"), "owner.name is required"


def test_marketplace_name_is_not_reserved(marketplace: dict[str, Any]) -> None:
    assert marketplace["name"].lower() not in _RESERVED_MARKETPLACE_NAMES


def test_marketplace_entry_source_resolves_to_the_plugin(marketplace: dict[str, Any]) -> None:
    """Paths resolve against the marketplace root, not against `.claude-plugin/`."""
    entry = marketplace["plugins"][0]
    source = entry["source"]
    assert isinstance(source, str) and source.startswith("./"), source
    assert ".." not in source, "a source may not climb out of the marketplace root"
    assert (_REPO / source).resolve() == _PLUGIN_DIR.resolve()


# --- the plugin manifest -----------------------------------------------------------


def test_plugin_version_tracks_pyproject(plugin: dict[str, Any], version: str) -> None:
    assert plugin["version"] == version


def test_plugin_declares_identity_a_stranger_can_check(plugin: dict[str, Any]) -> None:
    assert plugin["name"] == "gflow"
    assert plugin["license"] == "MIT"
    assert plugin["repository"] == "https://github.com/ffroliva/gflow-cli"
    assert plugin["description"].strip()


# --- spend consent -----------------------------------------------------------------


def test_the_plugin_does_not_enable_itself(
    plugin: dict[str, Any], marketplace: dict[str, Any]
) -> None:
    """Enabling the plugin starts an MCP server that can spend the user's Veo credits.

    Claude Code starts a plugin's MCP servers automatically on enable, with no separate
    prompt, so shipping this enabled would mean a server that bills a real Google account
    starts because someone installed a plugin. Both the manifest and the marketplace entry
    say false; the entry wins where they differ, so both are pinned.
    """
    assert plugin.get("defaultEnabled") is False
    assert marketplace["plugins"][0].get("defaultEnabled") is False


def test_enabling_requires_an_explicit_billing_acknowledgement(plugin: dict[str, Any]) -> None:
    config = plugin.get("userConfig", {})
    assert config, "userConfig is the enable-time prompt; without it there is no consent step"
    required = [k for k, v in config.items() if v.get("required")]
    assert required, "at least one userConfig option must be required"
    text = " ".join(
        f"{v.get('title', '')} {v.get('description', '')}" for v in config.values()
    ).lower()
    assert "credit" in text or "bill" in text, (
        "the consent prompt must say that generation costs the user money"
    )


# --- the declared command ----------------------------------------------------------


def test_the_mcp_command_is_a_console_script_that_exists() -> None:
    """`command` is executed verbatim; a name with no entry point fails at enable time."""
    servers = _load(_MCP_JSON)["mcpServers"]
    assert set(servers) == {"gflow"}, servers
    scripts = _pyproject()["project"]["scripts"]
    assert servers["gflow"]["command"] in scripts, (
        f"the plugin runs {servers['gflow']['command']!r}, "
        f"but [project.scripts] defines only {sorted(scripts)}"
    )
    assert servers["gflow"]["args"] == ["mcp", "run"]


# --- curation: the property that matters -------------------------------------------


def _generator() -> Any:
    """Load `scripts/ci/generate_plugin_skills.py` (not importable as a package)."""
    from importlib import util

    spec = util.spec_from_file_location(
        "_gen", _REPO / "scripts" / "ci" / "generate_plugin_skills.py"
    )
    assert spec and spec.loader
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shipped_set_matches_the_generator() -> None:
    assert set(_generator().SHIPPED) == _SHIPPED


def test_no_maintainer_only_skill_reaches_the_plugin() -> None:
    """Every directory under the plugin's skills tree is one a user should see."""
    shipped = {p.name for p in (_PLUGIN_DIR / "skills").iterdir() if p.is_dir()}
    assert shipped == _SHIPPED, f"unexpected skills in the plugin payload: {shipped - _SHIPPED}"


def test_every_shipped_skill_is_byte_identical_to_its_source() -> None:
    """The plugin copy is generated. A hand-edit here would fork the protocol silently.

    Uses the generator's own `render()` rather than restating the rewrite rule. A second copy
    of the regex here would drift from the generator and the test would still pass.
    """
    generator = _generator()
    render = generator.render
    for name in _SHIPPED:
        # Tracked files only, exactly as the generator ships them. Walking the disk made an
        # untracked file in skills/ fail this test: a local scratch note, or the fixture that
        # `test_the_generator_ships_only_what_git_tracks` creates while another pytest-xdist
        # worker runs this one (measured).
        for source in generator._tracked_files(_REPO / "skills" / name):
            dest = _PLUGIN_DIR / "skills" / name / source.relative_to(_REPO / "skills" / name)
            assert dest.exists(), f"{dest} is missing — run generate_plugin_skills.py"
            text = source.read_text(encoding="utf-8")
            expected = render(text) if source.suffix == ".md" else text
            assert dest.read_text(encoding="utf-8") == expected, f"{dest} has drifted"


def test_no_link_in_the_plugin_climbs_out_of_the_repo() -> None:
    """`../../KNOWN_ISSUES.md` resolves in the repo and points at nothing once installed."""
    for md in (_PLUGIN_DIR / "skills").rglob("*.md"):
        assert "](../../" not in md.read_text(encoding="utf-8"), (
            f"{md} still has a repo-escaping relative link"
        )


# --- the other two channels --------------------------------------------------------


def test_codex_ships_the_curated_payload_not_every_skill() -> None:
    """`skills: ./skills/` shipped all 18 to every Codex user, `release` included."""
    declared = _load(_CODEX)["skills"]
    assert declared == "./plugins/gflow/skills/", declared


def test_chatgpt_desktop_marketplace_ships_the_curated_payload() -> None:
    """`source.path: ./` published the whole repo tree as the plugin."""
    path = _load(_AGENTS)["plugins"][0]["source"]["path"]
    assert path == "./plugins/gflow", path


def test_the_generator_ships_only_what_git_tracks() -> None:
    """An untracked file sitting in a shipped skill must never reach the payload.

    The generator used to walk the directory, so anything left there locally was fair game.
    On the maintainer's machine that was `skills/video-production/fixtures/*.jpg` — untracked
    QA images. Being binary they merely crashed it on `read_text`; an untracked *text* file in
    the same position would have been copied into the plugin and shipped to every user on the
    next regenerate, with no gate objecting, because the generator itself put it there.
    """
    generator = _generator()
    fixtures = _REPO / "skills" / "video-production" / "_pytest_untracked"
    fixtures.mkdir(parents=True, exist_ok=True)
    binary = fixtures / "scratch.jpg"
    textual = fixtures / "notes.md"
    try:
        binary.write_bytes(b"\xff\xd8\xff\xfe not utf-8")
        textual.write_text("a local scratch note\n", encoding="utf-8")

        payload = generator._payload()

        assert not any(p.name == "scratch.jpg" for p in payload), (
            "an untracked binary reached the payload"
        )
        assert not any(p.name == "notes.md" for p in payload), (
            "an untracked text file reached the payload — it would ship to users"
        )
        # And the real payload is unaffected.
        assert {p.name for p in payload} >= {"SKILL.md", "composition.md", "tasks.json"}
    finally:
        binary.unlink(missing_ok=True)
        textual.unlink(missing_ok=True)
        fixtures.rmdir()
