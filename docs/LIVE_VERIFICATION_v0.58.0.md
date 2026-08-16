# Live verification — v0.58.0

> Hand-run against real Flow on Windows 11, profile `denon82` (chrome strategy,
> **pt-BR**), 2026-08-15/16. This release's features are the #529 picker
> contract and the r2v named-reference fixes discovered while live-verifying
> it — every claim below is backed by a committed, re-runnable e2e test.
> Credits spent: ~7 Imagen (seed/i2i generations across runs) + 1 veo-lite
> video generation (standing e2e credit grant).

## Environment

| | |
|---|---|
| Branch | `develop` @ `8411b27` (merge of PR #540) |
| Local version | `0.57.1` editable (pre-bump tree) |
| Profile | `denon82` (pt-BR, natively agentic) |
| Date | 2026-08-15 → 2026-08-16 |
| OS | Windows 11 |
| Credits spent | ~7 Imagen + 1 veo-lite generation |

## Pre-tag gates

- Scoped offline suites on the release tree: **2058 passed / 3 skipped**
  (`tests/api tests/cli tests/data tests/features tests/mcp tests/worker`);
  full matrix green in CI on the same tree (PR #540, 3.11/3.12/3.13).
- `pyright src`: **0 errors**. ruff check + `ruff format --check` (368 files),
  hygiene (764), doc links, website PII, mirror sync — all green.
- SonarCloud quality gate: **GREEN** (zero new issues) on both #540 pushes.
- `/gflow:pr-council-review`: consensus green (run during the #529 cycle).
- `/gflow:doc-review` council: GREEN / GREEN / YELLOW across the 3 auditors;
  0 Tier 1 findings; 7 Tier 2 findings all fixed in the release-prep commit
  (USAGE/MCP superseded #287 semantics, CONFIGURATION redacted-mode
  display_name trade-off, KNOWN_ISSUES superseded note + async-caption entry,
  INDEX credits figure, `_remote_option_tile` docstring). Council reports at
  `tmp/council/0{1,2,3}-*.md` (local-only).

## Matrix

All four live paths are committed as opt-in e2e tests and passed against real
Flow (events asserted from structlog JSON, artifacts verified on disk):

| # | Feature | Test | Result |
|---|---|---|---|
| 1 | #529 same-project UUID ref → displayName search → exact-UUID tile attach, no duplicate upload | `tests/e2e/test_image_uuid_ref_e2e.py::test_e2e_same_project_uuid_ref_selected_in_picker` | ✅ `image_ref_selected_existing` (resolved_by=display_name); `image_ref_upload_fallback` absent; `ref_count == 1` |
| 2 | #393 cross-project UUID ref → verified local-file upload fallback | `...::test_e2e_cross_project_uuid_ref_falls_back_to_upload` | ✅ `image_ref_upload_fallback` fired; exit 0; `ref_count == 1` |
| 3 | Unresolvable UUID ref → loud typed abort, zero generation | `...::test_e2e_unresolvable_uuid_ref_fails_loud` | ✅ non-zero exit, typed no-fallback error, no generated media on disk (assertion updated to the #529 no-UUID-search contract) |
| 4 | r2v named remote reference from a catalog UUID's recorded displayName → real video | `tests/e2e/test_video_r2v_uuid_name_e2e.py` | ✅ seed t2i → catalog `display_name` read back by UUID → `remote_reference_attached` → SUCCEEDED VideoResult, mp4 on disk (`ftyp` magic bytes), 110 s |

## 5-layer ledger (r2v path, the deepest chain)

1. **File count** — exactly one downloaded `.mp4` in the pytest out dir.
2. **Magic bytes** — `ftyp` present in the first 32 bytes (asserted).
3. **Shape** — terminal `VideoStatus.succeeded` with non-empty `media_id`.
4. **Structlog invariants** — `remote_reference_attached` fired with the exact
   catalog `display_name`; the recorder had persisted that name from the seed
   generation's UI response (the #529 fix under test).
5. **User-confirmable artifact** — the generated video is in the Flow project
   library of the `denon82` account (project auto-created by the seed run).

## Live findings fixed during verification (shipped in this release)

The r2v e2e was not a rubber stamp — it caught two real UI drifts that had
silently broken `ref_names` on every locale, both root-caused with a
credit-free spike (`aria_snapshot`, DOM walks incl. shadow roots, screenshots):

1. **Picker exposes no accessible tree** (`aria_snapshot` = bare `- dialog:`),
   so the historical ARIA role+name tile match could never succeed. Fixed:
   `_remote_option_tile` matches the option's text with an anchored regex that
   tolerates only the localized media-type badge (`…mapImagem` on pt), keeping
   the PR #245 guarantee that a substring name cannot attach the wrong asset.
2. **Single-click attach** — clicking a result tile now attaches directly and
   closes the picker; the `Incluir no comando` include button exists only in
   the hover-preview pane. Fixed: `_pick_option_and_include` treats a closed
   dialog as success and keeps the legacy include-button flow as fallback.

Operational facts recorded for future e2e (memory + test hardening): the
agentic account can answer a t2i with a video (`WireFormatError` — seeds now
run with `GFLOW_CLI_PREFER_CLASSIC=1` and bounded retry), and Flow computes
the caption asynchronously, so a fresh generation may record without a
`display_name` (seed retry handles it; backfill tracked in #543).

## Not verified this cycle

- Non-pt locales for the badge-suffix regex (the regex is locale-agnostic by
  construction — anchored name + single capitalized suffix word — but only
  `Imagem` was observed live). Watch post-release.
- MCP-layer r2v UUID→local-upload merge (`_resolve_ref_local_path`): covered
  by unit tests; the transport upload path it feeds is #393-verified (row 2).

## Post-tag evidence

_To be filled after the v0.58.0 tag publishes: release workflow run link, PyPI
listing, and `pipx install gflow-cli==0.58.0` smoke check._
