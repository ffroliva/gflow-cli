#!/usr/bin/env python3
"""Open or update one GitHub issue per browser-driver release the pins refuse.

Dependabot ignores every playwright and patchright update on purpose: a driver bump has
broken generation before (#1319, #1324), so it needs the engine-contract job and a live
run, never an automatic merge. This job only announces that a bump exists.

For each driver it reads the pin from ``pyproject.toml``, asks PyPI for the latest
release, and when the pin does not admit it, opens an issue labelled ``driver-watch``,
or updates the open one for that package. ``--dry-run`` prints the plan and changes
nothing.
"""

from __future__ import annotations

import argparse
import json
import operator
import re
import subprocess
import sys
import tomllib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

DRIVERS = ("playwright", "patchright")
LABEL = "driver-watch"
TITLE_PREFIX = "Driver release available: "
_PYPI = "https://pypi.org/pypi/{}/json"
_CLAUSE = re.compile(r"^\s*(==|!=|>=|<=|>|<)\s*([0-9][0-9.]*)\s*$")
_OPS: dict[str, Callable[[tuple[int, ...], tuple[int, ...]], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}


@dataclass(frozen=True)
class Bump:
    package: str
    pin: str
    latest: str

    @property
    def title(self) -> str:
        return f"{TITLE_PREFIX}{self.package} {self.latest} (pinned {self.pin})"


def _version(text: str) -> tuple[int, ...]:
    """The release number only: ``1.64.4.post1`` is ``1.64.4``, ``1.65.0rc1`` is ``1.65.0``."""
    m = re.match(r"\d+(?:\.\d+)*", text)
    if m is None:
        raise ValueError(f"not a version: {text!r}")
    parts = tuple(int(p) for p in m[0].split("."))
    return parts + (0,) * (4 - len(parts))  # 1.64 == 1.64.0


def admits(spec: str, version: str) -> bool:
    """True when every comma-separated clause of ``spec`` admits ``version``.

    Only the plain numeric clauses gflow's pins use are understood; anything else raises."""
    v = _version(version)
    for clause in spec.split(","):
        m = _CLAUSE.match(clause)
        if m is None:
            raise ValueError(f"unsupported specifier clause {clause!r}")
        if not _OPS[m[1]](v, _version(m[2])):
            return False
    return True


def pins(pyproject: str) -> dict[str, str]:
    """The specifier each driver is pinned to, from dependencies and extras."""
    project = tomllib.loads(pyproject)["project"]
    reqs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        reqs.extend(extra)
    found: dict[str, str] = {}
    for req in reqs:
        m = re.match(r"^\s*([A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*(.*?)\s*(;.*)?$", req)
        if not (m and m[1].lower() in DRIVERS and m[2]):
            continue
        name, spec = m[1].lower(), m[2].replace(" ", "")
        if found.setdefault(name, spec) != spec:
            raise ValueError(f"{name} is pinned twice: {found[name]!r} and {spec!r}")
    return found


def bumps(pinned: dict[str, str], latest: dict[str, str]) -> list[Bump]:
    return [
        Bump(pkg, spec, latest[pkg])
        for pkg, spec in sorted(pinned.items())
        if pkg in latest and not admits(spec, latest[pkg])
    ]


Action = tuple[str, Bump | None, int]


def plan(found: list[Bump], issues: list[dict[str, object]]) -> list[Action]:
    """One issue per package: ``create``, ``edit`` the open one, or ``close`` it once the pin
    admits the latest release. A release whose issue a maintainer closed as not planned is
    not reopened; the job's own close (completed) is not a decline."""

    def is_open(issue: dict[str, object]) -> bool:
        return str(issue["state"]).upper() == "OPEN"

    def declined(issue: dict[str, object]) -> bool:
        return str(issue.get("stateReason", "")).upper() == "NOT_PLANNED"

    actions: list[Action] = []
    wanted = {bump.package for bump in found}
    for bump in found:
        prefix = f"{TITLE_PREFIX}{bump.package} "
        mine = [i for i in issues if str(i["title"]).startswith(prefix)]
        current = next((i for i in mine if is_open(i)), None)
        if any(i["title"] == bump.title and declined(i) for i in mine):
            continue  # a maintainer declined this exact release
        if current is None:
            actions.append(("create", bump, 0))
        elif current["title"] != bump.title:
            actions.append(("edit", bump, int(str(current["number"]))))
    for issue in issues:
        title = str(issue["title"])
        if not title.startswith(TITLE_PREFIX):
            continue  # a hand-filed issue with the label is not the job's to close
        package = title.removeprefix(TITLE_PREFIX).split(" ")[0]
        if is_open(issue) and package in DRIVERS and package not in wanted:
            actions.append(("close", None, int(str(issue["number"]))))
    return actions


def body(bump: Bump) -> str:
    return (
        f"PyPI has **{bump.package} {bump.latest}**; `pyproject.toml` pins "
        f"`{bump.package}{bump.pin}`, which does not admit it.\n\n"
        "Dependabot ignores driver updates by design: a driver bump has broken generation "
        "before (#1319, #1324). To take this release:\n\n"
        "1. Raise the pin in `pyproject.toml` and run `uv lock` (for playwright, also "
        "`SUPPORTED_PLAYWRIGHT_RANGE` in `src/gflow_cli/api/transports/_video_common.py`).\n"
        "2. Let the `engine-contract` CI job run against the new driver.\n"
        "3. Live-verify one generation on the engine that uses it.\n\n"
        "This issue is opened and updated by the scheduled `driver-watch` job "
        "(`.github/workflows/deps-watch.yml`). It is never merged automatically.\n"
    )


def _latest(package: str) -> str:
    req = urllib.request.Request(  # noqa: S310 - fixed https URL
        _PYPI.format(package), headers={"User-Agent": "driver-watch"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — fixed https URL
        return str(json.load(resp)["info"]["version"])


def _gh(*args: str, stdin: str | None = None) -> str:
    done = subprocess.run(  # noqa: S603 - argv list, no shell
        ["gh", *args],  # noqa: S607 - gh from the runner's PATH
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode:
        sys.stderr.write(done.stderr)
        done.check_returncode()
    return done.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    args = parser.parse_args(argv)

    pinned = pins(Path("pyproject.toml").read_text(encoding="utf-8"))
    found = bumps(pinned, {pkg: _latest(pkg) for pkg in pinned})
    issues = json.loads(
        _gh(
            "issue",
            "list",
            "--label",
            LABEL,
            "--state",
            "all",
            "--limit",
            "500",
            "--json",
            "number,title,state,stateReason",
        )
    )
    actions = plan(found, issues)
    print(f"pins: {pinned}; releases outside them: {[b.title for b in found] or 'none'}")
    for action, bump, number in actions:
        print(action, f"#{number}" if number else "", bump.title if bump else "")
        if args.dry_run:
            continue
        if action == "close":
            _gh(
                "issue",
                "close",
                str(number),
                "--reason",
                "completed",
                "--comment",
                "The pin now admits the latest release.",
            )
        elif bump is not None and action == "create":
            _gh(
                "label",
                "create",
                LABEL,
                "--force",
                "--color",
                "C5DEF5",
                "--description",
                "A browser-driver release the pins do not admit",
            )
            _gh(
                "issue",
                "create",
                "--title",
                bump.title,
                "--label",
                LABEL,
                "--body-file",
                "-",
                stdin=body(bump),
            )
        elif bump is not None:
            _gh(
                "issue",
                "edit",
                str(number),
                "--title",
                bump.title,
                "--body-file",
                "-",
                stdin=body(bump),
            )
            # A title or body edit notifies nobody; a comment reaches the watchers.
            _gh("issue", "comment", str(number), "--body", f"Newer release: {bump.title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
