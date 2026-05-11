# Documentation Index

Welcome to the `gflow-cli` documentation. This index is the routing layer: it tells you where each topic lives. Keep `README.md` slim (high-level overview + install) and document deep details here.

| Doc | Purpose | Read when… |
|---|---|---|
| [README](../README.md) | Project overview, install, quick start | First time landing on the repo |
| [CLAUDE.md](../CLAUDE.md) | Project memory hub for AI coding agents | First time an agent (Claude/Cursor/Codex/Gemini/Aider) opens the repo |
| [PLAN.md](../PLAN.md) | Implementation plan (DDD / CQRS / phases / ADRs) | You want the architectural intent and roadmap |
| [CHANGELOG](../CHANGELOG.md) | Version-by-version user-visible changes | Upgrading or auditing what shipped |
| [KNOWN_ISSUES](../KNOWN_ISSUES.md) | Open / mitigated / resolved issues with workarounds | Before opening a bug report; when something feels off |
| [DISCLAIMER](../DISCLAIMER.md) | Legal scope, takedown policy, prohibited uses | Before deploying anywhere non-trivial |
| [LICENSE](../LICENSE) | MIT license text | Always |
| [CONTRIBUTING](../CONTRIBUTING.md) | TDD workflow, test categories, coverage targets | Before opening a PR |
| [.env.template](../.env.template) | All environment variables with defaults | Setting up a new shell or container |
| **[docs/AUTHENTICATION.md](AUTHENTICATION.md)** | Full auth flow, session storage, multi-account, refresh | First `gflow auth login`, or auth errors |
| **[docs/CONFIGURATION.md](CONFIGURATION.md)** | All env vars, precedence chain, default paths per OS | Tuning behaviour, picking output paths |
| **[docs/USER_GUIDE.md](USER_GUIDE.md)** | Task-oriented walkthroughs (first setup, batch video, multi-image, log forensics, recovery, multi-account, migration) | You want to GET SOMETHING DONE — not look up a flag |
| **[docs/USAGE.md](USAGE.md)** | Command-by-command reference, manifest format, recipes, exit-code table | Day-to-day CLI use — look up specific commands / flags |
| **[docs/ARCHITECTURE.md](ARCHITECTURE.md)** | Modular monolith, per-worker Page pool, RFC 9457 Problem Details, retry layer | Adding a feature or a new provider |
| **[docs/SECURITY.md](SECURITY.md)** | What secrets are stored where, threat model, hardening | Audit, code review, multi-user machines |
| **[tasks/lessons.md](../tasks/lessons.md)** | Running notebook of patterns + reviewer findings, dated and traced to commits | Starting a new phase; debugging "why did the council flag this?" |

## Topic shortcuts

**"I just installed gflow — how do I get to my first video?"** → [USER_GUIDE § Journey 1](USER_GUIDE.md#journey-1--first-time-setup-10-minutes)
**"How do I render 20 clips overnight with concurrency?"** → [USER_GUIDE § Journey 3](USER_GUIDE.md#journey-3--batch-video-with-concurrency)
**"My batch died on entry 23 with exit code 3 — what now?"** → [USER_GUIDE § Journey 7](USER_GUIDE.md#journey-7--recovering-from-an-authexpirederror-mid-batch)
**"Where is my session stored?"** → [AUTHENTICATION § Session storage](AUTHENTICATION.md#session-storage)
**"Where do generated files land?"** → [CONFIGURATION § Output paths](CONFIGURATION.md#output-paths)
**"How do I run with multiple Google accounts?"** → [AUTHENTICATION § Multiple accounts](AUTHENTICATION.md#multiple-accounts)
**"How does the layered structure work?"** → [ARCHITECTURE § Layers](ARCHITECTURE.md#layers)
**"What env var should I set for X?"** → [CONFIGURATION § Reference](CONFIGURATION.md#reference)
**"How do I report a security issue?"** → [SECURITY § Reporting](SECURITY.md#reporting)

## Documentation governance

- `README.md` is for first-time visitors. Keep it under ~400 lines. Anything longer belongs in `docs/`.
- Every new behaviour or env var needs a row in this index AND a section in the relevant `docs/*.md`.
- Cross-link generously. `[X](Y.md#anchor)` everywhere — readers should never get stuck.
- Keep file size sane. If `docs/USAGE.md` grows past ~600 lines, split into `docs/USAGE/<topic>.md`.
