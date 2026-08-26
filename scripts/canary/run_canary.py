#!/usr/bin/env python3
"""Nightly live-e2e canary — local scheduled run, published to a rolling issue (#502).

Hosted CI cannot run the live tiers: they need a real authenticated Chrome
profile, and Google bot-detection / reCAPTCHA / ToS make hosted auth infeasible.
So the canary runs on the maintainer's machine — where the warm profile already
lives — and publishes a sanitized result to GitHub.

Four states, and the canary **gates nothing** (a gate on a machine that might be
off is self-DoS):

    GREEN         every selected tier passed AND at least one test actually ran
    RED           auth was healthy but a $0 tier failed -> real drift/regression
    AUTH-EXPIRED  session rot; expected maintenance, not a regression
    DEFERRED      nothing conclusive ran (profile precondition, or all skipped)

Keeping AUTH-EXPIRED and DEFERRED out of RED is the whole point: RED must always
mean "code or Flow drifted", never "please re-login" or "you had Chrome open".
Otherwise the signal trains red-blindness and dies. The converse matters just as
much — see ``_PRECONDITION_RE`` for why a real failure must never be *demoted*
to DEFERRED.

Scope: ``-m e2e_auth`` only ($0, no reCAPTCHA). Credit tiers are refused outright
(see ``_CREDIT_MARKERS``) — a promise in a docstring is not enforcement.

Publishing is sanitized for a public repo: SHA, counts, duration, failure class,
and failing test *base* names. Never raw logs, profile paths, prompts, signed
URLs, or parametrize ids.

Usage:
    # dry run — execute for real, print the payload, touch nothing on GitHub
    python scripts/canary/run_canary.py --profile ffroliva --dry-run

    # real run (rolling issue #559 must already exist; the canary never opens one)
    python scripts/canary/run_canary.py --profile ffroliva --issue 559

Exit codes: always 0 unless the canary ITSELF broke (bad args, gh failure).
The Flow-side verdict lives in the issue, not in this process's exit code —
a scheduled task that reports failure by exiting non-zero just fills the
Windows event log with noise nobody reads.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
JUNIT_PATH = REPO_ROOT / "tmp" / "canary-junit.xml"

GREEN, RED, AUTH_EXPIRED, DEFERRED = "GREEN", "RED", "AUTH-EXPIRED", "DEFERRED"

# Wall-clock cap on the tier. Task Scheduler's own limit is an hour, but hitting
# THAT kills the process before it can publish — a hung browser would produce
# silence, which reads identically to "the machine was off". Cap here instead so
# a hang still reports.
_TIER_TIMEOUT_S = 45 * 60

# Tiers that spend real money. #502 is explicit that credit tiers stay manual;
# relying on the default value of --markers to enforce that is not enforcement.
_CREDIT_MARKERS = ("e2e_image", "e2e_video", "e2e_batch", "e2e_character", "smoke")

# Profile-state preconditions: they fail closed BEFORE any browser starts, so
# nothing was exercised and the run says nothing about Flow.
#
# This MUST anchor on the raised-exception line, never a bare substring. Two
# tests in the e2e_auth tier mention ProfileLockedError in ordinary source — one
# in an assertion message, one in a *comment* — and pytest echoes the failing
# function's source into the traceback. A substring match therefore demoted a
# genuine regression in those tests to DEFERRED, i.e. published real drift under
# the one label that says "ignore me". Found by council review of PR #560.
_PRECONDITION_RE = re.compile(
    r"^E\s+[\w.]*(ProfileLockedError|ProfileEngineDowngradeError):",
    re.MULTILINE,
)


@dataclass(frozen=True)
class Result:
    state: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    failing: tuple[str, ...] = ()
    note: str = ""


def is_precondition_failure(text: str) -> bool:
    """True when pytest output carries a RAISED profile-precondition exception."""
    return _PRECONDITION_RE.search(text) is not None


def classify(
    auth_ok_before: bool,
    pytest_rc: int | None,
    failure_text: str,
    auth_ok_after: bool | None = None,
    passed: int = 1,
) -> str:
    """Pure verdict function — the only place a state is decided.

    ``pytest_rc`` is None when the suite never ran (the pre-probe failed first).
    ``auth_ok_after`` is None when no post-probe was needed (nothing failed).
    ``passed`` defaults to 1 so callers testing only the failure arms need not
    supply it; the zero case is what the green arm guards against.

    Session rot is distinguished from drift by **re-probing after the run**, not
    by pattern-matching error names. An ``AuthExpiredError`` from the aisandbox
    upload path looks exactly like session rot, but if the session still
    verifies clean seconds later it was a real divergence between two auth
    surfaces. An auth-shaped failure whose session is *still valid* is drift.

    A green run that executed nothing is not green: every e2e test skips when the
    profile directory is missing, and pytest exits 0 on an all-skipped run. That
    would pin the dashboard to GREEN forever after a profile move.
    """
    if not auth_ok_before:
        return AUTH_EXPIRED
    if pytest_rc == 0:
        return GREEN if passed > 0 else DEFERRED
    if is_precondition_failure(failure_text):
        return DEFERRED
    if auth_ok_after is False:
        # Healthy at the start and not now: genuine rot.
        return AUTH_EXPIRED
    return RED


def _run(
    cmd: list[str], env: dict[str, str] | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False, timeout=timeout
    )


def _child_env(profile: str) -> dict[str, str]:
    env = dict(os.environ)
    env["GFLOW_CLI_E2E_PROFILE"] = profile
    # FORCE_COLOR leaks ANSI into plain-text assertions and reds ~26 CLI tests.
    env.pop("FORCE_COLOR", None)
    return env


def probe_auth(profile: str) -> bool:
    """`gflow auth status` probes the Flow session endpoint — no browser, no credits."""
    proc = _run([sys.executable, "-m", "gflow_cli", "auth", "status", "--profile", profile])
    return proc.returncode == 0


def run_tiers(profile: str, markers: str) -> tuple[int, str, float]:
    """Run the selected $0 tiers. Returns (returncode, combined_output, seconds)."""
    JUNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    # junit_logging=all folds pytest's captured stdout/log into the JUnit, so a
    # preserved RED carries the run's structlog output and not just a traceback.
    # An auth 401's traceback cannot say WHICH cookie carrier failed; the
    # `client.context_cookie_state` line (the #222 diagnostic) answers exactly
    # that and is otherwise captured and discarded. The failure only reproduces
    # unattended, so a red that drops it is untriageable by construction
    # (#559/#561). The JUnit already stays local — it already carries raw
    # tracebacks — so this exposes nothing the sanitization contract covers.
    argv = [
        sys.executable, "-m", "pytest",
        "-m", markers,
        "--junitxml", str(JUNIT_PATH),
        "-o", "junit_logging=all",
        "-q", "--no-header",
    ]  # fmt: skip
    try:
        proc = _run(argv, env=_child_env(profile), timeout=_TIER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        # Report the hang rather than letting Task Scheduler kill us silently.
        return 1, "E   CanaryTimeout: tier exceeded the wall-clock cap", time.monotonic() - started
    return proc.returncode, f"{proc.stdout}\n{proc.stderr}", time.monotonic() - started


def parse_junit(path: Path) -> tuple[int, int, int, tuple[str, ...]]:
    """Return (passed, failed, skipped, failing_test_names) from the JUnit XML."""
    if not path.exists():
        return 0, 0, 0, ()
    root = ET.parse(path).getroot()  # noqa: S314 - our own pytest output
    total = failures = errors = skipped = 0
    failing: list[str] = []
    for suite in root.iter("testsuite"):
        total += int(suite.get("tests", 0))
        failures += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
        for case in suite.iter("testcase"):
            if case.find("failure") is not None or case.find("error") is not None:
                # Test NAMES are publishable; the parametrize id is NOT a name.
                # pytest embeds raw param values in `name`, so a case id built
                # from a profile directory would carry an absolute filesystem
                # path — or an email, or a prompt — straight into a public issue.
                # Reproduced, not theorised. Dropping the `[...]` suffix keeps the
                # test findable and closes the channel entirely.
                base = case.get("name", "").split("[", 1)[0]
                failing.append(f"{case.get('classname', '')}::{base}")
    failed = failures + errors
    return total - failed - skipped, failed, skipped, tuple(dict.fromkeys(failing))


def preserve_evidence(stamp: str) -> Path | None:
    """Keep the failing JUnit so a RED stays triageable days later.

    ``JUNIT_PATH`` is overwritten every run, so without this a Monday red is gone
    by Wednesday. Stays LOCAL and out of the issue — it carries raw tracebacks,
    which the sanitization contract keeps off a public repo. ``tmp/`` is
    gitignored.
    """
    if not JUNIT_PATH.exists():
        return None
    slug = stamp.replace(":", "").replace(" ", "-")
    kept = JUNIT_PATH.with_name(f"canary-red-{slug}.xml")
    shutil.copyfile(JUNIT_PATH, kept)
    print(f"evidence preserved: {kept}")
    return kept


def render(result: Result, sha: str, markers: str, stamp: str) -> str:
    headline = {
        GREEN: "All selected $0 tiers passed.",
        RED: "A $0 tier failed while auth was healthy — real drift or regression.",
        AUTH_EXPIRED: "Session rot, not a regression. Re-login and the next run clears it.",
        DEFERRED: "Nothing conclusive ran. Neutral — this says nothing about Flow.",
    }[result.state]

    lines = [
        f"### {result.state} — {stamp}",
        "",
        headline,
        "",
        f"| commit | `{sha}` |",
        "| --- | --- |",
        f"| markers | `{markers}` |",
        f"| passed | {result.passed} |",
        f"| failed | {result.failed} |",
        f"| skipped | {result.skipped} |",
        f"| duration | {result.duration_s:.1f}s |",
    ]
    if result.note:
        lines += ["", f"**Reason:** {result.note}"]
    if result.failing:
        lines += ["", "**Failing:**", ""]
        lines += [f"- `{name}`" for name in result.failing]
    if result.state == RED:
        lines += ["", "> Canary gates nothing. Triage at your convenience."]
    return "\n".join(lines)


def publish(issue: int, state: str, body: str, stamp: str, dry_run: bool) -> None:
    title = f"[{state}] gflow nightly canary — {stamp}"
    if dry_run:
        print(f"--- DRY RUN: would set title ---\n{title}\n--- would comment ---\n{body}\n")
        return
    # Never `gh issue create`: issue spam trains red-blindness (#502).
    edit = _run(["gh", "issue", "edit", str(issue), "--title", title])
    if edit.returncode != 0:
        raise SystemExit(f"gh issue edit failed: {edit.stderr.strip()}")
    comment = _run(["gh", "issue", "comment", str(issue), "--body", body])
    if comment.returncode != 0:
        raise SystemExit(f"gh issue comment failed: {comment.stderr.strip()}")
    print(f"published {state} to issue #{issue}")


_REEXEC_GUARD = "GFLOW_CANARY_REEXECED"
_SCRIPT_PATH = Path(__file__).resolve()


def _script_digest(path: Path = _SCRIPT_PATH) -> str:
    """SHA-256 of this script, or "" when it cannot be read.

    Unreadable degrades to "unchanged" deliberately: updating one night late is a
    nuisance, re-running forever is an outage.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _maybe_rerun_after_pull(digest_before: str, digest_now: str | None = None) -> None:
    """Re-run this script once if the pull just changed it.

    `--pull` updates this file and then keeps executing the copy Python loaded at
    startup, so a runner change only takes effect the FOLLOWING night. #572 added
    `-o junit_logging=all` to make a RED carry the structlog line that decides
    #561; the next run pulled it and still produced a log-less RED, because the
    pre-pull copy was the one running. The failure is invisible — it reads as "the
    fix did not work".

    Uses `subprocess.run`, never `os.execv`: on Windows execv does not replace the
    process image (the CRT spawns a new process and terminates this one), so the
    PID changes and Task Scheduler can read the task as finished. A supervising
    process keeps the scheduler's view intact and propagates the child's code.

    Two independent loop guards — either is sufficient on its own:
      1. `_REEXEC_GUARD` in the child's env, checked first here.
      2. The digest comparison: no content change, no re-run.
    """
    if os.environ.get(_REEXEC_GUARD):
        return
    now = _script_digest() if digest_now is None else digest_now
    if not now or not digest_before or now == digest_before:
        return
    print(f"canary.reexec_after_pull: {digest_before[:12]} -> {now[:12]}")
    child_env = dict(os.environ)
    child_env[_REEXEC_GUARD] = "1"
    proc = subprocess.run(  # noqa: S603 - our own script, fixed interpreter
        [sys.executable, str(_SCRIPT_PATH), *sys.argv[1:]],
        env=child_env,
        check=False,
    )
    raise SystemExit(proc.returncode)


