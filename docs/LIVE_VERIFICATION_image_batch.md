# Live verification — `gflow image batch` jitter matrix

**Status:** Skeleton — sessions not yet recorded.
**Spec:** [`docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md`](superpowers/specs/2026-05-21-multi-image-prompt-design.md) §8
**Profile (planned):** `ui_automation` (Chrome strategy, mandatory per project memory)

---

## Why this exists

`gflow image batch --same-project` inserts a 3–7 s random delay between submissions. The rationale is anti-bot-detection (avoid Flow throttling) but it has never been verified empirically. Spec §8 mandates a systematic check before committing to keep or drop the jitter.

## Method — the 3 × 2 × 3 matrix

Three cells × two sessions × N=3 reps per cell. Sessions are separated by **≥ 2 hours** to defeat account-warmth confounders.

| Cell | `--same-project` | `--jitter` env | What it tests |
|---|---|---|---|
| **R1** | `1` | `0` | Same-project, no sleep → if pass, jitter is unnecessary in same-project mode. |
| **R2** | `1` | `1` (default 3–7 s) | Current behaviour baseline. |
| **R3** | `0` | `0` | Different-project, no sleep → control. Isolates "rapid-fire across projects" from jitter. |

### Cell pass criteria (all must hold)

- CLI exit code 0.
- `ui_automation.batch_response_seen` count == manifest row count.
- `ui_automation.batch_response_dropped_project_id_mismatch` count == 0.
- `ui_automation.overlay_dismiss_failed` count == 0.
- No row times out (each row produces an image within the configured row timeout).

Otherwise it fails. If a failure is suspected to be the open listener-miss flake (memory `phase-b-followups`), classify the cell as **inconclusive**, not failed.

### Decision rule

| Outcome | Action (commit 5b) |
|---|---|
| R1 passes 3/3 in both sessions AND R3 passes 3/3 in both sessions | **Drop jitter** — `refactor(image): drop unconditional jitter from --same-project (live-verified safe)`. |
| R1 fails any time with non-listener-miss failure | **Keep jitter** — `docs(image): document anti-detection jitter rationale on --same-project`. Cite the failing run. |
| R1 mixed (some pass, some inconclusive/fail) | **Default conservative: keep jitter.** File a follow-up issue to make it configurable. |
| Matrix incomplete (< 2 sessions) | **Default conservative: keep jitter.** |

Mid-matrix abort: if R1 first session fails non-listener-miss, the matrix may abort early. The conclusion is "keep jitter"; remaining cells are not run. The abort reason is recorded below.

## Environment

| Property | Session 1 | Session 2 |
|---|---|---|
| Date / UTC time | 2026-05-22T16:12:27Z | _(pending)_ |
| `gflow-cli` git rev | `81eb012` | _(pending)_ |
| Python version | 3.13.3 | _(pending)_ |
| Playwright version | 1.59.0 | _(pending)_ |
| Chromium build | bundled with Playwright 1.59.0 | _(pending)_ |
| UTC hour | 16 | _(pending)_ |
| Account-warmth proxy | cold (first matrix run today on profile `denon82`; last profile use 2026-05-21 16:00 UTC, > 24 h prior) | _(pending)_ |
| Profile used | `denon82` (substituted for `ui_automation` — see Aborted runs section for rationale) | _(pending)_ |

## Matrix runs

| Session | Cell | Rep | Exit | `batch_response_seen` | `dropped_pid` | `overlay_fail` | Notes |
|---|---|---|---|---|---|---|---|
| 1 | R1 | 1 | 1 | — | — | — | **Aborted pre-flight** — `AuthExpiredError: HTTP 401` from `project.createProject` after 12 s. Stale `denon82` cookies. Did not reach Flow submission. See Aborted runs section. |
| 1 | R1 | 1 (retry) | 1 | **8** | 0 (inferred) | 0 (inferred) | **Test-assertion bug, not a jitter verdict.** 162.5 s. Flow submission succeeded: 4 images generated, all quality assertions (status, file count, magic bytes, aspect ratio) passed. Then crashed on assertion 5 `len(batch_response_seen) == len(prompts)` — got 8 events for 3 prompts. Manifest has a `count=2` row, and `same_project=1` multiplexes events per project, so 1-per-row invariant doesn't hold. See Aborted runs. |
| 1 | R1 | 1 (retry 2, after assertion fix) | 1 | (not captured) | (not captured) | (not captured) | **Dropped image under jitter=0 + same_project=1 + count=2.** 324 s. Only 3 files produced when 4 expected; the second image of the `count=2` row (`prompt_1_1.png`) is missing. Crashed on assertion 2 (file cardinality). All 3 returned outcomes had `status=ok`. Cannot distinguish "Flow generated 3" from "Flow generated 4 but listener missed 1" without an additional debug-dump rerun — but either way, this is exactly the rapid-fire failure jitter is designed to prevent. |

