"""The documentation catalog behind `gflow docs` (#861) — enumeration, lookup, search.

Pure: no network, no account, no database, no Click. Everything it reads is text that
shipped inside this package.

**Why this exists.** 126 pages in `docs/` and nothing in the CLI points at any of them, so
at the moment of use the knowledge is unreachable and the reader guesses. The failure that
filed #861 was a run with `--duration 10` against a host that offers reference-to-video at
8 s only. That rule was already written down -- inside a 4 000-character bullet in
`MCP.md`. Which is why :func:`search` returns a **line with its position**, windowed around
the match: answering "it is in MCP.md" would leave the reader exactly where they started.

**Why no manifest.** A hand-maintained topic list drifts the first time someone adds a
page. The directory listing is the list, at build time (`hatch_build.py` globs it) and at
read time (:func:`topics` iterates it). There is nothing to keep in sync.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from gflow_cli.errors import ConfigurationError

#: The directory `hatch_build.py` installs the pages into, inside this package.
_PACKAGE_DOCS = "_docs"

#: Where the pages live in the repository — printed so a reader can find the source,
#: and the prefix used for the `file:line` positions search reports.
REPO_DOCS_DIR = "docs"

#: How much of a matching line to show. `MCP.md` has single lines over 4 000 characters;
#: printing one whole is the "go read the file" answer with extra steps.
_SNIPPET_WIDTH = 160

#: Body hits shown before the caller is told how many more there are.
MAX_HITS = 20


@dataclass(frozen=True, slots=True)
class Topic:
    """One documentation page."""

    slug: str
    """Lowercase, dash-separated, no suffix — `REFERENCE_STRATEGIES.md` → `reference-strategies`."""

    file_name: str
    """The page's own file name, e.g. `REFERENCE_STRATEGIES.md`."""

    title: str
    """The first `# ` heading, or the file name when a page has none."""

    summary: str
    """The first line of prose under the title. Empty when the page opens with a table."""

    @property
    def repo_path(self) -> str:
        """Where to find this page in the repository."""
        return f"{REPO_DOCS_DIR}/{self.file_name}"


@dataclass(frozen=True, slots=True)
class Match:
    """One search hit."""

    file_name: str
    """The page's real file name — `MCP.md`, not `mcp.md`. The slug is for typing at the
    command line; a position a reader is meant to open has to be the path that exists."""

    line_no: int
    """1-indexed line within that page."""

    text: str
    """The matching line, windowed around the most selective term."""

    score: int
    """Relevance — see :func:`_score`. Only meaningful for ordering."""

    @property
    def topic(self) -> str:
        return _slug(self.file_name)

    @property
    def position(self) -> str:
        return f"{REPO_DOCS_DIR}/{self.file_name}:{self.line_no}"


def _docs_dir() -> Any:
    """The directory holding the pages, or ``None`` when there is none.

    Two sources, in order:

    1. ``gflow_cli/_docs`` — what `hatch_build.py` ships. This is the only one that exists
       for someone who installed from PyPI, which is the whole point of #861.
    2. the repository's own ``docs/`` — because the build hook runs at *build* time, so an
       editable install (`uv sync`, every test run, every contributor) has no ``_docs``.
       Without this fallback the command would work for users and be dead in development,
       which is the reverse of the usual bug and twice as confusing.

    Resolved through ``importlib.resources`` rather than ``__file__`` so a zipimport or
    frozen environment still reads. The repository fallback is derived from the package's
    own location and never from user input.
    """
    try:
        shipped = resources.files("gflow_cli").joinpath(_PACKAGE_DOCS)
        if shipped.is_dir():
            return shipped
    except (ModuleNotFoundError, TypeError):  # pragma: no cover - defensive
        pass
    try:
        checkout = Path(__file__).resolve().parents[2] / REPO_DOCS_DIR
    except IndexError:  # pragma: no cover - defensive
        return None
    return checkout if checkout.is_dir() else None


def _slug(file_name: str) -> str:
    return file_name[:-3].lower().replace("_", "-") if file_name.endswith(".md") else file_name


#: Markdown that carries no meaning once a line is a one-line summary in a table.
#: **Single `*` and `_` are deliberately NOT stripped.** They were, and it turned
#: `REFERENCE_STRATEGIES.md` into `REFERENCESTRATEGIES.md` — a file name that does not
#: exist, printed as the answer. 106 of the 126 pages carry an underscore.
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_EMPHASIS = re.compile(r"(\*\*|__|`)")
_LIST_ITEM = re.compile(r"^(\d+[.)]|[-*+])\s")

