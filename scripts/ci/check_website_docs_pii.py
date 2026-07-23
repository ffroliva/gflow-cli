"""Guard: no private identifiers leak into the published ``website/docs/`` mirror.

``website/docs/`` is the ANONYMIZED, mkdocs-published mirror of the canonical
``docs/`` tree (``denon82`` → ``my-profile``, ``ffrol`` → ``your-user``, real
email → placeholder, ...). A blind copy or find/replace can republish real PII —
this is exactly the failure mode of PR #362. This check fails CI if any known
private token appears in a published file under ``website/docs/``, so an
anonymization miss can never ship silently.

Forbidden (private) tokens vs. public references that MUST pass:

* ``denon82``          — private Google profile name.
* ``profile_ffroliva`` — derived private profile directory.
* ``ffrol``            — private OS username; matched with a word boundary so it
  does NOT hit the public repo owner ``ffroliva``.
* ``flavio`` / ``flavio.oliva`` — real name (case-insensitive).
* ``ffroliva@``        — real email local-part.

The public references that legitimately contain ``ffroliva`` — the repo URL
``github.com/ffroliva/gflow-cli``, the bare slug ``ffroliva/gflow-cli``, and the
platformdirs APP_AUTHOR path segment ``ffroliva\\gflow-cli`` — are never matched
by the patterns above (none is followed by ``@`` or preceded by ``profile_``),
so no explicit allow-list is needed and the guard is trap-free.

Exit code 0 = clean; 1 = at least one leak. Broken files are printed to stdout
with the file, line number, and the offending token.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# (compiled pattern, human-readable reason). Each targets a SPECIFIC private
# token — never the bare substring ``ffroliva``, which appears in public repo
# URLs and the APP_AUTHOR path and must pass. This is the sibling guard to the
# anonymization map in ``scripts/ci/generate_website_docs.py`` (which PRODUCES
# the mirror this check VERIFIES): every FORBIDDEN token here must be rewritten
# by a substitution there. Keep the two in sync.
FORBIDDEN: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"denon82"), "private Google profile name"),
    (re.compile(r"profile_ffroliva"), "private profile directory"),
    (re.compile(r"\bffrol\b"), "private OS username"),
    (re.compile(r"flavio", re.IGNORECASE), "real name"),
    (re.compile(r"ffroliva@"), "real email address"),
)

# Published, scannable file types under website/docs/.
SCANNED_SUFFIXES = (".md", ".html")


def find_pii(text: str) -> list[tuple[int, str, str]]:
    """Return ``(lineno, matched_token, reason)`` for every forbidden token."""
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for pattern, reason in FORBIDDEN:
            for match in pattern.finditer(line):
                hits.append((lineno, match.group(0), reason))
    return hits


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    web_docs = repo_root / "website" / "docs"
    if not web_docs.is_dir():
        print(f"website/docs not found at {web_docs} — nothing to scan.")
        return 0

    leaks = 0
    scanned = 0
    for path in sorted(web_docs.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SCANNED_SUFFIXES:
            continue
        scanned += 1
        rel = path.relative_to(repo_root).as_posix()
        for lineno, token, reason in find_pii(path.read_text(encoding="utf-8")):
            print(f"{rel}:{lineno}  →  {token!r}  ({reason})")
            leaks += 1

    if leaks:
        print(
            f"\n{leaks} private-identifier leak(s) in website/docs/ — anonymize before publishing."
        )
        return 1
    print(f"No private identifiers found across {scanned} published website/docs files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
