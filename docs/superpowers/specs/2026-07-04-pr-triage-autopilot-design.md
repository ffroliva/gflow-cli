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

### Review engine seam (addendum 2026-07-10)

The "run the review" step is pluggable: `engine ∈ {council-claude, council-multi-cli}`, config-defaulted to `council-claude`, and every ledger entry records the engine that produced its verdict. v1 implements only `council-claude` — the claude-CLI council described throughout this spec, which already fans out 5-13 persona sub-agents internally. `council-multi-cli` (an orchestrator distributing review personas across claude + codex/Antigravity headless CLI agents with a per-persona model-assignment matrix) is named here so the seam exists, but its design is explicitly backlog (see "Out of scope"); nothing in this spec's security or cost model is licensed for other CLIs without that follow-up spec.

### Notifications (addendum 2026-07-10)

Telegram remains the firehose: every outcome, exactly as specified in the data flow above. Email is a second, high-signal-only channel — council report posted, `NEEDS-HUMAN`, `DEFERRED_SIZE`, and `FAILED_PERMANENT` — sent by the **host orchestrator only**, after the sandboxed container has exited; the email credential (`RESEND_API_KEY`) is never mounted into the review container, extending Security item 3's credential boundary unchanged. Mechanism, recipient, provider, and the notifier module live in hermes-ops (see `hermes-ops/docs/specs/2026-07-10-autopilot-notifications-and-home-channel-design.md`); email delivery failure is non-fatal and logged, mirroring the Telegram row in "Error handling".

## Architecture — data flow

```
hermes cron "pr-triage-autopilot" (hourly)
  │
  ├─ Acquire lock (flock/pidfile). If a previous tick's process is still alive, skip this
  │    tick entirely and log it — no overlap, no concurrent daily-cap races (see
  │    "Operational hardening").
  │
  ├─ gh pr list --repo ffroliva/gflow-cli --state open --json number,author,isDraft,headRefOid,
  │    additions,deletions,changedFiles,statusCheckRollup,comments,state
  │
  ├─ Stage 0 — cheap deterministic gate (pr_triage_gate.py, no LLM, free):
  │    author != 'ffroliva'  AND  not author.is_bot  AND  not isDraft  AND  state == 'OPEN'
  │    AND all CI checks finished (no PENDING/IN_PROGRESS)
  │    AND diff size within cap (default: ≤30 files AND ≤1500 lines changed;
  │        else → ledger `DEFERRED_SIZE`, Telegram-notify, skip)
  │    AND (pr, head_sha) not already in pr_triage_ledger.jsonl
  │    AND independent deterministic injection-pattern pre-filter finds nothing obvious
  │        (regex/keyword scan over title+body+comments — see Security; a hit routes
  │        straight to `NEEDS-HUMAN`, skipping Stage 1 entirely)
  │    AND per-PR Stage-1 volume cap not exceeded (default: 3 Stage-1 evaluations per PR
  │        per day, independent of the full-council daily cap — see Security item 5)
  │
  ├─ On the HOST (as `hermes`, before any container launches): `cd /opt/gflow-cli &&
  │    git fetch origin pull/<N>/head` — a fetch step never executes the fetched code,
  │    so this is safe to do outside the sandbox.
  │
  ├─ Stage 1 — cheap single-agent pre-evaluation, run INSIDE the same sandboxed,
  │    fresh-per-review container as the council (see Security — Stage 1 gets the same
  │    tool-scope restriction; it is not a trusted step just because it's cheap): one
  │    non-parallel `claude -p` call reads diff + title + body + existing comments (NOT
  │    the full council):
  │    verdict ∈ {PROCEED, TRIVIAL, OBVIOUS-JUNK, NEEDS-HUMAN}
  │    only PROCEED/TRIVIAL continue; OBVIOUS-JUNK/NEEDS-HUMAN → ledger + Telegram-notify, no post
  │
  ├─ For each PROCEED/TRIVIAL PR, in a FRESH ephemeral Docker container (destroyed after
  │    the run — see Security): the host's `/opt/gflow-cli` clone is bind-mounted
  │    READ-ONLY; the container never fetches, writes, or holds a GitHub write-scoped token.
  │    1. claude -p "/gflow:pr-council-review <N>"  — §9 autonomous mode:
  │         - PROCEED → full 5-13 dimension council
  │         - TRIVIAL → reduced dimension set (D1+D2 only)
  │         - fixed resolutions for every interactive gate (see §9 below)
  │         - assembles the report and prints it (plus one structured summary line) to
  │           stdout — does NOT post it itself (see Security item 3, credential boundary)
  │    2. the HOST orchestrator (holding the dedicated, comment-only-scoped bot token —
  │         never the operator's own broad-scope token) posts the report via
  │         `gh pr comment`, then parses the structured summary line:
  │         success → append {pr, head_sha, verdict, ts, fail_count: 0} to ledger; Telegram-notify
  │         failure (claude crash, git fetch fail, post fail, PR closed mid-run) →
  │           increment fail_count for (pr, head_sha); if fail_count < 3, retry next tick;
  │           at fail_count == 3, ledger as `FAILED_PERMANENT` (stops auto-retry) and send
  │           a distinct Telegram alert requiring manual reset — see "Operational hardening"
  │
  └─ Daily cap: stop at N (default 5) new full-council reviews per calendar day;
       remaining qualifying PRs are deferred to tomorrow's tick with a Telegram note.
       (Independent of the per-PR Stage-1 cap above.)
```

