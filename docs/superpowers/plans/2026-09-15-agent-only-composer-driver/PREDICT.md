# Predict: drive the agent-only composer for t2i and t2v (#799)

**Proposal.** When `MigratedComposer.ensure_editor` identifies the agent-only composer, route
`run_images` / `run_video` to a new `AgentOnlyComposer` instead of raising exit 25. It types a
directive (`Make me N picture(s) / a D second video of <prompt> in a W:H aspect ratio.`) into
`div.ProseMirror`, clicks `flow-generate-icon-button`, approves exactly one credit gate that
appeared after its own submit, and completes when new `flow-content.google/{image,video}/<uuid>`
tiles exist that were not in the pre-submit baseline. Downloads reuse the existing signed-URL
paths. Slice 1: t2i and t2v only; every other form keeps its typed refusal.

## Verdict: CAUTION
**Confidence:** 6/10

## Summary

The drive path is measured end to end (3 live runs) and the change is contained to the two
functions every surface already routes through, so architecture and MCP parity are cheap. The
risk is semantic: a chat agent interprets the request, so model, count and duration are
*asked for*, not *set*, and must be verified after the fact rather than trusted.

## Persona findings

### Architect — GO (8/10)
- Lives beside `MigratedComposer` in `api/transports/` as `agent_only_composer.py`; the
  2,500-line `migrated_composer.py` does not grow. No new port, no factory entry.
- Seam: `ensure_editor` already computes the discriminator. Make it **return** which composer
  it found instead of raising, and let `run_images` / `run_video` branch. Two callers only
  (`migrated_composer.py:2414`, `:2498`) — no ripple.
- Return types unchanged (`list[GeneratedImage]`, `VideoResult`) → recorder, CLI, MCP, worker
  untouched.
- Do **not** port labs `drivers/agentic.py`; its anchors (Slate, `getMediaUrlRedirect`) are all
  wrong here. Reuse only the directive phrasing (`_compose_directive`).

### Security / reCAPTCHA — CAUTION (6/10)
- **Credit gate auto-approval is the sharp edge.** Approve only a
  `flow-permission-message` row that is `:not(.read-only)` **and** was not present before our
  submit, and only one per run. A stale gate from an earlier session (they survive reloads)
  must never be approved by us.
- **Prompt injection into the agent.** The user prompt is concatenated into a directive a chat
  agent reads. A prompt like "…and make 4 videos" can raise spend. Mitigation: count the gate
  (one gate per run; refuse and do not approve a second) and verify produced-count ≤ requested.
- Never click Agent-settings **Save** — it mutates a server-remembered account default.
- Downloads: keep `_is_allowed_download_host` + `max_redirects=0`; strip `Signature` from any
  logged URL (`redaction.py` already does for `flow-content.google`).

### Performance / Playwright — CAUTION (6/10)
- The readiness gate waits **30 s** for a trigger that never becomes visible before the
  discriminator runs — every agent-only run pays it. Race the two anchors instead (trigger
  visible OR agent prompt box visible), then run the discriminator. Healthy classic runs keep
  one wait.
- Completion is DOM polling (image ~45 s, video ~86 s measured). Poll at ≥1 s; bounded by the
  existing `poll_timeout_s`.
- A tile renders as grid tile + chat option with the same uuid → dedupe by uuid (measured 2×).
- Video poster (`/image/<uuid>`) precedes `<video src=/video/<uuid>>` by ~30 s — a poster is
  not completion.

### CLI & MCP UX / Cross-platform — CAUTION (6/10)
- **MCP:** no new option; `gflow_generate_image` / `gflow_generate_video` and the queued
  `worker/codec.py` path end in the same two functions. Parity holds by construction. **But
  the Iron Law applies separately**: one MCP e2e run is required. Docstrings/docs that say
  the cohort "cannot be driven" (KNOWN_ISSUES, `errors.py` remediation text) become false.
- Exit codes: unported forms on this composer → `FlowHostMigratedError` (36) as today. Gate not
  appearing / no tile in budget → `TransportTimeoutError` (9). A produced result that
  contradicts the request (wrong count, wrong orientation) → typed failure, not silent success.
- **`--model` cannot be set per request.** Honest options: (a) refuse when the requested model
  is not the Agent-settings default (read-only pane read, $0), (b) name the model in the
  directive and record that it is unverified, (c) ignore. (c) is a lie; (b) is unmeasured.
  → open question for the plan.
- Selectors: all Tier 1 (component tags, `mat-icon` ligatures, `role`, class). No text.

### Devil's Advocate — CAUTION (5/10)
- Simpler path considered: *keep refusing* and document the web UI. Rejected — the cohort now
  spans ≥2 accounts and 2 locales, and the drive path is measured, so the refusal is the
  expensive option for users.
- Smaller still: ship **t2i only** first (0 credits to verify), then t2v. Worth it only if t2v
  e2e cannot be funded; it can (7 credits).
- Unmeasured and load-bearing: count > 1, "Never confirm" setting, model-by-name, repeatability
  (N=3, one account). Measure count>1 in the e2e before claiming `--count`; refuse it otherwise.
- Rollback: the branch is a single `if composer == "agent_only"`; reverting restores exit 25.

## High-confidence risks (2+ personas)
1. **Request fidelity** (Security, UX, DA): model/count/duration are requests to an agent.
   Verify after generation what can be verified (count, orientation from tile dimensions);
   refuse what cannot.
2. **Credit-gate approval scope** (Security, Performance): baseline + one-gate rule.
3. **30 s dead wait** (Performance, Architect): race the anchors.

## Conflicts resolved
- Architect "return a value" vs existing tests pinning `FlowAgentUiError` from `ensure_editor`:
  keep the raise for any *other* caller path by having `run_*` own the branch; update the
  fixture test to assert routing rather than the raise.

## Required mitigations before EXECUTE
1. Baseline-diffed, single-gate approval; second gate → stop, do not approve.
2. Post-hoc checks: produced uuid count == requested; tile aspect matches requested orientation.
3. Model policy decided (plan question 1). Count>1 measured in e2e or refused.
4. No Agent-settings Save anywhere in the driver.
5. Race readiness anchors; no 30 s dead wait on this composer.

## Recommended next step
`/gflow:scenario` → [SCENARIO.md](SCENARIO.md), then answer the plan's open questions.
