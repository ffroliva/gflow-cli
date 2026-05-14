# Shell Multi-Prompt `t2i` Orchestration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:dispatching-parallel-agents` for parallel council/review work and `superpowers:subagent-driven-development` or `superpowers:executing-plans` for implementation. The Coordinator owns task progress, review gates, and user go/no-go decisions.

**Companion docs:**

- [`PLAN.md`](PLAN.md) - implementation plan for v0.6 shell multi-prompt `t2i`.
- [`../../specs/2026-05-14-shell-multi-prompt-design.md`](../../specs/2026-05-14-shell-multi-prompt-design.md) - authoritative spec v2.
- [`COUNCIL_REVIEW_CODE.md`](COUNCIL_REVIEW_CODE.md), [`COUNCIL_REVIEW_SECURITY.md`](COUNCIL_REVIEW_SECURITY.md), [`COUNCIL_REVIEW_GEMINI.md`](COUNCIL_REVIEW_GEMINI.md) - completed spec council reviews.

**Architecture:** Coordinator-led implementation with review gates. Test scaffold tasks run first, implementation tasks follow in dependency order, and implementation council review runs before release prep. Security review is mandatory for prompt-source parsing and final implementation because v0.6 adds a new local file/stdin input surface.

---

## 1. Roles

| Role | Agent / Owner |
|---|---|
| Coordinator | Main session |
| Implementer | `worker` or inline execution |
| Test Auditor | `default` reviewer with tests-only brief |
| Python Reviewer | `everything-claude-code:python-reviewer` if available, otherwise `default` with Python review brief |
| Security Reviewer | `everything-claude-code:security-reviewer` if available, otherwise `default` with security brief |
| Gemini Reviewer | Local `gemini --approval-mode plan -p ...` when available |
| Build Resolver | Coordinator or worker, only if quality gates fail after implementation |

---

## 2. Task Matrix

| Task | Short Name | Implementer | Test Auditor | Python Review | Security Review | Notes |
|---|---|---|---|---|---|---|
| 1 | Unit test scaffold | Coordinator/worker | skip | skip | skip | Red tests only. No production code. |
| 2 | BDD scaffold | Coordinator/worker | skip | skip | skip | Red BDD scenarios only. |
| 3 | Shared image batch module | Coordinator/worker | yes | yes | skip | Refactor risk to `gflow run`. |
| 4 | Prompt parsing/validation | Coordinator/worker | yes | yes | yes | New file/stdin input safety surface. |
| 5 | Wire `t2i` + docs | Coordinator/worker | yes | yes | yes | User-facing CLI behavior and docs. |
| 6 | BDD green | Coordinator/worker | yes | skip | skip | Verifies user-facing workflows. |
| 7 | Examples/docs polish | Coordinator/worker | skip | skip | light security read | Avoid billing claims and hardcoded profile names. |
| 8 | Full review | Coordinator | yes | yes | yes | Required implementation council. |
| 9 | Release prep | Coordinator | skip | skip | skip | Full gates before tag. |

---

## 3. Per-Task Workflow

For Tasks 1-7:

1. Coordinator reads the task block from `PLAN.md`.
2. Implementer executes only that task and creates one atomic commit.
3. Coordinator verifies `git status --short`, `git log -1 --stat`, and the task exit gate.
4. If the matrix says Test Auditor, dispatch a reviewer to inspect whether tests prove the spec contract.
5. If the matrix says Python Review, dispatch a reviewer with the task diff and spec.
6. If the matrix says Security Review, dispatch a reviewer with the task diff, `docs/SECURITY.md`, and the focused threat brief.
7. Coordinator applies or dispatches fixes as separate atomic commits.
8. Coordinator moves to the next task only when no major findings remain.

Task 8 is the formal implementation council review. Task 9 is release prep and local tagging only.

---

## 4. Review Briefs

### Test Auditor Brief

```text
Review the latest task diff against docs/superpowers/specs/2026-05-14-shell-multi-prompt-design.md.
Focus only on tests: do they fail for the right reason before implementation, do they exercise the user-visible contract, and do they avoid live Playwright/Flow?
Return PASS / MINOR-EDITS / MAJOR-REVISION with concrete file/line findings.
```

