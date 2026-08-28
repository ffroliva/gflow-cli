"""Offline listings render account-correct editor URLs (#587).

`project list` / `project show` / MCP emitted no URL at all: they are
network-free catalog reads, so they could not resolve a locale and correctly
declined to guess one. With the locale cached per profile the value is readable
offline. Unknown locale still yields the BARE url, never a guessed `en`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli import profile_store
from gflow_cli.cli import main
from gflow_cli.data.models import ProjectRecord
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore

PID = "2ddc3a33-97db-41a0-a0d3-7f9488b0d5a9"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    monkeypatch.delenv("GFLOW_CLI_PROFILE", raising=False)
    reset_settings()
    db = tmp_path / "gflow.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    reset_settings()
    with DataStore.open(db) as store:
        repo = DataRepository(store)
        repo.upsert_profile("denon82", tmp_path / "profile_denon82")
        repo.upsert_project(
            ProjectRecord(
                id="rec-1",
                profile_name="denon82",
                flow_project_id=PID,
                title="A project",
                source="gflow-cli",
            )
        )
    yield tmp_path
    reset_settings()


def _pdir(home: Path) -> Path:
    d = home / "profile_denon82"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _list_json(runner: CliRunner) -> dict:
    res = runner.invoke(main, ["project", "list", "--json"])
    assert res.exit_code == 0, res.output
    return json.loads(res.output)


def test_cached_locale_produces_an_account_correct_url(home: Path) -> None:
    profile_store.write_account_locale(_pdir(home), "pt")

    payload = _list_json(CliRunner())

    assert payload["projects"][0]["url"] == (f"https://labs.google/fx/pt/tools/flow/project/{PID}")


def test_unknown_locale_yields_a_bare_url_not_a_guessed_en(home: Path) -> None:
    """No cache entry => no segment. A guessed `en` is the original defect."""
    _pdir(home)

    payload = _list_json(CliRunner())

    url = payload["projects"][0]["url"]
    assert url == f"https://labs.google/fx/tools/flow/project/{PID}"
    assert "/fx/en/" not in url


def test_no_redirect_account_also_yields_a_bare_url(home: Path) -> None:
    """`""` means "probed, Flow does not redirect" — still no segment to add."""
    profile_store.write_account_locale(_pdir(home), None)

    payload = _list_json(CliRunner())

    assert payload["projects"][0]["url"] == (f"https://labs.google/fx/tools/flow/project/{PID}")


def test_project_show_reports_the_same_url(home: Path) -> None:
    profile_store.write_account_locale(_pdir(home), "pt")

    res = CliRunner().invoke(main, ["project", "show", PID, "--json"])

    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["project"]["url"] == (
        f"https://labs.google/fx/pt/tools/flow/project/{PID}"
    )


def test_account_locale_for_is_none_when_the_profile_dir_is_absent(home: Path) -> None:
    """A catalog row can outlive its profile dir; that must not raise."""
    assert profile_store.account_locale_for("deleted-profile") is None


# --- MCP `gflow_list_projects` carries the same URL (had zero coverage) ------


@pytest.mark.parametrize(
    ("cached", "expected_segment"),
    [("pt", "/fx/pt/"), (None, "/fx/tools/")],
    ids=["segment", "no-redirect"],
)
def test_mcp_list_projects_emits_the_url(
    home: Path, cached: str | None, expected_segment: str
) -> None:
    """The MCP listing inherits whatever `project list` does — pin it directly."""
    import asyncio

    from gflow_cli.mcp.tools import gflow_list_projects

    profile_store.write_account_locale(_pdir(home), cached)

    payload = asyncio.run(gflow_list_projects(profile="denon82", limit=10))

    assert payload["status"] == "ok"
    url = payload["projects"][0]["url"]
    assert url.endswith(PID)
    assert expected_segment in url
    assert "/fx/en/" not in url


# --- the table render must survive a hostile project id ---------------------


@pytest.mark.parametrize(
    "pid",
    ["550e8400-e29b-41d4-a716-446655440000", "ab link https://evil.example"],
    ids=["links", "rejected"],
)
def test_projects_table_survives_a_hostile_id(home: Path, pid: str) -> None:
    """Rendering must never raise, and any link emitted must be a Flow URL.

    Two parsers were tried and both were wrong: `[link=...]` markup ends at the
    first `]` in the URL (MarkupError), and a `f"link {url}"` style STRING ends
    at the first space, letting the rest of an id become style tokens.

    The "rejected" case pins the choice: with the allowlist refusing that id the
    URL is None, and a style STRING renders `link None` as a literal link target
    "None" — mutation-verified. The allowlist itself is pinned separately below.
    """
    import io
    import re
    from datetime import UTC, datetime

    from rich.console import Console

    from gflow_cli import cli_data
    from gflow_cli.data.queries import ProjectRow

    buf = io.StringIO()
    fake = Console(file=buf, force_terminal=True, width=250, legacy_windows=False)
    original = cli_data.Console
    cli_data.Console = lambda *a, **k: fake  # type: ignore[assignment,misc]
    try:
        cli_data._emit_projects_table(
            [
                ProjectRow(
                    project_id=pid,
                    profile="denon82",
                    created_at=datetime.now(UTC),
                    image_count=0,
                    video_count=0,
                    title="T",
                )
            ]
        )
    finally:
        cli_data.Console = original  # type: ignore[assignment]

    osc8 = re.compile(r"\x1b\]8;[^;]*;([^\x1b\a]*)")
    for target in osc8.findall(buf.getvalue()):
        assert target == "" or target.startswith("https://labs.google/fx/"), target


# --- the id allowlist is a security control; pin it directly ----------------


@pytest.mark.parametrize(
    "pid",
    [
        "ab link https://evil.example",
        "../../etc/passwd",
        "a b",
        "x\ty",
        "x\ny",
        "\x1b]0;pwned\a",
        "",
        "a" * 200,
    ],
    ids=["space-hijack", "traversal", "space", "tab", "newline", "ansi", "empty", "too-long"],
)
def test_editor_url_refuses_a_malformed_project_id(pid: str) -> None:
    """`Style(link=...)` neutralises the hijack; this is the layer beneath it.

    Mutation testing found the allowlist could be deleted with the whole suite
    still green -- defence in depth that nothing pinned. The strict builder feeds
    navigation, so it refuses; the tolerant one feeds listings, so it degrades.
    """
    from gflow_cli.api import routes

    with pytest.raises(ValueError, match="Invalid project_id"):
        routes.project_editor_url("pt", pid)

    assert routes.project_editor_url_or_none("pt", pid) is None


def test_editor_url_accepts_a_real_flow_id() -> None:
    from gflow_cli.api import routes

    assert routes.project_editor_url("pt", PID) == (
        f"https://labs.google/fx/pt/tools/flow/project/{PID}"
    )
    assert routes.project_editor_url_or_none(None, PID) == (
        f"https://labs.google/fx/tools/flow/project/{PID}"
    )
