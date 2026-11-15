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
Windows NT enforces a strict image lock on running `.exe` files. A running launcher executable **cannot be renamed, replaced, or removed**, even within the same directory on NTFS. In contrast, loaded C-extension `.pyd` libraries **can** be renamed or replaced aside on the same filesystem volume.

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
| **Option 1: Rename `gflow.exe` aside before running pip** | **KILLED** | Windows NT image section lock forbids `os.rename` and `os.replace` on running `.exe` (`WinError 32`). Measured in `locks b` and `renamed c`. |
| **Option 2: Detached helper process** | **SUPPORTED** | A lightweight detached background process (e.g. `python -c "import time, subprocess, sys; time.sleep(1); subprocess.run([...])"` or spawned with `CREATE_NEW_PROCESS_GROUP`/`DETACHED_PROCESS`) that waits for `gflow.exe` (PID) to terminate before executing `pip install --upgrade`. Once `gflow.exe` exits, `Scripts\gflow.exe` is unlocked and pip succeeds. |
| **Option 3: Refuse `gflow update` with a remediation hint when under `pip` on Windows** | **SUPPORTED (Simplest & Safest)** | If `sys.platform == "win32"` and `inst.name == "pip"` and invoked via `gflow.exe`: raise `ConfigurationError` (exit 11) *before* invoking pip, instructing the user: `"On Windows, 'gflow update' cannot replace its own running launcher in a pip venv. Run 'python -m gflow_cli update' or 'python -m pip install -U gflow-cli' instead."` As proven by `pymodule d`, `python -m gflow_cli update` works cleanly without corrupting the environment. |
| **Option 4: Correct documentation (`docs/USAGE.md`)** | **REQUIRED** | `docs/USAGE.md` currently claims the launcher lock on Windows is always benign ("the upgrade lands anyway"). This statement is only true for `uv tool`, but dangerously false for `pip`. |

---

## Not Measured

- `pipx` installs on Windows: `pipx` is not installed on this test machine (`pipx` uses isolated venvs and may invoke pip similarly; behavior under `pipx upgrade` remains unmeasured).
- `pip install --user` on Windows: user-site script directory installations were not tested.
- POSIX platforms: Unix-like systems unlink open files via inode reference semantics, so this issue is Windows-specific by design.
