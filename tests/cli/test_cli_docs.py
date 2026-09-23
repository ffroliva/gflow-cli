"""`gflow docs` — resolution, refusal, encoding and `--json` (#861).

Covers `SCENARIO.md` scenarios 1, 2, 4, 6, 7, 10, 12, 16. The two that need the real
shipped pages live in `test_docs_catalog_real_docs.py`; the one that needs a built wheel
lives in `tests/integration/test_docs_ships_in_wheel.py`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from gflow_cli import docs_catalog
from gflow_cli.cli import main
from gflow_cli.errors import EXIT_CODE_MAP, ConfigurationError

_PAGES = {
    "USAGE.md": "# Usage\n\nCommand-by-command reference for every flag.\n",
    "USER_GUIDE.md": "# User Guide\n\nTask-oriented walkthroughs.\n",
    "CONFIGURATION.md": "# Configuration\n\nEnvironment variables and precedence.\n",
    "INDEX.md": (
        "# Documentation Index\n\n"
        "## Topic shortcuts\n\n"
        '**"Where do generated files land?"** → [CONFIGURATION](CONFIGURATION.md#paths)\n'
    ),
}


@pytest.fixture
def docs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A small documentation tree standing in for the shipped one."""
    directory = tmp_path / "docs"
    directory.mkdir()
    for name, body in _PAGES.items():
        (directory / name).write_text(body, encoding="utf-8")
    monkeypatch.setattr(docs_catalog, "_docs_dir", lambda: directory)
    return directory


def _run(*args: str) -> Any:
    return CliRunner().invoke(main, ["docs", *args], catch_exceptions=False)


# --- resolution -------------------------------------------------------------


@pytest.mark.parametrize("name", ["usage", "USAGE", "USAGE.md", "usage.md", "  usage  "])
def test_a_page_resolves_from_every_form_a_reader_types(docs_dir: Path, name: str) -> None:
    assert docs_catalog.resolve(name).file_name == "USAGE.md"


def test_an_unambiguous_prefix_resolves(docs_dir: Path) -> None:
    # Scenario 16: `gflow docs conf` is what a reader types; requiring the full name
    # makes the command feel broken for the most obvious input.
    assert docs_catalog.resolve("conf").file_name == "CONFIGURATION.md"


def test_an_ambiguous_prefix_is_refused_and_names_the_candidates(docs_dir: Path) -> None:
    with pytest.raises(ConfigurationError) as caught:
        docs_catalog.resolve("us")
    detail = str(caught.value)
    assert "usage" in detail and "user-guide" in detail


def test_an_underscore_in_the_name_resolves_like_the_dash(docs_dir: Path) -> None:
    assert docs_catalog.resolve("user_guide").file_name == "USER_GUIDE.md"


# --- refusal ----------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../../etc/passwd",
        r"..\..\..\Windows\System32\config\SAM",
        "/etc/shadow",
        "C:\\Windows\\win.ini",
        "..",
        "../INDEX",
        "usage/../../secret",
    ],
)
def test_a_topic_name_is_never_used_to_build_a_path(docs_dir: Path, hostile: str) -> None:
    """Scenarios 1 and 2.

    The argument is a key looked up in the enumerated set of pages, so a traversal
    attempt is an ordinary unknown-topic refusal. Asserted per input anyway: this is the
    one property of this command whose breakage would be a file-read primitive, and a
    future refactor that starts joining paths must fail here.
    """
    with pytest.raises(ConfigurationError):
        docs_catalog.resolve(hostile)


@pytest.mark.parametrize(
    "hostile", ["../../../../etc/passwd", "usage\x00.md", r"..\..\Windows\win.ini"]
)
def test_a_hostile_name_is_refused_through_the_command_too(docs_dir: Path, hostile: str) -> None:
    """The traversal tests above stop at `resolve()`. If `_emit_page` ever grew its own
    path join, every one of them would still pass — so the refusal is asserted at the
    surface a user actually reaches."""
    result = _run(hostile)
    assert result.exit_code == 11, result.output
    assert "passwd" not in result.output or "no documentation topic" in result.output


def test_a_refusal_carries_a_remediation_about_docs_not_transports(docs_dir: Path) -> None:
    """Every refusal used to inherit ConfigurationError's default hint: "Run `gflow config
    list-transports`" — advice about a transport registry, printed at someone who asked
    for a page."""
    # The error lane emits two JSON documents (the structured-log line, then the payload),
    # so this asserts on the text rather than pretending it is one object.
    output = _run("nosuchtopic", "--json").output
    assert '"remediation_hint"' in output, output
    assert "gflow docs" in output, output
    assert "list-transports" not in output, output


def test_an_unknown_topic_exits_11_and_suggests(docs_dir: Path) -> None:
    # Scenario 6: a class already in EXIT_CODE_MAP — no new exception, no new exit code.
    assert EXIT_CODE_MAP[ConfigurationError] == 11
    # "guide" is nobody's prefix, but it IS inside `user-guide` — so the refusal has
    # something to offer. A refusal that only says no makes the reader run `docs` and
    # scan 126 rows, which is the state this command exists to end.
    result = _run("guide")
    assert result.exit_code == 11, result.output
    assert "user-guide" in result.output


def test_a_typo_is_offered_the_nearest_topic(docs_dir: Path) -> None:
    # `usge` shares no substring with `usage`, so the substring scan offered nothing and
    # the documented promise ("suggests the nearest ones") was false until difflib.
    result = _run("usge")
    assert result.exit_code == 11, result.output
    assert "usage" in result.output


