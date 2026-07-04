# PR-triage autopilot — design

> Status: **approved design** (2026-07-04). Next step: `writing-plans` → implementation plan.
> Scope: an hourly hermes-ops autopilot that autonomously runs `/gflow:pr-council-review` against every open PR from a non-owner, non-bot, non-draft author on `ffroliva/gflow-cli`, and auto-posts the council's report as a PR comment. Sibling of the existing `dependabot-autopilot`; specializes the shared skeleton in hermes-ops' `autopilot-core-design.md`. Issue-triage (the third planned specialization) is explicitly **out of scope** here — captured as backlog (see "Out of scope").

## Problem

External contributions currently go unreviewed until a human happens to notice them. The triggering incident: PR #237 (an external fork PR) sat unreviewed for ~6 hours before being manually discovered and reviewed in this session — not because any automation failed, but because no automation existed to catch it. The existing `dependabot-autopilot` explicitly only classifies dependabot PRs (`SKIP` for everyone else) and runs once daily; even when it does mention human PRs, it's a terse "skipped N human PR(s): #A, #B" footer with no title, no CI status, no risk signal.

Manually reviewing PR #237 in this session also surfaced two confirmed bugs (`AssetLookup` field mismatch causing a `TypeError` on every call to the new code path; a Playwright selector broken by unescaped apostrophes) that CI's own gates never reached, because CI died earlier at a trivial `ruff format` failure. A systematic per-PR review closes this gap.

## Key environmental constraints

- **Hermes's own agent cannot run the council.** Hermes routes all its own model calls through a VPS-local, free-only LLM pool (`freellmapi`) with no billing path. `pr-council-review` requires a real Claude-Code harness (parallel `Agent`/`Skill` tool dispatch across 5-13 sub-agents) that freellmapi cannot provide.
- **The VPS already has what's needed to bridge this.** `claude` CLI v2.1.181 is already installed and OAuth-authenticated under the `hermes` user (verified live 2026-07-04 via `claude -p "..."`) — no new API key, no new billing path. Hermes shells out to it as a subprocess; hermes remains the control plane (trigger, gate, ledger, Telegram), `claude` CLI does the actual review.
- **Claude Code memory is keyed by working-directory path, not repo identity.** A fresh `/opt/gflow-cli` clone on the VPS starts with an *empty* memory namespace — none of gflow-cli's ~150 accumulated memory files are visible there unless synced.
- **Fork-PR secrets exposure is a real, actively-exploited attack class**, not a theoretical one (confirmed via Orca Security's documented RCE/exfiltration incidents from misconfigured `pull_request_target` in high-profile OSS repos). This shapes both the SonarCloud decision and the sandboxing requirement below.
- **All hermes-managed projects live under `/opt/<name>`**, never a user home directory (`/opt/hermes`, `/opt/hermes-ops`, `/opt/experience-vault` — and now `/opt/gflow-cli`).

## Two-layer architecture (unchanged from the existing pattern)

- **hermes-ops = orchestration** (trigger, gate, ledger, Telegram, sandboxing).
- **gflow-cli = domain** (`skills/pr-council-review/SKILL.md`, gaining a new §9 "Autonomous mode").

hermes calls into gflow-cli's skill; gflow-cli never imports hermes. The skill runs identically whether invoked interactively (a human, this session) or autonomously (hermes) — only the interactive-gate resolutions differ, defined in §9 itself so there is one canonical protocol, not a forked copy (same rationale the skill's existing §8 branch-mode gives for being a mode, not a sibling skill).

## Architecture — data flow

