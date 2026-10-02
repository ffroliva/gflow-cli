# Live verification — v0.82.0

> Evidence for the user-facing changes in this release, run against real Flow on
> 2026-10-01 during development (PRs #928, #929, #930, #931, #932). Where a check could not
> be run, the blocker is named. Each row is a run that someone watched, not an inference.

## Environment

| | |
|---|---|
| Profile | `ci-probe` (real-browser Chrome strategy) |
| Host Flow served | `flow.google.com` |
| Transport | `ui_automation` → migrated composer |
| OS | Windows 11 |
| Date | 2026-10-01 |
| Veo credits spent | 1 × `veo-lite` clip (#926 e2e); everything else images (daily quota) or $0 |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| `"ref": "batch:N"` in `gflow run --config` (#913, PR #931) | `gflow run` on flow.google.com | ✅ BDD e2e, 7 runs (run 3 and the last 3 pass, 9/9 refs in place) | ~24 images |
| Local-file `ref`, uploaded once (#913, PR #932) | `gflow run` | ✅ CLI run + BDD e2e | 4 images |
| Run successes recorded, i2i lineage (#913) | catalog | ✅ asserted by the e2e against the run's isolated DB | — |
| Manifest refs refused, not dropped (#913, PR #930) | `gflow run`, `gflow image batch` | ✅ real CLI: exit 11 in 2 s / exit 2, no browser | $0 |
| Submit guard on the existing upload path | `gflow image i2i --ref <file>`, MCP i2i | ✅ CLI exit 0 guard `passed`; MCP e2e passed | 2 images |
| UUID refs stay unported on flow.google.com | CLI `i2i --ref <uuid>`, MCP | ✅ exit 36 / refused | $0 |
| Video JSON e2e drops `--duration` (#926, PR #928) | `tests/e2e/test_json_output_e2e.py` | ✅ passed (71 s) | 1 veo-lite clip |
| Docs: chooser/consent → `gflow auth login` (#925, PR #929) | docs only | n/a: no Flow surface | — |

## `batch:N` — #913

`tests/e2e/test_manifest_refs_bdd.py` (`@e2e @e2e_image`), rows `0`, `1`, `2→1`, `3→2`:

1. **Count:** 4 files `prompt_0_*` … `prompt_3_*`.
2. **Magic bytes:** each JPEG (`ff d8 ff`) or PNG.
3. **Shape:** min side ≥ 512 px (1024×1024 measured).
4. **Structlog:** `migrated.existing_references_attached` ×2, no `migrated.references_attached`
   (no upload), `migrated.image_submit_guarded` `passed` ×2; the final run also logged one
   grid miss and one picker miss, each recovered by an editor reload.
5. **User-confirmable:** the catalog records each child as `i2i` with its parent as `input`.

Run history (what the e2e caught, fixed before release):

| Run | Result | Cause |
|---|---|---|
| 1, 2 | row 1 failed | the grid is a per-editor-load snapshot; waiting in the page never shows a just-generated tile |
| 3 | pass | reload-on-miss for the grid |
| 4 | row 1 failed | the `@` picker search is also a snapshot (0 options ×3) |
| 5, 6, 7 | pass | reload-on-miss for grid and picker |

**Collision past the first option:** two images captioned "a single red apple"; the newer
chosen at option index 1, a media chip landed (`probe_collision_binder.py`, $0).

**Gate spikes** (`scripts/dev/spike_ref_gate.py`): the picker is project-scoped (with a
control); a mismatched reference is refused by the body check (negative control); Enter on an
empty picker does not submit.

## Local-file references — #913

Two rows naming one file: one `migrated.reference_uploaded`, two in-place attaches, guard
`passed` ×2, both rows `i2i` with `reference_media_ids` = the upload. Spike first:
`scripts/dev/spike_upload_token_join.py` (uploads join grid tile ↔ picker token, round trip
true).

## Blockers (named, not waived)

- **Accounts served labs:** labs answers 308 on the three profiles here that hold a live Flow session, so the labs driver's
  handling of `batch:N` refs and the REST upload fallback (`GFLOW_CLI_FLOW_HOST=labs.google`)
  are unobserved.
- **One stalled e2e invocation** (PR #932): it stopped at startup with no DB writes for
  30 min and was stopped; not reproduced on two reruns. Cause not isolated.

## Pre-tag gates

- **PRs** #928, #929, #930, #931, #932: CI 16/16 each, including SonarCloud.
- **Branch councils:** #931 6 reviewers (2 GREEN, 4 YELLOW, every must-fix fixed and verified
  live); #932 correctness/security/parity (GREEN after one fix).
- **`/gflow:doc-review`:** 3 auditors (completeness, cross-reference, drift), each YELLOW
  with no false claim; every finding for this release fixed before the tag (agent entry
  points and INDEX route to `gflow run` refs, the no-resume entry names local files, the
  exit-36 row and the CHANGELOG exception list name both ref forms, `--fail-fast` moved
  under Error semantics, labs upload wording narrowed to what the code routes).
- **`/gflow:check`:** all gates green on the release branch (4817 passed, 93% coverage).

## Post-tag evidence

- **Release workflow:** [run 36928643524](https://github.com/ffroliva/gflow-cli/actions/runs/36928643524) — success (build-and-publish, mcp-registry publish).
- **PyPI:** `gflow_cli-0.82.0-py3-none-any.whl` and `gflow_cli-0.82.0.tar.gz`, uploaded 2026-10-01T21:26Z.
- **GitHub Release:** [v0.82.0](https://github.com/ffroliva/gflow-cli/releases/tag/v0.82.0), published 2026-10-01T21:26Z.
- **Release PR:** #937, merged to `main` with a merge commit (`00ff5720`).
- **Back-merge:** `main` → `develop` (`e45532d0`).
- **Install from PyPI:** `uvx --from gflow-cli==0.82.0 gflow --version` → `gflow, version 0.82.0`.
