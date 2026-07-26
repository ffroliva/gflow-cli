"""Contract tests for the repo-local Codex skills plugin."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"missing required Codex plugin file: {path.relative_to(ROOT)}"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_plugin_manifest_packages_canonical_skills_under_gflow_namespace() -> None:
    manifest = _load_json(ROOT / ".codex-plugin" / "plugin.json")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert manifest["name"] == "gflow"
    assert manifest["version"] == project["version"]
    assert manifest["skills"] == "./skills/"
    assert manifest["interface"]["displayName"] == "gflow-cli"


def test_repo_marketplace_exposes_root_plugin() -> None:
    marketplace = _load_json(ROOT / ".agents" / "plugins" / "marketplace.json")

    assert marketplace["name"] == "gflow-cli"
    assert marketplace["interface"]["displayName"] == "gflow-cli"
    assert marketplace["plugins"] == [
        {
            "name": "gflow",
            "source": {"source": "local", "path": "./"},
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Developer Tools",
        }
    ]


def test_every_packaged_skill_has_matching_frontmatter_name() -> None:
    skill_dirs = sorted(path for path in (ROOT / "skills").iterdir() if path.is_dir())

    assert skill_dirs
    for skill_dir in skill_dirs:
        skill_path = skill_dir / "SKILL.md"
        assert skill_path.is_file(), f"{skill_dir.relative_to(ROOT)} is missing SKILL.md"
        contents = skill_path.read_text(encoding="utf-8")
        assert contents.startswith("---\n")
        frontmatter_text, separator, _ = contents[4:].partition("\n---")
        assert separator
        frontmatter = yaml.safe_load(frontmatter_text)
        assert isinstance(frontmatter, dict)
        assert frontmatter["name"] == skill_dir.name
        assert isinstance(frontmatter.get("description"), str)
        assert frontmatter["description"].strip()


def test_release_workflows_bump_the_plugin_version() -> None:
    for relative_path in ("RELEASE.md", "skills/release/SKILL.md"):
        contents = (ROOT / relative_path).read_text(encoding="utf-8")
        assert ".codex-plugin/plugin.json" in contents, (
            f"{relative_path} must keep the Codex plugin version aligned with pyproject.toml"
        )