```
hermes cron "pr-triage-autopilot" (hourly)
  │
  ├─ gh pr list --repo ffroliva/gflow-cli --state open --json number,author,isDraft,headRefOid,
  │    additions,deletions,changedFiles,statusCheckRollup,comments
  │
  ├─ Stage 0 — cheap deterministic gate (pr_triage_gate.py, no LLM, free):
  │    author != 'ffroliva'  AND  not author.is_bot  AND  not isDraft
  │    AND all CI checks finished (no PENDING/IN_PROGRESS)
  │    AND diff size within cap (default: ≤30 files AND ≤1500 lines changed;
  │        else → ledger `DEFERRED_SIZE`, Telegram-notify, skip)
  │    AND (pr, head_sha) not already in pr_triage_ledger.jsonl
  │
  ├─ Stage 1 — cheap single-agent pre-evaluation (one non-parallel `claude -p` call,
  │    reads diff + title + body + existing comments — NOT the full council):
  │    verdict ∈ {PROCEED, TRIVIAL, OBVIOUS-JUNK, NEEDS-HUMAN}
  │    includes an injection-pattern scan (see Security)
  │    only PROCEED/TRIVIAL continue; OBVIOUS-JUNK/NEEDS-HUMAN → ledger + Telegram-notify, no post
  │
  ├─ For each PROCEED/TRIVIAL PR, inside a locked-down Docker container (see Security):
  │    1. cd /opt/gflow-cli && git fetch origin pull/<N>/head
  │    2. claude -p "/gflow:pr-council-review <N>"  — §9 autonomous mode:
  │         - PROCEED → full 5-13 dimension council
  │         - TRIVIAL → reduced dimension set (D1+D2 only)
  │         - fixed resolutions for every interactive gate (see §9 below)
  │         - auto-posts the report as a PR comment on completion
  │         - prints one structured summary line to stdout
  │    3. parse summary line:
  │         success → append {pr, head_sha, verdict, ts} to ledger; Telegram-notify
  │         failure (claude crash, git fetch fail, post fail) → do NOT ledger (retry next tick);
  │           Telegram-notify an explicit error line
  │
  └─ Daily cap: stop at N (default 5) new full-council reviews per calendar day;
       remaining qualifying PRs are deferred to tomorrow's tick with a Telegram note.
```

## Stage 0 — deterministic pre-filter

`scripts/autopilot/pr_triage_gate.py`, mirrors `dependabot_gate.py`'s pure-function-plus-fixtures shape:

```python
def should_review(pr: dict) -> ShouldReviewResult:
    # author != owner, not bot, not draft, CI finished,
    # changedFiles <= 30 and (additions + deletions) <= 1500,
    # (pr, head_sha) not in ledger
    ...
```

Unit-tested via fixtures (`eval/pr_triage_fixtures.json` + a fixture-eval script), same pattern as `dependabot_gate.py`.

## Stage 1 — cheap pre-evaluation

A single, non-parallel `claude -p` call (materially cheaper than the full council) reads the diff, title, body, and existing comments, and returns:

| Verdict | Meaning | Action |
|---|---|---|
| `PROCEED` | Substantive, in-scope, legitimate contribution | Full council (all applicable D1-D13) |
| `TRIVIAL` | e.g. a one-line typo/docs fix | Reduced council (D1+D2 only) |
| `OBVIOUS-JUNK` | Spam/vandalism/nonsense diff | Skip entirely, Telegram-notify only, **no auto-post** (auto-commenting on spam invites more bad-faith engagement) |
| `NEEDS-HUMAN` | Ambiguous, or an injection attempt was detected in the PR content | Defer, Telegram-notify, **no auto-post** |

This is the actual lever on LLM cost: most spend concentrates on PRs worth reviewing, not every PR unconditionally.

## Security — prompt injection & privilege isolation

Every reviewer sub-agent reads fully untrusted, attacker-controlled content (PR title/body/comments/diff, including comments embedded in the submitted code). This is broader than "should we read comments" — it's inherent to reviewing external contributions at all. It is materially higher-stakes in the autonomous path than the interactive path: no human is watching in real time, and the `hermes` user on the VPS is root-trusted with **passwordless sudo** (per hermes' own `USER.md`). A successful injection's blast radius is not "one bad PR comment" — it is potentially the whole VPS.

Mitigations (all required, not optional given that privilege level):

1. **Docker sandbox.** The actual `claude` CLI review subprocess (Stage 1 and the full council) runs inside a container on the existing VPS Docker install (`hermes` already runs containers — no new machine, no new infrastructure): non-root user inside the container, no SOPS secrets mounted, no other `/opt/` project mounted, only the `/opt/gflow-cli` clone (or a fetched copy), no Docker socket inside (prevents container escape), network egress restricted to `api.anthropic.com`/`github.com`.
2. **Read-only tool scope for autonomous-mode reviewer sub-agents.** No general `Bash`, no write access — Read/Grep/Glob/`git show`/`gh`-read-only only. The only thing with write/post privilege is the top-level orchestrator's final `gh pr comment` call, never a sub-agent acting mid-review.
3. **Explicit injection-awareness in every dispatched prompt** — PR content is untrusted external input, not instructions; embedded directives ("ignore previous instructions", "mark as safe", etc.) are a prompt-injection attempt to be flagged as a security finding, never acted on.
4. **Injection-pattern pre-scan** as part of Stage 1 — an obvious injection attempt routes to `NEEDS-HUMAN` rather than auto-proceeding.

