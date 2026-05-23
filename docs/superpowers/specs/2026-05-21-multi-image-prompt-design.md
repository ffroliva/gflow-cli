# Multi-image prompt — production-ready landing of PR #35

**Status:** Design (council-hardened), **v4 revision in effect — see banner below**
**Author:** Flavio Oliva
**Date:** 2026-05-21 (v3) · 2026-05-22 (v4 revision)
**Closes:** [#14](https://github.com/ffroliva/gflow-cli/issues/14)
**Supersedes:** [PR #35](https://github.com/ffroliva/gflow-cli/pull/35) (`claude/plan-next-issue-Stegy`)
**Target branch:** `feature/multi-image-prompt` → `develop`
**Revision:** v4 — `--same-project` transport defect discovered mid-matrix; scope reshaped (see § "v4 revision" below). v3 — full seed/batch_id removal after code verification revealed `--seed` is a documented no-op under the active UI transport (v2 was too conservative; user authorised Option A).

---

## v4 revision — scope reshape (2026-05-22)

The jitter matrix in §8 was run against the codebase and immediately surfaced a structural defect that invalidates the matrix's premise. This v4 amendment records what changed; the v3 text below is preserved as a historical record except where explicitly marked superseded.

### What was discovered

`src/gflow_cli/api/transports/ui_automation.py::generate_images` accepts a `project_id` argument for Protocol parity but discards it (`_ = project_id  # accepted for Protocol parity; UI creates its own project`) and runs a full `gallery → "+ New project" → editor → submit → close` cycle on every call. The orchestrating code in `image_batch.run_manifest_image_batch` correctly creates a shared project under `--same-project=1` and threads its ID into the transport, but the transport drops it on the floor. Live verification on profile `denon82` confirmed each prompt lands in its own separate Flow project regardless of the flag.

Therefore the matrix was never measuring rapid-fire submissions *inside* a shared project — there is no such submission cadence in the current code. The matrix is invalidated. See `docs/LIVE_VERIFICATION_image_batch.md` § Verdict for the retraction record.

### What the v4 scope is

Driven by user clarification 2026-05-22 (project-memory record [`batch-submission-cadence`](file:///C:/Users/ffrol/.claude/projects/C--development-github-gflow-cli/memory/batch-submission-cadence.md)):

1. **`gflow image batch` is always same-project.** All prompts in one batch share one Flow project, by design. There is no "different-project batch" mode. If a user wants different projects, they loop `gflow image t2i` externally.
2. **The `--same-project` CLI flag is removed entirely.** It collapses from a toggle to the only behaviour; documentation and `--help` should describe always-same-project semantics.
3. **`run_manifest_image_batch` drops its `same_project: bool` parameter and the entire `same_project=False` branch.** The `transport=None` plumbing remains.
4. **`ui_automation.generate_images` is refactored to a stay-mounted shape.** The editor page is opened once for the batch, then each prompt is submitted via the in-editor prompt input with jitter between submissions, generation_ids are captured at submission time (in memory), completions are awaited after all submissions are sent, and the page is closed only at the very end of the batch.
5. **Jitter is documented as a submission-cadence (anti-bot) control**, not a completion-poll setting. CLI help, `docs/USAGE.md`, and the public docstring on `run_manifest_image_batch` carry this rationale.
6. **No persistence layer in this branch.** Generation_ids stay in memory just long enough to download. Persistent (profile, project_id, generation_id, file) records are a separate phase — see [`phase-b-followups`](file:///C:/Users/ffrol/.claude/projects/C--development-github-gflow-cli/memory/phase-b-followups.md) item #10.
7. **No second jitter matrix.** The matrix as designed cannot answer the original question without the stay-mounted refactor in hand; if cadence tuning is needed later, it will be sized against the real implementation, not the broken one.

### What v4 supersedes in the v3 body

- **§3 In scope** — `--same-project` flag stays in CLI surface. **Superseded:** the flag is removed.
- **§4 Strategy / commit chain** — commit #5b's verdict-driven content was either drop-jitter (code) or document-jitter (docs). **Superseded:** #5b becomes the stay-mounted refactor + flag removal; new commit chain will be defined in the plan v3.
- **§8 Live verification matrix** — 3-cell × 2-session × N=3 matrix. **Superseded:** not run, not re-run; this section is historical only.
- **§9 Decision rule (if §8 was numbered there)** — KEEP/DROP/INCONCLUSIVE-KEEP table. **Superseded:** verdict is "matrix invalidated, scope reshaped" — see this banner.
- **§11 Risk register** entries that depended on the matrix outcome are now moot. The defect itself becomes the headline risk for the new scope; mitigations are unit + e2e tests on the stay-mounted code path.

### What v4 leaves untouched in the v3 body

- `--seed` removal (already shipped on the branch).
- The native xN count-selector bugfix (already shipped).
- TSV/JSON manifest parser, max-prompts cap, malformed-manifest negative fixture.
- Application-layer observability events (`image_batch.submission_attempt`, `image_batch.row_completed`).
- Docs commits (`USAGE.md`, `CHANGELOG.md`, `INDEX.md`) — they will be amended for the always-same-project surface, not torn up.
- The PR-supersedes-#35 plan.

### Implementation cost (rough)

The stay-mounted refactor in `ui_automation.py` is the substantive piece. It needs a session abstraction (`_BatchEditorSession` or similar) that opens once, holds the editor page, submits N prompts via the existing prompt-input helpers, captures generation_ids, awaits completions, downloads, and closes. Unit tests can mock the page; one credit-spending e2e at the end verifies all-prompts-one-project. Persistence and per-project Chrome-session multiplexing remain explicitly out of scope.

---

## 1. Problem

PR #35 already contains a working bugfix + feature for issue #14, but it is a *starting point*, not production-ready:

- Branch name `claude/plan-next-issue-Stegy` violates the project convention (`feature/`, `bugfix/`, `fix/`, `docs/`, `test/`, `chore/`, `release/`).
- Single squashed commit is authored by `Claude <noreply@anthropic.com>`; the PR body has a "Generated by Claude Code" footer. CLAUDE.md requires human-only attribution.
- No end-to-end test exists for `gflow image batch` or for the `-n N` native count selector — only unit tests.
- `--same-project` ships with a hard-coded 3–7s random jitter whose rationale (anti-bot-detection) is **plausible but unverified**. The project's standard is to *verify systematically, not guess*.
- The batch path emits no observability events at the application layer (only the UI transport emits `batch_response_seen` / `batch_response_dropped_project_id_mismatch`). A future throttling regression would have no telemetry breadcrumb.
- **`--seed` is a documented user-facing flag on `gflow image t2i` and `gflow image i2i`, but it has been a silent no-op since v0.7.0** — the user's seed is discarded by the `_ = seed, batch_id` shim in `_drive_image_generation` before reaching any transport. The active UI transport doesn't use seeds. Misleading API surface.
- Docs (`docs/USAGE.md`, `CHANGELOG.md [Unreleased]`, `docs/INDEX.md`) are not updated.
- Sample manifests for users to copy-paste are missing from `test_assets/`.

> **Note on the dead `--seed` user-facing feature.** Verification revealed `--seed` is advertised in `gflow image t2i --help` (see `src/gflow_cli/cli_image.py:288-291` and example at line 237) **but is a no-op under the active UI transport since v0.7.0**. Full trace: CLI passes `seed=42` to `client.generate_image` (`cli_image.py:493,726`) → mints/forwards at `client.py:723` → calls `_drive_image_generation(seed=42, batch_id=..., ...)` → private helper does `_ = seed, batch_id` (the shim) and discards them → delegates to `_drive_images_generation(req=...)` with no seed → UI transport ignores it (clicks buttons, no body builder). The `seed` field on the wire body is only populated by the experimental HTTP transports (bearer/evaluate_fetch/sapisidhash) which **mint locally** (`seed=0` hardcoded in two of them); they never receive the user's seed either. **Decision (v3):** delete `--seed` from the CLI, remove `seed`/`batch_id` from public `generate_image`, private `_drive_image_generation`, and `generate_images_batch`'s `seeds=` parameter. Keep `seed`/`batch_id` parameters on the body builder (wire format, internal to experimental transports). Honest API surface. CHANGELOG `### Removed`.

## 2. Goal

Land a **production-ready, complete, correct, e2e-tested** multi-image prompt feature on `develop` via a clean PR from `feature/multi-image-prompt`, with:

- the bugfix and the new subcommand functionally unchanged where they're already correct;
- **honest API surface** — no documented features that are silently no-ops (current `--seed` is misleading);
- the jitter question **resolved empirically**, not by guesswork, against an experimental matrix that isolates jitter from confounders;
- application-layer observability so a future regression in throttling behaviour is detectable without re-instrumentation;
- a parameterized live e2e in the existing `tests/e2e/` family using the project's canonical `GFLOW_CLI_E2E_*` env-var convention and DI-based jitter override;
- a malformed-manifest negative fixture exercised by a unit test;
- documentation updates and committed sample fixtures;
- a clean commit history attributed to the human user, with no AI co-author tags.

## 3. Scope

### In scope

- New branch `feature/multi-image-prompt`, cut from current `origin/develop` (which is ahead of PR #35 by 5+ commits — #36 video-download merge, t2v docs, stale-stub removal, plan updates).
- Re-author and re-message the PR #35 work as human-authored commits, split atomically.
- **Remove `--seed` from `gflow image t2i` and `gflow image i2i` CLI commands** (`src/gflow_cli/cli_image.py:237-239,288-291,456-465,544-546`). Drop the help-text examples and the `UsageError` cross-flag validations.
- **Remove `seed: int | None = None` and `batch_id: str | None = None` from `client.generate_image`** (`src/gflow_cli/api/client.py:688-696`). Remove the internal `secrets.randbelow(2**31)` mint and `_new_batch_id()` fallback at lines 723-724. Update docstring (remove the "idempotency by seed" paragraph at lines 709-711 — it's been false since v0.7.0).
- **Remove `seed`/`batch_id` kwargs from `_drive_image_generation`** (the private helper) and the `_ = seed, batch_id` shim. Method becomes a thin delegator to `_drive_images_generation` with count=1.
- **Remove `seeds: Sequence[int] | None = None` parameter from `generate_images_batch`** and the `seeds_list = [secrets.randbelow(...) for _ in range(count)]` mint at line 775. Update docstring (no more "per-shot seeds" — they were never threaded through).
- **Update test_client_image.py** — replace all 9 `seed=...` kwarg occurrences (lines 134, 149, 178, 196, 206, 234, 659, 703, 747) with no-seed calls. Delete any tests whose entire purpose was asserting seed plumbing (none functional under UI transport).
- **Update USAGE.md** — remove any `--seed` mentions.
- Keep `seed`/`batch_id` parameters on `_build_batch_generate_images_body` (`src/gflow_cli/api/image.py:208-213`) — wire-format level, internal to the experimental HTTP transports which mint locally. The body builder is **not** "leftover"; it is the documented wire schema mirror of `samples/captured/06_batchGenerateImages.json` and `07_batchGenerateImages_seeded.json`.
- If `_new_batch_id()` becomes unreferenced after the cleanup, delete it (don't leave orphan helpers).
- New application-layer observability events emitted from `image_batch.py` (see §8):
  - `image_batch.submission_attempt`
  - `image_batch.submission_result`
  - `image_batch.row_completed`
  - `image_batch.inter_submission_latency_ms`
- Sample manifests under `test_assets/sample_batch.tsv` and `test_assets/sample_batch.json` (note: hygiene gate at `scripts/ci/check_repo_hygiene.py:35` blocks `test_assets/{smoke_,debug_}*` only — non-prefixed sample fixtures are allowed).
- A malformed-row negative fixture (`test_assets/sample_batch_invalid.tsv`) exercised by a **unit** test only (never the live e2e — saves credits).
- New live e2e `tests/e2e/test_image_batch_e2e.py` parameterized by `GFLOW_CLI_E2E_*` env vars (canonical pattern from `tests/e2e/test_video_t2v_e2e.py`), gated by presence of `GFLOW_CLI_E2E_PROFILE`, profile must be Chrome-strategy.
- Jitter override in the e2e via **dependency injection** — pass `jitter_range=(0.0, 0.0)` to `run_manifest_image_batch` (the parameter already exists in PR #35 at `src/gflow_cli/image_batch.py:597`). No monkeypatching, no production-code conditional on test env vars.
- Jitter investigation evidence written to `docs/LIVE_VERIFICATION_image_batch.md` (same shape as `docs/LIVE_VERIFICATION_video_download.md`).
- A jitter decision in code (keep / drop / make configurable) **driven by the evidence collected**, landed as a pair of separate commits (5a docs, 5b refactor-or-docstring) so the rationale lives in the message.
- `docs/USAGE.md` section for `gflow image batch`, `CHANGELOG.md [Unreleased]` entries (`### Fixed` for the `-n N` wiring, `### Added` for `image batch`), `docs/INDEX.md` row for the new live-verification doc.
- Close PR #35 with a comment pointing at the new PR. Delete `origin/claude/plan-next-issue-Stegy` (remote) and any local checkout of it.

### Out of scope (deferred)

- **Restoring seed-controlled reproducibility under the UI transport.** Flow's UI may not expose a seed surface at all — research blocked on Flow's behaviour, not on us. If/when the experimental HTTP transports go live again, the seed plumbing rejoins via the body builder (which still takes `seed`/`batch_id`); no public API needs to change.
- **Cosmetic rename** `_drive_image_generation` → `_drive_one_image` and `_drive_images_generation` → `_drive_image_batch`. Real readability win (one-letter footgun) but atomicity-breaking when mixed with the bugfix. Filed as follow-up issue, not landed here.
- Reworking `--same-project` into a general `--jitter MIN MAX` configurable knob — only on the table if the investigation specifically shows tunability is needed.
- Touching `gflow video` or any other unrelated CLI.
- Architecture-layer refactors (e.g. moving image-batch into a hypothetical `application/commands/` package per the target architecture in CLAUDE.md).
- Adding parallel-batch execution. `--same-project` stays sequential by design.
- Coverage-floor bumps. We stick to the 80% / 90% policy already in CLAUDE.md.

## 4. Strategy — branch and PR

**Strategy A: close PR #35, open a fresh PR from `feature/multi-image-prompt`.**

Rejected alternatives:
- **B (force-push rebase, keep PR #35):** `gh pr edit` cannot change the head ref, so we'd be stuck with `claude/plan-next-issue-Stegy` as the branch name. Violates branch-naming convention.
- **C (rebase old branch + API-swap PR head):** Possible via raw GitHub API but fragile and offers no value over A. PR #35 has zero reviews to preserve.

**Execution sketch:**

```text
git fetch --all --prune
git checkout -b feature/multi-image-prompt origin/develop

# Mechanism §6.A — tree-replay + staged commits (NEVER `git rebase -i`)
# Carries forward the PR #35 work re-authored to the human.

# After all 5 commits land locally:
git push -u origin feature/multi-image-prompt

# PR body MUST start with: "Supersedes #35."
gh pr create --base develop \
  --title "feat(image): native count selector + gflow image batch (closes #14)" \
  --body "$(cat <<'EOF'
Supersedes #35.

[full body — see §9 docs]
EOF
)"

# Close the original PR with a back-pointer.
gh pr close 35 --comment "Superseded by #<new>. Branch renamed to feature/multi-image-prompt per branch-naming convention; commit history re-authored to remove AI attribution; live e2e + observability + docs added."

# Delete the lingering `claude/*` ref locally and remotely.
git push origin --delete claude/plan-next-issue-Stegy
git branch -D claude/plan-next-issue-Stegy   # only if present locally
```

## 5. Commit structure

One commit per logical change. Each commit must pass `/gflow:check`.

1. **`fix(image): use native xN count selector for -n N (#14 part 1)`**
   - `_drive_image_generation` (singular) now delegates to a new `_drive_images_generation` (plural, returns list).
   - `generate_images_batch` single-call refactor (one transport call with `count` baked into `req`, no `asyncio.gather` fan-out).
   - Body builder confirmation: `_build_batch_generate_images_body` at `src/gflow_cli/api/image.py:208` already reads `count` from the request via the `GenerateImageRequest.count` field (PR #35 added the validation at line 191). No further change needed there.
   - Carry forward the count-selector deltas in `tests/api/test_client_image.py` (≈180 lines changed in PR #35, mostly removals of the old fan-out tests + new single-call assertions). **Seed-related test deletions belong to commit #1b, not here.**
   - **Independence claim:** commit #1 has zero dependency on `src/gflow_cli/image_batch.py` or `src/gflow_cli/cli_image.py` batch-wiring AND zero dependency on the seed-cleanup in #1b, so it can be cherry-picked to a `release/*` branch independently of the feature and of the cleanup.

1b. **`refactor(image,cli): remove no-op --seed flag and dead seed/batch_id params`** (BREAKING CHANGE)
   - **CLI (`src/gflow_cli/cli_image.py`):** delete `--seed` click option from `t2i` (lines ~288-291) and from `i2i`. Delete the help-text examples at lines 237-239 and 544-546. Delete the cross-flag `UsageError` validations at lines 456-465 (no longer relevant once the flag is gone). Drop `seed=seed` from the two `generate_image` call sites (lines 493, 726).
   - **Public client (`src/gflow_cli/api/client.py`):** delete `seed: int | None = None` and `batch_id: str | None = None` from `generate_image`'s signature (lines 693, 695). Delete the `secrets.randbelow` mint and `_new_batch_id()` fallback at lines 723-724. Update the docstring — drop the false "idempotency by seed" paragraph (lines 709-711).
   - **Private path (`src/gflow_cli/api/client.py`):** delete `seed: int` and `batch_id: str` kwargs from `_drive_image_generation`. Delete the `_ = seed, batch_id` shim and its comment. Method becomes a thin `count=1` delegator to `_drive_images_generation`.
   - **Batch (`src/gflow_cli/api/client.py`):** delete `seeds: Sequence[int] | None = None` from `generate_images_batch`. Delete the `seeds_list` mint (line ~775) and `shared_batch_id = _new_batch_id()` (line ~790). Update docstring.
   - **`_new_batch_id()`:** delete the helper if it has no remaining callers after the cleanup. Otherwise leave it (`/gflow:check` will surface ruff-unused-function if dead).
   - **Tests (`tests/api/test_client_image.py`):** delete `seed=...` from all 9 call sites (lines 134, 149, 178, 196, 206, 234, 659, 703, 747). Delete or repurpose any tests whose entire assertion was "seed flows to the body" — none functional under the active UI transport.
   - **Tests (CLI):** if `tests/cli/test_cli_image.py` (or equivalent) has tests for the `--seed` validation rules, delete them.
   - **Docs (`docs/USAGE.md`):** remove `--seed` references.
   - **Wire-format body builder is UNCHANGED.** `_build_batch_generate_images_body(seed, batch_id)` keeps both params — they live at the wire-protocol layer, internal to the experimental HTTP transports which mint locally.
   - `CHANGELOG.md` entry for this commit goes into the docs commit (#5 below) under `### Removed` per the project's batched-changelog style.
   - **Independence claim:** #1b can be reverted without touching #1, #2, or any other commit. It is a self-contained dead-feature deletion.

2. **`feat(image): add gflow image batch subcommand with --same-project (#14 part 2)`**
   - `image_batch.py` additions: `MAX_BATCH_PROMPTS = 5`, `JITTER_MIN_SECONDS`, `JITTER_MAX_SECONDS`, manifest dispatcher, JSON/TSV parsers, `run_manifest_image_batch` (with `jitter_range` parameter for DI).
   - `cli_image.py` wiring for `gflow image batch`.
   - **New structlog events** (per council methodology review):
     - `image_batch.submission_attempt` `{row_idx, prompt_hash, aspect, model, same_project, jitter_enabled, t_since_prev_submit_ms}`
     - `image_batch.submission_result` `{row_idx, outcome: ok|timeout|dropped|overlay, latency_ms}`
     - `image_batch.row_completed` `{row_idx, file_path, sha256_prefix}`
     - `image_batch.inter_submission_latency_ms` (computed; promoted to first-class event so a future throttling regression is detectable via grep on `structlog` output).
   - `test_assets/sample_batch.tsv`, `test_assets/sample_batch.json`, `test_assets/sample_batch_invalid.tsv` (malformed rows for unit test).
   - Carry forward `tests/image_batch/test_image_manifest.py` (336 net-new lines) plus a new unit test that pins the malformed-row fixture to `ConfigurationError`.
   - Unit tests for the new structlog events (use `caplog` or `structlog.testing.LogCapture`).

3. **`test(e2e): live image batch e2e parameterized by same-project + DI jitter`**
   - `tests/e2e/test_image_batch_e2e.py`, gated by presence of `GFLOW_CLI_E2E_PROFILE` (per the canonical pattern in `tests/e2e/test_video_t2v_e2e.py`).
   - Env-var matrix per §7.
   - Jitter override mechanism: pass `jitter_range=(0.0, 0.0)` directly to `run_manifest_image_batch` from the test fixture. **Not** a monkeypatch on `asyncio.sleep`. **Not** a production-code env-var branch.
   - References `test_assets/sample_batch.tsv` so the test is self-contained.
   - **Module-level `pytestmark = pytest.mark.e2e`** so collection honours the marker.
   - **`from __future__ import annotations`** at top per CLAUDE.md.

4. **`docs: gflow image batch in USAGE, CHANGELOG, INDEX`**
   - `docs/USAGE.md` section for `gflow image batch`.
   - `CHANGELOG.md [Unreleased]` entries (`### Fixed` for the count selector, `### Added` for `image batch` and the new observability events).
   - `docs/INDEX.md` row for `LIVE_VERIFICATION_image_batch.md` (file added in commit #5a).

5. **Evidence-driven** — two commits, even when the matrix says "keep jitter":

   **5a.** `docs(image): jitter live-verification evidence for image batch`
   - `docs/LIVE_VERIFICATION_image_batch.md` capturing the matrix outcomes per §8.

   **5b.** Either
   - `refactor(image): drop unconditional jitter from --same-project (live-verified safe)` — set the default `jitter_range=(0.0, 0.0)` in `run_manifest_image_batch` (or remove the sleep entirely) — citing commit 5a's evidence file in the body.
   - OR
   - `docs(image): document anti-detection jitter rationale on --same-project` — add a docstring + module-level comment to `image_batch.py` citing the evidence file and listing the failure modes observed.

   (Split exists per CLAUDE.md "small, atomic commits" — separating evidence capture from code/docstring change is critical for revert isolation.)

## 6. Mechanism for re-authoring the PR #35 work

**6.A — tree-replay + staged commits (the only mechanism we will use).**

  1. `git checkout -b feature/multi-image-prompt origin/develop`
  2. Identify PR #35's commit: `e8f932a`.
  3. `git checkout e8f932a -- src/gflow_cli tests test_assets pyproject.toml CHANGELOG.md uv.lock`
  4. `git restore --staged .` — un-stage everything so we can re-stage by area. (Spelled out per council nit: bulk checkout auto-stages on Windows in some configs.)
  5. Stage commit #1's files (only the count-selector refactor + relevant client tests), commit. Author auto-resolves to the human (no `--author` override needed — tree-replay drops the original author).
  6. Stage commit #2's files (image_batch.py, cli_image.py wiring, sample fixtures, new observability events, all 336+ lines of image_batch tests), commit. Add the new structlog events to the same commit.
  7. Add commit #3 (e2e) — net-new file.
  8. Add commit #4 (docs).
  9. Add commit #5a, run the matrix (§8), then commit #5b.

**6.B — DO NOT USE.** `git rebase -i` + `git commit --amend --reset-author`. CLAUDE.md and the BLOCKED list forbid interactive git in the agent loop. Documented here only as a human-pairing fallback; **must not** be executed by an agent under any circumstances.

> **Note on the version bump in PR #35's diff.** PR #35 contained a `pyproject.toml` bump from `0.6.0a6 → 0.7.0` and a CHANGELOG footer update. Both are already on `develop` via the v0.7.0 release (PR #33). Tree-replay from `e8f932a` onto current `develop` produces no diff for these hunks — they silently disappear, which is correct. We do **not** bump the version in this PR; the next release (whichever it is) will do that.

## 7. E2E test design

File: `tests/e2e/test_image_batch_e2e.py`

Patterns mirrored from `tests/e2e/test_video_t2v_e2e.py`:
- `pytestmark = pytest.mark.e2e`
- Skip-by-presence: collection runs `pytest.skip(...)` if `GFLOW_CLI_E2E_PROFILE` is unset.
- Profile resolution via the existing `tests/e2e/conftest.py:resolve_e2e_profile_dir` (or equivalent helper present at `tests/e2e/conftest.py:21`).
- Chrome-strategy fail-fast on non-Chrome profile per `real-browser-auth-mandatory` memory.

**Env vars (aligned with the canonical `GFLOW_CLI_E2E_*` prefix):**

| Variable | Default | Purpose |
|---|---|---|
| `GFLOW_CLI_E2E_PROFILE` | unset → skip | Master gate. Test is skipped unless set. Must be a Chrome-strategy profile. |
| `GFLOW_CLI_E2E_BATCH_MANIFEST` | `test_assets/sample_batch.tsv` | Manifest under test. |
| `GFLOW_CLI_E2E_BATCH_SAME_PROJECT` | `0` | `1` enables `--same-project`. |
| `GFLOW_CLI_E2E_BATCH_JITTER` | `1` | `0` ⇒ test fixture passes `jitter_range=(0.0, 0.0)` via DI. |

Output dir is the pytest `tmp_path` fixture (no hand-rolled timestamp, avoids Windows `:`-in-path issues raised by R4).

**Assertions:**

1. CLI exit code 0.
2. **File cardinality:** `len(output_files) == sum(row.count for row in manifest)`. Naming/ordering deterministic.
3. **Magic bytes:** every file's first 4 bytes match PNG (`\x89PNG`) **or** JPEG (`\xff\xd8\xff`). Imagen can return JPEG for some aspects.
4. **Image dimensions:** `PIL.Image.open(f).size` aspect ratio is within ±1% of the manifest row's `aspect_ratio`.
5. **Listener event** `ui_automation.batch_response_seen` count == number of manifest rows (one submission per row, regardless of `count`). Verified to exist at `src/gflow_cli/api/transports/ui_automation.py:783`.
6. **New application event** `image_batch.row_completed` count == number of manifest rows.
7. **`--same-project=1` only:** all rows used the same Flow project ID (extracted from `image_batch.submission_result` events).
8. **`--same-project=0` only:** N distinct project IDs across N rows.

**Sample manifests (committed):**

`test_assets/sample_batch.tsv`
```
a small calico kitten sitting on a windowsill
a watercolor sunset over rolling hills	2	16:9
an isometric pixel-art bakery	1	1:1	nano2
```

`test_assets/sample_batch.json`
```json
[
  {"text": "a small calico kitten sitting on a windowsill"},
  {"text": "a watercolor sunset over rolling hills", "count": 2, "aspect_ratio": "16:9"},
  {"text": "an isometric pixel-art bakery", "count": 1, "aspect_ratio": "1:1", "model": "nano2"}
]
```

`test_assets/sample_batch_invalid.tsv` (unit-test only — never live e2e)
```
ok prompt
prompt with bad count	not-a-number
prompt with bad aspect	1	9999:9999
prompt with unknown model	1	16:9	imaginary-model
```

Each row exercises a distinct parse failure → `ConfigurationError`.

## 8. Jitter investigation — the systematic part

**Question:** Is the 3–7s random delay between submissions inside `--same-project` necessary to avoid Flow rate-limits / anti-bot throttling, or is it cargo-cult?

### Matrix (revised per methodology review)

The matrix isolates jitter from same-project rapid-fire by adding a third cell:

| Cell | `same_project` | `jitter_range` | What it tests |
|---|---|---|---|
| R1 | 1 | `(0.0, 0.0)` | Same-project, no sleep → if pass, jitter is unnecessary in same-project mode. |
| R2 | 1 | default 3–7s | Current behaviour baseline. |
| R3 | 0 | `(0.0, 0.0)` | Different-project, no sleep → control. Isolates "rapid-fire across projects" from jitter. |

### Replication

- **N=3 per cell** in **two distinct sessions** (cold morning + warm afternoon), with cross-session gap ≥ 2 hours. Conservative-but-feasible compromise between R3's request for N=5 and the credit budget. The matrix uses `test_assets/sample_batch.tsv` (3 rows producing 4 images total: 1 + 2 + 1). Total: 3 cells × 3 reps × 2 sessions × 4 images ≈ **72 image generations** (worst case).
- Each session run captures the **session fingerprint** in the evidence file: profile name, Chromium build, Playwright version, UTC hour, count of `ui_automation.*` events observed in the prior 60 minutes (account-warmth proxy).

### Decision rule (operationally pinned)

**A cell run "passes" iff ALL of the following hold:**
- CLI exit code 0.
- `ui_automation.batch_response_seen` count == manifest row count.
- `ui_automation.batch_response_dropped_project_id_mismatch` count == 0.
- `ui_automation.overlay_dismiss_failed` count == 0.
- No row times out (each row produces an image within the configured row timeout).

**Otherwise it "fails."**

If a failure is suspected to be the open listener-miss flake (memory `phase-b-followups`), classify the cell as **inconclusive**, not failed.

### Verdict

| Outcome | Action |
|---|---|
| R1 passes 3/3 in both sessions **AND** R3 passes 3/3 in both sessions | **Drop jitter** (commit 5b = `refactor(image): drop unconditional jitter`). |
| R1 fails any time with non-listener-miss failure | **Keep jitter** (commit 5b = `docs(image): document anti-detection jitter rationale`). Cite the failing run in the docstring. |
| R1 mixed (some pass, some inconclusive/fail) | **Default conservative: keep jitter.** File a follow-up issue to make it configurable. |
| Matrix incomplete (< 2 sessions) | **Default conservative: keep jitter.** |

The conservative default (keep) honours R3's "honest uncertainty" requirement.

### Mid-matrix abort

If R1 first session fails non-listener-miss, the matrix may abort. The conclusion is "keep jitter"; remaining cells are not run. The evidence file records the abort reason.

### Regression detection on the drop path

Even if we drop the jitter, the application-layer events from §3/§5 fire unconditionally:
- `image_batch.submission_attempt.t_since_prev_submit_ms`
- `image_batch.submission_result.outcome`
- `image_batch.inter_submission_latency_ms`

A future user reporting throttling has structured-log breadcrumbs to grep without us re-instrumenting. This is the explicit reversibility hook R3 demanded.

## 9. Docs updates

### `docs/USAGE.md`

Add a "Batch image generation" subsection under the existing `gflow image` content with:
- Both manifest formats with annotated examples.
- The `--same-project` flag, what it does, and the live-verified jitter behaviour decided in §8.
- A pointer to `test_assets/sample_batch.{tsv,json}` for copy-paste.
- The `MAX_BATCH_PROMPTS = 5` cap and how to change it (source constant; no env-var override in this PR).
- Exit codes: 0 success; 1 user error (bad manifest); other non-zero on transport failures.
- A pointer to the new structlog events for users who want to instrument throttling debugging.

### `CHANGELOG.md`

Under `[Unreleased]`:

- `### Fixed` — `gflow image t2i -n N` now makes a single transport call using Flow's native `xN` count selector (was fanning out N parallel single-image submissions). Closes #14 part 1.
- `### Added` — `gflow image batch <manifest>` subcommand with JSON and TSV manifests, `MAX_BATCH_PROMPTS=5`, `--same-project` flag, and live-verified jitter behaviour. Closes #14 part 2.
- `### Added` — application-layer structlog events for image batch submission, useful for throttling-regression debugging (`image_batch.submission_attempt`, `image_batch.submission_result`, `image_batch.row_completed`, `image_batch.inter_submission_latency_ms`).
- `### Removed` — `--seed` flag from `gflow image t2i` and `gflow image i2i`. The flag was a no-op under the active UI transport since v0.7.0 (silently discarded inside the client before reaching the transport). The `seed` field on the wire body is still populated by the experimental HTTP transports' internal mint; no functional change for any user who was actually getting images. If reproducibility via user-controlled seed becomes possible (Flow UI change or HTTP transport revival), it will be re-introduced at that layer.
- `### Removed` — public-API parameters `seed`/`batch_id` from `FlowApiClient.generate_image` and `seeds` from `FlowApiClient.generate_images_batch`. **Library callers passing these as kwargs will get a `TypeError`.** Justification: they were never propagated to the active UI transport. The body-builder retains `seed`/`batch_id` for the experimental HTTP transports' internal use.

### `docs/INDEX.md`

Add a row for `LIVE_VERIFICATION_image_batch.md`.

### `KNOWN_ISSUES.md`

Touched **only** if §8 reveals a real Flow throttle that justifies a `Known Issues` entry.

## 10. Acceptance criteria

The PR is mergeable when **all** of these are true:

1. `PYTHONUTF8=1 uv run python scripts/ci/check_repo_hygiene.py` — pass.
2. `uv run ruff check src tests` — clean.
3. `uv run ruff format --check src tests` — clean.
4. `uv run pyright src` — 0 errors.
5. Scoped pytest on changed dirs passes locally (`tests/api/test_client_image*.py`, `tests/image_batch/`, anything image-batch). **Avoid the unscoped full suite locally** per `full-test-suite-ooms` memory; trust CI for the full sweep.
6. New live e2e passes the matrix cell selected by §8's verdict:
   - If verdict is "drop jitter": `GFLOW_CLI_E2E_BATCH_SAME_PROJECT=1 GFLOW_CLI_E2E_BATCH_JITTER=0` must pass on `ui_automation`.
   - If verdict is "keep jitter": default e2e (`SAME_PROJECT=0`) must pass on `ui_automation`.
7. `docs/LIVE_VERIFICATION_image_batch.md` exists, is non-empty, has a UTC timestamp within 24 h of merge, and is referenced from `docs/INDEX.md`.
8. CHANGELOG `[Unreleased]` has both `### Fixed` and `### Added` entries.
9. `docs/USAGE.md` documents `image batch`.
10. PR commits are all human-authored (`git log feature/multi-image-prompt --format='%an %ae'` shows only Flavio Oliva).
11. PR body contains no AI-generated footer and begins with `Supersedes #35.`.
12. PR #35 is closed with a comment pointing at the new PR. `origin/claude/plan-next-issue-Stegy` is deleted.
13. **Stale-test grep clean:** `rg -n 'not yet available|temporarily unavailable|5-prompt cap' tests/` returns no hits (per `stale-test-discovery` memory).
14. `pre-commit run --all-files` clean (no `--no-verify`).
15. New observability events (`image_batch.submission_attempt`, `..._result`, `..._row_completed`, `..._inter_submission_latency_ms`) have at least one passing unit test each.
16. **Seed cleanup is complete with no leftovers:**
    - `rg -n '--seed|seed=' src/gflow_cli/cli_image.py` returns zero hits.
    - `rg -n 'seed\\s*[:=]' src/gflow_cli/api/client.py` returns zero hits (only the body-builder helper at `api/image.py` retains seed parameters).
    - `rg -n 'seed=' tests/` returns zero hits in production-path tests (only in body-builder unit tests under `tests/api/test_image_body.py` or equivalent, if any).
    - `gflow image t2i --help` output contains no mention of `--seed`.

## 11. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Live e2e exhausts Flow credits during the jitter matrix | Medium | Worst case ~72 generations across two sessions (see §8 budget). Mid-matrix abort on first non-listener-miss failure caps the bleeding. One matrix sweep total; no automatic retry. |
| Tree-replay split (§6.A) accidentally drops a hunk | Low | After staging, diff `feature/multi-image-prompt..origin/claude/plan-next-issue-Stegy -- src/gflow_cli/image_batch.py src/gflow_cli/cli_image.py src/gflow_cli/api/client.py tests/image_batch/ tests/api/test_client_image.py` must be empty (filter scope to image-batch paths only — the v0.7.0 release-only hunks naturally diff and are correctly stripped). |
| Listener-miss flake masquerades as throttling in §8 | Medium | §8 decision rule explicitly classifies listener-miss failures as inconclusive, not failed. Evidence file records which classification was used and why. |
| Same-session R1/R2 co-location is itself a confounder | Low | Mitigated by the two-session replication and the session-fingerprint capture. The matrix is not perfect but it is auditable. |
| Splitting commits regresses test coverage on commit #1 | Medium | Run scoped pytest after each commit, not just at the end. CI catches it too. |
| New PR misses the v0.7.x release window | Low | Targets `develop`. No release coupling in this PR. No signed-tag work triggered (CI gate #30 untouched). |
| Partial e2e leaves images in `tmp/` after failure | Low | `tmp/` is gitignored. Mitigation: on failure, the e2e fixture captures the last observability event into the evidence file before propagating the exception. No auto-retry. |
| Cosmetic rename deferred → footgun lingers | Low | Filed as a follow-up issue; the one-letter difference is acceptable for the duration of this PR. |
| `--seed` removal breaks a downstream script using the (broken) flag | Low | The flag was a no-op since v0.7.0; any script "using" it was already not getting seed-controlled images. Mitigation: CHANGELOG `### Removed` entry with the verified-no-op explanation; the next release notes call it out explicitly. |
| Library caller passes `seed=` / `batch_id=` to `FlowApiClient.generate_image` after the cleanup | Low | `TypeError` at call site — loud failure, not silent. The same justification applies: it was never functional. CHANGELOG `### Removed` lists the public-API removals so downstream library users have a clear migration note. |
| `tests/cli/` tests for `--seed` validation rules silently kept and broken | Medium | Part of #1b: grep `tests/cli/` for `--seed` and remove. CI's `pyright` and `ruff` will fail loudly if anything is missed. |

## 12. Decision log

- **D1.** Use Strategy A (close + reopen). Rationale: only path that satisfies branch-naming convention without raw API gymnastics.
- **D2.** Atomic commit split into 7 commits (1, 1b, 2, 3, 4, 5a, 5b). Rationale: CLAUDE.md "small, atomic commits"; lets the bugfix (#1) be cherry-picked into a release branch independently of the seed cleanup (#1b) and the feature (#2+); separates evidence capture (#5a) from code change (#5b) for revert isolation.
- **D3.** Jitter decision deferred to evidence. Rationale: project mandate to verify systematically, not guess.
- **D4.** Land application-layer observability for image batch in commit #2. Rationale: post-drop regression detection (R3 council concern); the events are useful regardless of the verdict.
- **D5.** Sample manifests committed as fixtures (not generated in-test). Rationale: doubles as user-facing copy-paste examples; the live e2e becomes hermetic. Hygiene gate allows non-`smoke_`/`debug_`-prefixed `test_assets/`.
- **D6.** Use Mechanism 6.A (tree-replay + staged commits), not interactive rebase. Rationale: CLAUDE.md forbids `git rebase -i` in the agent loop. 6.B is documented only as a human-pairing fallback.
- **D7.** Mandate branch deletion (local + remote) for `claude/plan-next-issue-Stegy` after PR close. Rationale: no `claude/*` ref may linger; supports the branch-naming convention.
- **D8.** **Remove** `seed`/`batch_id` from the public API (`generate_image`), the private path (`_drive_image_generation`), the batch method (`generate_images_batch.seeds`), the CLI (`--seed` on `gflow image t2i` and `i2i`), and all 9 test call sites. Rationale: code-verified empirically dead under the active UI transport (`v0.7.0`): user-passed `seed=42` never reaches any transport — `_drive_image_generation` discards it via `_ = seed, batch_id` (`client.py` shim). The body builder retains `seed`/`batch_id` as wire-format parameters used internally by the experimental HTTP transports (which mint locally). User authorised Option A (full removal) over Option B (DTO-thread plumbing for transports nobody uses). Per CLAUDE.md "delete completely, no backwards-compat hacks." CHANGELOG `### Removed` documents the breaking change; commit #1b's title prefix is `refactor` plus a BREAKING CHANGE footer.
- **D9.** **Defer** the `_drive_image_generation` ↔ `_drive_images_generation` rename. Rationale: real readability win but atomicity-breaking when mixed with the bugfix. Filed as a follow-up issue.
- **D10.** Use `tmp_path` pytest fixture for the e2e output dir, not a hand-rolled timestamped path. Rationale: avoids Windows path issues with `:` in ISO-8601 timestamps; pytest cleans up automatically.
- **D11.** Use dependency injection (`jitter_range=(0.0, 0.0)`) for the e2e jitter override, not monkeypatch and not env-var conditional in production code. Rationale: the parameter already exists in PR #35 (`src/gflow_cli/image_batch.py:597`) — using it keeps the test boundary clean.
- **D12.** Canonical env-var prefix `GFLOW_CLI_E2E_*` (aligning with `test_video_t2v_e2e.py`). Rationale: project consistency; the v1 spec invented `GFLOW_E2E_*` which would have created two parallel conventions.
- **D13.** Conservative default on inconclusive jitter evidence: **keep** jitter. Rationale: anti-bot signals are opaque; safety first. Aligned with R3 council finding.
- **D14.** Matrix replication N=3 per cell across 2 sessions (R3 wanted N=5; budget allows N=3). Rationale: balances statistical defensibility against credit cost. The verdict table biases conservative on mixed results to compensate for small N.

## 13. Memory entries to update after merge

(Not edits to memory now — these are post-merge follow-ups for the human user.)

- `stale-test-discovery.md` — append the `gflow image batch` restoration as a concrete example (grep targets: `not yet available`, `temporarily unavailable`, `5-prompt`).
- `branch-naming-convention.md` — append: "PR #35 (closed 2026-05-21) is the canonical example of why `claude/*` is rejected; superseded by `feature/multi-image-prompt`."
- (no new memory needed for jitter — `LIVE_VERIFICATION_image_batch.md` serves that role)

## 14. Open questions

None. All design decisions above are sufficient to produce an implementation plan via the `writing-plans` skill.
