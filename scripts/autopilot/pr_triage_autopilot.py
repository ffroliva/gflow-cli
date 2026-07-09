#!/usr/bin/env python3
"""Main Host Orchestrator for the PR-Triage Autopilot.

Polls open PRs, applies Stage 0 pre-filter, checks/updates audit ledger,
fetches PR branches, runs sandboxed container reviews, posts comments to GitHub,
and sends Telegram notifications.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
from pathlib import Path

import httpx
import structlog

# Initialize structured logger
logger = structlog.get_logger()

# Configurable defaults
DAILY_CAP = 5
MAX_RETRIS = 3
LEDGER_FILE = "pr_triage_ledger.jsonl"
LOCK_FILE_PATH = "/tmp/pr_triage_autopilot.lock"

# Imports for cross-platform file locking
try:
    import fcntl

    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False
    try:
        import msvcrt

        HAS_MSVCRT = True
    except ImportError:
        HAS_MSVCRT = False


def acquire_lock(lock_file):
    """Attempt non-blocking lock acquisition."""
    if HAS_FCNTL:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif HAS_MSVCRT:
        # locking requires lock size in bytes. We lock the first byte.
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)


def send_telegram_alert(text: str) -> None:
    """Send alert message to the configured Telegram user."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    user_id = os.environ.get("TELEGRAM_USER_ID")
    if not bot_token or not user_id:
        logger.warning("Telegram configuration missing. Skipping notification.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = httpx.post(url, json={"chat_id": user_id, "text": text}, timeout=10)
        if resp.status_code != 200:
            logger.error(
                "Telegram API returned error", status_code=resp.status_code, body=resp.text
            )
    except Exception as exc:
        logger.error("Failed to send Telegram alert", error=str(exc))


def _gh_json(args: list[str], repo: str) -> any:
    proc = subprocess.run(
        ["gh", *args, "--repo", repo], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def post_gh_comment(pr_num: int, body: str, repo: str) -> None:
    """Post comment back to PR using GitHub CLI."""
    proc = subprocess.run(
        ["gh", "pr", "comment", str(pr_num), "--body", body, "--repo", repo],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to post comment to PR #{pr_num}: {proc.stderr.strip()}")


def get_ledger_entries(ledger_path: Path) -> list[dict]:
    """Parse all entries from the JSONL ledger file."""
    if not ledger_path.exists():
        return []
    entries = []
    with open(ledger_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def append_ledger_entry(ledger_path: Path, entry: dict) -> None:
    """Append a new entry atomically to the JSONL ledger."""
    entry["timestamp"] = datetime.datetime.now(datetime.UTC).isoformat()
    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def get_pr_failures_count(entries: list[dict], pr_num: int, head_sha: str) -> int:
    """Count how many failed attempts have been recorded for the current PR and SHA."""
    count = 0
    for e in entries:
        if e.get("pr") == pr_num and e.get("head_sha") == head_sha:
            if e.get("status") == "FAILED":
                count += 1
            elif e.get("status") == "FAILED_PERMANENT":
                return MAX_RETRIS  # Mark as maxed out immediately
    return count


def check_daily_review_count(entries: list[dict]) -> int:
    """Count completed reviews in the last 24 hours UTC."""
    today = datetime.datetime.now(datetime.UTC).date()
    count = 0
    for e in entries:
        if e.get("status") == "COMPLETED":
            ts_str = e.get("timestamp")
            if ts_str:
                dt = datetime.datetime.fromisoformat(ts_str)
                if dt.date() == today:
                    count += 1
    return count


def fetch_and_checkout_pr(pr_num: int, repo_dir: Path) -> str:
    """Fetch the PR branch, checkout, and return the head SHA."""
    # Fetch
    ref = f"pull/{pr_num}/head:pr-{pr_num}-review"
    logger.info("Fetching PR branch", pr=pr_num, ref=ref)
    subprocess.run(
        ["git", "fetch", "-f", "origin", ref], cwd=repo_dir, capture_output=True, check=True
    )

    # Get head SHA
    sha_proc = subprocess.run(
        ["git", "rev-parse", f"pr-{pr_num}-review"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    head_sha = sha_proc.stdout.strip()

    # Checkout
    logger.info("Checking out PR branch", sha=head_sha)
    subprocess.run(
        ["git", "checkout", f"pr-{pr_num}-review"], cwd=repo_dir, capture_output=True, check=True
    )
    return head_sha


def restore_repo_branch(repo_dir: Path) -> None:
    """Restore host clone repository back to develop branch."""
    logger.info("Restoring repository to develop branch")
    subprocess.run(["git", "checkout", "develop"], cwd=repo_dir, capture_output=True, check=True)


def run_docker_sandbox(
    pr_num: int, repo_dir: Path, memory_dir: Path, anthropic_key: str, gh_token: str
) -> str:
    """Invoke the run_sandboxed_review.sh wrapper script and capture its stdout."""
    script_path = repo_dir / "scripts" / "autopilot" / "run_sandboxed_review.sh"

    cmd = [
        "bash",
        str(script_path),
        "--pr",
        str(pr_num),
        "--repo",
        str(repo_dir),
        "--memory",
        str(memory_dir),
        "--token",
        gh_token,
        "--key",
        anthropic_key,
    ]

    logger.info("Running sandboxed Docker review", pr=pr_num)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Docker sandbox failed (exit {proc.returncode}): {proc.stderr.strip()}\n{proc.stdout}"
        )
    return proc.stdout


def parse_summary_verdict(container_output: str) -> tuple[str | None, int]:
    """Parse SUMMARY_VERDICT and MUST_FIX_COUNT from stdout."""
    verdict = None
    must_fixes = 0
    for line in container_output.splitlines():
        if "SUMMARY_VERDICT:" in line:
            # Parse line e.g.: SUMMARY_VERDICT: YELLOW | MUST_FIX_COUNT: 5 | PR_URL: ...
            parts = [p.strip() for p in line.split("|")]
            for p in parts:
                if p.startswith("SUMMARY_VERDICT:"):
                    verdict = p.split(":")[1].strip()
                elif p.startswith("MUST_FIX_COUNT:"):
                    try:
                        must_fixes = int(p.split(":")[1].strip())
                    except ValueError:
                        pass
    return verdict, must_fixes


def run_triage_cycle(
    repo: str,
    repo_dir: Path,
    memory_dir: Path,
    ledger_path: Path,
    anthropic_key: str,
    gh_token: str,
) -> None:
    """Execute the full orchestrator polling and review cycle."""
    # 1. Fetch list of open PRs
    from pr_triage_gate import GH_JSON_FIELDS, should_review

    logger.info("Polling open PRs via GitHub CLI", repo=repo)
    try:
        prs = _gh_json(["pr", "list", "--state", "open", "--json", GH_JSON_FIELDS], repo)
    except Exception as exc:
        logger.error("Failed to fetch open PRs", error=str(exc))
        return

    # Load ledger entries
    ledger_entries = get_ledger_entries(ledger_path)

    # Check daily cap
    daily_count = check_daily_review_count(ledger_entries)
    if daily_count >= DAILY_CAP:
        logger.warning(
            "Daily review cap reached. Skipping further reviews.",
            daily_count=daily_count,
            cap=DAILY_CAP,
        )
        return

    for pr in prs:
        pr_num = pr.get("number")

        # 2. Run Stage 0 Gate
        gate_res = should_review(pr)
        verdict = gate_res["verdict"]

        if verdict == "SKIP":
            logger.info("PR Stage 0 skipped", pr=pr_num, reason=gate_res["reasons"])
            continue

        if verdict == "DEFERRED_SIZE":
            logger.warning(
                "PR deferred due to oversized diff", pr=pr_num, reason=gate_res["reasons"]
            )
            append_ledger_entry(
                ledger_path,
                {
                    "pr": pr_num,
                    "head_sha": "",
                    "status": "DEFERRED_SIZE",
                    "reasons": gate_res["reasons"],
                },
            )
            send_telegram_alert(f"⚠️ PR #{pr_num} deferred: diff size too large.")
            continue

        if verdict == "NEEDS-HUMAN":
            logger.warning(
                "PR flagged for human triage in Stage 0", pr=pr_num, reason=gate_res["reasons"]
            )
            reasons_str = ", ".join(gate_res["reasons"])
            comment_body = (
                "🤖 **PR-Triage Autopilot Gate**\n\n"
                "This PR has been flagged for human review in Stage 0.\n"
                f"Reason: {reasons_str}"
            )
            try:
                post_gh_comment(pr_num, comment_body, repo)
            except Exception as exc:
                logger.error("Failed to post comment to flagged PR", pr=pr_num, error=str(exc))

            append_ledger_entry(
                ledger_path,
                {
                    "pr": pr_num,
                    "head_sha": "",
                    "status": "NEEDS-HUMAN",
                    "reasons": gate_res["reasons"],
                },
            )
            send_telegram_alert(f"🚨 PR #{pr_num} flagged for human triage: {gate_res['reasons']}")
            continue

        # PROCEED: PR is eligible
        logger.info("PR qualified for review", pr=pr_num)

        # 3. Fetch branch & check ledger state
        head_sha = ""
        try:
            head_sha = fetch_and_checkout_pr(pr_num, repo_dir)
        except Exception as exc:
            logger.error("Failed to checkout PR branch", pr=pr_num, error=str(exc))
            continue

        try:
            # Check if already successfully reviewed
            already_reviewed = False
            for e in ledger_entries:
                if e.get("pr") == pr_num and e.get("head_sha") == head_sha:
                    if e.get("status") in ("COMPLETED", "FAILED_PERMANENT"):
                        already_reviewed = True
                        break
            if already_reviewed:
                logger.info("PR and SHA already triaged. Skipping.", pr=pr_num, sha=head_sha)
                continue

            # Verify fail limits
            failures = get_pr_failures_count(ledger_entries, pr_num, head_sha)
            if failures >= MAX_RETRIS:
                logger.warning(
                    "PR has hit maximum failures cap. Skipping.", pr=pr_num, sha=head_sha
                )
                continue

            # Run Stage 1 Pre-eval & Full sandboxed review
            output = run_docker_sandbox(pr_num, repo_dir, memory_dir, anthropic_key, gh_token)

            # Parse verdict & MUST-FIX count
            parsed_verdict, must_fixes = parse_summary_verdict(output)

            # Post review output
            comment_body = f"🤖 **PR-Triage Autopilot Verdict: {parsed_verdict or 'UNKNOWN'}**\n\n"
            if must_fixes > 0:
                comment_body += (
                    f"⚠️ Found **{must_fixes}** MUST-FIX issue(s). "
                    "Please review and resolve them before merge.\n\n"
                )
            else:
                comment_body += "🟢 All checks passed! No must-fix items identified.\n\n"

            comment_body += (
                "<details>\n<summary>View Sandboxed Review Output</summary>\n\n"
                f"{output}\n</details>"
            )

            post_gh_comment(pr_num, comment_body, repo)

            # Record completed in ledger
            append_ledger_entry(
                ledger_path,
                {
                    "pr": pr_num,
                    "head_sha": head_sha,
                    "status": "COMPLETED",
                    "verdict": parsed_verdict,
                    "must_fixes": must_fixes,
                },
            )

            send_telegram_alert(
                f"✅ Auto-reviewed PR #{pr_num} ({parsed_verdict}): {must_fixes} must-fixes."
            )

        except Exception as exc:
            failures = get_pr_failures_count(ledger_entries, pr_num, head_sha) + 1
            status = "FAILED_PERMANENT" if failures >= MAX_RETRIS else "FAILED"

            logger.error(
                "PR triage cycle run failed",
                pr=pr_num,
                sha=head_sha,
                attempt=failures,
                status=status,
                error=str(exc),
            )

            append_ledger_entry(
                ledger_path,
                {
                    "pr": pr_num,
                    "head_sha": head_sha,
                    "status": status,
                    "fail_count": failures,
                    "error": str(exc),
                },
            )

            if status == "FAILED_PERMANENT":
                send_telegram_alert(
                    f"❌ PR #{pr_num} review failed permanently after {MAX_RETRIS} retries: {exc}"
                )
            else:
                send_telegram_alert(f"⚠️ PR #{pr_num} review attempt {failures} failed: {exc}")

        finally:
            restore_repo_branch(repo_dir)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Main Host Orchestrator for PR-Triage Autopilot.")
    ap.add_argument("--repo", default="ffroliva/gflow-cli", help="owner/repo name")
    ap.add_argument("--repo-dir", required=True, help="path to local host clone")
    ap.add_argument("--memory-dir", required=True, help="path to project-specific memory directory")
    args = ap.parse_args(argv)

    # Validate required credentials
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    gh_token = os.environ.get("GH_COMMENT_TOKEN") or os.environ.get("GITHUB_TOKEN")

    if not anthropic_key or not gh_token:
        logger.error("Missing credentials. Requires ANTHROPIC_API_KEY and GH_COMMENT_TOKEN.")
        return 1

    repo_dir = Path(args.repo_dir).resolve()
    memory_dir = Path(args.memory_dir).resolve()
    ledger_path = repo_dir / LEDGER_FILE

    # Acquire lock
    try:
        lock_file = open(LOCK_FILE_PATH, "w")
        acquire_lock(lock_file)
    except OSError:
        logger.info("Another autopilot instance is running. Exiting.")
        return 0

    logger.info("PR-Triage Autopilot iteration started", repo=args.repo)
    run_triage_cycle(args.repo, repo_dir, memory_dir, ledger_path, anthropic_key, gh_token)
    logger.info("PR-Triage Autopilot iteration completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
