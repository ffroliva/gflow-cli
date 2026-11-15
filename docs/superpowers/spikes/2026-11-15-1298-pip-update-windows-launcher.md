# Spike: `gflow update` vs the running `gflow.exe` launcher on Windows (#1298)

**Date:** 2026-11-15 · **Platform:** Windows 11 (OS: Windows, Python: 3.13.3 via `py -3.13`, pip: 25.0.1)  
**Script:** [`scripts/dev/spike_1298_pip_launcher.py`](../../../scripts/dev/spike_1298_pip_launcher.py)  
**Cost:** $0 (no calls to Google Flow; only PyPI package index queries and local wheel builds)  
**Worktree:** `C:\development\github\gflow-cli\.worktrees\1298` (branch: `bugfix/1298-pip-update-windows`)

---

## Question

Why does `gflow update` executed via `gflow.exe` in a **pip** virtual environment on Windows leave the environment broken and unimportable (issue #1298), whereas `uv tool upgrade` supposedly keeps working, and `python -m gflow_cli update` succeeds? What exact filesystem operations does Windows allow or forbid on a running executable launcher vs loaded `.pyd` libraries, and which architectural fix options do these measurements support or kill?

---

## Method

All experiments were run strictly inside scratch virtual environments under `scripts/dev/_spike_out/1298/` (gitignored), using a local baseline wheel lowered to version `0.98.99` while PyPI served `0.99.0`.

1. **`build`**: Extract `git archive HEAD` into `_spike_out/1298/src`, pin `version = "0.98.99"` in `pyproject.toml` and `src/gflow_cli/__init__.py`, and build `gflow_cli-0.98.99-py3-none-any.whl` via `uv build --wheel` (`UV_LINK_MODE=copy`).
2. **`venv <name>`**: Create a clean Python 3.13 venv (`py -3.13 -m venv`) and install `gflow-cli==0.98.99` using `--find-links` with PyPI dependencies.
3. **`reproduce <name>`**: Execute `<venv>\Scripts\gflow.exe update` (no flags). Capture exit code, stdout/stderr, test `python -I -c "import gflow_cli"`, list `gflow*.exe*`, and inspect `pip list`.
4. **`locks <name>`**: Spawn `<venv>\Scripts\gflow.exe mcp run` as a long-lived child with stdin piped. Probe file locking semantics on both the running `gflow.exe` launcher and a loaded `.pyd` module (`pydantic_core\_pydantic_core*.pyd`):
   - `open(p, "r+b")`
   - `os.remove(p)`
   - `os.replace(p, p + ".old")` (followed by writing new bytes to `p` and restoring)
   - `os.rename(p, tempfile.gettempdir() / ...)`
5. **`renamed <name>`**: With a running child launcher, test whether renaming `gflow.exe` aside prior to `pip install --upgrade` is possible and whether pip upgrade succeeds.
6. **`pymodule <name>`**: Control run: execute `<venv>\Scripts\python.exe -m gflow_cli update`.
7. **`pipdry <name>`**: Trace the chronological order of operations during `pip install -v --upgrade` using verbose log output and filesystem inspection.
8. **`check-temp <name>`**: Verify drive/volume alignment between `tempfile.gettempdir()` and the venv, then re-test `reproduce` with `TMP` and `TEMP` directed to a directory within the venv root.

---

## Pre-Registered Expectations

| Scenario | Hypothesis |
|---|---|
| `reproduce` | Exit 11; `[WinError 32]` on `gflow.exe`; `import gflow_cli` fails with `ModuleNotFoundError`. |
| `locks` on `gflow.exe` | Windows image section (`SEC_IMAGE`) blocks write, remove, and rename. |
| `locks` on `.pyd` | Windows DLL loader blocks remove/write, but allows rename/replace on the same volume. |
| `renamed` | Rename fails with `[WinError 32]` because the launcher binary is running. |
| `pymodule` | Control succeeds (exit 0) because `gflow.exe` is not running. |
| TMP/TEMP redirect | Changes nothing because default `%TEMP%` is already on drive `C:`. |

---

## Results

### 1. Reproduction: `gflow.exe update` (`venv a`)

Command: `_spike_out/1298/a/Scripts/gflow.exe update`

| Venv | update Exit | WinError 32 | Import Probe Afterwards | `gflow*.exe*` on Disk | `pip list` Output |
|---|---|---|---|---|---|
| `a` | **exit 11** | **True** | **FAILED**: `ModuleNotFoundError: No module named 'gflow_cli'` | `gflow.exe` (stale) | `NOT LISTED` |

**Exact error captured:**
```
ERROR: Could not install packages due to an OSError: [WinError 32] The process cannot access the file because it is being used by another process: '...\\a\\scripts\\gflow.exe'
Check the permissions.
```
**gflow diagnostic log:**
```
{"installer": "pip", "returncode": 1, "event": "update.environment_broken", "level": "warning"}
Configuration error: `.../python.exe -m pip install --upgrade gflow-cli` left this gflow-cli install unusable: a fresh interpreter can no longer import it (ModuleNotFoundError: No module named 'gflow_cli')
```

### 2. Lock Semantics: Running Launcher vs Loaded `.pyd` (`venv b`)

Child process: `gflow.exe mcp run` (PID 37440) holding files active in memory.

| Target | `open(r+b)` | `os.remove` | `os.replace` + new file | `os.rename` into `%TEMP%` |
|---|---|---|---|---|
| `gflow.exe` (running launcher) | `PermissionError: [Errno 13]` | `PermissionError: [WinError 32]` | `PermissionError: [WinError 32]` | `PermissionError: [WinError 32]` |
| `_pydantic_core.cp313-win_amd64.pyd` (loaded DLL) | `PermissionError: [Errno 13]` | `PermissionError: [WinError 5]` | **SUCCESS** (replace ok, write ok, restore ok) | **SUCCESS** (rename & restore ok) |

**Key finding:**  
**Corrected by the coordinator's controls (section 7):** the `WinError 32` is real, but its cause is not an NT image lock on running executables. A running pip launcher with nothing else holding it CAN be renamed aside. What holds `gflow.exe` is the gflow process itself: pip's launcher makes Python run `gflow.exe` as a zip (`sys.path[0]`), and `importlib.metadata` caches a `FastPath` whose `zipfile.Path` keeps that archive open. Loaded `.pyd` files can be renamed or replaced, as measured here.

### 3. Attempting to Rename Launcher Aside (`venv c`)

Child process: `gflow.exe mcp run` (PID 37948).

| Step | Operation | Result |
|---|---|---|
| 1 | `os.rename(gflow_exe, gflow_exe + ".old")` | **FAILED**: `PermissionError: [WinError 32] The process cannot access the file because it is being used by another process` |
| 2 | `python -m pip install --upgrade gflow-cli` | **FAILED**: exit 1 (`WinError 32` on `gflow.exe`) |
| 3 | `import gflow_cli` | **FAILED**: `ModuleNotFoundError` |

### 4. Control Run: `python -m gflow_cli update` (`venv d`)

Command: `_spike_out/1298/d/Scripts/python.exe -m gflow_cli update`

| Venv | update Exit | Import Version Afterwards | Notes |
|---|---|---|---|
| `d` | **exit 0** | **0.99.0** | Clean upgrade. `gflow.exe` was not running, so pip updated all binaries without conflict. |

### 5. Pip Upgrade Execution Order (`venv e`, `pipdry`)

Verbose log inspection of `python -m pip install -v --upgrade gflow-cli`:

```
Installing collected packages: gflow-cli
  Attempting uninstall: gflow-cli
    Found existing installation: gflow-cli 0.98.99
    Uninstalling gflow-cli-0.98.99:
      Removing file or directory ...\lib\site-packages\gflow_cli-0.98.99.dist-info\
      Removing file or directory ...\lib\site-packages\gflow_cli\
      Removing file or directory ...\scripts\flow.exe
      Removing file or directory ...\scripts\gflow-cli.exe
      Removing file or directory ...\scripts\gflow.exe  <--- FAILS HERE WITH WinError 32 IF RUNNING
Successfully uninstalled gflow-cli-0.98.99
Successfully installed gflow-cli-0.99.0
```

### 6. Volume Alignment and TMP/TEMP Test (`venv a` & `venv f`)

| Item | Path | Drive / Volume |
|---|---|---|
| `tempfile.gettempdir()` | `C:\Users\ffrol\AppData\Local\Temp` | `C:` |
| `venv a` | `C:\development\github\gflow-cli\.worktrees\1298\scripts\dev\_spike_out\1298\a` | `C:` |
| Volume identity | Identical volume (`C:`) | Both paths share volume |

Running `reproduce` in fresh `venv f` with `TMP` and `TEMP` explicitly pointed to `_spike_out/1298/f/custom_tmp` (inside the venv tree):

| Venv | update Exit | WinError 32 | Import Probe | Status |
|---|---|---|---|---|
| `f` (`--tmp-dir custom_tmp`) | **exit 11** | **True** | **FAILED**: `ModuleNotFoundError` | Identical failure |

**Conclusion:** The error has nothing to do with temporary directory locations or cross-volume move restrictions. It is purely due to the file lock on `Scripts\gflow.exe`.

---

## Conclusions & Root Cause

### Root Cause Location
- `src/gflow_cli/update_check.py:227`:
  ```python
  return Installer("pip", (sys.executable, "-m", "pip", "install", "--upgrade", "gflow-cli"))
  ```
- `src/gflow_cli/update_check.py:405-412`:
  ```python
  res = _run_command(inst.command, capture_output=not stream_output)
  ```

### Why pip Leaves the Venv Broken While uv Does Not
1. **`uv tool upgrade`**:
   `uv` installs wheels transactionally / staging-first. It unpacks the new package and libraries into `site-packages` *before* updating console script entrypoints. When it fails to overwrite `gflow.exe` due to Windows file sharing (`os error 32`), the updated Python package is already present in `site-packages`. The pre-existing launcher executable points to `python.exe`, which successfully imports the newly updated code.
2. **`pip install --upgrade`**:
   Pip follows a strict **"uninstall old version first, then install new version"** sequence. During the uninstall phase, pip deletes `site-packages/gflow_cli/` and `.dist-info/` before attempting to delete `Scripts/gflow.exe`. When deleting `Scripts/gflow.exe` hits `[WinError 32]` because the launcher process is running, pip terminates with an `OSError`. Crucially, pip's uninstall rollback mechanism does not restore the deleted `site-packages/gflow_cli/` directory. The virtual environment is left stripped of the application modules, causing `import gflow_cli` to fail with `ModuleNotFoundError` (detected by `_import_failure()` at `update_check.py:421`).

---

## Fix Options Evaluated

| Fix Option | Viability | Evidence |
|---|---|---|
| **Option 1: Rename `gflow.exe` aside before running pip** | **SUPPORTED (root-cause fix)** | Section 7: after `importlib.metadata.FastPath.__new__.cache_clear()` + `gc.collect()` the gflow process renames its own running launcher. Another gflow process (for example `gflow mcp run`) still holds it, so the rename doubles as a pre-flight: if it fails, refuse BEFORE pip runs and nothing has changed. |
| **Option 2: Detached helper process** | **SUPPORTED** | A lightweight detached background process (e.g. `python -c "import time, subprocess, sys; time.sleep(1); subprocess.run([...])"` or spawned with `CREATE_NEW_PROCESS_GROUP`/`DETACHED_PROCESS`) that waits for `gflow.exe` (PID) to terminate before executing `pip install --upgrade`. Once `gflow.exe` exits, `Scripts\gflow.exe` is unlocked and pip succeeds. |
| **Option 3: Refuse `gflow update` with a remediation hint when under `pip` on Windows** | **REJECTED as the fix** | Message-only: it reports the problem instead of removing it. Its suggested `python -m gflow_cli update` also strands the venv while another gflow process holds the launcher (section 7, run B). A refusal survives only as the fallback when the rename aside fails, before pip runs. Sections 7 and 9 are authoritative. |
| **Option 4: Correct documentation (`docs/USAGE.md`)** | **REQUIRED** | `docs/USAGE.md` currently claims the launcher lock on Windows is always benign ("the upgrade lands anyway"). This statement is only true for `uv tool`, but dangerously false for `pip`. |

---

## 7. Coordinator controls (2026-11-15)

The `locks b` / `renamed c` runs left no log in `_spike_out/1298`, and their claim decides the fix, so they
were re-measured with controls.

| Run | Setup | Result |
|---|---|---|
| A: bare pip launcher | `sleeper.exe` (a pip console script that sleeps 30 s), alive | `open(r+b)` and `remove` refused (WinError 5); **`os.rename` aside OK**; a new file at the original path OK; the `.old` is deletable only after it exits |
| B: real `gflow.exe` held by another process | a live `gflow.exe mcp run`, then rename from outside | **rename refused, WinError 32**; `pip install --upgrade` then exits 1 and `import gflow_cli` fails (the bug) |
| C: gflow renames its own launcher | `sitecustomize` probe in a 0.98.99 venv, `gflow.exe --version` | at startup (before `__main__`): rename OK. At exit: `sys.path[0]` is `...\Scripts\gflow.exe`, `__main__.__loader__` is `zipimporter`, rename refused (WinError 32). Clearing zipimport caches: still refused |
| D: release the hold | as C, then `importlib.metadata.FastPath.__new__.cache_clear()` + `gc.collect()` | `FastPath` cache had 10 entries; **after clearing, the self-rename succeeds** |

**Conclusion.** The launcher is held by `importlib.metadata`'s cached `zipfile.Path` on `sys.path[0]` (the
zip-launched `gflow.exe`), in every gflow process. The updating process can drop its own hold and rename the
launchers aside. A second live gflow process cannot be released from here, so a failed rename means: stop,
change nothing, tell the user which processes to close. pip only runs once every launcher path is free, so
its uninstall-first order can no longer strand the venv.


