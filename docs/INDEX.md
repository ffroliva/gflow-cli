# Documentation Index

Welcome to the `gflow-cli` documentation. This index is the routing layer: it tells you where each topic lives. Keep `README.md` slim (high-level overview + install) and document deep details here.

| Doc | Purpose | Read when… |
|---|---|---|
| [README](../README.md) | Project overview, install, quick start | First time landing on the repo |
| [AGENTS.md](../AGENTS.md) | Universal coding-agent spec — Cursor / Codex / Aider / Gemini CLI / Jules / etc. | Any AI coding agent enters the repo |
| [llms.txt](../llms.txt) | LLM-readable summary (llmstxt.org format) | A user pastes context about gflow into ChatGPT / Claude / Gemini |
| [docs/PROJECT_STATUS.md](PROJECT_STATUS.md) | Full milestone history + lifecycle policy | Auditing where the project is in its lifecycle |
| [docs/AGENT_GUIDE.md](AGENT_GUIDE.md) | Mandates and routing rules for AI agents (companion to AGENTS.md) | A coding agent needs the longer non-negotiable rules |
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
| **[docs/GITHUB.md](GITHUB.md)** | Maintainer PR triage protocol, forked PR handling, SonarCloud scenarios | Reviewing or merging a GitHub PR |
| [.env.template](../.env.template) | All environment variables with defaults | Setting up a new shell or container |
| **[docs/AUTHENTICATION.md](AUTHENTICATION.md)** | Full auth flow, session storage, multi-account, refresh | First `gflow auth login`, or auth errors |
| **[docs/CONFIGURATION.md](CONFIGURATION.md)** | All env vars, precedence chain, default paths per OS | Tuning behaviour, picking output paths |
| **[docs/USER_GUIDE.md](USER_GUIDE.md)** | Task-oriented walkthroughs (first setup, batch video, multi-image, log forensics, recovery, multi-account, migration) | You want to GET SOMETHING DONE — not look up a flag |
| **[docs/USAGE.md](USAGE.md)** | Command-by-command reference, manifest format, recipes, exit-code table | Day-to-day CLI use — look up specific commands / flags |
| **[docs/ARCHITECTURE.md](ARCHITECTURE.md)** | Modular monolith, per-worker Page pool, RFC 9457 Problem Details, retry layer | Adding a feature or a new provider |
| **[docs/SECURITY.md](SECURITY.md)** | What secrets are stored where, threat model, hardening | Audit, code review, multi-user machines |
| **[docs/DATA_LAYER.md](DATA_LAYER.md)** | Local SQLite catalog: goals, schema, recording flow, redaction, `gflow data` CLI, migrations, extension guide | Anything touching `gflow_cli.data`, debugging missing rows, building I2V/repair tooling, auditing what is stored |
| **[tasks/lessons.md](../tasks/lessons.md)** | Running notebook of patterns + reviewer findings, dated and traced to commits | Starting a new phase; debugging "why did the council flag this?" |

## Agent commands

Slash commands for Claude Code, stored in `.claude/commands/gflow/`. All prefixed `/gflow:` to signal project scope and avoid colliding with Claude Code built-ins or user-global commands.

| Command | Purpose | Call when… |
|---|---|---|
| `/gflow:check` | Hygiene + auto-fix lint/format + type/test report | Before every commit |
| `/gflow:plan` | Show current phase scope, sequence, and definition of done | Starting a feature or unsure what's in scope |
| `/gflow:known-issues` | Surface open and mitigated issues | Before touching auth, reCAPTCHA, or previously-flagged code |
| `/gflow:changelog` | Show `[Unreleased]` entries + last tagged release | Need a quick picture of recent work |
| `/gflow:release` | Full release flow (calls `/gflow:changelog` + `/gflow:check`) | Cutting a new version |

**Governance:** commands are executable docs — they decay like any doc. When a phase advances or a file path changes, update the relevant command in the same commit. `/gflow:release` includes a staleness review step.

## Topic shortcuts

