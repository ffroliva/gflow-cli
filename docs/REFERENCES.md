# Reference Repositories

> External repositories studied for harness engineering, agent workflow, and knowledge-base patterns. Kept as a citable record so future sessions know what we evaluated, what we adopted, and why we made each call.
>
> Last updated: 2026-05-26.

---

## How to use this file

- Look here before reinventing a pattern — we may have already evaluated it.
- Each entry records: what the repo does, what we extracted, adoption status, and the decision rationale.
- "Adopted" means the pattern is live in this repo (cite the file). "Pending" means it's in the improvement plan. "Rejected" means we considered it and said no.

---

## 1. `walkinglabs/learn-harness-engineering`

**URL:** <https://github.com/walkinglabs/learn-harness-engineering>
**Stars:** ~6,500 | **Language:** TypeScript (course site + Electron app)
**What it is:** Official harness engineering course — 6 progressive projects teaching how to build reliable environments for AI coding agents. Defines the canonical 5-subsystem model.

### Key patterns extracted

**The 5-Subsystem Harness Model:**
1. **Instructions** — progressive disclosure across multiple files, not a monolith
2. **State** — persistent tracking of progress, features, session context on disk
3. **Verification** — gated task completion requiring runnable proof (tests, lint, type check)
4. **Scope** — one feature per session, explicit "done" definition
5. **Session Lifecycle** — structured `init → execute → wrap-up` phases with clean restart paths

**Generated artifacts from their harness-creator skill:**
- `AGENTS.md` / `CLAUDE.md` — agent operating manual
- `feature_list.json` — machine-readable feature list with completion status
- `progress.md` — session narrative + what's next
- `init.sh` — environment health-check
- `session-handoff.md` — wrap-up artifact each session writes

**Core design philosophy:**
> "The model decides what code to write. The harness governs when, where, and how it writes it. The harness doesn't make the model smarter; it makes the model's output reliable."

**Anti-patterns they document:**
- Monolithic instructions — agents skip sections
- No state persistence — subsequent sessions lose continuity
- Agent self-judgment on completion — no external verification gate
- Unbounded scope — agent overreaches, under-finishes, hides unfinished work
- Missing lifecycle phases — skipping init or cleanup leads to brittle state

### What we adopted
- Session lifecycle pattern (CLAUDE.md init sequence: AGENTS.md → INDEX.md → demand-load)
- Verification-first completion (`/gflow:check` gates every commit)
- Superpowers plan files as session narrative (our version of `progress.md`)
- One handover doc per stopped investigation (organic from 2026-05-17-issue-15-handover.md)

### What we decided against
- `feature_list.json` — redundant with our `active_plan.py` + markdown checkboxes. Adding JSON would create dual state that can drift.
- `init.sh` — covered by CI + documented `uv sync && playwright install chromium` in AGENTS.md.

### What's pending
- Formalized handover template (plan Task 3)
- `/gflow:handover` command (plan Task 5)

---

## 2. `earendil-works/pi`

**URL:** <https://github.com/earendil-works/pi>
**Stars:** ~55,000 | **Language:** TypeScript (monorepo)
**What it is:** The most-starred AI coding agent CLI on GitHub. Modular monorepo — unified LLM abstraction, agent runtime, TUI, Slack bot, vLLM pods. Their `AGENTS.md` is the most battle-tested agent instruction file we've seen in the wild.

### Key patterns extracted

**AGENTS.md production rules (full text):**
```
- Keep responses short and direct without emojis or filler
- Answer questions first, then implement
- Explicitly agree or disagree with feedback before making changes
- Read full files before broad edits; never rely on search snippets
- Avoid `any` types; check node_modules for external API types
- No inline imports — top-level only
- Upgrade outdated dependencies rather than downgrading code
- Ask before removing intentional code
- Never hardcode key checks; use configurable defaults instead
- Run `npm run check` after code changes; fix all errors before committing
- Don't run full test suite directly; use wrapper scripts
- Create regression tests named `<issue-number>-<short-slug>.test.ts`
- Write ad-hoc scripts to temp files; remove when done
- Don't commit unless the user asks
- Stage only your own changed files using explicit paths
- Never use `git add -A`, `git reset --hard`, or force push
- Resolve conflicts only in files you modified
- Add `fixes #N` / `closes #N` in commit messages
- Post GitHub comments via temp files with gh CLI
```

**Dependency management discipline:**
> "Pin direct external deps to exact versions. Treat npm/pip changes as reviewed code. Use `--ignore-scripts` locally. Never downgrade code to match a dependency."

**Changelog structure (enforced):**
```
## [Unreleased]
### Breaking Changes
### Added
### Changed
### Fixed
### Removed
```
Never modify released sections. Link external contributions to PRs with author attribution.

**Lockstep versioning:** All packages version together. Smoke-test isolated installs before release.

**Supply chain hardening:**
- Direct external deps pinned to exact versions
- Lock files are the dependency ground truth; changes undergo review
- Pre-commit hooks prevent accidental lock file modifications
- Explicit allowlists for dependency lifecycle scripts (supply chain attack vector)

### What we adopted
- Changelog discipline with `[Unreleased]` sections (L25 in lessons.md) — already in place
- `fixes #N` / `closes #N` in commit messages — already in AGENTS.md PR section implicitly
- Don't commit unless user asks — already our practice

