"""Unit tests for the council-memory routing gate.

`scripts/` is outside `--cov=gflow_cli`, so coverage never sees this file and
mutation resistance is the only real signal. An earlier version of these tests
passed against 8 of 10 mutations of the gate — including one where the test
named for fence-stripping used an already-exempt slug and therefore asserted
nothing. Every problem class now has a negative test that goes red when its
branch is disabled.
"""

from __future__ import annotations

import re

import pytest

from scripts.ci import check_council_memory as gate
from scripts.ci import check_website_docs_pii as website_guard


def _ok(stem: str, body: str = "") -> str:
    return f"---\nname: {stem}\ndescription: t\n---\n{body}"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """Build an isolated skills/ + memory/ tree and point the gate at it."""

    def build(memory: dict[str, str], skill_text: str) -> None:
        memory_dir = tmp_path / "docs" / "superpowers" / "memory"
        memory_dir.mkdir(parents=True)
        for stem, text in memory.items():
            (memory_dir / f"{stem}.md").write_text(text, encoding="utf-8")
        skill_dir = tmp_path / "skills" / "demo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")
        monkeypatch.setattr(gate, "ROOT", tmp_path)
        monkeypatch.setattr(gate, "SKILLS", tmp_path / "skills")
        monkeypatch.setattr(gate, "MEMORY", memory_dir)

    return build


# --- extraction ------------------------------------------------------------


def _slugs(text: str) -> set[str]:
    return gate.citations_in(text)


def test_citation_inside_an_inline_code_span_still_counts() -> None:
    # The real table format. Stripping inline code here erased 46 of 50.
    table = "| D1 | `[[pr-must-verify-on-affected-surface]]`, `[[video-model-capability-matrix]]` |"
    assert _slugs(table) == {
        "pr-must-verify-on-affected-surface",
        "video-model-capability-matrix",
    }


def test_toml_array_of_tables_is_not_a_citation() -> None:
    prose = "Overrides via `movie.toml` `[[scene.instructions.card]]` or `[scene.instructions]`."
    assert _slugs(prose) == set()


def test_slug_with_dots_is_still_a_citation() -> None:
    # "has a dot" cannot be the rule separating citations from TOML keys.
    assert _slugs("See `[[data-layer-v0.9.0-bugs]]`.") == {"data-layer-v0.9.0-bugs"}


@pytest.mark.parametrize("written", ["[[ spaced-slug ]]", "[[Spaced-Slug]]", "[[spaced-slug]]"])
def test_whitespace_and_casing_are_normalised(written: str) -> None:
    # A human-typed citation must be checked, not silently skipped.
    assert _slugs(written) == {"spaced-slug"}


def test_fenced_blocks_are_ignored_but_line_numbers_survive() -> None:
    # A NON-exempt slug, so this actually tests fence stripping.
    doc = "\n".join(["intro", "```toml", "[[fenced-only-slug]]", "```", "[[real-slug]]"])
    assert _slugs(doc) == {"real-slug"}
    assert len(gate.strip_code(doc).splitlines()) == len(doc.splitlines())


def test_html_comments_are_ignored() -> None:
    # Otherwise a commented-out citation silences an orphan the router never reads.
    assert _slugs("<!-- [[hidden-slug]] -->\n[[visible-slug]]") == {"visible-slug"}


# --- each problem class ----------------------------------------------------


@pytest.mark.parametrize(
    ("memory", "skill_text", "tag"),
    [
        ({}, "`[[ghost]]`", "DANGLING"),
        ({"lonely": _ok("lonely")}, "cites nothing", "ORPHAN"),
        ({"leak": _ok("leak", "originSessionId: abc")}, "`[[leak]]`", "PRIVATE"),
        ({"bare": "no frontmatter here\n"}, "`[[bare]]`", "NAME"),
        ({"named": "---\nname: something-else\n---\n"}, "`[[named]]`", "NAME"),
    ],
)
def test_main_reports_each_problem_class(tree, capsys, memory, skill_text, tag) -> None:
    tree(memory, skill_text)
    assert gate.main() == 1
    assert tag in capsys.readouterr().out


