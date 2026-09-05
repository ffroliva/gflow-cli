# Scenario: `gflow update` — self-update command

Feature: a `gflow update` command that upgrades an index-installed gflow-cli in
place (`uv tool` / `pipx` / plain `pip`), plus a `--check` mode that reports
installed vs latest without touching anything. The once-a-day stderr notice
(#479, v0.56.0) already exists; it is re-pointed at `gflow update`.

## Coverage map

Active: **D7** (exit codes), **D8** (cross-platform — Windows in-use executable,
venv paths), **D11** (input / boundary — install source, missing binaries),
**D12** (log events), **D13** (MCP parity — exemption).

Skipped: D1/D2/D3/D9/D10 — no Flow transport, no browser, no auth touched.
D4/D5 — no batch, no page pool. D6 — no DataStore access; the only file written
is the existing `update_check.json` cache.

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D11 | Editable / local / VCS install (`direct_url.json` present) | Critical | `ConfigurationError` exit 11, remediation says to reinstall from the checkout; **no subprocess spawned** — `pip install -U` would silently replace a deliberate install | Unit |
| 2 | D11 | No `gflow-cli` distribution at all (source run) | High | Same as #1 — exit 11, no subprocess | Unit |
| 3 | D8 | Installed via `uv tool` (`uv-receipt.toml` in `sys.prefix`) | High | Runs `uv tool upgrade gflow-cli` | Unit |
| 4 | D8 | Installed via `pipx` (`pipx_metadata.json` in `sys.prefix`) | High | Runs `pipx upgrade gflow-cli` | Unit |
| 5 | D8 | Plain venv / pip install (neither marker) | High | Runs `<sys.executable> -m pip install --upgrade gflow-cli` — the venv's own interpreter, never a bare `pip` from PATH | Unit |
| 6 | D11 | `uv` / `pipx` marker present but the binary is not on PATH | High | Exit 11 with the exact command to run manually; no subprocess | Unit |
| 7 | D7 | Package manager exits non-zero | High | Exit 11, detail carries the manager's return code and the command; `--json` emits the Problem Details envelope | Unit |
| 8 | D11 | Already on the latest version | Medium | Prints "up to date", exit 0, **no subprocess** (no wasted reinstall) | Unit |
| 9 | D11 | PyPI unreachable / bad JSON | Medium | Manager still runs (it is authoritative); `--check` reports latest unknown, exit 0 | Unit |
| 10 | D11 | `--check` | Medium | Sync PyPI fetch (bypasses the once-a-day cache), refreshes the cache, prints installed/latest/installer/command, never spawns the manager | Unit |
| 11 | D11 | Upgrade bumps `playwright` | Medium | After a successful upgrade the command compares the venv's playwright version before/after and prints the `playwright install chromium` hint only when it changed | Unit |
| 12 | D8 | Windows: `gflow.exe` is the running process while the manager replaces it | High | `uv tool upgrade` completes (empirically verified in a scratch `UV_TOOL_DIR`); the running process finishes normally | E2E smoke (manual, recorded in PLAN) |
| 13 | D12 | Log contract | Low | `update.installer_detected`, `update.command_finished` events; no new keys leak paths beyond `sys.executable` | Unit |
| 14 | D13 | MCP twin | High | `update` is **exempt** in `tests/mcp/test_cli_parity.py`: an MCP session must not replace its own running server's code | Unit (parity test) |
| 15 | D7 | Notice text | Low | The #479 notice now says "run `gflow update`"; still stderr-only, still gated by `GFLOW_CLI_UPDATE_CHECK` / CI / index-install | Unit |

## Must-cover before merge (Critical + High)

1. Source installs refused, exit 11, no subprocess (#1, #2).
2. Installer detection for uv / pipx / pip from `sys.prefix` markers (#3–#5).
3. Missing manager binary → exit 11 with manual command (#6).
4. Manager failure → exit 11, JSON envelope (#7).
5. Windows in-use executable verified empirically with `uv tool` (#12).
6. Parity exemption (#14).

## Deferred (Medium + Low)

- `--check` non-zero exit when an update is available (scripting convenience) — `--json` covers it.
- `conda` / Homebrew / distro packages — none publish gflow-cli today.

## Suggested BDD scenarios

```gherkin
Feature: gflow update
  Scenario: editable install is refused
    Given gflow-cli was installed with `pip install -e .`
    When the user runs `gflow update`
    Then it exits 11 and no package manager is invoked

  Scenario: uv tool install upgrades in place
    Given gflow-cli was installed with `uv tool install gflow-cli`
    And PyPI publishes a newer version
    When the user runs `gflow update`
    Then `uv tool upgrade gflow-cli` runs and the command exits 0

  Scenario: already current
    Given the installed version equals the PyPI version
    When the user runs `gflow update`
    Then it prints "up to date" and no package manager is invoked
```

## Known-issues cross-reference

None. `KNOWN_ISSUES.md` has no update/installer entry.