## 8. pipx and uv tool (2026-11-15)

Tested on Windows 11 (Python 3.13.3, pipx 1.18.16, uv 0.8.16) in clean isolated directories under `scripts/dev/_spike_out/1298/installers/`. Baseline wheel: lowered `0.98.99`; PyPI holds `0.99.0`.

### Installer Layout & Launcher Semantics

| Installer | Runnable Launcher (`gflow.exe`) | Size | Link Type | `sys.prefix` | Metadata File in Prefix |
|---|---|---|---|---|---|
| **pipx** | `PIPX_BIN_DIR\gflow.exe` | 108,427 B | File copy (`nlink=1`) | `pipx_home\venvs\gflow-cli` | `pipx_metadata.json` (9,466 B) |
| **uv tool** | `UV_TOOL_BIN_DIR\gflow.exe` | 41,615 B | File copy (`nlink=1`) | `uv_tools\gflow-cli` | `uv-receipt.toml` (352 B) |

Both package managers place an independent file copy (trampoline executable) in their respective bin directories rather than a symlink or hardlink (`os.path.samefile` is False, `is_symlink` is False).

### Experimental Results

| Installer | Scenario | `gflow update` Exit | Manager Return Code | Usable Afterwards? | Error Line / Failure Point |
|---|---|---|---|---|---|
| **pipx** | Literal wheel (`pipx install <wheel>`) | **exit 11** | N/A (not run) | Yes (still 0.98.99) | `ConfigurationError: This gflow-cli is not a PyPI install` (`direct_url.json` present) |
| **pipx** | Case A: index-simulated (uncontested) | **exit 0** | 0 | **Yes (0.99.0)** | None (`pipx` emits warning and skips overwriting `PIPX_BIN_DIR\gflow.exe`) |
| **pipx** | Case B: index-simulated + child `mcp run` | **exit 0** | 0 | **Yes (0.99.0)** | None (`pipx` emits warning and skips overwriting `PIPX_BIN_DIR\gflow.exe`) |
| **uv tool** | Literal wheel (`uv tool install <wheel>`) | **exit 11** | N/A (not run) | Yes (still 0.98.99) | `ConfigurationError: This gflow-cli is not a PyPI install` (`direct_url.json` present) |
| **uv tool** | Case A: index-simulated (uncontested) | **exit 0** | 1 | **Yes (0.99.0)** | `os error 32` copying to `UV_TOOL_BIN_DIR\gflow.exe` (caught by `gflow update`) |
| **uv tool** | Case B: index-simulated + child `mcp run` | **exit 0** | 1 | **Yes (0.99.0)** | `os error 32` copying to `UV_TOOL_BIN_DIR\gflow.exe` (caught by `gflow update`) |

