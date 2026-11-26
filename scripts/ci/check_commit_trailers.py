"""Fail when a commit in a revision range credits an AI assistant.

The project rule (AGENTS.md, "PR instructions") forbids AI attribution in commit
messages. A local commit-msg hook strips it, but hooks are not versioned and an agent
harness may ask for the trailer, so the rule is enforced here, on every pull request.

Run manually:

    python scripts/ci/check_commit_trailers.py origin/develop..HEAD
"""

from __future__ import annotations

import re
import subprocess
import sys

_AI = r"(claude|anthropic|codex|openai|chatgpt|gpt-|gemini|copilot)"
_PATTERNS = (
    re.compile(rf"^co-authored-by:.*{_AI}", re.IGNORECASE),
    re.compile(r"generated with \[?claude code", re.IGNORECASE),
)


def offending_lines(message: str) -> list[str]:
    """Return the lines of ``message`` that credit an AI assistant."""
    return [
        line.strip()
        for line in message.splitlines()
        if any(p.search(line.strip()) for p in _PATTERNS)
    ]


def _git_log(rev_range: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "log", "--format=%H%x00%B%x1e", rev_range],  # noqa: S607 - git on PATH
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: check_commit_trailers.py <rev-range>", file=sys.stderr)
        return 2
    failed = False
    for record in _git_log(argv[0]).split("\x1e"):
        sha, _, message = record.strip().partition("\x00")
        for line in offending_lines(message):
            failed = True
            print(f"{sha[:12]}: AI attribution is not allowed: {line}")
    if failed:
        print(
            "Remove these lines (git rebase, or squash at merge). AGENTS.md forbids AI "
            "attribution in commit messages, even when an agent harness asks for it."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
