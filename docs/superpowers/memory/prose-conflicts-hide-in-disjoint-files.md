---
name: prose-conflicts-hide-in-disjoint-files
description: "Two PRs that edit DIFFERENT files can still contradict each other in prose. Git merges them clean, no conflict marker, and no CI gate can see it. When a PR changes behaviour, grep the docs it does NOT touch for the old behaviour stated as present-tense fact."
---

**Git only detects conflicts in the same lines of the same file. Two PRs that
edit different files can still ship a repo that contradicts itself, and nothing
in the toolchain will notice.**

On 2026-09-04, #655 rewrote the `KNOWN_ISSUES.md` row describing gflow's video
duration gate, correctly, for the code as it stood that morning. #650 then
changed exactly that behaviour while deliberately **not** touching
`KNOWN_ISSUES.md` — the maintainer had asked the contributor to drop it to avoid
a merge conflict. The two merged cleanly. Three sentences in `KNOWN_ISSUES.md`
became false the moment #650 landed:

- "`--model omni-flash`, the only model gflow currently allows a duration on"
- "gflow refuses `--duration` on every named Veo 3.1 model at the CLI edge"
- "the flag is unavailable on every cohort today"

Three separate council dimensions (D1 correctness, D9 docs, D15 parity) found it
independently. No mechanical gate did, and none could: `check_doc_links.py`
validates link targets, `generate_website_docs.py --check` validates the mirror
is byte-identical to canonical, and both were green. **A mirror can be
identically wrong, and a link can point at a page that lies.**

**Why the usual instinct fails.** Asking a contributor to drop a file to dodge a
conflict is the right call for *merge mechanics* and the wrong one for
*correctness* — it converts a visible conflict into an invisible one. If the
file must come out of their PR, the correction becomes yours, and it has to land
with or immediately after theirs, never before.

**How to apply.**
- When reviewing a PR that changes behaviour, grep the docs it does **not**
  touch for the old behaviour stated as present-tense fact. `KNOWN_ISSUES.md`,
  `PROJECT_STATUS.md` and `docs/MCP.md` are the usual offenders here, because
  they describe current state rather than usage.
- When two PRs are in flight on one behaviour, name which one owns the doc, and
  say so on both.
- A doc correction that describes a PR's post-merge state **must not merge
  first**. Open it as a draft gated on the other, or land it immediately after.
- Watch for the mirror image too: while fixing an old absolute, it is easy to
  write a new one. On the same day, #650 fixed "Veo renders no duration control"
  in `USAGE.md` and simultaneously introduced "4/6/8 for Veo 3.1" with no cohort
  caveat in `docs/MCP.md` and `docs/MOVIE.md`.

See [[flow-capabilities-are-cohort-dependent]],
[[doc-examples-are-untested-fixtures]], [[project-status-md-drifts-across-releases]].
