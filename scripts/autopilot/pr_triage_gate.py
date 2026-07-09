#!/usr/bin/env python3
"""Stage 0 Deterministic Pre-filter for external PR triage — NO LLM.

Determines if an open PR on ffroliva/gflow-cli is eligible for automated
review. Excludes owner, bots, drafts, oversized diffs, and obvious prompt
injections, routing results deterministically.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any

# Diff limits
MAX_FILES = 30
MAX_LINES = 1500

# Verdicts
PROCEED = "PROCEED"
SKIP = "SKIP"
DEFERRED_SIZE = "DEFERRED_SIZE"
NEEDS_HUMAN = "NEEDS-HUMAN"

# Fields required by gh command
GH_JSON_FIELDS = (
    "number,author,baseRefName,title,body,state,isDraft,additions,deletions,changedFiles,comments"
)

# Common injection patterns to scan title, body, and comments
INJECTION_REGEXES = [
    re.compile(
        r"ignore\s+(?:previous|all|above|below)\s+(?:instructions|directives|rules|guidelines|prompts|rulesets)",
        re.IGNORECASE,
    ),
    re.compile(r"system\s+instructions\b", re.IGNORECASE),
    re.compile(
        r"you\s+must\s+(?:set|mark|report|write|output|return)\s+(?:the\s+)?verdict\s+(?:is|to|as)\b",
        re.IGNORECASE,
    ),
    re.compile(r"override\s+(?:verdict|status|rules|directives)", re.IGNORECASE),
    re.compile(r"assistant\s+must\s+bypass\b", re.IGNORECASE),
]


def _is_bot(author: dict | None) -> bool:
    if not author:
        return False
    if author.get("is_bot"):
        return True
    login = (author.get("login") or "").lower()
    return "bot" in login or login == "dependabot" or login == "renovate"


def _has_injection(text: str | None) -> bool:
    if not text:
        return False
    return any(rx.search(text) for rx in INJECTION_REGEXES)


def should_review(pr: dict) -> dict:
    """Evaluate PR shape deterministically to produce a triage verdict."""
    num = pr.get("number")
    author = pr.get("author") or {}
    login = author.get("login") or ""

    # Exclude owner and bots
    if login == "ffroliva":
        return {"pr": num, "verdict": SKIP, "reasons": ["author is owner"]}
    if _is_bot(author):
        return {"pr": num, "verdict": SKIP, "reasons": [f"author '{login}' is a bot"]}

    # Exclude drafts and non-open state
    state = (pr.get("state") or "").upper()
    if state != "OPEN":
        return {"pr": num, "verdict": SKIP, "reasons": [f"PR state is not open (state={state})"]}
    if pr.get("isDraft"):
        return {"pr": num, "verdict": SKIP, "reasons": ["PR is a draft"]}

    # Detect incorrect base branch targeting main (request retarget)
    base = pr.get("baseRefName")
    if base == "main":
        return {
            "pr": num,
            "verdict": NEEDS_HUMAN,
            "reasons": ["PR incorrectly targets main branch"],
        }

    # Exclude oversized diffs
    changed_files = pr.get("changedFiles")
    if changed_files is None:
        changed_files = len(pr.get("files") or [])
    additions = pr.get("additions", 0)
    deletions = pr.get("deletions", 0)
    total_lines = additions + deletions

    if changed_files > MAX_FILES:
        return {
            "pr": num,
            "verdict": DEFERRED_SIZE,
            "reasons": [f"diff changed files ({changed_files}) exceeds limit ({MAX_FILES})"],
        }
    if total_lines > MAX_LINES:
        return {
            "pr": num,
            "verdict": DEFERRED_SIZE,
            "reasons": [f"diff lines changed ({total_lines}) exceeds limit ({MAX_LINES})"],
        }

    # Scan title and body for prompt injections
    title = pr.get("title", "")
    body = pr.get("body", "")
    if _has_injection(title):
        return {
            "pr": num,
            "verdict": NEEDS_HUMAN,
            "reasons": ["injection pattern matched in PR title"],
        }
    if _has_injection(body):
        return {
            "pr": num,
            "verdict": NEEDS_HUMAN,
            "reasons": ["injection pattern matched in PR body"],
        }

    # Scan comments for prompt injections
    comments = pr.get("comments") or []
    for comment in comments:
        comment_body = comment.get("body", "")
        if _has_injection(comment_body):
            comment_author = comment.get("author", {}).get("login") or "unknown"
            return {
                "pr": num,
                "verdict": NEEDS_HUMAN,
                "reasons": [f"injection pattern matched in comment by '{comment_author}'"],
            }

    return {"pr": num, "verdict": PROCEED, "reasons": ["all pre-filter checks passed"]}


# --- CLI ---------------------------------------------------------------------
def _gh_json(args: list[str]) -> Any:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def _fetch_open_prs(repo: str) -> list[dict]:
    listed = _gh_json(["pr", "list", "--repo", repo, "--state", "open", "--json", "number"])
    prs: list[dict] = []
    for row in listed:
        pr = _gh_json(["pr", "view", str(row["number"]), "--repo", repo, "--json", GH_JSON_FIELDS])
        prs.append(pr)
    return prs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stage 0 Deterministic Triage Gate (no LLM).")
    ap.add_argument("--repo", default="ffroliva/gflow-cli", help="owner/repo to triage")
    ap.add_argument("--pr", type=int, help="evaluate a single PR number (else all open)")
    ap.add_argument("--from-json", help="read PR JSON from a file instead of calling gh (testing)")
    args = ap.parse_args(argv)

    try:
        if args.from_json:
            with open(args.from_json, encoding="utf-8") as fh:
                data = json.load(fh)
            prs = data if isinstance(data, list) else [data]
        elif args.pr is not None:
            prs = [
                _gh_json(
                    ["pr", "view", str(args.pr), "--repo", args.repo, "--json", GH_JSON_FIELDS]
                )
            ]
        else:
            prs = _fetch_open_prs(args.repo)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"gate config/IO error: {exc}", file=sys.stderr)
        return 2

    verdicts = [should_review(pr) for pr in prs]
    print(json.dumps(verdicts, indent=2))
    return 0 if any(v["verdict"] == PROCEED for v in verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
