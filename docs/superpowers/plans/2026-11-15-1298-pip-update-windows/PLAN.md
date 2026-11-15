# `gflow update` keeps a Windows pip venv working (#1298) Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature 2026-11-15-1298-pip-update-windows` to find
> the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** on Windows, `gflow update` in a pip-managed install (venv or `--user`) either upgrades and
leaves a working install (exit 0), or refuses before pip runs and changes nothing (exit 11). It never
leaves an install that cannot import `gflow_cli`.

**Architecture:** one context manager in `src/gflow_cli/update_check.py` wraps the existing
`_run_command(inst.command, ...)` call in `run_update` (`:405`). For `inst.name == "pip"` on `win32`
it sets every console-script launcher aside before pip runs and restores any that pip did not
recreate. A sweep at the start of `run_update` deletes `.old` files left by an earlier run. uv tool,
pipx and every POSIX path stay untouched: the manager does nothing there. `run_update`'s branches do
not change (C901 is at its baseline, 12). A `with` adds no branch.

**Evidence:** `docs/superpowers/spikes/2026-11-15-1298-pip-update-windows-launcher.md`. Sections 7, 8
and 9 are authoritative; the "Fix Options" table is superseded wherever it disagrees with them.
- **Section 7.** The hold is `importlib.metadata`'s cached `zipfile.Path` on `sys.path[0]`, which
  is the zip-launched `gflow.exe`. `importlib.invalidate_caches()` plus `gc.collect()` releases it.
- **Section 9.** Rename aside, then pip, then restore gave a working 0.99.0, end to end inside a real
  `gflow.exe`.
- **Section 8.** pipx and uv tool already exit 0 and stay usable.

**Predict verdict:** CAUTION, 7/10 (2026-11-15). The design is proven in one process. Its risks are
in the interleavings below.

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Critical | Another process (a second `gflow mcp run`, `gflow serve`) holds a launcher, and pip strands the venv anyway | The rename is the pre-flight. One failed rename restores every launcher already moved and raises exit 11 BEFORE pip runs (Task 2 r3, Task 1 case B). |
| Critical | A metadata call between the release and the rename re-takes the hold | Read RECORD first, then release, then rename, then run pip, and make no `importlib.metadata` call in between. The test spies on the order (Task 2 r7). No thread in `gflow update` reads metadata: the update notice is skipped for `update` (`cli.py:214`), and the telemetry sender is a separate `python -m` process. |
| High | Ctrl-C or an exception between the rename and the end of pip leaves the launchers moved | The `finally` restores every launcher whose original path is missing (Task 2 r4, r5). |
| High | The restore runs after pip removed `gflow_cli` from disk | The `finally` uses only names bound BEFORE pip starts: `os`, the `(original, aside)` path pairs and the stdlib. No lazy `from gflow_cli... import` inside it; the house pattern of lazy imports is the trap here. |
| High | A hard kill (no `finally`) leaves `gflow.exe` missing. The next run can only be `python -m gflow_cli update` | A launcher listed in RECORD but missing means refuse with exit 11 and `_reinstall_hint(inst, __version__)`: close every gflow process, then `pip install --force-reinstall`, which regenerates the launchers (Task 2 r6). Never "another update is in flight" alone. |
| Medium | Two `gflow update` runs at once | The second finds launchers missing (the first moved them) and refuses, as for r6. |
| Medium | Existing installs (<= 0.99.0) run the OLD updater on their first hop to the fixed version, and are still stranded | Docs, CHANGELOG and the #1298 reply give the one-time command: close every gflow process, then `<venv>\Scripts\python.exe -m pip install --upgrade gflow-cli`. |
| Low | `.old` files pile up | The sweep deletes `<name>.gflow-update-<pid>.old` only when `<name>` exists, and skips an `OSError` (the file is still in use). |
| Low | Python 3.11/3.12 behave differently | The Windows CI job runs only `test_profile_lease*` on 3.13; the cache release was measured on 3.11, 3.12 and 3.14 locally (`MetadataPathFinder.invalidate_caches` clears `FastPath`). |

---

## File structure

### New files
```
tests/e2e/test_update_installer_e2e.py
  real installers, $0: builds a low and a high wheel from the tree under test, installs into tmp venvs
```

### Modified files
```
src/gflow_cli/update_check.py
  _launchers_set_aside (context manager), _sweep_set_aside, the `with` at :405, corrected comments
tests/test_update_check.py
  unit tests r1-r9 (any OS; sys.platform patched, launchers are files under tmp_path)
pyproject.toml
  register the `e2e_update` marker
docs/USAGE.md (~1996-2006), docs/E2E_TESTING.md, AGENTS.md (marker list), CHANGELOG.md, website mirror
src/gflow_cli/cli_update.py (docstring 5-7)
```

