---
name: memory-is-working-dir-keyed
description: "Claude Code keys memory by working-directory path, so any agent on another machine — sandboxed triage, CI, a fresh clone — starts with an EMPTY namespace. Anything an off-machine agent must read has to live in the repo. The review-relevant subset is published at docs/superpowers/memory/."
---

**Claude Code memory is keyed by working-directory path, not by repo identity.**
A fresh clone at a different path — the autopilot's `/opt/gflow-cli`, a CI
runner, another machine — starts with an **empty** memory namespace. None of the
~230 accumulated files are visible there unless they were synced.

This was documented in
`docs/superpowers/specs/2026-07-04-pr-triage-autopilot-design.md` from the start,
including a one-way local→VPS sync. **The sync was never implemented.**

**What that cost.** On 2026-09-04 the PR-triage autopilot reviewed external PR
#650 and reported D5 as **GREEN — "no Claude-memory entry contradicts this PR"**
— from a directory it could not read, while the local store recorded that exact
PR as REJECTED. Structural blindness was published as a positive finding, under
the maintainer's identity, on a public PR. That is the assumed-not-verified
failure the council protocol forbids by name, arriving through a deployment gap
rather than a reasoning error.

**The fix, shipped 2026-09-04 (PR #656).** The review-relevant subset now lives
in the repo at **`docs/superpowers/memory/<slug>.md`**, so `[[slug]]` in a skill
resolves to a real file for any agent that can read the tree — no sync, no
secret, no VPS change, because the repo is already bind-mounted read-only into
every sandbox. `scripts/ci/check_council_memory.py` enforces it both ways: a
citation with no file fails, and a file no dimension cites fails.

**How to apply.**
- **Published copy is canonical for review; the private store is upstream
  drafting.** A fact in both is edited in the repo first.
- To add one: write `<slug>.md`, cite it from the Dimension → Slugs table in
  `skills/pr-council-review/SKILL.md`, run the gate. Step 2 is the one people
  skip, and it is enforced because an uncited file is unroutable.
- Only review-relevant, publishable facts go there. Session handoffs, tooling
  notes, and anything naming an account, profile, project UUID or local path
  stay private — the gate rejects those patterns.
- **Never report a memory dimension as GREEN from a store you could not read.**
  Empty or absent is `UNAVAILABLE`. Verify you can actually read it, and quote
  the file count, before any verdict.
- The general form: *wire the rule, don't document it* — a documented sync that
  nobody built is worth exactly nothing, and worse than nothing if a reviewer
  assumes it ran. See [[wire-the-rule-dont-document-it]].
