# AGENTS.md — gflow-cli

> Universal entry point for AI coding agents. Read this first; everything else routes from here.

Supported tools that auto-discover this file: Cursor, Codex, Aider, Antigravity (`agy`), Jules, Devin, Windsurf, Zed, Warp, opencode, RooCode, Amp, Junie, Phoenix, GitHub Copilot, VS Code, Factory, Augment, Semgrep, Kilo Code, UiPath. Claude Code reads [CLAUDE.md](CLAUDE.md), which cross-references this file.

## Project at a glance

- Unofficial Python CLI for [Google Flow](https://labs.google/fx/tools/flow) — drives Veo (image-to-video, text-to-video) and Imagen (text-to-image) generations from the terminal by reverse-engineering Flow's private REST API at `aisandbox-pa.googleapis.com`.
- Python 3.11+ · `uv`-managed · `hatchling` builds · Playwright Chromium transport · `pyright` strict · `ruff` · `pytest`.
- Single-package modular monolith. Top-level modules under `src/gflow_cli/`: `api/`, `auth/`, `data/`, `mcp/`, `services/`, `tools/`, `ui/`, `worker/`, `browser_manager.py`, `cli.py`, `_cli_helpers.py`, `cli_character.py`, `cli_data.py`, `cli_image.py`, `cli_instructions.py`, `cli_models.py`, `cli_movie.py`, `cli_run.py`, `cli_scene.py`, `cli_tools.py`, `cli_video.py`, `chain.py`, `chain_manifest.py`, `composition.py`, `config.py`, `errors.py`, `exceptions.py`, `image_batch.py`, `movie_manifest.py`, `observability.py`, `paths.py`, `profile_store.py`.
- Command surface: `gflow auth`, `gflow image` (t2i/i2i/batch/upload/upscale), `gflow video` (t2v/i2v/r2v/chain — no `batch` subcommand; the nonfunctional stub was removed, loop `gflow video t2v`/`i2v` from the shell for multi-clip runs), `gflow character` (create/list/show/rm/voices — reusable project-scoped Flow Character entities), `gflow scene` (create/show — Add Clip / Scenes, with `create --output` for credit-free server-side extended video), `gflow instructions` (persistent Agent-Mode brief cards — add/list/enable/disable/rm/apply/toggle-mode, credits-free, `--project` required), `gflow movie` (run/template — multi-scene manifest pipeline), `gflow tools` (list/show/run — prompt-rewriting tools, also `--tool` on generation commands), `gflow data` (catalog queries), `gflow models`, `gflow run`, `gflow mcp` (run/setup — stdio MCP server), and `gflow serve` (Streamable HTTP at `/mcp`; `--transport sse` is deprecated).
- Works with any Google account that has Flow access. All generations bill against the user's own Google account.

## Headed-browser dependency (architectural reality)

gflow-cli currently drives Flow via a **real Chrome session managed by Playwright** — `ui_automation` transport. Google's auth + reCAPTCHA stack rejects Playwright's bundled Chromium and most headless approaches. This is the project's defining trade-off:

- ✅ Works end-to-end against live Google accounts.
- ❌ Requires a saved Chrome profile, a display server for one-time login, and ~150 MB for Chromium.
- ❌ Cannot run on serverless / headless CI workers without prerecorded profile transplant.
- ❌ Per-account horizontal concurrency is capped by what one warm Page pool can drive.

If you can help unblock a pure HTTP transport (especially for video generation, where HTTP 401 + reCAPTCHA mints currently block us), please open an issue — see the README "Architecture & current limitations" section.

## Dev environment tips

- `uv sync` then `uv run playwright install chromium`. No global Python install needed.
- Copy `.env.template` to `.env.local`; never commit `.env.local`. It documents every env var.
- Output goes to `./tmp/` for scripts/tests or `$GFLOW_CLI_OUTPUT_DIR` for CLI outputs (defaults to `./out/`).
- One-time auth: `gflow auth login --browser chrome` (recommended — a real-Chrome profile is what generation runs need; the default `--browser auto` can also pick the internal strategy, and generation later fails fast on profiles created with a non-chrome strategy).
- Use `/gflow:status` to see the current task before starting work; `/gflow:known-issues` before touching auth or reCAPTCHA code paths.

## Testing instructions — The Impeccable Routine

Run these gates in order before every commit:

```powershell
$env:PYTHONUTF8=1
uv run python scripts/ci/check_repo_hygiene.py
uv run python scripts/ci/check_doc_links.py
uv run python scripts/ci/check_website_docs_pii.py
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run python -m pytest -q --cov=gflow_cli
```

Or invoke the wrapper: `/gflow:check`.

- Use `pytest -m "not live and not e2e and not smoke"` locally; full suite OOMs on small dev machines. Scope to changed dirs; trust CI for the full sweep.
- TDD is non-negotiable. Coverage floor: 80% overall.
- Documentation is a first-class deliverable. Every behavior, workflow, config, or operator-facing change must update the relevant docs or state why no docs changed in the PR/checklist. `scripts/ci/check_doc_links.py` is a merge gate.
- **A PR is not done until its SonarCloud gate is green (zero new issues).** The six gates above are local/pre-commit; SonarCloud is server-side and runs in CI (`sonar.qualitygate.wait=true` → a red gate turns the `SonarCloud analysis` check red). Before calling a PR merge-ready, verify it with `/gflow:sonar <N>` (it is skipped on fork PRs — maintainer-checked there).
- Live tests (`@pytest.mark.live`) opt in via `GFLOW_LIVE=1`. E2E tests require `GFLOW_CLI_E2E_PROFILE`.

## Code style

- Type hints everywhere; `pyright` strict on `src/gflow_cli`.
- Structured logging only (`structlog`) — **never** raw `print()` or `import logging` in `src/`.
- Errors as RFC 9457 Problem Details with stable per-class exit codes (3–30, e.g. 11 is `ConfigurationError` — including `ProfileLockedError` for same-profile lease contention, 16 is the `DataStoreError` family, 19 `SceneConcatError`, 20 `FrameExtractionError`, 21 `ChainPartialError`, 22 `UpscaleUnavailableError`, 25 `FlowAgentUiError`, 28 `UiModeUnavailableError`, 29 `MentionIndexUnavailableError`, 30 `QueueSchemaError`). See `src/gflow_cli/errors.py::EXIT_CODE_MAP` for the complete mapping.
- 100-char line length, `ruff` configured. Imports sorted by `ruff` (isort rules).
- **YAGNI / least-code**: prefer the smallest change that works. No speculative abstractions (interface/factory with one implementation), no config or flags nobody sets, no dead constants/helpers, no reinventing the stdlib. Review carries this as its own lens — the **D14 over-engineering** dimension of [`pr-council-review`](skills/pr-council-review/SKILL.md) (baseline, always runs). Its rubric is portable; the `ponytail` plugin (see CONTRIBUTING) is an optional accelerant, not a dependency.
- **MCP & CLI Schema Symmetry**: Any updates or additions to user-facing CLI command parameters (e.g., `gflow image t2i`, `gflow video`) must be mirrored in the corresponding MCP tool definitions. Never add option/argument fields to Click commands without updating the MCP server implementation. This symmetry is enforced programmatically in CI via `tests/mcp/test_cli_parity.py` (every CLI leaf command needs a mapped MCP tool or an explicit, reasoned exemption) plus the schema checks in `tests/mcp/test_server.py`.
- **Locale-Invariance Discipline for UI Automation**: **Never** write text-label string selectors (`has-text(...)` or multi-locale text lists) for DOM elements, overlays, announcements, menus, tabs, or buttons. All DOM selectors in `src/gflow_cli/api/transports/` must be 100% language-agnostic, anchoring exclusively on structural properties: **Tier 1 Anchors** — e.g. hyperlinks (`a[href*='changelog']`), icon ligatures (`button:has(i.google-symbols:text('close'))`), ARIA roles (`[role='banner']`, `button[data-dismiss]`), and hierarchical DOM relationships (`[role='dialog']:has(a[href*='changelog']) button`). Relying on translated display labels or maintaining multi-locale text cascades is strictly forbidden as an anti-pattern hack.

## PR instructions

- Branch naming: `feature/`, `bugfix/`, `hotfix/`, `chore/`, `docs/`, `test/`, `release/` — never `claude/` or unprefixed.
- `develop` is the integration branch; `main` is protected. Releases tag off `main` only.
- **Never add AI attribution to commit messages.** `Co-Authored-By:` trailers are fine if the user asks for them; auto-generated "🤖 Generated with Claude Code" footers are not.
- Run `/gflow:check` (or the Impeccable Routine) before every commit.
- All releases require a signed annotated tag (`git tag -s vX.Y.Z`); CI rejects unsigned tags.

## Working discipline — verify before you act

These rules exist because docs alone don't bind under momentum: a "check open PRs first" rule was already written and still got skipped, and a PR was merged without seeing an entangled open one. Follow them on every task.

- **Check what's already in flight before coding a fix, opening a PR, or merging.** Run `gh pr list` and `git ls-remote` first — another open PR may already touch the same issue or files. Reconcile against it; don't re-derive "the only thing left" from a stale handoff. (A `PreToolUse` hook also surfaces same-issue open PRs at `gh pr create`/`merge` time, but treat that as a backstop, not a substitute for looking.)
- **Truth is the CLI and running the code — not IDE/LSP "reminder" diagnostics.** Editor / `pyright`-in-worktree warnings go stale for an entire session and throw false positives (especially across multiple worktrees). Confirm with `ruff` / `pyright` / `pytest` from the terminal — or trust the worktree's own venv — never an IDE squiggle.
- **Verify third-party runtime behavior empirically before wiring it in.** Don't assume how an external library, API, or browser actually behaves — exercise it once and observe, then build on the observed contract.
- **If a claim can't be verified in the current environment, it's LIKELY — not CONFIRMED.** Keep the issue open, reference it with `Refs #N` (not `Closes #N`), and ship diagnostics rather than a blind fix. When you can't reproduce it, hand the fix to whoever can.
- **This project reverse-engineers a blackbox.** gflow-cli doesn't own Google Flow — it drives real Flow through inspected HAR/DOM/browser-log behavior. Offline checks (types, lint, unit/BDD tests) verify *our* code does what we think it does; they cannot verify Flow still behaves the way we captured it. Every feature that touches a generation path is **live-verified**, not just offline-tested, before it's called done — see `/gflow:live-verify`.

## Standard Workflow Sequence

All AI agents and harnesses working on `gflow-cli` follow this standard 10-phase pipeline from issue discovery to public release:

| Phase | Skill / Command | Purpose | Output Artifact |
|---|---|---|---|
| 1. Triage | `/gflow:issue-assessment <N>` | Read-only issue analysis & root cause hypothesis | `issue_assessment_<N>.md` |
| 2. Pre-Implementation | `/gflow:predict <proposal>` | Adversarial audit (D14 YAGNI, security, risks) | GO / CAUTION / STOP verdict |
| 3. BDD Scaffolding | `/gflow:scenario <feature>` | Edge-case explorer & BDD Gherkin spec | `Scenario:` blocks & test scaffold |
| 4. Implementation Plan | `/gflow:plan <feature>` | Task-by-task atomic implementation plan | `docs/superpowers/plans/<date>-<slug>/PLAN.md` |
| 5. Council Review | `/gflow:pr-council-review` / `llm-council` | Multi-dimensional audit across 6 core dimensions | Consensus verdict report |
| 6. Task Execution | `/gflow:status` | Track unchecked tasks during TDD execution | Next unchecked task |
| 7. Pre-Commit Quality | `/gflow:check` | The Impeccable Routine (hygiene, ruff, pyright, pytest) | All green local checks |
| 8. Live Verification | `/gflow:live-verify` | 5-layer proof against real Flow transport | `docs/LIVE_VERIFICATION_vX.Y.Z.md` |
| 9. PR & Issue Close | `/gflow:issue-resolve <N>` | PR, SonarCloud 0-issue gate, merge to develop | Closed GitHub issue |
| 10. Release Pipeline | `/gflow:release` | Version bump, signed tag (`git tag -s`), PyPI publish | Shipped release & back-merge |

### Pipeline Continuation Mandate

Every AI agent executing any phase of this pipeline MUST proactively state the completed phase result and explicitly recommend/announce the exact next sequential phase to the user. Never stop passively after a phase completes without identifying the next step.

| Current Phase | Completed Artifact / Gate | Next Sequential Phase & Command |
|---|---|---|
| Phase 1: Triage | `issue_assessment_<N>.md` | ➔ Phase 2: Pre-Implementation (`/gflow:predict <proposal>`) |
| Phase 2: Pre-Implementation | Verdict `GO` or `CAUTION` | ➔ Phase 3: BDD Scaffolding (`/gflow:scenario <feature>`) |
| Phase 3: BDD Scaffolding | `Scenario:` blocks & test scaffold | ➔ Phase 4: Implementation Plan (`/gflow:plan <feature>`) |
| Phase 4: Implementation Plan | `PLAN.md` created & approved | ➔ Phase 6: Task Execution (`/gflow:status`) |
| Phase 6: Task Execution | All plan tasks checked | ➔ Phase 7: Pre-Commit Quality (`/gflow:check`) |
| Phase 7: Pre-Commit Quality | All 7 quality gates green | ➔ Phase 5: Council Review (`/gflow:branch-review` or `/gflow:pr-council-review`) |
| Phase 5: Council Review | Consensus 🟢 GREEN | ➔ Phase 8: Live Verification (`/gflow:live-verify`) or Phase 9 (`/gflow:issue-resolve <N>`) |
| Phase 8: Live Verification | `LIVE_VERIFICATION_vX.Y.Z.md` | ➔ Phase 9: Issue Resolve & PR (`/gflow:issue-resolve <N>`) |
| Phase 9: Issue Resolve & PR | PR open & SonarCloud 0-issue gate green | ➔ Phase 10: Release Pipeline (`/gflow:release`) |
| Phase 10: Release Pipeline | Signed tag & PyPI published | ➔ Done (Release shipped & back-merged) |

## Skills reference (cross-tool)

The `skills/` directory ships installable agent skill docs in plain Markdown with YAML frontmatter. Any agent can consume them directly:

| Skill | Path | When to load |
|---|---|---|
| `gflow-cli` | [`skills/gflow-cli/SKILL.md`](skills/gflow-cli/SKILL.md) | User wants to run any `gflow` command — auth, T2V, I2V, T2I, I2I, batch |
| `predict` | [`skills/predict/SKILL.md`](skills/predict/SKILL.md) | Pre-implementation adversarial analysis before any high-stakes change |
| `scenario` | [`skills/scenario/SKILL.md`](skills/scenario/SKILL.md) | Edge-case explorer after a predict GO/CAUTION |
| `plan` | [`skills/plan/SKILL.md`](skills/plan/SKILL.md) | Create a structured task-by-task implementation plan for a feature |
| `status` | [`skills/status/SKILL.md`](skills/status/SKILL.md) | Show current plan state, progress, and next unchecked task |
| `pr-council-review` | [`skills/pr-council-review/SKILL.md`](skills/pr-council-review/SKILL.md) | Multi-dimensional PR council review |
| `llm-council` | [`skills/llm-council/SKILL.md`](skills/llm-council/SKILL.md) | Wraps `pr-council-review` with external CLI reviewers (`codex` + Antigravity `agy`) for high-stakes reviews |
| `issue-assessment` | [`skills/issue-assessment/SKILL.md`](skills/issue-assessment/SKILL.md) | Triage a GitHub issue (read-only) before any fix work |
| `issue-resolve` | [`skills/issue-resolve/SKILL.md`](skills/issue-resolve/SKILL.md) | Drive an assessed issue to a test-first fix + draft PR |
| `check` | [`skills/check/SKILL.md`](skills/check/SKILL.md) | Quality gates (lint/format/types/tests) before every commit |
| `changelog` | [`skills/changelog/SKILL.md`](skills/changelog/SKILL.md) | Unreleased changes + last tagged version |
| `known-issues` | [`skills/known-issues/SKILL.md`](skills/known-issues/SKILL.md) | Open/mitigated known issues — before auth/reCAPTCHA work |
| `sonar` | [`skills/sonar/SKILL.md`](skills/sonar/SKILL.md) | Drive the SonarCloud quality gate to zero for a PR/branch |
| `live-verify` | [`skills/live-verify/SKILL.md`](skills/live-verify/SKILL.md) | Pre-flight state check at start of work; live-verification against real Flow before claiming done |
| `doc-review` | [`skills/doc-review/SKILL.md`](skills/doc-review/SKILL.md) | Council-driven documentation audit before a release |
| `release` | [`skills/release/SKILL.md`](skills/release/SKILL.md) | Cut a release — bump, CHANGELOG, tag, push, back-merge |

**RULE — agent-agnostic by construction.** Core skills live in `skills/<name>/SKILL.md`
and must be resolvable by ANY agent via this file. Vendor directories hold thin wrappers
only (`.claude/commands/gflow/*.md` are pointers into `skills/`); never put protocol
content in a vendor directory.

**Codex CLI / desktop app:** install the repo's skills-only plugin from the repository root,
then start a new session:

```powershell
codex plugin marketplace add .
codex plugin add gflow@gflow-cli
```

Invoke skills with Codex's `$` syntax, for example `$gflow:status`, `$gflow:check`, or
`$gflow:pr-council-review`. Codex reserves slash commands for its own command surface; the
Claude Code spelling `/gflow:*` is therefore intentionally not reproduced. The Codex IDE
extension does not currently load plugins, so IDE-only users should include the relevant
`skills/<name>/SKILL.md` directly.

**Cursor / Aider / Antigravity (`agy`):** paste or include the relevant `SKILL.md` in your system context. Note: in the `agy` TUI prompt, custom slash commands (e.g. `/gflow:pr-council-review`) are blocked by the TUI's command parser. Type them as plain text without the leading slash (e.g. `gflow:pr-council-review`, `gflow:branch-review`, or `gflow:check`) to trigger the corresponding agent skill workflow.
**Claude Code:** the `/gflow:` slash commands in `.claude/commands/gflow/` are auto-discovered when the project is open — no extra setup needed. To register a skill globally, copy the command file to `~/.claude/commands/`.
**Custom agents:** fetch `skills/gflow-cli/SKILL.md` into your knowledge base before answering gflow questions.

The SkillOpt harness at `scripts/dev/skillopt/` measures how accurately each skill guides an agent, and supports multiple providers (Anthropic, OpenAI-compat, Gemini, local models). See [`scripts/dev/skillopt/README.md`](scripts/dev/skillopt/README.md).

## Where to look next

- **Architecture & target shape** → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Mandates & routing rules** → [docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md)
- **Referencing assets** (`@Name` mention vs `--reference-entity` vs `--ref`) → [docs/REFERENCE_STRATEGIES.md](docs/REFERENCE_STRATEGIES.md)
- **Full docs index** → [docs/INDEX.md](docs/INDEX.md)
- **Known issues** (read before touching auth / reCAPTCHA) → [KNOWN_ISSUES.md](KNOWN_ISSUES.md)
- **Current task** → `/gflow:status` · **Create a feature plan** → `/gflow:plan <feature>` · **Full roadmap** → [PLAN.md](PLAN.md)
- **Release protocol** → [RELEASE.md](RELEASE.md)

## Claude Code-specific notes

[CLAUDE.md](CLAUDE.md) carries the auto-load instructions Claude Code reads natively. It cross-references this file for the universal rules; Claude-Code-specific session protocol (skills, slash commands, memory) stays in CLAUDE.md.
