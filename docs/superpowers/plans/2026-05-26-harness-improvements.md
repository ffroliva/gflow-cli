# Harness Improvements Plan

**Date:** 2026-05-26
**Source:** Pattern analysis of 4 reference repositories (see `docs/REFERENCES.md`)
**Goal:** Systematically close the gaps between gflow-cli's current harness and patterns proven in the wild — without over-engineering for a single-developer CLI project.

---

## Assessment summary

After a multi-dimensional audit across 8 dimensions, gflow-cli's harness is **above average** compared to the studied repos. The 5-subsystem model (Instructions, State, Verification, Scope, Lifecycle) is essentially complete. The gaps are surgical, not structural.

### What we already do well (do not touch)

| Dimension | Our implementation | Verdict |
|---|---|---|
| Instructions layer | CLAUDE.md + AGENTS.md + AGENT_GUIDE.md + docs/INDEX.md | Strong |
| Verification gate | `/gflow:check` (hygiene + ruff + pyright + pytest) | Strong |
| Scope control | `/gflow:plan` + active_plan.py + superpowers plans | Strong |
| Session routing | CLAUDE.md init sequence (AGENTS.md → INDEX.md → demand-load) | Strong |
| Lessons notebook | tasks/lessons.md (L1-L25, dated, commit-traced) | Excellent |
| Feature narrative | docs/superpowers/plans/ + orchestration plans | Good |
| Knowledge routing | docs/INDEX.md with topic shortcuts | Excellent |
| Release protocol | /gflow:release (signed tags, back-merge, doc-review gate) | Strong |

### Confirmed gaps (what this plan addresses)

| Gap | Source repo | Priority | Task |
|---|---|---|---|
| Missing pi behavioral rules in AGENTS.md | earendil-works/pi | HIGH | T1 |
| No MUST/FORBID/NORM severity markers on critical rules | yzddp/harnesscode | MEDIUM | T2 |
| No formalized handover template | walkinglabs | MEDIUM | T3 |
| No doc stability badges on volatile/reverse-engineered docs | atomicstrata/llm-wiki | LOW | T4 |
| No `/gflow:handover` command | walkinglabs + harnesscode | LOW | T5 |
| Memory file missing / path wrong in CLAUDE.md | (internal) | CRITICAL | T0 |
| Reference repos undocumented | (internal) | HIGH | T0 |

---

## Task 0 — Memory + References (immediate, no review needed)

**Status: DONE (2026-05-26)**

- [x] Create `~/.claude/projects/-home-user-gflow-cli/memory/MEMORY.md` with 7 drawers
- [x] Create `docs/REFERENCES.md` — annotated record of all 4 reference repos
- [x] Fix `CLAUDE.md` — remove hardcoded Windows memory path; add environment-agnostic note
- [x] Add `docs/REFERENCES.md` to `docs/INDEX.md` routing table
- [x] Create this plan file

---

## Task 1 — AGENTS.md: Add pi behavioral rules

**Priority:** HIGH
**Effort:** Small (add ~8 rules to existing AGENTS.md)
**Source:** `earendil-works/pi` AGENTS.md (battle-tested, 55k-star project)

### Rules to add

These 6 rules are absent from our current AGENTS.md and directly address failure modes we've seen in practice (L10, L11, L17 in tasks/lessons.md):

```
Read full files before broad edits; never rely on search snippets alone.
Explicitly agree or disagree with feedback before making changes.
Ask before removing code that appears intentional.
Stage only your own changed files using explicit git paths; never `git add -A`.
Resolve merge conflicts only in files you modified.
Name regression tests: tests/<issue-number>-<short-slug>.test.py
```

### Where to insert

Add as a new subsection `## Agent behavioral rules` in AGENTS.md, between `## Code style` and `## PR instructions`. Keep each rule to one sentence.

### Definition of done

- `AGENTS.md` has the 6 new rules
- No existing rule is changed or removed (additive only)
- `/gflow:check` passes after the edit

---

## Task 2 — Add MUST/FORBID/NORM markers to critical rules

**Priority:** MEDIUM
**Effort:** Small (annotation pass over AGENTS.md + AGENT_GUIDE.md)
**Source:** `yzddp/harnesscode` tech-spec writing guide

### What this is

Prefix the most critical non-negotiable rules with severity markers to make precedence unambiguous:

- **MUST:** mandatory — cannot be violated under any circumstances
- **FORBID:** explicitly disallowed behavior
- **NORM:** recommended — default behavior that can be overridden with reason

### Scope

Only apply markers to the rules most likely to cause damage if violated:

| Rule | Marker |
|---|---|
| TDD before code; write a failing test first | MUST |
| No raw `print()` or `import logging` in `src/` | MUST |
| No secrets in commits | MUST |
| No AI attribution in commit messages | MUST |
| Signed tags only (`git tag -s`) | MUST |
| Never `git add -A` | FORBID |
| Never `git reset --hard` without explicit user instruction | FORBID |
| Never force push to `main` or `develop` | FORBID |
| Never `--no-verify` past hooks | FORBID |
| Run `/gflow:check` before every commit | MUST |

**Do not marker** every rule — only the ~10 that cause irreversible damage if skipped. Over-marking creates noise.

### Definition of done

