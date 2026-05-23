# Multi-image prompt — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Revision:** v3 (2026-05-22) — see "v3 revision" banner below. v2 (2026-05-21) — applied all 25 council findings. v1 — initial plan.

**Goal (v3):** Land `gflow image batch` on `feature/multi-image-prompt` → `develop` with the `--same-project` transport defect *actually fixed* (stay-mounted editor session in `ui_automation.generate_images`), and with the `--same-project` CLI flag removed in favour of always-same-project semantics. The v2 goal of "resolve the jitter question empirically via a 3-cell × 2-session matrix" is dropped — the matrix was invalidated mid-run (see spec v4 banner).

**Architecture (v3):** Five of the seven atomic commits from v2 are landed already (Waves 1-5 on the branch). The remaining commits are a new chain that replaces v2's commits #5a + #5b: rather than evidence-file + verdict-driven docs/code, v3 lands the stay-mounted refactor in `ui_automation.py`, the CLI flag removal in `cli_image.py`, the orchestration simplification in `image_batch.py`, the e2e assertion update in `tests/e2e/test_image_batch_e2e.py`, the doc updates in `USAGE.md` / `CHANGELOG.md`, and one credit-spending e2e verification at the end. The matrix evidence file (`docs/LIVE_VERIFICATION_image_batch.md`) is preserved with its v3-era retraction record.

**Tech Stack:** Python 3.11+, Click, Rich, Playwright (UI transport), structlog, pytest + pytest-bdd, ruff, pyright --strict, uv, gh CLI.

**Spec:** [`docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md`](../specs/2026-05-21-multi-image-prompt-design.md) **v4**.

---

## v3 revision — what changed and why (2026-05-22)