def sync_to_develop() -> str | None:
    """Fast-forward the checkout to ``origin/develop`` and refresh deps.

    Returns None on success, or a human-readable reason the sync was refused —
    the caller publishes that as DEFERRED rather than dying silently. A
    scheduled task that exits before publishing is indistinguishable from a
    machine that was switched off.

    REFUSES on any local modification rather than resetting over it: a nightly
    task that can destroy uncommitted work is a far worse bug than a stale
    canary. Point the task at a dedicated clone.

    Deps are re-synced too — pulling source without the lockfile's dependency
    tree turns a routine bump on develop into an import error, i.e. a false RED.
    """
    if _run(["git", "status", "--porcelain"]).stdout.strip():
        return "checkout has local modifications; point the task at a dedicated clone"
    if _run(["git", "fetch", "origin", "develop"]).returncode != 0:
        return "git fetch origin develop failed"
    if _run(["git", "checkout", "--force", "origin/develop"]).returncode != 0:
        return "git checkout origin/develop failed"
    if _run(["uv", "sync", "--quiet"]).returncode != 0:
        return "uv sync failed after pull; dependency tree may be stale"
    return None


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_E2E_PROFILE", ""))
    p.add_argument("--issue", type=int, default=int(os.environ.get("GFLOW_CANARY_ISSUE", 0)))
    p.add_argument("--markers", default="e2e_auth", help="pytest -m expression ($0 tiers only)")
    p.add_argument("--dry-run", action="store_true", help="execute, print payload, skip GitHub")
    p.add_argument(
        "--pull",
        action="store_true",
        help="fast-forward to origin/develop and re-sync deps; refuses a dirty tree",
    )
    args = p.parse_args()

    spending = [m for m in _CREDIT_MARKERS if m in args.markers]
    if spending:
        raise SystemExit(
            f"--markers selects credit-spending tiers {spending}. The canary never spends "
            "credits unattended (#502); run those manually via /gflow:live-verify."
        )
    if not args.profile:
        raise SystemExit("--profile (or GFLOW_CLI_E2E_PROFILE) is required")
    if not args.issue and not args.dry_run:
        raise SystemExit("--issue (or GFLOW_CANARY_ISSUE) is required; the canary never opens one")

    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    digest_before = _script_digest() if args.pull else ""
    refused = sync_to_develop() if args.pull else None
    if args.pull and refused is None:
        # Only after a SUCCESSFUL pull — a refused sync changed nothing, and
        # re-running on a refusal would just repeat the refusal.
        _maybe_rerun_after_pull(digest_before)
    sha = _run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip() or "unknown"

    if refused:
        result = Result(state=DEFERRED, note=refused)
    elif probe_auth(args.profile):
        rc, output, secs = run_tiers(args.profile, args.markers)
        passed, failed, skipped, failing = parse_junit(JUNIT_PATH)
        # Re-probe only when something failed and it was not a profile
        # precondition: the post-probe separates session rot from real
        # divergence, and it costs ~45s we should not spend on a green.
        after: bool | None = None
        if rc != 0 and not is_precondition_failure(output):
            after = probe_auth(args.profile)
        state = classify(True, rc, output, after, passed)
        note = (
            "every selected test skipped — nothing exercised Flow"
            if state == DEFERRED and rc == 0
            else ""
        )
        result = Result(state, passed, failed, skipped, secs, failing, note)
    else:
        result = Result(state=classify(False, None, ""))

    if result.state == RED:
        preserve_evidence(stamp)

    publish(args.issue, result.state, render(result, sha, args.markers, stamp), stamp, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
