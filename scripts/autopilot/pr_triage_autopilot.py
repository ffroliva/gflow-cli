#!/usr/bin/env python3
"""Main Host Orchestrator for the PR-Triage Autopilot.

Polls open PRs, applies Stage 0 pre-filter, checks/updates audit ledger,
fetches PR branches, runs sandboxed container reviews, posts comments to GitHub,
and sends Telegram notifications.
"""

from __future__ import annotations

import argparse
import datetime
import html as html_lib
import json
import os
import subprocess
import sys
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

# Review engine seam (spec addendum 2026-07-10). Only council-claude is
# implemented; council-multi-cli is reserved backlog behind this seam.
SUPPORTED_ENGINES = ("council-claude",)
DEFAULT_ENGINE = SUPPORTED_ENGINES[0]

# Claude auth is the SUBSCRIPTION token minted by `claude setup-token`, read
# from CLAUDE_CODE_OAUTH_TOKEN. This deployment has no ANTHROPIC_API_KEY
# (operator decision, 2026-08-02).
#
# NOT ~/.claude/.credentials.json: `setup-token` does not write that file.
# Verified on the ops VPS 2026-08-02 -- after a successful mint it still held the
# expired 2026-07-16 interactive-login token. Reading the file would silently
# authenticate with a dead credential.
#
# The token is a static 1-year bearer value with no refresh pair, so there is
# nothing to validate locally beyond presence: no expiry is encoded in it. That
# makes the rotation reminder (hermes-ops ev-ops-health digest) the only thing
# standing between a lapse and a silent outage -- the 2026-07-16 token 401'd for
# 16 days with nothing reporting it.
CLAUDE_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"

# Council memory directory (D5 reads this tree read-only inside the sandbox).
#
# Resolved per host, never hardcoded -- a machine-specific path in a cron line
# is not portable and cannot be validated. XDG_DATA_HOME (not STATE or CACHE):
# this is durable curated knowledge meant to be backed up, which is the data
# slot in the XDG Base Directory Specification.
MEMORY_DIR_ENV = "GFLOW_TRIAGE_MEMORY_DIR"
MEMORY_XDG_SUBDIR = "gflow-cli/memory"

# Read-only GitHub token handed to the review container (see
# resolve_sandbox_token). Provisioned in hermes-ops SOPS, rendered into
# /opt/hermes/.env, delivered by the cron line that sources it.
SANDBOX_TOKEN_ENV = "GH_SANDBOX_TOKEN"


def check_claude_auth() -> str | None:
    """Return an error string when Claude auth is unusable, else None.

    Checked on the HOST before building the image and starting a container: a
    missing token otherwise surfaces as an opaque 401 several minutes into a
    sandboxed run.
    """
    if not os.environ.get(CLAUDE_TOKEN_ENV):
        return (
            f"{CLAUDE_TOKEN_ENV} is not set. Mint one with "
            "`sudo -u hermes -H claude setup-token` (valid 1 year) and store it in "
            "hermes-ops secrets/vps-prod.env.sops.yaml. The cron line sources "
            "/opt/hermes/.env, which is where it must appear."
        )
    return None


# Verdict allowlist: container stdout is untrusted, so anything outside this
# set is treated as unparseable (None) rather than interpolated downstream.
VALID_VERDICTS = ("GREEN", "YELLOW", "RED")

# The review payload (`claude -p`) shares one subscription with interactive use,
# so it can exhaust the session quota. That is transient and self-healing: the
# window resets on its own. It reached us as a generic non-zero exit, which the
# cycle counted as a review failure -- and three hourly retries land inside the
# SAME exhausted window, so PR #650 was marked FAILED_PERMANENT (manual ledger
# surgery) by a condition that needed nothing but time. The Docker build had
# succeeded on every attempt.
_QUOTA_SIGNATURES: tuple[str, ...] = (
    "hit your session limit",
    "usage limit reached",
    "rate limit exceeded",
    "429 too many requests",
)

