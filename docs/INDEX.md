# Documentation Index

Welcome to the `gflow-cli` documentation. This index is the routing layer: it tells you where each topic lives. Keep `README.md` slim (high-level overview + install) and document deep details here.

| Doc | Purpose | Read when… |
|---|---|---|
| [README](../README.md) | Project overview, install, quick start | First time landing on the repo |
| [docs/DEMOS.md](DEMOS.md) | Gallery of `gflow` in action (terminal + split-screen Flow recordings) | You want to see what gflow looks like running |
| [AGENTS.md](../AGENTS.md) | Universal coding-agent spec — Cursor / Codex / Aider / Antigravity / Jules / etc. | Any AI coding agent enters the repo |
| [llms.txt](../llms.txt) | LLM-readable summary (llmstxt.org format) | A user pastes context about gflow into ChatGPT / Claude / Gemini |
| [docs/PROJECT_STATUS.md](PROJECT_STATUS.md) | Full milestone history + lifecycle policy | Auditing where the project is in its lifecycle |
| [docs/AGENT_GUIDE.md](AGENT_GUIDE.md) | Mandates and routing rules for AI agents (companion to AGENTS.md) | A coding agent needs the longer non-negotiable rules |
| [docs/GOVERNANCE_BENCHMARK.md](GOVERNANCE_BENCHMARK.md) | How the advisory materiality gate is measured (false-positive / coverage backtest) and calibrated | You're changing `MATERIAL_PATHS` or auditing whether the governance gate earns its friction |
| [CLAUDE.md](../CLAUDE.md) | Claude Code's session memory hub (Claude-Code-specific protocol; delegates universal rules to AGENTS.md) | First time Claude Code opens the repo |
| [PLAN.md](../PLAN.md) | Implementation plan (DDD / CQRS / phases / ADRs) | You want the architectural intent and roadmap |
| [RELEASE.md](../RELEASE.md) | Release checklist, prerelease policy, PyPI/GitHub publishing protocol | Cutting or auditing a release |
| [ROADMAP.md](../ROADMAP.md) | Themed milestones from v0.9 through v1.0 | You want a multi-release view of where the project is heading |
| [CHANGELOG](../CHANGELOG.md) | Version-by-version user-visible changes | Upgrading or auditing what shipped |
| [KNOWN_ISSUES](../KNOWN_ISSUES.md) | Open / mitigated / resolved issues with workarounds | Before opening a bug report; when something feels off |
| [DISCLAIMER](../DISCLAIMER.md) | Legal scope, takedown policy, prohibited uses | Before deploying anywhere non-trivial |
| [LICENSE](../LICENSE) | MIT license text | Always |
| [CONTRIBUTING](../CONTRIBUTING.md) | TDD workflow, test categories, coverage targets | Before opening a PR |
| **[docs/DEVELOPMENT.md](DEVELOPMENT.md)** | Branching model, PR protocol, e2e gate, version bump protocol, AI-assisted workflow | Understanding the end-to-end dev process |
| **[docs/E2E_TESTING.md](E2E_TESTING.md)** | Layer model, cost sub-markers, run commands, isolation patterns, roadmap to contract/replay layer | Running or extending e2e tests; cost control |
| **[docs/AGENT_UI_E2E.md](AGENT_UI_E2E.md)** | Deterministic e2e runbook for the Agentic UI image path (force-agent trigger, validation ledger) | Live-verifying Agentic-UI features |
| **[docs/GITHUB.md](GITHUB.md)** | Maintainer PR triage protocol, forked PR handling, SonarCloud scenarios, workflow security gates | Reviewing or merging a GitHub PR; adding or changing a workflow |
| [docs/sonar-cleanup-tracker.md](sonar-cleanup-tracker.md) | SonarCloud zero-smells cleanup tracker (`chore/sonar-zero-cleanup`) | Resuming or auditing the Sonar cleanup effort |
| [docs/medium_tutorial.md](medium_tutorial.md) | Long-form tutorial article (Medium draft): Veo + Imagen from the terminal | Writing or updating outreach/tutorial content |
| [.env.template](../.env.template) | All environment variables with defaults | Setting up a new shell or container |
| **[docs/AUTHENTICATION.md](AUTHENTICATION.md)** | Full auth flow, session storage, multi-account, refresh | First `gflow auth login`, or auth errors |
| **[docs/CONFIGURATION.md](CONFIGURATION.md)** | All env vars, precedence chain, default paths per OS | Tuning behaviour, picking output paths |
| **[docs/EXTERNAL_STORAGE.md](EXTERNAL_STORAGE.md)** | S3, MinIO, and Google Cloud Storage output configuration | Sending generated assets to a bucket instead of local disk |
| **[docs/USER_GUIDE.md](USER_GUIDE.md)** | Task-oriented walkthroughs (first setup, batch video, multi-image, log forensics, recovery, multi-account, migration) | You want to GET SOMETHING DONE — not look up a flag |
| **[docs/USAGE.md](USAGE.md)** | Command-by-command reference, manifest format, recipes, exit-code table | Day-to-day CLI use — look up specific commands / flags |
| **[docs/ARCHITECTURE.md](ARCHITECTURE.md)** | Modular monolith, per-worker Page pool, RFC 9457 Problem Details, retry layer | Adding a feature or a new provider |
| **[docs/SECURITY.md](SECURITY.md)** | What secrets are stored where, threat model, hardening | Audit, code review, multi-user machines |
| **[docs/DATA_LAYER.md](DATA_LAYER.md)** | Local SQLite catalog: goals, schema, recording flow, redaction, `gflow data` CLI, migrations, extension guide | Anything touching `gflow_cli.data`, debugging missing rows, building I2V/repair tooling, auditing what is stored |
| **[docs/CHARACTER.md](CHARACTER.md)** | Characters feature spec & system design: domain model, endpoint/cost matrix, sequence diagrams, JSON payloads (I/O), CLI surface, reuse via `referenceEntities` (#145) | Working on `gflow character`, reusing a character in generations, or understanding Flow's character wire protocol |
| **[docs/REFERENCE_STRATEGIES.md](REFERENCE_STRATEGIES.md)** | How to reference an asset: `@Name` mention vs `--reference-entity` vs `--ref` — the two-axis model (identity vs image), same-wire+dedupe rule, per-path support matrix, image-less-entity prerequisite, agent rule | Deciding how to tag a saved character/asset in a prompt, or wiring `@`-mentions into a generation |
| **[docs/MEDIA_LIBRARY.md](MEDIA_LIBRARY.md)** | Flow's media library as a component: asset-kind taxonomy (`media[]`/`workflows[]`/preset voices), UUID-vs-displayName identity rules (AI caption ≤~35 chars, mutable, async, non-unique), the `projectInitialData` listing endpoint (complete, direct-GET-able), picker UI facts (virtualized grid, per-picker project, no a11y tree, single-click attach) + full pitfall table | Touching any picker/listing/reference code, or debugging why an asset "isn't found" |
| **[docs/MOVIE.md](MOVIE.md)** | `gflow movie` — multi-scene character-consistent films: manifest format, run lifecycle (browser stays open through generate→poll→download), entity-attach mechanism (Personagens right-click), resume/handoff, credits | Working on `gflow movie`, debugging scene generation, or understanding the character-entity attach + consistency model |
| **[docs/TOOLS.md](TOOLS.md)** | Tools framework: the `gflow tools list/show/run` group, the `--tool/-t` option on generation commands, the TOML tool schema, how a tool is defined, "My Tools" user-authored tools, MCP exposure + §61 parity | Working on `gflow_cli.tools`, adding a built-in or user tool, or wiring `--tool` into a command |
| **[docs/INSTRUCTIONS.md](INSTRUCTIONS.md)** | Agent Instructions: project-brief cards for Flow's Agent Mode — the mechanism (enabled cards steer generation via the reasoning path), ephemeral `-i` vs persistent cards, the `gflow instructions` CRUD surface with the single generic `--ref` (image **or** character), and the 3-layer pipeline | Working on `-i`/agent instructions, the `gflow instructions` group, or understanding how the project brief anchors generations |
| **[docs/MCP.md](MCP.md)** | MCP server: `gflow mcp run` (stdio) + `gflow serve` (Streamable HTTP, SSE deprecated), the tools/prompts/resources surface, the CLI↔MCP parity contract (AGENTS.md "MCP & CLI Schema Symmetry", enforced by `tests/mcp/test_cli_parity.py`), security model, client setup | Configuring an MCP client, working on `gflow_cli.mcp`, or debugging the stdio/HTTP transports |
| **[docs/PROMPT_EXPANSION.md](PROMPT_EXPANSION.md)** | The `creative-director` tool: 5-component formula, 15 domain styles, banned-keyword policy, Gemini endpoint + I/O, never-fatal contract, `expanded_prompt` + `metadata_json.tool` provenance/redaction, config | Using or debugging `--tool creative-director`, editing the tool TOML, or auditing recorded prompt provenance |
| **[docs/IMAGE_UPSCALE_RECON.md](IMAGE_UPSCALE_RECON.md)** | `gflow image upscale` wire protocol: `upsampleImage` endpoint, full clientContext (projectId/sessionId/tool/userPaygateTier), reCAPTCHA action `IMAGE_GENERATION`, inline base64 response, 4K Ultra-gating, REST-is-dead proof | Working on `gflow image upscale`, debugging the 403/reCAPTCHA path, or understanding Flow's image-upscale wire protocol (#171) |
| **[docs/AGENT_UI_RECON.md](AGENT_UI_RECON.md)** | Agentic Flow UI cohort: classic-vs-agentic DOM signature, server-side + volatile (flapping) A/B gating (no client-readable flag), why `_exit_agent_mode` fails, runtime-DOM-detection recommendation | Touching `_exit_agent_mode` / composer mode-switch, diagnosing exit 23 selector drift, or understanding Flow's agentic-UI A/B (#183/#174) |
| **[docs/ASSET_TAGGING_RECON.md](ASSET_TAGGING_RECON.md)** | `@`-mention asset tagging research & integration design: what Flow's `@CharacterName`/`@me`/`@ingredient` tags are, how they map onto the already-verified `referenceEntities`/`referenceImages` wire, the H1-vs-H2 serialization hypothesis, the CLI-side mention-resolver proposal, and the pre-implementation capture spike plan | Working on inline `@` mentions in prompts, `video --character` reuse, or planning the mention-capture spike |
| **[tasks/lessons.md](../tasks/lessons.md)** | Running notebook of patterns + reviewer findings, dated and traced to commits | Starting a new phase; debugging "why did the council flag this?" |
| **[skills/README.md](../skills/README.md)** | Installable agent skill docs (gflow-cli, predict, pr-council-review, scenario) — cross-tool portable Markdown consumed by Claude Code, Cursor, Codex, Antigravity, Aider, etc. | Any agent wanting to use gflow-cli correctly |
| **[scripts/dev/skillopt/README.md](../scripts/dev/skillopt/README.md)** | SkillOpt mock harness — rollout→score loop for measuring and improving skill doc accuracy across multiple LLM providers | Measuring a skill edit's impact; comparing Claude vs GPT-4o vs Gemini on gflow tasks |
| **[scripts/diag/README.md](../scripts/diag/README.md)** | Diagnostic investigation scripts — run against a live authenticated profile to capture wire samples, measure Chrome memory, or mint reCAPTCHA tokens | Running a one-off investigation against a live Flow session; establishing baseline measurements for issue #155 |
| [docs/superpowers/specs/2026-07-04-pr-triage-autopilot-design.md](superpowers/specs/2026-07-04-pr-triage-autopilot-design.md) | PR-triage autopilot design spec: Stage 0/1 gate, Docker sandboxing, Telegram and audit ledger | Reviewing or auditing the automated PR-triage setup |
| [docs/superpowers/plans/2026-07-08-pr-triage-autopilot/PLAN.md](superpowers/plans/2026-07-08-pr-triage-autopilot/PLAN.md) | Implementation plan for the PR-Triage Autopilot | Tracking task-by-task execution |
| [docs/superpowers/specs/2026-07-18-asset-tagging-design.md](superpowers/specs/2026-07-18-asset-tagging-design.md) | Asset-tagging (`@`-mention) design spec: mention grammar, resolution contract, Option-B architecture, error taxonomy, spike gate | Reviewing or implementing the `@`-mention feature |
| [docs/superpowers/plans/2026-07-18-asset-tagging/PLAN.md](superpowers/plans/2026-07-18-asset-tagging/PLAN.md) | Task-by-task implementation plan for asset tagging (spike gate → resolver → CLI/MCP → live e2e) | Tracking task-by-task execution of the `@`-mention feature |
| [docs/superpowers/specs/2026-07-19-live-verify-design.md](superpowers/specs/2026-07-19-live-verify-design.md) | `/gflow:live-verify` design spec: pre-flight state check + per-feature live-verification gate generalizing release step 4b, council-reviewed | Reviewing or implementing the live-verification enforcement skill |
| [docs/superpowers/plans/2026-07-19-live-verify/PLAN.md](superpowers/plans/2026-07-19-live-verify/PLAN.md) | Task-by-task implementation plan for `/gflow:live-verify` (skill file → AGENTS.md/check.md wiring → INDEX row) | Tracking task-by-task execution of the live-verify skill |
| [docs/superpowers/plans/2026-07-27-tier-aware-credit-confirmations/SCENARIO.md](superpowers/plans/2026-07-27-tier-aware-credit-confirmations/SCENARIO.md) | Edge-case matrix for replacing fixed chain/movie credit estimates with truthful pending-operation guidance | Reviewing the financial-safety, resume, dry-run, and compatibility scenarios |
| [docs/superpowers/plans/2026-07-27-tier-aware-credit-confirmations/PLAN.md](superpowers/plans/2026-07-27-tier-aware-credit-confirmations/PLAN.md) | TDD implementation plan for tier-aware chain/movie planning and confirmation output | Tracking the isolated runtime pricing-guidance bugfix |
| [docs/superpowers/specs/2026-08-21-selector-registry-design.md](superpowers/specs/2026-08-21-selector-registry-design.md) | Selector registry + drift probe design spec: measured one-cookie auth evidence, data model, grading semantics, CI probe, risk register | Reviewing the registry/probe design or auditing its evidence |
| [docs/superpowers/plans/2026-08-21-selector-registry.md](superpowers/plans/2026-08-21-selector-registry.md) | Task-by-task TDD plan for the selector registry + CI probe (executed — PR #563) | Auditing how the registry/probe was built |
| [docs/superpowers/plans/2026-08-25-workflow-hardening-gaps.md](superpowers/plans/2026-08-25-workflow-hardening-gaps.md) | Spec + decisions + TDD plan for #568 — why the pin is derived from `ci.yml`, why the injection ban stays blanket, why the property tests are kept | Bumping zizmor, or re-opening "should these tests exist" |

## Agent commands

Slash commands for Claude Code, stored in `.claude/commands/gflow/`. All prefixed `/gflow:` to signal project scope and avoid colliding with Claude Code built-ins or user-global commands.

| Command | Purpose | Call when… |
|---|---|---|
| `/gflow:check` | Hygiene + auto-fix lint/format + type/test report | Before every commit |
| `/gflow:status [feature]` | Full state: active plan, progress, next unchecked task | Starting a session; after completing a task |
| `/gflow:next [feature]` | Next unchecked task only — no context noise | Quick "what do I do right now?" |
| `/gflow:active` | Which plan is active and its goal — no task detail | Before predict/scenario; quick orientation |
| `/gflow:plan <feature>` | Create a task-by-task implementation plan → writes `docs/superpowers/plans/` | After predict GO/CAUTION; when a backlog item needs a concrete breakdown |
| `/gflow:known-issues` | Surface open and mitigated issues | Before touching auth, reCAPTCHA, or previously-flagged code |
| `/gflow:changelog` | Show `[Unreleased]` entries + last tagged release | Need a quick picture of recent work |
| `/gflow:release` | Full release flow (calls `/gflow:changelog` + `/gflow:check`) | Cutting a new version |
| `/gflow:predict <proposal>` | 5-persona pre-implementation analysis → GO / CAUTION / STOP | Before any high-stakes design decision (new transport, auth change, selector redesign, schema migration) |
| `/gflow:scenario <feature>` | 12-dimension edge-case explorer → severity-ranked scenario table + BDD skeleton | After predict GO/CAUTION; before `/gflow:plan` |
| `/gflow:pr-council-review [PR#]` | Multi-dimensional council review of an open PR (5 baseline + adaptive dimensions) | Before merging any non-trivial PR; mandatory for auth/transport/data changes |
| `/gflow:branch-review` | Same council review run against the current local feature branch (no PR needed) | Pre-PR self-audit; after predict/scenario on a high-stakes branch |
| `/gflow:llm-council [PR#\|--base <ref>] [--tier small\|medium\|high]` | Wraps `pr-council-review` with external CLI reviewers (`codex` + Antigravity `agy`) for cross-model corroboration | High-stakes reviews where a same-model-family blind spot is a real risk |
| `/gflow:doc-review` | Systematic council-driven audit of documentation completeness and drift | Before cutting a release; after major documentation changes |

**Governance:** commands are executable docs — they decay like any doc. When a phase advances or a file path changes, update the relevant command in the same commit. `/gflow:release` includes a staleness review step.

## Topic shortcuts

**"What's the governance flow, and which paths require predict/council?"** → [AGENT_GUIDE § Governance & Enforcement](AGENT_GUIDE.md#governance--enforcement)
**"Why did the materiality advisory flag my PR?"** → [AGENT_GUIDE § Materiality coverage](AGENT_GUIDE.md#materiality-coverage-path--recommended-gate)
**"I just installed gflow — how do I get to my first video?"** → [USER_GUIDE § Journey 1](USER_GUIDE.md#journey-1--first-time-setup-10-minutes)
**"How do I render 20 clips overnight?"** → [USER_GUIDE § Journey 3](USER_GUIDE.md#journey-3--batch-video-from-a-prompt-list-shell-loop)
**"How much will this batch cost me in Veo credits?"** → [USER_GUIDE § Journey 10](USER_GUIDE.md#journey-10--budgeting-credits-before-a-batch-run)
**"How do I feed gflow outputs into ffmpeg / a pipeline?"** → [USER_GUIDE § Journey 11](USER_GUIDE.md#journey-11--wiring-gflow-outputs-into-a-downstream-pipeline)
**"How do I chain clips into one continuous video (initial-frame I2V)?"** → [USAGE § `gflow video chain`](USAGE.md#gflow-video-chain)
**"How do I stitch existing clips into one extended .mp4 (credit-free, no ffmpeg)?"** → [USAGE § `gflow scene`](USAGE.md#gflow-scene)
**"How do I create a reusable character (face + body) I can reuse across generations?"** → [USAGE § `gflow character`](USAGE.md#gflow-character) · design: [CHARACTER](CHARACTER.md)
**"Should I use an `@mention`, `--reference-entity`, or `--ref` to tag an asset?"** → [REFERENCE_STRATEGIES](REFERENCE_STRATEGIES.md) — saved named identity → `@Name`/`--reference-entity`; one-off image → `--ref`
**"How does Flow's media library / picker actually behave (names, tabs, scrolling, listing)?"** → [MEDIA_LIBRARY](MEDIA_LIBRARY.md) — identity = UUID, names are async AI captions; grid is virtualized; full pitfall table
**"How do I turn a terse prompt into a vivid one before generating?"** → `--tool creative-director:style=cinema` on any generation command — see [PROMPT_EXPANSION](PROMPT_EXPANSION.md) · framework: [TOOLS](TOOLS.md)
**"What tools are available and what styles do they support?"** → run `gflow tools list` / `gflow tools show creative-director` — see [TOOLS](TOOLS.md)
**"How do I make Flow's agent follow a style/reference across a project's generations?"** → agent instruction cards — ephemeral `-i "text"` (shipped) or the persistent `gflow instructions` group with a generic `--ref` (image or character), see [INSTRUCTIONS](INSTRUCTIONS.md)
**"What preset voices can a character use?"** → run `gflow character voices` — see [USAGE § `gflow character voices`](USAGE.md#gflow-character-voices)
**"My batch run died with exit code 3 (auth) — what now?"** → [USER_GUIDE § Journey 7](USER_GUIDE.md#journey-7--recovering-from-an-authexpirederror-mid-run)
**"Exit code 4 (rate-limit) or 5 (content-policy) — how do I recover?"** → [USER_GUIDE § Journey 12](USER_GUIDE.md#journey-12--recovering-from-contentpolicyerror-or-ratelimiterror)
**"How do I read the structured log (`error_raised` events)?"** → [USER_GUIDE § Journey 6](USER_GUIDE.md#journey-6--reading-structured-logs-jq-recipes)
**"What exit code does shell branching see for each error class?"** → [USAGE § Exit codes](USAGE.md#exit-codes)
**"Is `v0.5.0a1` a prerelease, and how do I cut a full release?"** → [RELEASE § Prerelease Versus Full Release](../RELEASE.md#prerelease-versus-full-release)
**"Where is my session stored?"** → [AUTHENTICATION § Session storage](AUTHENTICATION.md#session-storage)
**"Where do generated files land?"** → [CONFIGURATION § Output paths](CONFIGURATION.md#output-paths)
**"How do I write outputs to S3, MinIO, or Google Cloud Storage?"** → [EXTERNAL_STORAGE](EXTERNAL_STORAGE.md) and [CONFIGURATION § `GFLOW_CLI_STORAGE_URI`](CONFIGURATION.md#gflow_cli_storage_uri)
**"How do I run with multiple Google accounts?"** → [AUTHENTICATION § Multiple accounts](AUTHENTICATION.md#multiple-accounts)
**"How do I know which Google account a profile is signed into?"** → [AUTHENTICATION § gflow auth list](AUTHENTICATION.md#gflow-auth-list)
**"Why is my profile named 'default' and how do I rename it?"** → [AUTHENTICATION § Profile naming](AUTHENTICATION.md#profile-naming)
**"How does login verification work without launching a browser?"** → [AUTHENTICATION § Session verification](AUTHENTICATION.md#session-verification-cookie-store-fast-path) (cookie-store fast path, v0.17.0)
**"How does the layered structure work?"** → [ARCHITECTURE § Layers](ARCHITECTURE.md#layers)
**"What env var should I set for X?"** → [CONFIGURATION § Reference](CONFIGURATION.md#reference)
**"What does gflow remember after a generation finishes?"** → [DATA_LAYER § What is recorded](DATA_LAYER.md#what-is-recorded)
**"How do I see what's in my gflow catalog?"** → run `gflow data list projects` (or `images` / `videos` / `profiles`) — see [DATA_LAYER § Querying the data layer](DATA_LAYER.md#querying-the-data-layer)
**"Where can I look up a media ID I generated yesterday?"** → [DATA_LAYER § Querying the data layer](DATA_LAYER.md#querying-the-data-layer)
**"How do I stop gflow from storing my prompts?"** → [DATA_LAYER § Privacy and redaction](DATA_LAYER.md#privacy-and-redaction)
**"What does exit code 16 mean and how do I recover?"** → [DATA_LAYER § Persistence-failure handling](DATA_LAYER.md#persistence-failure-handling)
**"Is my install/catalog healthy?"** → run `gflow doctor` (read-only, never migrates; exit 33 = findings present) — see [USAGE § `gflow doctor`](USAGE.md#gflow-doctor)
**"My old assets re-upload / have no names?"** → run `gflow data sync --names` (credit-free name backfill from Flow's listing) — see [USAGE § `gflow data sync`](USAGE.md#gflow-data-sync)
**"How do I use this project's skills in Cursor / Codex / Antigravity / Aider?"** → [skills/README.md](../skills/README.md#use-with-other-agents)
**"How do I benchmark a skill doc against real tasks?"** → [scripts/dev/skillopt/README.md](../scripts/dev/skillopt/README.md)
**"How do I compare Claude vs GPT-4o vs Gemini on gflow tasks?"** → `python scripts/dev/skillopt/harness.py --provider openai --model gpt-4o` (see [skillopt README](../scripts/dev/skillopt/README.md))
**"What does Chrome actually use in RAM during a generation?"** → run `uv run python scripts/diag/memory_profile.py --profile NAME` (see [scripts/diag/README.md](../scripts/diag/README.md))
**"How do I report a security issue?"** → [SECURITY § Reporting](SECURITY.md#reporting)
**"What branch do I work on? How do I name it?"** → [DEVELOPMENT § Branching model](DEVELOPMENT.md#branching-model)
**"What secures our GitHub Actions workflows?"** → [GITHUB § Workflow Security Gates](GITHUB.md#workflow-security-gates)
**"How do I handle an external GitHub PR?"** → [GITHUB § Scenario Matrix](GITHUB.md#scenario-matrix)
**"What automation runs on external PRs?"** → [GITHUB § Automated External PR Triage](GITHUB.md#automated-external-pr-triage)
**"How do we use Copilot review on PRs?"** → [GITHUB § GitHub Copilot Code Review](GITHUB.md#github-copilot-code-review)
**"Why did SonarCloud skip or fail on a forked PR?"** → [GITHUB § Forked PRs And SonarCloud](GITHUB.md#forked-prs-and-sonarcloud)
**"How do I run e2e tests before a release?"** → [DEVELOPMENT § E2e gate](DEVELOPMENT.md#e2e-gate-before-merging-develop--main)
**"What does each e2e marker cost? How do I run only the cheap tests?"** → [E2E_TESTING § Run commands](E2E_TESTING.md#run-commands)
**"Has Flow drifted since the last release? What is the nightly canary telling me?"** → [E2E_TESTING § Nightly canary](E2E_TESTING.md#nightly-canary-502)
**"Which Flow selector moved? What is the CI selector probe telling me (exit 0/1/2)?"** → [E2E_TESTING § Selector drift probe](E2E_TESTING.md#selector-drift-probe-563)
**"When does the version get bumped?"** → [DEVELOPMENT § Version bump protocol](DEVELOPMENT.md#version-bump-protocol)
**"How do I embed FlowApiClient in a long-lived worker / service?"** → [USER_GUIDE § Journey 14](USER_GUIDE.md#journey-14--embedding-flowapiclient-in-a-long-lived-worker)
**"What's the standard way to import gflow errors in my code?"** → [USAGE § Programmatic use](USAGE.md#programmatic-use)
**"gflow refuses to open my profile after a downgrade (exit 11, 'written by a newer Chromium')"** → [AUTHENTICATION § Chromium downgrade guard](AUTHENTICATION.md#chromium-downgrade-guard)
**"Two gflow runs on the same profile — can the second one wait instead of failing (exit 11)?"** → [CONFIGURATION § GFLOW_CLI_LEASE_WAIT_SECONDS](CONFIGURATION.md#gflow_cli_lease_wait_seconds) (v0.56.0 opt-in bounded wait)
**"How do I silence the 'newer version on PyPI' stderr notice?"** → [CONFIGURATION § GFLOW_CLI_UPDATE_CHECK](CONFIGURATION.md#gflow_cli_update_check) (`0` disables; auto-skipped in CI and for editable installs)
**"How do I run the MCP server so an agent can't spend credits?"** → [CONFIGURATION § GFLOW_MCP_NO_SPEND](CONFIGURATION.md#gflow_mcp_no_spend) + [MCP § Option A2](MCP.md#option-a2-read-only-server) (`gflow mcp run --no-spend` / `GFLOW_MCP_NO_SPEND=1`; v0.57.0 — both generate tools go unregistered, not merely refused)
**"Which Flow UI arm does a video run require, and why did it exit 2 or 28?"** → [CONFIGURATION § GFLOW_CLI_UI_MODE](CONFIGURATION.md#gflow_cli_ui_mode) (v0.57.0 — video is classic-only: `auto` ≡ `classic`, explicit `agentic` exits 2, unreachable classic aborts pre-submit with exit 28, $0)
**"A gflow command hangs / fails — where do I start?"** → [DEBUGGING § Quick reference](DEBUGGING.md#quick-reference)
**"A command failed — where is the incident bundle and what's in it?"** → [DEBUGGING § Automatic incident bundles](DEBUGGING.md#automatic-incident-bundles) (layout, triggers, review-before-sharing); privacy boundaries: [SECURITY § Automatic incident bundles](SECURITY.md#automatic-incident-bundles-gflow_cli_incident_capture-default-on); disable via [CONFIGURATION § GFLOW_CLI_INCIDENT_CAPTURE](CONFIGURATION.md#gflow_cli_incident_capture)
**"Flow's UI broke a selector — how do I diagnose it?"** → [DEBUGGING § Inspecting Flow's live UI](DEBUGGING.md#inspecting-flows-live-ui)
**"What does each `ui_automation.*` log event mean?"** → [DEBUGGING § Listener & HTTP-layer debugging](DEBUGGING.md#listener--http-layer-debugging)
**What was actually live-verified for the latest release?** → latest: [LIVE_VERIFICATION_v0.61.0](LIVE_VERIFICATION_v0.61.0.md) (**#539 video-model refusal + #586 image-model refusal and server-side attribution, verified 2026-08-26/27 on profile `denon82`, ONE Veo generation total** — the video fix was proven by a **zero-credit A/B against the stashed pre-fix source running on live Flow**: the old code returned SELECTED for `veo-lite-lp`, a model Flow does not offer to this account, and for a deliberately ambiguous selector matching 3 entries; the new code refuses both with exit 18 naming what Flow offered. Model selection happens before submit, so refusals cost nothing and the A/B was free. The happy path still generates: `--model omni-flash` → exit 0, a real 2.2 MB `ftypisom` mp4, catalog recording `omni_flash`. Two items are recorded as NOT verified rather than omitted: three of #584's four navigation-settle sites were never exercised [no bearer refresh occurred in these runs], and #582's canary self-re-exec has its first real exercise tonight. Also falsified: #539's recorded note that the video picker uses a different trigger — the two constants are byte-identical strings, and the repeated empty menu captures were the capture's own fault.) · previous: [LIVE_VERIFICATION_v0.60.0](LIVE_VERIFICATION_v0.60.0.md)
**"Was the playwright upper bound raised, and on what evidence?"** → [LIVE_VERIFICATION_playwright_1.61](LIVE_VERIFICATION_playwright_1.61.md) (**`>=1.59.0,<1.60.0` → `>=1.61.0,<1.62.0` verified 2026-08-05, 1 Imagen + 1 Veo credit** — the 2026-08-03 regression that motivated the bound does NOT reproduce on 1.61.0: a live `i2v` drove the full chain with `image_uploaded status=200` → `frame_attached` → `generate_captured status=200` with `startImage` parsed, where **1.62.0 hung silently at exactly that upload step**; live `i2i` local-ref attach passed outright [96.7s]. **1.62.0 stays excluded** — never root-caused. Also found, A/B-proven pre-existing on BOTH 1.59.0 and 1.61.0: Flow dropped the duration-tab UI, so **`--duration` is currently broken for i2v** — `UiSelectorDriftError` fires correctly [refusing rather than silently accepting Flow's default, #288] but the selector needs re-deriving; that is why the i2v proof was driven via the CLI without `--duration`, and Flow's own server-side `PUBLIC_ERROR_VIDEO_GENERATION_TIMED_OUT` means no finished mp4 is claimed).
**"Where is the reverse-engineered wire protocol for a feature?"** → the `*_RECON.md` design docs: [CHARACTER_RECON](CHARACTER_RECON.md) (Flow character entity protocol), [IMAGE_UPSCALE_RECON](IMAGE_UPSCALE_RECON.md) (`/v1/flow/upsampleImage` wire). Naming convention: one `<FEATURE>_RECON.md` per reverse-engineered surface, kept as the durable spec after the feature ships. Pre-capture research recons: [ASSET_TAGGING_RECON](ASSET_TAGGING_RECON.md) (`@`-mention asset tagging — shipped in v0.40.0, see [LIVE_VERIFICATION_v0.40.0](LIVE_VERIFICATION_v0.40.0.md)).
**"What was live-verified for the data layer (PR #58)?"** → [LIVE_VERIFICATION_data_layer](LIVE_VERIFICATION_data_layer.md) — 1 Imagen + 1 Veo credit on denon82, 6-layer ledger (file + magic + Pillow + DB rows + CLI round-trip + structlog)
**"What was live-verified for the video-download feature (#29)?"** → [LIVE_VERIFICATION_video_download](LIVE_VERIFICATION_video_download.md)
**"What is the jitter matrix evidence for `gflow image batch`?"** → [`LIVE_VERIFICATION_image_batch.md`](LIVE_VERIFICATION_image_batch.md) — jitter matrix evidence for `gflow image batch` (always-same-project mode)

## Documentation governance

- `README.md` is for first-time visitors. Keep it under ~400 lines. Anything longer belongs in `docs/`.
- Every new behaviour or env var needs a row in this index AND a section in the relevant `docs/*.md`.
- Cross-link generously. Use markdown link syntax with an anchor wherever a reader might need to jump deeper — they should never get stuck.
- Keep file size sane. If `docs/USAGE.md` grows past ~600 lines, split into `docs/USAGE/<topic>.md`.
