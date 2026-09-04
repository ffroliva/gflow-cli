"""Validate that every memory slug a skill cites resolves to a real file.

The council skills route by slug: a dimension fires, and it opens exactly the
`[[slug]]` files its row names. That is only deterministic if every citation
resolves, so this gate enforces the round trip in both directions:

  1. Every ``[[slug]]`` cited in ``skills/**/SKILL.md`` has a matching
     ``docs/superpowers/memory/<slug>.md``. A citation with no file silently
     degrades routing into a search, which is the failure this whole directory
     exists to prevent.
  2. Every file in ``docs/superpowers/memory/`` is cited by at least one skill.
     An uncited file is unroutable — no dimension will ever open it — so it is
     noise that costs review attention and drifts out of date unnoticed.

It also refuses the private identifiers the port strips, so a future hand-copied
file cannot reintroduce a session id, the maintainer's address, or an OS
username into a public tree.

Fenced blocks are stripped before scanning, but **inline code spans are not** —
the Dimension → Slugs table writes its citations as `` `[[slug]]` ``, so
stripping inline code would erase almost every real citation. That leaves one
collision: TOML array-of-tables syntax uses the same brackets, and
`skills/gflow-cli/SKILL.md` documents `[[scene.instructions.card]]` inline. A
heuristic to tell the two apart (dots? resolves?) would misfire on real slugs
like `data-layer-v0.9.0-bugs`, so the exceptions are listed explicitly below
instead. One named exception beats a clever rule.

Exit 0 = all good; 1 = at least one problem, printed with file + line.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
MEMORY = ROOT / "docs" / "superpowers" / "memory"

FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
CITATION_RE = re.compile(r"\[\[([a-z0-9][a-z0-9\-.]*)\]\]")

# `[[...]]` tokens that are not memory citations. Currently only TOML
# array-of-tables keys, which `skills/gflow-cli/SKILL.md` documents inline.
NOT_A_SLUG: frozenset[str] = frozenset({"scene.instructions.card"})

# Identifiers that must never reach this public tree. Mirrors the port script.
FORBIDDEN: tuple[tuple[str, str], ...] = (
    ("session id", r"originSessionId"),
    ("maintainer email", r"dev@axelate\.io|ffroliva@gmail\.com"),
    ("OS username", r"[Uu]sers[\\/]+ffrol\b"),
    (
        "Flow project UUID",
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    ),
)


def strip_code(text: str) -> str:
    """Blank out fenced blocks, preserving line numbering."""
    return FENCE_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def citations() -> dict[str, list[tuple[Path, int]]]:
    """Map each cited slug to the (file, line) sites citing it."""
    found: dict[str, list[tuple[Path, int]]] = {}
    for skill in sorted(SKILLS.rglob("SKILL.md")):
        prose = strip_code(skill.read_text(encoding="utf-8"))
        for lineno, line in enumerate(prose.splitlines(), 1):
            for slug in CITATION_RE.findall(line):
                if slug in NOT_A_SLUG:
                    continue
                found.setdefault(slug, []).append((skill, lineno))
    return found


def main() -> int:
    if not MEMORY.is_dir():
        print(f"FAIL  council memory directory is missing: {MEMORY.relative_to(ROOT)}")
        return 1

    # README.md is the directory signpost, not a memory file: it is never
    # cited by a dimension and must not read as an orphan.
    on_disk = {p.stem for p in MEMORY.glob("*.md") if p.name != "README.md"}
    cited = citations()
    problems: list[str] = []

    for slug in sorted(set(cited) - on_disk):
        for skill, lineno in cited[slug]:
            problems.append(
                f"  DANGLING  {skill.relative_to(ROOT)}:{lineno}  cites [[{slug}]] "
                f"but docs/superpowers/memory/{slug}.md does not exist"
            )

    for slug in sorted(on_disk - set(cited)):
        problems.append(
            f"  ORPHAN    docs/superpowers/memory/{slug}.md  is cited by no skill; "
            "cite it from the dimension that needs it, or delete it"
        )

    for path in sorted(p for p in MEMORY.glob("*.md") if p.name != "README.md"):
        text = path.read_text(encoding="utf-8")
        for label, pattern in FORBIDDEN:
            if re.search(pattern, text):
                problems.append(
                    f"  PRIVATE   {path.relative_to(ROOT)}  contains a {label}; "
                    "redact it before publishing"
                )
        if not text.startswith("---"):
            problems.append(f"  NOFRONT   {path.relative_to(ROOT)}  has no frontmatter")
        elif f"name: {path.stem}" not in text:
            problems.append(
                f"  NAME      {path.relative_to(ROOT)}  frontmatter `name:` does not "
                f"match the filename ({path.stem})"
            )

    print("── council memory check ─────────────────────────────────────")
    if problems:
        print("\n".join(problems))
        print(f"\n❌  {len(problems)} problem(s) across {len(on_disk)} memory file(s).")
        return 1

    print(f"✅  {len(on_disk)} memory files, all cited and all resolving.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
