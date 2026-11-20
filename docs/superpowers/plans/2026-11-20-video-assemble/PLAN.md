# Local Video Assembly Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature video-assemble` for the next task. Run `/gflow:check` before every commit.

**Goal:** Join local or catalog video clips with optional music and voiceover, entirely locally and without Flow credits.

**Architecture:** Put decoding, normalization, mixing and encoding in `services/video_assembly.py`, reusing PyAV conventions in `media.py`; keep Click and MCP as thin adapters. Resolve catalog IDs through `media_recovery` and verify paths through `verified_local_path`. Use a local `VideoAssemblyError` because `SceneConcatError` specifically means a failed Flow server-side job; reuse the chain-extra missing-dependency error and install hint.

**Predict verdict:** GO — confidence 8/10.

**Predict personas:** Architect GO (service boundary); Security GO (no auth/network); Performance CAUTION (memory and long clips; stream frames); CLI/MCP GO (typed errors, parity and no-spend); Devil's Advocate GO (spike proves PyAV; scene create remains simpler when no mix needed).

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| High | Stream mismatch and audio timeline drift | Normalize geometry/rate and resample audio; tests with varied inputs |
| High | Large media memory use | Stream decode/encode; bounded buffering |
| High | Output clobbers source | Validate canonical paths and refuse existing output |
| Medium | MCP queued payload loses options | Round-trip tests and six-axis check per `skills/check/SKILL.md` step 1b |

---

## File structure

### New files
- `src/gflow_cli/services/video_assembly.py` — local assembly domain service
- `tests/features/video_assemble.feature` — offline BDD contract
- `tests/services/test_video_assembly.py` — media behavior tests
- `tests/e2e/test_video_assemble_e2e.py` — installed local CLI/MCP smoke
- `docs/superpowers/plans/2026-11-20-video-assemble/SCENARIO.md` — scenario matrix

### Modified files
- `src/gflow_cli/cli_video.py` — Click command
- MCP tool module, worker codec and `tests/mcp/test_cli_parity.py` — mirrored tool and queued payload
- `docs/USAGE.md`, `docs/MCP.md`, `CHANGELOG.md`, `README.md` — public docs

## Severity-ranked scenarios
See `SCENARIO.md`; must-cover High items are tasks 1–3 and 6.

## Task 1 — Red service tests
**What:** Pin normalization, audio absence, mixing, path validation and failure cleanup.
**Files:** `tests/services/test_video_assembly.py`.
**Steps:**
- [x] Build compact synthetic PyAV fixtures for varied dimensions/rates and silent clips.
- [x] Assert output streams/duration/mix and safe failure behavior.
**Tests created (red):**
- [x] Heterogeneous inputs normalize and missing audio is silence.
- [x] Invalid media and output collision fail without damaging inputs.

## Task 2 — BDD contract
**What:** Bind CLI behavior scenarios offline.
**Files:** `tests/features/video_assemble.feature`, step bindings.
**Steps:**
- [x] Bind successful mixed assembly and collision refusal.
- [x] Keep feature untagged: local code only, no browser/Flow account.

## Task 3 — Service implementation
**What:** Implement streaming PyAV assembly and track mixing.
**Files:** `src/gflow_cli/services/video_assembly.py`, `errors.py` only if no reusable error fits.
**Steps:**
- [x] Normalize video to first clip dimensions and the first clip's frame rate.
- [x] Treat absent clip audio as silence; mix optional tracks and duck music when requested.
- [x] Validate volumes, inputs and output; clean partial output on failure.
- [x] Reuse typed missing-chain-extra error; use `VideoAssemblyError` for media processing because `SceneConcatError` is reserved for Flow's server-side job.

## Task 4 — Catalog resolution
**What:** Resolve each argument as local path or catalog ID.
**Files:** service or narrow helper, relevant tests.
**Steps:**
- [ ] Reuse `media_recovery` catalog lookup and `_repo_base.verified_local_path`.
- [ ] Test missing ID/file and traversal/symlink boundary behavior.

## Task 5 — CLI adapter
**What:** Add `gflow video assemble CLIP...` with fixed requested options.
**Files:** `src/gflow_cli/cli_video.py`, CLI tests.
**Steps:**
- [ ] Validate volumes in [0,1], require clips and `--out-file`.
- [ ] Emit consistent human and `--json` success output.
- [ ] Test help, errors, and dependency hint.

## Task 6 — MCP twin
**What:** Register `gflow_video_assemble`, preserving CLI semantics.
**Files:** MCP tool module, `worker/codec.py`, `tests/mcp/test_cli_parity.py`, MCP tests.
**Steps:**
- [ ] Expose all clips/options and map typed errors to Problem Details.
- [ ] Ensure direct and queued payload round-trip retains all keys.
- [ ] Verify local assembly remains available with `--no-spend`.
- [ ] Check six mirror axes in `skills/check/SKILL.md` step 1b.

## Task 7 — Documentation
**What:** Document command, tool, limits and alternative.
**Files:** `docs/USAGE.md`, `docs/MCP.md`, `CHANGELOG.md`, `README.md`.
**Steps:**
- [ ] Add Unreleased entry and CLI/MCP examples.
- [ ] Update README “Join clips” gflow-cli cell to “Yes, server-side (`scene create`) or local with music and voiceover (`video assemble`)”; remove planned link.
- [ ] Update `gflow docs` topic catalog and capabilities only if they enumerate commands/features.

## Task 8 — Installed-surface E2E
**What:** Exercise installed CLI and MCP with real local media.
**Files:** `tests/e2e/test_video_assemble_e2e.py`.
**Steps:**
- [ ] Use real Flow-generated mp4s from checkout `out/` if available; otherwise synthetic fixtures.
- [ ] Run CLI and MCP twin. Local-only: use `@pytest.mark.e2e` with no Flow cost marker, following `docs/E2E_TESTING.md`.

## Task 9 — Gates and review
- [ ] Run `/gflow:check`, doc gates, and verify e2e output.
- [ ] Record docs and parity review; no live Flow verification required for local-only processing.

## Definition of done
- [ ] All tasks complete; critical/high scenarios covered.
- [ ] `/gflow:check` green; docs, CHANGELOG and BDD updated.
- [ ] Installed CLI and MCP e2e pass with local fixtures.
- [ ] No partial output remains on failure.

## Decision log
| Date | Decision | Why | Rejected |
|---|---|---|---|
| 2026-11-20 | Normalize heterogeneous clips; keep assembly local | Flow inputs vary; PyAV spike confirms local codec/filter path | Refuse mixed formats, which blocks ordinary clips |
| 2026-11-20 | Use synthetic fixtures if checkout has no generated mp4s | E2E is local-only and should not require account/credits | Browser/Flow-dependent test |
