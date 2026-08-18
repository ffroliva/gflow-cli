from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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
    monkeypatch,
):
    # The deployed shape: a dedicated read-only token exists, so the container
    # must receive THAT, never the write-scoped token used to post comments.
    monkeypatch.setenv("GH_SANDBOX_TOKEN", "ro-token")

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
        gh_token="token-test",
    )

    # Assertions
    mock_fetch.assert_called_once_with(101, repo_dir)
    # read-only token into the sandbox, NOT the write-scoped "token-test"
    mock_sandbox.assert_called_once_with(101, repo_dir, memory_dir, "ro-token")
    assert mock_post_comment.call_args.kwargs["token"] == "token-test"
    mock_post_comment.assert_called_once()
    # pr_num is passed so the fetched pr-<N>-review branch is deleted, not left behind
    mock_restore.assert_called_once_with(repo_dir, pr_num=101)
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
        out = pr_triage_autopilot.run_review("council-claude", 1, Path("/r"), Path("/m"), "tok")
    assert out == "out"
    m.assert_called_once_with(1, Path("/r"), Path("/m"), "tok")


def test_run_review_unknown_engine_raises():
    with pytest.raises(NotImplementedError):
        pr_triage_autopilot.run_review("council-multi-cli", 1, Path("/r"), Path("/m"), "tok")


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
            "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "tok"
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
            "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "tok"
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
            "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "tok"
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
                "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "tok"
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
                "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "tok"
            )
        assert m_email.call_count == 1
        assert m_comment.call_count == 1

        # Second tick: same SHA already ledgered -> no re-alert, no re-comment
        with patch(
            "pr_triage_autopilot.get_ledger_entries",
            return_value=[{"pr": 7, "head_sha": "sha-x", "status": "NEEDS-HUMAN"}],
        ):
            pr_triage_autopilot.run_triage_cycle(
                "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "tok"
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
                "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "tok"
            )
        assert m_email.call_count == 1

        # Second tick: same SHA already ledgered -> no re-alert
        with patch(
            "pr_triage_autopilot.get_ledger_entries",
            return_value=[{"pr": 7, "head_sha": "sha-x", "status": "DEFERRED_SIZE"}],
        ):
            pr_triage_autopilot.run_triage_cycle(
                "org/repo", tmp_path, tmp_path, tmp_path / "l.jsonl", "tok"
            )
        assert m_email.call_count == 1
        assert m_comment.call_count == 0


# --- Claude subscription auth via CLAUDE_CODE_OAUTH_TOKEN (2026-08-02) -------
# `claude setup-token` mints a 1-year bearer token read from the environment.
# It does NOT write ~/.claude/.credentials.json -- verified on the ops VPS, where
# that file still held the expired 2026-07-16 token after a successful mint. An
# earlier revision of this branch validated the FILE and would therefore have
# authenticated with a dead credential.


def test_missing_token_is_reported(monkeypatch):
    monkeypatch.delenv(pr_triage_autopilot.CLAUDE_TOKEN_ENV, raising=False)
    err = pr_triage_autopilot.check_claude_auth()
    assert err and "setup-token" in err


def test_empty_token_is_reported(monkeypatch):
    monkeypatch.setenv(pr_triage_autopilot.CLAUDE_TOKEN_ENV, "")
    assert pr_triage_autopilot.check_claude_auth()


def test_present_token_passes(monkeypatch):
    monkeypatch.setenv(pr_triage_autopilot.CLAUDE_TOKEN_ENV, "sk-ant-oat01-xxx")
    assert pr_triage_autopilot.check_claude_auth() is None


def test_sandbox_token_prefers_the_dedicated_readonly_token(monkeypatch):
    monkeypatch.setenv("GH_SANDBOX_TOKEN", "github_pat_readonly")
    with patch("pr_triage_autopilot.send_telegram_alert") as m_alert:
        assert pr_triage_autopilot.resolve_sandbox_token("write-token") == "github_pat_readonly"
    assert m_alert.call_count == 0


def test_sandbox_token_falls_back_loudly_when_unset(monkeypatch):
    """Absent secret must degrade noisily, never silently re-grant write access."""
    monkeypatch.delenv("GH_SANDBOX_TOKEN", raising=False)
    with patch("pr_triage_autopilot.send_telegram_alert") as m_alert:
        assert pr_triage_autopilot.resolve_sandbox_token("write-token") == "write-token"
    assert m_alert.call_count == 1
    assert "GH_SANDBOX_TOKEN" in m_alert.call_args[0][0]


def test_post_gh_comment_passes_the_write_token_explicitly():
    """Must not rely on ambient GH_TOKEN — the host env carries several tokens."""
    with patch("pr_triage_autopilot.subprocess.run") as m_run:
        m_run.return_value.returncode = 0
        pr_triage_autopilot.post_gh_comment(7, "body", "o/r", token="write-token")
    assert m_run.call_args.kwargs["env"]["GH_TOKEN"] == "write-token"


