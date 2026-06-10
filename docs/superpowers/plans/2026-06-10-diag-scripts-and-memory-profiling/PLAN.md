# Diag Scripts Directory and Memory Profiling Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature diag-scripts-and-memory-profiling` to
> find the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Establish `scripts/diag/` as the documented home for investigation scripts,
migrate the two existing `diag_*.py` scripts there, add `--disable-dev-shm-usage` to
both Chrome launch paths (the only safe flag we can ship without live measurements), and
write a `memory_profile.py` diagnostic that measures Chrome process-tree RSS so the
remaining issue #155 checklist items can be run by anyone with a live profile.

**Architecture:** Two Chrome launch paths need the flag — `FlowApiClient._persistent_context_kwargs()`
in `client.py` (has a pinned kwargs test at `tests/api/test_client_launch_kwargs.py`) and
`UiAutomationTransport.setup()` in `ui_automation.py` (inline launch, no pinned kwargs test
yet). The memory profiler lives in `scripts/diag/` as a standalone script; it uses `psutil`
detected at runtime (not added as a hard dep) and works on Linux, macOS, and Windows.
No new production dependencies.

**Predict verdict:** GO — confidence 9/10 (4-angle council already reviewed issue #155;
no auth path changes; `--disable-dev-shm-usage` is the standard Docker/container fix,
no effect outside containers)

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Low | `git mv` of `diag_*.py` breaks local shell aliases | Docstring updated with new path; git rename tracking preserves history |
| Low | `--disable-dev-shm-usage` changes Chrome behaviour on developer machines | Flag is a no-op when `/dev/shm` is adequately sized; only changes behaviour when `/dev/shm` ≤ 64 MB (Docker default) |
| Low | `test_client_launch_kwargs.py` pins exact args list — adding a flag breaks it | Task 1 is the red test; Task 2 is the green fix. Test must fail first. |
| Low | `ui_automation.py` launch has no pinned kwargs test | Task 1 adds one alongside the client test |

---

## File structure

### New files
```
scripts/diag/README.md
  Category documentation — what belongs here, how to run, platform notes
scripts/diag/memory_profile.py
  Chrome process-tree RSS profiler for issue #155 measurement checklist
```

### Moved files (git mv — history preserved)
```
scripts/diag_capture_flow_traffic.py  →  scripts/diag/capture_flow_traffic.py
scripts/diag_recaptcha_mint.py        →  scripts/diag/recaptcha_mint.py
```

### Modified files
```
src/gflow_cli/api/client.py
  Add "--disable-dev-shm-usage" to args list in _persistent_context_kwargs()
src/gflow_cli/api/transports/ui_automation.py
  Add "--disable-dev-shm-usage" to args list in setup() inline launch
tests/api/test_client_launch_kwargs.py
  Update pinned args assertion + add new test pinning ui_automation setup() args
docs/INDEX.md
  Add scripts/diag/README.md row to the docs table
```

---

## Task 1 — Pinned launch-kwargs tests (red scaffold)

**What:** Update the existing `test_client_launch_kwargs.py` assertion and add a new
test pinning `UiAutomationTransport.setup()`'s Chrome args — both go red before
production code changes.

**Files:**
- `tests/api/test_client_launch_kwargs.py` — update args assertion + add ui_automation test

**Steps:**
- [ ] In `test_persistent_context_kwargs_are_unchanged`, change the args assertion to:
  `assert kwargs["args"] == ["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"]`
- [ ] Add `test_ui_automation_setup_passes_disable_dev_shm_usage` that mocks `async_playwright`,
  calls `UiAutomationTransport.setup()`, and asserts `"--disable-dev-shm-usage"` is in the
  `args` kwarg passed to `launch_persistent_context`
- [ ] Run `pytest tests/api/test_client_launch_kwargs.py -v` — confirm BOTH tests are RED

**Tests created (red):**
- [ ] `test_persistent_context_kwargs_are_unchanged` (updated) — asserts `--disable-dev-shm-usage` in `FlowApiClient` kwargs args
- [ ] `test_ui_automation_setup_passes_disable_dev_shm_usage` (new) — asserts `--disable-dev-shm-usage` in `UiAutomationTransport.setup()` launch call args

---

## Task 2 — Add `--disable-dev-shm-usage` to both Chrome launch paths

**What:** Add the flag to `_persistent_context_kwargs()` in `client.py` and to the inline
`args` list in `ui_automation.py` `setup()`. Tests from Task 1 go green.

**Files:**
- `src/gflow_cli/api/client.py` — `_persistent_context_kwargs()`, `args` list (line ~296)
- `src/gflow_cli/api/transports/ui_automation.py` — `setup()`, inline `args` list (line ~677)

**Steps:**
- [ ] In `client.py` `_persistent_context_kwargs()`, extend `"args"` to:
  `["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"]`
- [ ] In `ui_automation.py` `setup()`, extend `args=` to:
  `["--disable-blink-features=AutomationControlled", "--password-store=basic", "--disable-dev-shm-usage"]`
- [ ] Run `pytest tests/api/test_client_launch_kwargs.py -v` — both tests GREEN
- [ ] Run `pytest -q -m "not live and not e2e and not smoke"` — no regressions

