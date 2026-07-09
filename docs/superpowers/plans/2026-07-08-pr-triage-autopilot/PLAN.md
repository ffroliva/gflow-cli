# PR-Triage Autopilot Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature pr-triage-autopilot` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Implement an hourly `hermes-ops` autopilot that autonomously runs `/gflow:pr-council-review` against eligible external PRs on `ffroliva/gflow-cli` using a sandboxed Docker container, auto-posts reviews, and alerts via Telegram.

**Architecture:** 
- `scripts/autopilot/pr_triage_gate.py` (Stage 0): Deterministic pre-filter verifying author, draft status, diff sizes, and scanning for obvious prompt injections.
- `scripts/autopilot/Dockerfile.triage`: Ephemeral container image packing the `claude` CLI, run with restricted read-only volume mounts and network egress.
- `skills/pr-council-review/SKILL.md`: Gaining §9 autonomous resolutions for interactive gates (credit limits, memory, etc.).
- `scripts/autopilot/pr_triage_autopilot.py`: The hourly host orchestrator handling polling, Stage 1/council execution, posting comments via the host token boundary, Telegram alerts, and writing to `pr_triage_ledger.jsonl`.

**Predict verdict:** GO — confidence 9/10 (based on reference `dependabot-autopilot` pattern).

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Critical | Prompt injection executing commands on host VPS | Run reviews in ephemeral Docker sandbox with read-only code mounts, no write-scoped GitHub credentials, and network restrictions. |
| High | Unbounded LLM cost via commit churn or failed posts | Implement per-PR Stage-1 volume cap (default 3/day), calendar daily cap (default 5/day), and fail-count limit (cap at 3 retries, mark `FAILED_PERMANENT`). |
| Medium | Re-reviewing unchanged PRs | Key the audit ledger by `(pr, head_sha)` instead of just `pr` to skip already-reviewed commits. |

---

## File structure

### New files
```
scripts/autopilot/pr_triage_gate.py
  Stage 0 deterministic pre-filter logic
scripts/autopilot/pr_triage_autopilot.py
  Main host orchestrator script (triggers, sandbox executor, posting, ledger)
scripts/autopilot/Dockerfile.triage
  Ephemeral sandbox container definition
scripts/autopilot/run_sandboxed_review.sh
  Docker invocation wrapper
eval/pr_triage_fixtures.json
  Recorded/synthetic GitHub PR shapes for pre-filter testing
eval/pr_triage_eval.py
  100%-match assertion script for Stage 0
deploy/PR-TRIAGE-AUTOPILOT-OPS.md
  As-built operations runbook for the VPS
```

### Modified files
```
skills/pr-council-review/SKILL.md
  Add §9 Autonomous mode resolutions and structured verdict summary output
PLAN.md
  Add PR-Triage Autopilot Phase status
```

---

## Task 1 — Stage 0 Deterministic Pre-Filter (`pr_triage_gate.py`)

**What:** Implement the deterministic logic to identify eligible PRs, filter out drafts, bots, owner, oversized diffs, and obvious prompt injections.

**Files:**
- `scripts/autopilot/pr_triage_gate.py` — Pre-filter function and CLI wrapper
- `eval/pr_triage_fixtures.json` — Target test vectors
- `eval/pr_triage_eval.py` — Run gate logic over fixtures

**Steps:**
- [x] Define the `should_review(pr: dict) -> ShouldReviewResult` logic:
  - Exclude author `ffroliva`, bot authors, and draft PRs.
  - Detect PRs incorrectly targeting `main` base branch (route to `NEEDS-HUMAN` to post an automated request asking contributor to retarget to `develop` rather than silently skipping).
  - Exclude diff sizes $> 30$ files or $> 1500$ additions+deletions.
  - Scan title, body, and comments for regex-based injection patterns (e.g., "ignore previous instructions").
- [x] Record 10+ fixtures in `eval/pr_triage_fixtures.json` covering:
  - Valid PRs (`PROCEED`)
  - Drafts, owner, bots, and merged/closed PRs (`SKIP`)
  - Oversized diffs (`DEFERRED_SIZE`)
  - Injection matches (`NEEDS-HUMAN`)
- [x] Implement `eval/pr_triage_eval.py` asserting a 100% verdict match rate over the fixtures.

**Tests created (red):**
- [x] Run `python eval/pr_triage_eval.py` and verify all fixtures pass.

---

## Task 2 — §9 Autonomous Mode in `pr-council-review` Skill

**What:** Add non-interactive resolutions to the review skill when run under `hermes` orchestration.

**Files:**
- `skills/pr-council-review/SKILL.md` — Modify review instructions

