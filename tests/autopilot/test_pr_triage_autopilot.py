from __future__ import annotations

import datetime
import sys
from pathlib import Path
from unittest.mock import patch

# Ensure scripts/autopilot is in path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "autopilot"))

import pr_triage_autopilot  # noqa: E402


def test_parse_summary_verdict():
    output = (
        "Some random logs from claude CLI...\n"
        "SUMMARY_VERDICT: YELLOW | MUST_FIX_COUNT: 4 | PR_URL: https://github.com/org/repo/pull/123\n"
        "Another log line"
    )
    verdict, count = pr_triage_autopilot.parse_summary_verdict(output)
    assert verdict == "YELLOW"
    assert count == 4


def test_get_pr_failures_count():
    entries = [
        {"pr": 101, "head_sha": "sha1", "status": "FAILED"},
        {"pr": 101, "head_sha": "sha1", "status": "FAILED"},
        {"pr": 102, "head_sha": "sha2", "status": "COMPLETED"},
        {"pr": 103, "head_sha": "sha3", "status": "FAILED_PERMANENT"},
    ]

    # 2 failures recorded for pr 101
    assert pr_triage_autopilot.get_pr_failures_count(entries, 101, "sha1") == 2

    # 0 failures for pr 102 (completed)
    assert pr_triage_autopilot.get_pr_failures_count(entries, 102, "sha2") == 0

    # max retries immediately for FAILED_PERMANENT
    assert pr_triage_autopilot.get_pr_failures_count(entries, 103, "sha3") >= 3


def test_check_daily_review_count():
    today = datetime.datetime.now(datetime.UTC).isoformat()
    yesterday = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
    ).isoformat()

    entries = [
        {"timestamp": today, "status": "COMPLETED"},
        {"timestamp": today, "status": "COMPLETED"},
        {"timestamp": yesterday, "status": "COMPLETED"},
        {"timestamp": today, "status": "FAILED"},  # Not completed
    ]

    assert pr_triage_autopilot.check_daily_review_count(entries) == 2


@patch("pr_triage_autopilot.send_telegram_alert")
@patch("pr_triage_autopilot.post_gh_comment")
@patch("pr_triage_autopilot.run_docker_sandbox")
@patch("pr_triage_autopilot.fetch_and_checkout_pr")
@patch("pr_triage_autopilot.restore_repo_branch")
@patch("pr_triage_autopilot._gh_json")
@patch("pr_triage_autopilot.get_ledger_entries")
@patch("pr_triage_autopilot.append_ledger_entry")
def test_run_triage_cycle_success(
    mock_append_ledger,
    mock_get_ledger,
    mock_gh_json,
    mock_restore,
    mock_fetch,
    mock_sandbox,
    mock_post_comment,
    mock_telegram,
    tmp_path,
):
    # Mock Open PRs
    mock_gh_json.return_value = [
        {
            "number": 101,
            "author": {"login": "external-contributor", "is_bot": False},
            "baseRefName": "develop",
            "title": "fix: resolve selector drift",
            "body": "Fixing drift.",
            "state": "OPEN",
            "isDraft": False,
            "additions": 10,
            "deletions": 2,
            "changedFiles": 1,
            "comments": [],
        }
    ]

    # Mock Ledger
    mock_get_ledger.return_value = []

    # Mock Fetch
    mock_fetch.return_value = "sha-abc-123"

    # Mock Sandbox execution output
    mock_sandbox.return_value = (
        "SUMMARY_VERDICT: GREEN | MUST_FIX_COUNT: 0 | PR_URL: https://github.com/org/repo/pull/101"
    )

    repo_dir = tmp_path / "repo"
    memory_dir = tmp_path / "memory"
    ledger_path = tmp_path / "ledger.jsonl"

    pr_triage_autopilot.run_triage_cycle(
        repo="owner/repo",
        repo_dir=repo_dir,
        memory_dir=memory_dir,
        ledger_path=ledger_path,
        anthropic_key="key-test",
        gh_token="token-test",
    )

    # Assertions
    mock_fetch.assert_called_once_with(101, repo_dir)
    mock_sandbox.assert_called_once_with(101, repo_dir, memory_dir, "key-test", "token-test")
    mock_post_comment.assert_called_once()
    mock_restore.assert_called_once_with(repo_dir)
    mock_append_ledger.assert_called_once()
    mock_telegram.assert_called_once()

    # Check what was logged to ledger
    ledger_data = mock_append_ledger.call_args[0][1]
    assert ledger_data["pr"] == 101
    assert ledger_data["head_sha"] == "sha-abc-123"
    assert ledger_data["status"] == "COMPLETED"
    assert ledger_data["verdict"] == "GREEN"


@patch("pr_triage_autopilot.send_telegram_alert")
@patch("pr_triage_autopilot.post_gh_comment")
@patch("pr_triage_autopilot._gh_json")
@patch("pr_triage_autopilot.get_ledger_entries")
def test_run_triage_cycle_stage0_skipped(
    mock_get_ledger, mock_gh_json, mock_post_comment, mock_telegram, tmp_path
):
    # Mock Open PR from owner (should skip)
    mock_gh_json.return_value = [
        {
            "number": 102,
            "author": {"login": "ffroliva", "is_bot": False},
            "baseRefName": "develop",
            "title": "feat: owner change",
            "body": "No action needed.",
            "state": "OPEN",
            "isDraft": False,
            "additions": 10,
            "deletions": 2,
            "changedFiles": 1,
            "comments": [],
        }
    ]

    mock_get_ledger.return_value = []

    repo_dir = tmp_path / "repo"
    memory_dir = tmp_path / "memory"
    ledger_path = tmp_path / "ledger.jsonl"

    pr_triage_autopilot.run_triage_cycle(
        repo="owner/repo",
        repo_dir=repo_dir,
        memory_dir=memory_dir,
        ledger_path=ledger_path,
        anthropic_key="key-test",
        gh_token="token-test",
    )

    # Skip shouldn't call comment, telegram alerts, or any git branch checkout
    mock_post_comment.assert_not_called()
    mock_telegram.assert_not_called()
