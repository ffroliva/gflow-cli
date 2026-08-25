# Workflow-hardening test — closing the three council gaps (#568)

> Spec + decisions + BDD scenarios + task plan for #568, the follow-up to
> #565 / PR #566 (`230200b`). Scope is `tests/scripts/test_workflow_hardening.py`,
> `docs/GITHUB.md`, and nothing else. No workflow behaviour changes.

## 1. Context

PR #566 added two overlapping controls:

| Control | Where | Required check? |
|---|---|---|
| `zizmor==1.29.0 --offline --min-severity low` | `ci.yml` job `workflow-audit` | on `main` only (added by #567) |
| `tests/scripts/test_workflow_hardening.py` | `test` job | on `main` |

The `/gflow:pr-council-review` council on #566 raised four items. None blocked
the merge; all were recorded on #568 rather than dropped.

Verified against the tree at `5d10554` before deciding anything:

- All 11 workflows are `.yml`. **No `.yaml` exists today** — gap 1 is latent, not active.
- `zizmor==` appears in exactly **3** places: `ci.yml:97`, `docs/GITHUB.md:217`,
  `test_workflow_hardening.py:34`. Matches the issue.
- Every `${{ github.* }}` in a workflow already arrives through an `env:` block
  (`BASE_REF`, `HEAD_REF`). The blanket ban currently costs nothing.

## 2. Assessment per gap

### Gap 1 — `glob("*.yml")` misses `*.yaml`  → CONFIRMED

`test_workflow_hardening.py:38`. GitHub honours both extensions. A future
`.yaml` workflow escapes the persist-credentials and template-injection guards
locally and in the `test` job. CI's zizmor job is handed the *directory*, so it
still audits such a file — the gap is in the local guard, not total.

**Decision: `glob("*.y*ml")`.**

Rejected `glob("*.yml") + glob("*.yaml")`: same effect, more code. `*.y*ml` also
matches contrived names like `x.yZZml`, which is over-inclusion — the fail-safe
direction for a security guard. Under-inclusion is the failure mode that matters.

### Gap 2 — the zizmor pin has a third, unguarded copy → CONFIRMED

`ZIZMOR_PIN` locks `ci.yml` ↔ the test. `docs/GITHUB.md:217` is unguarded, while
that same doc claims *"the gate and the test cannot drift apart silently"*.

The issue offered two fixes. **Both are rejected**, and a third is taken:

| Option | Verdict |
|---|---|
| Assert the doc copy against `ZIZMOR_PIN` | Works, but keeps a hand-maintained constant — 3 files to edit per bump. |
| Drop `ZIZMOR_PIN`, assert `zizmor==\d+\.\d+\.\d+` | **Weaker than today.** It only proves *a* pin exists. `ci.yml` could say `1.31.0` and the doc `1.29.0` and the test would pass — the exact drift the issue is about. |
| **Derive the pin from `ci.yml`, assert the doc matches** | Taken. |

**Decision: `ci.yml` becomes the single source of truth.** A helper extracts the
pinned version from the `workflow-audit` run command; the doc is asserted equal
to it. This is strictly better than both options: it removes the constant *and*
locks all three copies, so a bump touches `ci.yml` + the doc and the test follows
on its own. Editing one file per bump instead of three, with drift impossible
rather than merely detected.

### Gap 3 — the injection regex is broader than zizmor's rule → CONFIRMED, keep as-is

`r"\$\{\{\s*github\."` bans *every* `github.*` context in a `run:` block. zizmor's
`template-injection` is context-aware, so a benign `${{ github.run_id }}` fails the
test and passes zizmor.

**Decision: keep the blanket ban; document it as deliberate.** Comment only, no
logic change.

Narrowing was considered and rejected on security grounds: the attacker-controllable
set is long and moves (`event.*`, `head_ref`, `base_ref`, `actor`,
`triggering_actor`, `event.issue.title`, `event.pull_request.body`, …). An
allowlist that is wrong on one entry is a hole; a blanket ban that is
occasionally inconvenient is not. "Always go through `env:`" is one rule a
contributor can hold in their head, it currently costs nothing (all 11 workflows
already comply), and the escape hatch — add an `env:` line — is trivial.

### Gap 4 — keep or cut the three property tests → KEEP

D14 (YAGNI) proposed cutting ~60 lines because `artipacked` / `template-injection`
/ `cache-poisoning` duplicate zizmor rules running on the same commit. D4 (tests)
countered that all four assertions were mutation-verified to bite, run offline in
0.46s, and guard a property zizmor cannot check about itself.

**Decision: keep — but on narrower grounds than first drafted.**

Two tempting arguments were checked against the live repo and **both failed**:

| Candidate argument | Checked | Verdict |
|---|---|---|
| "These are the only *enforced* control on `develop`" | `gh api .../branches/develop/protection` → **404, not protected** | **False.** Nothing is enforced on `develop` — not this job, not zizmor. |
| "zizmor is skipped on some PRs, these are not" | The one `SKIPPED` check on the dependabot PRs is **SonarCloud**; `workflow-audit` has no `if:` guard | **False.** zizmor runs on every PR. |

What actually survives:

- **No network.** `uvx zizmor` downloads before it audits; these run offline in
  well under a second, so they bite pre-push and in every matrix leg — where a
  hardening slip is cheapest to fix. This is the only property zizmor cannot
  match, and it is what the module docstring already claimed from the start.
- **Mutation-verified.** Every assertion has been watched failing against a
  deliberate revert.
- `test_ci_runs_the_zizmor_workflow_audit` and the new doc-pin test were never in
  scope for cutting — they check properties zizmor cannot check about itself.

So the honest position is: in CI the three property tests **are** largely
redundant, and they are kept for local pre-push feedback and analyser
independence, at a cost of ~60 lines that run in 0.26s. That is a modest
argument stated at its real strength, not an enforcement argument — which is what
the first draft of this section wrongly claimed.

## 3. BDD scenarios

```gherkin
Feature: the workflow-hardening guard covers what it claims to cover

  Scenario: a .yaml workflow is not invisible to the guard
    Given a workflows directory containing "a.yml" and "b.yaml"
    When the guard enumerates workflow files
    Then both files are returned

  Scenario: the documented zizmor command is the one CI runs
    Given ci.yml pins zizmor to a specific version
    When docs/GITHUB.md quotes a zizmor invocation
    Then the two pins are identical

  Scenario: an unpinned gate is rejected
    Given ci.yml's workflow-audit runs bare "uvx zizmor"
    When the guard reads the pin from ci.yml
    Then it fails, rather than silently comparing nothing

  Scenario: a benign github context in a run block is still refused
    Given a run block interpolating "${{ github.run_id }}"
    When the template-injection guard inspects it
    Then it fails — the blanket ban is policy, not an oversight
```

The last scenario is already covered by the existing test's behaviour; it is
recorded here so the behaviour is understood as intended.

## 4. Task plan (TDD order)

Each task: failing test first, then the change, then green.

- [ ] **T1** — `_workflow_paths()` finds `.yaml`.
      Test: point the module's `WORKFLOWS` at a tmp dir holding `a.yml` + `b.yaml`,
      assert both are returned. Red on `*.yml`. Fix: `*.y*ml`.
- [ ] **T2** — the pin is derived from `ci.yml`, not a constant.
      Test: `_zizmor_pin_from_ci()` returns `zizmor==<version>` for the real
      `ci.yml`; raises/fails on an unpinned command. Fix: add the helper, delete
      `ZIZMOR_PIN`, rewire `test_ci_runs_the_zizmor_workflow_audit`.
- [ ] **T3** — `docs/GITHUB.md` cannot drift from `ci.yml`.
      Test: the doc contains the pin derived in T2. Red today only if drifted —
      so prove it bites by temporarily mutating the doc.
- [ ] **T4** — document gap 3 and gap 4 decisions in the module docstring /
      inline comments. No logic change.
- [ ] **T5** — `/gflow:check` (full Impeccable Routine).
- [ ] **T6** — self-review, then PR closing #568.

**Mutation check (non-negotiable):** every new assertion must be shown to fail
when the thing it guards is reverted. A guard that has never been seen red is
not a guard. #566's council verified the original four this way; the new ones
get the same treatment.

## 5. Out of scope

- **#567** — required status checks on `develop`. Blocked on a maintainer
  decision (protect `develop` and route the release back-merge through a PR, vs
  `enforce_admins: false`, vs leave it). Not a code change and not decidable here.
- Bumping zizmor itself. `1.29.0` stays; this PR only changes how the pin is
  guarded.
