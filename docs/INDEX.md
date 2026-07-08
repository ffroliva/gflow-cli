# Documentation Index

Welcome to the `gflow-cli` documentation. This index is the routing layer: it tells you where each topic lives. Keep `README.md` slim (high-level overview + install) and document deep details here.

| Doc | Purpose | Read when… |
|---|---|---|
| [README](../README.md) | Project overview, install, quick start | First time landing on the repo |
| [docs/DEMOS.md](DEMOS.md) | Gallery of `gflow` in action (terminal + split-screen Flow recordings) | You want to see what gflow looks like running |
| [AGENTS.md](../AGENTS.md) | Universal coding-agent spec — Cursor / Codex / Aider / Gemini CLI / Jules / etc. | Any AI coding agent enters the repo |
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
| **[docs/GITHUB.md](GITHUB.md)** | Maintainer PR triage protocol, forked PR handling, SonarCloud scenarios | Reviewing or merging a GitHub PR |
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
| **[docs/MOVIE.md](MOVIE.md)** | `gflow movie` — multi-scene character-consistent films: manifest format, run lifecycle (browser stays open through generate→poll→download), entity-attach mechanism (Personagens right-click), resume/handoff, credits | Working on `gflow movie`, debugging scene generation, or understanding the character-entity attach + consistency model |
| **[docs/TOOLS.md](TOOLS.md)** | Tools framework: the `gflow tools list/show/run` group, the `--tool/-t` option on generation commands, the TOML tool schema, how a tool is defined, "My Tools" user-authored tools, MCP exposure + §61 parity | Working on `gflow_cli.tools`, adding a built-in or user tool, or wiring `--tool` into a command |
| **[docs/MCP.md](MCP.md)** | MCP server: `gflow mcp run` (stdio) + `gflow serve` (HTTP/SSE), the tools/prompts/resources surface, CLI↔MCP §61 parity, security model, client setup | Configuring an MCP client, working on `gflow_cli.mcp`, or debugging the stdio/SSE transports |
| **[docs/PROMPT_EXPANSION.md](PROMPT_EXPANSION.md)** | The `creative-director` tool: 5-component formula, 15 domain styles, banned-keyword policy, Gemini endpoint + I/O, never-fatal contract, `expanded_prompt` + `metadata_json.tool` provenance/redaction, config | Using or debugging `--tool creative-director`, editing the tool TOML, or auditing recorded prompt provenance |
| **[docs/IMAGE_UPSCALE_RECON.md](IMAGE_UPSCALE_RECON.md)** | `gflow image upscale` wire protocol: `upsampleImage` endpoint, full clientContext (projectId/sessionId/tool/userPaygateTier), reCAPTCHA action `IMAGE_GENERATION`, inline base64 response, 4K Ultra-gating, REST-is-dead proof | Working on `gflow image upscale`, debugging the 403/reCAPTCHA path, or understanding Flow's image-upscale wire protocol (#171) |
| **[docs/AGENT_UI_RECON.md](AGENT_UI_RECON.md)** | Agentic Flow UI cohort: classic-vs-agentic DOM signature, server-side + volatile (flapping) A/B gating (no client-readable flag), why `_exit_agent_mode` fails, runtime-DOM-detection recommendation | Touching `_exit_agent_mode` / composer mode-switch, diagnosing exit 23 selector drift, or understanding Flow's agentic-UI A/B (#183/#174) |
| **[tasks/lessons.md](../tasks/lessons.md)** | Running notebook of patterns + reviewer findings, dated and traced to commits | Starting a new phase; debugging "why did the council flag this?" |
| **[skills/README.md](../skills/README.md)** | Installable agent skill docs (gflow-cli, predict, pr-council-review, scenario) — cross-tool portable Markdown consumed by Claude Code, Cursor, Codex, Gemini CLI, Aider, etc. | Any agent wanting to use gflow-cli correctly |
| **[scripts/dev/skillopt/README.md](../scripts/dev/skillopt/README.md)** | SkillOpt mock harness — rollout→score loop for measuring and improving skill doc accuracy across multiple LLM providers | Measuring a skill edit's impact; comparing Claude vs GPT-4o vs Gemini on gflow tasks |
| **[scripts/diag/README.md](../scripts/diag/README.md)** | Diagnostic investigation scripts — run against a live authenticated profile to capture wire samples, measure Chrome memory, or mint reCAPTCHA tokens | Running a one-off investigation against a live Flow session; establishing baseline measurements for issue #155 |

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
| `/gflow:doc-review` | Systematic council-driven audit of documentation completeness and drift | Before cutting a release; after major documentation changes |

**Governance:** commands are executable docs — they decay like any doc. When a phase advances or a file path changes, update the relevant command in the same commit. `/gflow:release` includes a staleness review step.

## Topic shortcuts