**Tests must pass (green):**
- [ ] `test_persistent_context_kwargs_are_unchanged`
- [ ] `test_ui_automation_setup_passes_disable_dev_shm_usage`

---

## Task 3 — Create `scripts/diag/`, move existing diag scripts, write README

**What:** Establish `scripts/diag/` as a documented, first-class directory.
Git-move the two existing `diag_*.py` files so history is preserved. Write the README
that defines what belongs in this directory and why.

**Files:**
- `scripts/diag/README.md` — new
- `scripts/diag/capture_flow_traffic.py` — moved from `scripts/diag_capture_flow_traffic.py`
- `scripts/diag/recaptcha_mint.py` — moved from `scripts/diag_recaptcha_mint.py`

**Steps:**
- [ ] `git mv scripts/diag_capture_flow_traffic.py scripts/diag/capture_flow_traffic.py`
- [ ] `git mv scripts/diag_recaptcha_mint.py scripts/diag/recaptcha_mint.py`
- [ ] Update "Run:" example in docstring of both moved files to use new path
- [ ] Write `scripts/diag/README.md` with: one-paragraph purpose, script table
  (name | purpose | prerequisite | run command), "What belongs here" criteria
  (needs live session, produces human-readable output, does not modify production state),
  "What does NOT belong here" (CI gates → `scripts/ci/`; dev workflow → `scripts/dev/`)
- [ ] Run `uv run python scripts/ci/check_doc_links.py` — green

---

## Task 4 — Write `scripts/diag/memory_profile.py`

**What:** Chrome process-tree RSS profiler. Launches the production browser context,
samples RSS at key milestones (baseline, post_launch, post_navigation, post_close),
and prints a structured report. Answers the first checklist item in issue #155.

**Files:**
- `scripts/diag/memory_profile.py` — new
- `scripts/diag/README.md` — add memory_profile row to the script table

**Steps:**
- [ ] Write `scripts/diag/memory_profile.py` with:
  - CLI: `--profile NAME` (required), `--output-json PATH` (optional)
  - `psutil` detection at module load — print install hint and `sys.exit(2)` if missing
  - `_process_tree_rss(pid)` — sums RSS of process + all children recursively
  - Milestones: `baseline` (before Playwright), `post_launch` (after `launch_persistent_context`),
    `post_navigation` (after `page.goto(FLOW_URL)`), `post_close` (after context close)
  - Prints human-readable table: milestone | own RSS (MB) | tree RSS (MB) | delta from baseline (MB)
  - Writes JSON if `--output-json` provided: `{"milestones": [...], "platform": ..., "chrome_args": [...]}`
  - Uses same Chrome args as production (`--disable-blink-features=AutomationControlled`,
    `--disable-dev-shm-usage`) and same `launch_persistent_context` kwargs shape as `client.py`
  - Exit 0 on success, 1 on profile-not-found, 2 on missing psutil
- [ ] Update `scripts/diag/README.md` script table to include `memory_profile.py`
- [ ] Run `uv run pyright scripts/diag/memory_profile.py` — clean (or note if pyright
  not configured for scripts/ and skip)

---

## Task 5 — Update `docs/INDEX.md`

**What:** Add `scripts/diag/README.md` to the documentation index so agents and
contributors can discover it through the standard routing layer.

**Files:**
- `docs/INDEX.md` — add one row

**Steps:**
- [ ] Add a row to the docs table (in the scripts section, after the skillopt row):
  `**[scripts/diag/README.md](../scripts/diag/README.md)**` | "Diagnostic investigation
  scripts — run against a live authenticated profile to capture wire samples, measure
  Chrome memory, or mint reCAPTCHA tokens" | "Running a one-off investigation against a
  live Flow session; setting up issue #155 memory measurements"
- [ ] Run `uv run python scripts/ci/check_doc_links.py` — green

---

## Task 6 — Full gates, CHANGELOG, close issue

**What:** All gates green; changelog updated; issue #155 closed.

**Files:**
- `CHANGELOG.md` — add `[Unreleased]` entries

**Steps:**
- [ ] Run full Impeccable Routine:
  ```
  uv run python scripts/ci/check_repo_hygiene.py
  uv run python scripts/ci/check_doc_links.py
  uv run ruff check src tests
  uv run ruff format --check src tests
  uv run pyright src
  uv run python -m pytest -q -m "not live and not e2e and not smoke"
  ```
- [ ] Add `CHANGELOG.md` entries under `[Unreleased]`:
  - `### Changed` — `--disable-dev-shm-usage` added to Chrome launch args in both
    `FlowApiClient` and `UiAutomationTransport`; prevents OOM in Docker containers
    with default `/dev/shm` allocation (64 MB)
  - `### Added` — `scripts/diag/` directory with `memory_profile.py` (Chrome RSS profiler),
    `capture_flow_traffic.py`, `recaptcha_mint.py` (both moved from `scripts/` root)
- [ ] Close issue #155 with link to commits

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
- [ ] `docs/INDEX.md` has `scripts/diag/README.md` entry
- [ ] `scripts/diag/` has README + 3 scripts (2 moved + 1 new); no loose `diag_*.py` at `scripts/` root
- [ ] Both Chrome launch paths include `--disable-dev-shm-usage`
- [ ] Issue #155 closed