## SonarCloud for fork PRs

`ci.yml`'s `sonar` job already explicitly skips fork PRs (`github.event.pull_request.head.repo.full_name != github.repository`) because GitHub Actions does not inject repository secrets into fork-triggered `pull_request` runs — a platform-level trust boundary, not a config gap. The two ways to actually enable it are `pull_request_target` (confirmed real attack surface — Orca Security has documented RCE/exfiltration incidents from exactly this misconfiguration in other OSS repos) or a safe two-workflow split (`pull_request` builds without secrets → `workflow_run` runs the Sonar scan against the artifact without executing the fork's code). Given the autopilot's whole purpose is reviewing potentially-adversarial external contributions, **§9 autonomous mode treats a skipped/missing SonarCloud check on a fork PR as an informational note, not a blocker.** The safe two-workflow split remains available as independent future work, out of scope here.

## Ledger & idempotency

`pr_triage_ledger.jsonl` under `HERMES_HOME`, append-only, keyed by **`(pr, head_sha)`** — not just `pr` (unlike the dependabot ledger). A new commit on a previously-reviewed PR is a new SHA, so it gets a fresh review; an unchanged PR is never re-reviewed. Verdicts `DEFERRED_SIZE`, `OBVIOUS-JUNK`, and `NEEDS-HUMAN` are also ledgered (by SHA) so they don't re-notify every hour, but a human can still act on them out of band (manually running `/gflow:pr-council-review <N>` from any Claude Code session, exactly as done for PR #237 this session).

**Retry-on-failure, not skip-on-failure**: a ledger entry is only written on a *successful* post. A `claude` CLI crash, a failed `git fetch origin pull/<N>/head`, or a failed `gh pr comment` post all leave the PR un-ledgered so the next hourly tick retries — paired with an explicit Telegram error notification so failures are never silent.

## Memory sync

One-way: your local machine → the VPS, never the reverse. Your local memory directory remains the single source of truth; the VPS's Claude Code namespace at `/opt/gflow-cli` is a read-only consumer of the latest snapshot. If an autonomous review surfaces something worth remembering long-term, it is reported (Telegram / PR comment) for you to add yourself in a future session — never auto-written back, consistent with the "never auto-apply memory edits" rule already in the interactive gate defaults below. Sync mechanism (manual push after memory-heavy sessions, or a session-end hook) is left to the implementation plan; slight staleness is acceptable since memory is already documented as point-in-time observation, not live state.

## `pr-council-review` §9 — Autonomous mode (new)

Fixed resolutions replacing every interactive gate, for when hermes invokes the skill unattended:

| Interactive gate (existing protocol) | Autonomous-mode resolution |
|---|---|
| §0 step 4, draft-PR confirmation | N/A — drafts are filtered out upstream by Stage 0 |
| §5 step 6, live-verify credit-spend gate | Always skip; never spend Flow/Veo credits; note as an open item in the report |
| §5 step 7, memory-action gate | Report the suggested memory action only; never auto-apply |
| §5 step 8, YELLOW-dismiss escape valve | Never auto-dismiss; report YELLOW as-is |
| §6, final "How to proceed" `AskUserQuestion` | Omitted — replaced by auto-posting the assembled report as a PR comment via `gh pr comment` |
| SonarCloud required-gate (command-level instruction) | Fork PR + skipped/missing SonarCloud check → informational note, not a blocking condition |

Also defines: the machine-parseable one-line structured summary printed at the end of a run (verdict + must-fix count + PR URL, or an error marker) that `pr_triage_autopilot.py` greps for instead of parsing free-form markdown; the reduced D1+D2-only dimension set for `TRIVIAL` verdicts; and the injection-awareness + read-only-tool-scope requirements from the Security section above as mandatory parts of every dispatched sub-agent prompt in this mode.

## Error handling

