# Project creation on flow.google.com + MCP profile contention (bundle #864)

> **For agentic workers:** Run `/gflow:status --feature project-create-bundle` to find the next
> unchecked task. Run `/gflow:check` before every commit.

**Goal:** generating without a project works again — CLI and MCP — on accounts Flow serves from
flow.google.com, `gflow project create` / `rename` work there, and an MCP server waits out profile
contention instead of failing it.

**Issues:** #864 (bundle) · closes #561 · supersedes PRs #863 and #862 (@iceblue03) · refs #639

**Evidence:** [`2026-09-17-project-create-on-flow-google-com.md`](../../spikes/2026-09-17-project-create-on-flow-google-com.md)
— labs `project.createProject` 404 "Flow RPCs have been deprecated and disabled" (token sessions)
or 401 (tokenless), 12/12 on four profiles; flow.google.com creates by `jHPbke` (FAB `add` in
`flow-projects-page`) and renames by `o8DA4` (`flow-editable-text`), both measured.

**Predict:** CAUTION 7/10 (five personas, inline, 2026-09-17). Conditions carried into the tasks:
1. Fall back on the **observed** labs refusal (401/404 at the project route), never on host
   membership; `GFLOW_CLI_FLOW_HOST=labs.google` never falls back; `flow.google.com` goes direct.
2. Read success from the rpc reply (id + title), not from the URL.
3. Anchors structural only (`flow-projects-page`, `mat-icon` ligature, `flow-editable-text`).
4. Every "a fresh project cannot be created on flow.google.com / `--project` is required" claim in
   code messages, MCP docstrings and docs is corrected in the same PR.
5. No new setting. #863's default project is the WS3 sticky-defaults workstream
   (`2026-09-14-community-feedback-uplift`), which owns the source/trust design it needs.

## Scenarios

Create (`migrated_composer.create_project`)
- S1 FAB click → `jHPbke` reply `[id, [title]]` → `ProjectInfo`; requested title differs → rename.
- S2 reply title already equals the requested title → no rename.
- S3 FAB never visible → known-landing diagnosis first, else `UiSelectorDriftError` naming the anchor.
- S4 clicked, no `jHPbke` reply in time → `TransportTimeoutError`.
- S5 reply carries no project UUID → `WireFormatError`.

Rename (`migrated_composer.rename_project`)
- R1 fill + Enter → `o8DA4` reply echoes the title.
- R2 reply echoes a different title → `WireFormatError`.
- R3 no `flow-editable-text` input → known-landing diagnosis, else `UiSelectorDriftError`.

Routing (`FlowApiClient.create_project` / `rename_project`)
- C1 `flow_host=flow.google.com` → migrated directly; no labs request.
- C2 `auto`, labs 200 → labs result; migrated never touched.
- C3/C4 `auto`, labs 404 / 401 → migrated.
- C5 `labs.google`, labs 404 → original error.
- C6 `auto`, labs 403 / 400 → original error, no fallback.

MCP profile contention (#862)
- M1 `gflow mcp run` / `gflow serve`, `GFLOW_CLI_LEASE_WAIT_SECONDS` unset → loaded setting is 180.
- M2 explicit value (incl. `0`) wins.
- M3 two concurrent tool calls on one profile inside one server → serialized, not `ProfileLockedError`.

Surfaces: CLI `image t2i/i2i`, `video t2v/i2v/r2v`, `project create/rename`, `image upload`, image
batch; MCP `gflow_generate_image` / `gflow_generate_video` (worker path). No new options, so the
parity gate is unchanged; docstrings are the MCP axis that moves.

## Tasks

- [ ] 1. RED+GREEN S1–S5, R1–R3: `create_project` / `rename_project` in `migrated_composer.py`
- [ ] 2. RED+GREEN C1–C6 in `api/client.py` for create and rename
- [ ] 3. Correct stale claims: `run_video`/`run_images` messages + docstrings, `migrated_can_serve`
      docstring, MCP tool docstrings, `docs/USAGE.md`, `docs/MCP.md`, `KNOWN_ISSUES.md`, CHANGELOG
- [ ] 4. RED+GREEN M1–M3 (`mcp/server.py`, `mcp/tools.py`); docs for the MCP wait default
- [ ] 5. `/gflow:check`
- [ ] 6. e2e (zero credits): `project create` + `rename`, `image t2i` without `--project` (CLI),
      `gflow_generate_image` without `project` (MCP)
- [ ] 7. Council review, live-verify ledger, PR, Sonar
- [ ] 8. Release; close #561/#863/#862 with thanks; notify reporters on #561, #639, #863, #862
