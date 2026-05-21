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
| Date / UTC time | _(pending)_ | _(pending)_ |
| `gflow-cli` git rev | _(pending)_ | _(pending)_ |
| Python version | _(pending)_ | _(pending)_ |
| Playwright version | _(pending)_ | _(pending)_ |
| Chromium build | _(pending)_ | _(pending)_ |
| UTC hour | _(pending)_ | _(pending)_ |
| Account-warmth proxy | _(pending — count of `ui_automation.*` events in prior 60 min from structlog history, or "cold" if first run today)_ | _(pending)_ |

## Matrix runs

| Session | Cell | Rep | Exit | `batch_response_seen` | `dropped_pid` | `overlay_fail` | Notes |
|---|---|---|---|---|---|---|---|
| _(none)_ | _(pending)_ | | | | | | |

## Verdict

_(pending — will be filled after both sessions complete per the §8 decision rule)_

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

_(filled if mid-matrix abort triggers)_

## Post-#5b verification

_(filled in commit #5b's amend OR a follow-up edit — the e2e re-run under the verdict's chosen cell config per AC6)_
