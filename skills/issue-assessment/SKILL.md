---
name: issue-assessment
version: "1.0"
description: >
  Use when triaging a GitHub issue for gflow-cli — a reporter's bug claim, a
  freshly-filed issue, or deciding whether and how to act on one. Also use when
  an autonomous agent (hermes-ops) picks up a labelled issue. Read-only: produces
  a verdict, an end-to-end-verifiability judgment, and a reporter-facing reply.
  Does not modify code or post anything on its own.
---

# `issue-assessment` — triage a gflow-cli issue honestly

Read-only conductor. Verify the reporter's claim against the code, tests, docs,
`KNOWN_ISSUES.md`, and auto-memory; classify it; judge whether it can be
verified end-to-end *in the current environment*; and draft a reply. The output
is a standard artifact a human or the `issue-resolve` skill can act on.

**Core principle:** never assert more than the evidence supports. A claim is
`CONFIRMED` only with line-level code evidence or a reproduction; a fix is
"verified" only after running it on the **affected surface**. Honest
"can't verify here" beats a false green check — a bounced fix costs more trust
than an accurate "not yet."

---

## When to invoke

- A new or updated GitHub issue needs a verdict before anyone spends effort.
- An autonomous run (hermes-ops) reacts to an issue labelled for triage.
- You're about to "just fix" a reported bug — assess first; the scope decision
  (reply-only vs hand to `issue-resolve`) depends on this.

Skip for: issues that are obviously feature requests routed elsewhere, or
already-triaged issues entering implementation.

---

## Invocation

```
/gflow:issue-assessment <issue number or URL>
```

This repo's `skills/*/SKILL.md` are plain Markdown — invoke by **reading** the
file (via the `.claude/commands/gflow/*` wrapper), never `Skill(skill=...)`.

---

## Protocol

### 1. Ingest
`gh issue view <N> --json title,body,comments,labels,author,state`. Extract: the
claimed symptom, environment (OS, version, install method), exact repro steps,
and any logs/error classes the reporter pasted.

### 2. Verify (read-only)
Dispatch a search/Explore agent (keep your own context clean) to corroborate or
refute the claim against the real tree. Always check, in order:
- the source path(s) the symptom implicates — cite `file_path:line_number`;
- `KNOWN_ISSUES.md` (is this Open / Mitigated / Resolved already?);
- open issues/PRs (`gh pr list`, `gh issue list`) for duplicates or in-flight fixes;
- auto-memory for prior context on the surface.
Disprove parts of the reporter's framing where the code says otherwise (e.g.
`browser_engine: playwright` is the engine axis, not the channel) — a precise
correction is more useful than agreement.

### 3. Classify — exactly one verdict

| Verdict | Meaning |
|---|---|
| `CONFIRMED-BUG` | Reproduced, or root-caused in code with line-level evidence. |
| `LIKELY-BUG / NEEDS-E2E` | Strong code hypothesis, but unverifiable in this environment (e.g. macOS-only or headed-browser bug on a headless/Windows host). |
| `NEEDS-INFO` | A specific discriminating diagnostic is required before deciding. |
| `DUPLICATE` / `KNOWN-ISSUE` | Matches an open issue/PR or a `KNOWN_ISSUES.md` entry. |
| `WORKING-AS-INTENDED` / `INVALID` | Usage error or expected behavior. |
| `WONTFIX / OUT-OF-SCOPE` | Real but deliberately not addressed. |

### 4. e2e-gate — what would verification actually require?
Classify the verification cost before claiming anything is fixed:
- **Browser-free** (pure-Python logic, Gemini tool-path, unit/lint/type, recording-verif) → verifiable anywhere, including the headless VPS. Run it.
- **Headed-Flow-browser required** (generation, selector, auth, reCAPTCHA) → **not** verifiable on a headless or wrong-OS host. Verdict tilts to `LIKELY-BUG / NEEDS-E2E`; never claim success; the final check is a human on the affected surface. (See memory: `done-means-e2e-verified`, `pr-must-verify-on-affected-surface`.)

### 5. Report (the artifact)
Produce the reply below. Post it only if the autonomy gate allows (autonomous
runs may comment; otherwise surface for a human to send).

```
**Assessment of #<N>: <verdict>** (confidence <N>/10)

Restated claim: <one line>.

Findings:
- <evidence as file:line> …
- <corrections to the reporter's framing, if any> …

Root cause / hypothesis: <what and why, or "unconfirmed because …">.

What we need next:
- <the single discriminating diagnostic — for NEEDS-INFO/NEEDS-E2E>, or
- <draft PR link — if issue-resolve ran>, or
- <why this is a dup/invalid/wontfix>.
```

### 6. Hand-off decision (graded, not a hard stop)
- Verdict ∈ {`CONFIRMED-BUG`, `LIKELY-BUG`} **and** scope is single-surface/localized
  **and** a fix is verifiable in this environment → chain to **`issue-resolve`**.
- Otherwise → reply only; the next step is a human or more info.

---

## Skill routing (do not hallucinate skill names)

Re-derive this from `ls skills/` + `ls .claude/commands/gflow/` before relying on
it — names drift. Current map:

| Need | Use | How |
|---|---|---|
| High-stakes change (auth/transport/selector/schema) | `/gflow:predict` | read `skills/predict/SKILL.md` |
| Edge cases + BDD skeleton | `/gflow:scenario` | read `skills/scenario/SKILL.md` |
| Touching auth/reCAPTCHA | `/gflow:known-issues` | `.claude/commands/gflow/known-issues.md` |
| Drive the fix | `issue-resolve` | read `skills/issue-resolve/SKILL.md` |
| Worktree / TDD | superpowers | `Skill()` tool (these *are* invocable) |

---

## Output format

A single Markdown block: the verdict line, findings with citations, root-cause
hypothesis, and the "what we need next" step. No code changes, no posting unless
the autonomy gate permits.

---

## Provenance

Designed 2026-06-29 (`docs/superpowers/specs/2026-06-29-issue-assessment-workflow-design.md`).
Validated against issue #222 (a `LIKELY-BUG / NEEDS-E2E` case: macOS + headed
browser, unverifiable on Windows/headless). Authored recipe-shaped after three
baseline runs showed capable agents already comply with the project's discipline
rules — the skill standardizes the procedure and artifact, it does not enforce
discipline the agent lacks.
