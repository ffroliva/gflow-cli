# Development Workflow

> For _contributing_ (TDD, quality gates, commit style, how to add a route) see [CONTRIBUTING.md](../CONTRIBUTING.md).
> This document covers the **internal development process**: branching model, PR protocol, e2e gate, and release pipeline.
> For scenario-based GitHub PR triage, external fork handling, and SonarCloud
> decisions, see [docs/GITHUB.md](GITHUB.md).

---

## Branching model

```
feature/* ──┐
fix/*       ├──► develop ──► main
docs/*      ┘
chore/*
```

| Branch | Purpose | Merges into |
|---|---|---|
| `main` | Stable releases only. Tagged. Published to PyPI. | — |
| `develop` | Integration branch. E2e tests run here. | `main` (via release PR) |
| `feature/*` | New capabilities. | `develop` |
| `fix/*` | Bug fixes. | `develop` |
| `docs/*` | Documentation only. | `develop` |
| `chore/*` | Deps, CI, tooling, refactor. | `develop` |

**Nothing merges directly to `main`.** All work flows through `develop` first.

---

## Branch naming

```
<type>/<short-kebab-description>
<type>/issue-<N>-<short-kebab-description>   # when tracking a GitHub issue
```

Examples:

```
feature/issue-16-integration-ergonomics
fix/issue-15-sapisidhash-uploadimage-401
docs/dev-workflow-and-branching-protocol
chore/bump-playwright-1-49
```

Types mirror Conventional Commits: `feature`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`.

---

## PR protocol

### Opening a PR

1. Branch off `develop` (not `main`).
2. Use Conventional Commits for all commit messages.
3. Open the PR as a **draft** until all quality gates pass locally.
4. Target `develop` as the base branch.
5. PR title follows Conventional Commits: `feat(api): ...`, `fix(cli): ...`, `docs: ...`.

### Quality gates (all must pass before merge)

```bash
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run python -m pytest -q --cov=gflow_cli
```

CI runs the same five on every push. Do not bypass with `--no-verify`.
Project pytest defaults exclude `e2e` and `live`; run those credit-spending
tests only with an explicit marker expression and profile/env setup.
Local agents running inside an MCP/context sandbox may need to split the
marker-filtered pytest suite by path and omit coverage locally; CI remains the
authoritative coverage gate.

### Merging feature PRs into develop

- Squash merge preferred — keeps `develop` history linear.
- The squash commit message becomes the canonical record; make it clean.
- Delete the feature branch after merge.

### E2e gate before merging develop → main

E2e tests require a live authenticated profile and are **not run in CI** (they spend real Veo/Imagen credits). Before opening a `develop → main` release PR:

```bash
export GFLOW_CLI_E2E_PROFILE=<your-profile-name>
uv run pytest -m e2e -v
```

All `@pytest.mark.e2e` scenarios must pass. See [CONTRIBUTING § E2e tests](../CONTRIBUTING.md#test-categories) for the full marker reference.

---

## Version bump protocol

Version bumps belong in the **`develop → main` release PR**, not in feature PRs. The sequence:

1. All feature PRs for a release batch are merged to `develop`.
2. E2e gate passes on `develop`.
3. Open a release PR: `develop → main`.
4. In that PR, bump `version` in `pyproject.toml` and finalize `CHANGELOG.md [Unreleased]` → `[vX.Y.Z]`.
5. Use the `/release` skill to automate steps 4 + tagging.
6. Merge the release PR to `main` — CI publishes to PyPI automatically.

**Alpha releases** use the form `vX.Y.ZaN` (e.g. `v0.6.0a7`). Ship alphas from `develop → main` to validate with real users before cutting a stable `vX.Y.Z`.

---

## AI-assisted development (Claude Code)

This repo is actively developed with Claude Code (see [CLAUDE.md](../CLAUDE.md)). A few conventions that keep the workflow clean:

- Claude branches are renamed to the appropriate `feature/`, `fix/`, `docs/` prefix before the PR is opened. The `claude/` prefix is internal scaffolding.
- Claude never bumps the version or cuts a release without explicit instruction — use `/release`.
- Claude opens PRs as **drafts** targeting `develop`. The maintainer reviews, runs e2e if needed, and merges.
- Commit messages never carry `Co-Authored-By: Claude` or any AI attribution — author credit is the human maintainer's only.

---

## See also

- [CONTRIBUTING.md](../CONTRIBUTING.md) — TDD, quality gates, how to add a route, commit style
- [docs/GITHUB.md](GITHUB.md) — GitHub PR triage, external fork handling, SonarCloud scenarios
- [RELEASE.md](../RELEASE.md) — release checklist, PyPI/GitHub publishing, tag protocol
- [CHANGELOG.md](../CHANGELOG.md) — version history
- [CLAUDE.md](../CLAUDE.md) — project memory hub for AI agents
