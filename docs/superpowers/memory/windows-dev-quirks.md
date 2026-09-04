---
name: windows-dev-quirks
description: "Windows-specific dev environment quirks for gflow-cli — uv pytest broken, harness background broken, paths normalized — with the workarounds used 2026-05-24."
---

**Rule:** On this Windows machine, prefer `.venv/Scripts/python.exe -m <tool>` over `uv run <tool>` for any tool that's a console-script entry point (like pytest). Use detached `nohup ... &` for long-running background commands instead of the harness's `run_in_background=true`.

**Why:** Hit both 2026-05-24 while running the locale-agnostic e2e verification:

1. **`uv run pytest` fails with "Failed to canonicalize script path"** on Windows — every invocation, even `uv run pytest --version`. But `uv run python -c "..."`, `uv run ruff`, and `uv run pyright` all work. Some `uv`-vs-pytest console-script interaction. Workaround: `.venv/Scripts/python.exe -m pytest ...` — bypasses uv's script-shim, works perfectly. Same `[tool.pytest]` config from `pyproject.toml` is loaded.

2. **⚠️ CORRECTED 2026-06-12: `run_in_background=true` now WORKS.** Used it repeatedly this session (spike runs + scoped pytest, all completed + notified cleanly). The 2026-05-24 breakage below is stale — try `run_in_background=true` first now; only fall back to the `ctx_execute` + `nohup` pattern if it actually fails. ~~Bash/PowerShell tool `run_in_background=true` is broken on Windows~~ — (historical) every attempt returned "Failed to canonicalize script path" before the shell started. Was confirmed across:
   - `Bash` tool with inline `VAR=val cmd` (POSIX) → fails
   - `Bash` tool with `bash -c '...'` → fails
   - `Bash` tool calling a `.sh` wrapper → fails
   - `PowerShell` tool with `$env:VAR = "..."; cmd` → fails

   Workaround that actually works: `ctx_execute(language="shell", code="nohup ... > log 2>&1 & disown")` — uses the MCP server's subprocess facility instead of the harness's broken background. Tail the log file with `Read` or `ctx_execute` as needed.

3. **`Bash` tool rewrites `/foo/bar/baz` paths** — `gh api /repos/...` gets mangled to `C:/Program Files/Git/repos/...`. Workaround: drop the leading slash in `gh api` calls (`gh api repos/...`).

4. **Windows-style absolute paths in `Write` work** — `C:\Users\<you>\AppData\Local\Temp\file.md` (or forward-slash variant) is fine for `Write` / `Read` / `gh --body-file`. `/tmp/file` does NOT exist on Windows.

5. **`TMPDIR=$PWD` set by the msys2-style shell** — `tempfile.gettempdir()` returns the current working directory because Python checks `TMPDIR` first (POSIX convention) before `TMP`/`TEMP`. Side effects: pytest's `tmp_path` lands in CWD/`pytest-of-<user>/` instead of `%TEMP%`. Fixed via `pyproject.toml addopts = "--basetemp=tmp/pytest"` (cleanup PR 2026-05-26). For other tools, set `TMPDIR=` (empty) or use absolute paths.

6. **Stray `nul` file breaks `git add -A`** (2026-06-12) — a Windows reserved device name (`nul`) created on disk (e.g. a stray `> nul` redirect) makes `git add -A` abort with `error: nul: failed to insert into database` / `short read while indexing nul`. Fix: `rm -f nul` (git-bash deletes it; Explorer/`del` can't), then `git add <explicit files>`. Watch for it after commands that redirect to `nul`.

**How to apply:**

- Before any `uv run pytest` call, prefer `.venv/Scripts/python.exe -m pytest`.
- For long-running tests / live e2e runs (which can take 10+ min and exceed the 600s foreground timeout), launch via `ctx_execute(language="shell", code="nohup bash wrapper.sh > tmp/log 2>&1 &")` then poll the log file. Don't waste cycles trying `run_in_background=true` first.
- For `gh api` paths: no leading slash.
- For temp files: `C:/Users/<you>/AppData/Local/Temp/`, not `/tmp/`.

**Edge case:** if a future session sees `uv run pytest` work, drop the workaround (and probably this memory). It's an environment quirk, not a permanent rule.

**Related:** [[real-browser-auth-mandatory]] (the Chrome-strategy profile + `channel="chrome"` requirement that adds to the Windows-specific Playwright pain), [[full-test-suite-ooms]] (different but related local-vs-CI testing constraint), [[background-e2e-pytest-pattern]] (the working e2e backgrounding pattern that uses ctx_execute + nohup + Monitor).
