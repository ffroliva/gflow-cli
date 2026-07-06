# Live Verification — v0.25.0

Release date: 2026-07-06. This release ships **remote image UUIDs in
`gflow_generate_video`** (#237, hardened by the #245 follow-up review), the
**`$GFLOW_CLI_HOME/.env` dotenv fallback** (#240 — the minor-bump reason), and the
**shadowed duplicate `Settings.daemon_token` removal** (#243).

All live runs below were executed on 2026-07-06 against real Flow (headed
Playwright, profile `ffroliva`) at the merged v0.25.0 tree on `main` (`619c270`).

## Scope

| Change | Surface | Verification |
|---|---|---|
| **#240** — home `.env` fallback | `config.py` | ✅ **Live CLI matrix** (4 cases below) + pinning tests `tests/test_config.py` (`test_home_env_file_is_loaded_without_cwd_env`, `test_cwd_env_file_wins_over_home_env_file`, `test_process_env_var_beats_both_env_files`, `test_empty_home_env_var_means_unset_for_field_and_env_lookup`, `test_env_file_none_disables_all_dotenv_loading`, …) |
| **#237/#245** — UUID image refs in `gflow_generate_video` | `mcp/tools.py`, `ui_automation_video.py` | ⚠️ **PARTIAL — RELEASE-BLOCKING FINDING.** Fail-fast branch, enqueue-time UUID→name resolution, and the #245 `flow_media_id` envelope fix verified live ✅. The **happy path (generated-image UUID → i2v attach) FAILED live** — root-caused below: Flow's picker search does not match generation prompts and generated media carry no display name on this account, so the resolved search term can never find a tile. |
| **#243** — duplicate `daemon_token` field removed | `config.py` | ✅ Automated — `test_daemon_token_defined_exactly_once`, `test_daemon_token_keeps_both_env_aliases`, `test_daemon_token_override` pin the surviving field's contract (both `GFLOW_CLI_DAEMON_TOKEN` and `GFLOW_DAEMON_TOKEN` accepted, defined exactly once). Internal dead-code removal; no user-facing behavior to exercise beyond the pinned contract. |

## #240 — `$GFLOW_CLI_HOME/.env` fallback (live CLI matrix)

Marker: `GFLOW_CLI_DB_PATH` (a real `Settings` field) pointed at fresh per-case
SQLite paths; observed via `gflow data list images` from a foreign CWD (a temp
directory, no repo, no CWD `.env`). Note `GFLOW_CLI_PROFILE` is **not** a valid
marker — it is read directly from `os.environ` by `profile_store.py`, not via
`Settings`, in every version.

| Case | Setup | Expected | Observed |
|---|---|---|---|
| Baseline | no home `.env`, no CWD `.env` | real catalog (populated) | ✅ real `gflow.db` listed (8+ image rows) |
| T1 home `.env` only | `GFLOW_CLI_DB_PATH=…/home_marker.db` in `<default home>/.env` | empty catalog; `home_marker.db` created | ✅ empty output; `home_marker.db` appeared |
| T2 CWD beats home | additional CWD `.env` → `cwd_marker.db` | CWD marker wins | ✅ `cwd_marker.db` created |
| T3 process env beats both | `GFLOW_CLI_DB_PATH=…/env_marker.db` env var | env var wins | ✅ `env_marker.db` created |
| T4 set-but-empty home | `GFLOW_CLI_HOME=` (empty) + home `.env` marker | treated as unset → default-home `.env` still loads | ✅ `home_marker.db` re-created |

The test home `.env` was removed after the run (the default home had none before).

## #237 — remote image UUIDs in `gflow_generate_video` (live, real MCP server)

Both cases were driven through the **real stdio MCP server** (`gflow mcp run`)
with a minimal `mcp` Python client calling `gflow_generate_video` — the exact
path an IDE/agent consumer takes (tool → enqueue → UUID→name resolution →
`FlowWorker` → headed browser → Flow picker).

### Step 1 — UUID source: live credit-free image generation

`gflow -v image t2i 'a hand-painted wooden sign reading "v0.25.0" hanging on a
rustic workshop wall, warm morning light, photorealistic' --profile ffroliva --json`

5-layer ledger:

1. **File count**: exactly 1 file written — `60dcb880-d401-4743-99ca-08cc2099615a_1.jpg` (944 KB).
2. **Magic bytes**: `ff d8 ff e0` (JPEG/JFIF).
3. **Dimensions**: 768×1376 (portrait, matches `IMAGE_ASPECT_RATIO_PORTRAIT` / 9:16 request).
4. **Structlog invariants**: 11 events incl. `client.persistent_context_launch` → `ui_automation.setup_shared_page` → `ui_automation.entering_existing_project` → `agentic_driver.send_prompt.typed` → `agentic_driver.configure_image_settings.stored`; exit 0; JSON envelope `status: ok`, `project_id: 0f4e7eaa-…`, media UUID `60dcb880-d401-4743-99ca-08cc2099615a`.
5. **User-confirmable artifact**: the image visibly shows a hand-painted wooden sign reading **"v0.25.0"** in a rustic workshop (inspected).

Catalog row confirmed via `gflow data media 60dcb880-…` (profile `ffroliva`, kind
`image`). `metadata_json.display_name` was empty for this asset, so the resolver's
documented **prompt fallback** (`resolve_seed_image(...).prompt`) is what the happy
path below exercises — the harder of the two resolution branches.

A second image was generated through the **MCP `gflow_generate_image` tool**
(short quote-free prompt, `f94bdd3f-458f-4139-9558-71843ce12ad0`), which also
live-verified the **#245 envelope fix**: the result's `flow_media_id` carries
the real media id (not the workflow id), with `flow_workflow_id` exposed under
its own key.

### Step 2 — fail-fast: UUID not in catalog (credit-free)

`gflow_generate_video(mode="i2v", initial_frame="00000000-0000-4000-8000-000000000000", profile="ffroliva")`

- Returned in **1.5 s** — no browser launch, no ~120 s Playwright timeout (the
  pre-#237-review failure mode).
- Exact RFC 9457 envelope: `status: error`, `type:
  https://gflow-cli.dev/errors/bad-parameter`, `title: "Reference Not Found"`,
  `status: 400`, detail names the UUID, the profile, and the remedy ("Generate the
  image first, or pass its display name.") — precisely the CHANGELOG contract.

### Step 3 — happy path: i2v from the generated image's UUID — ❌ FAILED (live)

Three live attempts through the real MCP server, all failing identically at the
picker tile match (`get_by_role("option", name=<resolved name>, exact=True)`
timeout after 8 s; task fails cleanly in ~30 s, **no credits spent**, no 120 s
hang — the #245 fail-cleanly hardening works):

| Attempt | Ref | Resolved search term | Project | Result |
|---|---|---|---|---|
| 1 | `60dcb880-…` (CLI t2i image) | 116-char quoted prompt (fallback) | scratch | ❌ tile timeout |
| 2 | `f94bdd3f-…` (MCP `gflow_generate_image` image) | 45-char clean prompt (fallback) | scratch | ❌ tile timeout |
| 3 | same as 2 | same | **the image's own project** (`d6af6531-…`) | ❌ tile timeout |

### Root cause (instrumented diagnostic, screenshots on file)

A patched worker run dumped the picker dialog's accessibility tree and a
screenshot at the exact failure moment:

- The automation **does** find and type into the correct input — the resource
  picker's own "Search assets" box (`#add-menu-input`), Images tab selected.
- Flow answers **"No results found"** for the generation-prompt text: Flow's
  asset search **does not index generation prompts**, and `[role='option']`
  count is 0 before and after typing.
- Generated media on this account carry **no display name**: Flow returns an
  empty `workflow.metadata.displayName`, so the recorder stores
  `display_name: None` — for **0 of 190 assets** in the entire catalog is a
  display name recorded. The resolver's primary branch is therefore dead in
  practice and every UUID ref takes the prompt-fallback, which Flow's search
  cannot match. A tile in the project's media grid is visibly **unnamed**
  (screenshot), so even an unfiltered exact-name match cannot succeed.

**Conclusion**: `#237`'s advertised flow — "pipe the output of an image
generation straight into a video generation" — does not work against live Flow
as of 2026-07-06 on this account. What DOES work live: UUID validation, catalog
lookup, fail-fast errors, and clean failure envelopes. Untested (plausible
alternative): a ref whose display name is real — e.g. an **uploaded** asset
(named by filename) or a literal display-name string typed by the caller.

**Release impact**: #237 is the headline Added feature of v0.25.0. Tagging
v0.25.0 with the CHANGELOG advertising it as working would ship a
non-functional feature. Options: fix the attach mechanism first (e.g. attach
by media-id/thumbnail rather than name search, or name assets at generation),
re-scope the CHANGELOG entry to the verified subset, or pull #237 from the
release. **Owner decision required before the signed tag.**

## Automated coverage

- Full suite green on PR #249 CI (matrix 3.11/3.12/3.13) at the merged tree,
  SonarCloud gate green.
- `pyright src`: 0 errors; `ruff check` / `ruff format --check`: clean.
- #245 follow-up regressions each pinned by tests (i2i media-id pass-through,
  `flow_media_id` vs `flow_workflow_id` envelope keys, no-display-name fail-fast,
  `exact=True` tile match, R2V picker-close budget, ARIA-based audio tile).
