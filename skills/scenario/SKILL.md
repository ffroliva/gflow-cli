---
name: scenario
description: >
  Pre-implementation edge-case and scenario explorer for gflow-cli.
  Decomposes a proposed feature or change across 13 structured dimensions
  specific to gflow-cli's failure surfaces (WAF/reCAPTCHA, Playwright selectors,
  auth token lifecycle, batch resume, data layer safety, cross-platform paths).
  Produces a severity-ranked scenario table to feed into the PLAN spec and
  the test matrix before EXECUTE begins.
---

# `scenario` — Edge Case & Scenario Explorer

Systematic pre-implementation scenario analysis. For a given feature or change,
produces a severity-ranked table of test scenarios across 13 dimensions tuned
to gflow-cli's known failure surfaces. Feed the output into PLAN.md tasks and
`tests/features/` BDD scenarios before entering EXECUTE mode.

---

## When to invoke

Use before implementing any of:
- A new generation path (T2V, I2V, R2V, batch manifest runner)
- Transport changes (any new HTTP call against `aisandbox-pa.googleapis.com`)
- Auth or session changes (new strategy, cookie extraction, SAPISIDHASH wiring)
- Selector cascade changes (`ONBOARDING_SELECTORS`, `NEW_PROJECT_SELECTORS`, `FRAME_SLOTS_STRUCT`, `IMAGE_MODEL_OPTION_SELECTORS`)
- Data layer changes (schema migration, new `OperationRecorder` callsite, redaction change)
- New CLI subcommand, flag, or exit code

Skip for: pure doc changes, CHANGELOG/version bumps, `scripts/` tooling with no production callpath.

---

## Invocation

```
/gflow:scenario <feature or change description>
```

`<feature or change description>` is a brief summary of what you're about to implement. Examples:
- "SAPISIDHASH auth header wired into `_post_json` for `aisandbox-pa` routes"
- "gflow video batch manifest ledger (skip already-completed rows)"
- "Image model picker converted from English has-text to structural anchor"
- "CDP-attach transport as opt-in alongside ui_automation"

---

## The 13 dimensions

For each dimension, enumerate scenarios that are **non-obvious** — do not list
things that a basic happy-path test already covers. Focus on things that break
in production but pass in unit tests.

### D1 — Auth & session lifecycle
The SAPISID cookie expires. The user re-runs `gflow auth login` mid-batch. A
profile is created but the Flow session was never verified. The session is valid
for `labs.google` tRPC but not for `aisandbox-pa`. Two profiles are in use
simultaneously (Chromium profile-lock).

### D2 — WAF / reCAPTCHA scoring
A profile's WAF heat score is elevated from prior automation runs. reCAPTCHA
Enterprise detects `navigator.webdriver=true` despite the `--disable-blink-features`
stealth flag. The `grecaptcha.execute()` call times out or returns a challenge
that requires human interaction. The same token is submitted twice (single-use
token reuse). A batch run fires multiple rapid token mints within one session.

### D3 — Selector cascade drift & Locale Invariance (Flow UI updates)
Google Flow ships a UI update that renames or restructures a selector anchor.
`FRAME_SLOTS_STRUCT` matches zero elements (the PR #70 regression). The
structural-first tier matches but selects the wrong element (e.g., two elements
matching `div[type='button'][aria-haspopup='dialog']` after Flow adds a new
dialog button). A non-English locale is used (e.g. `GFLOW_CLI_LOCALE=pt-BR` or a Brazilian Google account) and single-language English text selectors fail because they lack Tier 1 structural anchors (`a[href*='changelog']`, `button:has(i:text('close'))`) or Tier 2 multi-locale text cascades. A new locale is used whose CMP dialog is not in
`_ONBOARDING_TEXT_SELECTORS`. The `--lang=en-US` Chromium arg is removed before
`IMAGE_MODEL_OPTION_SELECTORS` is converted to structural anchors.

### D4 — Batch manifest & resume
A 50-row TSV manifest dies at row 23 (auth expiry). On resume, rows 1–22 are
re-submitted and double-billed. A row has an empty `start_image` field (T2V)
while the column parser expected a path. The output path already exists on disk
from a prior run (overwrite vs skip decision). Two `gflow video batch` invocations
against the same profile run concurrently (Chromium profile-lock).

### D5 — Concurrency & Page pool
`GFLOW_CLI_CONCURRENCY=16` fans out 16 simultaneous `page.evaluate()` reCAPTCHA
token mints — do they share site key state safely? A Page is returned to the pool
while still in a modal dialog (state contamination for the next checkout). A
`QueueFull` double-checkin race where two coroutines both attempt to return the
same Page object. `asyncio.gather` propagates one failure but leaves the other
coroutines in a half-completed state with no cleanup path.

### D6 — Data layer (SQLite / DataStore)
`DataStore` migration runs on a schema that's one version ahead (newer-schema
detection should raise `DataStoreError` exit 16, not silently corrupt). An
`OperationRecorder.on_started` callback raises inside a generation loop — does
the batch continue or crash? `GFLOW_CLI_HISTORY_PROMPTS=redacted` is set — does
the new callsite respect the redaction gate? A Windows path with a drive letter
is stored in the DB and retrieved on Linux (or vice versa). The DB is on a
network filesystem and the write locks time out.

### D7 — Error propagation & exit codes
A new exception class is added but not registered in `EXIT_CODE_MAP`. A
`FlowApiError` subclass is raised inside a retry loop — does `reraise=True`
propagate the right typed exception or does tenacity eat it? `WireFormatError`
discovery payload logs the route body prefix — does it redact reCAPTCHA tokens
and auth headers? An unhandled exception escapes `run_with_handlers` — is it
SHA-256-hashed before logging (never raw message)?