## Stage 0 — deterministic pre-filter

`scripts/autopilot/pr_triage_gate.py`, mirrors `dependabot_gate.py`'s pure-function-plus-fixtures shape:

```python
def should_review(pr: dict) -> ShouldReviewResult:
    # author != owner, not bot, not draft, state == OPEN, CI finished,
    # changedFiles <= 30 and (additions + deletions) <= 1500,
    # (pr, head_sha) not in ledger,
    # no obvious injection pattern in title/body/comments (regex/keyword scan),
    # per-PR Stage-1 volume cap (default 3/day) not exceeded
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
| `NEEDS-HUMAN` | Ambiguous scope/legitimacy call | Defer, Telegram-notify, **no auto-post** |

This is the actual lever on LLM cost: most spend concentrates on PRs worth reviewing, not every PR unconditionally. Note this Stage-1 call is itself reading untrusted content and is bounded by the same per-PR daily volume cap and sandbox/tool-scope restrictions as the full council (see Security) — its own judgment is not treated as a trusted gate on its own; the deterministic pre-filter in Stage 0 is the first line of defense, not this LLM call.

## Security — prompt injection & privilege isolation

Every reviewer sub-agent reads fully untrusted, attacker-controlled content (PR title/body/comments/diff, including comments embedded in the submitted code). This is broader than "should we read comments" — it's inherent to reviewing external contributions at all. It is materially higher-stakes in the autonomous path than the interactive path: no human is watching in real time, and the `hermes` user on the VPS is root-trusted with **passwordless sudo** (per hermes' own `USER.md`). A successful injection's blast radius is not "one bad PR comment" — it is potentially the whole VPS.

Mitigations (all required, not optional given that privilege level). This list was substantially hardened after an adversarial review round (three independent fresh-agent passes — security/ops/completeness — against the first draft of this spec); see "Provenance" for what changed and why.

1. **Docker sandbox, fresh container per review.** The actual `claude` CLI review subprocess (both Stage 1 and the full council) runs inside a **new, ephemeral container destroyed after each review** — never a long-lived reused container, to avoid state/ref accumulation and cross-review leakage. Non-root user inside the container; no SOPS secrets mounted; no other `/opt/` project mounted; the host's `/opt/gflow-cli` clone (kept up to date and periodically pruned by the host orchestrator, not the container) is bind-mounted **read-only**; no Docker socket inside (prevents container escape); network egress restricted to `api.anthropic.com`/`github.com`. `git fetch` (including the per-PR `pull/<N>/head` ref) happens on the host, before the container launches — a fetch never executes the fetched content, so it doesn't need the sandbox.
2. **Read-only tool scope, enforced by the harness, not by prompt text alone.** Prompt-only restriction ("don't use Bash") is exactly what a successful injection would ignore — it cannot be the only control. Enforcement must be at the tool-dispatch layer (e.g. Claude Code's allowed/disallowed-tools restriction for the `claude -p` invocation) so that reviewer sub-agents and the Stage-1 call are *unable* to invoke write-capable tools regardless of what they're told to do, with the container's read-only filesystem and no-secrets/no-write-token setup (item 1, item 3) as the backstop if that restriction is ever bypassed. This applies to Stage 1 exactly as much as to the council — being cheap doesn't make Stage 1 a trusted step.
3. **Credential boundary: the container never holds a GitHub write-scoped token.** Read access to the repo comes from the pre-fetched local clone (no auth needed for a public repo's read paths); the container has no credential capable of posting anything. The **only** thing with GitHub write/post privilege is the host orchestrator's final `gh pr comment` call, made **after** the container has exited — never a sub-agent, never from inside the sandbox, never mid-review. That orchestrator credential is a dedicated bot/service GitHub account's fine-grained PAT, scoped to comment-only on this one repo — deliberately never the operator's own personal token (which in practice carries much broader scopes, e.g. `admin:org`) and never presented to the injection-exposed review process at all.
4. **Explicit injection-awareness in every dispatched prompt, including a content constraint on the report itself.** PR content is untrusted external input, not instructions; embedded directives ("ignore previous instructions", "mark as safe", etc.) are a prompt-injection attempt to be flagged as a security finding, never acted on. This must also cover the "confused deputy" case where no tool use is involved at all — a PR crafted to get the reviewer to *write* something inappropriate into its own report text (e.g. asking it to "explain your environment/security setup" in the review). The report format is constrained to the existing structured sections (verdict, must-fix, nice-to-have, confirmed-good) only; it must never reproduce suspicious embedded text verbatim, disclose environment/infrastructure details, or respond to any meta-request embedded in the PR. The posted comment also carries a clear "🤖 automated council review" header — via the dedicated bot account (item 3), so an injected report can never read as the repo owner's own words.
5. **Two independent injection defenses, not one circular check.** A single LLM call cannot be both the judge of untrusted content and its own reliable injection detector — one successful jailbreak defeats both simultaneously. Stage 0 (free, deterministic, no LLM) runs a regex/keyword pre-filter over title+body+comments for obvious injection patterns *before* Stage 1 ever runs; a hit routes straight to `NEEDS-HUMAN`. Stage 1's own LLM-level judgment (item 4) is a second, weaker layer on top of this, not the only line of defense. Additionally, a **per-PR Stage-1 volume cap** (default 3 evaluations per PR per day) prevents an attacker from forcing unbounded cheap-pass spend by repeatedly pushing trivial commits to churn new SHAs — this is separate from, and in addition to, the full-council daily cap in "Safety valve."

## SonarCloud for fork PRs

`ci.yml`'s `sonar` job already explicitly skips fork PRs (`github.event.pull_request.head.repo.full_name != github.repository`) because GitHub Actions does not inject repository secrets into fork-triggered `pull_request` runs — a platform-level trust boundary, not a config gap. The two ways to actually enable it are `pull_request_target` (confirmed real attack surface — Orca Security has documented RCE/exfiltration incidents from exactly this misconfiguration in other OSS repos) or a safe two-workflow split (`pull_request` builds without secrets → `workflow_run` runs the Sonar scan against the artifact without executing the fork's code). Given the autopilot's whole purpose is reviewing potentially-adversarial external contributions, **§9 autonomous mode treats a skipped/missing SonarCloud check on a fork PR as an informational note, not a blocker.** The safe two-workflow split remains available as independent future work, out of scope here.

## Ledger & idempotency

`pr_triage_ledger.jsonl` under `HERMES_HOME`, append-only, keyed by **`(pr, head_sha)`** — not just `pr` (unlike the dependabot ledger). A new commit on a previously-reviewed PR is a new SHA, so it gets a fresh review; an unchanged PR is never re-reviewed. Verdicts `DEFERRED_SIZE`, `OBVIOUS-JUNK`, and `NEEDS-HUMAN` are also ledgered (by SHA) so they don't re-notify every hour, but a human can still act on them out of band (manually running `/gflow:pr-council-review <N>` from any Claude Code session, exactly as done for PR #237 this session).

**Atomicity.** Unlike memory sync (below), the ledger is the *sole* idempotency mechanism this whole design rests on, so it doesn't get a "staleness is fine" pass — the writer must use a single atomic append (`O_APPEND` write, or an `flock` around the write) and the reader must tolerate and discard a trailing malformed/partial line rather than crash on it. The concurrency lock in "Operational hardening" means there is normally only ever one writer at a time; this is a defense-in-depth guarantee, not the primary safeguard against concurrent writes.

**Retry-on-failure, not skip-on-failure, but bounded.** A ledger entry with `verdict` set is only written on a *successful* post. A `claude` CLI crash, a failed `git fetch origin pull/<N>/head`, a failed `gh pr comment` post, or a PR/branch that disappears mid-run all increment a `fail_count` for that `(pr, head_sha)` instead. Below `fail_count == 3` the next hourly tick retries, paired with an explicit Telegram error notification so failures are never silent; at `fail_count == 3` the entry is marked `FAILED_PERMANENT` and auto-retry stops, with a distinct Telegram alert requiring a manual reset (deleting the ledger entry, or a future `hermes` text-command) to try again. Without this cap, a PR engineered to reliably fail the post step (or simply closed with its branch deleted) would otherwise retry — and re-spend a full council's worth of compute — every hour, forever.

## Memory sync

One-way: your local machine → the VPS, never the reverse. Your local memory directory remains the single source of truth; the VPS's Claude Code namespace at `/opt/gflow-cli` is a read-only consumer of the latest snapshot. If an autonomous review surfaces something worth remembering long-term, it is reported (Telegram / PR comment) for you to add yourself in a future session — never auto-written back, consistent with the "never auto-apply memory edits" rule already in the interactive gate defaults below. Sync mechanism (manual push after memory-heavy sessions, or a session-end hook) is left to the implementation plan; slight staleness is acceptable since memory is already documented as point-in-time observation, not live state.

## `pr-council-review` §9 — Autonomous mode (new)

Fixed resolutions replacing every interactive gate, for when hermes invokes the skill unattended:

| Interactive gate (existing protocol) | Autonomous-mode resolution |
|---|---|
| §0 step 4, draft-PR confirmation | N/A — drafts are filtered out upstream by Stage 0 |
| §5 step 6, live-verify credit-spend gate | Always skip; never spend Flow/Veo credits; the report's mandatory "Next step — live validation" section (see "Live-validation ceiling" below) carries this, not a loose open-item note |
| §5 step 7, memory-action gate | Report the suggested memory action only; never auto-apply |
| §5 step 8, YELLOW-dismiss escape valve | Never auto-dismiss; report YELLOW as-is |
| §6, final "How to proceed" `AskUserQuestion` | Omitted — the skill assembles the full report and prints it (plus the structured summary line) to stdout; it does **not** call `gh pr comment` itself. Posting is exclusively the host orchestrator's job, using the dedicated bot credential, after the sandboxed run has exited (see Security item 3) |
| SonarCloud required-gate (command-level instruction) | Fork PR + skipped/missing SonarCloud check → informational note, not a blocking condition |

Also defines: the machine-parseable one-line structured summary printed at the end of a run (verdict + must-fix count + PR URL, or an error marker like `POST_FAILED`) that `pr_triage_autopilot.py` greps for instead of parsing free-form markdown; the reduced D1+D2-only dimension set for `TRIVIAL` verdicts; and the injection-awareness, report-content-constraint, and harness-enforced read-only-tool-scope requirements from the Security section above as mandatory parts of every dispatched sub-agent prompt in this mode — including the Stage-1 pre-evaluation call itself, not just the council's sub-agents.

### Live-validation ceiling (addendum 2026-07-10)

The sandbox structurally cannot exercise the code live: network egress is restricted to `api.anthropic.com`/`github.com`, no Flow/Google auth state is mounted, and credit spend is forbidden by §9's own gate resolution. Therefore the **e2e test suite and `/gflow:benchmark` are never run in autonomous mode**, and an autonomous verdict is capped at **static-green** — it asserts the diff, tests-as-code, CI results, and council dimensions, never live behavior against Flow.

Consequences, all mandatory:

1. **A green autonomous verdict must never read as merge-ready.** Every green report ends with a mandatory **"Next step — live validation"** section stating that e2e + `/gflow:benchmark` (run by the operator, outside the sandbox, with real credentials/credits) is the final triage gate, that it is deliberately last (run only when everything else is green, so credits are never spent on a PR that static review would have bounced anyway), and that this step is **expected to surface issues and return the PR to development** — a bounce there is the process working, not the autopilot having missed something.
2. **The verdict taxonomy is explicit about the ceiling:** autonomous GREEN ≡ "static review green, live validation outstanding". The structured summary line format is unchanged; the ceiling is carried in the report body and this documented semantics.
3. **Non-green verdicts skip the section** — there is no point flagging live validation on a PR that is already going back to the contributor.

## Error handling

| Failure | Handling |
|---|---|
| `claude` CLI crash/timeout/bad exit | Increment `fail_count` for `(pr, head_sha)` (retry next tick below the cap); Telegram error notification |
| `git fetch origin pull/<N>/head` fails (network blip, or PR/branch deleted mid-run) | Same |
| Review succeeds but `gh pr comment` post fails (e.g. GitHub rate limit) | Same — §9 must surface `POST_FAILED` in its summary line rather than reporting success |
| `fail_count` reaches 3 for a given `(pr, head_sha)` | Ledger as `FAILED_PERMANENT`, stop auto-retry, distinct Telegram alert requiring manual reset (see Ledger section) |
| Container hangs without crashing (no timeout hit) | A wrapper-level timeout (e.g. `timeout` around the container run, or Docker's own stop-timeout) forcibly kills the container; treated the same as a crash |
| GitHub API rate-limited (403/429) | Respect `Retry-After`/backoff and retry within the same tick where practical; in practice the daily cap (≤5 full reviews/day) and per-PR Stage-1 cap keep total volume well within standard authenticated rate limits, so this is a defensive backstop, not an expected steady-state condition |
| Telegram delivery itself fails | Logged locally (so it's visible on next VPS access) even though the realtime notification is lost — the ledger/report-on-GitHub remain the source of truth, not the Telegram message |
| Email delivery fails (high-signal channel) | Same rule as Telegram: logged locally, non-fatal, no retry loop — the ledger/report-on-GitHub remain the source of truth |
| Stale/missing memory sync | Not a failure — degraded grounding only, silently accepted |

## Operational hardening

- **Concurrency.** A flock/pidfile lock wraps the whole hourly job; if the previous tick's process is still running (a full council plus container startup can plausibly exceed an hour), the new tick skips entirely rather than running concurrently. This is what makes the daily-cap check-then-act safe — without it, two overlapping ticks could each independently see "under cap" and jointly exceed it, or double-review/double-post the same PR.
- **Docker lifecycle.** Fresh, ephemeral container per review (not persistent) — see Security item 1. The host-side `/opt/gflow-cli` clone is the only persistent state; it needs periodic pruning of old `pull/<N>/head` refs (e.g. weekly, or refs for PRs no longer open) to bound disk growth over months of hourly runs.
- **Reboot / cron-daemon restart.** No special recovery procedure needed — the whole design is stateless polling plus a durable ledger, so a reboot simply resumes from whatever the ledger already reflects on the next tick, the same as an ordinary missed tick.

## Safety valve — daily cap

Default 5 new full-council reviews per calendar day (separate from the per-PR Stage-1 volume cap in Security item 5, which bounds the cheaper pre-evaluation step independently). A burst of PRs (spam, mass low-effort contributions) is reviewed up to the cap; the rest are deferred to tomorrow's tick with an explicit Telegram note (`⏸️ daily review cap (5) reached — N PR(s) deferred: #a, #b, ...`) rather than silently processing an unbounded queue. Bounds both GitHub API usage and Claude usage exposure in a pathological scenario — independently validated during design research (a public account of a competitor's usage-based-billing bot producing a surprise ~$200 bill). Pausing the whole autopilot is just disabling the hermes cron job, same as any other job.

