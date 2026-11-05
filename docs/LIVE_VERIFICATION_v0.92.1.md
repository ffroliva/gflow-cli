# Live verification — v0.92.1

> Evidence for this patch release, gathered on 2026-10-30. Every row below was a run someone
> watched, not an inference.

## Environment

| | |
|---|---|
| Profile | `ffroliva` (real-browser Chrome strategy; `gflow auth status` verified the Flow session first) |
| Host Flow served | `flow.google.com` |
| Entrypoint | the real `gflow mcp run` stdio server, driven by an MCP SDK client (not in-process tool functions) |
| OS | Windows 11 |
| Tree | `develop@32657398` for the refusal runs; the `tools/list` run was repeated on the final release tree (server reports 0.92.1), which adds the version bump, docs, a corrected remediation hint and doc-review wording fixes to the schema text and one docstring |
| Veo credits spent | 0. Image quota: 0 (both generation calls were refused before submit) |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Schema states the `tools` item shape and the id + name pairing (#1121) | `tools/list` of `gflow mcp run` | ✅ The live server lists 19 tools. `gflow_generate_image` and `gflow_generate_video` both carry the new `description` on `tools`, `reference_entities` and `reference_entity_names`; the video `reference_entities` text adds "Character references need mode r2v there." These are the strings an MCP client hands the model | $0 |
| The schema's claim "an id without its name fails before submit" | `gflow_generate_image` over stdio | ✅ `reference_entities=["00000000-…"]`, no names, `wait=true`: the tool returned `status: failed`, `flow-host-migrated`, detail "a character given by entity id without a name is not ported yet (#639)", in 66 s. The refusal is raised on the migrated route before any submit | $0 |
| The same claim for video ("Character references need mode r2v there") | `gflow_generate_video` over stdio | ✅ `mode=r2v`, `reference_entities=["00000000-…"]`, no names, `wait=true`: `status: failed`, `flow-host-migrated`, detail "a character given by entity id without a name is not ported yet (#639)"; the worker log shows `FlowHostMigratedError` then browser teardown, no submit | $0 |
| Remediation hint no longer says video takes no character | `FlowHostMigratedError` text | Offline only: a string change made after the runs above, which show this hint is what an MCP caller receives | $0 |

## The five layers

1. **File count** — n/a: both calls were refused before submit, so nothing was generated. (The `out/` count taken during the runs proves nothing: that directory does not exist in the release worktree.)
2. **Magic bytes** — n/a: no media is produced by this release's changes.
3. **Shape** — `tools/list`: 19 tools; the three properties present on both generate tools with the descriptions above.
4. **Structlog invariants** — `auth_migrated_host_fallback_authenticated` on the auth pre-check; the refusal surfaced as the RFC 9457 `flow-host-migrated` problem type.
5. **User-confirmable artifact** — re-run `tools/list` against `gflow mcp run` with any MCP client (MCPJam Inspector, or the eval in `scripts/dev/mcpjam/`).

## Observed, not caused by this release

Both refused calls logged teardown warnings after the refusal (`TargetClosedError` on the image
call; `browser_teardown.context_close_error` with a `TimeoutError` on the video call). The tool still returned its structured failure. Not investigated: no change in this
release touches browser teardown.

## Not run

The `scripts/dev/mcpjam/` tool-selection eval (4/5 → 0/5) was run on the PR (#1121) and is
not repeated here: it drives a model, not Flow, and the schema strings it reads are the ones
verified above.

## Pre-tag gates

| Gate | Result |
|---|---|
| `/gflow:check` | ✅ all repo gates, ruff, format, pyright (0 errors); pytest 5647 passed, 11 skipped, coverage 93% |
| Live verification | ✅ the rows above |
| `/gflow:doc-review` | ✅ mechanical sections 1–7 pass (links, website mirror in sync, PII gate, release artifacts, CHANGELOG footer); council below |

### Doc-review council

_Council verdict: YELLOW across all 3 auditors (completeness, cross-reference, drift), no RED.
11 findings; 0 Tier 1. 9 Tier 2 fixed in the release commit: the video refusal run live (row
above); `docs/MCP.md` shows the `tools` item shape, calls `reference_entity_names` required on
flow.google.com, and says what `gflow_character_list` returns there; the video docstring no longer
calls the names optional; the `FlowHostMigratedError` hint says `@Name` on images and id + name
on reference-to-video; `docs/REFERENCE_STRATEGIES.md` and `llms.txt` name the r2v id + name
route; the schema says `wire-format` error instead of "exits 7", since an MCP tool has no exit
code; PROJECT_STATUS names the hint fix. 2 Tier 3 deferred: the hint still omits single-segment
`video extend` and the agent-only composer (both predate this release); no INDEX route to
`scripts/dev/mcpjam/`. Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._

## Post-tag evidence

| Check | Result |
|---|---|
| Signed tag | `v0.92.1`, SSH-signed (`git cat-file -p v0.92.1` carries one `BEGIN SSH SIGNATURE`) |
| `Release` workflow | run 41128416699, success |
| GitHub Release | `v0.92.1`, published 2026-10-30T21:07:03Z, not a prerelease |
| PyPI | `pip index versions gflow-cli` lists 0.92.1 as the latest |
| Release PR | #1133 merged into `main` (merge commit `9007b1c7`); SonarCloud gate OK, 0 new issues |
| Back-merge | `main` into `develop` as `e49cb1e5`; `main` is 0 commits ahead of `develop` |
