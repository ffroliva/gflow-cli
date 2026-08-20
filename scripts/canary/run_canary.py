#!/usr/bin/env python3
"""Nightly live-e2e canary — local scheduled run, published to a rolling issue (#502).

Hosted CI cannot run the live tiers: they need a real authenticated Chrome
profile, and Google bot-detection / reCAPTCHA / ToS make hosted auth infeasible.
So the canary runs on the maintainer's machine — where the warm profile already
lives — and publishes a sanitized result to GitHub.

Four states, and the canary **gates nothing** (a gate on a machine that might be
off is self-DoS):

    GREEN         every selected tier passed
    RED           auth probe OK but a $0 tier failed -> real drift/regression
    AUTH-EXPIRED  auth-shaped failure; session rot is EXPECTED, not a regression
    DEFERRED      profile precondition blocked it (lease held, engine mismatch)

Keeping AUTH-EXPIRED and DEFERRED out of RED is the whole point: RED must always
mean "code or Flow drifted", never "please re-login" or "you had Chrome open".
Otherwise the signal trains red-blindness and dies.

Scope: ``-m e2e_auth`` only ($0, no reCAPTCHA). Fast-follow adds ``e2e_scene``
($0). Credit tiers (e2e_image / e2e_video / smoke) stay strictly manual via
``/gflow:live-verify`` — this never spends credits unattended.

Publishing is sanitized for a public repo: SHA, counts, duration, failure class,
and failing test *names*. Never raw logs, profile paths, prompts, or signed URLs.

Usage:
    # dry run — execute for real, print the payload, touch nothing on GitHub
    python scripts/canary/run_canary.py --profile ffroliva --dry-run

    # real run (rolling issue must already exist; the canary never opens one)
    python scripts/canary/run_canary.py --profile ffroliva --issue 600

    # exercise the publish path for a state without waiting for the condition
    python scripts/canary/run_canary.py --simulate AUTH-EXPIRED --issue 600 --dry-run

Exit codes: always 0 unless the canary ITSELF broke (bad args, gh failure).
The Flow-side verdict lives in the issue, not in this process's exit code —
a scheduled task that reports failure by exiting non-zero just fills the
Windows event log with noise nobody reads.
"""

from __future__ import annotations

import argparse
import os
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
STATES = (GREEN, RED, AUTH_EXPIRED, DEFERRED)

# Profile-state preconditions: they fail closed BEFORE any browser starts, so
# nothing was ever exercised and the run says nothing about Flow. Both are
# ConfigurationError subclasses sharing exit 11 with everything else under it,
# so the class name is the discriminator — the exit code cannot be.
# ProfileEngineDowngradeError's own docstring names ProfileLockedError as its
# sibling in this class; a canary that reported either as RED would be claiming
# drift from a run that never reached Flow.
_PRECONDITION_MARKERS = (
    "ProfileLockedError",
    "Profile locked",
    "ProfileEngineDowngradeError",
)


@dataclass(frozen=True)
class Result:
    state: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    failing: tuple[str, ...] = ()


def classify(
    auth_ok_before: bool,
    pytest_rc: int | None,
    failure_text: str,
    auth_ok_after: bool | None = None,
) -> str:
    """Pure verdict function — the only place a state is decided.

    ``pytest_rc`` is None when the suite never ran (the pre-probe failed first).
    ``auth_ok_after`` is None when no post-probe was needed (nothing failed).

    Session rot is distinguished from drift by **re-probing after the run**, not
    by pattern-matching error names. The first live run proved why: an
    ``AuthExpiredError`` from the aisandbox upload path looks exactly like
    session rot, but the session probe still verified clean immediately
    afterwards — so it was a real divergence between two auth surfaces, and a
    name-matching classifier would have buried it as AUTH-EXPIRED. An
    auth-shaped failure whose session is *still valid* is drift, not rot.

    A hardcoded name list was also unmaintainable in the other direction: it had
    already missed ``AuthExpiredError`` and ``AisandboxAuthError`` on day one.
    The probe is a real signal and cannot fall out of date.
    """
    if not auth_ok_before:
        return AUTH_EXPIRED
    if pytest_rc == 0:
        return GREEN
    if any(marker in failure_text for marker in _PRECONDITION_MARKERS):
        return DEFERRED
    if auth_ok_after is False:
        # The session was healthy at the start and is not now: genuine rot.
        return AUTH_EXPIRED
    return RED


def _run(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False
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
    proc = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            markers,
            "--junitxml",
            str(JUNIT_PATH),
            "-q",
            "--no-header",
        ],
        env=_child_env(profile),
    )
    return proc.returncode, f"{proc.stdout}\n{proc.stderr}", time.monotonic() - started