## Testing

- `pr_triage_gate.py`: pure-function unit tests via fixtures (mirrors `dependabot_gate.py` / `eval/pr_fixtures.json`).
- `pr_triage_autopilot.py`: a dry-run mode (mirrors dependabot's) that logs what *would* be reviewed/posted without invoking `claude` or `gh pr comment` — safe to test against real current open PRs without side effects.
- Live-fire: one real end-to-end test against a low-stakes PR before enabling the hourly cron for real (not PR #237, which already carries a manual review comment from this session).
- **Done as part of this design**: a three-lens adversarial review pass against the spec itself (security / operations / completeness, fresh agents with no attachment to the design) — see "Provenance" for what it found and changed. Worth repeating the same exercise against the actual implementation before it goes live, since a plan can diverge from its spec.

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
- **`council-multi-cli` review engine** — orchestrator distributing review personas across claude + codex/Antigravity headless CLI agents, with an explicit per-persona model-assignment matrix (which CLI/model runs which dimension, including freellmapi-routed free models where quality permits). Requires its own spec covering per-CLI sandboxing, auth, injection hardening, and cost model; prerequisite: codex + Antigravity CLI provisioning on the VPS (tracked in hermes-ops). The engine seam above exists so this lands without re-opening this spec.

## Provenance

Designed 2026-07-04, in the same session that manually reviewed PR #237 (`ffroliva/gflow-cli`) and discovered the dependabot-autopilot's daily 06:00 UTC cadence had simply not yet ticked past that PR's creation time — not a bug, but the gap that motivated this design. Specializes hermes-ops' `autopilot-core-design.md` (draft, branch `feat/autopilot-core-spec`) as a fourth autopilot alongside dependabot/issue/release, and is the first specialization requiring a real Claude-Code harness rather than hermes' own freellmapi-routed agent.

**Revised same day** after three independent fresh-agent adversarial reviews (security / operations / completeness lenses) against the first draft (published as PR #238). All three converged independently on the same core gap — unbounded retry on a permanently-failing PR, an uncapped-cost DoS vector — which is now the `fail_count`/`FAILED_PERMANENT` mechanism. Also added from that round: the concurrency lock, ledger-atomicity note, fresh-per-review container decision (vs. persistent), the credential/privilege boundary separating the container from the posting token (dedicated bot account, never the operator's broad-scope personal token), hardening Stage 1 to the same tool-scope/sandbox restrictions as the council rather than treating it as an implicitly-trusted cheap step, a per-PR Stage-1 volume cap independent of the full-council daily cap, and the report-content ("confused deputy") constraint. Nothing the reviews raised was dismissed without a corresponding spec change or an explicit "already adequately handled" call-out.

**Addendum 2026-07-10** (brainstorm session, after v1 implementation completed against the 2026-07-08 plan): added the review-engine seam, the email high-signal notification channel, the email error-handling row, and the `council-multi-cli` backlog entry. Companion spec: `hermes-ops/docs/specs/2026-07-10-autopilot-notifications-and-home-channel-design.md` (email notifier mechanism + `/sethome` home-channel persistence fix).
