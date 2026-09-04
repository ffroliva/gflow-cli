---
name: doc-examples-are-untested-fixtures
description: "Fenced manifest examples in docs/ are untested fixtures — when a validator gains a rejection, grep every toml/jsonl block in the SAME PR or you ship a documented crash"
---

**A fenced example in `docs/` is a fixture nothing runs.** No gate parses it. `check_doc_links.py`
checks links, not content. `generate_website_docs.py --check` only proves the mirror matches — if
the canonical example is wrong, the mirror is *identically* wrong and the gate stays green.

**Three observed instances, all the same shape:**
1. `docs/USAGE.md` chain JSONL shipped `{"model": "veo-lite", "duration": 4}` — a guaranteed
   mid-spend crash — as THE canonical example (issue #635).
2. `docs/MOVIE.md` paired `model = "veo-lite"` with `duration = 8`. Missed by the very PR that
   fixed #1 and hardened the movie parser, then caught by two independent reviewers.
3. `docs/USAGE.md`'s movie example used `duration = 5`, invalid against
   `_VALID_DURATIONS = {4, 6, 8, 10}` since it was authored.

**Why:** [[doc-flag-rename-sweep-reference-docs]] already says "sweep ALL reference docs in the
SAME PR", but its trigger was written as a *flag rename*. A new **validator rejection** is the
same event and did not read as one.

**How to apply:** whenever a validator gains a rejection, grep every fenced ```toml / ```jsonl /
```bash block in `docs/` **and** `website/docs/` for input the new rule would now reject — before
opening the PR. Check scaffold generators too (`gflow movie template`'s `_TEMPLATE` in
`cli_movie.py` was clean, but only by luck of a prior fix). The mirror check will NOT catch it.

**Corollary:** a test fixture pinning the bad combination is the same defect wearing a green tick.
`tests/cli/test_movie_manifest.py::_FULL_TOML` pinned `veo-lite` + `duration = 8` as valid, and was
found only because the new guard made it fail — not by anyone reading it. When you add a guard,
expect it to break a fixture, and treat that break as a FINDING, not an annoyance to patch past.

Related: [[stale-test-discovery]], [[video-model-capability-matrix]], [[doc-review-council-catches-same-release-errors]].
