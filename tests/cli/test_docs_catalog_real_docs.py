"""`gflow docs` against the **real** pages this repository ships (#861).

Separate from `test_cli_docs.py` on purpose. Those tests use a four-page fixture and
answer "does the code do what it says". These answer "does the feature solve the problem
it was filed for", and that question is only decidable against the actual corpus — 126
pages, 136 lines mentioning `duration`, and one of them being the rule that cost a
session. A fixture can be made to pass by choosing its contents.

Scenarios 8, 8b and 11 from `SCENARIO.md`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli import docs_catalog

_REPO_DOCS = Path(__file__).resolve().parents[2] / "docs"

pytestmark = pytest.mark.skipif(
    not _REPO_DOCS.is_dir(), reason="runs against the repository's own docs/ tree"
)


def test_every_shipped_page_is_a_topic_with_no_manifest_to_maintain() -> None:
    """Scenario 11. The directory listing IS the topic list, at build time and read time.

    If this ever fails because someone added a page, the right fix is not to update a
    list — it is that a list appeared somewhere and should be deleted.

    This also covers what a separate `repo_path` test used to assert: `repo_path` is
    `"docs/" + file_name`, and `file_name` comes from this same glob, so equality here
    already proves every printed position exists.
    """
    on_disk = {p.name for p in _REPO_DOCS.glob("*.md")}
    catalogued = {t.file_name for t in docs_catalog.topics()}
    assert catalogued == on_disk
    assert len(on_disk) > 100, len(on_disk)  # the corpus this feature exists for


def test_the_query_that_filed_this_issue_finds_the_rule_it_missed() -> None:
    """Scenario 8 — the feature's acceptance test.

    #861 was filed after `video r2v --duration 10` against a host that offers r2v at 8 s
    only. The rule was already written down, inside a ~4 000-character bullet in `MCP.md`,
    and was never found. This is the query a reader would type, and it has to land.

    Top three rather than exactly first: `_score` gives a heading +10, so a future page
    with a heading naming both terms would take first place without the feature being any
    worse. Pinning position 1 would make an ordinary docs edit red the build.
    """
    matches = docs_catalog.search("r2v duration")
    assert matches, "the rule is unreachable — the feature does not do its job"
    top = [m.position for m in matches[:3]]
    assert any(p.startswith("docs/MCP.md:") for p in top), [m.position for m in matches[:5]]
    # And the line must arrive readable. The raw line is over 4 000 characters; handing
    # that to a terminal is "go read the file" with extra steps.
    assert len(matches[0].text) < 250, len(matches[0].text)


def test_a_bare_common_word_is_answered_honestly_rather_than_silently_truncated() -> None:
    """Scenario 8b, and the limit this feature does not pretend past.

    `--search duration` alone has >100 hits and no ranking tried here could put the rule
    first (see `_score`'s docstring for the two that were built and measured). What the
    command owes the reader is the count and a way to narrow, not a confident wrong first
    result.
    """
    hits = docs_catalog.search("duration")
    assert len(hits) > docs_catalog.MAX_HITS, len(hits)


def test_a_vague_query_leads_with_curated_answers_not_release_records() -> None:
    """What the fence fix and the curated bonus buy, measured on the real corpus.

    Before them, `--search duration` opened with eight `LIVE_VERIFICATION_*` rows — one of
    which was a `#` shell comment inside a fenced block, scored as a heading. Those files
    are dated records of what was true at one release; a reader asking about durations
    wants the reference pages.

    Asserted as a property of the top three rather than a pinned file:line, so an ordinary
    docs edit cannot red the build.
    """
    top = docs_catalog.search("duration")[:3]
    assert top, "no hits at all"
    assert not all(m.file_name.startswith("LIVE_VERIFICATION") for m in top), [
        m.position for m in top
    ]


def test_a_curated_index_row_keeps_the_file_name_it_points_at() -> None:
    """Stripping Markdown from a curated row turned `REFERENCE_STRATEGIES.md` into
    `REFERENCESTRATEGIES.md` — a path that does not exist, printed as the answer, in the
    rows that rank first. 106 of the 126 pages carry an underscore."""
    hits = [m for m in docs_catalog.search("reference") if m.file_name == "INDEX.md"]
    assert hits, "no curated rows matched — the fixture for this assertion is gone"
    assert not any("REFERENCESTRATEGIES" in m.text for m in hits)
    assert any("_" in m.text for m in docs_catalog.search("REFERENCE_STRATEGIES.md"))