**Steps:**
- [x] Add `## 9. Autonomous Mode` section:
  - Instruct the agent to bypass the credit-spend gate (log as open item).
  - Bypassing memory actions (report suggestions but do not auto-write).
  - Forbid custom/unstructured output formats.
  - Specify printing a single structured line to stdout at the end: `VERDICT: [verdict] | MUST-FIX: [count] | URL: [pr_url]` for the host orchestrator to parse.
  - Set a content constraint on the review text itself: do not echo potential injection text, disclose environment details, or respond to meta-queries.

---

## Task 3 — Ephemeral Docker Sandbox (`Dockerfile.triage`)

**What:** Package the review environment in a secure, ephemeral container.

**Files:**
- `scripts/autopilot/Dockerfile.triage` — Docker image specification
- `scripts/autopilot/run_sandboxed_review.sh` — Execution wrapper shell script

**Steps:**
- [x] Define `Dockerfile.triage`:
  - Base on a lightweight image (e.g., node/python-slim).
  - Install dependencies (including the `gh` CLI required by the review skill) and Claude CLI.
  - Configure a non-root runtime user with a writable home directory `/home/nonroot` (e.g. via tmpfs mount) to allow Claude CLI internal state writes.
- [x] Write `run_sandboxed_review.sh`:
  - Bind-mount the fetched clone of `/opt/gflow-cli` as **read-only**.
  - Mount only the project-specific memory namespace (`C:\Users\ffrol\.claude\projects\C--development-github-gflow-cli\memory` or its VPS equivalent) read-only, explicitly avoiding broad mounting of `/opt/experience-vault` to prevent cross-project exfiltration.
  - Enforce restricted network egress (block everything except `api.anthropic.com` and `github.com`), explicitly permitting DNS (UDP/TCP port 53) for host name resolution.
  - Restrict write capabilities (do not mount write-scoped GitHub tokens inside the container; pass the read-only auth token to `gh` safely).

---

## Task 4 — Main Orchestration Loop (`pr_triage_autopilot.py`)

**What:** Implement the hourly runner orchestrating Stage 0, Stage 1 pre-eval, sandbox execution, comment posting, Telegram notifications, and the audit ledger.

**Files:**
- `scripts/autopilot/pr_triage_autopilot.py` — Orchestrator entrypoint
- `tests/autopilot/test_pr_triage_autopilot.py` — Core tests

**Steps:**
- [x] Implement hourly lock file acquisition (`flock` or lockfile) to prevent concurrent cron ticks.
- [x] Fetch the list of open PRs via `gh pr list --json`.
- [x] Run Stage 0 gate. If qualified, fetch `pull/<N>/head` on the host machine.
- [x] Run Stage 1 pre-evaluation inside the container (cheap `claude -p` call). If `PROCEED`/`TRIVIAL`, proceed to the full review.
- [x] Launch the sandboxed Docker container executing the `pr-council-review` skill.
- [x] Capture the container's stdout, parse the structured summary line, and post the review comment from the host using `gh pr comment` (via the host's comment-only bot PAT).
- [x] Log outcomes atomically to `pr_triage_ledger.jsonl`.
- [x] Implement retry limits: increment `fail_count` on crashes; limit to 3 retries before marking `FAILED_PERMANENT` and sending a Telegram alert.
- [x] Implement the calendar daily cap (default 5 reviews/day).
- [x] Notify Telegram (Flavio) of every outcome (MERGE/FLAG/SKIP/ERROR).
- [x] Implement unit tests in `test_pr_triage_autopilot.py` ensuring that Docker and `gh` CLI subprocess calls are mocked/stubbed so tests pass cleanly on systems without Docker or `gh` CLI installed.

---

## Task 5 — Operations Runbook & Shim (`PR_TRIAGE_AUTOPILOT-OPS.md`)

**What:** Document deployment and operational management on the VPS.

**Files:**
- `deploy/PR-TRIAGE-AUTOPILOT-OPS.md` — Deployment runbook

**Steps:**
- [ ] Document the VPS installation, environment variables (`TELEGRAM_USER_ID`, `GH_COMMENT_TOKEN`), and symlink setups.
- [ ] Detail log locations, status checking (`hermes cron list`), and the kill-switch procedure (pausing cron / pausing docker daemon).
- [ ] Record the golden-task baselines setup.

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green in the worktree
- [ ] Pure-function fixture test (`pr_triage_eval.py`) passes 100%
- [ ] Dry-run mode verified against actual open PRs without making live comments
- [ ] `CHANGELOG.md` updated
- [ ] Plan written to `docs/superpowers/plans/2026-07-08-pr-triage-autopilot/PLAN.md`