### Detailed Findings

#### 1. Literal Wheel vs Index Detection (`_installed_from_index`)
When installed directly via `pipx install <wheel_path>` or `uv tool install <wheel_path>`, PEP 610 `direct_url.json` is created in `.dist-info`. Consequently, `_installed_from_index()` returns `False`, and `gflow update` refuses to proceed, raising exit 11:
```
Configuration error: This gflow-cli is not a PyPI install (editable, local-path, VCS or source run)
-> Update it the way it was installed — `git pull` / reinstall from the checkout. `gflow update` only manages index installs (uv tool / pipx / pip).
```
Neither `pipx upgrade` nor `uv tool upgrade` is invoked. Removing `direct_url.json` and tracking the package spec `gflow-cli` restores normal index-installed behavior.

#### 2. pipx Upgrade Mechanics
From `pipx upgrade --verbose gflow-cli`:
- **Uninstall order**: pip runs inside the pipx venv and uninstalls the old package first:
  ```
  Attempting uninstall: gflow-cli
    Found existing installation: gflow-cli 0.98.99
    Uninstalling gflow-cli-0.98.99:
      Successfully uninstalled gflow-cli-0.98.99
  Successfully installed gflow-cli-0.99.0
  ```
- **Why pip does not hit `WinError 32` in the venv**: The running updater is `PIPX_BIN_DIR\gflow.exe`, which invokes `venv\Scripts\python.exe` directly. The internal launcher `venv\Scripts\gflow.exe` is **not running** and therefore not locked.
- **Launcher copy behavior**: After upgrading the venv, pipx attempts to update `PIPX_BIN_DIR\gflow.exe`. Detecting that the file already exists and differs, pipx intentionally refrains from modifying it:
  ```
  ⚠️  File exists at ...\pipx_bin\gflow.exe and does not match ...\pipx_home\venvs\gflow-cli\Scripts\gflow.exe. Not modifying.
  upgraded package gflow-cli from 0.98.99 to 0.99.0
  ```
  Because pipx refuses to overwrite the existing binary, it never encounters `WinError 32`. The existing trampoline points to the venv's python and seamlessly executes 0.99.0.

