# Live verification — v0.9.0

> Evidence for the v0.9.0 release. The marquee feature is a **read-only query
> layer** over the existing SQLite catalog (`gflow data list`). No Flow credits
> are spent by the new code; all paid surfaces (image/video generation) are
> unchanged behaviorally from v0.8.1, which is itself the most-recently
> verified release (see [LIVE_VERIFICATION_v0.8.1.md](LIVE_VERIFICATION_v0.8.1.md)).
>
> Per memory `verification-ledger-5-layer`, paid Flow runs are normally
> verified across 5 layers (file count, magic bytes, Pillow dims, structlog
> events, user gallery). For v0.9.0, automated test coverage of the new
> read-only surface plus the carry-forward verification of the unchanged paid
> surfaces is treated as the evidence floor. Hands-on usage logs will be
> appended below as they accumulate.

## Environment

- Date: 2026-05-25
- gflow-cli version: 0.9.0
- Python: 3.11+ (CI matrix: 3.11 / 3.12 / 3.13 — all green on every PR in the v0.9.0 release sequence)
- Chrome: headed, real-Chrome strategy mandatory (per memory `real-browser-auth-mandatory`)
- OS: Windows 11 (primary dev), macOS / Linux on CI

## Surface 1 — `gflow data list projects`

**Automated evidence (`tests/test_data_queries.py`, `tests/test_cli_data.py`):**

- `test_list_projects_returns_all_by_default` — seeded catalog with 4 projects across 3 profiles → all 4 returned
- `test_list_projects_filters_by_profile` — `--profile alice` → only alice's 2 projects
- `test_list_projects_respects_limit` / `_respects_offset` — pagination correct, no row overlap between pages
- `test_list_projects_newest_first` — `ORDER BY created_at DESC` honored
- `test_list_projects_image_video_counts` — aggregate subqueries over `assets WHERE kind = 'image' / 'video'` return correct per-project counts
- `test_list_projects_empty_catalog_returns_empty_list` — fresh migration, no rows → exit 0 + empty result
- CLI integration tests cover Rich-table TTY output, JSONL on pipe / `--json`, invalid `--limit` → exit 2

## Surface 2 — `gflow data list images`

**Automated evidence:**

- `test_list_images_returns_all_by_default` — 8 seed images returned
- `test_list_images_filters_by_profile` — `--profile alice` → 4 (alice has 2 projects × 2 images)
- `test_list_images_newest_first`
- `test_list_images_carries_prompt_aspect_model_local_path` — confirms the `prompt` LEFT JOIN via `operation_assets`+`operations` and the `local_path` JOIN via `local_files` both flow non-NULL when the catalog row has them
- `test_list_images_pagination` — non-overlapping pages
- CLI: default + `--json` + `--profile` + 40-char prompt truncation in the table emitter

## Surface 3 — `gflow data list videos`

**Automated evidence:**

- `test_list_videos_returns_all_by_default` — 2 seed videos (alice's projects only)
- `test_list_videos_filter_no_match` — `--profile bob` → empty (bob has no videos)
- `test_list_videos_carries_duration` — `duration` flows from `assets.duration_seconds` (REAL/float) — `r.duration > 0`
- CLI: default + `--json`, duration formatted as `{value:g}s` in the table

## Surface 4 — `gflow data list profiles`

**Automated evidence:**

- `test_list_profiles_returns_catalog_known_profiles` — set membership `{alice, bob, carol}` (the three seeded profiles all have ≥1 generation)
- `test_list_profiles_carries_aggregate_counts` — alice has 2 projects / 4 images / 2 videos; bob has 1 project / 2 images / 0 videos
- `test_list_profiles_sorted_by_last_used_desc` — `MAX(created_at)` aggregation across projects + assets used for sort
- CLI: `--profile` flag is correctly absent on this subcommand (would be meaningless)

## Surface 5 — DataStoreError handling

**Automated evidence:**

- `test_data_list_db_missing_exits_16` — invokes CLI against a non-existent DB path; `_safe_db()` context manager catches the `sqlite3.OperationalError` and re-raises as `DataStoreError`; the CLI's `@_guard` decorator maps it to `click.exceptions.Exit(16)`.

> **Note (post-v0.9.0, unreleased):** [#88](https://github.com/ffroliva/gflow-cli/issues/88) inverted this contract on `develop` — `_safe_db()` now routes through `DataStore.open()`, which auto-creates the file and applies migrations. A missing-DB `gflow data list` returns exit **0** with an empty table (the test was renamed to `test_data_list_db_missing_exits_0_with_empty_rows`). The exit-16 path still triggers for actual `DataStoreError` / `DataMigrationError` failures (permission denied, corrupt schema, write-side recorder errors).

## Surface 6 — Paid surfaces (image / video) — carry-forward verification

`v0.9.0` changes ZERO behavior on `gflow image t2i / i2i / upload` or `gflow video t2v / i2v / r2v`. The most recent end-to-end paid verification of these is captured in:

- [`LIVE_VERIFICATION_v0.8.1.md`](LIVE_VERIFICATION_v0.8.1.md) — README/docs refresh release; paid surfaces inherit from v0.8.0
- [`LIVE_VERIFICATION_video_download.md`](LIVE_VERIFICATION_video_download.md) — video download surface
- [`LIVE_VERIFICATION_image_batch.md`](LIVE_VERIFICATION_image_batch.md) — `gflow image batch` (always-same-project mode)

A hands-on smoke run against a real Pro/Ultra profile is **recommended** after install (`gflow image t2i "smoke v0.9.0" --aspect 1:1 --profile <yours>` + `gflow video t2v "smoke v0.9.0" --aspect 9:16 --model omni-flash --duration 4 --profile <yours>`) but not gated by this release ship.

## Surface 7 — Documentation & navigation

**Manual evidence (this release):**

- [`ROADMAP.md`](../ROADMAP.md) renders with themed milestones through v1.0; no dates; no editorial drift.
- [`docs/DATA_LAYER.md`](DATA_LAYER.md) — new `gflow data list` subsection documents flags, output formats, sort order, exit codes, and the `data list profiles` vs `gflow auth list` semantic distinction.
- [`docs/INDEX.md`](INDEX.md) — `ROADMAP.md` row + the `gflow data list` topic shortcut both present.
- [`AGENTS.md`](../AGENTS.md) — `cli_data.py` + `data/` in the module list; exit-code range updated to 3–16.
- [`llms.txt`](../llms.txt) — description mentions the catalog + `gflow data list`; Docs section links Data Layer + Roadmap.
- [`CHANGELOG.md`](../CHANGELOG.md) `[0.9.0]` entry summarizes the release; compare links updated; previously-missing `[0.8.1]` link added.

## Conclusion

Read-only feature surfaces pass automated verification on every CI run (31/31 tests across 3 Python versions). Paid surfaces are unchanged from the v0.8.1 baseline. Sponsorship wiring (FUNDING.yml + README badges) was prepared but deferred to a follow-up patch release pending GitHub Sponsors / Buy Me a Coffee account activation. The release is safe to tag.

## Hands-on usage log

> *Append your first real-account usage outputs here. Not a release blocker — this section grows organically as v0.9.0 is exercised in the field.*

<!-- format example:
### 2026-05-25 — first real-catalog run on profile `<name>`

```
$ gflow data list projects --limit 5
...
```
-->