@pytest.mark.parametrize("term", ["flag", ""])
def test_a_topic_and_a_search_together_are_refused(docs_dir: Path, term: str) -> None:
    # The empty term is the interesting one: the guard used to test truthiness while the
    # dispatch tested `is not None`, so `--search ""` slipped through and the topic was
    # silently discarded.
    result = _run("usage", "--search", term)
    assert result.exit_code == 11, result.output
    assert "not both" in result.output


def test_an_empty_topic_is_refused_rather_than_listing_everything(docs_dir: Path) -> None:
    result = _run("")
    assert result.exit_code == 11, result.output


# --- search -----------------------------------------------------------------


def test_a_search_with_no_matches_exits_zero(docs_dir: Path) -> None:
    # Scenario 7: an empty result is a true answer to a valid question, not a failure.
    result = _run("--search", "zzzznotinanypage")
    assert result.exit_code == 0, result.output


def test_a_curated_index_answer_outranks_a_body_hit(docs_dir: Path) -> None:
    # Scenario 9. "generated files land" is an INDEX shortcut; "Environment" is body text.
    matches = docs_catalog.search("generated files land")
    assert matches
    assert matches[0].file_name == "INDEX.md"
    assert matches[0].score >= docs_catalog._CURATED_BONUS


def test_search_still_works_when_the_index_has_no_curated_rows(
    docs_dir: Path,
) -> None:
    # Scenario 10: the curated bonus is additive. If INDEX.md's format changes, search
    # degrades to plain body text — it must not crash and must not silently return nothing.
    (docs_dir / "INDEX.md").write_text("# Index\n\nNo shortcuts here any more.\n", "utf-8")
    matches = docs_catalog.search("precedence")
    assert matches and matches[0].file_name == "CONFIGURATION.md"


def test_a_hash_inside_a_fenced_block_is_not_scored_as_a_heading(docs_dir: Path) -> None:
    """A `#` in a code fence is a shell comment, not a section heading.

    Scored as a heading it beat real prose: `# 5. One cheapest stable T2V generation, no
    explicit --duration` — a line from a release record — ranked 2nd of 136 for
    `--search duration`, which is the noise the ranking was added to remove.
    """
    (docs_dir / "USAGE.md").write_text(
        "\n".join(
            [
                "# Usage",  # 1
                "",  # 2
                "A real heading follows.",  # 3
                "",  # 4
                "## widget options",  # 5 — a real heading
                "",  # 6
                "```bash",  # 7
                "# widget --flag",  # 8 — a shell comment, not a heading
                "```",  # 9
            ]
        ),
        encoding="utf-8",
    )
    by_line = {m.line_no: m.score for m in docs_catalog.search("widget")}
    heading, fenced = by_line[5], by_line[8]
    assert heading >= docs_catalog._HEADING_BONUS, by_line
    assert fenced < docs_catalog._HEADING_BONUS, by_line


def test_all_query_terms_must_appear_on_the_line(docs_dir: Path) -> None:
    assert docs_catalog.search("environment precedence")
    assert not docs_catalog.search("environment walkthroughs")


def test_a_long_line_is_windowed_around_the_match(docs_dir: Path) -> None:
    (docs_dir / "USAGE.md").write_text(
        "# Usage\n\n" + ("padding " * 400) + "NEEDLE" + (" padding" * 400) + "\n",
        encoding="utf-8",
    )
    text = docs_catalog.search("needle")[0].text
    assert "NEEDLE" in text
    assert len(text) < 250, len(text)  # not the 6 KB line


# --- rendering --------------------------------------------------------------


@pytest.mark.parametrize("args", [["docs", "usage"], ["docs"], ["docs", "--search", "the"]])
def test_the_command_survives_a_cp1252_console(args: list[str]) -> None:
    """The #846 shape, one release old, asserted on the COMMAND rather than on a helper.

    The first version of this test monkeypatched `sys.stdout` and called the encoding
    helper directly. A council mutation proved it worthless: replacing every call site of
    that helper with `str` left the whole suite green. Nothing required the command to
    use it, and Rich's own box-drawing glyphs were never covered at all.

    A subprocess with `PYTHONIOENCODING=cp1252` is the only arrangement where a real
    `UnicodeEncodeError` can happen, so it is the only one that can fail.
    """
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    env.pop("PYTHONUTF8", None)
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "gflow_cli.cli", *args],
        capture_output=True,
        text=True,
        encoding="cp1252",
        errors="replace",
        env=env,
    )
    assert done.returncode == 0, done.stderr
    assert "UnicodeEncodeError" not in done.stderr, done.stderr
    assert done.stdout.strip(), "the command printed nothing"


# --- --json -----------------------------------------------------------------


def test_topics_json_lists_every_page(docs_dir: Path) -> None:
    payload = json.loads(_run("--json").output)
    assert {t["topic"] for t in payload["topics"]} == {
        "usage",
        "user-guide",
        "configuration",
        "index",
    }
    assert all(t["path"].startswith("docs/") for t in payload["topics"])


def test_page_json_carries_the_content(docs_dir: Path) -> None:
    payload = json.loads(_run("usage", "--json").output)
    assert payload["topic"] == "usage"
    assert payload["path"] == "docs/USAGE.md"
    assert "Command-by-command" in payload["content"]


def test_search_json_reports_positions_and_what_it_held_back(docs_dir: Path) -> None:
    payload = json.loads(_run("--search", "reference", "--json").output)
    assert payload["term"] == "reference"
    assert payload["matches"], payload
    first = payload["matches"][0]
    # The position must be the path that EXISTS — the file name, never the slug.
    assert first["path"] == "docs/USAGE.md:3", first
    assert payload["omitted"] == 0
    assert payload["total"] == len(payload["matches"])


def test_an_installation_with_no_pages_says_so_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(docs_catalog, "_docs_dir", lambda: None)
    assert docs_catalog.topics() == []
    assert _run().exit_code == 0
    assert _run("usage").exit_code == 11