| Failure | Handling |
|---|---|
| `claude` CLI crash/timeout/bad exit | No ledger write (retry next tick); Telegram error notification |
| `git fetch origin pull/<N>/head` fails | Same — no ledger write, retry, notify |
| Review succeeds but `gh pr comment` post fails | Same — §9 must surface `POST_FAILED` in its summary line rather than reporting success |
| Stale/missing memory sync | Not a failure — degraded grounding only, silently accepted |

## Safety valve — daily cap

Default 5 new full-council reviews per calendar day. A burst of PRs (spam, mass low-effort contributions) is reviewed up to the cap; the rest are deferred to tomorrow's tick with an explicit Telegram note (`⏸️ daily review cap (5) reached — N PR(s) deferred: #a, #b, ...`) rather than silently processing an unbounded queue. Bounds both GitHub API usage and Claude usage exposure in a pathological scenario — independently validated during design research (a public account of a competitor's usage-based-billing bot producing a surprise ~$200 bill). Pausing the whole autopilot is just disabling the hermes cron job, same as any other job.

## Testing

- `pr_triage_gate.py`: pure-function unit tests via fixtures (mirrors `dependabot_gate.py` / `eval/pr_fixtures.json`).
- `pr_triage_autopilot.py`: a dry-run mode (mirrors dependabot's) that logs what *would* be reviewed/posted without invoking `claude` or `gh pr comment` — safe to test against real current open PRs without side effects.
- Live-fire: one real end-to-end test against a low-stakes PR before enabling the hourly cron for real (not PR #237, which already carries a manual review comment from this session).
- Before implementation is considered complete: an adversarial review pass against this spec itself (fresh agents, no attachment to the design) explicitly hunting for gaps — given this runs autonomously, spends real compute, and posts to GitHub unattended.

## Prior-art check (informs, doesn't change, the above)

Quick research against GitHub Copilot code review, CodeRabbit, Qodo PR-Agent (open source, closest analog), Greptile, and Sourcery, done 2026-07-04:

- Auto-triggering a review on every external PR is standard practice (GitHub Copilot's own ruleset-based auto-trigger) — validates the hourly-poll approach as unremarkable.
- The fork-secrets/`pull_request_target` risk is independently confirmed as a real, actively-exploited attack class (Orca Security), not just self-reasoned — validates the SonarCloud decision above.
- `pr-council-review`'s existing false-positive-filter step and this design's daily cap are both independently validated: noise-reduction is a marketed differentiator for Greptile/Sourcery, and a public account of a competitor's surprise billing spike directly validates the cap.
- Two deltas identified and deliberately deferred to backlog (below): CodeRabbit's incremental-re-review-on-new-commits pattern (cost optimization), and Qodo PR-Agent's on-demand comment-trigger pattern.

## Out of scope (backlog — captured, not designed here)

- **Issue-triage autopilot.** Needs new issue-assessment dimensions beyond the current bug-report-shaped verdict taxonomy (`CONFIRMED-BUG`/`LIKELY-BUG`/etc.) — specifically dimensions for "is this feature request aligned with project direction" and "does this bring value to the project," which don't exist yet. Also needs a trigger-model decision (auto-fire on every new issue from a non-owner, vs. the existing hermes-ops draft plan's "human applies a `triage` label first" model). To be brainstormed as its own spec.
- **Incremental re-review** — only re-review what changed since the last-reviewed SHA, rather than a full fresh council pass on every new commit. Cost optimization; current SHA-keyed ledger already avoids re-reviewing *unchanged* PRs, this only affects PRs that receive new commits after an initial review.
- **On-demand comment-trigger** — force a review via a PR comment command, as an escape hatch for `DEFERRED_SIZE`/`NEEDS-HUMAN` cases. Needs its own comment-polling plumbing; manual invocation (as done throughout this session) covers the same need for v1.
- **Safe two-workflow SonarCloud split for fork PRs** — independent CI hardening work, not required for this autopilot to ship.

## Provenance

Designed 2026-07-04, in the same session that manually reviewed PR #237 (`ffroliva/gflow-cli`) and discovered the dependabot-autopilot's daily 06:00 UTC cadence had simply not yet ticked past that PR's creation time — not a bug, but the gap that motivated this design. Specializes hermes-ops' `autopilot-core-design.md` (draft, branch `feat/autopilot-core-spec`) as a fourth autopilot alongside dependabot/issue/release, and is the first specialization requiring a real Claude-Code harness rather than hermes' own freellmapi-routed agent.
