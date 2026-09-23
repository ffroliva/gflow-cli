"""The release skill's version-site table must match reality (#839).

Three lists of "the version sites" disagreed, and one of them lived inside the gate that
catches the disagreement: `check_repo_hygiene.py`'s docstring said *three*, its own
`_check_version_agreement` checked *five files / six occurrences*, and
`skills/release/SKILL.md` named a fourth set. The code was right and both prose copies
were wrong — differently. The release engineer met each omission as a gate failure
mid-release rather than as a checklist item.

Fixing the prose does not stop it recurring; prose drifts precisely because nothing reds
when it does. So the table in `skills/release/SKILL.md` step 6 is now the canonical list
and this pins it: every file it names must exist and must declare the version
`pyproject.toml` declares. Add a version site without adding it to that table — or let
the table name a file that stops carrying a version — and this goes red.

Deliberately NOT a second copy of the list: the table is parsed, never restated here.
A constant here would be the fourth disagreeing list.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SKILL = _ROOT / "skills" / "release" / "SKILL.md"

# Sites whose version is not a bare `"version"` JSON key or a `version = "x"` line.
_CUSTOM_READERS: dict[str, re.Pattern[str]] = {
    "docker/Dockerfile": re.compile(r"^ARG\s+GFLOW_VERSION=(?P<v>.+?)\s*$", re.MULTILINE),
    "src/gflow_cli/__init__.py": re.compile(r'^__version__\s*=\s*"(?P<v>[^"]+)"', re.MULTILINE),
    "uv.lock": re.compile(r'^name = "gflow-cli"\r?\nversion = "(?P<v>[^"]+)"', re.MULTILINE),
    "pyproject.toml": re.compile(r'^version\s*=\s*"(?P<v>[^"]+)"', re.MULTILINE),
}


def _expected_version() -> str:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _table_sites() -> list[str]:
    """Every repo path named in the step-6 version-site table.

    Parses rather than restates: a hardcoded list here would be exactly the fourth
    disagreeing copy this test exists to prevent.
    """
    text = _SKILL.read_text(encoding="utf-8")
    start = text.index("**6. Bump the shared release version")
    end = text.index("`server.json` is the one that bites quietly", start)
    sites: list[str] = []
    for row in text[start:end].splitlines():
        if not row.startswith("|"):
            continue
        cells = [c.strip() for c in row.strip("|").split("|")]
        if len(cells) < 2 or not cells[0].isdigit():
            continue
        # First backticked token in the Site cell that looks like a repo path.
        for token in re.findall(r"`([^`]+)`", cells[1]):
            if "/" in token or token.endswith((".toml", ".json", ".lock")):
                sites.append(token)
                break
    return sites


def _declared_versions(rel: str) -> list[str]:
    """Every version this file declares (server.json declares more than one)."""
    path = _ROOT / rel
    text = path.read_text(encoding="utf-8")
    if rel in _CUSTOM_READERS:
        match = _CUSTOM_READERS[rel].search(text)
        return [match.group("v")] if match else []
    if rel.endswith(".json"):
        data = json.loads(text)
        found = [str(data["version"])] if "version" in data else []
        found += [
            str(pkg["version"])
            for pkg in data.get("packages", [])
            if isinstance(pkg, dict) and "version" in pkg
        ]
        return found
    return []


def test_the_table_names_at_least_the_known_sites() -> None:
    """A shrunk table is the failure mode — #839 was three lists, each too short."""
    sites = _table_sites()
    assert len(sites) >= 7, (
        f"skills/release/SKILL.md step 6 lists only {len(sites)} version sites: {sites}. "
        "Seven are known. A shorter list is how #839 happened — two lists that disagree "
        "means the shorter one is silently wrong."
    )


@pytest.mark.parametrize("rel", _table_sites())
def test_every_listed_site_exists_and_declares_the_release_version(rel: str) -> None:
    path = _ROOT / rel
    assert path.is_file(), (
        f"skills/release/SKILL.md step 6 names {rel!r}, which does not exist. "
        "Either the file moved and the table is stale, or the path is a typo — "
        "a release engineer following that table would go looking for it mid-release."
    )
    declared = _declared_versions(rel)
    assert declared, f"{rel}: the table names it as a version site but no version was found in it"
    expected = _expected_version()
    assert all(v == expected for v in declared), (
        f"{rel} declares {declared}, but pyproject.toml declares {expected!r}. "
        "Bump them together (#839)."
    )
