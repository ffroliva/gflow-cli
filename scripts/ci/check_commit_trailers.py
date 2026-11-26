"""Fail when a commit in a revision range credits an AI assistant.

The project rule (AGENTS.md, "PR instructions") forbids AI attribution in commit
messages. A local commit-msg hook strips it, but hooks are not versioned and an agent
harness may ask for the trailer, so the rule is enforced here, on every pull request.
A commit authored by an AI identity is refused too: a squash merge turns its author into
a ``Co-authored-by:`` trailer.

The arguments are passed to ``git log``. With none, it reads the commits on no remote
``develop`` branch, so a stale fork or a fresh ``gh pr checkout`` is not blamed for
develop's history. Run manually:

    python scripts/ci/check_commit_trailers.py
"""

from __future__ import annotations

import re
import subprocess
import sys

# An e-mail address, without surrounding punctuation such as "(" or ".".
_EMAIL = re.compile(r"[\w.+\[\]-]+@[\w-]+(?:\.[\w-]+)+")
# An AI vendor's own domain (or a subdomain of it), so raider.com and cursor.example.org pass.
_AI_DOMAIN = re.compile(
    r"@([\w-]+\.)*(anthropic\.com|openai\.com|cursor\.com|aider\.chat|ampcode\.com"
    r"|opencode\.ai|codeium\.com|windsurf\.com|factory\.ai)$",
    re.IGNORECASE,
)
# On the shared domains bots use (github.com, google.com), only an exact bot login or a
# "[bot]" account counts, so a human login such as 12345+claudette passes.
_SHARED_DOMAIN = re.compile(r"(^|\.)(github|google)\.com$", re.IGNORECASE)
_BOT_LOGIN = re.compile(
    r"^(\d+\+)?((claude|copilot|gemini|codex|chatgpt)"
    r"|[\w.-]*(claude|copilot|devin|jules|codex|gemini|chatgpt|cursor|factory)[\w.-]*\[bot\])$",
    re.IGNORECASE,
)
_TRAILER = re.compile(r"^co-authored-by:", re.IGNORECASE)
# A robot-emoji "Generated with/by" footer from any tool, or the bare Claude Code footer;
# anchored to its own shape, so a bullet that mentions it in prose passes.
_FOOTER = re.compile(
    r"^(\U0001f916\uFE0F?\s*generated (with|by) "
    r"|generated with \[?claude code\]?\s*(\(|\.?$))",
    re.IGNORECASE,
)


def _is_ai_address(email: str) -> bool:
    local, _, domain = email.rpartition("@")
    if _AI_DOMAIN.search(email):
        return True
    return bool(_SHARED_DOMAIN.search(domain) and _BOT_LOGIN.match(local))


def is_ai_email(text: str) -> bool:
    """Return whether ``text`` contains an e-mail address that belongs to an AI assistant."""
    return any(_is_ai_address(e) for e in _EMAIL.findall(text))


def offending_lines(message: str) -> list[str]:
    """Return the lines of ``message`` that credit an AI assistant.

    Only the e-mail of a ``Co-authored-by:`` trailer is read, never its display name, so
    a human co-author named Claude passes.
    """
    found = []
    for raw in message.splitlines():
        line = raw.strip()
        if (_TRAILER.match(line) and is_ai_email(line)) or _FOOTER.match(line):
            found.append(line)
    return found


def _git_log(revisions: list[str]) -> str:
    # -z separates commits with NUL, which git never stores in a message.
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "log", "-z", "--format=%H%n%an <%ae>%n%B", *revisions],  # noqa: S607
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]  # cp1252 consoles
    argv = argv or ["HEAD", "--not", "--remotes=*/develop"]
    try:
        log = _git_log(argv)
    except subprocess.CalledProcessError as exc:
        print(f"git log {' '.join(argv)} failed: {exc.stderr.strip()}", file=sys.stderr)
        return 2
    except OSError as exc:  # git not installed
        print(f"cannot run git: {exc}", file=sys.stderr)
        return 2
    failed = False
    for record in log.split("\x00"):
        sha, _, rest = record.strip().partition("\n")
        author, _, message = rest.partition("\n")
        problems = offending_lines(message)
        if is_ai_email(author):
            problems.insert(0, f"author {author}")
        for line in problems:
            failed = True
            print(f"{sha[:12]}: AI attribution is not allowed: {line}")
    if failed:
        print(
            "Reword these commits on the branch (git rebase; git commit --amend --reset-author "
            "for an AI author). AGENTS.md forbids AI attribution in commit messages, even "
            "when an agent harness asks for it."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
