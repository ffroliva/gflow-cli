# Scenario: #1249 wave 1 - size ratchet + FlowApiClient split

What could change behaviour when code moves? Each row was checked against the tree on
2026-11-11 (develop @ b7fc3b3f), not assumed.

## Coverage map

| Dimension | Relevant? | Why |
|---|---|---|
| D1 Auth & session | yes | lifecycle + token code moves (verbatim) |
| D2 WAF / reCAPTCHA | yes | `_mint_recaptcha_token` moves; must stay patchable on the instance |
| D3 Selectors | no | no selector text changes |
| D4 Batch resume | no | `generate_images_batch` body unchanged |
| D5 Concurrency / page pool | yes | `_checkout_page`/`_checkin_page` move to the base |
| D6 Data layer | no | recorder handed in, not owned |
| D7 Errors / exit codes | yes | `_raise_for_non_retryable` and friends move; re-exported |
| D8 Paths | no | |
| D9 Transport | yes | `transport` attribute set in `__init__`, read by surfaces |
| D10 Headless | no | |
| D11 Input validation | yes | `validate_image_file` is imported by services; re-export |
| D12 Log contract | yes | event names stay; logger name is not rendered (no `add_logger_name`) |
| D13 MCP parity | no | no tool, option or queued-payload key touched; parity tests unchanged |

## Scenario table

| # | Sev | Scenario | Evidence | Handling |
|---|---|---|---|---|
| S1 | Critical | A test patches `client._mint_recaptcha_token` / `_post_json` / `_checkout_page` on the instance and the moved code must still call the patch | 15 / 10 / 24 patch sites | Mixins: moved bodies keep `self._x`; instance lookup finds the patch first. No body rewrite. |
| S2 | Critical | `from gflow_cli.api.client import X` for a moved name | `FlowApiClient`, `FlowApiError`, `validate_image_file`, `MAX_IMAGE_BYTES`, `MAX_UPSAMPLE_B64_LEN`, `_AISANDBOX_CONTENT_TYPE`, `_raise_for_non_retryable`, `_unwrap_trpc`, `_storage_key_from_path`, `_is_profile_access_denied`, `_extract_provider_error_message`, `_default_project_title`, `_is_supported_image_header`, `_is_target_closed` | `client.py` re-exports each one (`import x as x`); characterization test pins the importable set |
| S3 | High | `patch("gflow_cli.api.client.<name>")` or `setattr(client_mod, ...)` stops affecting moved code | `logger` (test_client_image 169/192), `routes` (test_client_image 474), `storage_path` (test_client_image 663, test_client_upscale 286), `MAX_CONCAT_B64_LEN` (test_client_scene 231), `_is_target_closed` (test_client_incidents 321); found during the move, missed by the first survey's regex: `setattr(client_mod, "TokenMinter", ...)` (test_client_migrated_mint, 10 tests) and `client_mod.routes` (test_character_gen_no_direct_post) | Retarget the string to the new module. Import-path edit only, listed in the PR |
| S4 | High | Source-scan test reads `client.py` to find `generate_character_image` | `tests/services/test_character_gen_no_direct_post.py:237` | Point its path at the module that now holds the method; listed in the PR |
| S5 | High | `asyncio.sleep` / `time.monotonic` patched via `gflow_cli.api.client.asyncio.sleep` | test_video_status_poll, test_charged_surfaces | Patches the shared module object: survives the move unchanged |
| S6 | Medium | `MagicMock(spec=FlowApiClient)` loses inherited async methods | 8 sites in tests/cli | `spec` reads `dir()`, which includes inherited members; characterization test asserts the method set |
| S7 | Medium | `patch.object(FlowApiClient, "x")` on an inherited attribute | `__aenter__`/`__aexit__` 75 sites | Those two stay in `FlowApiClient`'s body; for inherited names mock sets and deletes on the subclass correctly |
| S8 | Medium | Import cycle: a surface module imports `client.py` | design | Surfaces import only `_flow_session` and each other, in chain order; `client.py` imports them |
| S9 | Medium | New class over the 1,000-line limit fails the gate it ships with | generation surface ~1,050 lines as one class | Split media edits (upsample, extend) from generation |
| S10 | Low | Pickling or copying the client | none found | n/a |
| S11 | Low | `__qualname__` of a method changes (`GenerationSurface.generate_image`) | no test or src reads it | Accept; noted in PR |
| S12 | Medium | pyright strict `reportPrivateUsage` on cross-module private imports | 46 existing per-line ignores in src | File-level `# pyright: reportPrivateUsage=false` in each new surface module only |

## Gate scenarios (PR 1)

| # | Sev | Scenario | Handling |
|---|---|---|---|
| G-a | Critical | A new file/class over the limit, or a new C901/PLR0915/PLR0904 hit, passes | test: new offender fails |
| G-b | Critical | Baseline used to wave a new violation through | `--update-baseline` only lowers/drops; test |
| G-c | High | An offender shrinks and the gate fails | test: shrink passes |
| G-d | High | Pre-commit runs the gate in an isolated venv without ruff | hook is `language: system` via `uv run --no-sync` |
| G-e | Medium | ruff renders rule names, not codes, in concise output (0.17.4) | parse `--output-format json` (`code` field) |
| G-f | Medium | Thresholds drift between ruff and the gate | thresholds set once in `pyproject.toml`; the gate parses `(N > M)` from ruff |

## Must-cover before merge (Critical + High)

S1, S2, S3, S4, S5, G-a, G-b, G-c, G-d.
