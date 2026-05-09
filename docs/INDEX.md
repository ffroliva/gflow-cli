# Documentation Index

Welcome to the `flow-cli` documentation. This index is the routing layer: it tells you where each topic lives. Keep `README.md` slim (high-level overview + install) and document deep details here.

| Doc | Purpose | Read when… |
|---|---|---|
| [README](../README.md) | Project overview, install, quick start | First time landing on the repo |
| [PLAN.md](../PLAN.md) | Implementation plan (DDD / CQRS / phases / ADRs) | You want the architectural intent and roadmap |
| [CHANGELOG](../CHANGELOG.md) | Version-by-version user-visible changes | Upgrading or auditing what shipped |
| [KNOWN_ISSUES](../KNOWN_ISSUES.md) | Open / mitigated / resolved issues with workarounds | Before opening a bug report; when something feels off |
| [DISCLAIMER](../DISCLAIMER.md) | Legal scope, takedown policy, prohibited uses | Before deploying anywhere non-trivial |
| [LICENSE](../LICENSE) | MIT license text | Always |
| [CONTRIBUTING](../CONTRIBUTING.md) | TDD workflow, test categories, coverage targets | Before opening a PR |
| [.env.template](../.env.template) | All environment variables with defaults | Setting up a new shell or container |
| **[docs/AUTHENTICATION.md](AUTHENTICATION.md)** | Full auth flow, session storage, multi-account, refresh | First `gflow auth login`, or auth errors |
| **[docs/CONFIGURATION.md](CONFIGURATION.md)** | All env vars, precedence chain, default paths per OS | Tuning behaviour, picking output paths |
| **[docs/ARCHITECTURE.md](ARCHITECTURE.md)** | Layered structure, CQRS bus, DDD pieces, ports/adapters | Adding a feature or a new provider |
| **[docs/USAGE.md](USAGE.md)** | Command-by-command reference, manifest format, recipes | Day-to-day CLI use |
| **[docs/SECURITY.md](SECURITY.md)** | What secrets are stored where, threat model, hardening | Audit, code review, multi-user machines |

## Topic shortcuts

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