**"I just installed gflow — how do I get to my first video?"** → [USER_GUIDE § Journey 1](USER_GUIDE.md#journey-1--first-time-setup-10-minutes)
**"How do I render 20 clips overnight with concurrency?"** → [USER_GUIDE § Journey 3](USER_GUIDE.md#journey-3--batch-video-with-concurrency)
**"How much will this batch cost me in Veo credits?"** → [USER_GUIDE § Journey 10](USER_GUIDE.md#journey-10--budgeting-credits-before-a-batch-run)
**"How do I feed gflow outputs into ffmpeg / a pipeline?"** → [USER_GUIDE § Journey 11](USER_GUIDE.md#journey-11--wiring-gflow-outputs-into-a-downstream-pipeline)
**"My batch died with exit code 3 (auth) — what now?"** → [USER_GUIDE § Journey 7](USER_GUIDE.md#journey-7--recovering-from-an-authexpirederror-mid-batch)
**"Exit code 4 (rate-limit) or 5 (content-policy) — how do I recover?"** → [USER_GUIDE § Journey 12](USER_GUIDE.md#journey-12--recovering-from-contentpolicyerror-or-ratelimiterror)
**"How do I read the structured log (`error_raised` events)?"** → [USER_GUIDE § Journey 6](USER_GUIDE.md#journey-6--reading-structured-logs-jq-recipes)
**"What exit code does shell branching see for each error class?"** → [USAGE § Exit codes](USAGE.md#exit-codes)
**"Is `v0.5.0a1` a prerelease, and how do I cut a full release?"** → [RELEASE § Prerelease Versus Full Release](../RELEASE.md#prerelease-versus-full-release)
**"Where is my session stored?"** → [AUTHENTICATION § Session storage](AUTHENTICATION.md#session-storage)
**"Where do generated files land?"** → [CONFIGURATION § Output paths](CONFIGURATION.md#output-paths)
**"How do I run with multiple Google accounts?"** → [AUTHENTICATION § Multiple accounts](AUTHENTICATION.md#multiple-accounts)
**"How does the layered structure work?"** → [ARCHITECTURE § Layers](ARCHITECTURE.md#layers)
**"What env var should I set for X?"** → [CONFIGURATION § Reference](CONFIGURATION.md#reference)
**"What does gflow remember after a generation finishes?"** → [DATA_LAYER § What is recorded](DATA_LAYER.md#what-is-recorded)
**"How do I see what's in my gflow catalog?"** → run `gflow data list projects` (or `images` / `videos` / `profiles`) — see [DATA_LAYER § Querying the data layer](DATA_LAYER.md#querying-the-data-layer)
**"Where can I look up a media ID I generated yesterday?"** → [DATA_LAYER § Querying the data layer](DATA_LAYER.md#querying-the-data-layer)
**"How do I stop gflow from storing my prompts?"** → [DATA_LAYER § Privacy and redaction](DATA_LAYER.md#privacy-and-redaction)
**"What does exit code 16 mean and how do I recover?"** → [DATA_LAYER § Persistence-failure handling](DATA_LAYER.md#persistence-failure-handling)
**"How do I report a security issue?"** → [SECURITY § Reporting](SECURITY.md#reporting)
**"What branch do I work on? How do I name it?"** → [DEVELOPMENT § Branching model](DEVELOPMENT.md#branching-model)
**"How do I handle an external GitHub PR?"** → [GITHUB § Scenario Matrix](GITHUB.md#scenario-matrix)
**"What automation runs on external PRs?"** → [GITHUB § Automated External PR Triage](GITHUB.md#automated-external-pr-triage)
**"How do we use Copilot review on PRs?"** → [GITHUB § GitHub Copilot Code Review](GITHUB.md#github-copilot-code-review)
**"Why did SonarCloud skip or fail on a forked PR?"** → [GITHUB § Forked PRs And SonarCloud](GITHUB.md#forked-prs-and-sonarcloud)
**"How do I run e2e tests before a release?"** → [DEVELOPMENT § E2e gate](DEVELOPMENT.md#e2e-gate-before-merging-develop--main)
**"When does the version get bumped?"** → [DEVELOPMENT § Version bump protocol](DEVELOPMENT.md#version-bump-protocol)
**"How do I embed FlowApiClient in a long-lived worker / service?"** → [USER_GUIDE § Journey 14](USER_GUIDE.md#journey-14--embedding-flowapiclient-in-a-long-lived-worker)
**"What's the standard way to import gflow errors in my code?"** → [USAGE § Programmatic use](USAGE.md#programmatic-use)
**"A gflow command hangs / fails — where do I start?"** → [DEBUGGING § Quick reference](DEBUGGING.md#quick-reference)
**"Flow's UI broke a selector — how do I diagnose it?"** → [DEBUGGING § Inspecting Flow's live UI](DEBUGGING.md#inspecting-flows-live-ui)
**"What does each `ui_automation.*` log event mean?"** → [DEBUGGING § Listener & HTTP-layer debugging](DEBUGGING.md#listener--http-layer-debugging)
**"What was actually live-verified for the latest release?"** → [LIVE_VERIFICATION_v0.8.1](LIVE_VERIFICATION_v0.8.1.md) · prior: [v0.7.0](LIVE_VERIFICATION_v0.7.0.md)
**"What was live-verified for the data layer (PR #58)?"** → [LIVE_VERIFICATION_data_layer](LIVE_VERIFICATION_data_layer.md) — 1 Imagen + 1 Veo credit on denon82, 6-layer ledger (file + magic + Pillow + DB rows + CLI round-trip + structlog)
**"What was live-verified for the video-download feature (#29)?"** → [LIVE_VERIFICATION_video_download](LIVE_VERIFICATION_video_download.md)
**"What is the jitter matrix evidence for `gflow image batch`?"** → [`LIVE_VERIFICATION_image_batch.md`](LIVE_VERIFICATION_image_batch.md) — jitter matrix evidence for `gflow image batch` (always-same-project mode)

## Documentation governance

- `README.md` is for first-time visitors. Keep it under ~400 lines. Anything longer belongs in `docs/`.
- Every new behaviour or env var needs a row in this index AND a section in the relevant `docs/*.md`.
- Cross-link generously. Use markdown link syntax with an anchor wherever a reader might need to jump deeper — they should never get stuck.
- Keep file size sane. If `docs/USAGE.md` grows past ~600 lines, split into `docs/USAGE/<topic>.md`.
