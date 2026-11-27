"""The AI-attribution gate: no commit in a pull request may credit an AI assistant."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.ci import check_commit_trailers as trailers


@pytest.mark.parametrize(
    "line",
    [
        "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>",
        "co-authored-by: claude <noreply@anthropic.com>",
        "Co-authored-by: Codex <codex@openai.com>",
        "Co-Authored-By: Gemini <gemini@google.com>",
        "Co-authored-by: Copilot <copilot@github.com>",
        "Co-authored-by: Cursor Agent <cursoragent@cursor.com>",
        "Co-authored-by: Devin <1+devin-ai-integration[bot]@users.noreply.github.com>",
        "Generated with Claude Code",
        "\U0001f916️ Generated with [Claude Code](https://claude.com/claude-code)",
        "Co-authored-by: Jules <1+google-labs-jules[bot]@users.noreply.github.com>",
        "Co-authored-by: aider (gpt-4o) <noreply@aider.chat>",
        "Co-authored-by: claude[bot] <1+claude[bot]@users.noreply.github.com>",
        "Co-Authored-By: Claude noreply@anthropic.com",
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
        "🤖 Generated with [opencode](https://opencode.ai)",
        "Generated with Claude Code.",
        "Co-Authored-By: Claude (noreply@anthropic.com)",
        "Co-authored-by: Amp <amp@ampcode.com>",
        "Co-Authored-By: opencode <noreply@opencode.ai>",
        "Co-authored-by: Windsurf <windsurf@codeium.com>",
        "Co-authored-by: Cursor <1+cursor[bot]@users.noreply.github.com>",
        "Co-authored-by: Droid <1+factory-droid[bot]@users.noreply.github.com>",
        "Co-authored-by: coderabbitai[bot] <1+coderabbitai[bot]@users.noreply.github.com>",
        "Co-authored-by: Autofix <1+github-advanced-security[bot]@users.noreply.github.com>",
        "Assisted-by: Claude <noreply@anthropic.com>",
        "Co-authored-by: Claude",
        "Assisted-by: Copilot",
        "Co-authored-by: Cursor Agent",
        "Generated with Claude Code (https://claude.com/claude-code)",
    ],
)
def test_an_ai_attribution_line_is_reported(line: str) -> None:
    assert trailers.offending_lines(f"fix: a thing\n\nbody\n\n{line}\n") == [line]


@pytest.mark.parametrize(
    "message",
    [
        "fix: a thing\n\nSigned-off-by: Jane Doe <jane@example.com>\n",
        "fix: a thing\n\nCo-Authored-By: Jane Doe <jane@example.com>\n",
        "fix: a thing\n\nCo-authored-by: Claude Dupont <claude.dupont@example.fr>\n",
        "fix: a thing\n\nCo-authored-by: Jules Martin <jules@example.fr>\n",
        "fix: a thing\n\nCo-authored-by: Claude Dupont\n",
        "fix: a thing\n\nCo-authored-by: Jane Roe <jane@raider.com>\n",
        "fix: a thing\n\nCo-authored-by: Ann <ann@philanthropic.org>\n",
        "fix: a thing\n\nCo-authored-by: Claudette <12345+claudette@users.noreply.github.com>\n",
        "fix: a thing\n\nCo-authored-by: Fan <9+gemini-fan@users.noreply.github.com>\n",
        "fix: a thing\n\nCo-authored-by: Claude Dupont <claude.dupont@google.com>\n",
        "fix: a thing\n\nCo-authored-by: Jules <148514960+julesverne@users.noreply.github.com>\n",
        "fix: a thing\n\nCo-authored-by: Jane <jane@cursor.example.org>\n",
        "docs: x\n\n- Generated with Claude Code footers are stripped\n",
        "docs: explain the Claude Code plugin install\n",
        "fix: strip Generated with Claude Code from PR bodies\n",
        "fix: x\n\nGenerated with Claude Code (the CLI) triggered this edge case.\n",
        (
            "docs: quote example\n\n"
            "Co-authored-by: Claude <noreply@anthropic.com>\n\n"
            "Signed-off-by: Jane Doe <jane@example.com>\n"
        ),
    ],
)
def test_a_human_trailer_or_a_mention_in_prose_passes(message: str) -> None:
    assert trailers.offending_lines(message) == []


def test_main_reports_each_offending_commit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log = "abc123\nA <a@example.com>\nfix: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
    log += "\x00def456\nA <a@example.com>\nchore: y\n\nSigned-off-by: A <a@example.com>\n"
    monkeypatch.setattr(trailers, "_git_log", lambda revisions: log)
    assert trailers.main(["HEAD", "^origin/develop"]) == 1
    out = capsys.readouterr().out
    assert "abc123" in out
    assert "def456" not in out


def test_main_passes_a_clean_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trailers, "_git_log", lambda revisions: "abc\nA <a@example.com>\nfix: x\n")
    assert trailers.main(["origin/develop..HEAD"]) == 0


def test_main_reports_an_ai_author(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log = "abc123\nClaude <noreply@anthropic.com>\nfix: x\n"
    monkeypatch.setattr(trailers, "_git_log", lambda revisions: log)
    assert trailers.main(["HEAD"]) == 1
    assert "author Claude <noreply@anthropic.com>" in capsys.readouterr().out


def test_main_without_arguments_reads_commits_on_no_remote_develop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    def fake_log(revisions: list[str]) -> str:
        seen.append(revisions)
        return ""

    monkeypatch.setattr(trailers, "_in_git_repo", lambda: True)
    monkeypatch.setattr(trailers, "_git_log", fake_log)
    monkeypatch.setattr(trailers, "_remote_develop_refs", lambda: ["origin/develop"])
    assert trailers.main([]) == 0
    assert seen == [["HEAD", "--not", "origin/develop"]]


def test_main_without_arguments_refuses_when_no_remote_develop_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(trailers, "_in_git_repo", lambda: True)
    monkeypatch.setattr(trailers, "_remote_develop_refs", list)
    monkeypatch.setattr(trailers, "_git_log", lambda revisions: pytest.fail("read history"))
    assert trailers.main([]) == 2


def test_main_without_arguments_reports_outside_git_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert trailers.main([]) == 2
    assert "not inside a git repository" in capsys.readouterr().err


def test_main_reports_an_ai_committer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log = "abc123\nJane Doe <jane@example.com>\tClaude <noreply@anthropic.com>\nfix: x\n"
    monkeypatch.setattr(trailers, "_git_log", lambda revisions: log)
    assert trailers.main(["HEAD"]) == 1
    assert "committer Claude <noreply@anthropic.com>" in capsys.readouterr().out


def test_main_runs_against_real_git_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def git(*args: str, extra_env: dict[str, str] | None = None) -> None:
        env = None
        if extra_env:
            import os

            env = {**os.environ, **extra_env}
        subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, env=env, capture_output=True
        )

    git("init")
    git("config", "user.name", "Jane Doe")
    git("config", "user.email", "jane@example.com")
    (tmp_path / "f.txt").write_text("base\n", encoding="utf-8")
    git("add", "f.txt")
    git("commit", "-m", "chore: base\n\nCo-authored-by: Claude <noreply@anthropic.com>")
    git("update-ref", "refs/remotes/origin/develop", "HEAD")

    (tmp_path / "f.txt").write_text("second\n", encoding="utf-8")
    git("commit", "-am", "fix: good commit")
    monkeypatch.chdir(tmp_path)
    assert trailers.main([]) == 0

    (tmp_path / "f.txt").write_text("third\n", encoding="utf-8")
    git(
        "commit",
        "-am",
        "fix: bad commit",
        extra_env={"GIT_COMMITTER_NAME": "Claude", "GIT_COMMITTER_EMAIL": "noreply@anthropic.com"},
    )
    assert trailers.main([]) == 1
    assert "committer Claude <noreply@anthropic.com>" in capsys.readouterr().out


def test_main_reports_a_missing_git(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_git(revisions: list[str]) -> str:
        raise FileNotFoundError("git")

    monkeypatch.setattr(trailers, "_git_log", no_git)
    assert trailers.main(["HEAD"]) == 2


def test_main_reports_a_bad_range_without_a_traceback() -> None:
    assert trailers.main(["no-such-ref..HEAD"]) == 2
