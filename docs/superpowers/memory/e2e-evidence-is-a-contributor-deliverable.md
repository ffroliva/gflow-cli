---
name: e2e-evidence-is-a-contributor-deliverable
description: "E2E is the decisive test layer; a PR touching a Flow surface owes an e2e test it ran or wrote. Offline-green and a live-verify ledger are both insufficient substitutes"
---

**Rule (since PR #675, merged 2026-09-05):** a behavior change **that touches a Flow surface**
owes e2e evidence — the `tests/e2e/` test the author ran, with its result, or a new one they
wrote. Not a nice-to-have: **absent with no reason given, D4 scores RED** and consensus is RED.
It is deliberately not YELLOW — a YELLOW is dismissable with a one-line logged justification,
and *"the live runs in the PR body are good enough"* is precisely the justification that would
be written, reopening the substitution below through the escape valve. If the author states why
they could not run it, that is the documented exemption: YELLOW, blocked on a maintainer run.
A change touching no Flow surface (docs, help text, exit-code plumbing) is out of scope, and
the PR says that rather than leaving the box blank.

**Why offline-green is never enough here.** This project reverse-engineers a blackbox. Unit,
integration and BDD tests prove only that *our* code does what we think it does; they cannot
prove Flow still behaves the way we captured it. External PR #671 (`gflow credits`) shipped
eight offline test files, zero e2e, and a fully green pipeline — and the HTTP fast path it
added failed with `AisandboxAuthError` on both maintainer profiles the first time anyone ran
it, falling back to a full Chrome launch per profile. An `e2e_auth`-marked test would have
caught that before the PR was opened. See [[pr-must-verify-on-affected-surface]].

**A live-verify ledger is not an e2e test.** `/gflow:live-verify` drives CLI commands by hand
and writes a 5-layer ledger to a gitignored `tmp/live-verify/` note; it never runs
`pytest -m e2e`. The two are complementary and neither substitutes for the other: an e2e test
is a **re-runnable regression** that fails when Flow drifts, a ledger is a **narrative record**
of one run on one account. A PR body describing live runs satisfies
[[verification-ledger-5-layer]], not this rule. Accepting one for the other is how the rule
erodes — it was proposed within an hour of the rule landing, on PR #669.

**Why it falls on the contributor.** CI cannot run these — they need a live authenticated
profile — so e2e is the one gate only someone with credentials can close. Cost is rarely the
objection: `e2e_auth` and `e2e_image` spend zero credits, and read-only or inspection paths
belong under `e2e_auth`.

**How to apply (reviewer):**
- Ask which `tests/e2e/` test covers the change. No answer, no new test, and no stated reason
  on a Flow surface → D4 RED, not YELLOW (see above for why the colour matters).
- A PR body full of live runs does not close it. Ask for the test.
- When a test *is* named, verify it: cite its file:line, confirm it exists at `REVIEWED_SHA`,
  and confirm it exercises the changed surface. A passing e2e test that never touches the
  changed path is [[pr-must-verify-on-affected-surface]] wearing an e2e label.
- Do not hold a PR opened before #675 merged (`e7a09d8`, 2026-09-05 19:15Z) to this rule
  retroactively; flag it forward-looking. Anchor on the merge, not the day — #669 and #671
  were both opened hours earlier that same date.
- The rule lives in `CONTRIBUTING.md` (lifecycle table + § Test categories), `AGENTS.md`
  (§ Testing instructions), and `.github/PULL_REQUEST_TEMPLATE.md`. `tests/test_documentation_gate.py`
  pins all three, so deleting one fails CI rather than passing quietly.
