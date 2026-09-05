"""Validate that every memory slug a skill cites resolves to a real file.

The council skills route by slug: a dimension fires, and it opens exactly the
files its row in the Dimension → Slugs table names. That is only deterministic
if every citation resolves, so this gate enforces the round trip:

  1. Every citation in ``skills/**/SKILL.md`` has a matching
     ``docs/superpowers/memory/<slug>.md``. A citation with no file silently
     degrades routing into a search, which is the failure this directory exists
     to prevent.
  2. Every file in ``docs/superpowers/memory/`` is cited by at least one skill.
     An uncited file is unroutable — no dimension will ever open it — so it is
     dead weight that ages badly with nobody watching.

It also refuses private identifiers, so a hand-added file cannot reintroduce a
session id, an address, a device name, a local checkout path or a token into a
public tree.

**Links between memory files are NOT required to resolve.** A memory file may
cite a slug that lives only in the private store: the memory convention is to
link liberally, where an unresolved link marks something worth writing later.
Those are reported as INFO and never fail — only skill citations are binding.

Fenced blocks and HTML comments are stripped before scanning, but **inline code
spans are not**: the Dimension → Slugs table writes its citations inside
backticks, and stripping inline code erased 46 of 50 real citations while this
gate was being written. That leaves one collision — TOML array-of-tables syntax
uses the same brackets, and `skills/gflow-cli/SKILL.md` documents
``[[scene.instructions.card]]`` inline. A heuristic to separate them (has dots?
resolves?) would misfire on the real slug ``data-layer-v0.9.0-bugs``, so the
exceptions are named explicitly instead. One named exception beats a clever rule.

Exit 0 = all good; 1 = at least one problem, printed with file + line.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
MEMORY = ROOT / "docs" / "superpowers" / "memory"
MEMORY_REL = "docs/superpowers/memory"

FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Tolerates surrounding whitespace and casing so a human-typed `[[ Slug ]]` is
# still checked rather than silently ignored; the slug itself is normalised.
CITATION_RE = re.compile(r"\[\[\s*([A-Za-z0-9][A-Za-z0-9\-._]*?)\s*\]\]")
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)

# `[[...]]` tokens that are not memory citations: TOML array-of-tables keys.
NOT_A_SLUG: frozenset[str] = frozenset(
    {"scene.instructions.card", "scene.instructions", "config.domains"}
)

# Identifiers that must never reach this public tree.
#
# The first four classes are shared with ``check_website_docs_pii.py``; a test
# asserts the two lists have not drifted apart. They are duplicated rather than
# imported because CI runs these gates as scripts, not modules, so a
# cross-import would need a ``sys.path`` hack to save three lines.
#
# `denon82` is deliberately absent. It is a maintainer profile name that already
# appears across the canonical `docs/` tree; the website guard scrubs it from the
# *published mirror*, which is a different bar from this one.
FORBIDDEN: tuple[tuple[str, str], ...] = (
    ("session id", r"originSessionId"),
    ("maintainer email", r"ffroliva@|dev@axelate\.io"),
    ("real name", r"(?i)flavio"),
    ("OS username", r"\bffrol\b"),
    ("Flow project UUID", r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
    ("device name", r"(?i)\b(?:hp|dell|lenovo|macbook)-[\w-]*(?:elitebook|thinkpad|pro)?[\w-]*\b"),
    ("local checkout path", r"(?i)[a-z]:[\\/](?:development|repos)[\\/]"),
    ("OAuth or API token", r"ya29\.[\w-]{5,}|AIza[\w-]{10,}|gh[pousr]_\w{20,}"),
    ("session cookie value", r"__Secure-[\w.-]+=\S+"),
    ("Claude session URL", r"claude\.ai/code/session"),
    ("IP address", r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
)


def strip_code(text: str) -> str:
    """Blank out fenced blocks and HTML comments, preserving line numbering."""
    for pattern in (FENCE_RE, HTML_COMMENT_RE):
        text = pattern.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return text


def citations_in(text: str) -> set[str]:
    """Return the memory slugs cited by one document."""
    return {
        slug.lower()
        for slug in CITATION_RE.findall(strip_code(text))
        if slug.lower() not in NOT_A_SLUG
    }


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def skill_citations() -> dict[str, list[tuple[Path, int]]]:
    """Map each slug cited by a skill to the (file, line) sites citing it."""
    found: dict[str, list[tuple[Path, int]]] = {}
    for skill in sorted(SKILLS.rglob("SKILL.md")):
        for lineno, line in enumerate(strip_code(_read(skill)).splitlines(), 1):
            for slug in citations_in(line):
                found.setdefault(slug, []).append((skill, lineno))
    return found


def memory_files() -> list[Path]:
    """Every memory file, recursively — a subdirectory must not dodge the scan."""
    return sorted(p for p in MEMORY.rglob("*.md") if p.is_file() and p.name != "README.md")


def frontmatter_name_ok(text: str, stem: str) -> bool:
    """True when the frontmatter declares `name:` exactly matching the filename."""
    block = FRONTMATTER_RE.match(text)
    if block is None:
        return False
    return re.search(rf"^name:\s*{re.escape(stem)}\s*$", block.group(1), re.MULTILINE) is not None


def main() -> int:
    print("── council memory check ─────────────────────────────────────")
    if not MEMORY.is_dir():
        print(
            f"  MISSING   {MEMORY_REL}/ does not exist.\n"
            "            The council skills cite slugs that resolve there; restore the\n"
            "            directory or drop the citations from the Dimension → Slugs table."
        )
        return 1

    files = memory_files()
    on_disk = {p.stem for p in files}
    cited = skill_citations()
    problems: list[str] = []
    notes: list[str] = []

    for slug in sorted(set(cited) - on_disk):
        for skill, lineno in cited[slug]:
            problems.append(
                f"  DANGLING  {skill.relative_to(ROOT).as_posix()}:{lineno}  cites [[{slug}]] "
                f"but {MEMORY_REL}/{slug}.md does not exist"
            )

    for slug in sorted(on_disk - set(cited)):
        problems.append(
            f"  ORPHAN    {MEMORY_REL}/{slug}.md  is cited by no skill; "
            "cite it from the dimension that needs it, or delete it"
        )

    unresolved_links: set[str] = set()
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        text = _read(path)
        for label, pattern in FORBIDDEN:
            if re.search(pattern, text):
                problems.append(
                    f"  PRIVATE   {rel}  contains a {label}; redact it before publishing"
                )
        if not frontmatter_name_ok(text, path.stem):
            problems.append(
                f"  NAME      {rel}  needs frontmatter with `name: {path.stem}` "
                "matching the filename"
            )
        unresolved_links |= citations_in(text) - on_disk

    for slug in sorted(unresolved_links):
        notes.append(
            f"  INFO      [[{slug}]] is linked from a memory file but lives only in the "
            "private store; port it if a dimension needs it"
        )

    if notes:
        print("\n".join(notes))
    if problems:
        print("\n".join(problems))
        print(f"\n❌  {len(problems)} problem(s) across {len(files)} memory file(s).")
        return 1

    print(f"✅  {len(files)} memory files, all cited and all resolving.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