#: How much of the first prose line the topic table shows.
_SUMMARY_WIDTH = 90


def _plain(line: str) -> str:
    """*line* with the Markdown taken out: links become their label, bold/code markers go.

    A summary column showing `Read [DISCLAIMER.md](../DISCLAIMER.md) first` is worse than
    no summary — it costs the reader the same parsing the raw file would.
    """
    return " ".join(_EMPHASIS.sub("", _LINK.sub(r"\1", line)).split())


def _title_and_summary(text: str) -> tuple[str, str]:
    """The page's `# ` heading and its first line of prose.

    Tolerant by design: a page with no heading, or one that opens with a table, a list or
    a blockquote, still produces a :class:`Topic` — it just has less to say about itself.
    """
    title, summary = "", ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not title:
            if line.startswith("# "):
                title = _plain(line[2:])
            continue
        if line.startswith(("#", ">", "|", "`", "<")) or _LIST_ITEM.match(line):
            continue
        summary = _plain(line)
        if len(summary) > _SUMMARY_WIDTH:
            summary = summary[:_SUMMARY_WIDTH].rstrip() + " …"
        break
    return title, summary


def _read(entry: Any) -> str:
    """A page's text.

    ``read_bytes`` then decode, never ``read_text(errors=…)``: ``Traversable.read_text``
    takes only ``encoding``, so passing ``errors`` raises ``TypeError`` on the one path
    that matters — a page read out of a zipimport. Every test here exercises the checkout
    fallback, where ``Path`` happens to accept it, so the bug could not surface locally.

    Replacement rather than strict: a page that cannot be decoded must still be listed and
    searched, because a catalog that raises is worse than one that is lossy.
    """
    try:
        return entry.read_bytes().decode("utf-8", errors="replace")
    except (OSError, ValueError, AttributeError):  # pragma: no cover - defensive
        return ""


def topics() -> list[Topic]:
    """Every shipped page, by slug. Empty when no docs directory is available."""
    directory = _docs_dir()
    if directory is None:
        return []
    found: list[Topic] = []
    for entry in directory.iterdir():
        name = entry.name
        if not name.endswith(".md") or not entry.is_file():
            continue
        title, summary = _title_and_summary(_read(entry))
        found.append(Topic(slug=_slug(name), file_name=name, title=title or name, summary=summary))
    return sorted(found, key=lambda t: t.slug)


def read(topic: Topic) -> str:
    """The full text of a page."""
    directory = _docs_dir()
    if directory is None:  # pragma: no cover - resolve() cannot yield a Topic without one
        return ""
    return _read(directory.joinpath(topic.file_name))


#: Remediation for every refusal this module raises. Without it they inherit
#: `ConfigurationError._default_remediation`, which tells the reader to run
#: `gflow config list-transports` — advice about a transport registry, printed at someone
#: who asked for a documentation page.
_REMEDIATION = "Run `gflow docs` for the list of topics, or `gflow docs --search <term>`."


def resolve(name: str) -> Topic:
    """The page *name* refers to.

    Accepts the slug, the file name, either case, and any unambiguous prefix — the three
    forms a reader actually types (`usage`, `USAGE.md`, `ref`).

    **The lookup is a dictionary hit against the enumerated pages; the argument is never
    joined onto a path.** That is what makes `gflow docs ../../../../etc/passwd` an
    ordinary unknown-topic refusal rather than a file read, and it stays true without a
    traversal check to keep correct.
    """
    available = topics()
    if not available:
        raise ConfigurationError(
            detail=(
                "no documentation is available in this installation — the pages ship "
                "inside the wheel, so a source tree without them cannot serve them"
            ),
            remediation_hint="Reinstall gflow-cli, or run from a checkout with a docs/ tree.",
        )
    key = name.strip().lower()
    if key.endswith(".md"):
        key = key[:-3]
    key = key.replace("_", "-")
    by_slug = {t.slug: t for t in available}
    if key in by_slug:
        return by_slug[key]
    if key:
        starting = [t for t in available if t.slug.startswith(key)]
        if len(starting) == 1:
            return starting[0]
        if len(starting) > 1:
            raise ConfigurationError(
                detail=(
                    f"{name!r} matches {len(starting)} topics: "
                    f"{', '.join(t.slug for t in starting[:8])}"
                    f"{' …' if len(starting) > 8 else ''} — name one of them"
                ),
                remediation_hint=_REMEDIATION,
            )
    raise ConfigurationError(
        detail=_unknown_detail(name, key, available), remediation_hint=_REMEDIATION
    )


