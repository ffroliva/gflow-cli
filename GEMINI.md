# GEMINI.md

> Project memory hub for **Gemini CLI**. The universal coding-agent rules for any tool (Cursor, Codex, Aider, Gemini CLI, etc.) live in [AGENTS.md](AGENTS.md) — this file carries Gemini-specific session protocol only.

## What this project is

`gflow-cli` is an unofficial Python CLI that drives [Google Flow](https://labs.google/fx/tools/flow) (Veo image-to-video, Imagen text-to-image) from the terminal by reverse-engineering Flow's private REST API. See [README.md](README.md) for the user-facing overview.

## On every session start

1. Read **[AGENTS.md](AGENTS.md)** — universal rules every agent must follow.
2. Read **[docs/INDEX.md](docs/INDEX.md)** — routing layer for all project docs and commands.
3. Pull deeper context on demand (type as plain text in the `agy` TUI prompt):
   - Current task / where we left off → `gflow:status`
   - Starting a new feature → `gflow:predict` → `gflow:scenario` → `gflow:plan <feature>`
   - Touching auth or reCAPTCHA → `gflow:known-issues`
   - Cutting a release → `gflow:release`
   - Before any commit → `gflow:check`

## Gemini-specific

- Use specialized skills when relevant (e.g., `find-docs` for library research, `pr-council-review` for PR audits).
- Maintain memory via the `mcp-mempalace` tool if available.
- Prioritize **turn efficiency** and **high-signal output**.

## Active phase

- **v0.29.0 shipped (2026-07-09):** persistent `gflow instructions` CRUD + movie-manifest instructions brief-sync + six `gflow_instructions_*` MCP tools, with a CI-enforced MCP↔CLI parity contract (`tests/mcp/test_cli_parity.py`) and the agentic-indicator selectors consolidated onto `drivers/factory.py`. Released to PyPI; issue #192 closed.
- **PR #258 (Camoufox stealth engine, external contribution) — taken over and CLOSED-OUT (2026-07-09).** A 5-persona `/gflow:predict` rejected a rebase in favour of a decomposed, evidence-gated series: Phase 1 landed the MCP video param parity (#273, contributor-credited); a WAF-evidence harness (#274/#275) measured a **0.0% WAF 403 rate over 20 live generations**, so per ADR-13 the Camoufox engine was **NOT built** (#276). The i2i "UUID-ref bug" was investigated and found not-a-bug (#277, stale comment fixed). Contributor thanked + credited on the closed PR. Evidence: `docs/superpowers/spikes/2026-07-09-camoufox-waf-403.md`.
- **Decoupled Daemon/Worker Plan:** The MCP→FlowWorker wiring shipped in v0.23.0 (PR #228). The remaining headless SSE Daemon + Tauri/React editor blueprint is scheduled in [gflow-studio-scaffold/PLAN.md](file:///C:/development/github/gflow-cli/docs/superpowers/plans/2026-06-24-gflow-studio-scaffold/PLAN.md) and [rest-api-layer/PLAN.md](file:///C:/development/github/gflow-cli/docs/superpowers/plans/2026-06-24-rest-api-layer/PLAN.md).
- **Core Lesson (Retrospective):** 
  - **Verify before adopting — measure, don't assume.** The PR #258 takeover's value was checking every claim against the real code and real data instead of adopting on assertion: a stealth engine we didn't build (0% WAF baseline), a "credit guard" already enforced one layer down, a `display_name` already shipped in v0.26.0, an i2i "bug" that wasn't one, and a concurrency bug that never merged. Five claims, five dismissals — the cheapest correct outcome came from investigation, not implementation.
  - **ADR gates pay for themselves.** ADR-13 ("confirm the current stealth fix insufficient before implementing an alternative") turned a "build Camoufox" epic into a one-afternoon 20-generation spike that said "don't." Encode the gate as a runnable measurement (`scripts/spike_waf_camoufox.py`), not a debate.
  - **Decompose contested external PRs; never rebase a branch that's 60+ commits behind.** Cherry-pick/re-express the genuinely-valuable pieces onto fresh branches with `Co-authored-by` credit, evidence-gate the risky parts, and split by concern — one PR per slice.
  - Kept `gflow-cli` strictly headless (Uvicorn + FastMCP over localhost HTTP/SSE); browser-context locks bypassed by serializing task writes in the SQLite queue with WAL-mode parallel reads. Relational instruction cards must be phrased conversationally (imperative prompts bypass the brief); multi-scene movie sync is read-modify-write to preserve server-assigned card IDs.

See [PLAN.md](PLAN.md) or type `gflow:status` for the current task. Type `gflow:plan <feature>` to create a new feature plan.
