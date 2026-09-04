"""Unit tests for the council-memory routing gate.

The gate's whole value is that a citation resolves deterministically to a file,
so its two interesting behaviours are the ones tested here: it must NOT strip
inline code spans (the Dimension -> Slugs table writes citations as `` `[[x]]` ``,
and stripping them erased 46 of 50 real citations while this gate was being
written), and it must ignore TOML array-of-tables keys, which use the same
brackets and appear inline in `skills/gflow-cli/SKILL.md`.
"""

from __future__ import annotations

from scripts.ci import check_council_memory as gate


def _slugs(text: str) -> set[str]:
    """Run the gate's extraction over one document's text."""
    return {
        slug
        for line in gate.strip_code(text).splitlines()
        for slug in gate.CITATION_RE.findall(line)
        if slug not in gate.NOT_A_SLUG
    }


def test_citation_inside_an_inline_code_span_still_counts() -> None:
    # The real table format. Stripping inline code here is what broke it.
    table = "| D1 | `[[pr-must-verify-on-affected-surface]]`, `[[video-model-capability-matrix]]` |"
    assert _slugs(table) == {
        "pr-must-verify-on-affected-surface",
        "video-model-capability-matrix",
    }


def test_toml_array_of_tables_is_not_a_citation() -> None:
    prose = "Overrides via `movie.toml` `[[scene.instructions.card]]` or `[scene.instructions]`."
    assert _slugs(prose) == set()


def test_slug_with_dots_is_still_a_citation() -> None:
    # `data-layer-v0.9.0-bugs` is a real slug, so "has a dot" cannot be the
    # rule that separates citations from TOML keys.
    assert _slugs("See `[[data-layer-v0.9.0-bugs]]`.") == {"data-layer-v0.9.0-bugs"}


def test_fenced_blocks_are_ignored_but_line_numbers_survive() -> None:
    doc = "\n".join(["intro", "```toml", "[[scene.instructions.card]]", "```", "[[real-slug]]"])
    assert _slugs(doc) == {"real-slug"}
    # Line numbering must be preserved so DANGLING reports point at the right line.
    assert len(gate.strip_code(doc).splitlines()) == len(doc.splitlines())


def test_forbidden_patterns_catch_the_identifiers_the_port_strips() -> None:
    samples = {
        "session id": "metadata:\n  originSessionId: 256bacd3-527a",
        "maintainer email": "signed by dev@axelate.io",
        # Forward-slash form on purpose: the repo-hygiene gate forbids a literal
        # backslash Windows user path in tracked source, and the pattern under
        # test matches either separator.
        "OS username": "/c/Users/ffrol/AppData",
        "Flow project UUID": "project d2e1c023-de75-4196-a9c4-4be3fba5bc54",
    }
    import re

    for label, pattern in gate.FORBIDDEN:
        assert re.search(pattern, samples[label]), label

    # The PUBLIC GitHub handle must survive every pattern untouched.
    public = "https://github.com/ffroliva/gflow-cli/issues/288"
    assert not any(re.search(pattern, public) for _, pattern in gate.FORBIDDEN)


def test_the_real_tree_passes() -> None:
    # The gate is only useful if it is green on the tree it ships with.
    assert gate.main() == 0
