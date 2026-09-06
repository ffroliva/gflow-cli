# AGENTS.md — gflow-cli

> Universal entry point for AI coding agents. Read this first; everything else routes from here.

Supported tools that auto-discover this file: Cursor, Codex, Aider, Antigravity (`agy`), Jules, Devin, Windsurf, Zed, Warp, opencode, RooCode, Amp, Junie, Phoenix, GitHub Copilot, VS Code, Factory, Augment, Semgrep, Kilo Code, UiPath. Claude Code reads [CLAUDE.md](CLAUDE.md), which cross-references this file.

## Skill Routing — the default workflow, not a menu

**The `/gflow:` skills ARE this project's development lifecycle.** They are not
optional tooling you may reach for; they are how work is done here. If a row below
matches your situation, load that skill **before acting** — before exploring the
codebase, before answering, before editing. A skill loaded after the work is a skill
that did not run.

| If you are about to… | Load first | Non-negotiable because |
|---|---|---|
| Touch a GitHub issue | `/gflow:issue-assessment <N>` | Read-only triage precedes any fix; classification changes what "fixing" means |
| Propose a transport, auth, selector, or schema change | `/gflow:predict <proposal>` | Five adversarial personas return GO / CAUTION / STOP before code exists |
| Start a feature | `/gflow:scenario` → `/gflow:plan` | Edge cases before tasks; tasks before code |
| Resume work / ask "where are we?" | `/gflow:status` | The current plan's next unchecked task is the answer, not your guess |
| Commit **anything** | `/gflow:check` | It is the exact CI gate; skipping it is how a format failure shipped past an 8-agent council (PR #269) |
| Open or review a PR | `/gflow:pr-council-review <N>` (or `/gflow:branch-review`) | Standing-authorized. Run it — do not ask permission first |
| Claim a generation feature works | `/gflow:live-verify` | Offline-green is never done-done on a generation path |
| Cut a release | `/gflow:release` | It hard-gates on changelog → check → live-verify → doc-review; each is a STOP |
| Audit docs before shipping | `/gflow:doc-review` | Catches same-release errors a read-through cannot |
| See a red SonarCloud check | `/gflow:sonar <N>` | The gate measures *new* code; the fix is rarely where you would look |
| Touch auth or reCAPTCHA | `/gflow:known-issues` | Known-broken surfaces have documented workarounds; rediscovering them costs days |

**Canonical bodies live in `skills/<name>/SKILL.md`** — plain Markdown, agent-agnostic
by construction, so Codex / Cursor / Aider / `agy` read exactly what Claude Code reads.
`.claude/commands/gflow/*.md` are thin Claude-Code wrappers that point at them.

> **This table exists because routing by memory failed.** On 2026-09-01 an agent worked
> a full session — recovering two interrupted sessions, merging five PRs — without ever
> loading this file, because `CLAUDE.md` only *asked* it to. It reached step 10 of a
> release before meeting the pipeline's own doc-review gate, and the required
> `LIVE_VERIFICATION_v0.63.0.md` had not been written despite the feature having been
> live-verified. `CLAUDE.md` now `@`-imports this file, so it is always in context. The
> table is the other half: being loaded is useless if the mapping from situation to skill
> is left to recall.

## Project at a glance

- Unofficial Python CLI for [Google Flow](https://labs.google/fx/tools/flow) — drives Veo (image-to-video, text-to-video) and Imagen (text-to-image) generations from the terminal by reverse-engineering Flow's private REST API at `aisandbox-pa.googleapis.com` — and, for accounts Google has moved to `flow.google.com`, that frontend's `batchexecute` wire (text-to-video, and image-to-video from a local `--initial-frame`, today; `GFLOW_CLI_FLOW_HOST`).
- Python 3.11+ · `uv`-managed · `hatchling` builds · Playwright Chromium transport · `pyright` strict · `ruff` · `pytest`.
- Single-package modular monolith. Top-level modules under `src/gflow_cli/`: `api/`, `auth/`, `data/`, `mcp/`, `services/`, `tools/`, `ui/`, `worker/`, `browser_manager.py`, `cli.py`, `_cli_helpers.py`, `diagnostics.py`, `json_output.py`, `media.py`, `profile_lease.py`, `redaction.py`, `storage.py`, `winsec.py`, `cli_project.py`, `cli_character.py`, `cli_credits.py`, `cli_data.py`, `cli_image.py`, `cli_instructions.py`, `cli_models.py`, `cli_movie.py`, `cli_run.py`, `cli_scene.py`, `cli_tools.py`, `cli_video.py`, `chain.py`, `chain_manifest.py`, `cli_doctor.py`, `cli_update.py`, `composition.py`, `config.py`, `errors.py`, `file_integrity.py`, `flow_selectors/`, `update_check.py`, `exceptions.py`, `image_batch.py`, `movie_manifest.py`, `observability.py`, `paths.py`, `profile_store.py`.
- Command surface: `gflow auth`, `gflow credits` (user/list — read-only Veo balance), `gflow image` (t2i/i2i/batch/upload/upscale), `gflow video` (t2v/i2v/r2v/chain/extend — `extend` continues an existing clip past Flow's 8s ceiling, server-seeded from the source so the join is continuous; no `batch` subcommand; the nonfunctional stub was removed, loop `gflow video t2v`/`i2v` from the shell for multi-clip runs), `gflow character` (create/list/show/rm/voices — reusable project-scoped Flow Character entities), `gflow scene` (create/show — Add Clip / Scenes, with `create --output` for credit-free server-side extended video), `gflow instructions` (persistent Agent-Mode brief cards — add/list/enable/disable/rm/apply/toggle-mode, credits-free, `--project` required), `gflow movie` (run/template — multi-scene manifest pipeline), `gflow tools` (list/show/run — prompt-rewriting tools, also `--tool` on generation commands), `gflow data` (catalog queries), `gflow doctor` (read-only pre-flight diagnostic, exit 33 = findings present), `gflow update` (self-update through the installer that put it here — uv tool / pipx / pip; `--check` only reports; source installs refused, exit 11; deliberately no MCP twin), `gflow project`, `gflow models`, `gflow run`, `gflow mcp` (run/setup — stdio MCP server), and `gflow serve` (Streamable HTTP at `/mcp`; `--transport sse` is deprecated).
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
uv run python scripts/ci/generate_website_docs.py --check
uv run python scripts/ci/check_council_memory.py
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run python -m pytest -q --cov=gflow_cli
```

Or invoke the wrapper: `/gflow:check` — **prefer it.** This list is the mechanical subset;
the skill additionally carries the **step 1b surface blast-radius sweep** (MCP↔CLI parity and
the five other mirror axes), which no command here can check and no CI gate can see.

> **Do not trim this list again.** It previously omitted
> `generate_website_docs.py --check`, so an agent following AGENTS.md instead of the skill
> shipped a stale `website/docs/` mirror to a green local run and a red CI (#626 session,
> 2026-09-02). Two lists that disagree means the shorter one is silently wrong.

- Use `pytest -m "not live and not e2e and not smoke"` locally; full suite OOMs on small dev machines. Scope to changed dirs; trust CI for the full sweep.
- TDD is non-negotiable. Coverage floor: 80% overall.
- Documentation is a first-class deliverable. Every behavior, workflow, config, or operator-facing change must update the relevant docs or state why no docs changed in the PR/checklist. `scripts/ci/check_doc_links.py` is a merge gate.
- **A PR is not done until its SonarCloud gate is green (zero new issues).** The nine gates above are local/pre-commit; SonarCloud is server-side and runs in CI (`sonar.qualitygate.wait=true` → a red gate turns the `SonarCloud analysis` check red). Before calling a PR merge-ready, verify it with `/gflow:sonar <N>` (it is skipped on fork PRs — maintainer-checked there).
- **E2E is the decisive layer, and it is required for every behavior change that touches a Flow surface.** Run the `tests/e2e/` test that covers the change, or write one — offline-green proves only that *our* code does what we think, never that Flow still behaves as captured. `/gflow:live-verify` does **not** satisfy this: it drives CLI commands by hand into a gitignored note and never runs `pytest -m e2e`. The two are complementary — an e2e test is a re-runnable regression, a live-verify ledger is a narrative record. A change touching no Flow surface at all (docs, help text, exit-code plumbing) is out of scope — say so explicitly rather than leaving it blank. Anything else follows the **Iron Law** above: run it, or name the external blocker that stops you. "A maintainer will run it" is a handoff, not a verification, and is only available once the blocker is named.
- E2E tests require `GFLOW_CLI_E2E_PROFILE` and `-m e2e` (excluded from the default `addopts`, so they never run by accident). Cost sub-markers: `e2e_auth` (zero credits, browser only), `e2e_image` (zero credits, daily cap), `e2e_scene`, `e2e_data`, `e2e_batch`, `e2e_character` (opt in with `GFLOW_CLI_E2E_RUN_CHARACTER=1`), `e2e_video` (spends Veo credits — opt in with `GFLOW_CLI_E2E_RUN_VIDEO=1`). Redact account identifiers and any token or cookie value before pasting output into a PR.
- Live tests (`@pytest.mark.live`) opt in via `GFLOW_LIVE=1`.

## Code style

- Type hints everywhere; `pyright` strict on `src/gflow_cli`.
- Structured logging only (`structlog`) — **never** raw `print()` or `import logging` in `src/`.
- Errors as RFC 9457 Problem Details with stable per-class exit codes (3–36, e.g. 11 is `ConfigurationError` — including `ProfileLockedError` for same-profile lease contention, 16 is the `DataStoreError` family, 19 `SceneConcatError`, 20 `FrameExtractionError`, 21 `ChainPartialError`, 22 `UpscaleUnavailableError`, 25 `FlowAgentUiError`, 28 `UiModeUnavailableError`, 29 `MentionIndexUnavailableError`, 30 `QueueSchemaError`). See `src/gflow_cli/errors.py::EXIT_CODE_MAP` for the complete mapping. Exit 33 is reserved outside that map: `gflow doctor` findings-present — a successful diagnosis, not an error class.
- 100-char line length, `ruff` configured. Imports sorted by `ruff` (isort rules).
- **YAGNI / least-code**: prefer the smallest change that works. No speculative abstractions (interface/factory with one implementation), no config or flags nobody sets, no dead constants/helpers, no reinventing the stdlib. Review carries this as its own lens — the **D14 over-engineering** dimension of [`pr-council-review`](skills/pr-council-review/SKILL.md) (baseline, always runs). Its rubric is portable; the `ponytail` plugin (see CONTRIBUTING) is an optional accelerant, not a dependency.
- **MCP & CLI Schema Symmetry**: Any updates or additions to user-facing CLI command parameters (e.g., `gflow image t2i`, `gflow video`) must be mirrored in the corresponding MCP tool definitions. Never add option/argument fields to Click commands without updating the MCP server implementation. This symmetry is enforced programmatically in CI via `tests/mcp/test_cli_parity.py` (every CLI leaf command needs a mapped MCP tool or an explicit, reasoned exemption) plus the schema checks in `tests/mcp/test_server.py`.

  **That gate now covers leaves *and their options*** (`test_every_cli_leaf_has_an_mcp_decision`,
  `test_every_cli_option_reaches_its_mcp_tool`). It still cannot see a queued-payload key that
  goes unread, a tool docstring asserting a restriction the CLI no longer has, or a twin that
  was never *run*. So MCP stays a responsibility of **every phase**, not a post-hoc checklist
  item. Each skill owns its slice:

  | Phase | Skill | Its MCP duty |
  |---|---|---|
  | 1 Triage | `issue-assessment` | Name which surfaces reproduce it — CLI, MCP, or both |
  | 2 Pre-impl | `predict` | Persona 4 scopes the MCP blast radius before code exists |
  | 3 BDD | `scenario` | **D13** — enumerate the MCP twin of every edge case |
  | 4 Plan | `plan` | **Task 6 is not optional when task 5 exists** |
  | 5 Review | `pr-council-review` | **D15** — surface parity, incl. the payload-key round trip |
  | 7 Check | `check` | **Step 1b** — the canonical six mirror axes |
  | 8 Live-verify | `live-verify` | The MCP queued path is *different code*; decide if it needs its own run |
  | 10 Release | `doc-review` | Grade `docs/MCP.md` + tool docstrings; a **false** claim blocks |

  The six mirror axes are written out **once**, in [`skills/check/SKILL.md`](skills/check/SKILL.md)
  step 1b. Every other skill cites them. Do not copy the table around — a duplicated checklist
  drifts, which is the exact failure this row exists to prevent.
- **Locale-Invariance Discipline for UI Automation**: **Never** write text-label string selectors (`has-text(...)` or multi-locale text lists) for DOM elements, overlays, announcements, menus, tabs, or buttons. All DOM selectors in `src/gflow_cli/api/transports/` must be 100% language-agnostic, anchoring exclusively on structural properties: **Tier 1 Anchors** — e.g. hyperlinks (`a[href*='changelog']`), icon ligatures (`button:has(i.google-symbols:text('close'))`), ARIA roles (`[role='banner']`, `button[data-dismiss]`), and hierarchical DOM relationships (`[role='dialog']:has(a[href*='changelog']) button`). Relying on translated display labels or maintaining multi-locale text cascades is strictly forbidden as an anti-pattern hack.

## PR instructions

- Branch naming: `feature/`, `bugfix/`, `hotfix/`, `chore/`, `docs/`, `test/`, `release/` — never `claude/` or unprefixed.
- `develop` is the integration branch; `main` is protected. Releases tag off `main` only.
- **Never add AI attribution to commit messages.** `Co-Authored-By:` trailers are fine if the user asks for them; auto-generated "🤖 Generated with Claude Code" footers are not.
- Run `/gflow:check` (or the Impeccable Routine) before every commit.
- All releases require a signed annotated tag (`git tag -s vX.Y.Z`); CI rejects unsigned tags.

## Working discipline — verify before you act

These rules exist because docs alone don't bind under momentum: a "check open PRs first" rule was already written and still got skipped, and a PR was merged without seeing an entangled open one. Follow them on every task.

### The Iron Law — done means verified by a run

```
IF YOU TOUCHED A COMMAND, A FEATURE, OR A FLOW SURFACE,
YOU RUN ITS E2E TEST. NO RUN = NOT DONE.
```

Not "tests pass". Not "the gates are green". Not "it's a thin wrapper over code that
is already covered". **A run, against the real thing, that you watched.** Offline green
proves our code does what we think it does; it cannot prove Flow still behaves as
captured, and it cannot prove the surface an agent or a user actually calls is wired to
the code under test. Everything not exercised is unknown, and unknown ships as a bug.

If no e2e test covers the change, **write one** — that is part of the change, not
follow-up work.

**The only permitted exception is a named external blocker** you cannot remove: an
exhausted API quota, hardware you do not have, an account you do not control, a cohort
Google has not put you in. Write the blocker down. Then these are **not** blockers, and
naming one is a rule violation, not a caveat:

- "I didn't get to it" · "it's covered by unit tests" · "it's a thin adapter"
- "the CLI path is verified and it shares the service"
- "recorded, not omitted"

That last one is the dangerous one, because it is a **good** convention being misused.
Recording an unverified item beats silently dropping it — but it is a record of a
blocker, never a substitute for a run. **Listing something as unverified that you could
have verified is the same failure as omitting it, plus a false paper trail.**

> **This law is written from the failure it prevents.** v0.69.0 shipped with two items
> under "Not verified": the MCP twin of the credits path, and 5 of 19 skill-benchmark
> tasks. Asked why we were going blind, both were verified **within the hour**. The MCP
> twin was a $0 read-only test — 3 assertions, 58 seconds, and it is now
> `tests/e2e/test_credits_mcp_e2e.py`. The 5 tasks ran on a model whose quota was still
> open, against a control run to separate model weakness from skill weakness, and
> exposed **four real defects** that the words "recorded, not omitted" had made invisible.
> Neither was ever blocked. Both were labelled instead of run.

### The second law — MCP parity is not a chore, it is the contract

```
EVERY USER-FACING CLI CAPABILITY HAS AN MCP TWIN,
OR A RECORDED REASON IT DOES NOT.
```

The CLI and the MCP server are two doors onto one product. A capability behind only one
of them is not "mostly shipped" — for whoever is standing at the other door it does not
exist, and nothing tells them so. Agents cannot read a `--help` they were never given;
they discover a missing option by getting the wrong result.

Parity is **not** satisfied by a command existing. It has to hold at every level the two
surfaces can drift apart: the leaf, **its options**, the queued-payload keys the worker
reads back, and the tool docstring's claims about what the CLI does. The canonical list
is the **six mirror axes**, written out once in
[`skills/check/SKILL.md`](skills/check/SKILL.md) step 1b and cited from everywhere else —
never copied, because a duplicated checklist drifts, which is the failure it exists to
catch.

**A twin is a separate surface, so the Iron Law applies to it separately.** Verifying the
CLI path does not verify the MCP path. They share a service, and the service was never
the risk — the adapter is. Run both.

Where a capability is deliberately CLI-only — an interactive login, a destructive local
operation, a long-running billed job with no way to take consent — that is a legitimate
answer. **Record it with its reason** in `tests/mcp/test_cli_parity.py`, and revisit the
reason when whatever justified it changes.

> **Written from a live gap.** `--reference-entity` exists on four leaf commands and no
> MCP tool exposes the concept at all, while the whole `gflow character` group sits
> exempt as "not yet ported". The product's entire **identity axis** — create a
> character, attach a character — is therefore unreachable over MCP, and the parity gate
> reported full compliance throughout, because it only ever checked that leaves were
> *mapped*. Found by an option-level sweep, filed as #689, and the gate now checks
> options too.

- **Check what's already in flight before coding a fix, opening a PR, or merging.** Run `gh pr list` and `git ls-remote` first — another open PR may already touch the same issue or files. Reconcile against it; don't re-derive "the only thing left" from a stale handoff. (A `PreToolUse` hook also surfaces same-issue open PRs at `gh pr create`/`merge` time, but treat that as a backstop, not a substitute for looking.)
- **Truth is the CLI and running the code — not IDE/LSP "reminder" diagnostics.** Editor / `pyright`-in-worktree warnings go stale for an entire session and throw false positives (especially across multiple worktrees). Confirm with `ruff` / `pyright` / `pytest` from the terminal — or trust the worktree's own venv — never an IDE squiggle.
- **Verify third-party runtime behavior empirically before wiring it in.** Don't assume how an external library, API, or browser actually behaves — exercise it once and observe, then build on the observed contract.
- **A claim you have not run is not a finding — it is a guess with formatting.** Confidence labels are not evidence: do not grade an unrun claim as "likely", "probably correct" or "low risk" and move on. Where a genuine external blocker stops the run (see the Iron Law), keep the issue open, reference it with `Refs #N` (not `Closes #N`), ship diagnostics rather than a blind fix, and hand it to whoever can reproduce it — naming what would settle it.
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
| 8. E2E + Live Verification | `pytest -m e2e` → `/gflow:live-verify` | The e2e suite against a live profile, then the 5-layer proof against real Flow transport | Pasted e2e result + `docs/LIVE_VERIFICATION_vX.Y.Z.md` |
| 9. PR & Issue Close | `/gflow:issue-resolve <N>` | PR, SonarCloud 0-issue gate, merge to develop | Closed GitHub issue |
| 10. Release Pipeline | `/gflow:release` | Version bump, signed tag (`git tag -s`), PyPI publish. **Internally gates on `/gflow:changelog` → `/gflow:check` → `/gflow:live-verify` → `/gflow:doc-review`; a failure at any of them is a STOP, not a warning.** | Shipped release & back-merge |

### Pipeline Continuation Mandate

Every AI agent executing any phase of this pipeline MUST proactively state the completed phase result and explicitly recommend/announce the exact next sequential phase to the user. Never stop passively after a phase completes without identifying the next step.

| Current Phase | Completed Artifact / Gate | Next Sequential Phase & Command |
|---|---|---|
| Phase 1: Triage | `issue_assessment_<N>.md` | ➔ Phase 2: Pre-Implementation (`/gflow:predict <proposal>`) |
| Phase 2: Pre-Implementation | Verdict `GO` or `CAUTION` | ➔ Phase 3: BDD Scaffolding (`/gflow:scenario <feature>`) |
| Phase 3: BDD Scaffolding | `Scenario:` blocks & test scaffold | ➔ Phase 4: Implementation Plan (`/gflow:plan <feature>`) |
| Phase 4: Implementation Plan | `PLAN.md` created & approved | ➔ Phase 6: Task Execution (`/gflow:status`) |
| Phase 6: Task Execution | All plan tasks checked | ➔ Phase 7: Pre-Commit Quality (`/gflow:check`) |
| Phase 7: Pre-Commit Quality | All quality gates green | ➔ Phase 5: Council Review (`/gflow:branch-review` or `/gflow:pr-council-review`) |
| Phase 5: Council Review | Consensus 🟢 GREEN | ➔ Phase 8: E2E + Live Verification (`pytest -m e2e`, then `/gflow:live-verify`) or Phase 9 (`/gflow:issue-resolve <N>`) |
| Phase 8: E2E + Live Verification | e2e result + `LIVE_VERIFICATION_vX.Y.Z.md` | ➔ Phase 9: Issue Resolve & PR (`/gflow:issue-resolve <N>`) |
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
| `video-production` | [`skills/video-production/SKILL.md`](skills/video-production/SKILL.md) | User wants a finished multi-shot or dialogue video (consistent actors, room, several angles, spoken lines), or clips came back with a film border, a drifting room, rushed dialogue, a person-policy refusal or exit 36 |
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

The SkillOpt harness at `scripts/dev/skillopt/` measures how accurately each skill guides an agent. It drives any OpenAI-compatible endpoint — OpenAI, a gateway (OpenRouter, LiteLLM, freellmapi), a local model, or Google's compat endpoint — through the project's own `GFLOW_CLI_LLM_*` settings, the same ones the prompt tools use; there is no separate provider configuration. See [`scripts/dev/skillopt/README.md`](scripts/dev/skillopt/README.md).

## Spiking a blackbox surface — the two modes

This project reverse-engineers Google Flow, so "go and look at the real thing" is a
first-class development activity, not a last resort. Two harnesses exist. **Use one of
them before writing a capture script from scratch.**

| Mode | Where | Driver | Who clicks | Use when |
|---|---|---|---|---|
| **In-process** | `scripts/dev/spike_*.py` | Playwright, via gflow's own transport | the script | you can already drive the surface, or you want to see exactly what gflow sees |
| **HAR + DOM** | [`scripts/dev/har-spike/`](scripts/dev/har-spike/README.md) | CDP-attached **real Chrome** through a pinned `agent-browser` | **a human, by hand** | the driver cannot reach the surface yet, or a capture is ambiguous and you need ground truth |

Start in-process; escalate to the HAR harness when the driver cannot get far enough to
observe anything. A hand-driven HAR is the tiebreaker.

Both write to **gitignored** output — `scripts/dev/_spike_out/` and
`scripts/dev/har-spike/artifacts/`. Captures carry Bearer tokens, cookies and prompts:
`*.har` is gitignored repo-wide, and a capture must never be committed or pasted into an
issue unredacted. Use `scripts/dev/har-spike/extract_har_summary.py` for the redacted
summary that IS shareable. Findings belong in `docs/superpowers/spikes/`; the evidence
that produced them stays local.

> **The harness is vendored in for discoverability, and that is the whole point.** It
> lived in a sibling directory outside git until 2026-09-06. Being outside the repo, no
> agent could find it, nothing pinned its `agent-browser` version, and its one test ran
> nowhere. A session that day wrote three capture scripts from scratch and only learned
> the harness existed because the user said so — after concluding, wrongly, that a
> working Flow feature was impossible. Keep it in-tree, keep its data disposable.

## Where to look next

- **Architecture & target shape** → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Mandates & routing rules** → [docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md)
- **Referencing assets** (`@Name` mention vs `--reference-entity` vs `--ref`) → [docs/REFERENCE_STRATEGIES.md](docs/REFERENCE_STRATEGIES.md)
- **Full docs index** → [docs/INDEX.md](docs/INDEX.md)
- **Known issues** (read before touching auth / reCAPTCHA) → [KNOWN_ISSUES.md](KNOWN_ISSUES.md)
- **Current task** → `/gflow:status` · **Create a feature plan** → `/gflow:plan <feature>` · **Full roadmap** → [PLAN.md](PLAN.md)
- **Release protocol** → [RELEASE.md](RELEASE.md)
- **Spiking a live Flow surface** (HAR + DOM capture) → [scripts/dev/har-spike/README.md](scripts/dev/har-spike/README.md)

## Claude Code-specific notes

[CLAUDE.md](CLAUDE.md) carries the auto-load instructions Claude Code reads natively. It cross-references this file for the universal rules; Claude-Code-specific session protocol (skills, slash commands, memory) stays in CLAUDE.md.
