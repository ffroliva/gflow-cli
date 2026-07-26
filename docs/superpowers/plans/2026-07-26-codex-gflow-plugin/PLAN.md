# Codex `gflow:*` Plugin Implementation Plan

> **For agentic workers:** Execute this plan task-by-task. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Package gflow-cli's canonical skills as an installable Codex plugin under the
`gflow:` namespace.

**Architecture:** Add a skills-only plugin manifest at the repository root and expose it
through a repo-local marketplace. Keep `skills/` as the single source of truth and enforce
the packaging contract with one focused pytest module.

**Tech Stack:** JSON manifests, Markdown documentation, pytest, Codex CLI.

---

### Task 1: Specify the packaging contract

**Files:**

- Create: `tests/scripts/test_codex_plugin.py`

- [x] Write tests for plugin identity, marketplace discovery, and skill inventory.
- [x] Run the focused test and confirm it fails because the manifests do not exist.

### Task 2: Package the canonical skills

**Files:**

- Create: `.codex-plugin/plugin.json`
- Create: `.agents/plugins/marketplace.json`

- [x] Add a validation-ready skills-only plugin manifest named `gflow`.
- [x] Add a repo marketplace entry whose local source is the repository root.
- [x] Run the focused test and confirm it passes.
- [x] Run the Codex plugin validator against the repository root.

### Task 3: Document Codex installation and invocation

**Files:**

- Modify: `AGENTS.md`
- Modify: `CONTRIBUTING.md`
- Modify: `RELEASE.md`
- Modify: `skills/release/SKILL.md`

- [x] Replace the Codex "paste the skill" guidance with the plugin install flow.
- [x] Document `$gflow:<skill>` invocation, restart/new-session behavior, and the IDE
      extension limitation.
- [x] Keep the plugin manifest version aligned during the release workflow.

### Task 4: Verify the integration

**Files:**

- Modify: `docs/superpowers/plans/2026-07-26-codex-gflow-plugin/PLAN.md`

- [x] Confirm Codex lists `gflow` from the repo marketplace.
- [x] Run focused pytest, repo hygiene, doc links, website-docs PII, ruff, formatting, and
      pyright.
- [x] Review the final diff and mark all plan tasks complete.