#### 3. uv tool Upgrade Mechanics
- **Staging-first install**: `uv tool upgrade` cleanly unpacks `gflow-cli==0.99.0` into the venv `site-packages`.
- **Launcher copy failure (`os error 32`)**: When `uv` attempts to install entrypoints into `UV_TOOL_BIN_DIR`, it fails to overwrite the running executable:
  ```
  error: Failed to upgrade gflow-cli
    Caused by: Failed to install entrypoint
    Caused by: failed to copy file from C:\...\installers\uv_tools\gflow-cli\Scripts\gflow.exe to C:\...\installers\uv_bin\gflow.exe: The process cannot access the file because it is being used by another process. (os error 32)
  ```
  **Path named:** `C:\development\github\gflow-cli\.worktrees\1298\scripts\dev\_spike_out\1298\installers\uv_bin\gflow.exe`
- **Tolerated by `gflow update`**: Because the venv was already upgraded to 0.99.0, `update_check.py` recognizes that `after == "0.99.0"` and `_import_failure()` is `None`. It prints an informational note and exits **0**, leaving the environment fully functional.

#### 4. Summary & Comparison with Bare pip
- **pip venv**: Broken and unimportable (exit 11, `ModuleNotFoundError: No module named 'gflow_cli'`). pip deletes `site-packages/gflow_cli/`, then fails on running `Scripts\gflow.exe` with `[WinError 32]`, aborting without rollback.
- **pipx**: Fully functional (exit 0). pip uninstalls in venv without conflict because `venv\Scripts\gflow.exe` is not running, and pipx skips overwriting `PIPX_BIN_DIR\gflow.exe` with a non-fatal warning.
- **uv tool**: Fully functional (exit 0). uv installs to `site-packages` before updating entrypoints; `os error 32` on `uv_bin\gflow.exe` fails the entrypoint copy, but `gflow update` handles the non-zero manager exit and the existing launcher runs the upgraded code.


