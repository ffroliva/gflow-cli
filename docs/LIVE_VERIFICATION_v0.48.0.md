# Live Verification — v0.48.0

- **Scope**: #411 — explicit `-o` / `--output` flag on `gflow image t2i`, `gflow image i2i`, `gflow video t2v`, `gflow video i2v` (PR #413, merged 2026-08-02). Local destination paths only; the MCP `output` param was removed pre-release (see the audit note below).
- **Date**: 2026-08-02
- **Credits spent**: 0

## Pre-tag gates

- `/gflow:check`: hygiene + doc-links + PII + mirror-drift ✅, ruff check/format ✅, pyright 0 errors ✅, full suite 2888 passed / 5 skipped ✅.
- `/gflow:doc-review`: mechanical pass ✅; 3-auditor council returned **RED** (auditors 1 & 3) — the release notes claimed more than the code shipped. Findings and their resolution are recorded below; the release was re-scoped to the truthful surface ("truth-only release") before tagging. Council reports at `tmp/council/` (local-only).
- SonarCloud: the develop branch analysis failed its quality gate on `new_duplicated_lines_density` (13.7% vs 3%) from #413's copy-pasted option stacks; deduplicated in the release branch (`_shared_gen_tail_options` in `cli_video.py`, `_generate_verify_download` in `cli_image.py`) — gate verified on the release PR.

## Evidence ledger (what is actually proven, and by what)

| Claim | Evidence | Status |
|---|---|---|
| `-o` writes the single image to the exact local path | `tests/cli/test_predictable_output.py::test_t2i_explicit_output_single_file`, `::test_i2i_explicit_output_file` (CLI runner, mocked transport, real path assertions) | ✅ offline |
| Multi-count images get `_1`, `_2` stem suffixes | `::test_t2i_explicit_output_multi_count`; logic at `cli_image.py::_download_images` | ✅ offline |
| Video `-o` relocates the mp4 to the target path | `::test_video_t2v_explicit_output_file`, `::test_video_i2v_explicit_output_file` — **weak**: the mock writes directly to the target, so `_relocate_video_output`'s mkdir+move short-circuits (#415 tracks de-mocking) | ⚠️ partial |
| Nested parent auto-creation | Production code exists (`_relocate_video_output` mkdir; `storage.py::_write_local` mkdir), but the covering test's mock performs the mkdir itself (#415) | ⚠️ code-read, test tautological |
| Single-prompt-only guard on `t2i` | `cli_image.py` raises `UsageError` (reproduced by hand: exits with the documented message) | ✅ |
| Storage layer writes/reads `s3://` keys byte-identically | `tests/integration/test_storage_s3.py` (8 tests, Docker MinIO) — exercises `storage_path()`/`write_asset_async()` **directly; not the `-o` flag**, which is local-only in v0.48.0 (#415) | ✅ layer-level only |
| Release tree healthy | Full suite 2888 passed / 5 skipped; green full-suite CI on the merged tree | ✅ |

## Live-Flow attempt (recorded, not omitted)

A live `image t2i --profile denon82 -o <nested path>` run was attempted 2026-08-02 15:24 UTC and aborted pre-generation with `AuthExpiredError` (HTTP 401 on `project.createProject`) — the profile's Flow session cookies had expired since the last live cycle (2026-07-31). The failure exercised the error contract correctly: typed `AuthExpiredError`, RFC-7807 problem payload, remediation hint naming `gflow auth login --profile <name>`, no file written, no credits spent. A live re-run is queued for the next authenticated session.

## Pre-release audit outcome (why the shipped surface is smaller than PR #413's description)

The doc-review council found two claims in PR #413's description that the code did not back:

1. **MCP parity** — the `output` param on `gflow_generate_image`/`gflow_generate_video` was written into the queue payload but never decoded by `worker/codec.py`; `worker/daemon.py` hardcodes the destination. A silent no-op param violates the project's own MCP rule, so it was **removed** in the release branch. Re-adding it with real worker support is #414.
2. **Cloud storage** — `-o` is `click.Path(path_type=Path)`; it never routes through the UPath/fsspec layer, and under `GFLOW_CLI_STORAGE_URI` an out-of-tree path is flattened to its basename. Cloud targets are #415.

## Post-tag evidence

_(filled after publish)_
