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

# Match the e-mail, never the display name, so a human co-author named Claude passes:
# an AI vendor's domain, or a bot's local part on a shared domain (github.com, google.com).
_AI_DOMAIN = r"[^@>]*@[^>]*(anthropic|openai|cursor|aider)\."
_AI_LOCAL = r"[^@>]*(copilot|devin-ai|jules|codex|gemini|chatgpt)"
_PATTERNS = (
    re.compile(rf"^co-authored-by:[^<]*<({_AI_DOMAIN}|{_AI_LOCAL})", re.IGNORECASE),
    # Anchored, so a subject such as "fix: strip Generated with Claude Code" passes.
    re.compile(r"^\W*generated with \[?claude code", re.IGNORECASE),
)


def offending_lines(message: str) -> list[str]:
    """Return the lines of ``message`` that credit an AI assistant."""
    return [
        line.strip()
        for line in message.splitlines()
        if any(p.search(line.strip()) for p in _PATTERNS)
    ]


def _git_log(rev_range: str) -> str:
    # -z separates commits with NUL, which git never stores in a message.
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "log", "-z", "--format=%H%n%B", rev_range],  # noqa: S607 - git on PATH
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]  # cp1252 consoles
    if len(argv) != 1:
        print("usage: check_commit_trailers.py <rev-range>", file=sys.stderr)
        return 2
    try:
        log = _git_log(argv[0])
    except subprocess.CalledProcessError as exc:
        print(f"git log {argv[0]} failed: {exc.stderr.strip()}", file=sys.stderr)
        return 2
    failed = False
    for record in log.split("\x00"):
        sha, _, message = record.strip().partition("\n")
        for line in offending_lines(message):
            failed = True
            print(f"{sha[:12]}: AI attribution is not allowed: {line}")
    if failed:
        print(
            "Reword these commits on the feature branch (git rebase). AGENTS.md forbids AI "
            "attribution in commit messages, even when an agent harness asks for it."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