---

## Task 1 - real-installer e2e (red on the unfixed tree)

**What:** a re-runnable `e2e_update` test that reproduces #1298 with real pip on this machine and
fails on develop for the bug's own reason.

**Files:** `tests/e2e/test_update_installer_e2e.py`, `pyproject.toml` (marker:
`e2e_update: gflow update through real installers in tmp venvs - zero credits, no browser, no Flow
account`).

**Steps:**
- [x] Build two wheels from the tree under test, as `scripts/dev/spike_1298_pip_launcher.py build`
  does: `git archive HEAD` into tmp, set the version, then `uv build --wheel` (`UV_LINK_MODE=copy`).
  LOW is `0.0.1` and HIGH is `9999.0.0`. They are built once per session (a session fixture). Build
  from the working tree, not HEAD, if HEAD lacks the fix under test. Copy the tree into tmp, so the
  fix is in both wheels.
- [x] Install LOW from `--find-links <wheels>`. The deps come from PyPI, and the install must look
  like an index install: no `direct_url.json`. Run `update` with `PIP_NO_INDEX=1`,
  `PIP_FIND_LINKS=<wheels>`, `GFLOW_CLI_HOME=<tmp>`, and telemetry and the update notice off.
- [x] Case A, `pip` venv uncontested: `<venv>/Scripts/gflow.exe update` (`bin/gflow` on POSIX)
  exits 0. `python -I -c "import gflow_cli.cli"` reports `9999.0.0`. The new `gflow --version`
  runs. After a second `gflow --version` and `update --check`, no `*.gflow-update-*.old` is left.
- [x] Case B, Windows only: a live `gflow.exe mcp run` (stdin=PIPE) in the same venv. `update`
  exits 11 before pip. The version is still `0.0.1`, the import works, every launcher is present,
  there is no `.old`, and the message names a launcher path. Stop the child by PID.
- [x] Case C, Windows only: a `--user` install with `PYTHONUSERBASE=<tmp>`. The interpreter is
  `sys._base_executable`, never the user's real user site. Same expectations as case A.
- [x] Cases D and E: uv tool and pipx, each skipped when the tool is missing. Every home goes under
  tmp (`UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`, `PIPX_HOME`, `PIPX_BIN_DIR`), installed the
  index-simulated way of spike section 8. `update` exits 0 and the install is usable afterwards.
- [x] Run it on develop's code: case A must fail with exit 11 and `ModuleNotFoundError` (the bug),
  not with a harness error. Quote that red in the commit body.

**Tests created (red):**
- [x] `test_pip_venv_update_keeps_a_working_install` - case A
- [x] `test_pip_update_refuses_untouched_while_another_gflow_runs` - case B
- [x] `test_pip_user_update_keeps_a_working_install` - case C
- [x] `test_uv_tool_update_still_works`, `test_pipx_update_still_works` - D, E (green before and after)

---

## Task 2 - unit tests (red), any OS

**What:** pin the context manager's contract with fakes: launchers are files under `tmp_path`,
`sys.platform` is patched to `win32`, and `distribution("gflow-cli").files` is faked.

**Files:** `tests/test_update_check.py`

**Tests created (red):**
- [x] r1 every RECORD `.exe` launcher is moved aside BEFORE the manager runs: the fake
  `_run_command` asserts that the originals are absent.
- [x] r2 after the manager returns (rc 0 and rc 1), a launcher pip recreated is kept and its `.old`
  is left for the sweep. A launcher pip did not recreate is restored.
- [x] r3 `os.replace` raising `PermissionError(13, ..., None, 32)` on the 2nd launcher restores the
  1st and raises `ConfigurationError` (exit 11). The manager is never called. The message names the
  path and does not suggest `python -m`.
- [x] r4 `KeyboardInterrupt` from the manager restores the moved launchers and re-raises.
- [x] r5 any other exception from the manager does the same.
- [x] r6 a RECORD launcher missing on disk refuses with exit 11, the hint is
  `_reinstall_hint(inst, __version__)`, and the manager is not called.
- [x] r7 order: RECORD is read, THEN `importlib.invalidate_caches` and `gc.collect`, THEN the first
  `os.replace`. Spy on all three.
