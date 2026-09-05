# gflow update Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature gflow-update` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** `gflow update` upgrades an index-installed gflow-cli in place through the
package manager that installed it, `gflow update --check` reports installed vs latest,
and the existing once-a-day notice (#479) points users at the command.

**Architecture:** All logic lives in `update_check.py` (it already owns the PyPI fetch
and the PEP 610 install-source check): a `fetch_latest()` factored out of the cache
refresh, an `installer()` that reads `sys.prefix` markers (`uv-receipt.toml` → uv,
`pipx_metadata.json` → pipx, else the venv's own `python -m pip`), and `run_update()`.
A thin `cli_update.py` Click command renders text/JSON via `run_with_handlers`. No new
exception class: source installs, missing manager binaries and manager failures are
`ConfigurationError` (exit 11). MCP: exempt — a server must not replace its own code.

**Predict verdict:** not required — no transport, auth, selector or schema change.

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| High | Windows cannot overwrite the running `gflow.exe` | Verify `uv tool upgrade` empirically in a scratch `UV_TOOL_DIR` before merge; record the result in Task 6 |
| High | Replacing a deliberate editable install | PEP 610 `direct_url.json` present → refuse, exit 11, no subprocess |
| Medium | Upgrade bumps Playwright and the browser build is stale | Compare the venv's playwright version before/after; print the install hint only when it changed |
| High | Manager exit code lies about the outcome | Re-read the installed gflow-cli version from a fresh interpreter after the run; the venv is the truth |

---

## File structure

### New files
```
src/gflow_cli/cli_update.py      Click command `gflow update [--check] [--json]`
tests/test_cli_update.py         CliRunner tests for the command surface
```

### Modified files
```
src/gflow_cli/update_check.py    fetch_latest(), installer(), run_update(); notice text
src/gflow_cli/cli.py             register the command
tests/test_update_check.py       installer / run_update unit tests; notice text
tests/mcp/test_cli_parity.py     `update` exemption
docs/USAGE.md, docs/CONFIGURATION.md, README.md, docs/INDEX.md, AGENTS.md,
skills/gflow-cli/SKILL.md, docs/MCP.md, CHANGELOG.md, website/docs (generated)
```

---

## Task 1 — Unit test scaffold (red)
- [x] `tests/test_update_check.py`: `installer()` for uv / pipx / pip markers; source install → None
- [x] `tests/test_update_check.py`: `run_update()` — up to date → no subprocess; newer → subprocess with the detected command; non-zero rc → `ConfigurationError`; missing binary → `ConfigurationError`
- [x] `tests/test_cli_update.py`: `--check` text + JSON; source install exit 11; manager failure exit 11 with JSON envelope
- [x] notice text asserts `gflow update`

## Task 2 — Core implementation
- [x] `fetch_latest(timeout)` factored out of `_refresh_cache`
- [x] `installer()` → `Installer(name, command)` or None
- [x] `run_update(check=...)` → `UpdateReport`
- [x] notice text → "Run `gflow update`"

## Task 3 — CLI surface
- [x] `cli_update.py` + `main.add_command`
- [x] `--help` text; text and `--json` rendering

## Task 4 — MCP surface mirror
- [x] `_MCP_EXEMPT["update"]` with reason (self-replacing the running server)
- [x] `docs/MCP.md` — one line noting the exemption

## Task 5 — Docs
- [x] USAGE.md `## gflow update` + notice paragraph; CONFIGURATION.md; README install block; INDEX.md FAQ; AGENTS.md command surface; skills/gflow-cli/SKILL.md; CHANGELOG Unreleased
- [x] `generate_website_docs.py` regenerated

## Task 6 — Empirical Windows check + gates
- [x] Build a wheel with a lowered version, install it into a scratch `UV_TOOL_DIR` via `--find-links`, run `gflow update` from that install, confirm it lands the PyPI release while `gflow.exe` is the running process. Result recorded below.
- [x] `/gflow:check` green

**Task 6 result (2026-09-05, Windows 11):** see the "Empirical check" section at the end of this file.

---

## Definition of done
- [x] All task steps checked off
- [x] `/gflow:check` green
- [x] `CHANGELOG.md` `[Unreleased]` updated
- [x] Docs updated
- [x] Windows in-use-executable check recorded

## Empirical check

2026-09-05, Windows 11 Pro, uv 0.8.16, pipx 1.8 (`pipx[uv]`), scratch `UV_TOOL_DIR` /
`PIPX_HOME`. A wheel built from this branch with the version lowered to 0.60.1 was installed
through each manager (`--find-links` — no `direct_url.json`, so it counts as an index install),
the install-time pin removed from the receipt / metadata, then `gflow update` run **from that
install's own `gflow.exe`**, upgrading it to the real PyPI 0.67.0.

| Manager | `gflow update` exit | Manager exit | Venv after | Launcher |
|---|---|---|---|---|
| `uv tool` | **0** | 1 — "failed to copy … gflow.exe … used by another process (os error 32)" **after** "Updated gflow-cli v0.60.1 -> v0.67.0" | **0.67.0** (`gflow --version` through the old launcher) | not refreshed; keeps working |
| `pipx` | **0** | 0 — "upgraded package gflow-cli from 0.60.1 to 0.67.0" | **0.67.0** | refreshed |

Findings that shaped the code:

1. **A running uv trampoline can be neither overwritten nor renamed.** `os.replace` and
   `mv` on a running `gflow.exe` both fail with error 32 / "Device or resource busy" —
   measured against `gflow serve`. The first design (rename the launcher aside, restore on
   failure) was therefore deleted: it worked for the idle `flow.exe` and never for the one
   that mattered.
2. **The manager's exit code is not the outcome.** uv had already installed the new wheel
   when it exited 1. `run_update` now re-reads `importlib.metadata.version("gflow-cli")` from
   a fresh interpreter after the run and reports that; a non-zero exit with a moved version
   is an upgrade with a note, a zero exit with an unmoved version is exit 11.
3. **`uv tool upgrade` honours the install-time pin** (`requirements = [{ name = "gflow-cli",
   specifier = "==0.60.1" }]`) — it silently does nothing. A real `uv tool install gflow-cli`
   writes no specifier, so users are unaffected; the receipt was unpinned by hand for the test,
   and the "exit 0, still the same version" branch exists because of it.
4. uv's wheel cache is keyed by filename: a rebuilt `0.60.0` wheel was silently replaced by the
   cached first build until `uv cache clean gflow-cli`. The scratch script bumps the fake version.

Not exercised: a plain-venv `pip` install on Windows (no `gflow.exe` running from a venv on
this machine), macOS / Linux for any manager. Those paths share the same code and the same
venv-is-truth check; they are LIKELY, not CONFIRMED.
