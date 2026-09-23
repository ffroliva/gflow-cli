#!/usr/bin/env python
# SPDX-License-Identifier: MIT
"""Generate the plugin's curated skill payload from the canonical ``skills/`` tree.

``skills/<name>/SKILL.md`` stays the single source of truth for every agent. A Claude Code
plugin, though, cannot follow a pointer: the marketplace installs a *directory*, so the
skills it ships have to physically live under the plugin root. That is a copy, and a copy
drifts — which is exactly what AGENTS.md forbids when it says never to put protocol content
in a vendor directory.

So the copy is generated and never hand-edited, and ``--check`` fails CI the moment it
drifts. That is the same contract ``generate_website_docs.py`` already provides for
``website/docs/``; this is the second instance of the pattern, not a new idea.

Two things it guards that a plain copy would not:

1. **Curation.** ``skills/`` also holds maintainer-only skills — ``release``, ``check``,
   ``pr-council-review``, ``sonar``, ``doc-review`` and friends. Those must never reach a
   user's agent. Only :data:`SHIPPED` goes out, and ``--check`` fails on any extra directory
   that appears under the plugin, so a stray copy cannot ride along unnoticed.
2. **Links that escape the repo.** ``skills/video-production/SKILL.md`` links to
   ``../../KNOWN_ISSUES.md`` and friends. Inside the repo those resolve; inside an installed
   plugin they point at nothing. They are rewritten to absolute GitHub URLs. Links *within*
   a skill (``composition.md``) and links *between* the two shipped skills
   (``../gflow-cli/SKILL.md``) still resolve under the plugin's own layout, so they are left
   exactly as they are.

Usage::

    python scripts/ci/generate_plugin_skills.py            # regenerate
    python scripts/ci/generate_plugin_skills.py --check    # CI: fail on any drift
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SKILLS = _REPO / "skills"
_PLUGIN_SKILLS = _REPO / "plugins" / "gflow" / "skills"

#: The only skills a user-facing plugin ships. Everything else in ``skills/`` drives this
#: repo's own development lifecycle and is useless — or actively confusing — to a user who
#: just wants to drive Google Flow. Adding a name here ships it to every installed agent, so
#: add one only after deciding it is a *user* skill.
SHIPPED = ("gflow-cli", "video-production")

_BLOB = "https://github.com/ffroliva/gflow-cli/blob/main/"

#: ``](../../path)`` — a link climbing out of ``skills/<name>/`` to the repo root. Anything
#: shallower stays put: it still resolves inside the installed plugin.
_ESCAPES_REPO = re.compile(r"\]\(\.\./\.\./([^)]+)\)")


def render(text: str) -> str:
    """Rewrite repo-escaping relative links to absolute URLs; leave every other link alone."""
    return _ESCAPES_REPO.sub(lambda m: f"]({_BLOB}{m.group(1)})", text)


def _tracked_files(source_dir: Path) -> list[Path]:
    """The files git tracks under *source_dir* — never whatever happens to be on disk.

    Walking the directory would pick up anything sitting there locally. On the maintainer's
    machine that is `skills/video-production/fixtures/*.jpg`, untracked QA images: they are
    binary, so the generator died on `read_text`. A *text* scratch file in the same position
    would have been worse — copied into the plugin payload without a murmur and shipped to
    every user on the next regenerate.

    Asking git is the precise statement of intent: the plugin ships what the repository
    ships. It also matches how `check_repo_hygiene.py` reasons about the tree.
    """
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
        ["git", "ls-files", "-z", "--", str(source_dir)],  # noqa: S607
        cwd=_REPO,
        capture_output=True,
        check=True,
        text=True,
    )
    return sorted(_REPO / p for p in out.stdout.split("\0") if p)


def _payload() -> dict[Path, str]:
    """Map each destination path to the exact bytes it should contain."""
    out: dict[Path, str] = {}
    for name in SHIPPED:
        source_dir = _SKILLS / name
        if not source_dir.is_dir():
            msg = f"skills/{name}/ does not exist — SHIPPED names a skill that is not there"
            raise SystemExit(msg)
        tracked = _tracked_files(source_dir)
        if not tracked:
            msg = f"skills/{name}/ has no tracked files — nothing to ship"
            raise SystemExit(msg)
        for source in tracked:
            text = source.read_text(encoding="utf-8")
            dest = _PLUGIN_SKILLS / name / source.relative_to(source_dir)
            out[dest] = render(text) if source.suffix == ".md" else text
    return out


def _strays(expected: set[Path]) -> list[str]:
    """Paths under the plugin's skills tree that the generator did not put there.

    This is the curation guard. A maintainer-only skill copied in by hand, or left behind
    after a name is removed from SHIPPED, would otherwise ship silently to every user.

    **Directories count, not just files.** An earlier version reported only files, so
    deleting a stray skill's files left its empty directory behind and ``--check`` went
    green — while an actual `claude plugin install` showed `skills/release/` sitting in the
    installed tree next to the two real ones. An empty directory ships no instructions, but
    a maintainer-only skill *name* in a user-facing plugin is exactly the signal this guard
    exists to prevent, and the next hand-edit would have filled it back in unnoticed.
    """
    if not _PLUGIN_SKILLS.is_dir():
        return []
    expected_dirs = {
        parent for p in expected for parent in p.parents if _PLUGIN_SKILLS in p.parents
    }
    return sorted(
        p.relative_to(_REPO).as_posix()
        for p in _PLUGIN_SKILLS.rglob("*")
        if (p.is_file() and p not in expected) or (p.is_dir() and p not in expected_dirs)
    )


def main(argv: list[str]) -> int:
    check = "--check" in argv
    payload = _payload()
    drift = [
        dest.relative_to(_REPO).as_posix()
        for dest, text in payload.items()
        if not dest.exists() or dest.read_text(encoding="utf-8") != text
    ]
    strays = _strays(set(payload))

    if check:
        if drift or strays:
            if drift:
                print("plugins/gflow/skills is stale — regenerate with:")
                print("  python scripts/ci/generate_plugin_skills.py")
                for d in drift:
                    print(f"  DRIFT: {d}")
            for s in strays:
                print(f"  STRAY: {s} is shipped to users but is not generated from skills/")
            return 1
        print(f"plugin skills in sync ({len(payload)} files, {len(SHIPPED)} skills).")
        return 0

    # Deepest first, so a directory is empty by the time its turn comes.
    for stray in sorted(strays, key=lambda s: s.count("/"), reverse=True):
        path = _REPO / stray
        path.rmdir() if path.is_dir() else path.unlink()
    for dest, text in payload.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8", newline="\n")
    print(f"Regenerated {len(payload)} plugin skill file(s); removed {len(strays)} stray(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