- AGENTS.md MUSTs and FORBIDs are clearly prefixed
- AGENT_GUIDE.md Mandates section is consistent with AGENTS.md markers
- No behavioral change — purely annotation

---

## Task 3 — Formalize the handover template

**Priority:** MEDIUM
**Effort:** Small (template + one slash command)
**Source:** `walkinglabs/learn-harness-engineering` + organic `2026-05-17-issue-15-handover.md`

### What we have

`docs/superpowers/2026-05-17-issue-15-handover.md` was created organically. It has excellent structure:
- Status (one line)
- Root cause (one line)
- What was superseded and why
- Current branch state
- Next step (explicit)
- Workflow reminder

### What we need

1. A template file at `docs/superpowers/HANDOVER_TEMPLATE.md`
2. A note in `docs/superpowers/README.md` (create if missing) explaining when to create a handover
3. A `/gflow:handover` command that scaffolds a handover doc from the current git state

### Template structure

```markdown
# Handover — [Feature/Issue description] — YYYY-MM-DD

## Status: [one line]

## Root cause / investigation finding (one line)
[Or: "Investigation not complete — stopped at:"]

## What was done this session
- Committed: [list commits with SHAs]
- Explored: [local-only work not committed]
- Superseded plans (if any): [file paths + why they are wrong]

## Current branch state
Branch: [name]
Clean working tree: [yes/no — if no, list files]
Tests: [passing/failing/unknown]

## Open questions / blockers (missing_info)
[Write these when you hit a decision you cannot make alone — human clears them]

## Next step
[Exactly what to do next — specific enough that a fresh session can start without re-reading history]

## Skills to use
[Suggested /gflow: commands or skills for the next session]

## Workflow note
[Any branch-protection or PR flow reminders relevant to this feature]
```

### Definition of done

- `docs/superpowers/HANDOVER_TEMPLATE.md` exists
- `.claude/commands/gflow/handover.md` exists and scaffolds a handover from git state
- `docs/INDEX.md` updated with new command
- `docs/superpowers/` has a brief README explaining the structure

---

## Task 4 — Doc stability badges on volatile docs

**Priority:** LOW
**Effort:** Tiny (add a callout block to ~3 doc files)
**Source:** `atomicstrata/llm-wiki-compiler` provenance/confidence pattern

### Problem

Several docs describe reverse-engineered behavior that can change without notice. Readers (especially agents reading docs to make decisions) don't know whether to trust these claims fully or hedge.

### Solution

Add a stability badge callout at the top of each volatile doc:

```markdown
> ⚠ **API Stability: UNSTABLE** — This document describes reverse-engineered behavior
> of Google Flow's private REST API. Claims may become incorrect without notice.
> Verified as of: v0.9.0 (2026-05-24). Check KNOWN_ISSUES.md before relying on specifics.
```

### Files to badge

| File | Why |
|---|---|
| `docs/AUTHENTICATION.md` | Describes private auth flow — reCAPTCHA, cookie capture |
| `docs/ARCHITECTURE.md` § API surface | `aisandbox-pa.googleapis.com` routes can rotate |
| `samples/README.md` | Captured request/response samples from live traffic |

Do NOT badge `docs/USAGE.md`, `docs/CONFIGURATION.md`, or `docs/USER_GUIDE.md` — those describe our CLI behavior, which we control.

### Definition of done

- 3 docs have stability callout in their header section
- Callout is consistent (same wording, same "Verified as of: vX.Y.Z" format)

---

## Task 5 — `/gflow:handover` slash command

**Priority:** LOW (depends on Task 3)
**Effort:** Small
**Source:** walkinglabs harness-creator skill pattern

### What it does

When invoked, the command:
1. Reads current git status and recent commits
2. Reads the active superpowers plan (via `active_plan.py`)
3. Reads any open blockers from memory
4. Scaffolds a populated `docs/superpowers/YYYY-MM-DD-<feature>-handover.md`
5. Tells the agent to fill in the "Root cause" and "Next step" sections manually

### Command file location

`.claude/commands/gflow/handover.md`

### Definition of done

- Command exists and produces a scaffolded handover doc
- `docs/INDEX.md` updated with the new command
- Handover doc creation is referenced in the session wrap-up note in CLAUDE.md

---

## Sequencing

Tasks are independent and can be done in any order. Recommended sequence:

```
T0 (done) → T1 → T2 → T3 → T4 → T5
```

T1 and T2 are the highest-value items: they improve every session immediately. T3-T5 are workflow improvements that compound over time.

---

## What this plan deliberately does NOT include

To avoid over-engineering:

- ❌ `feature_list.json` — redundant with `active_plan.py` + markdown
- ❌ `.harnesscode/` state directory — over-engineered for single-developer scope
- ❌ Full orchestrator state machine — human Coordinator is sufficient
- ❌ MCP server exposure — v1.0+ consideration
- ❌ Full wiki compilation pipeline — `docs/` + INDEX.md is sufficient
- ❌ `init.sh` environment script — CI + AGENTS.md dev-env section covers this
- ❌ Confidence scores per doc page — stability badge is sufficient signal

---

## See also

- `docs/REFERENCES.md` — full analysis of the 4 source repositories
- `~/.claude/projects/-home-user-gflow-cli/memory/MEMORY.md` — adoption decisions recorded
- `tasks/lessons.md` — operational lessons that informed this plan