## 9. The planned fix, end to end inside a real gflow.exe (2026-11-15)

Section 7 proved the self-rename; nothing had yet shown rename aside → pip → a working venv in one
process. A one-shot `sitecustomize` probe in a fresh 0.98.99 venv (`sv2`) ran, at the exit of a real
`Scripts\gflow.exe --version`, exactly the sequence the fix will run:

1. launchers read from RECORD (`distribution("gflow-cli").files`, `.exe` entries, `.locate()`);
2. `importlib.invalidate_caches()` + `gc.collect()`;
3. `os.replace` each to `<name>.gflow-update-<pid>.old`;
4. `python -m pip install --upgrade gflow-cli` (PyPI holds 0.99.0), no metadata call in between;
5. restore any launcher whose original path is missing; then a fresh-interpreter import check.

| Step | Result |
|---|---|
| launchers found | `flow.exe`, `gflow-cli.exe`, `gflow.exe` (three, not one: every console script) |
| rename aside | all three OK |
| pip | **exit 0**: `Successfully uninstalled gflow-cli-0.98.99`, `Successfully installed gflow-cli-0.99.0` |
| launchers after pip | all three re-created by pip, so nothing was restored |
| `python -I -c "import gflow_cli.cli"` | rc 0, `0.99.0` |
| the process's own exit | rc 0 |
| new `gflow.exe --version` | `gflow, version 0.99.0` |
| the three `.old` files | deleted once the process had exited |

**Method note (a defect in the first attempt, not in the product).** The first probe gated its hook on
an environment variable and left it set, so the pip child inherited it, re-ran the hook at its own exit
and started another pip: a chain of pip processes until the hook file was deleted and every PID it had
started was stopped. The probe now pops the variable and deletes itself first. The real fix has no such
hook, but the lesson stands for any `sitecustomize` probe: a child inherits the environment.

## Not Measured

- `pip install --user` on Windows: user-site script directory installations were not tested.
- POSIX platforms: Unix-like systems unlink open files via inode reference semantics, so this issue is Windows-specific by design.
