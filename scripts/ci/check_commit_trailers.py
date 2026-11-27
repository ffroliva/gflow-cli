"""Fail when a commit in a revision range credits an AI assistant.

The project rule (AGENTS.md, "PR instructions") forbids AI attribution in commit
messages. A local commit-msg hook strips it, but hooks are not versioned and an agent
harness may ask for the trailer, so the rule is enforced here, on every pull request.
A commit authored or committed by an AI identity is refused too: a squash merge turns its
author into a ``Co-authored-by:`` trailer.

The arguments are passed to ``git log``. With none, it reads the commits reachable from
``HEAD`` that are not on ``origin/develop`` or ``upstream/develop``, so a stale fork or a
fresh ``gh pr checkout`` is not blamed for develop's history and another contributor fork's
``develop`` ref never masks unmerged commits. Run manually:

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
    r"|[\w.-]*(claude|copilot|devin|jules|codex|gemini|chatgpt|cursor|factory|coderabbit"
    r"|github-advanced-security|github-code-quality)[\w.-]*\[bot\])$",
    re.IGNORECASE,
)
_ANY_TRAILER = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:\s*(.+)$")
_CREDIT_TRAILER = re.compile(r"^(?:co-authored-by|assisted-by):\s*(.+)$", re.IGNORECASE)
_AI_NAME = re.compile(
    r"^(?:claude(?:\s+(?:code|opus|sonnet|haiku)\b.*)?|copilot|codex|gemini|chatgpt"
    r"|cursor(?:\s+agent)?|devin|jules|aider|opencode|windsurf|amp|droid|coderabbit(?:ai)?)$",
    re.IGNORECASE,
)
# A robot-emoji "Generated with/by" footer from any tool, or the bare Claude Code footer;
# prose such as "Generated with Claude Code (see PR)" passes by requiring a URL in parens.
_FOOTER = re.compile(
    r"^(\U0001f916\uFE0F?\s*generated (with|by) "
    r"|generated with \[?claude code\]?\s*(?:\(https?://|\.?$))",
    re.IGNORECASE,
)
_BLANK_LINE = re.compile(r"(?:\r?\n)[ \t]*(?:\r?\n)+")


def _is_ai_address(email: str) -> bool:
    local, _, domain = email.rpartition("@")
    if _AI_DOMAIN.search(email):
        return True
    return bool(_SHARED_DOMAIN.search(domain) and _BOT_LOGIN.match(local))


def is_ai_email(text: str) -> bool:
    """Return whether ``text`` contains an e-mail address that belongs to an AI assistant."""
    return any(_is_ai_address(e) for e in _EMAIL.findall(text))


def offending_lines(message: str) -> list[str]:
    """Return the lines of ``message``'s trailer block that credit an AI assistant.

    Only the final blank-line-separated paragraph is inspected so quoted example trailers
    in the body pass. Trailer lines with e-mails are judged by e-mail address, while
    ``Co-authored-by:`` / ``Assisted-by:`` trailers without e-mails reject standalone
    assistant names without matching human names such as Claude Dupont or Jules Martin.
    """
    paragraphs = [p for p in _BLANK_LINE.split(message.strip()) if p.strip()]
    if not paragraphs:
        return []
    found = []
    for raw in paragraphs[-1].splitlines():
        line = raw.strip()
        if _FOOTER.match(line):
            found.append(line)
            continue
        if _ANY_TRAILER.match(line) and is_ai_email(line):
            found.append(line)
            continue
        credit = _CREDIT_TRAILER.match(line)
        if credit and "@" not in credit.group(1):
            name = credit.group(1).strip().strip("().")
            if _AI_NAME.match(name):
                found.append(line)
    return found


def _in_git_repo() -> bool:
    return (
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "rev-parse", "--is-inside-work-tree"],  # noqa: S607
            capture_output=True,
            encoding="utf-8",
        ).returncode
        == 0
    )


def _remote_develop_refs() -> list[str]:
    refs = []
    for ref in ("origin/develop", "upstream/develop"):
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],  # noqa: S607
            capture_output=True,
            encoding="utf-8",
        )
        if proc.returncode == 0:
            refs.append(ref)
    return refs


def _git_log(revisions: list[str]) -> str:
    # -z separates commits with NUL, which git never stores in a message.
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "log", "-z", "--format=%H%n%an <%ae>\t%cn <%ce>%n%B", *revisions],  # noqa: S607
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]  # cp1252 consoles
    try:
        if not argv:
            if not _in_git_repo():
                print(
                    "not inside a git repository: pass a revision range or cd into it",
                    file=sys.stderr,
                )
                return 2
            refs = _remote_develop_refs()
            if not refs:
                print(
                    "no origin/develop or upstream/develop branch: git fetch it, or pass a range",
                    file=sys.stderr,
                )
                return 2
            argv = ["HEAD", "--not", *refs]
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
        identities, _, message = rest.partition("\n")
        author, _, committer = identities.partition("\t")
        problems = offending_lines(message)
        if committer and is_ai_email(committer):
            problems.insert(0, f"committer {committer}")
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