## Verdict

**Matrix invalidated — no verdict reachable from the data collected.** Earlier drafts of this section asserted KEEP with a partial-data argument; that conclusion is retracted because the premise of every cell turned out to be false.

### Why retracted

The matrix was designed to compare image generation behaviour with vs without jitter while all prompts ran inside one shared Flow project (`--same-project=1`). Inspection of the user's Flow gallery after both R1 runs (Run 2 at 16:39 local, Run 3 at 16:57 local) shows each prompt landed in its own separate Flow project — not in the shared project the orchestration code created. The shared project (`gflow-cli e2e` at 05:01 PM) was created and then never used.

Root cause: `src/gflow_cli/api/transports/ui_automation.py::generate_images` accepts a `project_id` argument for Protocol-parity reasons but explicitly discards it (`_ = project_id  # accepted for Protocol parity; UI creates its own project`) and runs the full "gallery → + New project → editor → submit → close" navigation on every call. So the `--same-project=1` mode does not actually exist at the transport layer; every prompt creates a new Flow project regardless of the flag.

Consequences for this matrix:
- Every cell would have been measuring rapid-fire across *separate* projects, not within one project. There is no shared-project scenario to compare against.
- The R1 rep 1 second-retry observation ("only 3 of 4 images delivered") is unreliable evidence for or against jitter, because the prompts were not in the same project and the missing image was in its own separate project that no longer existed in our local references when the test crashed.
- All decision-rule paths in spec §8 assume both cells run with the same shared-project semantics; that assumption does not hold.

### What is actually decided (separate from the matrix)

