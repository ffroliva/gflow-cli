# Scenario: Media Trash and Restore (`#1363`)

Scope: `gflow media trash <id> [--profile NAME] [--yes] [--json]` and `gflow media restore <id> [--profile NAME] [--json]`, together with their MCP twins `media_trash` and `media_restore`.

Both commands resolve `<id>` as either a `flow_media_id` or local catalog asset ID, send the Flow `batchexecute` mutation RPC `pGCYOe`, spend zero credits, and operate wire-first without DOM interaction or virtual scroller dependencies.

---

## Coverage map

| Dimension | Active | Reason |
|---|---|---|
| D1 Auth & session lifecycle | Yes | Uses saved session cookies and `at` token for direct wire mutation; falls back to browser session when cookies expire. |
| D2 WAF / reCAPTCHA scoring | Yes | Verified in spike: `pGCYOe` has `has_token_slot: false`, requiring zero reCAPTCHA tokens and zero credits. |
| D3 Selector drift & locale invariance | Yes | Wire-first avoids DOM selectors entirely; eliminates the Angular CDK virtual scroller bottleneck. |
| D4 Batch manifest & resume | No | Trash/restore operates on individual media items; does not alter batch generation pipelines. |
| D5 Concurrency & Page pool | Yes | Direct wire requests use httpx with profile mutex (`_gate`); multiple concurrent commands to different profiles run safely. |
| D6 Data layer (SQLite / DataStore) | Yes | Input ID resolution via `DataRepository.get_asset_by_any_id`; cross-profile ambiguity requires `--profile`. |
| D7 Error propagation & exit codes | Yes | Stable exits: 2 (missing `--yes` in non-interactive shell), 11 (bad configuration/profile), 16 (ambiguous/missing asset), 4/40 (rate limit), 6/42 (network error). |
| D8 Cross-platform paths | Yes | Windows/macOS/Linux profile directory resolution; UTF-8 handling; zero OS-specific paths. |
| D9 Transport edge cases | Yes | `batchexecute` response decoding: parses `wrb.fr` frames, validates slot `[0][0][3][2]` (`true` for trash, `null` for restore), handles RPC errors. |
| D10 Headless vs headed environment | Yes | Direct wire path runs headless with no display server or Chromium launch required. |
| D11 Input validation & boundary values | Yes | Interactive confirmation prompt `[y/N]` without `--yes`; refusing ambiguous IDs; non-existent Flow media handling. |
| D12 Observability & structured log contract | Yes | Events `media.trash.*` and `media.restore.*` on stderr; pure structured JSON on stdout under `--json`. |
| D13 MCP surface parity | Yes | MCP twin tools `media_trash` and `media_restore`; `media_trash` requires `confirm: bool = True`. |

---

## Scenario table

