# Changelog

All notable changes to `flow-cli` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`CLAUDE.md`** at repo root — project memory hub for AI coding agents
  (Claude Code reads natively; Cursor/Codex/Gemini/Aider can read as
  reference). Covers project purpose, on-session-start ritual, active phase
  pointer, architecture skim, critical rules (no secrets, no AI co-authors,
  pure domain layer, TDD discipline), coding conventions, quality gates,
  where-to-look table, common tasks, things-not-to-do list.
- **`.claude/`** directory — repo-local Claude Code surface for maintainers.
  Distinct from the published end-user skill at `skills/flow-cli/`.
  - `.claude/README.md` — what goes here, how to extend.
  - `.claude/commands/release.md` — `/release` slash command that automates
    version bump + CHANGELOG migration + tag + push, with quality gates and
    "no AI co-author" reminders.
- `flow_cli.profile_store` — profile inventory + default-profile persistence
  in `$FLOW_CLI_HOME/config.toml`. Five-step resolution chain (CLI flag > env >
  config > auto-select > raise) with named exceptions
  (`NoProfilesError`, `NoDefaultProfileError`).
- New auth subcommands:
  - bare `gflow auth` — shows profile inventory table; auto-launches `login`
    when no profiles exist.
  - `gflow auth list` — same table as bare command (no auto-login fallback).
  - `gflow auth use <name>` — sets the default profile, persisted to
    `config.toml`.
  - `gflow auth logout [--profile NAME] [-y]` — deletes a profile's session.
  - First successful `auth login` auto-sets the new profile as default so
    single-account users never see "no default" friction.
- `KNOWN_ISSUES.md` at repo root — open/mitigated/resolved issues with
  workarounds. First entry: browser session expiry & re-login.
- `docs/` tree (INDEX, AUTHENTICATION, CONFIGURATION, ARCHITECTURE, USAGE,
  SECURITY) for deep-dive docs that don't belong in the README.
- `.env.template` documenting every supported env var.
- Tests: `tests/test_profile_store.py` covers list, set/clear/get default,
  auto-select, full resolution chain precedence, and delete (incl. clearing
  the default when the deleted profile was it).
- Initial repo scaffold: pyproject (uv + hatchling), Click-based CLI, Rich console output.
- `Provider` protocol for swappable backends (Flow now, official Veo 3.1 SDK later).
- `FlowProvider` skeleton with stubbed methods + captured route documentation.
- `auth login` / `auth status` commands using Playwright persistent context.
- CLI commands: `upload`, `generate`, `status`, `download`, `i2v` (stubbed pending route wiring).
- Smoke tests covering imports + `--help` exit code.
- Red-light TDD tests for every Provider method (under `tests/providers/`) — they pin the contract before implementation lands.
- Tests for `models` (frozen dataclasses, JobStatus enum) and `auth` helpers (no Playwright, no network).
- GitHub Actions CI: ruff, pyright, pytest on Python 3.11 and 3.12.
- GitHub Actions release workflow: tag-triggered PyPI publish via Trusted Publishing.
- MIT license, comprehensive README with badges (CI, version, downloads, stars), architecture diagram, install paths (uv, uvx, source), stack docs, TDD workflow, release policy.
- [`DISCLAIMER.md`](DISCLAIMER.md) — full unaffiliated/use-at-own-risk legal scope, takedown policy.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — TDD discipline, test categories, coverage targets, commit conventions.
- [`skills/flow-cli/SKILL.md`](skills/flow-cli/SKILL.md) — installable Claude Code Skill with frontmatter + agent recipes, also usable as a generic reference doc by Cursor / Codex / Gemini CLI / Aider.
- README "Stats" section at the bottom: stars, forks, watchers, issues, last commit, repo size, PyPI downloads (monthly + total via pepy.tech).

## [0.1.0] — _unreleased_

First skeleton. Not functional end-to-end yet.

[Unreleased]: https://github.com/ffroliva/flow-cli/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ffroliva/flow-cli/releases/tag/v0.1.0
