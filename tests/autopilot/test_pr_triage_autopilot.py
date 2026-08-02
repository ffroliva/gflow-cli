from __future__ import annotations

import datetime
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure scripts/autopilot is in path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "autopilot"))

import pr_triage_autopilot  # noqa: E402

# Subscription OAuth credentials path threaded to the sandbox in place of an
# API key (2026-08-02). The orchestrator only passes it through; validity is
# checked on the host in check_oauth_credentials.
CREDS_PATH = Path("/home/hermes/.claude/.credentials.json")


def test_parse_summary_verdict():
    output = (
        "Some random logs from claude CLI...\n"
        "SUMMARY_VERDICT: YELLOW | MUST_FIX_COUNT: 4 | PR_URL: https://github.com/org/repo/pull/123\n"
        "Another log line"
    )
    verdict, count = pr_triage_autopilot.parse_summary_verdict(output)
    assert verdict == "YELLOW"
    assert count == 4


def test_parse_summary_verdict_rejects_non_allowlisted():
    output = "SUMMARY_VERDICT: <img src=x onerror=alert(1)> | MUST_FIX_COUNT: 0 | PR_URL: x"
    verdict, count = pr_triage_autopilot.parse_summary_verdict(output)
    assert verdict is None
    assert count == 0


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
    yesterday = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)).isoformat()

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
        credentials_file=CREDS_PATH,
        gh_token="token-test",
    )

    # Assertions
    mock_fetch.assert_called_once_with(101, repo_dir)
    mock_sandbox.assert_called_once_with(101, repo_dir, memory_dir, CREDS_PATH, "token-test")
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
        credentials_file=CREDS_PATH,
        gh_token="token-test",
    )

    # Skip shouldn't call comment, telegram alerts, or any git branch checkout
    mock_post_comment.assert_not_called()
    mock_telegram.assert_not_called()


def test_resolve_engine_defaults_to_council_claude(monkeypatch):
    monkeypatch.delenv("PR_TRIAGE_ENGINE", raising=False)
    assert pr_triage_autopilot.resolve_engine() == "council-claude"


def test_resolve_engine_rejects_unknown(monkeypatch):
    monkeypatch.setenv("PR_TRIAGE_ENGINE", "council-multi-cli")
    with pytest.raises(SystemExit, match="council-multi-cli"):
        pr_triage_autopilot.resolve_engine()


def test_run_review_dispatches_council_claude():
    with patch("pr_triage_autopilot.run_docker_sandbox", return_value="out") as m:
        out = pr_triage_autopilot.run_review(
            "council-claude", 1, Path("/r"), Path("/m"), "key", "tok"
        )
    assert out == "out"
    m.assert_called_once_with(1, Path("/r"), Path("/m"), "key", "tok")


def test_run_review_unknown_engine_raises():
    with pytest.raises(NotImplementedError):
        pr_triage_autopilot.run_review("council-multi-cli", 1, Path("/r"), Path("/m"), "key", "tok")


def _cycle_mocks():
    """Standard patch stack for run_triage_cycle tests. Returns the context managers."""
    return [
        patch("pr_triage_autopilot.send_email_alert"),
        patch("pr_triage_autopilot.send_telegram_alert"),
        patch("pr_triage_autopilot.post_gh_comment"),
        patch("pr_triage_autopilot.run_docker_sandbox"),
        patch("pr_triage_autopilot.fetch_and_checkout_pr", return_value="abc123"),
        patch("pr_triage_autopilot.restore_repo_branch"),
        patch("pr_triage_autopilot._gh_json", return_value=[{"number": 7}]),
        patch("pr_triage_gate.should_review"),
    ]


def test_email_sent_on_completed_review(tmp_path):
    mocks = _cycle_mocks()
    with (
        mocks[0] as m_email,
        mocks[1],
        mocks[2],
        mocks[3] as m_sandbox,
        mocks[4],
        mocks[5],
        mocks[6],
        mocks[7] as m_gate,
    ):
        m_gate.return_value = {"verdict": "PROCEED", "reasons": []}
        m_sandbox.return_value = (
            "SUMMARY_VERDICT: GREEN | MUST_FIX_COUNT: 0 | PR_URL: https://x/pull/7"
        )
        pr_triage_autopilot.run_triage_cycle(
            "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "key", "tok"
        )
    assert m_email.call_count == 1
    subject = m_email.call_args[0][0]
    assert "#7" in subject and "GREEN" in subject