Severity: **Critical** (data loss / unrecoverable state / unconfirmed destruction) · **High** (broken core path, wrong exit code, or MCP parity drift) · **Medium** (degraded UX, error mapping, or edge-case input) · **Low** (cosmetic formatting detail).

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D11 / D7 | User invokes `gflow media trash <id>` in a non-interactive shell (or script) without `--yes` | **Critical** | Fails fast with exit 2 (`BadParameter`) before any network call; error message states `--yes` is required in non-interactive mode to confirm moving media to Trash | Unit + BDD |
| 2 | D6 / D7 | Input `<id>` matches assets in multiple local profiles and `--profile` is omitted | **Critical** | Raises `DataStoreError` (exit 16, `charged: "no"`) refusing cross-profile ambiguity; error message lists matching profiles and requests `--profile` | Unit + BDD |
| 3 | D13 | Agent calls MCP `media_trash` with `confirm=False` (or default) | **Critical** | Tool returns an RFC 9457 Problem Details error (`https://gflow-cli.dev/errors/confirmation-required`) refusing destruction without explicit `confirm=True` | Unit (MCP) + BDD |
| 4 | D9 | Golden path wire trash: valid media ID, fresh session cookies | **High** | Sends `pGCYOe` directly with payload `[[[media_id, null, null, [null, null, 1], project_id]], [[mask]]]`; receives HTTP 200 with slot `[0][0][3][2]==true`; exits 0; `--json` emits `{media_id, project_id, status: "trashed", restore_hint: "..."}` | Unit + BDD |
| 5 | D9 | Golden path wire restore: trashed media ID | **High** | Sends `pGCYOe` directly with payload `[[[media_id, "", null, [], project_id]], [[mask, "collection_id"]]]`; receives HTTP 200 with slot `[0][0][3][2]==null`; exits 0; `--json` emits `{media_id, project_id, status: "restored"}` | Unit + BDD |
| 6 | D1 / D9 | Stale session cookies or direct wire disabled (`GFLOW_CLI_WIRE_READS=0`) | **High** | Wire fails with `WireFallbackReason`; execution falls back to browser session context evaluation via Playwright; exits 0 without error | Integration |
| 7 | D6 | Input `<id>` is a local catalog asset integer ID or `flow_workflow_id` | **High** | `resolve_asset` maps the input to the canonical `flow_media_id` and `flow_project_id` before dispatching the wire RPC | Unit + BDD |
| 8 | D6 / D7 | Input `<id>` is not found in the local catalog and cannot be resolved | **High** | Raises `DataStoreError` (exit 16, `charged: "no"`) with remediation hint to run `gflow data sync` or verify the ID | Unit |
| 9 | D13 | MCP parity validation | **High** | `media_trash` and `media_restore` pass `tests/mcp/test_cli_parity.py`; parameters and docstrings match CLI surface; registered under `@spends_credits(False)` | Unit (MCP parity) |
| 10 | D9 / D7 | Flow returns an RPC error envelope for `pGCYOe` (e.g. invalid permissions or unknown media ID) | **High** | Maps error to `FlowApiError` (exit 39 or 3, `charged: "no"`) with structured problem details; never crashes unhandled | Unit |
| 11 | D11 | Interactive CLI confirmation prompt `[y/N]` | **Medium** | When invoked in a TTY without `--yes`, prompts `Move media <id> to Flow's Trash? [y/N]: `; input `y`/`yes` proceeds; input `n`/`no`/Enter aborts cleanly with exit 0 ("Aborted.") | Unit |
| 12 | D12 | Pure stdout output under `--json` and `-o id` | **Medium** | Under `--json`, outputs a single parseable JSON document on stdout; under `-o id`, outputs only `<media_id>`; all `structlog` progress events route to stderr | Unit |
| 13 | D9 / D7 | Flow API returns HTTP 429 (rate limit) or 503 (network error) | **Medium** | HTTP 429 maps to `RateLimitError` (exit 4 / 40); 503 maps to `NetworkError` (exit 6 / 42) with retry-after hint | Unit |
| 14 | D8 | Cross-platform profile directory isolation | **Medium** | Profile path confinement verifies directory is strictly inside `GFLOW_CLI_HOME`; Windows drive letters and spaces handled cleanly | Unit |
| 15 | D2 / E2E | Live end-to-end zero-credit verification on flow.google.com | **High** | Trashes one existing generated image and restores it immediately in the same run; balance verified before and after ($0 delta) | Live E2E (`@pytest.mark.e2e`) |

**Count:** 15 scenarios. Critical 3 · High 7 · Medium 5 · Low 0.

---

## Must-cover before merge (Critical + High)

Scenarios 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15.

---

## Deferred (out of scope for v1)

- Batch trashing (`gflow media trash --all` or multi-ID lists): omitted under YAGNI; callers can loop individual commands.
- Permanent deletion from Trash: Flow's permanent purge wire contract remains unmeasured.

---

## BDD Feature Specification

Gherkin feature file defined in [`tests/features/media_trash.feature`](../../../../tests/features/media_trash.feature).