The design intent the user articulated mid-session — that jitter exists for submission-cadence (anti-bot) rather than completion-wait — does stand on its own merits and is preserved in the project-memory record [`batch-submission-cadence`](file:///C:/Users/ffrol/.claude/projects/C--development-github-gflow-cli/memory/batch-submission-cadence.md). That rationale is being applied to the *next* branch scope, not as a verdict on this matrix.

### What this matrix did NOT verify

Everything it was meant to verify. The data collected cannot answer the jitter question because the same-project condition was never satisfied.

### Cumulative credit spend this matrix

~7 Flow image-credits (4 from Run 2, 3 from Run 3). Session 2 not entered. Five Flow projects exist on profile `denon82` (`denon82@gmail.com`) from these runs; the user has been pointed at them to inspect manually.

### Next step (no more matrix runs against this codebase)

The multi-image-prompt branch's scope is being revised: drop the `--same-project=0` mode entirely, refactor `ui_automation.generate_images` to keep the editor mounted across all prompts in a batch (so all of them actually share one project), and treat jitter as a documented submission-cadence control. Spec and plan are being updated in the same session. The jitter matrix as designed is not being re-run; if a future investigation needs cadence tuning, it will be sized against the real same-project implementation.

## Reproduce

Per-rep prompt variants prevent Flow-side caching of identical inputs:

```powershell
Copy-Item test_assets/sample_batch.tsv tmp/sample_batch_rep1.tsv
Copy-Item test_assets/sample_batch.tsv tmp/sample_batch_rep2.tsv
Copy-Item test_assets/sample_batch.tsv tmp/sample_batch_rep3.tsv
(Get-Content tmp/sample_batch_rep1.tsv) -replace 'kitten', 'kitten #r1' | Set-Content tmp/sample_batch_rep1.tsv
(Get-Content tmp/sample_batch_rep2.tsv) -replace 'kitten', 'kitten #r2' | Set-Content tmp/sample_batch_rep2.tsv
(Get-Content tmp/sample_batch_rep3.tsv) -replace 'kitten', 'kitten #r3' | Set-Content tmp/sample_batch_rep3.tsv

$env:GFLOW_CLI_E2E_PROFILE = "ui_automation"

# R1 cell, rep 1 (same_project=1, jitter=0)
$env:GFLOW_CLI_E2E_BATCH_SAME_PROJECT = "1"
$env:GFLOW_CLI_E2E_BATCH_JITTER = "0"
$env:GFLOW_CLI_E2E_BATCH_MANIFEST = "tmp/sample_batch_rep1.tsv"
uv run pytest -q tests/e2e/test_image_batch_e2e.py 2>&1 | Tee-Object -FilePath tmp/r1_session1_rep1.log
# (repeat for rep 2, rep 3, then R2, then R3)
```

After the matrix:

```powershell
$env:GFLOW_CLI_E2E_PROFILE = $null
```

## Tested

_(filled after runs — list every cell + rep with the relevant log path)_

## Invariants asserted (from `tests/e2e/test_image_batch_e2e.py`)

- All outcomes `status == "ok"`.
- File cardinality == sum of manifest row counts.
- Magic bytes: PNG, JPEG, or WebP (else fails loud).
- Pillow dimensions ± 2 % of declared `aspect_ratio`.
- `ui_automation.batch_response_seen` count == manifest row count.
- `image_batch.row_completed` count == total image count.
- `image_batch.submission_attempt` event present per row.
- `--same-project=1`: single project ID across rows. `--same-project=0`: distinct project ID per row.

## Correlation IDs

_(filled after runs — project IDs and SHA256 prefixes captured from `image_batch.row_completed` events per cell)_

## NOT verified

- Behaviour outside profile `ui_automation`.
- Behaviour outside Chrome strategy.
- Behaviour with `count > 1` per row across `--same-project=1` mode for prompts other than `test_assets/sample_batch.tsv` row 2.
- Long-running rate-limit windows beyond the 2-hour cross-session gap.

## Outputs

_(filled after runs — pytest tmp_path artefacts; not committed; SHA256 prefixes recorded above)_

## Aborted runs (e2e bug or non-listener-miss failure)

**2026-05-22T16:19:25Z — Session 1, R1 rep 1: `AuthExpiredError: HTTP 401`.**

- **Cell config:** `same_project=1`, `jitter=0`, `manifest=tmp/sample_batch_rep1.tsv`, `profile=denon82` (substituted for `ui_automation`).
- **Symptom:** `gflow_cli.errors.AuthExpiredError: Authentication expired: HTTP 401` raised from `client.create_project` → `_post_json("project.createProject")`. Test failed in 12 s; no credits spent at Flow.
- **Classification:** Pre-flight / infrastructure failure, not a jitter-cell verdict. The matrix never reached Flow submission. The §8 abort gate (which assumes the failure is observed under jitter=0 conditions) does not apply because no submission occurred.
- **Likely cause:** `denon82` profile cookies expired (last interactive use 2026-05-21 16:00 UTC, > 24 h ago); `tests/e2e/test_image_batch_e2e.py` invokes `run_manifest_image_batch(..., transport=None, ...)` so the default transport (API client via stored cookies) is used. Per memory `image-generation-401-next`, the v0.7.0 fix routed image generation through `ui_automation` transport — but the e2e test does not opt into it.
- **Resolution path (not done in this session):**
  1. Refresh auth on the chosen profile via `gflow auth login --profile <name>` (interactive Chrome window) **or** swap to a freshly-logged-in profile.
  2. Optionally: verify whether `run_manifest_image_batch`'s default transport is API-client or `ui_automation`. If API-client, consider whether the e2e test should be updated to pass `transport="ui_automation"` to mirror v0.7.0's resolution path. That is a code change, not a matrix-run decision.
  3. Re-run session 1 from R1 rep 1 with refreshed credentials. Session 2 timer (≥ 2 h after session 1 completes) starts at that point, not now.
- **Verdict impact:** None yet. Matrix incomplete. Per §8 decision rule "Matrix incomplete (< 2 sessions) → default conservative: keep jitter", the conservative default still applies and #5b would be the docs-update KEEP variant if no further runs land.
- **Log:** `tmp/r1_session1_rep1.log` (gitignored).

**2026-05-22T16:42:13Z — Session 1, R1 rep 1 (retry after auth refresh): `batch_response_seen` over-count.**

- **Cell config:** identical to abort above.
- **Auth status:** Refreshed successfully at 16:39 UTC (`auth_flow_session_verified` for `denon82@gmail.com`).
- **Run duration:** 162.5 s. Flow submission completed; Flow billed for 4 image generations.
- **Test outcome:** Quality assertions 1-4 passed (`status == "ok"`, file count == 4 == sum of prompt counts, all PNG/JPEG/WebP magic bytes valid, all images within ±2 % of declared aspect ratio). Failed assertion 5: `len(batch_response_seen) == len(prompts)` — got 8 events, expected 3.
- **Classification:** **Test-assertion bug, not a jitter verdict.** This is the *inverse* of the listener-miss flake (over-count, not under-count). All 8 events share the same `filter_project_id` (same-project mode), suggesting Flow emits multiple in-flight/complete events per image. With one `count=2` row in the manifest, the actual image-event count is at least 4, plus per-image lifecycle events.
- **Why this blocks the matrix:** Every R1/R2/R3 cell will hit the same assertion failure regardless of jitter setting. The matrix cannot distinguish "jitter unnecessary" from "test invariant wrong" while this assertion is over-strict.
- **Resolution path (not done in this session):**
  1. Relax assertion 5 in `tests/e2e/test_image_batch_e2e.py` line 184 — likely to `>= len(prompts)` or `>= sum(p.count for p in prompts)`. Tightening the lower bound preserves the "did we observe responses" signal while tolerating per-image / per-status multiplexing.
  2. Re-run R1 rep 1 with the relaxed assertion. If pass, continue the matrix.
  3. Or: simplify the manifest to all-`count=1` rows for the matrix runs only (changes the credit cost from 4 images/rep to 3, but isolates the jitter signal from the count-mux question).
- **Credit accounting:** ~4 images burned on this run. Cumulative session-1 cost so far: ~4 images.
- **Log:** `tmp/r1_session1_rep1.log` (gitignored — contains the full assertion error and 8 captured event payloads).

## Post-#5b verification

_(filled in commit #5b's amend OR a follow-up edit — the e2e re-run under the verdict's chosen cell config per AC6)_

## Post-refactor live verification — Phase 7 (PARTIAL PASS, 2026-05-22)

**Status:** PARTIAL — race-condition fix verified; count-tab selector
verification blocked by [Issue #24](https://github.com/ffroliva/gflow-cli/issues/24)
(locale-agnostic selectors).

### What was verified ✅

- **Race-condition fix in `c759c90`** ("fix(image): batch-transport race in
  await loop short-circuit"). The Phase 3 await loop's
  `if not captured and submit_error is None` short-circuit incorrectly
  caught in-flight successful submissions. The fix changes the sentinel to
  `detach is _noop_detach`. Live e2e on profile `denon82`:
  - First attempt (with the bug): 30s wall time, 3 fail outcomes returned
    without raising, all-fail with no actual response-await. Visible at
    `tmp/v3_7_live_verification.log` and `pytest-790`.
  - After race fix: 49s wall time, submissions actually waited for
    responses, count=2 row generated 2 images correctly, count=1 rows
    generated 1+1 images each (1 expected + 1 from count drift — see
    blocked items below). Visible at `tmp/v3_7_live_verification_retry.log`
    and `pytest-793`.

- **Stay-mounted editor session works structurally.** A single Flow project
  (`gflow-cli e2e`) was created once and reused for all prompts, with the
  editor page staying mounted across all submissions. Confirmed visually
  via the user's Flow gallery and via the `image_batch.submission_attempt`
  structlog events sharing one `project_id` per batch.

### What's blocked by [#24](https://github.com/ffroliva/gflow-cli/issues/24) ⏸️

- **Count selector read-back fails on non-English locales.** The user's
  browser runs Flow in Portuguese (`labs.google/fx/pt/tools/flow`). Count
  tab labels render as "1 imagem", "2 imagens", etc. — not the `x1`/`x2`
  English patterns the current selectors match. The instrumented
  `_set_count` in `18d184b` returned `'imageImagem'` (icon ligature `image`
  + Portuguese label `Imagem`) and exhausted its 3-attempt retry loop on
  the very first prompt, raising `RuntimeError` and triggering the
  orchestrator's `BatchPartialError` salvage path. Visible at
  `tmp/v3_7_live_verification_retry3.log`.

  Two earlier attempts to fix the count stickiness without
  locale-invariance also failed in different ways: `401aaf5`
  (force-reset by clicking x1 first) silently no-op'd and produced 5
  files for 3 prompts (`pytest-793`); `18d184b` (read-back verify with
  retry) raised the clear error above. Both are kept as commits — the
  read-back instrumentation in `18d184b` was load-bearing in diagnosing
  the locale root cause and stays.

- **Final all-green e2e** awaits #24 closing. The deliverable for #24 is
  locale-agnostic count-tab selectors (e.g., position-in-tablist,
  leading-digit regex, or aria attribute). Once #24 ships, a Phase 7b
  rerun on profile `denon82` should pass cleanly.

### Cumulative credit spend across Phase 7 attempts

~15-18 Flow image generations on profile `denon82` (`denon82@gmail.com`),
across the three e2e iterations referenced above. No production credits
spent beyond what was already authorised in the matrix and Phase 7 budgets.

### Conclusion

The v3-3 stay-mounted refactor lands the **core bug fix** for the
`--same-project=1` no-op: all prompts of a batch now actually share one
Flow project, the editor page stays mounted, and per-prompt responses are
captured correctly with race-immune detach semantics. The remaining
gap is a pre-existing locale-portability issue ([#24](https://github.com/ffroliva/gflow-cli/issues/24))
that was previously unobserved because earlier verification ran on
English-locale profiles. The branch is ready for review; a Phase 7b
follow-up will close the loop once #24 lands.