# Docker's build progress is front-loaded (`#1 DONE`, `#2 DONE`, ...) and the
# payload error is the LAST line, so a head-truncated alert shows only noise.
# Every alert for the 2026-09-04 incident cut off before the sentence that
# explained it.
ALERT_EXCERPT_CHARS = 900


def is_transient_quota_error(message: str) -> bool:
    """True when a failure is an LLM quota wait rather than a defect.

    Deliberately narrow: a broken sandbox, a missing token or a full disk must
    still burn a retry, or a genuinely stuck PR would be retried forever with
    nobody told.
    """
    haystack = message.casefold()
    return any(sig in haystack for sig in _QUOTA_SIGNATURES)


def alert_excerpt(message: str) -> str:
    """Excerpt an error for an alert, keeping the END where the reason lives."""
    text = message.strip()
    if len(text) <= ALERT_EXCERPT_CHARS:
        return text
    return "...(truncated)... " + text[-ALERT_EXCERPT_CHARS:]

# Markers the council report is sliced on before it is posted publicly.
# The heading opens the report; the machine line closes it and is parsed for
# the verdict, so everything outside the pair is preamble or wrapper logging.
REPORT_HEADING_PREFIX = "# PR #"
SUMMARY_VERDICT_MARKER = "SUMMARY_VERDICT:"

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


def send_email_alert(subject: str, html: str) -> None:
    """High-signal email via hermes-ops' Resend notifier. NEVER fatal.

    Delegates to $HERMES_OPS_DIR/scripts/notify/email_notify.py (subprocess,
    HTML on stdin) so the single Resend implementation stays in hermes-ops.
    Missing script / missing env / any failure degrades to a log line —
    the ledger and GitHub-posted report remain the source of truth.
    """
    ops_dir = os.environ.get("HERMES_OPS_DIR", "/opt/hermes-ops")
    script = Path(ops_dir) / "scripts" / "notify" / "email_notify.py"
    if not script.exists():
        logger.info("Email notifier not present; skipping email", script=str(script))
        return
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--subject", subject, "--html", "-"],
            input=html,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=60,
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if proc.returncode != 0:
            logger.warning(
                "Email notifier exited non-zero",
                returncode=proc.returncode,
                stderr=(proc.stderr or "")[-300:],
            )
    except Exception as exc:
        logger.error("Failed to send email alert", error=str(exc))


