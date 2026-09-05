---
name: flow-capabilities-are-cohort-dependent
description: "A Flow capability table needs a cohort dimension. N accounts inside one cohort is still N=1 - agreement across accounts is evidence of a shared cohort, never of universality. Ask which host AND which profile before encoding any static capability predicate."
---

**A Flow capability table needs a cohort dimension. N accounts in the same cohort
is still N=1.**

On 2026-08-14 the video duration matrix was encoded as fact after two accounts in
two locales agreed, and [[video-model-capability-matrix]] explicitly recorded *"the
one-cohort caveat is retired."* On 2026-09-04 a third profile on the **same**
`labs.google` frontend showed the opposite - `4s/6s/8s` on all three
selectable Veo 3.1 models (`lower_priority` missed its picker, so it stays
unmeasured) -
with different credit prices (5/10/100 vs 10/20/100). The two agreeing accounts were
one cohort, not two.

**Why:** Google runs experiment buckets. Two accounts landing in the same bucket
agree with each other and tell you nothing about the population. The failure is not
a bad capture - both captures were honest and both passed their own read-validity
checks.

**How to apply:**
- The public caveat lives in `KNOWN_ISSUES.md` under "Video duration control is
  absent on some account cohorts", plus its `website/docs/` mirror. When the
  cohort key is found, update the memory AND both docs. The code gate
  (`supports_duration()`, `api/video.py`) is still static and still refuses
  `--duration` on every named Veo model at the CLI edge (exit 2) - docs must not
  imply otherwise while [[#650]] is open.
- Before encoding any Flow capability as a static predicate, ask: which **host**
  (`labs.google` vs `flow.google.com`), which **profile**, which region - and is
  there a session-level read that makes the static table unnecessary?
- A static table that says "supported" when the account's UI has no control is as
  wrong as one that says "unsupported" when it does. Both directions cost the user.
- Reviewer heuristic: a PR that relaxes *or tightens* a capability gate on one
  capture gets the same question. Do not reject on "our capture disagrees" - that is
  symmetric evidence, not a refutation.
- When two captures conflict, check whether the **instrument** changed between them
  before concluding cohort difference. On #650 the collector's selector had widened;
  it was ruled out on the DOM evidence, but it had to be ruled out.

Applies to duration, ingredients, resolution tabs, count tabs, and aspect. See
[[video-model-capability-matrix]], [[flow-recon-must-run-on-denon82-ffroliva-migrated]],
[[pr-must-verify-on-affected-surface]].

**OUTCOME 2026-09-04/05:** `supports_duration()` is gone (#650): one shared `validate_duration_for_model()` backs DTO, CLI, chain, movie and MCP; whether the control is rendered is decided pre-submit at zero cost (exit 23 on labs, exit 11 on the migrated host). The line above describing the static refusal is history.
