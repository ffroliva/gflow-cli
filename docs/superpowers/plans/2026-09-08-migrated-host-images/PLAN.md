# Migrated-Host Image Generation Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature migrated-host-images` to find the
> next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Make `gflow image t2i` and local-file `gflow image i2i` work on
`flow.google.com` with existing projects, including their direct and queued MCP twins.

**Architecture:** Extend the existing `MigratedComposer` and dispatch to it from
`UiAutomationTransport`; do not add a new transport or replay private RPCs. Flow's own page
performs submission and polling while gflow observes, validates and downloads the result.
The labs path and public request DTOs remain unchanged.

**Predict verdict:** CAUTION — confidence 8/10. The plan begins with a live wire spike that
must settle the image RPC/result contract before production code.

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| Critical | Video/poster mistaken for an image result | Capture image envelopes and verify downloaded magic bytes |
| Critical | UI drift submits an unintended/billed request | Read back mode/settings and assert outgoing request shape |
| High | Pre-transport reCAPTCHA mint blocks on the migrated root grid | Route the migrated UI path before labs-only minting |
| High | CLI works while MCP adapter drops fields | Direct and queued MCP tests plus check-skill mirror sweep |
| High | Port overclaims unmeasured I2I/reference forms | Explicit pre-submit capability gate and narrow documentation |

## File structure

### New files

```text
scripts/dev/spike_migrated_image_submit.py
  Fail-loud, token-redacted live capture of the page-owned image wire.
tests/e2e/test_migrated_host_e2e.py
  Real CLI/service and MCP coverage on a migrated profile.
tests/features/migrated_host_images.feature
  Behaviour contract for supported and refused forms.
```

### Modified files

```text
src/gflow_cli/api/transports/migrated_composer.py
  Image settings, local reference attachment, observation and image result conversion.
src/gflow_cli/api/transports/ui_automation.py
  Migrated/labs image routing before the legacy driver path.
src/gflow_cli/api/client.py
  Avoid labs-only token minting only when migrated UI generation owns submission.
src/gflow_cli/api/transports/batchexecute.py
  Parse the measured image record without weakening video validation.
tests/api/ and tests/mcp/
  Parser, routing, fail-fast and adapter parity tests.
KNOWN_ISSUES.md, CHANGELOG.md, docs/USAGE.md, docs/MCP.md
  Narrow feature-matrix and operator documentation updates.
```

## Task 1 — Live wire spike

**What:** Measure one T2I and one local-file I2I submission on the migrated host.

**Steps:**
- [x] Reuse the profile lease and existing migrated composer anchors.
- [x] Capture RPC IDs, redacted request summaries, response record shapes and image bytes.
- [x] Record what was and was not measured in a durable spike note.

**Tests:**
- [x] Spike exits non-zero if Image mode/pane/submit/result is not reached.
- [x] Captures contain no cookies, tokens or signed query strings.

## Task 2 — Red unit/BDD tests

**What:** Add failing tests for routing, mode/settings, parsing, download guards and unsupported forms.

**Steps:**
- [x] Add BDD feature scenarios from `SCENARIO.md`.
- [x] Add parser fixtures derived from redacted capture structure.
- [x] Add fake-Page tests for listener registration/detachment and pre-submit validation.

**Tests created (red):**
- [x] Migrated T2I returns `GeneratedImage` records.
- [x] Local I2I includes each attached id in the outgoing image submit.
- [x] Unknown records, foreign URLs and non-image bytes fail loud.
- [x] Labs-host image generation retains its current route.

## Task 3 — Core migrated image implementation

**What:** Drive Image mode/settings, bind local references, observe results and convert them to existing DTOs.

**Steps:**
- [x] Add the smallest image-specific methods to `MigratedComposer`.
- [x] Validate the measured submit RPC/body before accepting a result.
- [x] Preserve project/workflow/media IDs and trusted signed image URLs.
- [x] Reuse existing upload/mention logic only where the live contract matches.

**Tests:**
- [x] Task 2 unit and BDD tests turn green.

## Task 4 — Route before labs-only minting

**What:** Select migrated image generation from the actual/forced host without breaking labs.

**Steps:**
- [x] Make host capability routing explicit at the transport/client seam.
- [x] Require an existing project on the migrated path.
- [x] Keep legacy token mint/retry semantics byte-for-byte on labs.

**Tests:**
- [x] Forced/actual migrated host skips client-side mint and enters the migrated composer.
- [x] Auto/labs routes preserve existing mint and UI driver calls.

## Task 5 — CLI behaviour verification

**What:** Verify existing CLI options reach the new shared path with no schema expansion.

**Steps:**
- [x] Confirm t2i/i2i help and errors accurately state migrated support boundaries.
- [x] Confirm output/history/checkpoint behaviour is unchanged.

**Tests:**
- [x] CLI t2i and local-file i2i integration tests cover project/model/aspect/count/ref paths.

## Task 6 — MCP surface mirror

**What:** Verify direct and queued `gflow_generate_image` use the same migrated path.

**Steps:**
- [x] Audit `mcp/tools.py`, `worker/codec.py`, and tool/resource documentation.
- [x] Run the six mirror axes in `skills/check/SKILL.md` step 1b.

**Tests:**
- [x] Direct MCP migrated T2I/I2I.
- [x] Queued payload round-trip preserves model/aspect/count/ref paths.

## Task 7 — Documentation and known-issue update

**What:** Document exactly the verified matrix and remaining #639 forms.

**Steps:**
- [x] Update `KNOWN_ISSUES.md`, user/MCP docs and `[Unreleased]` changelog.
- [x] Add the live-verification ledger after runs complete.

## Task 8 — Quality, council and live verification

**What:** Run offline gates, branch review, then both affected live surfaces.

**Steps:**
- [x] `/gflow:check` is green (ruff, format, pyright, docs checks, and full suite).
- [ ] `/gflow:branch-review` returns GREEN and all must-fix findings are addressed.
- [x] Run migrated CLI T2I and local-file I2I E2E tests.
- [x] Run direct and queued MCP migrated image E2E tests.
- [ ] Run the labs-host control or name the external account-cohort blocker (the available
  live profile is migrated; no separate labs-host account was available for a control run).
- [x] Live ledger records result bytes, IDs, durations and redactions.

## Definition of done

- [ ] All task steps checked off
- [x] `/gflow:check` green and coverage remains at least 80% (4086 passed, 7 skipped,
  91.65% coverage; full run required elevated access for its build/file fixtures)
- [ ] Critical/High scenarios have automated coverage
- [ ] CLI and both MCP paths have watched E2E runs
- [ ] `CHANGELOG.md`, user docs, MCP docs and `KNOWN_ISSUES.md` are current
- [x] No untracked `TODO` or overclaim about unmeasured migrated surfaces

Branch-review remains the next pipeline phase; the labs-host control is an account-cohort
blocker, not a claim that the labs route changed.