- [x] r8 `uv`, `pipx` and non-win32 `pip`: no rename, the manager runs as before.
- [x] r9 the sweep deletes `gflow.exe.gflow-update-123.old` when `gflow.exe` exists, keeps it when
  `gflow.exe` is missing (r6 needs it), and skips an `OSError` on delete. It runs before
  `run_update`'s "already up to date" return.

---

## Task 3 - implement

**Files:** `src/gflow_cli/update_check.py`

**Steps:**
- [x] `_launchers_set_aside(inst)`: a `contextlib.contextmanager`. It yields at once unless
  `inst.name == "pip"` and `sys.platform == "win32"`.
  1. Read the launchers: `distribution("gflow-cli").files` entries ending in `.exe`, resolved with
     `.locate()`. Add none from anywhere else.
  2. Check that each exists (r6).
  3. Release: `importlib.invalidate_caches()`, then `gc.collect()`.
  4. Move each one with `os.replace` to `<name>.gflow-update-<pid>.old`. On `OSError`, restore and
     raise (r3).
  5. `yield`. The `finally` restores any launcher whose original path is missing.
- [x] `_sweep_set_aside()`: called once in `run_update` right after `installer()`. It is a plain
  call, so it adds no new branch to `run_update`.
- [x] Wrap only `res = _run_command(...)` at `:405`.
- [x] Refusal text (r3), neutral about who holds the file: "`<path>` is in use by another program,
  so gflow changed nothing". The hint: "close every program that may be running gflow (an MCP
  server, `gflow serve`, another terminal), then run `gflow update` again".
- [x] Tasks 1 and 2 go green. Keep `run_update` C901 at 12 or below, and run `check_size.py`.

## Task 4 - docs

- [x] `docs/USAGE.md` ~1996-2006: replace "the benign case". uv (installs first) and pipx (the
  running copy is outside its venv) were always fine. pip now sets the launchers aside, or refuses
  untouched when another program holds one. Add the one-time command for installs at 0.99.0 and
  below.
- [x] `update_check.py` `:408-412`: the claim that the trampoline "cannot be renamed aside" is false
  (sections 7 and 9). `:449-451`: name uv's failed launcher copy, not "the launcher".
- [x] `cli_update.py` docstring 5-7, `docs/E2E_TESTING.md` (the `e2e_update` row and tree), the
  AGENTS.md marker list, and `CHANGELOG.md` `[Unreleased]` `### Fixed`, with the one-time command.
  Regenerate the website mirror.
- [x] grep the whole repo for "benign", "cannot be renamed aside" and "cannot be replaced while in
  use". `docs/LIVE_VERIFICATION_v0.68.0.md` is a historical record: leave it.

---

## Definition of done

- [x] Task 1's case A seen red on develop's code for the bug's reason (exit 11 + `ModuleNotFoundError`)
- [x] All steps checked; `/gflow:check` green; `check_size.py` green
- [ ] `e2e_update` run on the final tree on Windows: A, B and C pass; D and E pass, or are skipped
  with the missing tool named
- [ ] Two fresh xhigh reviews (`/code-review`, `ponytail-review`) and one fix round
- [ ] CodeRabbit answered; the SonarCloud scan step concluded success with 0 new issues
- [ ] Worktree clean; the `_spike_out/1298` venvs deleted after merge

## Decision log

| Date | Decision | Why | Rejected |
|---|---|---|---|
| 2026-11-15 | Rename the launchers aside in-process, and refuse when that fails | Sections 7 and 9: the self-rename works after the release, and pip then succeeds end to end | A detached helper: more moving parts, a window with no output, and the user loses the exit code. Message-only refusal: it does not fix the bug. |
| 2026-11-15 | Scope: pip on win32 only | Section 8: pipx and uv tool already exit 0 and stay usable; POSIX unlinks open files | Wrapping every installer |
| 2026-11-15 | A missing launcher means refuse with the force-reinstall hint | Neither a hard kill nor a concurrent update can be told apart without a PID probe, and both are repaired by closing gflow and force-reinstalling | Auto-restoring an `.old` from a possibly live update |
| 2026-11-15 | One-time manual step for <= 0.99.0 | The old updater runs on the first hop, so no code shipped now can reach it | none |
| 2026-11-15 | Review round: RECORD-based launchers, WinError 32 only refusal, `--user` | RECORD lists launchers even if missing (Python 3.12 `files` drops missing); only WinError 32 means "in use" (other rename errors let pip run without guard); `--user` upgrades with `--user` to prevent duplicate shadowing copy | Reading `Distribution.files`; refusing on all rename errors (breaks all-users Python); omitting `--user` from pip command |