def test_email_sent_on_needs_human(tmp_path):
    mocks = _cycle_mocks()
    with (
        mocks[0] as m_email,
        mocks[1],
        mocks[2],
        mocks[3],
        mocks[4],
        mocks[5],
        mocks[6],
        mocks[7] as m_gate,
    ):
        m_gate.return_value = {"verdict": "NEEDS-HUMAN", "reasons": ["injection pattern"]}
        pr_triage_autopilot.run_triage_cycle(
            "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "key", "tok"
        )
    assert m_email.call_count == 1
    assert "human" in m_email.call_args[0][0].lower()


def test_email_sent_on_deferred_size(tmp_path):
    mocks = _cycle_mocks()
    with (
        mocks[0] as m_email,
        mocks[1],
        mocks[2],
        mocks[3],
        mocks[4],
        mocks[5],
        mocks[6],
        mocks[7] as m_gate,
    ):
        m_gate.return_value = {"verdict": "DEFERRED_SIZE", "reasons": ["too big"]}
        pr_triage_autopilot.run_triage_cycle(
            "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "key", "tok"
        )
    assert m_email.call_count == 1


def test_email_sent_on_failed_permanent(tmp_path):
    mocks = _cycle_mocks()
    with (
        mocks[0] as m_email,
        mocks[1],
        mocks[2],
        mocks[3] as m_sandbox,
        mocks[4],
        mocks[5],
        mocks[6],
        mocks[7] as m_gate,
    ):
        m_gate.return_value = {"verdict": "PROCEED", "reasons": []}
        m_sandbox.side_effect = RuntimeError("container died")
        with patch(
            "pr_triage_autopilot.get_ledger_entries",
            return_value=[
                {"pr": 7, "head_sha": "abc123", "status": "FAILED"},
                {"pr": 7, "head_sha": "abc123", "status": "FAILED"},
            ],
        ):
            pr_triage_autopilot.run_triage_cycle(
                "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "key", "tok"
            )
    # third failure -> FAILED_PERMANENT -> email
    assert m_email.call_count == 1
    assert "permanent" in m_email.call_args[0][0].lower()


def test_send_email_alert_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OPS_DIR", str(tmp_path))  # notifier script absent
    pr_triage_autopilot.send_email_alert("subject", "<b>html</b>")  # must not raise


def test_send_email_alert_never_raises_on_subprocess_error(tmp_path, monkeypatch):
    notifier = tmp_path / "scripts" / "notify" / "email_notify.py"
    notifier.parent.mkdir(parents=True)
    notifier.write_text("# fake notifier", encoding="utf-8")
    monkeypatch.setenv("HERMES_OPS_DIR", str(tmp_path))
    with patch("pr_triage_autopilot.subprocess.run", side_effect=OSError("boom")) as m_run:
        pr_triage_autopilot.send_email_alert("s", "h")  # must not raise
    m_run.assert_called_once()


def test_needs_human_dedupes_by_gate_sha(tmp_path):
    mocks = _cycle_mocks()
    with (
        mocks[0] as m_email,
        mocks[1],
        mocks[2] as m_comment,
        mocks[3],
        mocks[4],
        mocks[5],
        mocks[6] as m_gh,
        mocks[7] as m_gate,
    ):
        m_gh.return_value = [{"number": 7, "headRefOid": "sha-x"}]
        m_gate.return_value = {"verdict": "NEEDS-HUMAN", "reasons": ["injection pattern"]}

        # First tick: nothing ledgered -> alert + comment once
        with patch("pr_triage_autopilot.get_ledger_entries", return_value=[]):
            pr_triage_autopilot.run_triage_cycle(
                "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "key", "tok"
            )
        assert m_email.call_count == 1
        assert m_comment.call_count == 1

        # Second tick: same SHA already ledgered -> no re-alert, no re-comment
        with patch(
            "pr_triage_autopilot.get_ledger_entries",
            return_value=[{"pr": 7, "head_sha": "sha-x", "status": "NEEDS-HUMAN"}],
        ):
            pr_triage_autopilot.run_triage_cycle(
                "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "key", "tok"
            )
        assert m_email.call_count == 1
        assert m_comment.call_count == 1