### D8 — Cross-platform paths
A Windows user has a Unicode character in their `%LOCALAPPDATA%` path. The
output directory path contains spaces. `platformdirs` returns a different base
path on macOS vs Linux vs Windows — does the feature hardcode any of these?
`PYTHONUTF8=1` is not set — are there `cp1252` encoding traps on Windows for
prompt text or file names?

### D9 — Transport edge cases
The HTTP response body is valid JSON but has an unexpected top-level key (should
emit `WireFormatError` with discovery payload, not crash). A video generation
response omits `operations[0].operation.name` (the `omni-flash` NULL
`flow_operation_id` issue). A status-poll response arrives before the listener
is attached (`_attach_status_response_listener` race). A video download URL has
a `googleapis.com` host not in the SSRF allowlist.

### D10 — Headless vs headed environment
The feature is run in a CI environment with no display server (Linux, no Xvfb).
The Playwright context is launched headed (user has `GFLOW_CLI_HEADLESS=false`)
and the window is closed manually mid-batch. `gflow auth login --browser
internal` is used on a machine where Playwright bundled Chromium is flagged by
Google's G12 block.

### D11 — Input validation & boundary values
A prompt string is 4001 characters (Flow's apparent limit). A manifest TSV has
zero rows (after the header). `--seed` receives a negative integer or a string.
`-n 5` is passed to `gflow image t2i` (Flow's UI cap is `x4`). An aspect ratio
of `3:2` is passed (valid-looking but not in the whitelist). A `--profile` name
contains path-separator characters.

### D12 — Observability & structured log contract
A new `structlog` event is emitted — is its key name stable and documented in
`docs/ARCHITECTURE.md`? A new `error_raised` path: does it carry the full RFC
9457 Problem Details shape (`type`, `title`, `status`, `detail`, `instance`,
`remediation_hint`)? `correlation_id` is bound at the process boundary — does
it propagate into the new code path or is it missing from the event?

### D13 — MCP surface parity
gflow ships the same capability twice: as a CLI command and as an MCP tool. Every
edge case above therefore has an MCP twin — enumerate it, because the paths are not
the same code. The MCP tool can run **direct** or **queued through
`worker/codec.py`**, so a param that works via the CLI can be accepted and silently
dropped on the queued path. Ask: what does this feature look like invoked as an MCP
tool? Which errors reach the agent as a Problem Details envelope rather than an exit
code? Does the tool's docstring still describe the behaviour truthfully? The six
mirror axes are enumerated once, in `skills/check/SKILL.md` step 1b — do not restate
them here, scenario's job is only to name the MCP cases that need scenarios.

---

## Artifact location

Write the analysis to **`docs/superpowers/plans/<YYYY-MM-DD>-<feature-slug>/SCENARIO.md`**
(same directory the subsequent `/gflow:plan` writes its `PLAN.md` into) and commit it
with the plan on the feature branch. Established by the image-upscale (#171) and
locale-selectors (#170) cycles — do not leave the analysis only in conversation.

## Output format

```
# Scenario: <feature short title>

## Coverage map
<Which of the 13 dimensions are relevant for this feature? List active dimensions and why skipped dimensions are skipped.>

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D2 WAF/reCAPTCHA | WAF score elevated on profile from prior run | Critical | WafRejectionError raised with remediation hint "let score decay or use a different profile"; exit code 403-mapped | E2E live (`@pytest.mark.live`) |
| … | … | … | … | … | … |

Severity: **Critical** (data loss / billed twice / unrecoverable) · **High** (feature broken, workaround exists) · **Medium** (degraded UX, explicit error) · **Low** (cosmetic or edge-only)

Test category: **Unit** (no I/O) · **Integration** (mocked HTTP/Playwright) · **BDD** (Gherkin feature file) · **E2E smoke** (`@pytest.mark.smoke`) · **E2E live** (`@pytest.mark.live`, opt-in)

## Must-cover before merge (Critical + High)
1. …

## Deferred (Medium + Low — log as issues, not blockers)
1. …

## Suggested BDD scenarios (for `tests/features/`)
```gherkin
Feature: <feature name>
  Scenario: <scenario title>
    Given …
    When …
    Then …
```

## Known-issues cross-reference
<Any scenario that maps to an existing KNOWN_ISSUES entry — link and note whether the proposed implementation resolves, mitigates, or is blocked by it.>
```

---

## Integration & Pipeline Continuation (Next Step Handoff)

1. Run `/gflow:predict` first to validate the approach (GO/CAUTION/STOP).
2. Run `/gflow:scenario` to enumerate edge cases and build the test matrix.
3. Use the "Must-cover before merge" list as the acceptance criteria for the PLAN.md task.
4. Add BDD scenarios to `tests/features/` **before** coding (TDD is non-negotiable per AGENTS.md).
5. **Next step:** Proactively announce: **"BDD Scenarios generated. Next step: Phase 4 Implementation Plan (`/gflow:plan <feature>`)."**

---

## Provenance

Adapted from `vc-scenario` in [vibecode-pro-max-kit](https://github.com/withkynam/vibecode-pro-max-kit) (assessment 2026-05-28).
13 dimensions re-scoped to gflow-cli's specific failure surfaces: Google WAF/reCAPTCHA, Playwright selector drift, auth token lifecycle, batch resume idempotency, SQLite data layer, RFC 9457 error propagation, cross-platform Windows/macOS/Linux paths.
