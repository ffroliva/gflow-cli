# Implementation Plan: Auth Login via Real Chrome (`v0.6.0a2`)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkpoint protocol.

## Phase 1: Test Scaffold (RED)
Goal: Define the contract and verify failure for all new surfaces.

- [ ] **T1.1: Auth Strategy Unit Tests** (`tests/auth/strategies/test_factory.py`)
  - Verify `AuthStrategyFactory` returns `RealChromeStrategy` when available and requested.
  - Verify fallback to `InternalChromiumStrategy` when Chrome is missing in `auto` mode.
  - Verify `ConfigurationError` when `chrome` is requested but missing.
- [ ] **T1.2: BDD Scenarios** (`tests/features/auth_login.feature`)
  - Add scenarios for `--browser chrome`, `--browser internal`, and `auto` selection.
- [ ] **T1.3: Strategy Protocol** (`src/gflow_cli/auth/base.py`)
  - Define `AuthStrategy` protocol with `login(profile_dir: Path, headless: bool) -> None`.
- [ ] **T1.4: Concrete Strategy Tests** (`tests/auth/strategies/test_strategies.py`)
  - Scaffold tests for `RealChromeStrategy` (stealth flags, success polling).
  - Scaffold tests for `InternalChromiumStrategy`.
- [ ] **T1.5: Privacy Guard Tests** (`tests/auth/strategies/test_strategies.py`)
  - Verify `RealChromeStrategy` raises a security exception if `user_data_dir` is outside the allowed `GFLOW_CLI_HOME` boundaries.

## Phase 2: Implementation (GREEN)
Goal: Feature-complete implementation via atomic commits.

- [ ] **T2.1: Package Promotion** (`src/gflow_cli/auth.py` -> `src/gflow_cli/auth/__init__.py`)
  - Convert the module to a package.
  - Re-export legacy functions (`profile_dir`, `status`, `default_profile_root`) to ensure zero breakage for `cli.py`, `profile_store.py`, and `_cli_helpers.py`.
- [ ] **T2.2: InternalChromiumStrategy** (`src/gflow_cli/auth/internal_chromium.py`)
  - Migrate legacy logic from `auth.py`.
- [ ] **T2.3: RealChromeStrategy** (`src/gflow_cli/auth/real_chrome.py`)
  - Implement with **Passive Capture**: launch via `subprocess.Popen` without any debugging flags.
  - Logic: Block on `proc.wait()`, prompting user to log in and CLOSE the browser.
  - Implement Privacy Guard (validate `user_data_dir`).
  - Add post-close verification: headless probe for `SAPISID` cookie.
- [ ] **T2.4: Factory & CLI Integration** (`src/gflow_cli/auth/factory.py` + `src/gflow_cli/cli.py`)
  - Implement lazy registry and `get_strategy`.
  - Update `gflow auth login` to parse `--browser` flag and `GFLOW_CLI_AUTH_BROWSER` env var, and dispatch via factory.
- [ ] **T2.5: Session Verification**
  - Implement the headless verification probe in `RealChromeStrategy`.

## Phase 3: Documentation & Knowledge Base (First-Class Citizen)
Goal: Ensure all documentation, architecture specs, and agent memory reflect the new strategy.

- [ ] **T3.1: Architecture Documentation** (`docs/ARCHITECTURE.md`)
  - Document the new `AuthStrategy` pattern, detection logic, and the "Real Chrome" bypass mechanism.
- [ ] **T3.2: User Guidance** (`docs/AUTHENTICATION.md` & `docs/USAGE.md`)
  - Rewrite Authentication flows to highlight the new Real Chrome default.
  - Document the `--browser` flag and env var overrides.
- [ ] **T3.3: Changelog & Issues** (`CHANGELOG.md` & `KNOWN_ISSUES.md`)
  - Add `v0.6.0a2` section highlighting the G12 fix.
  - Move G12 from "open investigation" to "fixed" in Known Issues.
- [ ] **T3.4: MemPalace & Knowledge Graph Updates**
  - Use `mempalace_kg_add` to record the architectural shift (Strategy pattern for auth).
  - Use `mempalace_diary_write` to log the G12 bypass methodology (stealth + real chrome).

## Phase 4: Council Review, Verification & Release
Goal: Final audit and release ceremony.

- [ ] **T4.1: Implementation Council Review**
  - Invoke `code-reviewer` and `codebase_investigator` for a final implementation audit.
- [ ] **T4.2: Manual Smoke-Test Verification**
  - Execute `uv run gflow auth login --browser chrome` on the operator's machine.
  - Verify G12 bypass and successful session capture empirically.
- [ ] **T4.3: Release Ceremony**
  - Bump version to `0.6.0a2` in `pyproject.toml`.
  - Tag `v0.6.0a2` and push to trigger PyPI release.

---
_End of Plan._