def test_deferred_size_dedupes_by_gate_sha(tmp_path):
    mocks = _cycle_mocks()
    with (
        mocks[0] as m_email,
        mocks[1],
        mocks[2] as m_comment,
        mocks[3],
        mocks[4],
        mocks[5],
        mocks[6] as m_gh,
        mocks[7] as m_gate,
    ):
        m_gh.return_value = [{"number": 7, "headRefOid": "sha-x"}]
        m_gate.return_value = {"verdict": "DEFERRED_SIZE", "reasons": ["too big"]}

        # First tick: nothing ledgered -> alert once
        with patch("pr_triage_autopilot.get_ledger_entries", return_value=[]):
            pr_triage_autopilot.run_triage_cycle(
                "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "key", "tok"
            )
        assert m_email.call_count == 1

        # Second tick: same SHA already ledgered -> no re-alert
        with patch(
            "pr_triage_autopilot.get_ledger_entries",
            return_value=[{"pr": 7, "head_sha": "sha-x", "status": "DEFERRED_SIZE"}],
        ):
            pr_triage_autopilot.run_triage_cycle(
                "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "key", "tok"
            )
        assert m_email.call_count == 1
        assert m_comment.call_count == 0


# --- OAuth subscription auth (2026-08-02) -----------------------------------
# This deployment has no ANTHROPIC_API_KEY; `claude -p` authenticates with the
# subscription OAuth token. The 2026-07-16 token sat EXPIRED for 16 days with
# nothing surfacing it, so the host-side precheck is the regression guard.


def _creds(tmp_path, *, access="tok", expires_ms=None, refresh=None):
    import json as _json

    blob = {"claudeAiOauth": {"accessToken": access, "scopes": ["user:inference"]}}
    if expires_ms is not None:
        blob["claudeAiOauth"]["expiresAt"] = expires_ms
    if refresh is not None:
        blob["claudeAiOauth"]["refreshToken"] = refresh
    p = tmp_path / ".credentials.json"
    p.write_text(_json.dumps(blob), encoding="utf-8")
    return p


def _ms(dt):
    return int(dt.timestamp() * 1000)


def test_oauth_missing_file_is_reported(tmp_path):
    err = pr_triage_autopilot.check_oauth_credentials(tmp_path / "nope.json")
    assert err and "authenticate" in err


def test_oauth_unparseable_file_is_reported(tmp_path):
    p = tmp_path / ".credentials.json"
    p.write_text("{not json", encoding="utf-8")
    assert pr_triage_autopilot.check_oauth_credentials(p)


def test_oauth_valid_unexpired_token_passes(tmp_path):
    future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=8)
    assert (
        pr_triage_autopilot.check_oauth_credentials(_creds(tmp_path, expires_ms=_ms(future)))
        is None
    )


def test_oauth_expired_without_refresh_token_is_fatal(tmp_path):
    """The exact 2026-07-16 state: expired, refreshToken absent."""
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=16)
    err = pr_triage_autopilot.check_oauth_credentials(_creds(tmp_path, expires_ms=_ms(past)))
    assert err and "re-authenticate" in err.lower()


def test_oauth_expired_with_refresh_token_is_allowed(tmp_path):
    """Expiry alone must not block: the CLI can renew when a refreshToken exists.

    Treating stale-but-refreshable as fatal would fail every run an hour after
    login, which is worse than the bug being fixed.
    """
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)
    assert (
        pr_triage_autopilot.check_oauth_credentials(
            _creds(tmp_path, expires_ms=_ms(past), refresh="r")
        )
        is None
    )


def test_resolve_credentials_file_honours_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CREDENTIALS_FILE", str(tmp_path / "x.json"))
    assert pr_triage_autopilot.resolve_credentials_file() == tmp_path / "x.json"