### What's pending (plan Task 1)
Six rules we're missing and should add to our `AGENTS.md`:
1. "Read full files before broad edits; never rely on search snippets"
2. "Explicitly agree or disagree with feedback before making changes"
3. "Ask before removing intentional code"
4. "Stage only your own changed files using explicit paths; never `git add -A`"
5. "Resolve conflicts only in files you modified"
6. Regression test naming: `tests/<issue-number>-<short-slug>.test.py`

### What we decided against
- Shrinkwrap files — Python uses `uv.lock`, which already serves this purpose.
- `--ignore-scripts` on install — Python/uv doesn't have the same lifecycle-script attack surface as npm.

---

## 3. `yzddp/harnesscode`

**URL:** <https://github.com/yzddp/harnesscode>
**Stars:** ~92 | **Language:** Python
**What it is:** Framework for long-running, unattended AI-driven development, based on the Anthropic Harness paper. External memory + small-step execution + auto-loop. Five specialized agents: Orchestrator, Initializer, Coder, Tester, Fixer, Reviewer.

### Key patterns extracted

**State-based coordination:**
All agent coordination happens through files in `.harnesscode/` directory, not in memory. Agents read state, execute, write results back. Survives restarts.

**8-priority orchestrator decision table:**
```
1. Missing init files          → INITIALIZER
2. Pending human dependencies  → PAUSE_FOR_HUMAN
3. Test failures               → FIXER (code_bug) or CODER (feature_not_implemented)
4. Code review failures        → FIXER all
5. Pending regular features    → CODER [module] [id]
6. Feature 990 pending         → TESTER [module]
7. Feature 991 pending         → REVIEWER
8. All complete                → PROJECT COMPLETE
```

**`missing_info.json` blocker pattern:**
When an agent hits a decision it cannot make alone, it writes a structured question to `missing_info.json` and pauses. Prevents autonomous drift. Human clears it to resume.

**Tech spec severity markers:**
```
MUST: Mandatory requirement that cannot be violated
FORBID: Explicitly disallowed behavior
NORM: Recommended requirement
```

**Tech spec file format:** `tech-spec-{module-name}.md` with sections: Scope → Key Requirements → Full Specification → Correct Examples → Incorrect Examples.

**Core principle:**
> "AI executes, humans decide — encode reversibility at every meaningful state transition."

### What we adopted
- Lessons notebook (`tasks/lessons.md`) serves our "state coordination" need — lighter-weight
- Open handover file (`docs/superpowers/2026-05-17-issue-15-handover.md`) is our `missing_info.json` equivalent

### What's pending (plan Task 2)
- MUST/FORBID/NORM markers on critical rules in AGENTS.md and key spec docs

### What we decided against
- `.harnesscode/` state directory — over-engineered for a single-developer CLI. Our superpowers plans + lessons.md cover this at appropriate weight.
- Full orchestrator state machine — we handle this through human Coordinator judgment, not an automated 8-priority loop.

---

## 4. `atomicstrata/llm-wiki-compiler`

**URL:** <https://github.com/atomicstrata/llm-wiki-compiler>
**Stars:** ~1,300 | **Language:** TypeScript
**What it is:** Compile-once, query-many knowledge architecture inspired by Karpathy's LLM Wiki pattern. Raw sources → structured interlinked wiki with provenance tracking. Outer loop handles decisions, inner loop handles knowledge compilation.

### Key patterns extracted

**Compile-once vs. RAG:**
> "RAG retrieves chunks at query time. Every question re-discovers the same relationships from scratch. Nothing accumulates."

The wiki builds a durable artifact — concepts become first-class pages with explicit cross-references. Knowledge compounds.

**Two-phase inner loop:**
1. Extract all concepts from all sources
2. Generate pages

Eliminates order-dependency. Catches failures before writing anything.

**Page provenance frontmatter:**
```yaml
confidence: 0.87
provenance: merged    # extracted | merged | inferred | ambiguous
contradictedBy: [...]
```
With inline citations: `This claim is grounded here. ^[file.md:42-58]`

**Staged approval workflow:**
Candidate pages land in `.llmwiki/candidates/` before approval. Human inspects each before it enters the wiki. Lock file ensures concurrent safety.

**Linting pass:**
Detects broken wikilinks, orphaned pages, low-confidence content, contradictions, malformed citations.

**Evidence packing (`get_context_pack`):**
Builds structured evidence bundles for agents: primary pages + graph neighbors + semantic chunks + citations + warnings + suggested next actions. Separates evidence gathering from answer generation.

**MCP server integration:**
Exposes full pipeline to Claude Desktop / Cursor / Claude Code without CLI scraping.

### What we adopted
- `docs/INDEX.md` routing layer — our implementation of the compile-once navigation idea
- `LIVE_VERIFICATION_*.md` files — dated verification evidence = our version of provenance

### What's pending (plan Task 4)
- Doc stability badges in volatile docs headers: e.g., `> ⚠ UNSTABLE — reverse-engineered API. May change without notice.`
- Cross-reference lint: check that every link in `docs/INDEX.md` resolves

### What we decided against
- Full wiki compilation pipeline — too heavy for a CLI project's docs. Our structured `docs/` + INDEX.md is sufficient.
- Confidence scores per page — over-engineered. A simple stability badge in the header conveys the same signal.
- MCP server exposure — interesting for v1.0+, but out of scope now.

---

## See also

- `tasks/lessons.md` — hard-won project rules (L1-L25), more implementation-specific than the patterns above
- `~/.claude/projects/-home-user-gflow-cli/memory/MEMORY.md` — cross-session memory including adoption decisions
- `docs/superpowers/plans/2026-05-26-harness-improvements.md` — the improvement plan derived from this analysis
