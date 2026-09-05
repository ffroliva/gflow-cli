---
name: sonarcloud-new-code-gotchas
description: SonarCloud PR gate traps — new_maintainability_rating fails on PRE-EXISTING smells your diff merely touches; the gate API returns STALE results until the new analysis check finishes
---

Two SonarCloud PR-gate traps hit on gflow-cli PR #158 (recording feature):

1. **`new_maintainability_rating` can fail on a PRE-EXISTING smell your diff merely "touches."** Adding one `-> dict[str, Any]` annotation gave the existing S1192 "duplicated literal `dict[str, Any]`" issue a NEW-code location, flipping the rating A→B — even though the smell predated the branch (its `creationDate` was days earlier and its lines weren't in the diff). The other two smells (`application/json` S1192 at L727, cognitive-complexity S3776 at L953) stayed OLD-code because the diff didn't touch them. Fix WITHOUT gaming: collapse the duplicated literal into ONE module-scope alias (`JsonObject = dict[str, Any]`) via `Edit replace_all` + a single alias def, so the literal appears once (< the S1192 threshold) → issue vanishes. pyright/tests verify it's safe (type annotations are runtime-erased).
2. **The quality-gate API returns the STALE (previous-commit) result until the new analysis completes.** After pushing the fix, `api/qualitygates/project_status?pullRequest=N` kept reporting the OLD B for ~2.5min. Always check the **SonarCloud CHECK status first** (`gh pr checks N` → the "SonarCloud analysis" row must read `pass`/`fail`, not `pending`); only THEN trust the gate API. The SonarCloud job (~50s) starts after the ~3.5min test matrix.

Diagnose with the SonarCloud API (token in `.env.local`): `qualitygates/project_status` (failing conditions) + `issues/search?componentKeys=ffroliva_gflow-cli&pullRequest=N&rules=python:S1192,python:S3776` (offending lines + `creationDate` to prove pre-existing). Extends [[sonarcloud-hotspot-review-workflow]].
