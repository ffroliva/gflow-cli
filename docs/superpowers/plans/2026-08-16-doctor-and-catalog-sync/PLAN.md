# Doctor + Catalog Sync Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature doctor-and-catalog-sync`
> to find the next unchecked task. Implement one task at a time. Run
> `/gflow:check` before every commit.

**Goal:** Ship `gflow doctor` (#542, read-only pre-flight diagnostics),
`gflow data sync --names` (#543, catalog↔Flow display-name reconciliation),
and refresh-on-miss name resolution (#546) so stale/missing names stop
degrading UUID references.

**Architecture:** Doctor = pure check functions in `services/doctor.py` over a
read-only (`mode=ro`) raw DB connection (schema inspection helper lives in
`data/store.py`), thin `cli_doctor.py` on top. Sync = orchestration in
`services/catalog_sync.py` (the `services/mentions.py` precedent: client +
repository, both dependency arrows legal — **never** placed in `data/`),
fetching `flow.projectInitialData` by **direct authenticated GET** (~0.5 s, no
navigation; live-proven on #543) with a dedicated
`workflows[].metadata.primaryMediaId → displayName` parser (the listing item
shape ≠ `batchGenerateImages`; no `fifeUrl`). Refresh-on-miss = a name-resolver
callback injected into the transport by the CLI layer (transport never imports
`data/`). Catalog stays the single store; sync state lives in `metadata_json`
via atomic `json_set` patches.

**Predict verdict:** CAUTION 4.5/10 as originally specced → all three blocking
unknowns since resolved by live spikes (endpoint identity, payload
completeness, #174-cohort irrelevance — evidence on #543). Effective GO with
the mitigations below baked into tasks.

**Decisions locked (do not re-litigate):**
- Sync **writes by default**; `--dry-run` is the opt-in preview (user decision
  2026-08-16). Dry-run still fetches ("don't write", not "don't visit").
- Doctor v1 has **no `fix` subcommand**, no `--fail-on`/`--only`/`--except`;
  remediation is suggested-command text (npm model). `--json` is plain and
  marked experimental (schema-versioning deferred).
- No attempt-ordering machinery: work list ordered by project `created_at`.
  Sync state = `sync.named_at` provenance + `sync.status: missing_remote` only.
- A changed remote name **overwrites** the cached one (names are mutable via
  the Flow Agent), always provenance-stamped.
- Privacy gate: sync refuses under `history_prompts=redacted` (exit 11);
  doctor reports `display_name_missing` as INFO ("suppressed by privacy
  setting") in that mode, never as fixable.

**Risk register:**
| Severity | Risk | Mitigation (task) |
|---|---|---|
| High | False-permanent `missing_remote` from a partial listing | Ghost-mark only when the parsed payload has zero pagination-marker keys and ≥1 media item; never on HTTP/parse failure (S3) |
| Medium | Sweep continues after a WAF 403 → score escalation | Abort the whole run on 403/`WafRejectionError` (S3) |
| Medium | Harvested ids are remote bytes later interpolated into selectors | Strict UUID-regex validation at parse time; non-conforming rows dropped with a counted warning (S2) |
| Medium | Doctor `--json` leaks: signed `fife_url` (stored key ∉ `SENSITIVE_URL_KEYS`), profile emails, username paths, caption text | Add `"fife_url"` to `SENSITIVE_URL_KEYS`; doctor output rule = `safe_path_text` paths, `CommandHasher` emails, UUIDs-never-names, C0/C1 control-char scrub (D2/D4) |
| Medium | Doctor exit 1 conflates findings with crash | Dedicated exit 33 via `ctx.exit` after report (D4) |
| Medium | Unbounded default checks on big catalogs | `PRAGMA quick_check` (not `integrity_check`); no SHA recompute in v1 (D3) |
| Low-Med | `DataStore.open()` migrates — doctor must not | Raw `mode=ro` URI connection first; open failure degrades to a finding, not a crash (D2/D3) |
| Low | Concurrent recorder vs sync writes | Single-statement `json_set` in `BEGIN IMMEDIATE`; pin the `upsert_asset` ON CONFLICT(id) invariant with a test (S4) |
| Low | Double progress output on TTY | Human lines only when structlog renders JSON; otherwise events only (S5) |
| Low | cp1252 consoles mojibake severity glyphs | ASCII tags `[PASS]/[INFO]/[WARN]/[FAIL]` (D4) |

**Out of scope (tracked, not built here):** `doctor fix`, `--verify-hashes`,
schema-versioned doctor JSON, MCP doctor tool (parity exemptions with reasons
instead), opportunistic recorder harvesting (#543 comment — follow-up),
mapping legacy `BatchPartialError`, and **UI-created (untracked) project
discovery/import** — sync v1 reconciles catalog-known rows only; the future
`--discover` (report-only, via the paginated `project.searchUserProjects`
endpoint) and explicit `--import` scopes are designed on #543 and deliberately
deferred until `--discover` telemetry shows demand.

---

## File structure

### New files
```
src/gflow_cli/services/doctor.py
  Pure check functions → frozen Finding dataclasses; no Click, no writes.
src/gflow_cli/cli_doctor.py
  `gflow doctor` command: report rendering, --json, exit 33.
src/gflow_cli/services/catalog_sync.py
  Sync orchestration: work list, per-project fetch, parse, write, summary.
scripts/dev/capture_project_listing.py
  Formalized #543 spike: direct projectInitialData GET + pair dump (recon tool).
tests/services/test_doctor.py
tests/services/test_catalog_sync.py
tests/cli/test_cli_doctor.py
tests/cli/test_cli_data_sync.py
tests/e2e/test_data_sync_names_e2e.py
tests/e2e/test_refresh_on_miss_e2e.py
```

### Modified files
```
src/gflow_cli/data/store.py         inspect_schema(path) + open_readonly(path) helpers
src/gflow_cli/data/redaction.py     add "fife_url" to SENSITIVE_URL_KEYS
src/gflow_cli/data/repository.py    work-list query; atomic json_set name/ghost writers
src/gflow_cli/api/client.py         fetch_project_listing(project_id) via context request GET
src/gflow_cli/api/transports/ui_automation_video.py  name-resolver callback on picker miss (#546)
src/gflow_cli/cli_data.py           `sync` command registration (function-scoped imports)
src/gflow_cli/cli.py                doctor registration
src/gflow_cli/errors.py             SyncPartialError → exit 34 (RETRYABLE)
tests/mcp/test_cli_parity.py        exemptions: doctor, data sync (reasons documented)
.env.template                       add GFLOW_CLI_HISTORY_PROMPTS
docs/USAGE.md / CONFIGURATION.md / MEDIA_LIBRARY.md / INDEX.md / CHANGELOG.md
```

---

## Phase D — `gflow doctor` (#542)

### Task D1 — Doctor test scaffold (red)

**What:** Red unit tests pinning the check inventory, finding shape, redaction
rules, and exit contract before any production code.

**Files:** `tests/services/test_doctor.py`, `tests/cli/test_cli_doctor.py`

**Steps:**
- [ ] Freeze the v1 check list as test parametrization: `catalog.display_name_missing`,
  `catalog.local_file_missing`, `catalog.sha256_null`, `db.migration_drift`
  (incl. newer-DB-than-binary), `db.wal_state` (quick_check + stale sidecars),
  `operations.stuck_started`, `queue.stuck_processing`, `env.deprecated_vars`
  (PREFER_CLASSIC/FORCE_AGENT_UI vs UI_MODE, removed GEMINI_API_KEY,
  DB_PATH env-vs-settings disagreement), `env.browsers_missing`, `auth.files_present`.
- [ ] Fixture DBs (tmp SQLite built via migrations) seeded per defect.

**Tests created (red):**
- [ ] each check flags its seeded defect and stays silent on a clean DB
- [ ] `display_name_missing` under `history_prompts=redacted` → severity INFO, remediation text says suppressed-by-privacy
- [ ] doctor never writes: DB file bytes identical before/after a run against a stale-schema DB (no migration applied)
- [ ] findings identify rows by UUID only — no display-name values anywhere in output
- [ ] paths rendered via `safe_path_text`; profile emails hashed; control chars stripped
- [ ] CLI: findings → exit 33; clean → exit 0; internal DataStoreError → 16
- [ ] `--json`: parseable envelope, `overall_status`, per-check entries; marked experimental in `--help`

### Task D2 — Data-layer helpers + redaction fix

**What:** Read-only DB access that cannot migrate, schema inspection, and the
`fife_url` redaction gap — the pieces doctor builds on.

**Files:** `src/gflow_cli/data/store.py`, `src/gflow_cli/data/redaction.py`, tests

**Steps:**
- [ ] `DataStore.open_readonly(path)` → `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`
  + `PRAGMA query_only=ON`; failure raises a typed error the caller degrades to a finding
  (precedent: `experimental/sapisidhash.py:76`).
- [ ] `inspect_schema(path)` → `{user_version, applied_migrations, expected_migrations, drift, newer_than_binary}`
  without touching `apply_migrations` (schema knowledge stays in `data/`).
- [ ] Add `"fife_url"` to `SENSITIVE_URL_KEYS` (`redaction.py:9`) — the recorder
  stores snake_case (`recorder.py:487`) which the current set misses.

**Tests created:**
- [ ] `open_readonly` on a WAL DB with stale sidecars fails typed, not crash
- [ ] `inspect_schema` reports drift + newer-DB against fixture DBs
- [ ] `redact_metadata` now masks a stored `fife_url` value

### Task D3 — `services/doctor.py` checks

**What:** The check functions, each returning frozen `Finding` dataclasses
(`check`, `severity: pass|info|warn|fail`, `summary`, `remediation`,
`row_uuids`), pure and import-light.

**Files:** `src/gflow_cli/services/doctor.py`

**Steps:**
- [ ] `Finding` frozen dataclass + `run_all(db_path, settings) -> DoctorReport`.
- [ ] Catalog checks via `open_readonly` + JSON1 (`json_extract` precedent `repository.py:968`).
- [ ] `db.wal_state` uses `PRAGMA quick_check` (never `integrity_check` in v1).
- [ ] Env checks read `Settings` semantics only (types already validated by pydantic).
- [ ] `auth.files_present` reuses `auth.status()` / `profile_store.list_profiles()`;
  `env.browsers_missing` reuses `installed_chromium_version() is None`.
- [ ] Every warn/fail carries a copy-pasteable suggested command
  (`gflow data prune --dry-run`, `gflow data sync --names`, `gflow auth login`, …).
- [ ] Make D1's service-layer tests green.

### Task D4 — `cli_doctor.py` + registration

**What:** The `gflow doctor` command: grouped ASCII report, brew-style caveat,
`--json`, exit 33.

**Files:** `src/gflow_cli/cli_doctor.py`, `src/gflow_cli/cli.py`,
`tests/mcp/test_cli_parity.py`

**Steps:**
- [ ] Click command; register in `cli.py` (`main.add_command`, pattern `cli.py:419-429`).
- [ ] Report: category groups, `[PASS]/[INFO]/[WARN]/[FAIL]` ASCII tags, caveat
  line before the first warning ("diagnostic signals, not a to-do list").
- [ ] `--json` via a new `json_output.doctor_payload()` builder; redaction rules
  from D1 enforced in ONE emitter path shared by text and JSON.
- [ ] Findings-present → `ctx.exit(33)` (documented in `--help` and AGENTS.md exit-code range note).
- [ ] MCP parity exemption entry with reason ("interactive diagnostic; MCP tool deferred").
- [ ] Make D1's CLI tests green.

### Task D5 — Doctor docs

**What:** User-facing documentation for the new command.

**Files:** `docs/USAGE.md`, `docs/INDEX.md`, `CHANGELOG.md`, `.env.template`

**Steps:**
- [ ] USAGE section: check table, severities, exit 33, `--json` (experimental), caveat.
- [ ] `.env.template`: add `GFLOW_CLI_HISTORY_PROMPTS` (doctor/sync remediation references it).
- [ ] INDEX routing row + CHANGELOG `[Unreleased]` entry.
- [ ] Regenerate website mirror; USAGE is published.

---

## Phase S — `gflow data sync --names` (#543)

### Task S1 — Sync test scaffold (red)

**What:** Red tests for the parser, repository writers, orchestration
decisions, and CLI contract.

**Files:** `tests/services/test_catalog_sync.py`, `tests/cli/test_cli_data_sync.py`,
`tests/data/test_repository.py` (extend)

**Tests created (red):**
- [ ] parser: saved-payload fixture (sanitized from the 2026-08-16 spike) →
  `primaryMediaId→displayName` map + media-presence set; malformed UUIDs dropped + counted
- [ ] ghost predicate: pagination-marker key present → NO ghost writes; HTTP/parse failure → NO ghost writes; clean complete payload → absent UUID marked
- [ ] repo writer: `json_set` patch preserves unrelated `metadata_json` keys; overwrites a changed name; stamps `sync.named_at`; never resurrects rows
- [ ] `upsert_asset` conflict-target invariant pinned (ON CONFLICT(id) — a future change to (profile_name, flow_media_id) fails this test)
- [ ] work list: nameless rows grouped by project, ordered by project `created_at`; `--project/--limit/--since/--all/--max-projects` scoping honored; bare `gflow data sync` → usage error exit 2
- [ ] redacted mode → refusal, exit 11, remediation names `GFLOW_CLI_HISTORY_PROMPTS`
- [ ] WAF 403 mid-sweep → whole run aborts (no further fetches), partial writes kept
- [ ] partial project failures → `SyncPartialError` exit 34; total failure re-raises underlying typed error
- [ ] `--dry-run`: fetches but writes nothing; summary lists `would set` lines (control-chars scrubbed)

### Task S2 — Listing fetch + parser + recon script

**What:** The one new primitive: authenticated direct GET of
`flow.projectInitialData`, and its dedicated parser.

**Files:** `src/gflow_cli/api/client.py`, `src/gflow_cli/services/catalog_sync.py`
(parser half), `scripts/dev/capture_project_listing.py`

**Steps:**
- [ ] `FlowApiClient.fetch_project_listing(project_id) -> dict` — context
  `request.get` on the tRPC URL (`{"json":{"projectId":…,"toolName":"PINHOLE"}}`),
  no navigation; 401/403/429 raise the existing typed errors.
- [ ] Parser: names map + presence set + pagination-marker detection
  (`nextPageToken|pageToken|cursor|totalCount|hasMore|pageInfo|continuationToken`);
  strict UUID regex on every harvested id.
- [ ] Formalize the scratchpad spike as `scripts/dev/capture_project_listing.py`
  (recon/debug tool, prints pairs + completeness signals).
- [ ] Make S1 parser tests green.

### Task S3 — `services/catalog_sync.py` orchestration

**What:** Work list → sequential per-project fetch (minimal jitter) → parse →
write → summary; all safety rails.

**Steps:**
- [ ] Work-list query (projects containing nameless store-mode rows), scoping filters.
- [ ] Sequential loop on ONE client context; jitter between GETs at existing minimal defaults.
- [ ] WAF fail-fast; per-project try/except recording failures for the summary.
- [ ] Ghost predicate exactly as pinned in S1; `sync.status: missing_remote` writes.
- [ ] Name writes incl. changed-name overwrite, always `sync.named_at`-stamped.
- [ ] structlog events: `sync.project_started/…done` (counts only — never name values),
  final `sync.summary`.
- [ ] Make S1 orchestration tests green.

### Task S4 — Repository writers

**What:** Atomic, additive catalog writes.

**Files:** `src/gflow_cli/data/repository.py`

**Steps:**
- [ ] `set_asset_display_name(profile, media_id, name, *, source)` — single
  `UPDATE … json_set(coalesce(metadata_json,'{}'), …)` in `BEGIN IMMEDIATE`.
- [ ] `mark_asset_missing_remote(profile, media_id)` — same shape.
- [ ] Nameless-work-list query method.
- [ ] Make S1 repo tests green.

### Task S5 — CLI surface

**What:** `gflow data sync` command group wiring.

**Files:** `src/gflow_cli/cli_data.py`, `src/gflow_cli/errors.py`

**Steps:**
- [ ] `sync` command under the `data` group (function-scoped transport imports so
  `gflow data list` never pays the `api/` tree); `--names` scope flag required;
  bare invocation → usage error.
- [ ] Flags: `--project` (repeatable) / `--limit` / `--since` / `--all` /
  `--max-projects` (default 50) / `--dry-run` / `--json` / `--profile`.
- [ ] **Write by default** (locked decision); `--dry-run` previews.
- [ ] `SyncPartialError` → `EXIT_CODE_MAP[…] = 34`, added to `RETRYABLE_ERRORS`.
- [ ] Progress: single channel — human `[i/N]` lines via `click.echo(err=True)`
  only when structlog is in JSON mode; summary always.
- [ ] Make S1 CLI tests green.

### Task S6 — Sync live e2e + docs

**What:** Prove it against real Flow; document it.

**Files:** `tests/e2e/test_data_sync_names_e2e.py`, `docs/USAGE.md`,
`docs/DATA_LAYER.md`, `docs/MEDIA_LIBRARY.md`, `CHANGELOG.md`

**Steps:**
- [ ] e2e (zero credits): point an isolated catalog at a known populated project
  with rows stripped of names → run sync → names restored with provenance;
  known-deleted UUID → `missing_remote`; re-run → idempotent no-op.
- [ ] Update MEDIA_LIBRARY freshness section (#543 planned → shipped), USAGE,
  DATA_LAYER (`sync.*` metadata keys), CHANGELOG; regenerate mirror.

---

## Phase R — Refresh-on-miss (#546)

### Task R1 — Refresh-on-miss test scaffold (red)

**Files:** `tests/api/transports/test_ui_automation_video.py` (extend),
`tests/cli/` (extend)

**Tests created (red):**
- [ ] picker miss + resolver returns a new name → search retried ONCE with it, attach proceeds, callback reports the fresh name
- [ ] resolver returns no name / raises → existing fallback chain unchanged, no retry loop
- [ ] happy path (cached name hits) → resolver never called, zero extra requests
- [ ] redacted mode → fresh name used transiently, never persisted

### Task R2 — Transport hook

**What:** Optional `name_resolver: Callable[[str], str | None]`-style seam on
the picker path (transport depends on a callable, never on `data/`).

**Files:** `src/gflow_cli/api/transports/ui_automation_video.py`

**Steps:**
- [ ] On `_select_existing_asset` miss with a resolver present: resolve current
  name by UUID (one bounded call), retry the search once, then the existing
  fallback chain.
- [ ] Surface refreshed `(uuid, name)` pairs to the caller for write-through.
- [ ] Make R1 transport tests green.

### Task R3 — CLI wiring + live e2e + docs

**Files:** `src/gflow_cli/cli_image.py`, `src/gflow_cli/cli_video.py`,
`tests/e2e/test_refresh_on_miss_e2e.py`, docs

**Steps:**
- [ ] CLI builds the resolver as a closure over `fetch_project_listing` +
  repository write-through (store mode only); passes it into the transport call.
- [ ] Live e2e: rename a seeded asset (Flow Agent), then same-project
  `image i2i --ref <uuid>` → `image_ref_selected_existing` fires, no upload
  fallback, catalog holds the new name. If Agent-rename automation proves
  flaky, verify manually once and record evidence per `/gflow:live-verify`.
- [ ] Docs: MEDIA_LIBRARY freshness model (#546 planned → shipped), CHANGELOG.

---

## Definition of done

- [ ] All task steps checked off; each task one atomic commit on a feature
  branch off `develop` (worktree per CLAUDE.md rules)
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `/gflow:pr-council-review` consensus green on the PR(s)
- [ ] Sync + refresh-on-miss live-verified per `/gflow:live-verify` Part 2
  (doctor is offline — unit fixtures suffice)
- [ ] `CHANGELOG.md` `[Unreleased]` updated; docs + website mirror in sync
- [ ] Doctor default run proven non-mutating (byte-identical DB test)
- [ ] No `# TODO` in diff without a tracked issue link
