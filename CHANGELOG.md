# Changelog

All notable changes to `flow-cli` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial repo scaffold: pyproject (uv + hatchling), Click-based CLI, Rich console output.
- `Provider` protocol for swappable backends (Flow now, official Veo 3.1 SDK later).
- `FlowProvider` skeleton with stubbed methods + captured route documentation.
- `auth login` / `auth status` commands using Playwright persistent context.
- CLI commands: `upload`, `generate`, `status`, `download`, `i2v` (stubbed pending route wiring).
- Smoke tests covering imports + `--help` exit code.
- GitHub Actions CI: ruff, pyright, pytest on Python 3.11 and 3.12.
- GitHub Actions release workflow: tag-triggered PyPI publish via Trusted Publishing.
- MIT license, comprehensive README with architecture diagram and release policy.

## [0.1.0] — _unreleased_

First skeleton. Not functional end-to-end yet.

[Unreleased]: https://github.com/ffroliva/flow-cli/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ffroliva/flow-cli/releases/tag/v0.1.0
