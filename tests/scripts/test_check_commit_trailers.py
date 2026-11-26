"""The AI-attribution gate: no commit in a pull request may credit an AI assistant."""

from __future__ import annotations

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
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
    ],
)
def test_an_ai_attribution_line_is_reported(line: str) -> None:
    assert trailers.offending_lines(f"fix: a thing\n\nbody\n\n{line}\n") == [line]


@pytest.mark.parametrize(
    "message",
    [
        "fix: a thing\n\nSigned-off-by: Jane Doe <jane@example.com>\n",
        "fix: a thing\n\nCo-Authored-By: Jane Doe <jane@example.com>\n",
        "docs: explain the Claude Code plugin install\n",
    ],
)
def test_a_human_trailer_or_a_mention_in_prose_passes(message: str) -> None:
    assert trailers.offending_lines(message) == []


def test_main_reports_each_offending_commit(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    log = "abc123\x00fix: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n\x1e"
    log += "def456\x00chore: y\n\nSigned-off-by: A <a@example.com>\n\x1e"
    monkeypatch.setattr(trailers, "_git_log", lambda rev_range: log)
    assert trailers.main(["origin/develop..HEAD"]) == 1
    out = capsys.readouterr().out
    assert "abc123" in out
    assert "def456" not in out


def test_main_passes_a_clean_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trailers, "_git_log", lambda rev_range: "abc\x00fix: x\n\x1e")
    assert trailers.main(["origin/develop..HEAD"]) == 0