def parse_junit(path: Path) -> tuple[int, int, int, tuple[str, ...]]:
    """Return (passed, failed, skipped, failing_test_names) from the JUnit XML."""
    if not path.exists():
        return 0, 0, 0, ()
    root = ET.parse(path).getroot()  # noqa: S314 - our own pytest output
    suites = root.iter("testsuite")
    total = failures = errors = skipped = 0
    failing: list[str] = []
    for suite in suites:
        total += int(suite.get("tests", 0))
        failures += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
        for case in suite.iter("testcase"):
            if case.find("failure") is not None or case.find("error") is not None:
                # Test NAMES are publishable; their failure text is not.
                failing.append(f"{case.get('classname', '')}::{case.get('name', '')}")
    failed = failures + errors
    return total - failed - skipped, failed, skipped, tuple(failing)


def preserve_evidence(stamp: str) -> Path | None:
    """Keep the failing JUnit XML so a RED stays triageable days later.

    ``JUNIT_PATH`` is overwritten every run, so without this a Monday red is
    gone by Wednesday. Learned from the first live run (#561): the only reason
    that failure could be investigated at all was that the file happened to
    still be on disk.

    Stays LOCAL and out of the issue — it carries raw tracebacks, which the
    sanitization contract keeps off a public repo. ``tmp/`` is gitignored.
    """
    if not JUNIT_PATH.exists():
        return None
    slug = stamp.replace(":", "").replace(" ", "-")
    kept = JUNIT_PATH.with_name(f"canary-red-{slug}.xml")
    kept.write_bytes(JUNIT_PATH.read_bytes())
    print(f"evidence preserved: {kept}")
    return kept


def render(result: Result, sha: str, markers: str, stamp: str) -> str:
    headline = {
        GREEN: "All selected $0 tiers passed.",
        RED: "A $0 tier failed while auth was healthy — real drift or regression.",
        AUTH_EXPIRED: "Session rot, not a regression. Re-login and the next run clears it.",
        DEFERRED: (
            "A profile precondition blocked the run (lease held, or a profile/engine "
            "Chromium mismatch). Neutral — nothing reached Flow."
        ),
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


def sync_to_develop() -> None:
    """Fast-forward the checkout to ``origin/develop`` — dedicated clone only.

    #502 wants the canary measuring a pinned checkout, not whatever is in a
    working tree. This REFUSES on any local modification rather than resetting
    over it: a nightly task that can silently destroy uncommitted work is a far
    worse bug than a stale canary. Point the scheduled task at a second clone.
    """
    dirty = _run(["git", "status", "--porcelain"]).stdout.strip()
    if dirty:
        raise SystemExit(
            "--pull refuses to run: the checkout has local modifications.\n"
            "Point the scheduled task at a DEDICATED clone, never your working tree."
        )
    if _run(["git", "fetch", "origin", "develop"]).returncode != 0:
        raise SystemExit("git fetch failed; canary not run")
    checkout = _run(["git", "checkout", "--force", "origin/develop"])
    if checkout.returncode != 0:
        raise SystemExit(f"git checkout failed: {checkout.stderr.strip()}")


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_E2E_PROFILE", ""))
    p.add_argument("--issue", type=int, default=int(os.environ.get("GFLOW_CANARY_ISSUE", 0)))
    p.add_argument("--markers", default="e2e_auth", help="pytest -m expression ($0 tiers only)")
    p.add_argument("--dry-run", action="store_true", help="execute, print payload, skip GitHub")
    p.add_argument("--simulate", choices=STATES, help="force a state to exercise publishing")
    p.add_argument(
        "--pull",
        action="store_true",
        help="fast-forward to origin/develop first; refuses if the tree is dirty",
    )
    args = p.parse_args()

    if args.pull and not args.simulate:
        sync_to_develop()

    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    sha = _run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip() or "unknown"

    if args.simulate:
        result = Result(
            state=args.simulate, failing=("tests::simulated",) if args.simulate == RED else ()
        )
    else:
        if not args.profile:
            raise SystemExit("--profile (or GFLOW_CLI_E2E_PROFILE) is required")
        if probe_auth(args.profile):
            rc, output, secs = run_tiers(args.profile, args.markers)
            passed, failed, skipped, failing = parse_junit(JUNIT_PATH)
            # Re-probe only when something failed and it was not lease
            # contention: the post-probe is what separates session rot from a
            # real divergence, and it costs ~45s we should not spend on a green.
            after: bool | None = None
            if rc != 0 and not any(m in output for m in _PRECONDITION_MARKERS):
                after = probe_auth(args.profile)
            state = classify(True, rc, output, after)
            result = Result(state, passed, failed, skipped, secs, failing)
        else:
            result = Result(classify(False, None, ""))

    if result.state == RED:
        preserve_evidence(stamp)

    body = render(result, sha, args.markers, stamp)
    if not args.issue and not args.dry_run:
        raise SystemExit("--issue (or GFLOW_CANARY_ISSUE) is required; the canary never opens one")
    publish(args.issue, result.state, body, stamp, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
