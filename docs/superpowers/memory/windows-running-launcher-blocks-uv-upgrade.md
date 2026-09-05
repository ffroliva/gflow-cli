---
name: windows-running-launcher-blocks-uv-upgrade
description: "On Windows a running uv-trampoline gflow.exe can be neither overwritten nor renamed; `uv tool upgrade` installs the wheel then exits 1 copying the launcher. Read the venv's installed version, never the manager's exit code. Measured 2026-09-05 for `gflow update`."
---

**Fact (measured 2026-09-05, Windows 11, uv 0.8.x, scratch `UV_TOOL_DIR`):** when
`gflow update` runs `uv tool upgrade gflow-cli` from the very `gflow.exe` it is about to
replace, uv installs the new wheel into the tool venv and *then* exits 1:

```
Updated gflow-cli v0.60.1 -> v0.67.0
error: Failed to upgrade gflow-cli
  Caused by: Failed to install entrypoint
  Caused by: failed to copy file ... to ...\bin\gflow.exe: The process cannot access the
  file because it is being used by another process. (os error 32)
```

The venv IS upgraded (`gflow --version` through the old launcher reports the new version),
because a uv trampoline only points at the venv's python. `pipx upgrade` on the same
machine exited 0 and refreshed its launcher.

**Why it matters for review:**

1. **The manager's exit code is not the outcome.** `update_check.run_update` re-reads
   `importlib.metadata.version("gflow-cli")` from a fresh interpreter after the run and
   reports *that*: non-zero exit with a moved version is an upgrade with a note; zero exit
   with an unmoved version is exit 11. A review that asks "why not just check the return
   code?" has the answer here.
2. **The launcher cannot be renamed aside either.** The trampoline holds its own file open:
   `os.replace` and `mv` on a running `gflow.exe` both fail (error 32 / "Device or resource
   busy", measured against `gflow serve`). A rename-then-restore design was built, measured,
   and deleted — do not propose it again.
3. **`uv tool upgrade` honours an install-time pin silently.** A receipt with
   `specifier = "==x.y.z"` makes the upgrade a no-op with exit 0. Real
   `uv tool install gflow-cli` writes no specifier, so users are unaffected, but tests that
   install a pinned local wheel must unpin the receipt first.
4. **uv's wheel cache is keyed by filename.** A rebuilt wheel with the same version is
   silently replaced by the cached first build until `uv cache clean gflow-cli` — bump the
   version between test builds.

**Not measured:** plain-venv `pip` on Windows (pip removes `Scripts/gflow.exe` — likely the
same lock), macOS / Linux for any manager (POSIX allows replacing a running binary, so the
manager should exit 0 there).

**Related:** [[windows-dev-quirks]] (the wider Windows environment list),
[[pr-must-verify-on-affected-surface]] (why the uv and pipx legs were run for real rather
than mocked), [[wheel-build-sanity-gate]] (the release-side wheel checks this test reused).