def _gh_json(args: list[str], repo: str) -> any:
    proc = subprocess.run(
        ["gh", *args, "--repo", repo], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def resolve_sandbox_token(write_token: str) -> str:
    """Return the read-only token handed to the review container.

    The container only reads the PR; the host posts the comment. Least
    privilege: no write credential belongs in the container.

    Falls back to the write token so a missing secret never takes the
    pipeline down, but alerts rather than degrading silently.
    """
    sandbox_token = os.environ.get(SANDBOX_TOKEN_ENV)
    if sandbox_token:
        return sandbox_token
    logger.warning(
        "Sandbox token missing; falling back to the write-scoped token",
        env_var=SANDBOX_TOKEN_ENV,
    )
    send_telegram_alert(
        f"⚠️ {SANDBOX_TOKEN_ENV} is not set — the review sandbox is running with a "
        "WRITE-scoped GitHub token. Restore the read-only token in hermes-ops SOPS."
    )
    return write_token


def post_gh_comment(pr_num: int, body: str, repo: str, token: str | None = None) -> None:
    """Post comment back to PR using GitHub CLI.

    The token is passed explicitly rather than inherited from ambient env, so
    the intended credential is always the one used.
    """
    env = {**os.environ, "GH_TOKEN": token} if token else None
    proc = subprocess.run(
        ["gh", "pr", "comment", str(pr_num), "--body", body, "--repo", repo],
        capture_output=True,
        text=True,
        check=False,
        env=env,
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


def _git(repo_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command pinned to ``repo_dir``.

    ``--git-dir``/``--work-tree`` bypass git's *upward* repository discovery.
    Without them a ``repo_dir`` that is not a repository -- a mistyped
    ``--repo-dir``, or a checkout whose ``.git`` has gone missing -- resolves to
    whatever clone happens to enclose it, so ``git checkout develop`` silently
    rewrites someone else's working tree. That is issue #605: a local test run
    under ``--basetemp=tmp/pytest`` moved the developer's own clone off its
    branch. Pinned, the same call fails loudly instead.

    Raises the module's ``RuntimeError`` idiom rather than letting ``check=True``
    produce a ``CalledProcessError``: that renders only "returned non-zero exit
    status 128" and keeps git's own "fatal: not a git repository: ..." in an
    unread ``.stderr``. Callers log ``str(exc)``, so the diagnostic this pinning
    exists to produce would never reach the operator. Matches ``_gh_json`` and
    ``post_gh_comment``.

    ``repo_dir`` is resolved first because ``--git-dir``/``--work-tree`` are
    interpreted relative to ``cwd`` -- which is ``repo_dir`` -- so a relative path
    would double-nest into ``<repo_dir>/<repo_dir>/.git``.
    """
    repo_dir = repo_dir.resolve()
    proc = subprocess.run(
        ["git", f"--git-dir={repo_dir / '.git'}", f"--work-tree={repo_dir}", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo_dir}: {proc.stderr.strip()}")
    return proc


def fetch_and_checkout_pr(pr_num: int, repo_dir: Path) -> str:
    """Fetch the PR branch, checkout, and return the head SHA."""
    # Fetch
    ref = f"pull/{pr_num}/head:pr-{pr_num}-review"
    logger.info("Fetching PR branch", pr=pr_num, ref=ref)
    _git(repo_dir, "fetch", "-f", "origin", ref)

    # Get head SHA
    head_sha = _git(repo_dir, "rev-parse", f"pr-{pr_num}-review").stdout.strip()

    # Checkout
    logger.info("Checking out PR branch", sha=head_sha)
    _git(repo_dir, "checkout", f"pr-{pr_num}-review")
    return head_sha


def restore_repo_branch(repo_dir: Path, pr_num: int | None = None) -> None:
    """Restore the host clone to develop and drop the fetched PR branch.

    Deleting matters: ``fetch_and_checkout_pr`` creates ``pr-<N>-review`` on
    every review and nothing ever removed it, so the clone accumulated one
    branch per PR forever (``pr-549-review`` was still present on the VPS
    weeks later). Best-effort -- a run that failed before fetching has no
    branch to drop, and cleanup must never mask the original error.
    """
    logger.info("Restoring repository to develop branch")
    _git(repo_dir, "checkout", "develop")
    if pr_num is not None:
        branch = f"pr-{pr_num}-review"
        proc = _git(repo_dir, "branch", "-D", branch, check=False)
        if proc.returncode == 0:
            logger.info("Deleted PR review branch", branch=branch)


def run_docker_sandbox(pr_num: int, repo_dir: Path, memory_dir: Path, gh_token: str) -> str:
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
    ]

    logger.info("Running sandboxed Docker review", pr=pr_num)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Docker sandbox failed (exit {proc.returncode}): {proc.stderr.strip()}\n{proc.stdout}"
        )
    return proc.stdout


def extract_report(container_output: str) -> str:
    """Return just the council report from the container's stdout.

    The container's stdout also carries the reviewing agent's own preamble, and
    historically the sandbox wrapper's Docker/iptables progress lines. Posting
    all of it put the build steps, a raw Docker network id, and the bridge
    subnet into a public reply to an external contributor (PR #650). The
    wrapper now logs to stderr; this is the second half of that fix, and it
    also drops the agent preamble.

    Slice to the report proper: the first ``# PR #`` heading through the line
    before the ``SUMMARY_VERDICT:`` machine marker. Falls back to the whole
    output when either marker is missing -- a malformed report is still worth
    posting, and silently truncating one would be worse than a noisy comment.
    """
    lines = container_output.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(REPORT_HEADING_PREFIX)), None)
    end = next((i for i, ln in enumerate(lines) if SUMMARY_VERDICT_MARKER in ln), None)
    if start is None or end is None or end <= start:
        return container_output.strip()
    return "\n".join(lines[start:end]).strip()


def parse_summary_verdict(container_output: str) -> tuple[str | None, int]:
    """Parse SUMMARY_VERDICT and MUST_FIX_COUNT from stdout."""
    verdict = None
    must_fixes = 0
    for line in container_output.splitlines():
        if SUMMARY_VERDICT_MARKER in line:
            # Parse line e.g.: SUMMARY_VERDICT: YELLOW | MUST_FIX_COUNT: 5 | PR_URL: ...
            parts = [p.strip() for p in line.split("|")]
            for p in parts:
                if p.startswith("SUMMARY_VERDICT:"):
                    candidate = p.split(":")[1].strip()
                    verdict = candidate if candidate in VALID_VERDICTS else None
                elif p.startswith("MUST_FIX_COUNT:"):
                    try:
                        must_fixes = int(p.split(":")[1].strip())
                    except ValueError:
                        pass
    return verdict, must_fixes


def resolve_engine() -> str:
    """Return the configured review engine; refuse unknown values at startup."""
    engine = os.environ.get("PR_TRIAGE_ENGINE", DEFAULT_ENGINE)
    if engine not in SUPPORTED_ENGINES:
        raise SystemExit(f"Unsupported PR_TRIAGE_ENGINE={engine!r}; supported: {SUPPORTED_ENGINES}")
    return engine


def resolve_memory_dir(cli_value: str | None = None) -> Path:
    """Return the council memory directory for THIS host; refuse a missing one.

    Precedence: ``--memory-dir`` > ``$GFLOW_TRIAGE_MEMORY_DIR`` > XDG default.
    Checked on the HOST before the container starts, for the same reason
    ``check_claude_auth`` is: an unresolvable path otherwise surfaces as an
    opaque "Docker sandbox failed (exit 1)" from a ``cd`` deep inside the
    wrapper script, after the lock is taken and a ledger failure is recorded.
    """
    if cli_value:
        memory_dir, source = Path(cli_value), "--memory-dir"
    elif os.environ.get(MEMORY_DIR_ENV):
        memory_dir, source = Path(os.environ[MEMORY_DIR_ENV]), f"${MEMORY_DIR_ENV}"
    else:
        data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        memory_dir, source = data_home / MEMORY_XDG_SUBDIR, "XDG default"

    memory_dir = memory_dir.expanduser().resolve()
    if not memory_dir.is_dir():
        raise SystemExit(
            f"Council memory directory does not exist: {memory_dir} (resolved from {source}).\n"
            f"  Create it:            mkdir -p {memory_dir}\n"
            f"  Or point elsewhere:   --memory-dir <path>  |  export {MEMORY_DIR_ENV}=<path>\n"
            "An empty directory is valid — D5 then reports that no memory is available."
        )
    if not any(memory_dir.glob("*.md")):
        logger.warning(
            "Council memory directory is empty; D5 must report UNAVAILABLE, not GREEN. "
            "Memory is keyed by working-directory path and is not synced to this host "
            "by default — see docs/superpowers/specs/2026-07-04-pr-triage-autopilot-design.md.",
            memory_dir=str(memory_dir),
        )
    return memory_dir


def run_review(
    engine: str,
    pr_num: int,
    repo_dir: Path,
    memory_dir: Path,
    gh_token: str,
) -> str:
    """Run the configured review engine and return its stdout."""
    if engine == "council-claude":
        return run_docker_sandbox(pr_num, repo_dir, memory_dir, gh_token)
    raise NotImplementedError(f"engine {engine!r} is reserved but not implemented")


def run_triage_cycle(
    repo: str,
    repo_dir: Path,
    memory_dir: Path,
    ledger_path: Path,
    gh_token: str,
    engine: str = DEFAULT_ENGINE,
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
        gate_sha = pr.get("headRefOid", "")

        # 2. Run Stage 0 Gate
        gate_res = should_review(pr)
        verdict = gate_res["verdict"]

        if verdict == "SKIP":
            logger.info("PR Stage 0 skipped", pr=pr_num, reason=gate_res["reasons"])
            continue

        if verdict == "DEFERRED_SIZE":
            if any(
                e.get("pr") == pr_num
                and e.get("head_sha") == gate_sha
                and e.get("status") == "DEFERRED_SIZE"
                for e in ledger_entries
            ):
                logger.info("PR already deferred at this SHA; skipping re-alert", pr=pr_num)
                continue
            logger.warning(
                "PR deferred due to oversized diff", pr=pr_num, reason=gate_res["reasons"]
            )
            append_ledger_entry(
                ledger_path,
                {
                    "pr": pr_num,
                    "head_sha": gate_sha,
                    "status": "DEFERRED_SIZE",
                    "reasons": gate_res["reasons"],
                },
            )
            send_telegram_alert(f"⚠️ PR #{pr_num} deferred: diff size too large.")
            send_email_alert(
                f"[gflow-cli PR #{pr_num}] Deferred: diff too large",
                f'<p>PR <a href="https://github.com/{repo}/pull/{pr_num}">#{pr_num}</a> '
                f"exceeds the autopilot size cap and needs a manual review.</p>"
                f"<p>Reasons: {html_lib.escape(', '.join(gate_res['reasons']))}</p>",
            )
            continue

        if verdict == "NEEDS-HUMAN":
            if any(
                e.get("pr") == pr_num
                and e.get("head_sha") == gate_sha
                and e.get("status") == "NEEDS-HUMAN"
                for e in ledger_entries
            ):
                logger.info("PR already flagged at this SHA; skipping re-alert", pr=pr_num)
                continue
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
                post_gh_comment(pr_num, comment_body, repo, token=gh_token)
            except Exception as exc:
                logger.error("Failed to post comment to flagged PR", pr=pr_num, error=str(exc))

            append_ledger_entry(
                ledger_path,
                {
                    "pr": pr_num,
                    "head_sha": gate_sha,
                    "status": "NEEDS-HUMAN",
                    "reasons": gate_res["reasons"],
                },
            )
            send_telegram_alert(f"🚨 PR #{pr_num} flagged for human triage: {gate_res['reasons']}")
            send_email_alert(
                f"[gflow-cli PR #{pr_num}] Flagged: needs human triage",
                f'<p>PR <a href="https://github.com/{repo}/pull/{pr_num}">#{pr_num}</a> '
                f"was flagged by the Stage-0 gate and will not be auto-reviewed.</p>"
                f"<p>Reasons: {html_lib.escape(', '.join(gate_res['reasons']))}</p>",
            )
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
            # Read-only token into the sandbox; the write token stays on the host.
            output = run_review(
                engine, pr_num, repo_dir, memory_dir, resolve_sandbox_token(gh_token)
            )

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

            comment_body += extract_report(output)

            post_gh_comment(pr_num, comment_body, repo, token=gh_token)

            # Record completed in ledger
            append_ledger_entry(
                ledger_path,
                {
                    "pr": pr_num,
                    "head_sha": head_sha,
                    "status": "COMPLETED",
                    "verdict": parsed_verdict,
                    "must_fixes": must_fixes,
                    "engine": engine,
                },
            )

            send_telegram_alert(
                f"✅ Auto-reviewed PR #{pr_num} ({parsed_verdict}): {must_fixes} must-fixes."
            )
            send_email_alert(
                f"[gflow-cli PR #{pr_num}] Council verdict: {parsed_verdict or 'UNKNOWN'} "
                f"— {must_fixes} must-fix",
                f"<p>Autonomous council review of "
                f'<a href="https://github.com/{repo}/pull/{pr_num}">PR #{pr_num}</a> '
                f"completed: <b>{parsed_verdict or 'UNKNOWN'}</b>, "
                f"{must_fixes} must-fix item(s). Full report posted as a PR comment.</p>"
                f"<p><b>Note:</b> this is a static review — live validation "
                f"(e2e + /gflow:benchmark) remains the final gate before merge.</p>",
            )

        except Exception as exc:
            # A quota wait is not a review failure. Record it, leave the retry
            # budget untouched, and let the next tick pick the PR up once the
            # window has reset.
            if is_transient_quota_error(str(exc)):
                logger.warning(
                    "PR triage deferred: LLM quota exhausted",
                    pr=pr_num,
                    sha=head_sha,
                    detail=alert_excerpt(str(exc)),
                )
                append_ledger_entry(
                    ledger_path,
                    {
                        "pr": pr_num,
                        "head_sha": head_sha,
                        "status": "DEFERRED",
                        "fail_count": 0,
                        "error": alert_excerpt(str(exc)),
                        "engine": engine,
                    },
                )
                send_telegram_alert(
                    f"\u23f8 PR #{pr_num} review deferred - LLM quota exhausted, "
                    "retrying next tick (no retry burned)"
                )
                continue

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
                    "engine": engine,
                },
            )

            if status == "FAILED_PERMANENT":
                send_telegram_alert(
                    f"❌ PR #{pr_num} review failed permanently after {MAX_RETRIS} retries: {exc}"
                )
                send_email_alert(
                    f"[gflow-cli PR #{pr_num}] Review FAILED permanently",
                    f'<p>Review of <a href="https://github.com/{repo}/pull/{pr_num}">'
                    f"PR #{pr_num}</a> failed {MAX_RETRIS} times and auto-retry has "
                    f"stopped. Manual ledger reset required.</p>"
                    f"<p>Last error: {html_lib.escape(alert_excerpt(str(exc)))}</p>",
                )
            else:
                send_telegram_alert(f"⚠️ PR #{pr_num} review attempt {failures} failed: {exc}")

        finally:
            restore_repo_branch(repo_dir, pr_num=pr_num)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Main Host Orchestrator for PR-Triage Autopilot.")
    ap.add_argument("--repo", default="ffroliva/gflow-cli", help="owner/repo name")
    ap.add_argument("--repo-dir", required=True, help="path to local host clone")
    ap.add_argument(
        "--memory-dir",
        default=None,
        help=(
            f"council memory directory; defaults to ${MEMORY_DIR_ENV}, "
            f"else $XDG_DATA_HOME/{MEMORY_XDG_SUBDIR}"
        ),
    )
    args = ap.parse_args(argv)

    # Validate required credentials
    gh_token = (
        os.environ.get("GH_COMMENT_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
    )
    if not gh_token:
        err_msg = "Missing credentials. Requires GH_COMMENT_TOKEN, GITHUB_TOKEN, or GH_TOKEN."
        logger.error(err_msg)
        send_telegram_alert(f"🚨 PR-Triage Autopilot ALERT: {err_msg}")
        return 1

    auth_error = check_claude_auth()
    if auth_error:
        logger.error("Claude authentication unusable", reason=auth_error)
        send_telegram_alert(
            "🚨 PR-Triage Autopilot ALERT: Claude authentication unusable on host.\n"
            f"Reason: {auth_error}"
        )
        return 1

    engine = resolve_engine()

    repo_dir = Path(args.repo_dir).resolve()
    memory_dir = resolve_memory_dir(args.memory_dir)
    ledger_path = repo_dir / LEDGER_FILE

    # Acquire lock
    try:
        lock_file = open(LOCK_FILE_PATH, "w")
        acquire_lock(lock_file)
    except OSError:
        logger.info("Another autopilot instance is running. Exiting.")
        return 0

    logger.info("PR-Triage Autopilot iteration started", repo=args.repo, engine=engine)
    run_triage_cycle(args.repo, repo_dir, memory_dir, ledger_path, gh_token, engine=engine)
    logger.info("PR-Triage Autopilot iteration completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
