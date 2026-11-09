# Error envelope reference

Every gflow failure is an [RFC 9457](https://datatracker.ietf.org/doc/html/rfc9457)
Problem Details object. `gflow ... --json` prints it as `{"status": "fail", "error": {...}}`,
and MCP tools return it as `{"status": "error", "error": {...}}`. Both carry the
same members, and the worker queue persists the same members for a queued task.

## Members

| Member | Always present | Meaning |
|---|---|---|
| `type` | yes | Stable URI per error class (table below) |
| `title` | yes | Short human summary of the class |
| `detail` | no | What happened this time |
| `status` | no | The literal HTTP status of the failed call, when there was one |
| `remediation_hint` | no | What a person should do, in prose |
| `route` | no | Sanitized route name (never a full URL) |
| `class` | CLI `--json` | The concrete error class name |
| `exit_code` | CLI `--json`, worker | The process exit code (table below) |
| `retryable` | yes (except the worker queue's unknown-error row) | `true` when the identical request may simply be run again |
| `charged` | yes | Whether Flow may have billed this operation: `no`, `maybe` or `yes` |
| `next_action` | yes | What a caller should do (values below) |
| `operation_id` | no | The local job or media id, when one exists, so a follow-up can find it |

`charged`, `next_action` and `operation_id` were added in
[#1201](https://github.com/ffroliva/gflow-cli/issues/1201). They are additive: no
existing member changed.

### `charged`

- `no`: the class proves nothing was submitted, or the operation is free (catalog,
  auth, uploads Flow refused, image generation).
- `maybe`: the failure can happen before or after a submit. Check the project before
  re-running a paid generation.
- `yes`: Flow made, and billed, the result. Re-running bills again.

### `next_action`

| Value | Do this | `retryable` |
|---|---|---|
| `retry` | Run the same command again | `true` |
| `retry_after` | Run it again after a pause (quota or rate limit) | `true` |
| `wait` | Wait for an account-side condition to change (for example, credits) | `false` |
| `reauth` | Run `gflow auth login` for the profile | `false` |
| `fix_input` | Change the command, its inputs or the local setup | `false` |
| `reconcile` | Settle the outcome first: find the result in Flow (`gflow data download <media_id>` for a billed clip) before re-running | `false` |
| `none` | Nothing a caller can do will change the result | `false` |

`retryable` and `next_action` always agree: `retryable` is `true` exactly when
`next_action` is `retry` or `retry_after`. A raise site that overrides `retryable`
also moves `next_action`: one that turns the retry on reads `retry`, and one that
turns it off reads `reconcile` when the class says Flow may have billed, otherwise
`none`.

### Raise-site overrides

The table gives each class's default. A raise site can override any of the three
members. The one in place today:

- `MediaDownloadError` raised for a finished, billed clip (`retryable: false`, #941)
  reads `charged: yes`, `next_action: reconcile`, with the clip's media id as
  `operation_id`. The same class raised by the free recovery (`gflow data download`,
  MCP `gflow_download_media`) keeps the default `no` / `retry`.

`operation_id` is only set where an id already exists on the error (today,
`MediaDownloadError`'s media id).

An unclassified failure (`UnexpectedError` on the CLI, `…/errors/unknown` over MCP
or in the worker queue) reads `charged: maybe`, `next_action: none`. The MCP
`gflow_auth_status` probe spends nothing, so every error it returns reads `charged: no`.

## Every `type`

Exit codes come from `errors.EXIT_CODE_MAP`; a class without its own entry inherits
its parent's, and an unmapped class exits 1. Exit 33 is not an error: `gflow doctor`
uses it for "findings present".

| `type` | Class | Exit | `charged` | `next_action` |
|---|---|---|---|---|
| `https://gflow-cli.dev/errors/api-error` | `FlowApiError` | 1 | `maybe` | `none` |
| `https://gflow-cli.dev/errors/batch-integrity` | `BatchIntegrityError` | 1 | `maybe` | `none` |
| `https://gflow-cli.dev/errors/batch-partial` | `BatchPartialError` | 1 | `maybe` | `none` |
| `https://gflow-cli.dev/errors/recaptcha-mint` | `RecaptchaError` | 1 | `no` | `none` |
| `https://gflow-cli.dev/errors/aisandbox-auth` | `AisandboxAuthError` | 3 | `maybe` | `reauth` |
| `https://gflow-cli.dev/errors/auth-expired` | `AuthExpiredError` | 3 | `maybe` | `reauth` |
| `https://gflow-cli.dev/errors/rate-limit` | `RateLimitError` | 4 | `no` | `retry_after` |
| `https://gflow-cli.dev/errors/content-policy` | `ContentPolicyError` | 5 | `maybe` | `fix_input` |
| `https://gflow-cli.dev/errors/media-download` | `MediaDownloadError` | 6 | `no` | `retry` |
| `https://gflow-cli.dev/errors/network` | `NetworkError` | 6 | `maybe` | `retry` |
| `https://gflow-cli.dev/errors/rpc-response-unavailable` | `RpcResponseUnavailableError` | 7 | `maybe` | `reconcile` |
| `https://gflow-cli.dev/errors/wire-format` | `WireFormatError` | 7 | `maybe` | `reconcile` |
| `https://gflow-cli.dev/errors/auth-missing` | `AuthMissingError` | 8 | `no` | `reauth` |
| `https://gflow-cli.dev/errors/transport-timeout` | `TransportTimeoutError` | 9 | `maybe` | `retry` |
| `https://gflow-cli.dev/errors/waf-rejection` | `WafRejectionError` | 10 | `maybe` | `retry` |
| `https://gflow-cli.dev/errors/chain-manifest` | `ChainManifestError` | 11 | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/configuration` | `ConfigurationError` | 11 | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/profile-access` | `ProfileAccessError` | 11 | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/profile-engine-downgrade` | `ProfileEngineDowngradeError` | 11 | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/profile-locked` | `ProfileLockedError` | 11 | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/auth-login-timeout` | `AuthLoginTimeoutError`, `IdentityRecheckPendingError` | 12 | `no` | `reauth` |
| `https://gflow-cli.dev/errors/security` | `SecurityError` | 13 | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/auth-browser-rejected` | `AuthBrowserRejectedError` | 14 | `no` | `reauth` |
| `https://gflow-cli.dev/errors/browser-session-closed` | `BrowserSessionClosedError` | 15 | `maybe` | `retry` |
| `https://gflow-cli.dev/errors/data-integrity` | `DataIntegrityError` | 16 | `maybe` | `fix_input` |
| `https://gflow-cli.dev/errors/data-migration` | `DataMigrationError` | 16 | `maybe` | `fix_input` |
| `https://gflow-cli.dev/errors/data-store` | `DataStoreError` | 16 | `maybe` | `fix_input` |
| `https://gflow-cli.dev/errors/model-mode-incompatibility` | `ModelModeIncompatibilityError` | 17 | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/video-model-selection` | `VideoModelSelectionError` | 18 | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/scene-concat` | `SceneConcatError` | 19 | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/frame-extraction` | `FrameExtractionError` | 20 | `maybe` | `fix_input` |
| `https://gflow-cli.dev/errors/chain-partial` | `ChainPartialError` | 21 | `yes` | `reconcile` |
| `https://gflow-cli.dev/errors/upscale-unavailable` | `UpscaleUnavailableError` | 22 | `no` | `none` |
| `https://gflow-cli.dev/errors/ui-selector-drift` | `UiSelectorDriftError` | 23 | `maybe` | `none` |
| `https://gflow-cli.dev/errors/browser-engine-unavailable` | `BrowserEngineUnavailableError` | 24 | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/flow-agent-ui` | `FlowAgentUiError` | 25 | `maybe` | `retry` |
| `https://gflow-cli.dev/errors/media-attribution` | `MediaAttributionError` | 26 | `yes` | `reconcile` |
| `https://gflow-cli.dev/errors/media-upload-rejected` | `MediaUploadRejectedError` | 27 | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/ui-mode-unavailable` | `UiModeUnavailableError` | 28 | `no` | `retry` |
| `https://gflow-cli.dev/errors/mention-index-unavailable` | `MentionIndexUnavailableError` | 29 | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/queue-schema` | `QueueSchemaError` | 30 | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/flow-app` | `FlowAppError` | 31 | `maybe` | `retry` |
| `https://gflow-cli.dev/errors/reference-not-found` | `ReferenceNotFoundError` | 32 | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/sync-partial` | `SyncPartialError` | 34 | `no` | `retry` |
| `https://gflow-cli.dev/errors/extend-unavailable` | `ExtendUnavailableError` | 35 | `no` | `none` |
| `https://gflow-cli.dev/errors/flow-host-migrated` | `FlowHostMigratedError` | 36 | `no` | `none` |
| `https://gflow-cli.dev/errors/insufficient-credits` | `InsufficientCreditsError` | 37 | `no` | `wait` |
| `https://gflow-cli.dev/errors/flow-account-chooser` | `FlowAccountChooserError` | 38 | `no` | `reauth` |
| `https://gflow-cli.dev/errors/flow-access-unavailable` | `FlowAccessUnavailableError` | 39 | `no` | `none` |

`tests/test_problem_details_extensions.py` fails when a class's `type` is missing
here or its row disagrees with the code.
