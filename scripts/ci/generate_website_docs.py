"""Deterministically generate the anonymized ``website/docs/`` mkdocs mirror
from canonical ``docs/`` (+ root ``KNOWN_ISSUES.md``).

The mirror had drifted silently for months (PR #362 shipped a PII leak; content
lagged canonical for whole releases) because it was hand-synced. This makes the
transform DATA: one anonymization map, one generator, and a ``--check`` mode CI
can run so a canonical doc change that was never mirrored — or a botched
anonymization — fails the build instead of shipping stale/leaky.

The anonymization map here is the sibling of the FORBIDDEN patterns in
``check_website_docs_pii.py``: this produces clean output, that proves it clean.
Keep the two in sync.

Usage:
    python scripts/ci/generate_website_docs.py           # write the mirror
    python scripts/ci/generate_website_docs.py --check    # CI: fail on any drift
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DOCS = _REPO / "docs"
_WEB = _REPO / "website" / "docs"

# Pages authored directly for the site (nav landing/onboarding) — NOT mirrors of
# canonical, so never regenerated or diffed. `index.md` has a canonical namesake
# (`docs/index.md`, the routing index) but the site's is a distinct intro page.
WEBSITE_ONLY = {
    "index.md",
    "agents.md",
    "installation.md",
    "onboarding.md",
    "onboarding-mockup.html",
}

# Ordered token substitutions (regex, replacement), applied to every generated
# file AFTER any per-file override below. Mirrors check_website_docs_pii.py's
# FORBIDDEN set: profile dir before the OS-username rule; the real email and
# real name before anything that could partial-match them; the public handle
# `ffroliva` in repo URLs / APP_AUTHOR is deliberately never touched.
TOKEN_SUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"profile_ffroliva"), "profile_your-name"),
    (re.compile(r"ffroliva@gmail\.com"), "your.name@gmail.com"),
    (re.compile(r"flavio\.oliva", re.IGNORECASE), "your.name"),
    (re.compile(r"flavio", re.IGNORECASE), "your.name"),
    (re.compile(r"denon82"), "my-profile"),
    (re.compile(r"\bffrol\b"), "your-user"),
)

# Per-file semantic rewrites (literal old → new), applied on the CANONICAL text
# BEFORE the token pass. Used where a token swap would be wrong — e.g. the
# maintainer's email in SECURITY's reporting table becomes actionable GitHub
# private-reporting steps, not a placeholder address.
FILE_OVERRIDES: dict[str, tuple[tuple[str, str], ...]] = {
    "SECURITY.md": (
        (
            "| **Security vulnerability** (RCE, auth bypass, secret leak in logs/output) | "
            "Email <ffroliva@gmail.com> with `gflow-cli SECURITY` in the subject. **Do not** "
            "open a public GitHub issue. PGP key available on request. |",
            "| **Security vulnerability** (RCE, auth bypass, secret leak in logs/output) | "
            "Report privately via GitHub's [private vulnerability reporting]"
            "(https://github.com/ffroliva/gflow-cli/security/advisories/new) form. **Do not** "
            "open a public GitHub issue. |",
        ),
        (
            "| **Suspected supply-chain compromise** | Email + open a private GitHub "
            "Security Advisory at "
            "<https://github.com/ffroliva/gflow-cli/security/advisories/new>. |",
            "| **Suspected supply-chain compromise** | Open a private GitHub "
            "Security Advisory at "
            "<https://github.com/ffroliva/gflow-cli/security/advisories/new>. |",
        ),
    ),
}


def _source_for(dest_name: str) -> Path | None:
    """Resolve the canonical source for a mirrored file (``docs/`` first, then
    repo root for ``KNOWN_ISSUES.md``). Returns None for bespoke/site-only files."""
    if dest_name in WEBSITE_ONLY:
        return None
    in_docs = _DOCS / dest_name
    if in_docs.is_file():
        return in_docs
    in_root = _REPO / dest_name
    if in_root.is_file():
        return in_root
    return None


_GITHUB_DOCS_BLOB = "https://github.com/ffroliva/gflow-cli/blob/main/docs/"


def _rewrite_root_doc_links(text: str) -> str:
    """Repo-root canonical files (KNOWN_ISSUES.md) link sibling docs as
    ``docs/X.md``; mirrored into ``website/docs/`` that resolves to a
    nonexistent ``docs/docs/`` path (#507). Targets that are themselves
    mirrored become sibling-relative links; everything else (release
    evidence, recon specs, plans — deliberately unpublished) becomes an
    absolute GitHub link so the published page never 404s."""

    def repl(match: re.Match[str]) -> str:
        target = match.group(1)
        path_part = target.split("#", 1)[0]
        if "/" not in path_part and (_WEB / path_part).is_file():
            return f"]({target})"
        return f"]({_GITHUB_DOCS_BLOB}{target})"

    return re.sub(r"\]\(docs/([^)]+)\)", repl, text)


def render(dest_name: str, source_text: str) -> str:
    """Apply per-file overrides, root-link rewriting, then the global token map."""
    text = source_text
    for old, new in FILE_OVERRIDES.get(dest_name, ()):
        if old not in text:
            msg = f"{dest_name}: override target not found (canonical changed?): {old[:60]!r}"
            raise ValueError(msg)
        text = text.replace(old, new)
    if _source_for(dest_name) == _REPO / dest_name:
        text = _rewrite_root_doc_links(text)
    for pattern, repl in TOKEN_SUBS:
        text = pattern.sub(repl, text)
    return text


def _mirrored_targets() -> list[Path]:
    """Every published mirror file (existing website/docs entry with a canonical
    source), excluding bespoke site pages."""
    targets: list[Path] = []
    for path in sorted(_WEB.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".md", ".html"):
            continue
        if _source_for(path.name) is not None:
            targets.append(path)
    return targets


def _nav_orphans() -> list[str]:
    """Published `.md` pages that `mkdocs.yml` never lists in `nav:`.

    An orphan still builds and deploys, but nothing links to it — it exists on
    the site only for whoever guesses the URL. Mirroring a doc and wiring it
    into the nav are two separate acts, and the mirror check only ever caught
    the first, so a new page could ship invisible.

    Substring match against the whole `nav:` block: entries are `File: NAME.md`
    and nesting depth does not matter, so this stays true no matter how the nav
    is reorganized. Requires no YAML parser (mkdocs' own `!!python/name:` tags
    make it non-trivial to parse anyway).
    """
    mkdocs = _REPO / "website" / "mkdocs.yml"
    if not mkdocs.exists():
        return []
    text = mkdocs.read_text(encoding="utf-8")
    nav = text[text.index("nav:") :] if "nav:" in text else ""
    return sorted(p.name for p in _WEB.glob("*.md") if p.name not in nav and p.name != "index.md")


def main(argv: list[str]) -> int:
    check = "--check" in argv
    drift: list[str] = []
    written = 0
    for dest in _mirrored_targets():
        source = _source_for(dest.name)
        assert source is not None  # _mirrored_targets guarantees it
        rendered = render(dest.name, source.read_text(encoding="utf-8"))
        current = dest.read_text(encoding="utf-8")
        if rendered == current:
            continue
        if check:
            drift.append(dest.relative_to(_REPO).as_posix())
        else:
            dest.write_text(rendered, encoding="utf-8", newline="\n")
            written += 1

    if check:
        orphans = _nav_orphans()
        if drift or orphans:
            if drift:
                print("website/docs mirror is stale — regenerate with:")
                print("  python scripts/ci/generate_website_docs.py")
                for d in drift:
                    print(f"  DRIFT: {d}")
            for o in orphans:
                print(f"  NAV-ORPHAN: {o} is published but absent from website/mkdocs.yml nav")
            return 1
        print(f"website/docs mirror in sync ({len(_mirrored_targets())} files), nav complete.")
        return 0
    print(f"Regenerated {written} website/docs file(s) from canonical.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