def _git(repo, *args):
    import subprocess

    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def _make_repo(tmp_path):
    """A real repo on develop with a pr-999-review branch checked out."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "develop")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("x")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "branch", "pr-999-review")
    _git(repo, "checkout", "-q", "pr-999-review")
    return repo


def _branches(repo):
    import subprocess

    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"], cwd=repo, capture_output=True, text=True
    )
    return set(out.stdout.split())


def test_restore_repo_branch_deletes_the_pr_branch(tmp_path):
    """The fetched pr-<N>-review branch must not outlive the review."""
    repo = _make_repo(tmp_path)
    assert "pr-999-review" in _branches(repo)

    pr_triage_autopilot.restore_repo_branch(repo, pr_num=999)

    assert "pr-999-review" not in _branches(repo)
    assert _branches(repo) == {"develop"}


def test_restore_repo_branch_tolerates_absent_pr_branch(tmp_path):
    """A run that failed before fetching leaves no branch; cleanup must not raise."""
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "-q", "develop")
    _git(repo, "branch", "-D", "pr-999-review")

    pr_triage_autopilot.restore_repo_branch(repo, pr_num=999)

    assert _branches(repo) == {"develop"}


def test_restore_repo_branch_without_pr_num_still_restores_develop(tmp_path):
    repo = _make_repo(tmp_path)
    pr_triage_autopilot.restore_repo_branch(repo)
    assert "pr-999-review" in _branches(repo)


def test_memory_dir_cli_arg_wins_over_env(monkeypatch, tmp_path):
    env_dir = tmp_path / "from-env"
    cli_dir = tmp_path / "from-cli"
    env_dir.mkdir()
    cli_dir.mkdir()
    monkeypatch.setenv(pr_triage_autopilot.MEMORY_DIR_ENV, str(env_dir))
    assert pr_triage_autopilot.resolve_memory_dir(str(cli_dir)) == cli_dir.resolve()


def test_memory_dir_falls_back_to_env(monkeypatch, tmp_path):
    env_dir = tmp_path / "from-env"
    env_dir.mkdir()
    monkeypatch.setenv(pr_triage_autopilot.MEMORY_DIR_ENV, str(env_dir))
    assert pr_triage_autopilot.resolve_memory_dir(None) == env_dir.resolve()


def test_memory_dir_falls_back_to_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.delenv(pr_triage_autopilot.MEMORY_DIR_ENV, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    expected = tmp_path / "gflow-cli" / "memory"
    expected.mkdir(parents=True)
    assert pr_triage_autopilot.resolve_memory_dir(None) == expected.resolve()


def test_memory_dir_missing_exits_naming_the_path_and_overrides(monkeypatch, tmp_path):
    monkeypatch.delenv(pr_triage_autopilot.MEMORY_DIR_ENV, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        pr_triage_autopilot.resolve_memory_dir(None)
    message = str(exc.value)
    assert "gflow-cli" in message and "memory" in message
    assert "mkdir -p" in message
    assert pr_triage_autopilot.MEMORY_DIR_ENV in message


def test_main_resolves_memory_from_environment_without_cli_flag(monkeypatch, tmp_path):
    """main() must reach run_triage_cycle with the resolved dir when no flag is passed.

    The cron line carries no --memory-dir, so this is the path production uses.
    """
    memory = tmp_path / "xdg" / "gflow-cli" / "memory"
    memory.mkdir(parents=True)
    monkeypatch.delenv(pr_triage_autopilot.MEMORY_DIR_ENV, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setenv(pr_triage_autopilot.CLAUDE_TOKEN_ENV, "sk-ant-oat01-xxx")

    with patch("pr_triage_autopilot.run_triage_cycle") as m_cycle:
        ret = pr_triage_autopilot.main(["--repo-dir", str(tmp_path)])

    assert ret == 0
    assert m_cycle.call_args[0][2] == memory.resolve()


def test_main_exits_when_resolved_memory_dir_is_absent(monkeypatch, tmp_path):
    """A missing memory dir must abort before the lock and the container."""
    monkeypatch.delenv(pr_triage_autopilot.MEMORY_DIR_ENV, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "empty"))
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setenv(pr_triage_autopilot.CLAUDE_TOKEN_ENV, "sk-ant-oat01-xxx")

    with patch("pr_triage_autopilot.run_triage_cycle") as m_cycle:
        with pytest.raises(SystemExit):
            pr_triage_autopilot.main(["--repo-dir", str(tmp_path)])

    assert m_cycle.call_count == 0


def test_main_sends_telegram_alert_on_missing_token(monkeypatch, tmp_path):
    monkeypatch.delenv("GH_COMMENT_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with patch("pr_triage_autopilot.send_telegram_alert") as m_alert:
        ret = pr_triage_autopilot.main(["--repo-dir", str(tmp_path), "--memory-dir", str(tmp_path)])
        assert ret == 1
        assert m_alert.call_count == 1
        assert "Missing credentials" in m_alert.call_args[0][0]


def test_main_sends_telegram_alert_on_auth_error(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.delenv(pr_triage_autopilot.CLAUDE_TOKEN_ENV, raising=False)
    with patch("pr_triage_autopilot.send_telegram_alert") as m_alert:
        ret = pr_triage_autopilot.main(["--repo-dir", str(tmp_path), "--memory-dir", str(tmp_path)])
        assert ret == 1
        assert m_alert.call_count == 1
        assert "Claude authentication unusable" in m_alert.call_args[0][0]


# --- council memory reaches the reviewer inside the sandbox -------------------
# The mount target and the path SKILL.md tells reviewers to read are two halves
# of one contract, held in two files that nothing linked. They disagreed: the
# sandbox mounted /memory while D5 was instructed to inspect
# ~/.claude/projects/<slug>/memory, which does not exist in the container. The
# council therefore found no memory even once the host tree was populated.

SANDBOX_SH = ROOT / "scripts" / "autopilot" / "run_sandboxed_review.sh"
COUNCIL_SKILL = ROOT / "skills" / "pr-council-review" / "SKILL.md"
CONTAINER_HOME = "/home/nonroot"


def _skill_memory_path() -> str:
    """The memory path SKILL.md § D5 instructs a reviewer to inspect."""
    match = re.search(
        r"Inspect `~(/\.claude/projects/[^`]+?)/?`", COUNCIL_SKILL.read_text(encoding="utf-8")
    )
    assert match, "SKILL.md no longer states a D5 memory path in the expected form"
    return CONTAINER_HOME + match.group(1)


def _sandbox_memory_dir() -> str:
    """The container path run_sandboxed_review.sh mounts council memory at."""
    match = re.search(r'COUNCIL_MEMORY_DIR="([^"]+)"', SANDBOX_SH.read_text(encoding="utf-8"))
    assert match, "run_sandboxed_review.sh no longer declares COUNCIL_MEMORY_DIR"
    return match.group(1)


def test_sandbox_mounts_memory_where_the_skill_looks_for_it():
    assert _sandbox_memory_dir() == _skill_memory_path(), (
        "the sandbox mounts council memory somewhere the reviewer is not told to look; "
        "D5 will report no memory no matter what the host tree contains"
    )


def test_sandbox_mounts_memory_read_only():
    sh = SANDBOX_SH.read_text(encoding="utf-8")
    assert '-v "$HOST_MEMORY:$COUNCIL_MEMORY_DIR:ro"' in sh, (
        "council memory must be mounted read-only; the container only ever reads it"
    )


def test_sandbox_grants_the_agent_access_to_the_memory_dir():
    """The mount is necessary but not sufficient.

    The tree is outside the agent's /workspace cwd, so without --add-dir Claude
    Code refuses to read it ("I don't have permission to read that file") and
    the council silently reviews with no memory.
    """
    sh = SANDBOX_SH.read_text(encoding="utf-8")
    assert '--add-dir "$COUNCIL_MEMORY_DIR"' in sh, (
        "sandbox mounts council memory but never grants the agent access to it"
    )


def test_add_dir_follows_the_positional_prompt():
    """--add-dir is variadic: placed before the prompt it consumes it.

    Symptom is not a permission error but a hard start-up failure:
    "Input must be provided either through stdin or as a prompt argument".
    """
    sh = SANDBOX_SH.read_text(encoding="utf-8")
    prompt = re.search(r'claude -p "(?P<body>[^"]+)"', sh)
    assert prompt, "could not locate the claude -p invocation"
    # match the flag as used, not the word: the surrounding comment mentions it too
    flag = sh.index('--add-dir "$COUNCIL_MEMORY_DIR"')
    assert flag > prompt.end(), "--add-dir precedes the positional prompt and will swallow it"


def test_firewall_resolves_only_ipv4_addresses():
    """iptables is v4-only and errors out on an AAAA result.

    api.anthropic.com publishes both an A and an AAAA record. Feeding the AAAA
    to `iptables -d` fails, and under `set -e` that killed the run before
    `docker run` was ever reached -- observed on the ops VPS 2026-08-18.
    """
    sh = SANDBOX_SH.read_text(encoding="utf-8")
    assert "getent ahosts " not in sh, (
        "firewall setup resolves AAAA records; iptables rejects them and set -e "
        "aborts the review before the container starts. Use `getent ahostsv4`."
    )
    assert sh.count("getent ahostsv4 ") == 4, (
        "expected all four host lookups (2 setup, 2 cleanup) to be IPv4-only"
    )


def test_sandbox_runs_the_agent_without_interactive_permission_prompts():
    """In -p mode a permission prompt is an auto-deny, not a pause.

    Observed on the ops VPS 2026-08-18: every `gh` call came back "This command
    requires approval" and the reviewer halted to ask a question no one would
    read. The container is the boundary (non-root, ro mounts, read-only PAT,
    egress firewall, --rm), so the in-container prompt only blocks work.
    """
    sh = SANDBOX_SH.read_text(encoding="utf-8")
    assert "--dangerously-skip-permissions" in sh, (
        "the sandboxed reviewer cannot run gh without this; it will auto-deny and stall"
    )


def test_entrypoint_has_no_dangling_memory_symlink():
    """The old symlink pointed at /memory, which is no longer a mount target."""
    ep = (ROOT / "scripts" / "autopilot" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "ln -sf /memory" not in ep, (
        "entrypoint still links /memory, which nothing mounts since the memory tree "
        "moved to the path SKILL.md reads"
    )