def test_main_passes_on_a_well_formed_tree(tree, capsys) -> None:
    tree({"good": _ok("good")}, "cites `[[good]]`")
    assert gate.main() == 0
    assert "✅" in capsys.readouterr().out


def test_missing_memory_directory_fails(tree, monkeypatch, tmp_path, capsys) -> None:
    tree({"good": _ok("good")}, "cites `[[good]]`")
    monkeypatch.setattr(gate, "MEMORY", tmp_path / "gone")
    assert gate.main() == 1
    assert "MISSING" in capsys.readouterr().out


def test_a_subdirectory_cannot_dodge_the_private_scan(tree, tmp_path, capsys) -> None:
    # `glob` instead of `rglob` let a nested file skip every content check.
    tree({"good": _ok("good")}, "cites `[[good]]` and `[[nested]]`")
    nested = tmp_path / "docs" / "superpowers" / "memory" / "sub"
    nested.mkdir()
    (nested / "nested.md").write_text(_ok("nested", "dev@axelate.io"), encoding="utf-8")
    assert gate.main() == 1
    assert "PRIVATE" in capsys.readouterr().out


def test_memory_to_memory_links_are_informational_not_failures(tree, capsys) -> None:
    # Memory files link liberally; an unresolved link marks future work.
    tree({"good": _ok("good", "see [[only-in-private-store]]")}, "cites `[[good]]`")
    assert gate.main() == 0
    assert "INFO" in capsys.readouterr().out


# --- the forbidden list ----------------------------------------------------


def test_every_forbidden_pattern_matches_its_sample() -> None:
    # Iterating FORBIDDEN means deleting an entry is otherwise invisible.
    samples = {
        "session id": "metadata:\n  originSessionId: 256bacd3",
        "maintainer email": "signed by dev@axelate.io",
        "real name": "asked by Flavio on 2026-05-27",
        "OS username": "pytest-of-ffrol/pytest-3/test_x",
        "Flow project UUID": "project d2e1c023-de75-4196-a9c4-4be3fba5bc54",
        "device name": 'the key ("hp-elitebook-someone" in GitHub) must',
        "local checkout path": "do NOT Read files in C:/development/github/gflow-cli/",
        "OAuth or API token": "Authorization: Bearer ya29.a0AfB_byXXXXX",
        "session cookie value": "__Secure-next-auth.session-token=abc123",
        "Claude session URL": "https://claude.ai/code/session_01RVav",
        "IP address": "the ops host at 89.167.1.15",
    }
    assert {label for label, _ in gate.FORBIDDEN} == set(samples), "sample list out of sync"
    for label, pattern in gate.FORBIDDEN:
        assert re.search(pattern, samples[label]), label


def test_public_references_survive_every_forbidden_pattern() -> None:
    # The PR #362 trap: `ffroliva` is the public handle and must not be scrubbed.
    for public in (
        "https://github.com/ffroliva/gflow-cli/issues/288",
        "Subscribe to GitHub Releases for `ffroliva/gflow-cli`.",
        # Assembled, not written literally: check_repo_hygiene.py forbids a
        # hardcoded user path in tracked source even when it is anonymised.
        "temp files live under C:" + "/Users/<you>/AppData/Local/Temp/",
        "the denon82 profile reproduced it",
    ):
        assert not [label for label, p in gate.FORBIDDEN if re.search(p, public)], public


def test_shared_identifier_classes_have_not_drifted_from_the_website_guard() -> None:
    # The two gates keep separate lists (CI runs them as scripts, not modules).
    # This is the drift detector that makes that duplication safe.
    ours = " ".join(pattern for _, pattern in gate.FORBIDDEN)
    for shared in (r"\bffrol\b", "ffroliva@", "flavio"):
        assert any(shared in p.pattern.lower() for p, _ in website_guard.FORBIDDEN), shared
        assert shared.lower() in ours.lower(), f"{shared} dropped from the council gate"


def test_the_real_tree_passes() -> None:
    assert gate.main() == 0