def _unknown_detail(name: str, key: str, available: list[Topic]) -> str:
    """The refusal, with the nearest topics attached.

    `difflib.get_close_matches` rather than only a substring scan: a substring scan offers
    nothing for a typo, and a typo is the common case — `usge` shares no substring with
    `usage` but is one transposition away.
    """
    slugs = [t.slug for t in available]
    near = difflib.get_close_matches(key, slugs, n=3, cutoff=0.6) if key else []
    if not near and key:
        near = [s for s in slugs if key in s][:3]
    hint = f" — did you mean {', '.join(near)}?" if near else ""
    return f"no documentation topic named {name!r}{hint}"


def _snippet(line: str, terms: list[str]) -> str:
    """*line*, windowed around its most selective term.

    `MCP.md` documents the migrated-host duration rule inside a single 4 000-character
    bullet. Returning that line whole is the failure #861 describes, restated as output.

    The window centres on whichever term occurs **least** on this line, not on the first
    one typed: for `r2v duration` against that bullet, `r2v` recurs throughout while
    `duration` marks the one clause the reader wants.
    """
    collapsed = " ".join(line.split())
    if len(collapsed) <= _SNIPPET_WIDTH:
        return collapsed
    lowered = collapsed.lower()
    present = [t for t in terms if t in lowered]
    if not present:  # pragma: no cover - callers only pass lines that matched
        return collapsed[:_SNIPPET_WIDTH] + " …"
    at = lowered.find(min(present, key=lowered.count))
    start = max(0, at - _SNIPPET_WIDTH // 3)
    end = min(len(collapsed), start + _SNIPPET_WIDTH)
    return ("… " if start else "") + collapsed[start:end] + (" …" if end < len(collapsed) else "")


#: An `INDEX.md` § Topic shortcuts row — `**"How do I X?"** → [LABEL](TARGET.md)`. These
#: are the highest-signal lines in the tree: each was written to *be* an answer. They are
#: a RANKING bonus, not a separate pass. A separate parser existed and was measured out:
#: its hits were a strict subset of what the body scan already found (0 of 7 for the
#: flagship `r2v duration`), so 28 lines of regex only re-ordered rows search already had
#: — and stripping Markdown from the row mangled the file name it was pointing at.
_CURATED_BONUS = 20
_HEADING_BONUS = 10
_FENCE = "```"


def _score(line: str, terms: list[str], *, curated: bool, in_fence: bool) -> int:
    """How much a matching line is *about* the terms, rather than merely containing them.

    A curated `INDEX.md` answer outranks a heading, which outranks prose; and a line that
    names a term repeatedly is discussing it rather than mentioning it in passing.

    **The heading bonus must not fire inside a fenced code block.** `# 5. One cheapest
    stable T2V generation, no explicit --duration` is a shell comment in a release record;
    scored as a heading it ranked 2nd of 136 for `--search duration` — exactly the noise
    this ordering was added to remove.

    **What ranking cannot do.** `--search duration` has 136 hits and no ordering tried here
    puts the rule first. Three were built and measured; the third reused `INDEX.md`'s
    routing as a reference-vs-record signal, and it is false — `INDEX.md` links 93 of the
    126 pages. It is not here because it did not work. What works is a second query word:
    `r2v duration` returns 7 hits with the rule first. See `SCENARIO.md` § M3.
    """
    lowered = line.lower()
    occurrences = sum(lowered.count(term) for term in terms)
    bonus = _CURATED_BONUS if curated else 0
    if not in_fence and line.lstrip().startswith("#"):
        bonus += _HEADING_BONUS
    return bonus + occurrences


def search(term: str) -> list[Match]:
    """Every line mentioning *term*, most relevant first.

    Whitespace splits the query into terms that must **all** appear on the line, so
    `--search "r2v duration"` narrows where a single common word cannot.
    """
    terms = [t for t in term.strip().lower().split() if t]
    if not terms:
        return []
    hits: list[Match] = []
    for topic in topics():
        is_index = topic.file_name == "INDEX.md"
        in_fence = False
        for line_no, raw in enumerate(read(topic).splitlines(), start=1):
            if raw.lstrip().startswith(_FENCE):
                in_fence = not in_fence
            lowered = raw.lower()
            if not all(t in lowered for t in terms):
                continue
            hits.append(
                Match(
                    file_name=topic.file_name,
                    line_no=line_no,
                    text=_snippet(raw, terms),
                    score=_score(
                        raw,
                        terms,
                        curated=is_index and raw.lstrip().startswith('**"'),
                        in_fence=in_fence,
                    ),
                )
            )
    hits.sort(key=lambda m: (-m.score, m.file_name, m.line_no))
    return hits