### Python Reviewer Brief

```text
Review the latest task diff against docs/superpowers/specs/2026-05-14-shell-multi-prompt-design.md.
Focus: Python idioms, strict typing, async correctness, small-module boundaries, batch extraction quality, and preserving existing `gflow run --config` behavior.
Return PROCEED-AS-IS / MINOR-EDITS / MAJOR-REVISION with concrete file/line findings.
```

### Security Reviewer Brief

```text
Review the latest task diff against docs/superpowers/specs/2026-05-14-shell-multi-prompt-design.md and docs/SECURITY.md.
Focus: prompt-file validation before full read, stdin handling, terminal-safe prompt previews, path disclosure, preflight ordering before profile resolution/browser/API work, and accidental credit-spend messaging.
Return PROCEED-AS-IS / MINOR-EDITS / MAJOR-REVISION with concrete file/line findings.
```

### Gemini Review Command

Use this only for Task 8 or if the Coordinator wants a cross-model lens on a disputed finding:

```powershell
$prompt = @'
Review the implementation of gflow-cli shell multi-prompt t2i against:
- docs/superpowers/specs/2026-05-14-shell-multi-prompt-design.md
- docs/superpowers/plans/2026-05-14-shell-multi-prompt/PLAN.md

Focus on CLI design consistency, hidden ambiguity, test coverage gaps, and scope creep.
Return markdown with Verdict: PROCEED-AS-IS / MINOR-EDITS / MAJOR-REVISION.
'@
gemini --approval-mode plan -p $prompt
```

If Gemini returns capacity errors but also returns a usable review, save the review. If it returns no usable review, record that Gemini was unavailable and proceed with the two Claude/code-review lenses.

---

## 5. Quality Gates

Before Task 8 and before Task 9 tag creation, run all four:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run pytest -q --cov=gflow_cli
```

Task-specific focused gates are listed in `PLAN.md`. Any failure blocks progression until fixed.

---

## 6. Escalation Rules

- **Single-prompt regression:** stop immediately. The spec's highest priority is non-breaking `gflow image t2i "one prompt"` behavior.
- **`gflow run --config` regression:** stop and fix before continuing. Shared extraction must not alter JSON schema or output behavior.
- **Prompt-file safety regression:** stop and fix before continuing. Oversized/non-file/invalid UTF-8 cases must fail before profile resolution and browser/API work.
- **Path disclosure dispute:** prefer basename/source labels in user-facing output; full absolute paths only under established debug logging policy.
- **Seed-scope dispute:** do not add seed to shell multi-prompt or JSON config in v0.6. Record follow-up only.
- **Docs/code commit split:** if user-facing behavior lands without docs in the same commit, stop. Amend/replace the offending behavior commit before proceeding unless Flavio explicitly approves a deviation. The locked project rule is that every commit affecting user-facing behavior updates docs in the same commit.
- **Reviewer disagreement:** Coordinator decides if one reviewer is clearly wrong. If uncertain, summarize the disagreement to Flavio for a decision.

---

## 7. State Tracking

Coordinator tracks this ledger:

```text
v0.6 shell multi-prompt t2i
  [ ] T1 test(cli): scaffold shell multi-prompt t2i tests
  [ ] T2 test(bdd): scaffold shell multi-prompt t2i scenarios
  [ ] T3 refactor(batch): share image batch execution
  [ ] T4 feat(batch): parse shell prompt sources safely
  [ ] T5 feat(image): add shell multi-prompt t2i
  [ ] T6 test(bdd): cover shell multi-prompt t2i
  [ ] T7 docs(examples): add multi-prompt t2i example
  [ ] T8 chore(review): apply shell multi-prompt implementation review
  [ ] T9 chore(release): v0.6.0a1
```

After each task, record the commit SHA and any deferred minor findings in the session notes or project memory.

---

## 8. Definition of Done

- [ ] Every task in `PLAN.md` completed in order or explicitly superseded with Coordinator rationale.
- [ ] All implementation council major findings addressed.
- [ ] Full quality gates pass.
- [ ] Worktree clean except any intentionally untracked local release notes.
- [ ] Local tag `v0.6.0a1` exists.
- [ ] User is given the exact push commands for release.

End of orchestration plan.