**"What's the governance flow, and which paths require predict/council?"** → [AGENT_GUIDE § Governance & Enforcement](AGENT_GUIDE.md#governance--enforcement)
**"Why did the materiality advisory flag my PR?"** → [AGENT_GUIDE § Materiality coverage](AGENT_GUIDE.md#materiality-coverage-path--recommended-gate)
**"I just installed gflow — how do I get to my first video?"** → [USER_GUIDE § Journey 1](USER_GUIDE.md#journey-1--first-time-setup-10-minutes)
**"How do I render 20 clips overnight with concurrency?"** → [USER_GUIDE § Journey 3](USER_GUIDE.md#journey-3--batch-video-with-concurrency)
**"How much will this batch cost me in Veo credits?"** → [USER_GUIDE § Journey 10](USER_GUIDE.md#journey-10--budgeting-credits-before-a-batch-run)
**"How do I feed gflow outputs into ffmpeg / a pipeline?"** → [USER_GUIDE § Journey 11](USER_GUIDE.md#journey-11--wiring-gflow-outputs-into-a-downstream-pipeline)
**"How do I chain clips into one continuous video (initial-frame I2V)?"** → [USAGE § `gflow video chain`](USAGE.md#gflow-video-chain)
**"How do I stitch existing clips into one extended .mp4 (credit-free, no ffmpeg)?"** → [USAGE § `gflow scene`](USAGE.md#gflow-scene)
**"How do I create a reusable character (face + body) I can reuse across generations?"** → [USAGE § `gflow character`](USAGE.md#gflow-character) · design: [CHARACTER](CHARACTER.md)
**"How do I turn a terse prompt into a vivid one before generating?"** → `--tool creative-director:style=cinema` on any generation command — see [PROMPT_EXPANSION](PROMPT_EXPANSION.md) · framework: [TOOLS](TOOLS.md)
**"What tools are available and what styles do they support?"** → run `gflow tools list` / `gflow tools show creative-director` — see [TOOLS](TOOLS.md)
**"What preset voices can a character use?"** → run `gflow character voices` — see [USAGE § `gflow character voices`](USAGE.md#gflow-character-voices)
**"My batch died with exit code 3 (auth) — what now?"** → [USER_GUIDE § Journey 7](USER_GUIDE.md#journey-7--recovering-from-an-authexpirederror-mid-batch)
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
**"How do I use this project's skills in Cursor / Codex / Gemini CLI / Aider?"** → [skills/README.md](../skills/README.md#use-with-other-agents)
**"How do I benchmark a skill doc against real tasks?"** → [scripts/dev/skillopt/README.md](../scripts/dev/skillopt/README.md)
**"How do I compare Claude vs GPT-4o vs Gemini on gflow tasks?"** → `python scripts/dev/skillopt/harness.py --provider openai --model gpt-4o` (see [skillopt README](../scripts/dev/skillopt/README.md))
**"What does Chrome actually use in RAM during a generation?"** → run `uv run python scripts/diag/memory_profile.py --profile NAME` (see [scripts/diag/README.md](../scripts/diag/README.md))
**"How do I report a security issue?"** → [SECURITY § Reporting](SECURITY.md#reporting)
**"What branch do I work on? How do I name it?"** → [DEVELOPMENT § Branching model](DEVELOPMENT.md#branching-model)
**"How do I handle an external GitHub PR?"** → [GITHUB § Scenario Matrix](GITHUB.md#scenario-matrix)
**"What automation runs on external PRs?"** → [GITHUB § Automated External PR Triage](GITHUB.md#automated-external-pr-triage)
**"How do we use Copilot review on PRs?"** → [GITHUB § GitHub Copilot Code Review](GITHUB.md#github-copilot-code-review)
**"Why did SonarCloud skip or fail on a forked PR?"** → [GITHUB § Forked PRs And SonarCloud](GITHUB.md#forked-prs-and-sonarcloud)
**"How do I run e2e tests before a release?"** → [DEVELOPMENT § E2e gate](DEVELOPMENT.md#e2e-gate-before-merging-develop--main)
**"What does each e2e marker cost? How do I run only the cheap tests?"** → [E2E_TESTING § Run commands](E2E_TESTING.md#run-commands)
**"When does the version get bumped?"** → [DEVELOPMENT § Version bump protocol](DEVELOPMENT.md#version-bump-protocol)
**"How do I embed FlowApiClient in a long-lived worker / service?"** → [USER_GUIDE § Journey 14](USER_GUIDE.md#journey-14--embedding-flowapiclient-in-a-long-lived-worker)
**"What's the standard way to import gflow errors in my code?"** → [USAGE § Programmatic use](USAGE.md#programmatic-use)
**"A gflow command hangs / fails — where do I start?"** → [DEBUGGING § Quick reference](DEBUGGING.md#quick-reference)
**"Flow's UI broke a selector — how do I diagnose it?"** → [DEBUGGING § Inspecting Flow's live UI](DEBUGGING.md#inspecting-flows-live-ui)
**"What does each `ui_automation.*` log event mean?"** → [DEBUGGING § Listener & HTTP-layer debugging](DEBUGGING.md#listener--http-layer-debugging)
**"What was actually live-verified for the latest release?"** → latest: [LIVE_VERIFICATION_v0.28.0](LIVE_VERIFICATION_v0.28.0.md) (**agent instructions `-i` now steer agentic generation — crayon e2e GREEN**: a style-neutral prompt + an enabled "crayon" card produced an unmistakable crayon drawing through the real transport; root causes were conversational phrasing + the `project_brief.enabled` master switch). Prior: [v0.27.1](LIVE_VERIFICATION_v0.27.1.md) (**v0.27.0 release follow-up fixes and documentation sync**). Prior: [v0.27.0](LIVE_VERIFICATION_v0.27.0.md) (**`[style]` block with named variants + prompt-aware resume — credit-free CLI verification**: real `movie run --dry-run` 3-way ledger proving resolved-style display, `skip (done)` on matching `style_hash`, and `re-run (style changed)` after a style edit; composition is local, wire path untouched, so no Veo credits spent — first credited styled run will confirm visually). Prior: [v0.26.0](LIVE_VERIFICATION_v0.26.0.md) (**image i2i by generated-image UUID proven live e2e GREEN** — the existing asset is *selected in place* by its media UUID, no duplicate upload, producing an i2i output that references it; plus generated-image `display_name` capture, credited @C1ph3r404). Prior: [v0.25.0](LIVE_VERIFICATION_v0.25.0.md) (**#237 remote-UUID i2v proven live e2e GREEN** — a real 8s 720×1280 interpolation from a generated image's UUID, after the picker-search attach was reworked to local upload; #240 home-`.env` matrix live; plus two silent-failure guards root-caused live: video-as-image download rejection and rejected-upload fail-loud). Prior: [v0.24.0](LIVE_VERIFICATION_v0.24.0.md) (--project parity across CLI + MCP — automated coverage; live gen auth-gated). Prior: [v0.23.0](LIVE_VERIFICATION_v0.23.0.md) (MCP generation wiring proven live end-to-end — tool→worker→real Flow REST→structured-error translation; final image-write blocked by an expired session, environmental. #222 macOS fix reporter-verified e2e on Apple Silicon). Prior: [v0.22.0](LIVE_VERIFICATION_v0.22.0.md) (Tools framework — automated/CI coverage complete; credit/key-gated live feature run is a pending owner gate, see the doc) · [v0.21.0](LIVE_VERIFICATION_v0.21.0.md) (MCP server over stdio + HTTP/SSE — credit-free protocol handshake; 4 tools / 2 prompts / 3 resources confirmed on stdout) · [v0.20.1](LIVE_VERIFICATION_v0.20.1.md) · [v0.20.0](LIVE_VERIFICATION_v0.20.0.md) · [v0.19.0](LIVE_VERIFICATION_v0.19.0.md) · [v0.18.0](LIVE_VERIFICATION_v0.18.0.md) · [v0.17.0](LIVE_VERIFICATION_v0.17.0.md) · [v0.16.0](LIVE_VERIFICATION_v0.16.0.md) · [v0.13.0](LIVE_VERIFICATION_v0.13.0.md) · [v0.12.0](LIVE_VERIFICATION_v0.12.0.md) · [v0.11.0](LIVE_VERIFICATION_v0.11.0.md) · [v0.10.0](LIVE_VERIFICATION_v0.10.0.md) · [v0.9.1](LIVE_VERIFICATION_v0.9.1.md) · [v0.9.0](LIVE_VERIFICATION_v0.9.0.md) · [v0.8.1](LIVE_VERIFICATION_v0.8.1.md) · [v0.7.0](LIVE_VERIFICATION_v0.7.0.md)
**"Where is the reverse-engineered wire protocol for a feature?"** → the `*_RECON.md` design docs: [CHARACTER_RECON](CHARACTER_RECON.md) (Flow character entity protocol), [IMAGE_UPSCALE_RECON](IMAGE_UPSCALE_RECON.md) (`/v1/flow/upsampleImage` wire). Naming convention: one `<FEATURE>_RECON.md` per reverse-engineered surface, kept as the durable spec after the feature ships.
**"What was live-verified for the data layer (PR #58)?"** → [LIVE_VERIFICATION_data_layer](LIVE_VERIFICATION_data_layer.md) — 1 Imagen + 1 Veo credit on denon82, 6-layer ledger (file + magic + Pillow + DB rows + CLI round-trip + structlog)
**"What was live-verified for the video-download feature (#29)?"** → [LIVE_VERIFICATION_video_download](LIVE_VERIFICATION_video_download.md)
**"What is the jitter matrix evidence for `gflow image batch`?"** → [`LIVE_VERIFICATION_image_batch.md`](LIVE_VERIFICATION_image_batch.md) — jitter matrix evidence for `gflow image batch` (always-same-project mode)

## Documentation governance

- `README.md` is for first-time visitors. Keep it under ~400 lines. Anything longer belongs in `docs/`.
- Every new behaviour or env var needs a row in this index AND a section in the relevant `docs/*.md`.
- Cross-link generously. Use markdown link syntax with an anchor wherever a reader might need to jump deeper — they should never get stuck.
- Keep file size sane. If `docs/USAGE.md` grows past ~600 lines, split into `docs/USAGE/<topic>.md`.