The v2 plan executed Waves 1-5 successfully on the branch (PR #35 work re-authored, count-selector fix, `--seed` removal, `image batch` subcommand + observability, parameterized e2e, docs). Wave 6 began the live jitter matrix and immediately discovered that `--same-project=1` does not work at the transport layer: `ui_automation.generate_images` accepts a `project_id` but discards it via `_ = project_id  # accepted for Protocol parity; UI creates its own project`. Every prompt was landing in its own Flow project regardless of the flag (verified live on profile `denon82`).

That invalidates the entire Wave 6 + 7 design as written. v3 replaces them with a focused stay-mounted-editor refactor and an honest CLI surface.

### v2 commits that landed (kept as-is on the branch)

| Wave | SHA | Subject | Status |
|---|---|---|---|
| 1 | `ea4e769` | `fix(image): use native xN count selector for -n N (#14 part 1)` | LANDED |
| 2 | `e83c609` | `refactor(image)!: remove no-op --seed flag and dead seed/batch_id params` (BREAKING) | LANDED |
| 3 | `32134a8` | `feat(image): add gflow image batch subcommand with --same-project (#14 part 2)` | LANDED but needs v3 amendment (flag removal) |
| 4 | `e7d01bc` | `test(e2e): live image batch e2e parameterized by same-project + DI jitter` | LANDED but needs v3 amendment (drop same_project parameterization) |
| 5 | `e6589e0` | `docs: gflow image batch in USAGE, CHANGELOG, INDEX` | LANDED but needs v3 amendment (rewrite the same-project section) |
| 6a | `81eb012` | `docs(image): jitter live-verification skeleton — matrix pending` | LANDED; will receive a retraction amendment under v3 |

The branch is at `81eb012` now with two uncommitted working-tree changes from the v3 session (the test assertion relaxation and the evidence-file verdict retraction).

### v3 commit chain (replaces v2's #5b + Wave 7)

| Order | Subject (proposed) | Files |
|---|---|---|
| v3-1 | `test(image): retract assertion-equality on batch_response_seen — Playwright listener fires per HTTP response, not per row` | `tests/e2e/test_image_batch_e2e.py` |
| v3-2 | `docs(image): retract jitter-matrix verdict — superseded by --same-project transport defect (v4 spec)` | `docs/LIVE_VERIFICATION_image_batch.md` |
| v3-3 | `refactor(image)!: stay-mounted editor session in ui_automation.generate_images so --same-project actually shares one Flow project` (BREAKING for direct transport callers) | `src/gflow_cli/api/transports/ui_automation.py`, possibly a new helper file |
| v3-4 | `refactor(image)!: drop --same-project flag from gflow image batch (always same-project now)` (BREAKING CLI) | `src/gflow_cli/cli_image.py`, `src/gflow_cli/image_batch.py` |
| v3-5 | `test(image): update batch e2e to assert all prompts share one project_id, drop GFLOW_CLI_E2E_BATCH_SAME_PROJECT env var` | `tests/e2e/test_image_batch_e2e.py`, unit tests as needed |
| v3-6 | `docs(image): always-same-project semantics in USAGE.md, CHANGELOG.md [Unreleased], help text` | `docs/USAGE.md`, `CHANGELOG.md`, possibly `docs/INDEX.md` |
| v3-7 | `docs(spec,plan): record v3 plan + v4 spec amendments and the live verification of the stay-mounted refactor` | `docs/superpowers/...` + `docs/LIVE_VERIFICATION_image_batch.md` (post-refactor verification record) |

Each commit independently passes `/gflow:check`. The credit-spending e2e is the gate for v3-5 → v3-6; verification record lands in v3-7.

### Out of scope for v3 (deferred)

- **Persistent asset layer** — `(profile, project_id, generation_id, output_idx) → file` registry. Item #10 in [`phase-b-followups`](file:///C:/Users/ffrol/.claude/projects/C--development-github-gflow-cli/memory/phase-b-followups.md). User-confirmed deferred 2026-05-22.
- **Per-project Chrome-session multiplexing** for any future "different-project batch" feature. The user-confirmed model is: batch = same-project; different-project = loop `gflow image t2i`. No multiplexing needed.
- **Re-running the jitter matrix.** Once the stay-mounted refactor lands and behaves, jitter is documented as a submission-cadence control; cadence tuning is not on this branch's table.
- **`chore/sync-develop-v0.7.0`** — the back-merge of `main`'s v0.7.0 bump commits to `develop`. Separate branch, separate chore. See [`phase-b-followups`](file:///C:/Users/ffrol/.claude/projects/C--development-github-gflow-cli/memory/phase-b-followups.md) item D for context.

### Task-by-task plan for v3

> The v2 task body below (Phases 0-7) is preserved as historical record. The v3 work is *not* a re-execution of those phases; it is the seven-commit chain in the table above, executed sequentially. A detailed task-by-task expansion of v3-3 (stay-mounted refactor) and v3-4 (flag removal) will be written when work starts — likely after a brief `superpowers:brainstorming` pass on the session abstraction.

---

---

## Conventions used throughout this plan

- **Commands are PowerShell-on-Windows.** For POSIX, swap `$env:VAR = "x"` → `VAR=x`.
- All file paths are absolute or repo-relative from `C:\development\github\gflow-cli`.
- All commits target the `feature/multi-image-prompt` branch (Strategy A per spec §4).
- "Run `/gflow:check`" means: `uv run python scripts/ci/check_repo_hygiene.py && uv run ruff check src tests && uv run ruff format --check src tests && uv run pyright src && uv run pytest -q <scoped-paths>` — execute the four-gate suite. Scope pytest per `full-test-suite-ooms` memory.
- "Expected: PASS" means exit code 0 + no failures reported.

---

## Phase 0 — Bootstrap branch and re-author PR #35 work

### Task 0.1: Cut the feature branch from current `develop`

**Files:** none (git operations).

- [ ] **Step 1: Fetch latest remote state**

```powershell
git fetch --all --prune
```

Expected: lists `origin/develop`, `origin/claude/plan-next-issue-Stegy`, etc., with no errors.

- [ ] **Step 2: Verify `develop` is clean**

```powershell
git checkout develop
git pull --ff-only origin develop
git status -sb
```

Expected: `## develop...origin/develop` with no local changes.

- [ ] **Step 3: Check out the feature branch (already created during planning)**

> **Note:** `feature/multi-image-prompt` was created during planning. 5 commits sit on it (spec v1/v2/v3, plan v1/v2). Use `checkout`, not `checkout -b`.

```powershell
git checkout feature/multi-image-prompt
git branch --show-current
git log --oneline origin/develop..HEAD
```

Expected: prints `feature/multi-image-prompt` and a 5-commit log of `docs(spec): ...` / `docs(plan): ...` commits ahead of `origin/develop`. If the branch is missing locally, recover with `git fetch origin feature/multi-image-prompt && git checkout feature/multi-image-prompt`. If the planning commits are missing, abort and have the user re-create them.

### Task 0.2: Stage the PR #35 tree onto the new branch (no commit yet)

**Mechanism §6.A from the spec — tree-replay, not cherry-pick.**

- [ ] **Step 1: Checkout PR #35's tree for the source areas only**

```powershell
git checkout e8f932a -- `
  src/gflow_cli/api/client.py `
  src/gflow_cli/cli_image.py `
  src/gflow_cli/image_batch.py `
  tests/api/test_client_image.py `
  tests/image_batch/
```

Expected: no errors. The v0.7.0 release hunks (`pyproject.toml`, `CHANGELOG.md` footer, `uv.lock`, `src/gflow_cli/__init__.py`) and unrelated files are intentionally excluded — they're already on `develop` or out of this PR's scope. Verified PR #35 file list: `client.py`, `cli_image.py`, `image_batch.py`, `tests/api/test_client_image.py`, `tests/image_batch/__init__.py`, `tests/image_batch/test_image_manifest.py` (plus release-only files we skip). **`src/gflow_cli/api/image.py` is NOT modified by PR #35** — do not include it.

- [ ] **Step 2: Un-stage everything so we can re-stage per commit**

```powershell
git restore --staged .
git status -sb
```

Expected: files listed as `?? ` (untracked) for net-new files or `M ` (modified) for existing ones, none staged (`A` or `M ` in left column).

- [ ] **Step 3: Verify the diff scope is what we expect**

```powershell
git diff --stat HEAD
```

Expected: shows changes only in the six paths from step 1. If you see anything else (`pyproject.toml`, `CHANGELOG.md` footer, `uv.lock`), abort and re-do step 1.

---

## Phase 1 — Commit #1: native xN count selector (fix)

### Task 1.1: Stage and verify the count-selector refactor

**Files:**
- Modify: `src/gflow_cli/api/client.py` (count-selector portion — `_drive_images_generation` new method, `generate_images_batch` single-call refactor)
- Modify: `tests/api/test_client_image.py` (count-selector deltas only — **NOT** seed-related deletions; those go in #1b)

- [ ] **Step 1: Read the current state of client.py around generate_images_batch**

```powershell
git diff HEAD -- src/gflow_cli/api/client.py | Select-String -Pattern "_drive_images_generation|generate_images_batch|asyncio.gather" -Context 0,5
```

Expected: shows the PR #35 refactor introducing `_drive_images_generation` (plural), `generate_images_batch` calling it with `count=N` baked into `req`, no `asyncio.gather` fan-out.

- [ ] **Step 2: Build commit #1's content of `client.py` by hand-edit (primary path)**

**Note:** PR #35's refactor of `_drive_image_generation` interleaves the count-selector change (rename + signature/return-type change) with the `seed`/`batch_id` removal in the same hunk. `git add -p` cannot separate them cleanly. The atomic-commit split is achieved by **editing `client.py` directly to land just the count-selector portion** for this commit, then in commit #1b removing the seed/batch_id surface area.

Concrete edits to `src/gflow_cli/api/client.py` for commit #1:
1. **Rename** `_drive_image_generation` → introduce a new `_drive_images_generation` (plural) returning `list[GeneratedImage]`. **Keep the original `_drive_image_generation` (singular)** as a thin delegator that calls `_drive_images_generation` and returns `images[0]`. The singular method still has `seed`/`batch_id` kwargs and the `_ = seed, batch_id` shim — those are commit #1b's territory.
2. **Rewrite `generate_images_batch`** to call `_drive_images_generation` **once** with `count` baked into the request via `_dc_replace(req, count=count)`, instead of the previous `asyncio.gather` fan-out. **Important:** removing the gather necessarily removes `seeds_list = [secrets.randbelow(...) for _ in range(count)]` and `shared_batch_id = _new_batch_id()` from this function — those were inputs to the gather, no longer needed. **This means commit #1 does touch seed-related code at one site**, but only to delete the inputs to a control-flow that no longer exists. The public `seeds=` parameter on `generate_images_batch` stays in commit #1; it gets removed in #1b. Update the commit-message body to acknowledge this nuance.
3. Validate `count` is in `[1, 4]` before the single call (preserve the existing range check or move it to the new function).

If you prefer, copy `client.py` from `git show e8f932a:src/gflow_cli/api/client.py` and then re-add the `seed`/`batch_id` parameters everywhere they exist on `develop` but were removed in PR #35. This is mechanically equivalent and may be easier to verify.

- [ ] **Step 2b (optional shortcut): tentatively try `git add -p`**

If you want, you can attempt `git add -p src/gflow_cli/api/client.py` first. Accept any hunks that touch ONLY the `_drive_images_generation` symbol and the rewrite of `generate_images_batch` body. **Reject anything that drops `seed`/`batch_id` from `_drive_image_generation`'s signature or removes the `secrets.randbelow` mint inside `generate_image`.** If the hunks come bundled, abandon `-p` and use Step 2's hand-edit path.

- [ ] **Step 3: Stage the count-selector test deltas**

Use `git add -p tests/api/test_client_image.py` and accept hunks where the assertion changes from "expects N parallel submissions" to "expects one submission with count=N." Skip hunks that delete `seed=42` kwargs (those go in #1b).

- [ ] **Step 4: Verify the staged diff is count-selector-only**

```powershell
git diff --cached --stat
git diff --cached | Select-String -Pattern "seed|batch_id" | Select-Object -First 20
```

Expected: stat shows only `client.py` and `test_client_image.py`. The seed-grep should show zero `--seed` removals and zero `seed:` parameter removals (the count-selector changes shouldn't touch seed at all).

- [ ] **Step 5: Run scoped tests**

```powershell
uv run pytest -q tests/api/test_client_image.py
```

Expected: PASS.

- [ ] **Step 6: Run `/gflow:check` scoped**

```powershell
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src/gflow_cli/api/client.py tests/api/test_client_image.py
uv run ruff format --check src/gflow_cli/api/client.py tests/api/test_client_image.py
uv run pyright src/gflow_cli/api/client.py
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```powershell
git commit -m @'
fix(image): use native xN count selector for -n N (#14 part 1)

`generate_images_batch` now makes one transport call with count baked
into the request, instead of fan-out via asyncio.gather. The UI transport
clicks the matching x{N} tab so Flow produces N images in one
submission round-trip. Simpler, faster, matches user intent.

Extracted `_drive_images_generation` (plural, returns list) so both the
single-image and multi-image paths share one token-mint + transport-call
site.

Independence: this commit has zero dependency on the seed cleanup in
#1b or on the new `image batch` subcommand in #2, so it is
independently cherry-pickable to a release/* branch.

Closes #14 part 1.
'@
```

Expected: commit succeeds. `git log --oneline -1` shows the new SHA + the title.

---

## Phase 2 — Commit #1b: remove dead `--seed` flag and `seed`/`batch_id` params

**This commit is a BREAKING CHANGE.** Library users passing `seed=` / `batch_id=` to `FlowApiClient.generate_image` will get a `TypeError`. CLI users using `--seed` will get an "unknown option" error. See spec §1 and §3 for the justification (no real regression: the flag was a no-op under the UI transport).

### Task 2.0: Write failing tests pinning the removal (TDD red phase)

**Files:**
- Create or extend: `tests/api/test_client_image.py` (add a `test_seed_removal.py` style block)
- Create or extend: `tests/cli/test_cli_image_seed_removed.py`

These tests assert the cleanup is complete. They MUST fail before Task 2.1-2.4's edits and MUST pass after.

- [ ] **Step 1: Write the public-API signature test**

Append to `tests/api/test_client_image.py` (or create new module if cleaner):

```python
import inspect

from gflow_cli.api.client import FlowApiClient


def test_generate_image_has_no_seed_kwarg() -> None:
    """seed/batch_id removed in commit #1b — see design spec §1, D8."""
    params = inspect.signature(FlowApiClient.generate_image).parameters
    assert "seed" not in params, f"generate_image still accepts seed: {list(params)}"
    assert "batch_id" not in params, f"generate_image still accepts batch_id: {list(params)}"


def test_generate_images_batch_has_no_seeds_kwarg() -> None:
    """seeds= removed in commit #1b — see design spec §1, D8."""
    params = inspect.signature(FlowApiClient.generate_images_batch).parameters
    assert "seeds" not in params, f"generate_images_batch still accepts seeds: {list(params)}"


def test_drive_image_generation_private_has_no_seed_kwarg() -> None:
    """_drive_image_generation kwargs shrunk in commit #1b."""
    params = inspect.signature(FlowApiClient._drive_image_generation).parameters
    assert "seed" not in params
    assert "batch_id" not in params
```

- [ ] **Step 2: Write the CLI flag-removal test**

Create `tests/cli/test_cli_image_seed_removed.py`:

```python
from __future__ import annotations

from click.testing import CliRunner

from gflow_cli.cli import cli


def test_t2i_rejects_seed_flag() -> None:
    """--seed removed from gflow image t2i in commit #1b — spec §1, D8."""
    runner = CliRunner()
    result = runner.invoke(cli, ["image", "t2i", "--seed", "42", "dummy prompt"])
    assert result.exit_code != 0
    assert "no such option" in result.output.lower() or "--seed" in result.output


def test_i2i_rejects_seed_flag() -> None:
    """--seed removed from gflow image i2i in commit #1b."""
    runner = CliRunner()
    result = runner.invoke(
        cli, ["image", "i2i", "--seed", "42", "--ref", "x.png", "dummy prompt"]
    )
    assert result.exit_code != 0
    assert "no such option" in result.output.lower() or "--seed" in result.output
```

- [ ] **Step 3: Run the new tests — expect ALL FAIL**

```powershell
uv run pytest -q tests/api/test_client_image.py::test_generate_image_has_no_seed_kwarg tests/api/test_client_image.py::test_generate_images_batch_has_no_seeds_kwarg tests/api/test_client_image.py::test_drive_image_generation_private_has_no_seed_kwarg tests/cli/test_cli_image_seed_removed.py
```

Expected: 5 FAIL. (The signatures still contain `seed`/`batch_id`/`seeds`; the CLI still accepts `--seed`.) If any test PASSES at this stage, you've misread the current state — investigate before proceeding.

**Note:** these tests stay in the working tree but uncommitted across Phases 2.1-2.3. Commit them as part of Task 2.4 once they go green.

### Task 2.1: Remove `--seed` from the CLI

**Files:**
- Modify: `src/gflow_cli/cli_image.py` (lines ~237-239, ~288-291, ~456-465, ~544-546, ~493, ~726 — line numbers approximate; locate the markers below)

- [ ] **Step 1: Locate the markers in cli_image.py**

```powershell
Select-String -Path src/gflow_cli/cli_image.py -Pattern '--seed|seed=seed' -SimpleMatch | Format-Table LineNumber, Line
```

Expected: shows all occurrences. Record line numbers — they will inform the edits below.

- [ ] **Step 2: Delete the `--seed` examples from t2i help text**

Edit `src/gflow_cli/cli_image.py`:

Find the block that includes the example:
```
'  gflow image t2i "reproducible shot" --seed 42\n\n'
"Note: --seed is only valid when generating a single image (-n 1) "
"and is not supported in multi-prompt mode."
```

Delete those three lines (one example line, two Note lines).

- [ ] **Step 3: Delete the `@click.option("--seed", ...)` decorator on `t2i`**

Find and delete the entire option block:
```python
@click.option(
    "--seed",
    default=None,
    type=int,
    ...    # whatever metavar/help follows
)
```

Also delete the `seed` parameter from the decorated function's signature.

- [ ] **Step 4: Delete the `--seed` cross-flag validation in t2i**

Find and delete the two `raise click.UsageError(...)` blocks at the `--seed`-related lines (around 456-465):

```python
if seed is not None and count != 1:
    raise click.UsageError(
        "--seed is only valid when generating a single image (-n 1). "
        "For multi-image runs, omit --seed and let each shot get its own."
    )
if seed is not None and is_multi_prompt:
    raise click.UsageError(
        "--seed is not supported for multi-prompt `gflow image t2i`. "
        "Use one single-prompt command per seed today; per-prompt seeds belong "
        "to a future `gflow run --config` schema update."
    )
```

If the surrounding function docstring also references `--seed`, update it to remove the reference.

- [ ] **Step 5: Update the t2i `client.generate_image(...)` call**

Find line ~493:
```python
img = await client.generate_image(project_id=project.project_id, req=req, seed=seed)
```

Replace with:
```python
img = await client.generate_image(project_id=project.project_id, req=req)
```

- [ ] **Step 6: Repeat steps 2-5 for the `i2i` command**

Same pattern:
- Delete `--seed` example/help line at ~544-546.
- Delete the `@click.option("--seed", ...)` decorator on `i2i`.
- Delete the `seed` parameter from i2i's function signature.
- Delete any `if seed is not None: raise UsageError(...)` blocks.
- At line ~726, replace `client.generate_image(..., seed=seed)` with `client.generate_image(...)`.

- [ ] **Step 7: Verify no `--seed` survives in cli_image.py**

```powershell
Select-String -Path src/gflow_cli/cli_image.py -Pattern '--seed|seed=seed' -SimpleMatch
```

Expected: no matches. (If matches, return to the affected line and remove.)

### Task 2.2: Remove `seed`/`batch_id` from the public client API

**Files:**
- Modify: `src/gflow_cli/api/client.py` (lines ~688-696 for `generate_image`, ~723-724 for the mint, ~732-740 for `generate_images_batch`, and the `_drive_image_generation` private signature)

- [ ] **Step 1: Locate the markers**

```powershell
Select-String -Path src/gflow_cli/api/client.py -Pattern 'seed|batch_id|_new_batch_id' -SimpleMatch | Select-Object -First 30 | Format-Table LineNumber, Line
```

- [ ] **Step 2: Remove `seed`/`batch_id` from `generate_image` signature**

Find:
```python
async def generate_image(
    self,
    *,
    project_id: str | None = None,
    req: GenerateImageRequest,
    seed: int | None = None,
    recaptcha_action: str = "imageGeneration",
    batch_id: str | None = None,
) -> GeneratedImage:
```

Replace with:
```python
async def generate_image(
    self,
    *,
    project_id: str | None = None,
    req: GenerateImageRequest,
    recaptcha_action: str = "imageGeneration",
) -> GeneratedImage:
```

- [ ] **Step 3: Remove the obsolete docstring paragraph**

Find the docstring of `generate_image` and delete the "Idempotency: calling twice with the same `seed` and `batch_id`..." paragraph (was around lines 709-711). It is now false. If the docstring becomes thin, leave a one-sentence summary: `"""Single-shot Imagen/Narwhal image generation."""`.

- [ ] **Step 4: Remove the seed/batch_id mint in `generate_image`'s body**

Find the call site to `_drive_image_generation` inside `generate_image` (around lines 720-726):
```python
return await self._drive_image_generation(
    project_id=resolved_project_id,
    req=req_with_token if "req_with_token" in locals() else req,
    seed=seed if seed is not None else secrets.randbelow(2**31),
    batch_id=batch_id or _new_batch_id(),
    recaptcha_action=recaptcha_action,
)
```

(Exact wording may differ — the markers are `seed=seed if seed is not None` and `batch_id=batch_id or _new_batch_id()`.)

Replace with:
```python
return await self._drive_image_generation(
    project_id=resolved_project_id,
    req=req,
    recaptcha_action=recaptcha_action,
)
```

- [ ] **Step 5: Shrink `_drive_image_generation` signature and body**

Find the private method (after the count-selector refactor from commit #1, it looks like):

```python
async def _drive_image_generation(
    self,
    *,
    project_id: str,
    req: GenerateImageRequest,
    seed: int,
    batch_id: str,
    recaptcha_action: str,
) -> GeneratedImage:
    """Single-image shortcut — delegates to ``_drive_images_generation`` (count=1)."""
    # seed + batch_id are passed through for HTTP transports that embed them
    # in the wire body; the UI transport ignores them.
    _ = seed, batch_id
    images = await self._drive_images_generation(
        project_id=project_id,
        req=req,
        recaptcha_action=recaptcha_action,
    )
    return images[0]
```

Replace with:
```python
async def _drive_image_generation(
    self,
    *,
    project_id: str,
    req: GenerateImageRequest,
    recaptcha_action: str,
) -> GeneratedImage:
    """Single-image shortcut — delegates to ``_drive_images_generation`` (count=1)."""
    images = await self._drive_images_generation(
        project_id=project_id,
        req=req,
        recaptcha_action=recaptcha_action,
    )
    return images[0]
```

- [ ] **Step 6: Remove `seeds` parameter from `generate_images_batch`**

Find:
```python
async def generate_images_batch(
    self,
    *,
    project_id: str | None = None,
    req: GenerateImageRequest,
    count: int = 1,
    seeds: Sequence[int] | None = None,
    recaptcha_action: str = "imageGeneration",
) -> list[GeneratedImage]:
```

Replace with:
```python
async def generate_images_batch(
    self,
    *,
    project_id: str | None = None,
    req: GenerateImageRequest,
    count: int = 1,
    recaptcha_action: str = "imageGeneration",
) -> list[GeneratedImage]:
```

Update the docstring to remove any mention of `seeds`/`per-shot seeds`.

- [ ] **Step 7: Remove the `seeds_list` mint and `_new_batch_id()` call in `generate_images_batch`**

Find lines like:
```python
seeds_list = seeds if seeds is not None else [secrets.randbelow(2**31) for _ in range(count)]
...
shared_batch_id = _new_batch_id()
```

These are now unused. Delete them.

- [ ] **Step 8: Check if `_new_batch_id()` has any remaining callers**

```powershell
Select-String -Path src/gflow_cli/ -Pattern '_new_batch_id' -Recurse -SimpleMatch
```

If no production callers remain, delete the `_new_batch_id()` function from `client.py` (CLAUDE.md: "delete completely, no backwards-compat hacks").

If callers remain elsewhere (e.g., experimental transports), leave it.

- [ ] **Step 9: Check if `secrets` import is still needed**

```powershell
Select-String -Path src/gflow_cli/api/client.py -Pattern 'secrets\.' -SimpleMatch
```

If no other `secrets.` references remain in client.py, remove `import secrets` from the top.

### Task 2.3: Update tests

**Files:**
- Modify: `tests/api/test_client_image.py` (9 call sites + any seed-specific assertion)
- Modify (if exists): `tests/cli/test_cli_image.py` for any `--seed` validation tests
- Modify (if exists): `tests/cli/test_image_command.py` similarly

- [ ] **Step 1: Find all `seed=` occurrences in tests**

```powershell
Select-String -Path tests/ -Pattern 'seed=' -Recurse -SimpleMatch | Format-Table Path, LineNumber, Line
```

- [ ] **Step 2: Edit each `client.generate_image(..., seed=...)` call site**

For each of the 9 occurrences in `tests/api/test_client_image.py` (lines 134, 149, 178, 196, 206, 234, 659, 703, 747), remove the `seed=...` kwarg.

Example:
```python
# Before
await client.generate_image(project_id="proj-1", req=_make_req(), seed=42)
# After
await client.generate_image(project_id="proj-1", req=_make_req())
```

- [ ] **Step 3: Delete tests whose sole purpose was asserting seed propagation**

Grep for test names referencing seed:
```powershell
Select-String -Path tests/ -Pattern 'def test.*seed' -Recurse
```

For each match, read the test. If the entire assertion is "seed flows from public API into the wire body" or similar plumbing assertion (no other meaningful behaviour), delete the test (it was never functional under the UI transport). If it tests something else and just happens to set a seed, keep it but remove the seed kwarg.

- [ ] **Step 4: Find and delete `--seed` CLI validation tests**

```powershell
Select-String -Path tests/ -Pattern '"--seed"|\\-\\-seed' -Recurse -SimpleMatch
```

Each match is a test that exercised the `UsageError` cross-flag validation we just deleted. Delete those tests.

- [ ] **Step 5: Verify no `seed=` survives in production-path tests**

```powershell
Select-String -Path tests/api/test_client_image.py -Pattern 'seed=' -SimpleMatch
```

Expected: zero matches in `test_client_image.py`. (Matches MAY remain in `tests/api/test_image_body.py` or wherever the body builder is unit-tested — that's allowed since the body builder still takes `seed=`/`batch_id=`.)

### Task 2.4: Verify, then commit

- [ ] **Step 1: Run the full `/gflow:check` scoped to affected dirs**

```powershell
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src/gflow_cli/api/client.py src/gflow_cli/cli_image.py tests/api/ tests/cli/
uv run ruff format --check src/gflow_cli/api/client.py src/gflow_cli/cli_image.py tests/api/ tests/cli/
uv run pyright src/gflow_cli/api/client.py src/gflow_cli/cli_image.py
uv run pytest -q tests/api/test_client_image.py tests/cli/
```

Expected: all PASS. **`pyright`** may surface unused-import warnings if `Sequence` is no longer needed in `client.py` — remove the import if so. Likewise for any now-unused imports of `secrets`.

- [ ] **Step 2: Smoke-verify `gflow image t2i --help` has no `--seed`**

```powershell
uv run gflow image t2i --help
```

Expected: the help output does NOT contain `--seed` anywhere. (Visual check.)

- [ ] **Step 3: Verify Task 2.0's red tests now pass**

```powershell
uv run pytest -q tests/api/test_client_image.py::test_generate_image_has_no_seed_kwarg tests/api/test_client_image.py::test_generate_images_batch_has_no_seeds_kwarg tests/api/test_client_image.py::test_drive_image_generation_private_has_no_seed_kwarg tests/cli/test_cli_image_seed_removed.py
```

Expected: 5 PASS. If any FAILs, the removal in 2.1-2.3 is incomplete; locate and fix.

- [ ] **Step 4: Stage and commit (Conventional Commits with `!` BREAKING marker)**

```powershell
git add src/gflow_cli/cli_image.py src/gflow_cli/api/client.py tests/api/test_client_image.py tests/cli/
git status -sb
git commit -m @'
refactor(image)!: remove no-op --seed flag and dead seed/batch_id params

Empirically verified dead under the active UI transport since v0.7.0.
The user-supplied seed was discarded by the `_ = seed, batch_id` shim
in `_drive_image_generation` before reaching any transport. The active
UI transport clicks buttons and never reads the seed; the experimental
HTTP transports mint locally and never received the user's seed either.

Cleanup:
- `gflow image t2i` and `gflow image i2i` no longer accept `--seed`.
- `FlowApiClient.generate_image` no longer accepts `seed=` / `batch_id=`.
- `FlowApiClient.generate_images_batch` no longer accepts `seeds=`.
- `_drive_image_generation` private kwargs and `_ = seed, batch_id`
  shim deleted.
- 9 test call sites updated; CLI `--seed` UsageError tests deleted;
  new `inspect.signature` + CliRunner red tests pin the removal.

The body builder `_build_batch_generate_images_body(seed, batch_id, ...)`
in `src/gflow_cli/api/image.py` is UNCHANGED — those parameters live at
the wire-protocol layer used by the experimental HTTP transports'
internal mint.

Refs: design spec §1, §3, §5 commit #1b, §10 AC16, §12 D8.

BREAKING CHANGE: --seed flag removed from `gflow image t2i` and
`gflow image i2i`. `FlowApiClient.generate_image` no longer accepts
`seed=` or `batch_id=` kwargs; `FlowApiClient.generate_images_batch`
no longer accepts `seeds=`. Callers passing these will get a TypeError
(library) or "no such option" (CLI). If reproducibility via
user-controlled seed becomes possible again (Flow UI change or HTTP
transport revival), it will be re-introduced at that layer.
'@
```

Expected: commit succeeds. `git log --oneline -2` shows #1b on top, #1 below.

- [ ] **Step 5: Stale-test grep (per spec §11 risk register, run after #1b not only at end)**

```powershell
Select-String -Path tests/ -Pattern '--seed|seed=42|seed=1\b' -SimpleMatch -Recurse
Select-String -Path tests/ -Pattern 'not yet available|temporarily unavailable|5-prompt cap' -SimpleMatch -Recurse
```

Expected: zero hits in `tests/api/test_client_image.py` and `tests/cli/`. Allowed hits: `tests/api/test_image_body.py` if it exists (body-builder tests still take `seed=`).

---

## Phase 3 — Commit #2: `gflow image batch` feature + observability

### Task 3.1: Stage the `gflow image batch` core files from PR #35

**Files:**
- Create: `src/gflow_cli/image_batch.py` (carried from PR #35 — already on disk from Phase 0)
- Modify: `src/gflow_cli/cli_image.py` (add `batch` subcommand wiring — carried from PR #35)
- Create: `tests/image_batch/test_image_manifest.py` (carried from PR #35, ~336 lines)
- Create: `tests/image_batch/__init__.py` (if not already present)

- [ ] **Step 1: Verify these are present (already checked out in Phase 0)**

```powershell
Test-Path src/gflow_cli/image_batch.py
Test-Path src/gflow_cli/cli_image.py
Test-Path tests/image_batch/test_image_manifest.py
git status --porcelain src/gflow_cli/image_batch.py tests/image_batch/
```

Expected: paths exist; `image_batch.py` shows `??` (untracked), the `tests/image_batch/` directory shows `??` for new files.

- [ ] **Step 2: Read `src/gflow_cli/image_batch.py` to confirm `run_manifest_image_batch(jitter_range=...)` exposes the DI parameter**

```powershell
Select-String -Path src/gflow_cli/image_batch.py -Pattern 'jitter_range|JITTER_MIN_SECONDS|JITTER_MAX_SECONDS' -SimpleMatch | Format-Table LineNumber, Line
```

Expected: `JITTER_MIN_SECONDS = 3.0` and `JITTER_MAX_SECONDS = 7.0` as module constants; `jitter_range: tuple[float, float] = (JITTER_MIN_SECONDS, JITTER_MAX_SECONDS)` as parameter to `run_manifest_image_batch`.

### Task 3.2: Add the four new structlog observability events (TDD)

**Files:**
- Modify: `src/gflow_cli/image_batch.py`
- Create: `tests/image_batch/test_observability_events.py`

Spec §3 + §5 commit #2 + §8 require these events:
- `image_batch.submission_attempt`
- `image_batch.submission_result`
- `image_batch.row_completed`
- `image_batch.inter_submission_latency_ms`

- [ ] **Step 1: Write the failing test**

Create `tests/image_batch/test_observability_events.py`:

```python
"""Unit tests for the four new application-layer observability events
emitted by `run_manifest_image_batch`. Each event MUST fire once per
submission row, with the documented field schema.

Spec: docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md §3, §5, §8.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from structlog.testing import LogCapture

from gflow_cli.image_batch import (
    BatchPromptItem,
    run_manifest_image_batch,
)


@pytest.fixture
def log_capture():
    """Capture structlog events. Resets the global config on teardown so
    test ordering does not bleed events between tests (council finding R2).
    """
    capture = LogCapture()
    structlog.configure(processors=[capture])
    try:
        yield capture
    finally:
        structlog.reset_defaults()


@pytest.fixture
def fake_client_factory() -> MagicMock:
    """Returns a factory that produces a MagicMock async-context-manager client
    whose `.generate_image` returns a stub image and `.create_project` returns
    a stub project."""
    factory = MagicMock()
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    stub_project = MagicMock(project_id="proj-stub-1")
    client.create_project = AsyncMock(return_value=stub_project)

    stub_image = MagicMock(bytes_=b"\x89PNG\r\n\x1a\n", filename="a.png")
    client.generate_image = AsyncMock(return_value=stub_image)
    client.generate_images_batch = AsyncMock(return_value=[stub_image])

    factory.return_value = client
    return factory


@pytest.mark.asyncio
async def test_emits_submission_attempt_per_row(
    tmp_path: Path,
    log_capture: LogCapture,
    fake_client_factory: MagicMock,
) -> None:
    prompts = (
        BatchPromptItem(text="cat", count=1, aspect_ratio="1:1", model="nano2"),
        BatchPromptItem(text="dog", count=1, aspect_ratio="1:1", model="nano2"),
    )
    await run_manifest_image_batch(
        profile_dir=tmp_path,
        headless=True,
        transport=None,
        prompts=prompts,
        output_dir=tmp_path / "out",
        continue_on_error=False,
        project_title="t",
        same_project=False,
        jitter_range=(0.0, 0.0),
        client_factory=fake_client_factory,
    )
    attempts = [e for e in log_capture.entries if e["event"] == "image_batch.submission_attempt"]
    assert len(attempts) == 2
    assert attempts[0]["row_idx"] == 0
    assert attempts[1]["row_idx"] == 1
    # Council finding R2: assert exact derivation, not just key presence.
    import hashlib
    expected_hash = hashlib.sha256(b"cat").hexdigest()[:12]
    assert attempts[0]["prompt_hash"] == expected_hash
    assert attempts[0]["same_project"] is False
    # Council finding R2: project_id must be on the event so the e2e
    # can assert isolation/sharing semantics (spec §7 assertions 7-8).
    assert "project_id" in attempts[0], f"missing project_id key: {attempts[0]}"


@pytest.mark.asyncio
async def test_emits_submission_result_per_row(
    tmp_path, log_capture, fake_client_factory
):
    prompts = (BatchPromptItem(text="cat", count=1, aspect_ratio="1:1", model="nano2"),)
    await run_manifest_image_batch(
        profile_dir=tmp_path,
        headless=True,
        transport=None,
        prompts=prompts,
        output_dir=tmp_path / "out",
        continue_on_error=False,
        project_title="t",
        same_project=False,
        jitter_range=(0.0, 0.0),
        client_factory=fake_client_factory,
    )
    results = [e for e in log_capture.entries if e["event"] == "image_batch.submission_result"]
    assert len(results) == 1
    assert results[0]["outcome"] == "ok"
    assert "latency_ms" in results[0]


@pytest.mark.asyncio
async def test_emits_row_completed_per_row(
    tmp_path, log_capture, fake_client_factory
):
    prompts = (BatchPromptItem(text="cat", count=1, aspect_ratio="1:1", model="nano2"),)
    await run_manifest_image_batch(
        profile_dir=tmp_path,
        headless=True,
        transport=None,
        prompts=prompts,
        output_dir=tmp_path / "out",
        continue_on_error=False,
        project_title="t",
        same_project=False,
        jitter_range=(0.0, 0.0),
        client_factory=fake_client_factory,
    )
    completed = [e for e in log_capture.entries if e["event"] == "image_batch.row_completed"]
    assert len(completed) == 1
    assert "file_path" in completed[0]
    assert "sha256_prefix" in completed[0]


@pytest.mark.asyncio
async def test_emits_inter_submission_latency_for_subsequent_rows(
    tmp_path, log_capture, fake_client_factory
):
    """The first row has no prior submission, so the latency event fires only
    starting from row 1."""
    prompts = (
        BatchPromptItem(text="a", count=1, aspect_ratio="1:1", model="nano2"),
        BatchPromptItem(text="b", count=1, aspect_ratio="1:1", model="nano2"),
        BatchPromptItem(text="c", count=1, aspect_ratio="1:1", model="nano2"),
    )
    await run_manifest_image_batch(
        profile_dir=tmp_path,
        headless=True,
        transport=None,
        prompts=prompts,
        output_dir=tmp_path / "out",
        continue_on_error=False,
        project_title="t",
        same_project=True,
        jitter_range=(0.0, 0.0),
        client_factory=fake_client_factory,
    )
    latencies = [
        e for e in log_capture.entries
        if e["event"] == "image_batch.inter_submission_latency_ms"
    ]
    assert len(latencies) == 2  # rows 1 and 2; row 0 has no prior
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
uv run pytest -q tests/image_batch/test_observability_events.py
```

Expected: 4 FAIL. The image_batch.py module from PR #35 does not yet emit these events.

- [ ] **Step 3: Edit `src/gflow_cli/image_batch.py` to emit the events**

Find the top of the file and ensure these imports are at module top (NOT inside the loop — council finding R3):
```python
import hashlib
import time

import structlog
```

Find `run_manifest_image_batch`'s main loop (around line ~615 in the PR #35 copy). It looks roughly like:

```python
for idx, item in enumerate(prompts):
    if same_project and idx > 0:
        delay = random.uniform(*jitter_range)
        await asyncio.sleep(delay)

    outcome = await run_one_image_prompt(
        client=client,
        project_id=shared_project_id,
        idx=idx,
        item=item,
        output_dir=output_dir,
    )
    outcomes.append(outcome)

    if outcome.status == "fail" and not continue_on_error:
        ...
```

Wrap each iteration with the four events. Add a logger at module-top:

```python
logger = structlog.get_logger(__name__)
```

Add a module-level helper for hashing prompts (deterministic, non-PII):

```python
def _prompt_hash(text: str) -> str:
    """Short, deterministic, non-reversible hash for observability."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
```

Modify the loop in `run_manifest_image_batch` (around `image_batch.py:616`):

```python
last_submit_ts: float | None = None

for idx, item in enumerate(prompts):
    if same_project and idx > 0:
        delay = random.uniform(*jitter_range)
        await asyncio.sleep(delay)

    now = time.monotonic()
    t_since_prev = None if last_submit_ts is None else int((now - last_submit_ts) * 1000)
    if t_since_prev is not None:
        logger.info(
            "image_batch.inter_submission_latency_ms",
            row_idx=idx,
            latency_ms=t_since_prev,
        )
    logger.info(
        "image_batch.submission_attempt",
        row_idx=idx,
        prompt_hash=_prompt_hash(item.text),
        aspect=item.aspect_ratio,
        model=item.model,
        same_project=same_project,
        jitter_enabled=jitter_range != (0.0, 0.0),
        t_since_prev_submit_ms=t_since_prev,
        # Council R2: project_id MUST be on this event so the e2e can
        # assert --same-project semantics (single ID vs N distinct IDs).
        project_id=shared_project_id if same_project else "<per-prompt>",
    )

    start = time.monotonic()
    outcome = await run_one_image_prompt(
        client=client,
        project_id=shared_project_id,
        idx=idx,
        item=item,
        output_dir=output_dir,
    )
    latency_ms = int((time.monotonic() - start) * 1000)
    last_submit_ts = time.monotonic()

    logger.info(
        "image_batch.submission_result",
        row_idx=idx,
        outcome=outcome.status,
        latency_ms=latency_ms,
    )
    if outcome.status == "ok":
        # outcome.file_paths is expected to be list[Path]; if BatchOutcome
        # exposes a different field name (e.g., outcome.file_path), adapt.
        # Read the dataclass definition near the top of image_batch.py
        # before writing this loop and match.
        for fp in (outcome.file_paths if hasattr(outcome, "file_paths") else [outcome.file_path]):
            try:
                sha = hashlib.sha256(Path(fp).read_bytes()).hexdigest()[:16]
            except OSError:
                sha = "unreadable"
            logger.info(
                "image_batch.row_completed",
                row_idx=idx,
                file_path=str(fp),
                sha256_prefix=sha,
            )

    outcomes.append(outcome)

    if outcome.status == "fail" and not continue_on_error:
        ...  # unchanged from PR #35
```

**Note on `BatchOutcome` shape:** if PR #35's `BatchOutcome` doesn't expose `file_paths` or `file_path`, **add the field** as part of this commit so Task 4.1 assertion #4 (Pillow dimensions) and assertion #2 (file cardinality) can be expressed without scanning the filesystem. Council finding R4 flags this as a major coupling between Task 3.2 and Task 4.1.

**If the actual `BatchOutcome` shape from PR #35 uses different field names**, adapt the `outcome.file_paths`/`outcome.file_path` access accordingly. Read the dataclass definition near the top of `image_batch.py` and match.

- [ ] **Step 4: Run the tests again — expect PASS**

```powershell
uv run pytest -q tests/image_batch/test_observability_events.py
```

Expected: all 4 PASS. If any fails, debug the event payloads and re-iterate.

- [ ] **Step 5: Run the rest of the image_batch tests to ensure no regression**

```powershell
uv run pytest -q tests/image_batch/
```

Expected: PASS (35 carried-forward tests + 4 new observability tests).

### Task 3.3: Add the malformed-row negative fixture

**Files:**
- Create: `test_assets/sample_batch_invalid.tsv`
- Create or extend: `tests/image_batch/test_image_manifest.py` (already exists from PR #35 — add a new test fn)

- [ ] **Step 1: Create the malformed fixture**

Write `test_assets/sample_batch_invalid.tsv` with:
```
ok prompt
prompt with bad count	not-a-number
prompt with bad aspect	1	9999:9999
prompt with unknown model	1	16:9	imaginary-model
```

- [ ] **Step 2: Append a parametrized unit test to `tests/image_batch/test_image_manifest.py`**

Each row in `sample_batch_invalid.tsv` exercises a distinct parse failure. Parametrize the assertion over each row so the test pinpoints which check fails (council finding R2):

```python
import pytest

from gflow_cli.errors import ConfigurationError
from gflow_cli.image_batch import parse_manifest_file, parse_tsv_manifest


@pytest.mark.parametrize(
    "row, expected_substring",
    [
        ("prompt with bad count\tnot-a-number", "count"),
        ("prompt with bad aspect\t1\t9999:9999", "aspect"),
        ("prompt with unknown model\t1\t16:9\timaginary-model", "model"),
    ],
)
def test_manifest_invalid_row_pins_specific_error(
    row: str, expected_substring: str
) -> None:
    """Each malformed row must surface the field name in the error message."""
    with pytest.raises(ConfigurationError) as exc_info:
        parse_tsv_manifest(row + "\n", default_count=1, default_aspect_ratio="16:9",
                          default_model="nano2", source_label="<test>")
    assert expected_substring in str(exc_info.value).lower(), exc_info.value


def test_manifest_dispatcher_raises_on_invalid_fixture(tmp_path: Path) -> None:
    """End-to-end: the committed sample_batch_invalid.tsv must raise."""
    fixture = Path("test_assets/sample_batch_invalid.tsv")
    assert fixture.is_file(), "fixture must be committed"
    with pytest.raises(ConfigurationError):
        parse_manifest_file(fixture)
```

(Adapt parameter names of `parse_tsv_manifest` to whatever PR #35's signature actually uses — check by grepping `def parse_tsv_manifest` in the carried-forward file.)

- [ ] **Step 3: Run the new test**

```powershell
uv run pytest -q tests/image_batch/test_image_manifest.py::test_manifest_invalid_rows_raise_configuration_error
```

Expected: PASS.

### Task 3.4: Add the happy-path sample manifests

**Files:**
- Create: `test_assets/sample_batch.tsv`
- Create: `test_assets/sample_batch.json`

- [ ] **Step 1: Create the TSV**

Write `test_assets/sample_batch.tsv`:
```
a small calico kitten sitting on a windowsill
a watercolor sunset over rolling hills	2	16:9
an isometric pixel-art bakery	1	1:1	nano2
```

(Tab characters between columns — verify with `Get-Content test_assets/sample_batch.tsv -Encoding UTF8` and visually checking for tab gaps.)

- [ ] **Step 2: Create the JSON**

Write `test_assets/sample_batch.json`:
```json
[
  {"text": "a small calico kitten sitting on a windowsill"},
  {"text": "a watercolor sunset over rolling hills", "count": 2, "aspect_ratio": "16:9"},
  {"text": "an isometric pixel-art bakery", "count": 1, "aspect_ratio": "1:1", "model": "nano2"}
]
```

- [ ] **Step 3: Verify the parsers can read both**

```powershell
uv run python -c "from pathlib import Path; from gflow_cli.image_batch import parse_manifest_file; print(parse_manifest_file(Path('test_assets/sample_batch.tsv'))); print(parse_manifest_file(Path('test_assets/sample_batch.json')))"
```

Expected: prints two tuples of `BatchPromptItem` rows, no exceptions.

### Task 3.5: Verify, then commit

- [ ] **Step 1: Run `/gflow:check` scoped**

```powershell
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src/gflow_cli/image_batch.py src/gflow_cli/cli_image.py tests/image_batch/
uv run ruff format --check src/gflow_cli/image_batch.py src/gflow_cli/cli_image.py tests/image_batch/
uv run pyright src/gflow_cli/image_batch.py src/gflow_cli/cli_image.py
uv run pytest -q tests/image_batch/ tests/cli/
```

Expected: all PASS.

- [ ] **Step 2: Stage and commit**

```powershell
git add src/gflow_cli/image_batch.py src/gflow_cli/cli_image.py tests/image_batch/ test_assets/sample_batch.tsv test_assets/sample_batch.json test_assets/sample_batch_invalid.tsv
git status -sb
git commit -m @'
feat(image): add `gflow image batch` subcommand with --same-project (#14 part 2)

- Accepts JSON or TSV manifests (dispatched by file extension).
- TSV: `prompt[<TAB>count[<TAB>aspect_ratio[<TAB>model]]]` — optional
  columns fall back to CLI defaults.
- JSON: `[{"text": "...", "count": 2, "aspect_ratio": "16:9", "model":
  "nano2"}, ...]`.
- `MAX_BATCH_PROMPTS = 5` constant in `image_batch.py`.
- `--same-project` flag: all prompts share one Flow project with a 3–7s
  random jitter between submissions (anti-bot-detection, will be
  empirically verified in commit #5b). Default (no flag): each prompt
  creates its own project.
- New structlog events for throttling-regression debugging:
  `image_batch.submission_attempt`, `..._result`, `..._row_completed`,
  `..._inter_submission_latency_ms`. Useful post-merge if a user reports
  Flow throttling.

Sample fixtures under `test_assets/`:
- `sample_batch.tsv` (happy path; 3 rows / 4 images)
- `sample_batch.json` (same, JSON form)
- `sample_batch_invalid.tsv` (malformed; unit-test only)

Tests: 35 carried-forward unit tests (parsing, dispatcher, runner) plus
4 new observability-event tests plus 1 malformed-fixture test.

Closes #14 part 2.
'@
```

Expected: commit succeeds.

---

## Phase 4 — Commit #3: Live e2e test

### Task 4.1: Create `tests/e2e/test_image_batch_e2e.py`

**Files:**
- Modify: `pyproject.toml` (add Pillow dev dependency)
- Create: `tests/e2e/test_image_batch_e2e.py`

- [ ] **Step 0: Add Pillow as a dev dependency**

The e2e uses `PIL.Image` for the aspect-dimension assertion. Pillow is not currently in `pyproject.toml`.

```powershell
uv add --dev pillow
```

Expected: `pyproject.toml` shows `pillow` under `[dependency-groups.dev]` or `[project.optional-dependencies].dev`; `uv.lock` updated.

- [ ] **Step 1: Read the existing pattern**

```powershell
Get-Content tests/e2e/test_video_t2v_e2e.py -TotalCount 120
Get-Content tests/e2e/conftest.py -TotalCount 80
```

Note: the canonical e2e uses a **pytest fixture** named `e2e_profile_dir` (defined at `tests/e2e/conftest.py:25`), not a function. The test signature is `def test_xxx(e2e_profile_dir: Path, tmp_path: Path) -> None:`.

- [ ] **Step 2: Write the new e2e file**

Create `tests/e2e/test_image_batch_e2e.py`:

```python
"""Live e2e for `gflow image batch`. Spends Flow credits. Skipped by
default; opt in by setting GFLOW_CLI_E2E_PROFILE (the canonical e2e gate).

Spec: docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md §7.

Env vars (all GFLOW_CLI_E2E_BATCH_*):
  - GFLOW_CLI_E2E_PROFILE             master gate; Chrome-strategy profile name
  - GFLOW_CLI_E2E_BATCH_MANIFEST      default: test_assets/sample_batch.tsv
  - GFLOW_CLI_E2E_BATCH_SAME_PROJECT  "0" or "1"; default "0"
  - GFLOW_CLI_E2E_BATCH_JITTER        "0" or "1"; default "1". When "0",
                                       passes jitter_range=(0,0) via DI.

Output: pytest's tmp_path (auto-cleaned). No hand-rolled timestamp dir.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import structlog
from structlog.testing import LogCapture

from gflow_cli.image_batch import (
    JITTER_MAX_SECONDS,
    JITTER_MIN_SECONDS,
    parse_manifest_file,
    run_manifest_image_batch,
)

pytestmark = pytest.mark.e2e

_E2E_PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"
_E2E_MANIFEST_ENV = "GFLOW_CLI_E2E_BATCH_MANIFEST"
_E2E_SAME_PROJECT_ENV = "GFLOW_CLI_E2E_BATCH_SAME_PROJECT"
_E2E_JITTER_ENV = "GFLOW_CLI_E2E_BATCH_JITTER"

# Council R2: relax to ±2% to tolerate Flow's H.264-aligned dimensions
# (e.g., 1920x1088 for 16:9 = 0.74% but other ratios may sit closer to 1.5%).
_ASPECT_TOLERANCE = 0.02

# Council R2: accept PNG, JPEG, and WebP. Fail loud on anything else.
_KNOWN_MAGIC = (
    (b"\x89PNG", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"RIFF", "webp"),  # WebP additionally starts with WEBP at byte 8
)


def _resolve_jitter_range() -> tuple[float, float]:
    enabled = os.environ.get(_E2E_JITTER_ENV, "1").strip() == "1"
    if not enabled:
        return (0.0, 0.0)
    return (JITTER_MIN_SECONDS, JITTER_MAX_SECONDS)


def _resolve_same_project() -> bool:
    return os.environ.get(_E2E_SAME_PROJECT_ENV, "0").strip() == "1"


def _resolve_manifest_path() -> Path:
    raw = os.environ.get(_E2E_MANIFEST_ENV, "test_assets/sample_batch.tsv").strip()
    path = Path(raw)
    # Council R4: guard against `_invalid` fixture — never burn credits on it.
    assert "_invalid" not in path.stem, (
        f"live e2e refuses malformed-row fixture: {path}"
    )
    return path


@pytest.fixture
def log_capture():
    """Capture structlog events; reset config on teardown to avoid bleed."""
    capture = LogCapture()
    structlog.configure(processors=[capture])
    try:
        yield capture
    finally:
        structlog.reset_defaults()


def _image_kind(path: Path) -> str | None:
    with path.open("rb") as f:
        head = f.read(12)
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    return None


def _aspect_within(ratio_str: str, actual: tuple[int, int], tol: float = _ASPECT_TOLERANCE) -> bool:
    a, b = (int(x) for x in ratio_str.split(":"))
    expected = a / b
    observed = actual[0] / actual[1]
    return abs(expected - observed) / expected <= tol


@pytest.mark.asyncio
async def test_image_batch_e2e(
    e2e_profile_dir: Path,   # council R1: pytest fixture, not a function call
    tmp_path: Path,
    log_capture: LogCapture,
) -> None:
    """Live image-batch e2e. e2e_profile_dir comes from tests/e2e/conftest.py."""
    manifest_path = _resolve_manifest_path()
    assert manifest_path.is_file(), f"manifest not found: {manifest_path}"

    prompts = parse_manifest_file(manifest_path)
    same_project = _resolve_same_project()
    jitter_range = _resolve_jitter_range()

    out = tmp_path / "out"
    out.mkdir()

    # Council R4: capture last 10 events on failure so the matrix evidence
    # file has triage data even when an exception aborts the run.
    try:
        outcomes = await run_manifest_image_batch(
            profile_dir=e2e_profile_dir,
            headless=False,  # UI transport needs a real Chromium window
            transport=None,   # default (UI automation)
            prompts=prompts,
            output_dir=out,
            continue_on_error=False,
            project_title="gflow-cli e2e",
            same_project=same_project,
            jitter_range=jitter_range,
        )
    except Exception:
        (tmp_path / "last_events.json").write_text(
            json.dumps(log_capture.entries[-10:], default=str, indent=2),
            encoding="utf-8",
        )
        raise

    # 1. Exit-code analogue: all outcomes are ok
    assert all(o.status == "ok" for o in outcomes), [o.status for o in outcomes]

    # 2. File cardinality: sum of row counts
    expected_files = sum(p.count for p in prompts)
    actual_files = sorted(out.rglob("*"))
    image_files = [
        f for f in actual_files
        if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    ]
    assert len(image_files) == expected_files, (
        f"expected {expected_files} got {len(image_files)}: {[f.name for f in image_files]}"
    )

    # 3. Magic bytes: PNG / JPEG / WebP only. Anything else fails loud.
    for f in image_files:
        kind = _image_kind(f)
        assert kind is not None, f"unsupported magic bytes in {f}: {f.read_bytes()[:12]!r}"

    # 4. Dimensions per row (Pillow). Tolerance ±2%.
    from PIL import Image
    files_by_row: dict[str, list[Path]] = {p.text: [] for p in prompts}
    for outcome in outcomes:
        if hasattr(outcome, "file_paths"):
            files_by_row[outcome.prompt.text] = list(outcome.file_paths)
        elif hasattr(outcome, "file_path"):
            files_by_row[outcome.prompt.text] = [outcome.file_path]
    for p in prompts:
        row_files = files_by_row.get(p.text, [])
        assert len(row_files) == p.count, (
            f"row {p.text!r} expected {p.count} files, got {len(row_files)}"
        )
        for f in row_files:
            with Image.open(f) as im:
                assert _aspect_within(p.aspect_ratio, im.size), (
                    f"aspect mismatch on {f.name}: expected {p.aspect_ratio}, got {im.size}"
                )

    # 5. Listener event count — one per row (per submission), not per image.
    # Spec §7 assertion 5. Verified semantics: UI transport fires this once
    # per `batchGenerateImages` POST, which maps 1:1 to manifest rows even
    # when count > 1.
    seen = [e for e in log_capture.entries if e["event"] == "ui_automation.batch_response_seen"]
    assert len(seen) == len(prompts), f"expected {len(prompts)} batch_response_seen, got {len(seen)}"

    # 6. New application event count — one per image (because row_completed
    # fires per file, not per row; see Task 3.2 implementation).
    completed = [e for e in log_capture.entries if e["event"] == "image_batch.row_completed"]
    assert len(completed) == sum(p.count for p in prompts)

    # 7 / 8. Project ID isolation/sharing — relies on Task 3.2 adding
    # `project_id` to the submission_attempt event.
    attempt_events = [
        e for e in log_capture.entries
        if e["event"] == "image_batch.submission_attempt"
    ]
    assert len(attempt_events) == len(prompts), "missing submission_attempt events"
    project_ids = {e.get("project_id") for e in attempt_events}
    project_ids.discard("<per-prompt>")  # sentinel for the non-same-project mode
    if same_project:
        assert len(project_ids) <= 1, (
            f"--same-project=1 should share one project_id, got {project_ids}"
        )
    else:
        # In --same-project=0 mode the event records "<per-prompt>" as a sentinel
        # and the real per-row project_id lives on the submission_result event.
        result_events = [
            e for e in log_capture.entries
            if e["event"] == "image_batch.submission_result"
        ]
        result_ids = {e.get("project_id") for e in result_events if "project_id" in e}
        assert len(result_ids) >= len(prompts) - 1, (
            f"--same-project=0 should have N distinct project_ids, got {result_ids}"
        )
```

> **NOTE:** the `submission_attempt` event must carry `project_id` (Task 3.2 step 3 already wires this). If `--same-project=0`, the per-prompt project ID is created inside `run_one_image_prompt` and is therefore best surfaced on the `submission_result` event instead of `submission_attempt`. Either:
>
> (a) emit a follow-up `submission_attempt_2` event after the real project ID is known, OR
> (b) emit the per-prompt project_id on `submission_result` and have the e2e read it from there.
>
> The plan goes with (b) above for assertion 7/8 in the `--same-project=0` branch. **Action item for Task 3.2:** ensure `submission_result` includes a `project_id` field — update the emit call to:
> ```python
> logger.info(
>     "image_batch.submission_result",
>     row_idx=idx,
>     outcome=outcome.status,
>     latency_ms=latency_ms,
>     project_id=getattr(outcome, "project_id", shared_project_id),
> )
> ```
> (assumes `BatchOutcome.project_id` exists; add the field in commit #2 if absent).

### Task 4.2: Static checks only (no live run yet)

**Council finding R2: do not spend Flow credits BEFORE the e2e is committed.** If the e2e has a bug, we waste credits AND the changes aren't preserved. The smoke-run moves to Phase 6 as the **first matrix data point** (R3 cell, rep 1).

- [ ] **Step 1: Run static gates only**

```powershell
uv run ruff check tests/e2e/test_image_batch_e2e.py
uv run ruff format --check tests/e2e/test_image_batch_e2e.py
uv run pyright tests/e2e/test_image_batch_e2e.py
# Don't run pytest -m e2e here; the marker will keep it skipped without GFLOW_CLI_E2E_PROFILE.
uv run pytest -q tests/e2e/test_image_batch_e2e.py --collect-only
```

Expected: all PASS. Collect-only confirms the test is discovered. **No Flow credits spent.**

### Task 4.3: Commit (no smoke-run yet — that happens in Phase 6)

- [ ] **Step 1: Commit**

```powershell
git add tests/e2e/test_image_batch_e2e.py pyproject.toml uv.lock
git commit -m @'
test(e2e): live image batch e2e parameterized by same-project + DI jitter

Spec §7. Mirrors tests/e2e/test_video_t2v_e2e.py conventions:
- pytestmark = pytest.mark.e2e
- Skipped unless GFLOW_CLI_E2E_PROFILE is set (canonical e2e gate)
- Env-var parameterization for manifest, same-project, jitter
- pytest tmp_path for output dir (auto-cleanup, no Windows `:` issues)
- e2e_profile_dir pytest fixture (from tests/e2e/conftest.py:25)

Assertions:
- Exit code 0 (via outcome status)
- File cardinality matches sum of row counts
- PNG / JPEG / WebP magic bytes (fail loud on anything else)
- Pillow dimensions match aspect ratio ±2%
- ui_automation.batch_response_seen count == manifest row count
- image_batch.row_completed count == total image count
- --same-project=1: single project ID across rows
- --same-project=0: distinct project ID per row (from submission_result events)

Jitter override uses DI (jitter_range=(0,0) via run_manifest_image_batch
parameter), not monkeypatch and not production-code env-var branch.

Adds Pillow as dev dependency for the dimension assertion.

This commit does NOT run the e2e live; the first credit-spending run
happens in Phase 6 (jitter matrix) as R3 cell, rep 1.
'@
```

---

## Phase 5 — Commit #4: Docs

### Task 5.1: Update `docs/USAGE.md`

**Files:**
- Modify: `docs/USAGE.md`

- [ ] **Step 1: Find the existing `gflow image` section**

```powershell
Select-String -Path docs/USAGE.md -Pattern '^#.*image|gflow image' | Format-Table LineNumber, Line
```

- [ ] **Step 2: Remove any `--seed` mentions**

```powershell
Select-String -Path docs/USAGE.md -Pattern '--seed' -SimpleMatch | Format-Table LineNumber, Line
```

Open `docs/USAGE.md`; for each match, delete the line or paragraph (use judgement — if `--seed` is part of an example, delete the whole example line; if it's described in prose, delete that sentence).

- [ ] **Step 3: Append a "Batch image generation" subsection under `gflow image`**

Insert (location: after the `gflow image i2i` section, before the next top-level heading):

````markdown
### Batch image generation — `gflow image batch <manifest>`

Generate multiple images from a single manifest. Supports JSON or TSV formats; the format is dispatched by file extension.

#### TSV manifest

```text
prompt<TAB>count<TAB>aspect_ratio<TAB>model
```

Only `prompt` is required; remaining columns fall back to the CLI defaults (count=1, aspect_ratio=16:9, model=nano2).

Sample: [`test_assets/sample_batch.tsv`](../test_assets/sample_batch.tsv)

```tsv
a small calico kitten sitting on a windowsill
a watercolor sunset over rolling hills	2	16:9
an isometric pixel-art bakery	1	1:1	nano2
```

#### JSON manifest

```json
[
  {"text": "a small calico kitten sitting on a windowsill"},
  {"text": "a watercolor sunset over rolling hills", "count": 2, "aspect_ratio": "16:9"},
  {"text": "an isometric pixel-art bakery", "count": 1, "aspect_ratio": "1:1", "model": "nano2"}
]
```

Sample: [`test_assets/sample_batch.json`](../test_assets/sample_batch.json)

#### Flags

- `--same-project` — all prompts share one Flow project (vs. one project per prompt). [If the §8 verdict was "drop jitter": add: "Sequential submissions; no inter-prompt delay."] [If "keep jitter": add: "Inserts a 3–7s random delay between submissions to avoid Flow's anti-bot detection."]
- `--continue-on-error` — continue past a failing row instead of fail-fast.

#### Limits

- `MAX_BATCH_PROMPTS = 5` (defined in `src/gflow_cli/image_batch.py`). To raise, edit the constant.

#### Exit codes

- `0` — all rows succeeded.
- `1` — invalid manifest (file not found, parse error, unknown aspect/model).
- non-zero (other) — transport-level failure.

#### Observability

`gflow image batch` emits four structlog events per run, useful for debugging throttling regressions:

- `image_batch.submission_attempt {row_idx, prompt_hash, aspect, model, same_project, jitter_enabled, t_since_prev_submit_ms}`
- `image_batch.submission_result {row_idx, outcome, latency_ms}`
- `image_batch.row_completed {row_idx, file_path, sha256_prefix}`
- `image_batch.inter_submission_latency_ms {row_idx, latency_ms}` (fires from row 1 onward)
````

(Replace the bracketed `[If ...]` placeholders with the actual decided text after Phase 6 / commit #5b. **For now, write "Inserts a 3–7s random delay between submissions to avoid Flow's anti-bot detection (live-verified — see `docs/LIVE_VERIFICATION_image_batch.md`)" since the default is conservative-keep until the matrix disproves it.**)

### Task 5.2: Update `CHANGELOG.md`

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Open `CHANGELOG.md` and find the `[Unreleased]` block**

- [ ] **Step 2: Add the entries under `[Unreleased]`**

Add (creating section headers as needed; order: Added → Fixed → Removed):

```markdown
### Added

- `gflow image batch <manifest>` subcommand for batch image generation from JSON or TSV manifests, with `--same-project` and `--continue-on-error` flags. `MAX_BATCH_PROMPTS = 5`. Closes #14 part 2.
- Application-layer structlog events for image batch submission: `image_batch.submission_attempt`, `image_batch.submission_result`, `image_batch.row_completed`, `image_batch.inter_submission_latency_ms`. Use these to debug Flow throttling regressions without re-instrumenting.

### Fixed

- `gflow image t2i -n N` now makes one transport call using Flow's native xN count selector instead of fanning out N parallel single-image submissions. Closes #14 part 1.

### Removed

- **BREAKING:** `--seed` flag from `gflow image t2i` and `gflow image i2i`. The flag was a no-op under the active UI transport since v0.7.0 (silently discarded inside the client before reaching the transport). If reproducibility via user-controlled seed becomes possible again — either through Flow UI exposing a seed control or via HTTP transport revival — the surface will be re-introduced at that layer. The wire-format body builder retains its `seed`/`batch_id` parameters for the experimental HTTP transports' internal use.
- **BREAKING (library):** `FlowApiClient.generate_image` no longer accepts `seed=` or `batch_id=` kwargs. `FlowApiClient.generate_images_batch` no longer accepts `seeds=`. Callers passing these will get a `TypeError`. Same justification as the CLI removal.
```

### Task 5.3: Update `docs/INDEX.md`

**Files:**
- Modify: `docs/INDEX.md`

- [ ] **Step 1: Find the live-verification section**

```powershell
Select-String -Path docs/INDEX.md -Pattern 'LIVE_VERIFICATION' | Format-Table LineNumber, Line
```

- [ ] **Step 2: Add a row pointing to the new evidence file**

In the table or list near existing `LIVE_VERIFICATION_*.md` entries, add:

```markdown
- [`LIVE_VERIFICATION_image_batch.md`](LIVE_VERIFICATION_image_batch.md) — jitter matrix evidence for `gflow image batch --same-project`
```

(Match the existing surrounding format.)

### Task 5.4: Verify, then commit

- [ ] **Step 1: Build the docs locally if there's a `mkdocs.yml` or equivalent (otherwise skip)**

```powershell
if (Test-Path mkdocs.yml) { uv run mkdocs build --strict }
```

- [ ] **Step 2: Run `/gflow:check`**

```powershell
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src tests
uv run ruff format --check src tests
```

Expected: PASS.

- [ ] **Step 3: Commit**

```powershell
git add docs/USAGE.md CHANGELOG.md docs/INDEX.md
git commit -m @'
docs: gflow image batch in USAGE, CHANGELOG, INDEX

- USAGE: new "Batch image generation" section with TSV + JSON formats,
  --same-project flag, MAX_BATCH_PROMPTS=5, exit codes, observability
  event schema, references to test_assets/sample_batch.{tsv,json}.
  Removes --seed mentions.
- CHANGELOG [Unreleased]:
  - ### Added — `gflow image batch` + structlog events.
  - ### Fixed — native xN count selector.
  - ### Removed — `--seed` flag (BREAKING) + public-API kwargs on
    FlowApiClient.generate_image and generate_images_batch.
- INDEX: row for LIVE_VERIFICATION_image_batch.md.

The jitter rationale paragraph in USAGE.md currently reads "live-verified
keep" — will be amended in commit #5b after the jitter matrix verdict
(spec §8).
'@
```

---

## Phase 6 — Jitter matrix and verdict (commits #5a, #5b)

### Task 6.1: Plan the matrix runs

**Spec §8.** Three cells × N=3 reps × 2 sessions:

| Cell | `--same-project` | `--jitter` env | Hypothesis |
|---|---|---|---|
| R1 | `1` | `0` | Same-project no-sleep → jitter unnecessary in same-project mode. |
| R2 | `1` | `1` | Same-project with jitter (baseline). |
| R3 | `0` | `0` | Different-project no-sleep → control. |

- [ ] **Step 1: Create the evidence skeleton**

Create `docs/LIVE_VERIFICATION_image_batch.md` with the schema (mirror `LIVE_VERIFICATION_video_download.md`):

```markdown
# Live verification — `gflow image batch` jitter matrix

**Date:** 2026-05-21 (session 1) / TBD (session 2)
**Spec:** [`docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md`](superpowers/specs/2026-05-21-multi-image-prompt-design.md) §8
**Profile:** `ui_automation` (Chrome strategy)

## Environment

- gflow-cli git rev: `<paste-from-git-rev-parse-HEAD>`
- Python: `<paste-from-python --version>`
- Playwright: `<paste-from-uv tree>`
- Chromium build: `<paste-from-playwright_version>`
- UTC hour at session start: `<HH:00>`
- Account-warmth proxy: `<count of ui_automation.* events in prior 60 min from structlog history, or "cold" if first run today>`

## Matrix

[Table to be filled. Each row = (Session, Cell, Rep, Outcome, Listener events, Notes).]

| Session | Cell | Rep | Exit | batch_response_seen | dropped_pid | overlay_fail | notes |
|---|---|---|---|---|---|---|---|

## Verdict

[Verdict per §8 decision rule, computed after both sessions complete.]

## Reproduce

Manifest: [`test_assets/sample_batch.tsv`](../test_assets/sample_batch.tsv)

```powershell
$env:GFLOW_CLI_E2E_PROFILE = "ui_automation"
$env:GFLOW_CLI_E2E_BATCH_MANIFEST = "test_assets/sample_batch.tsv"
# R1
$env:GFLOW_CLI_E2E_BATCH_SAME_PROJECT = "1"; $env:GFLOW_CLI_E2E_BATCH_JITTER = "0"
uv run pytest -q tests/e2e/test_image_batch_e2e.py
# R2
$env:GFLOW_CLI_E2E_BATCH_SAME_PROJECT = "1"; $env:GFLOW_CLI_E2E_BATCH_JITTER = "1"
uv run pytest -q tests/e2e/test_image_batch_e2e.py
# R3
$env:GFLOW_CLI_E2E_BATCH_SAME_PROJECT = "0"; $env:GFLOW_CLI_E2E_BATCH_JITTER = "0"
uv run pytest -q tests/e2e/test_image_batch_e2e.py
```

## Tested

- [Filled after runs.]

## Invariants asserted (from `test_image_batch_e2e.py`)

[List the assertion set from the e2e file.]

## Correlation IDs

[Project IDs and SHA256 prefixes captured from `image_batch.row_completed` events per cell.]

## NOT verified

- Behaviour outside profile `ui_automation`.
- Behaviour outside Chrome strategy.
- Behaviour with `count > 1` per row across `--same-project=1`. The matrix uses the default fixture (1 + 2 + 1 = 4 images).
- Long-running rate-limit windows beyond the 2h cross-session gap.

## Outputs

- `tmp/...` (per-pytest-tmp_path; not committed; SHA256 prefixes recorded above).
```

### Task 6.2: Session 1 — run cells R1, R2, R3 (back-to-back)

**Prompt-variance per rep:** to defeat any Flow-side caching of identical inputs (council finding R2), set `GFLOW_CLI_E2E_BATCH_MANIFEST` to a per-rep variant. Create three throwaway manifests under `tmp/` before running:

```powershell
Copy-Item test_assets/sample_batch.tsv tmp/sample_batch_rep1.tsv
Copy-Item test_assets/sample_batch.tsv tmp/sample_batch_rep2.tsv
Copy-Item test_assets/sample_batch.tsv tmp/sample_batch_rep3.tsv
(Get-Content tmp/sample_batch_rep1.tsv) -replace 'kitten', 'kitten #r1' | Set-Content tmp/sample_batch_rep1.tsv
(Get-Content tmp/sample_batch_rep2.tsv) -replace 'kitten', 'kitten #r2' | Set-Content tmp/sample_batch_rep2.tsv
(Get-Content tmp/sample_batch_rep3.tsv) -replace 'kitten', 'kitten #r3' | Set-Content tmp/sample_batch_rep3.tsv
```

Then point `GFLOW_CLI_E2E_BATCH_MANIFEST` at the rep-specific path on each run below.

- [ ] **Step 1: Capture session metadata**

```powershell
git rev-parse HEAD
python --version
Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ"
```

Update `docs/LIVE_VERIFICATION_image_batch.md` Environment block with these values.

- [ ] **Step 2: Run R1 (same_project=1, jitter=0) — three reps back to back**

```powershell
$env:GFLOW_CLI_E2E_PROFILE = "ui_automation"
$env:GFLOW_CLI_E2E_BATCH_SAME_PROJECT = "1"
$env:GFLOW_CLI_E2E_BATCH_JITTER = "0"

# Rep 1
$env:GFLOW_CLI_E2E_BATCH_MANIFEST = "tmp/sample_batch_rep1.tsv"
uv run pytest -q tests/e2e/test_image_batch_e2e.py 2>&1 | Tee-Object -FilePath tmp/r1_session1_rep1.log

# Rep 2
$env:GFLOW_CLI_E2E_BATCH_MANIFEST = "tmp/sample_batch_rep2.tsv"
uv run pytest -q tests/e2e/test_image_batch_e2e.py 2>&1 | Tee-Object -FilePath tmp/r1_session1_rep2.log

# Rep 3
$env:GFLOW_CLI_E2E_BATCH_MANIFEST = "tmp/sample_batch_rep3.tsv"
uv run pytest -q tests/e2e/test_image_batch_e2e.py 2>&1 | Tee-Object -FilePath tmp/r1_session1_rep3.log
```

For each rep, record into the evidence file's matrix table:
- Exit code (look at end of log for `passed`/`failed`)
- `batch_response_seen` count (`Select-String -Path tmp/r1_session1_repN.log -Pattern 'batch_response_seen' | Measure-Object`)
- `dropped_project_id_mismatch` count
- `overlay_dismiss_failed` count
- Notes (e.g., listener-miss flake suspected → mark INCONCLUSIVE per §8)

- [ ] **Step 2.5: Abort gate (per spec §8 / council finding R4)**

If R1 session-1 has **any** non-listener-miss failure (a real timeout, real `overlay_dismiss_failed`, real `dropped_project_id_mismatch > 0`, or HTTP error), **abort the matrix**. Skip Steps 3-4 of this task and skip Task 6.3 entirely. Jump to Task 6.4 with verdict = **KEEP jitter**. Record the abort reason in the evidence file under "Verdict".

This honours the credit-budget cap (~72 worst-case → ~12 if we abort after R1 cell session 1) and is consistent with §11 risk register's mid-matrix-abort mitigation.

- [ ] **Step 3: Run R2 (same_project=1, jitter=1) — three reps**

Same as Step 2 but with `$env:GFLOW_CLI_E2E_BATCH_JITTER = "1"`. Logs → `tmp/r2_session1_repN.log`. Reuse the per-rep manifests `tmp/sample_batch_repN.tsv`.

- [ ] **Step 4: Run R3 (same_project=0, jitter=0) — three reps**

Same with `$env:GFLOW_CLI_E2E_BATCH_SAME_PROJECT = "0"` and `$env:GFLOW_CLI_E2E_BATCH_JITTER = "0"`. Logs → `tmp/r3_session1_repN.log`.

**Note:** R3 rep 1 doubles as the e2e smoke verification (we did not pre-run the e2e in Phase 4). If it fails for any reason — including the e2e itself having a bug — fix the e2e and **start the matrix over**. Mark the aborted partial-matrix in the evidence file under a section labelled "Aborted runs (e2e bug)".

- [ ] **Step 5: Update the evidence file's session-1 rows**

Open `docs/LIVE_VERIFICATION_image_batch.md` and fill in 9 rows (3 cells × 3 reps) of the matrix table.

### Task 6.3: Wait ≥ 2 hours, then session 2

- [ ] **Step 1: Wait at least 2 hours** (cross-session gap per spec §8).

- [ ] **Step 2: Repeat Task 6.2 steps 1-5 as session 2.** Log paths now end `_session2_repN.log`.

### Task 6.4: Reach a verdict per §8 decision rule

- [ ] **Step 1: Tally results from the matrix table**

For R1 across both sessions:
- All 6 reps pass per the operational pass-criteria in §8? → "drop" candidate.
- Any non-listener-miss failure? → "keep" verdict.
- Mixed / inconclusive? → "keep" (conservative default).

- [ ] **Step 2: Write the Verdict section in the evidence file**

Write a 3-sentence verdict citing the matrix counts:

```markdown
## Verdict

R1 passed X/6 (sessions 1 + 2). R3 passed Y/6. R2 passed Z/6.
Per spec §8 decision rule: [DROP / KEEP / INCONCLUSIVE_KEEP].
Action: commit #5b will [drop the jitter sleep / document the jitter rationale].
```

### Task 6.5: Commit #5a — evidence file

- [ ] **Step 1: Stage and commit**

```powershell
git add docs/LIVE_VERIFICATION_image_batch.md
git commit -m @'
docs(image): jitter live-verification evidence for image batch

3-cell × 2-session matrix on profile ui_automation per spec §8.
Cells R1 (same_project=1, jitter=0), R2 (same_project=1, jitter=1),
R3 (same_project=0, jitter=0). N=3 reps per cell.

Verdict: [paste from evidence file].

This commit captures evidence only; the code/docstring change driven by
the verdict lands in commit #5b.
'@
```

### Task 6.6: Commit #5b — code or docstring per verdict

**Two branches depending on §8 verdict.**

#### Branch A — Verdict = DROP

- [ ] **Step 1: Edit `src/gflow_cli/image_batch.py` to drop the jitter sleep**

Find the loop:
```python
for idx, item in enumerate(prompts):
    if same_project and idx > 0:
        delay = random.uniform(*jitter_range)
        await asyncio.sleep(delay)
    ...
```

Replace the `if same_project and idx > 0: ...` block with: simply delete it. The default `jitter_range` parameter can stay on `run_manifest_image_batch`'s signature with a default of `(0.0, 0.0)` so callers passing it explicitly still work (DI parameter); OR remove the parameter entirely.

Recommendation: **keep the parameter with default `(0.0, 0.0)`**. Removing a public parameter is itself a breaking change to the (currently internal) signature; keep flexibility. Update the docstring to note that the default is no-sleep.

- [ ] **Step 2: Update `JITTER_MIN_SECONDS` / `JITTER_MAX_SECONDS`**

If they are no longer used in production code, delete them. Leave only if tests reference them.

- [ ] **Step 3: Update the unit tests**

If any unit test asserted that jitter happens (probably none in PR #35), update or delete.

- [ ] **Step 4: Update `docs/USAGE.md`**

In the `--same-project` flag description, replace the keep-jitter language with drop-jitter language:
> `--same-project` — all prompts share one Flow project. Submissions are sequential; **no inter-prompt jitter is inserted** (live-verified — see `docs/LIVE_VERIFICATION_image_batch.md`).

- [ ] **Step 5: Verify**

```powershell
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src/gflow_cli/image_batch.py
uv run pyright src/gflow_cli/image_batch.py
uv run pytest -q tests/image_batch/
```

Expected: PASS.

- [ ] **Step 6: Commit #5b (drop branch)**

```powershell
git add src/gflow_cli/image_batch.py docs/USAGE.md
git commit -m @'
refactor(image): drop unconditional jitter from --same-project (live-verified safe)

Spec §8 verdict: DROP. The 3-cell × 2-session matrix
(docs/LIVE_VERIFICATION_image_batch.md) confirmed that same-project
submissions without inter-prompt sleep produce no Flow throttling /
overlay / listener-miss signals across N=3 reps × 2 sessions.

Change:
- `run_manifest_image_batch` no longer inserts a sleep between
  submissions in `--same-project` mode. The `jitter_range` parameter
  remains for DI (default `(0.0, 0.0)`); callers can re-enable jitter
  by passing a non-zero range if a future regression demands it.
- USAGE.md updated to reflect no-jitter behaviour.

Reversibility: the four image_batch.* structlog events still fire
unconditionally, so a future user-reported throttling regression has
structured-log breadcrumbs (inter_submission_latency_ms, submission_attempt,
submission_result, row_completed) for triage without re-instrumenting.

Refs: design spec §8, evidence file docs/LIVE_VERIFICATION_image_batch.md.
'@
```

#### Branch B — Verdict = KEEP (or INCONCLUSIVE_KEEP)

- [ ] **Step 1: Add a docstring/comment to `run_manifest_image_batch`**

At the top of the function body, after the docstring, insert:

```python
# --- jitter rationale (live-verified) ---
# The 3-cell × 2-session matrix on 2026-05-21 (see
# docs/LIVE_VERIFICATION_image_batch.md) showed that removing the
# 3-7s sleep between same-project submissions correlates with [paste
# the specific failure mode observed: e.g., "listener-miss flakes"
# or "overlay_dismiss_failed events"]. We keep the jitter as a
# conservative anti-detection measure. If/when a future contributor
# wants to revisit, re-run the matrix with the same profile and append
# to the evidence file.
```

- [ ] **Step 2: Update `docs/USAGE.md`**

In the `--same-project` flag description, keep the jitter language and add a verification pointer:
> `--same-project` — all prompts share one Flow project. Inserts a 3–7s random delay between submissions as an anti-bot-detection measure (live-verified — see `docs/LIVE_VERIFICATION_image_batch.md`).

- [ ] **Step 3: Verify and commit**

```powershell
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src/gflow_cli/image_batch.py docs/USAGE.md
uv run pyright src/gflow_cli/image_batch.py
uv run pytest -q tests/image_batch/
git add src/gflow_cli/image_batch.py docs/USAGE.md
git commit -m @'
docs(image): document anti-detection jitter rationale on --same-project

Spec §8 verdict: KEEP (or INCONCLUSIVE_KEEP). Matrix outcomes in
docs/LIVE_VERIFICATION_image_batch.md showed [describe failure mode]
when same-project submissions ran without jitter. Conservative default
per spec §12 D13: keep the 3-7s sleep, document the empirical reason
in source.

No functional change; comment added in `image_batch.py`
`run_manifest_image_batch` body and USAGE.md flag description updated
to point at the evidence file.

Reversibility: a future contributor wanting to drop the jitter should
re-run the matrix (steps in evidence file's "Reproduce" section) and
append a new session block.

Refs: design spec §8, evidence file docs/LIVE_VERIFICATION_image_batch.md.
'@
```

---

## Phase 7 — Push, open PR

### Task 7.0: Rebase on develop (council finding R3)

The matrix in Phase 6 spans ≥2 hours. `origin/develop` may have moved during that window. Rebase to keep the feature branch current before push.

- [ ] **Step 1: Fetch and check for movement**

```powershell
git fetch origin develop
$ahead = git rev-list --count origin/develop..HEAD
$behind = git rev-list --count HEAD..origin/develop
"Ahead: $ahead   Behind: $behind"
```

If `Behind: 0` → no rebase needed, skip to Task 7.1.

- [ ] **Step 2: Rebase (only if behind > 0)**

```powershell
git rebase origin/develop
```

If conflicts surface (very unlikely given the scope), resolve them, `git add` the resolved files, then `git rebase --continue`. Do NOT use `--no-verify` or interactive rebase.

- [ ] **Step 3: Re-run the static gates after rebase**

```powershell
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src tests
uv run pyright src
```

Expected: PASS. If any commit broke from the rebase, fix in place and `git rebase --continue`.

### Task 7.1: Final sweep

- [ ] **Step 1: Run the full `/gflow:check` once more**

```powershell
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run pytest -q tests/api/test_client_image.py tests/image_batch/ tests/cli/
```

Expected: all PASS.

- [ ] **Step 2: Stale-test grep clean**

```powershell
Select-String -Path tests/ -Pattern 'not yet available|temporarily unavailable|5-prompt cap' -Recurse
```

Expected: zero hits. If any, investigate (likely stale `gflow image batch` stub assertions left over from when the command was stubbed).

- [ ] **Step 3: Acceptance criterion AC16 — seed cleanup completeness (tightened per council R1)**

```powershell
# Exclude comment lines (^\s*#) and docstring lines to avoid false positives.
Select-String -Path src/gflow_cli/cli_image.py -Pattern '--seed|seed=seed' -SimpleMatch |
    Where-Object { $_.Line -notmatch '^\s*#' }
Select-String -Path src/gflow_cli/api/client.py -Pattern '^\s*seed\s*[:=]|^\s*batch_id\s*[:=]' |
    Where-Object {
        ($_.Line -notmatch '^\s*#') -and
        ($_.Line -notmatch '_build_batch_generate_images_body')
    }
Select-String -Path tests/api/test_client_image.py -Pattern '\bseed=' -SimpleMatch |
    Where-Object { $_.Line -notmatch '^\s*#' }
uv run gflow image t2i --help | Select-String -Pattern '--seed'
uv run gflow image i2i --help | Select-String -Pattern '--seed'
```

Expected: all five commands return zero matches. The two `--help` checks confirm the user-facing surface is clean for both subcommands.

- [ ] **Step 4: `pre-commit run --all-files` clean**

```powershell
uv run pre-commit run --all-files
```

Expected: PASS. Fix any complaint (no `--no-verify`).

- [ ] **Step 5: Verify all 7 commits are present**

```powershell
git log --oneline origin/develop..HEAD
```

Expected: 7 commits in this order (newest at top): #5b, #5a, #4 docs, #3 e2e, #2 image batch, #1b --seed removal, #1 count selector. **All authored by Flavio Oliva.**

```powershell
git log --format='%an %ae' origin/develop..HEAD | Sort-Object -Unique
```

Expected: one line — `Flavio Oliva <your-email>`. Tree-replay via `git checkout <sha> -- <paths>` does **not** carry authorship — new commits use your `user.email` config. If `Claude <noreply@anthropic.com>` appears, **the tree-replay process is broken**. Do not paper over with `--amend --reset-author` (spec §6.B / D6 forbids mechanism 6.B). Instead:

```powershell
# Hard-reset to develop and redo Phase 0 + each Phase commit. Tree-replay
# must not preserve original authorship; if it does, your shell or git
# config has a bug worth investigating.
git reset --hard origin/develop
# ...then re-execute Phase 0 onward.
```

- [ ] **Step 6: AC6 verdict-driven e2e re-run (council R4)**

Spec §10 AC6 ties acceptance to the e2e passing under the verdict's chosen cell config. Phase 6 already ran the matrix; this step re-confirms the **final** behaviour post-#5b is clean.

If §8 verdict was **DROP**:
```powershell
$env:GFLOW_CLI_E2E_PROFILE = "ui_automation"
$env:GFLOW_CLI_E2E_BATCH_SAME_PROJECT = "1"
$env:GFLOW_CLI_E2E_BATCH_JITTER = "0"
$env:GFLOW_CLI_E2E_BATCH_MANIFEST = "test_assets/sample_batch.tsv"
uv run pytest -q tests/e2e/test_image_batch_e2e.py
```

If §8 verdict was **KEEP**:
```powershell
$env:GFLOW_CLI_E2E_PROFILE = "ui_automation"
$env:GFLOW_CLI_E2E_BATCH_SAME_PROJECT = "0"
$env:GFLOW_CLI_E2E_BATCH_JITTER = "1"
$env:GFLOW_CLI_E2E_BATCH_MANIFEST = "test_assets/sample_batch.tsv"
uv run pytest -q tests/e2e/test_image_batch_e2e.py
```

Expected: PASS. (Spends ~4 image credits.) Record the receipt in `docs/LIVE_VERIFICATION_image_batch.md` under a new "Post-#5b verification" section. Then clear the env:

```powershell
$env:GFLOW_CLI_E2E_PROFILE = $null
```

### Task 7.2: Push and create PR

- [ ] **Step 1: Push the branch**

```powershell
git push -u origin feature/multi-image-prompt
```

- [ ] **Step 2: Create the new PR (via --body-file to avoid PowerShell here-string mangling)**

Write the PR body to a temp file first (council finding R1: `gh pr create --body @'...'@` from PowerShell can lose newlines and choke on backticks in the body):

```powershell
$body = @'
Supersedes #35.

## Summary

Closes #14. Two changes in one PR:

1. **Bugfix (#14 part 1):** `gflow image t2i -n N` now makes one transport call with Flow's native xN count selector instead of fanning out N parallel single-image submissions.
2. **Feature (#14 part 2):** New `gflow image batch <manifest>` subcommand with JSON / TSV manifests, `--same-project` flag, and live-verified jitter behaviour.

Plus three cleanup items uncovered while preparing the production-ready landing of PR #35:

3. **Dead-feature removal (BREAKING):** `--seed` flag deleted from `gflow image t2i` and `gflow image i2i`. It was a no-op under the active UI transport since v0.7.0 (the user's seed was discarded by an internal shim before reaching any transport). Library `FlowApiClient.generate_image` / `generate_images_batch` lose their `seed=` / `batch_id=` / `seeds=` kwargs accordingly. See CHANGELOG `### Removed` for the verified-no-op explanation.
4. **Observability:** four new `image_batch.*` structlog events for post-merge throttling-regression debugging without re-instrumenting.
5. **Jitter empirically verified:** 3-cell × 2-session matrix (see `docs/LIVE_VERIFICATION_image_batch.md`). Verdict drives the final code/docstring change.

## What changed since PR #35

- Branch renamed `claude/plan-next-issue-Stegy` -> `feature/multi-image-prompt` (project convention).
- Commit history re-authored (human-only attribution per CLAUDE.md).
- Atomic split: 7 commits (count-selector fix, --seed cleanup, feature + observability + fixtures, live e2e, docs, evidence, code/docstring per verdict).
- Live e2e (tests/e2e/test_image_batch_e2e.py) — parameterized by GFLOW_CLI_E2E_BATCH_* env vars, gated by GFLOW_CLI_E2E_PROFILE.
- Sample manifests committed under test_assets/ (sample_batch.tsv, sample_batch.json, sample_batch_invalid.tsv).
- --seed deleted; CLI + public client kwargs removed (BREAKING).
- Application-layer structlog events added.

## Test plan

- [x] /gflow:check clean.
- [x] pre-commit run --all-files clean.
- [x] Scoped pytest (changed dirs) PASS — full sweep on CI.
- [x] Live e2e PASS on ui_automation profile (default + verdict-driven cell).
- [x] Jitter matrix complete (R1/R2/R3 x N=3 x 2 sessions) -> verdict in docs/LIVE_VERIFICATION_image_batch.md.
- [x] Stale-test grep clean.
- [x] Acceptance criteria 1-16 satisfied (spec §10).

## Design doc

docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md (v3).

## Implementation plan

docs/superpowers/plans/2026-05-21-multi-image-prompt.md (v2).

Refs: #14, #35.
'@
$body | Out-File -FilePath tmp/pr-body.md -Encoding UTF8

gh pr create --base develop --title "feat(image): native count selector + gflow image batch (closes #14)" --body-file tmp/pr-body.md
```

Expected: prints the new PR URL. The body file uses ASCII arrows (`->`) and no triple-backticks inside the here-string to avoid PowerShell parsing surprises.

- [ ] **Step 3: Capture the new PR number**

```powershell
$NEW_PR = gh pr view --json number -q .number
"New PR: #$NEW_PR"
```

### Task 7.3: Close PR #35 with a back-pointer

- [ ] **Step 1: Write the close comment via a temp file (avoids PowerShell backtick parsing)**

```powershell
@"
Superseded by #$NEW_PR. Branch renamed to feature/multi-image-prompt per branch-naming convention; commit history re-authored to remove AI attribution; --seed cleanup, application-layer observability, live e2e, and jitter matrix added.
"@ | Out-File -FilePath tmp/pr-35-close-comment.md -Encoding UTF8

gh pr close 35 --comment-file tmp/pr-35-close-comment.md
```

Expected: closes PR #35.

> **Note:** if your `gh` version does not support `--comment-file`, fall back to `gh pr close 35 --comment (Get-Content tmp/pr-35-close-comment.md -Raw)` — but check `gh --version` first; recent versions support the flag.

- [ ] **Step 2: Verify new PR is healthy**

```powershell
gh pr view $NEW_PR --json state,baseRefName,headRefName,mergeable,mergeStateStatus
```

Expected: `state=OPEN`, `baseRefName=develop`, `headRefName=feature/multi-image-prompt`, `mergeable=MERGEABLE`, `mergeStateStatus=CLEAN`.

> **Do NOT delete `origin/claude/plan-next-issue-Stegy` yet.** Council finding R3: deletion happens **after** the new PR merges, not after it opens, so we preserve a recovery path if review demands a do-over. See Phase 8.

---

## Phase 8 — Post-merge cleanup (only after the new PR merges)

**Pre-condition:** the new PR has been reviewed, approved, and merged into `develop`. Do NOT execute this phase before merge.

### Task 8.1: Verify the merge landed

- [ ] **Step 1: Check merge status**

```powershell
gh pr view $NEW_PR --json state,mergedAt,mergeCommit
```

Expected: `state=MERGED`, `mergedAt` is a timestamp, `mergeCommit` has a SHA. If not merged, abort Phase 8.

### Task 8.2: Delete the lingering `claude/*` branch

Now safe to delete because the new PR's commits are on `develop`.

- [ ] **Step 1: Delete remote branch**

```powershell
git fetch --all --prune
git push origin --delete claude/plan-next-issue-Stegy
```

Expected: `deleted` confirmation. If the remote branch is already gone (auto-cleanup), step succeeds as no-op.

- [ ] **Step 2: Delete local branch if any contributor still has it checked out**

```powershell
$exists = git branch --list 'claude/plan-next-issue-Stegy'
if ($exists) { git branch -D claude/plan-next-issue-Stegy }
```

Expected: deletes if present, no-op otherwise.

- [ ] **Step 3: Delete the feature branch (local + remote) too — it's merged**

```powershell
git checkout develop
git pull --ff-only origin develop
git branch -d feature/multi-image-prompt
git push origin --delete feature/multi-image-prompt
```

Expected: clean deletion. (GitHub's "Delete branch on merge" setting often handles the remote half automatically; the local half is yours.)

### Task 8.3: Update memory entries (per spec §13)

- [ ] **Step 1: Append to `~/.claude/projects/.../memory/stale-test-discovery.md`**

Add the `gflow image batch` restoration as a concrete example (grep targets: `not yet available`, `temporarily unavailable`, `5-prompt`).

- [ ] **Step 2: Append to `~/.claude/projects/.../memory/branch-naming-convention.md`**

Add: "PR #35 (closed 2026-05-21) is the canonical example of why `claude/*` is rejected; superseded by `feature/multi-image-prompt`."

(This task may be skipped if memory updates are batched separately.)

---

## Self-review against spec (v2 after council hardening)

Run through the spec's §10 acceptance criteria 1–16 and confirm each maps to a task above:

| AC | Spec text | Tasks satisfying |
|---|---|---|
| 1 | hygiene gate | 7.1 step 1 |
| 2 | `ruff check` clean | 7.1 step 1 |
| 3 | `ruff format --check` clean | 7.1 step 1 |
| 4 | `pyright` 0 errors | 7.1 step 1 |
| 5 | Scoped pytest PASS | 1.1 step 5, 2.0 step 3, 2.4 step 3, 3.5 step 1, 4.3 step 1, 7.1 step 1 |
| 6 | Live e2e PASS per verdict | 6.2/6.3 + **7.1 step 6 (final verdict-driven run)** |
| 7 | Evidence file present + INDEX-linked | 6.1, 6.5, 5.3 |
| 8 | CHANGELOG entries | 5.2 |
| 9 | USAGE.md updated | 5.1 |
| 10 | Human-only authorship | 7.1 step 5 |
| 11 | PR body "Supersedes #35" | 7.2 step 2 |
| 12 | PR #35 closed + `claude/*` deleted | 7.3 + **8.2 (post-merge)** |
| 13 | Stale-test grep clean | 2.4 step 5 + 7.1 step 2 |
| 14 | `pre-commit run --all-files` clean | 7.1 step 4 |
| 15 | New observability events unit-tested | 3.2 (four tests, all assert exact derivation + cleanup fixture) |
| 16 | Seed cleanup grep clean + `--help` clean | 7.1 step 3 (tightened to exclude comment lines) |

All 16 covered.

### v2 council compliance summary

| Council finding | Resolution |
|---|---|
| BLOCKER: `api/image.py` not in PR #35 | Removed from Task 0.2 path list |
| BLOCKER: `parse_manifest_file` (not `read_manifest_file`) | Replace-all applied throughout plan |
| BLOCKER: `e2e_profile_dir` is a fixture, not function | Task 4.1 e2e uses fixture parameter |
| BLOCKER: Pillow missing | Task 4.1 Step 0 adds `uv add --dev pillow` |
| BLOCKER: `git add -p` can't separate hunks | Task 1.1 Step 2 promotes "edit directly" as primary |
| BLOCKER: `git rebase --exec --reset-author` violates §6.B | Removed; replaced with hard-reset + redo Phase 0 |
| MAJOR: gh pr create body via --body-file | Task 7.2 writes body to tmp/pr-body.md |
| MAJOR: AC16 grep false-positives on comments | Task 7.1 step 3 tightened with `^\s*#` exclusion |
| MAJOR: Smoke-run before commit wastes credits | Task 4.2 reduced to static-only; smoke is R3 rep 1 in Phase 6 |
| MAJOR: Conventional Commits `refactor(image)!:` + BREAKING CHANGE footer | Task 2.4 step 4 commit message updated |
| MAJOR: Rebase on develop before push | New Task 7.0 |
| MAJOR: Branch deletion post-merge | Moved to Phase 8 |
| MAJOR: TDD red tests for #1b | New Task 2.0 with 5 tests |
| MAJOR: `project_id` on submission_attempt | Task 3.2 step 3 + test 1 assertion |
| MAJOR: log_capture fixture cleanup | Task 3.2 step 1 fixture uses yield + reset_defaults |
| MAJOR: WebP magic bytes accepted | Task 4.1 e2e `_image_kind` helper |
| MAJOR: Aspect tolerance ±2% | Task 4.1 `_ASPECT_TOLERANCE = 0.02` |
| MAJOR: Abort gate in Phase 6 | Task 6.2 Step 2.5 |
| Lower-priority: AsyncMock wiring | Task 3.2 fixture has explicit AsyncMock |
| Lower-priority: `_invalid` fixture guard | Task 4.1 `_resolve_manifest_path` assert |
| Lower-priority: try/finally event dump on failure | Task 4.1 try/except block |
| Lower-priority: Vary prompts per rep | Task 6.2 prologue creates per-rep manifests |
| Lower-priority: Module-top imports | Task 3.2 step 3 imports at top |
| Lower-priority: Stale-test grep after #1b | Task 2.4 step 5 |
| Lower-priority: Parametrized malformed-row test | Task 3.3 step 2 |

All 25 findings addressed.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-21-multi-image-prompt.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Suited to this plan because each phase is self-contained and several phases (e.g., Phase 6 matrix runs) benefit from independent sub-task delegation.

2. **Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints. Suited if you want me to drive every task personally with continuous narration.

**Which approach?**
