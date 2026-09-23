"""`gflow docs` — the documentation, reachable from the terminal (#861).

Three shapes, all read-only, all offline: list the topics, print one, or search for the
line that answers a question. No network, no account, no credits, no database.

**No MCP twin yet — deferred with a shape, not excluded on principle** (registered in
`tests/mcp/test_cli_parity.py::_MCP_EXEMPT`, so the decision is enforced rather than
asserted here). Documentation is not a tool call: MCP models documents as **resources**,
and the upgrade path is a resources provider over `gflow_cli.docs_catalog` — which is why
every decision lives in that module, Click-free and returning data. This file renders.
"""

from __future__ import annotations

import sys
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from gflow_cli import docs_catalog, json_output
from gflow_cli._cli_helpers import run_with_handlers
from gflow_cli.docs_catalog import MAX_HITS
from gflow_cli.errors import ConfigurationError

console = Console()

#: Widths that keep one topic on one terminal row. 126 topics rendered as wrapped
#: two-line cells was 424 lines — seventeen screens nobody reads, which makes the
#: listing decorative and `--search` the only real entry point. Truncated, it is one
#: screenful per 40 topics and stays scannable; `--json` still carries the full text.
_TOPIC_COL = 34
_SUMMARY_COL = 72


def _forgive_console_encoding() -> None:
    """Make stdout replace characters it cannot encode instead of raising.

    The pages are full of `—`, `→` and `✅`, and a Windows console is frequently cp1252.
    Printing straight into one raises `UnicodeEncodeError` part-way through a page — the
    exact shape of #846, shipped one release ago.

    One `reconfigure` covers `click.echo`, Rich's tables **and** Rich's box-drawing
    glyphs; the per-string helper this replaces covered only the strings it was wrapped
    around, and a review proved it: replacing every one of its call sites with `str` left
    the whole suite green. A stdout without `reconfigure` (a captured stream in a test
    harness) is already text-mode and UTF-8, so there is nothing to forgive there.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(errors="replace")
    except (ValueError, OSError):  # pragma: no cover - a stream that refuses to be retuned
        pass


@click.command("docs")
@click.argument("topic", required=False)
@click.option(
    "--search",
    "term",
    default=None,
    metavar="TERM",
    help="Find the lines mentioning TERM across every page, most relevant first.",
)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON.")
def docs(topic: str | None, term: str | None, as_json: bool) -> None:
    """Browse gflow's own documentation.

    \b
      gflow docs                          list every topic
      gflow docs usage                    print one page
      gflow docs --search "r2v duration"  find the line that answers a question

    Entirely offline and read-only: the pages ship inside the package.
    """
    run_with_handlers(lambda: _run(topic, term, as_json), cli_command="docs", as_json=as_json)


async def _run(topic: str | None, term: str | None, as_json: bool) -> None:
    _forgive_console_encoding()
    # `is not None`, not truthiness, on BOTH sides: `--search ""` is a passed flag, and
    # testing it for truth let `gflow docs usage --search ""` through the guard and then
    # silently discard the topic.
    if topic is not None and term is not None:
        raise ConfigurationError(
            detail=(
                "pass a topic or --search, not both — `gflow docs <topic>` prints one "
                "page, `gflow docs --search <term>` looks across all of them"
            ),
            remediation_hint="Drop one of the two and re-run.",
        )
    if term is not None:
        _emit_search(term, as_json=as_json)
        return
    if topic is not None:
        _emit_page(topic, as_json=as_json)
        return
    _emit_topics(as_json=as_json)


def _emit_topics(*, as_json: bool) -> None:
    found = docs_catalog.topics()
    if as_json:
        json_output.emit(
            {
                "topics": [
                    {
                        "topic": t.slug,
                        "title": t.title,
                        "summary": t.summary,
                        "path": t.repo_path,
                    }
                    for t in found
                ]
            }
        )
        return
    if not found:
        console.print("[yellow]No documentation is bundled with this installation.[/]")
        return
    table = Table(title=f"gflow documentation ({len(found)} topics)")
    table.add_column("topic", style="bold", no_wrap=True, width=_TOPIC_COL)
    table.add_column("what it covers", no_wrap=True, overflow="ellipsis", width=_SUMMARY_COL)
    for entry in found:
        table.add_row(entry.slug, entry.summary or entry.title)
    console.print(table)
    console.print(
        "  Show one: [bold]gflow docs <topic>[/]   Find a line: [bold]gflow docs --search <term>[/]"
    )


def _emit_page(topic: str, *, as_json: bool) -> None:
    entry = docs_catalog.resolve(topic)
    body = docs_catalog.read(entry)
    if as_json:
        json_output.emit(
            {
                "topic": entry.slug,
                "title": entry.title,
                "path": entry.repo_path,
                "content": body,
            }
        )
        return
    # Raw Markdown, not rendered: it pipes into a pager or an editor, and an agent reading
    # stdout wants the source rather than box-drawing characters.
    click.echo(body)


def _emit_search(term: str, *, as_json: bool) -> None:
    hits = docs_catalog.search(term)
    shown, held_back = hits[:MAX_HITS], max(0, len(hits) - MAX_HITS)
    if as_json:
        json_output.emit(
            {
                "term": term,
                "total": len(hits),
                "matches": [_match_json(m) for m in shown],
                "omitted": held_back,
            }
        )
        return
    if not shown:
        # Exit 0: "nothing mentions that" is a true answer to a valid question, not a
        # failure of the command.
        console.print(f"No page mentions [bold]{term}[/].")
        return
    # The TOTAL in the title, not the shown count. They disagreed, and a header reading
    # "20 match(es)" above a footer saying "and 116 more" is the command misreporting
    # itself in the one place a reader checks whether to narrow.
    table = Table(title=f"'{term}' — {len(hits)} match(es), showing {len(shown)}")
    table.add_column("where", style="bold", no_wrap=True)
    table.add_column("line", overflow="fold")
    for match in shown:
        table.add_row(match.position, match.text)
    console.print(table)
    if held_back:
        console.print(f"  … and {held_back} more. Add a second word to narrow the search.")


def _match_json(match: Any) -> dict[str, Any]:
    return {
        "topic": match.topic,
        "path": match.position,
        "line": match.line_no,
        "text": match.text,
        "score": match.score,
    }
