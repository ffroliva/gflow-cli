# Error envelope reference

Every gflow failure is an [RFC 9457](https://datatracker.ietf.org/doc/html/rfc9457)
Problem Details object. `gflow ... --json` prints it as `{"status": "fail", "error": {...}}`,
and MCP tools return it as `{"status": "error", "error": {...}}`. Both carry the
same members, and the worker queue persists the same members for a queued task.

## Members

| Member | Always present | Meaning |
|---|---|---|
| `type` | yes | Stable URI per error class (tables below) |
| `title` | yes | Short human summary of the class |
| `detail` | no | What happened this time |
| `status` | no | The literal HTTP status of the failed call, when there was one |
| `remediation_hint` | no | What a person should do, in prose |
| `route` | no | Sanitized route name (never a full URL) |
| `class` | CLI `--json` | The concrete error class name |
| `exit_code` | CLI `--json`, worker | The process exit code (table below) |
| `retryable` | yes | `true` when the identical request may simply be run again |
| `charged` | yes | Whether Flow may have billed this operation: `no`, `maybe` or `yes` |
| `next_action` | yes | What a caller should do (values below) |
| `operation_id` | no | The local job or media id, when one exists, so a follow-up can find it |
| `retry_after` | no | Seconds to wait, when Flow said so and `next_action` is `retry_after` |
| `completed_media_ids` | no | A partial `video chain`: the media ids of the links Flow made and billed |

`charged`, `next_action`, `operation_id`, `retry_after` and `completed_media_ids` were added in
[#1201](https://github.com/ffroliva/gflow-cli/issues/1201). They are additive: no
existing member changed.

### Result-shaped failures

Some `--json` commands report a failed run as a **result** document rather than an
error: `video t2v`/`i2v`/`r2v` when Flow reports the generation failed, both
`video extend` paths, and a partial `video chain`. Those documents carry `charged` and
`next_action` too (not `retryable`, `type` or `class`), because a billed clip is
exactly what these runs can leave behind
([#1159](https://github.com/ffroliva/gflow-cli/issues/1159),
[#1178](https://github.com/ffroliva/gflow-cli/issues/1178)): a failed generation reads
`maybe` / `reconcile`, and an extend or chain that completed at least one segment or
link reads `yes` / `reconcile`. An extend that spent credits reads `yes` even when no
segment finished, with the submitted segment's media id as `operation_id`.

### `charged`

- `no`: the class proves nothing was submitted, or the operation is free (catalog,
  auth, uploads Flow refused, image generation, scene renders, balance reads).
- `maybe`: the failure can happen before or after a submit. Check the project before
  re-running a paid generation.
- `yes`: Flow made, and billed, the result. Re-running bills again.

### `next_action`

| Value | Do this | `retryable` |
|---|---|---|
| `retry` | Run the same command again | `true` |
| `retry_after` | Run it again after a pause (quota or rate limit) | `true` |
| `wait` | Wait for a condition to clear (credits, another process holding the profile) | `false` |
| `reauth` | Run `gflow auth login` for the profile | `false` |
| `fix_input` | Change the command, its inputs or the local setup | `false` |
| `reconcile` | Settle the outcome first: find the result in Flow (`gflow data download <media_id>` for a billed clip, `gflow scene create --output` for a scene) before re-running | `false` |
| `none` | Nothing a caller can do will change the result | `false` |

`retryable` and `next_action` always agree: `retryable` is `true` exactly when
`next_action` is `retry` or `retry_after`. A raise site that overrides `retryable`
also moves `next_action`: one that turns the retry on reads `retry`, and one that
turns it off reads `reconcile` when the class says Flow may have billed, otherwise
`none`.

**`retry` with `charged: maybe`.** Six classes are raised both before and after a
submit and are retryable by default: `TransportTimeoutError`, `NetworkError`,
`WafRejectionError`, `BrowserSessionClosedError`, `FlowAppError`, `FlowAgentUiError`.
Every raise site gflow knows to come after a submit overrides them to `reconcile`
(`retryable: false`): the labs `t2v`/`i2v`/`r2v` and `video extend` poll timeouts, the
flow.google.com post-submit timeouts, the agent-only composer's refusals, and a failed
download of a finished clip. So a `retry` that reaches you comes from a site gflow treats as before the
submit. `maybe` still says gflow cannot prove it: a scheduler that runs paid
generations unattended should check the project before a second paid attempt.

### Raise-site overrides

The tables give each type's default. A raise site can change any member: classes
built on the base constructor take `charged=`, `next_action=` and `operation_id=`
keywords, and the rest (`RateLimitError`, `WireFormatError`, `BatchPartialError`,
`BatchIntegrityError`, `ChainPartialError`, `SyncPartialError`,
`UiModeUnavailableError`) have the attribute set after construction. The overrides in
place today:

- `MediaDownloadError` raised for a finished, billed clip (`retryable: false`, #941)
  reads `charged: yes`, `next_action: reconcile`, with the clip's media id as
  `operation_id`. The same class raised by the free recovery (`gflow data download`,
  MCP `gflow_download_media`) keeps the default `no` / `retry`.
- A labs video poll timing out after the submit reads `maybe` / `reconcile`, with the
  media id as `operation_id`. That covers both the `t2v`/`i2v`/`r2v` poll (which used to
  surface as an unclassified `UnexpectedError`) and the `video extend` poll.
- A video Flow reports failed reads `maybe` / `reconcile`, with its media id as
  `operation_id`, on the worker queue as on the CLI.
- A failed write of a generated asset to disk or a bucket (`ConfigurationError`) reads
  `maybe` / `reconcile`: the asset is in the Flow project. A scene render's write reads
  `no` / `fix_input`, because the render spends nothing.
- `video chain`: a typed error after at least one link completed keeps its class and
  reads `yes` / `reconcile`, with the last completed link's media id as `operation_id`
  unless it already names the in-flight link.
  An untyped one is wrapped in `ChainPartialError`. `ChainPartialError` reads `yes` only
  when it carries completed links, and lists them in `completed_media_ids`.
- `video extend --output`: any failure while rendering billed segments (reading the
  scene back or the render itself) reads `yes` / `reconcile`. Its hint is the free
  re-render as a command that runs: `gflow scene create --project <id> <clip refs>
  --output <path>`.
- Sites that provably submitted nothing read `no`: the flow.google.com extend editor
  guard (`none`), the `/about` landing (`reauth`), and both balance-read failures.

`operation_id` is only set where an id already exists on the error.

## Every `type`

Exit codes come from `errors.EXIT_CODE_MAP`; a class without its own entry inherits
its parent's, and an unmapped class exits 1. Exit 33 is not an error: `gflow doctor`
uses it for "findings present".

| `type` | Class | Exit | `charged` | `next_action` |
|---|---|---|---|---|
| `https://gflow-cli.dev/errors/api-error` | `FlowApiError` | 1 | `maybe` | `none` |
| `https://gflow-cli.dev/errors/batch-integrity` | `BatchIntegrityError` | 1 | `no` | `reconcile` |
| `https://gflow-cli.dev/errors/batch-partial` | `BatchPartialError` | 1 | `no` | `reconcile` |
| `https://gflow-cli.dev/errors/recaptcha-mint` | `RecaptchaError` | 1 | `no` | `none` |
| `https://gflow-cli.dev/errors/task-cancelled` | `TaskCancelledError` | 1 | `no` | `none` |
| `https://gflow-cli.dev/errors/task-not-found` | `TaskNotFoundError` | 1 | `no` | `fix_input` |
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
| `https://gflow-cli.dev/errors/profile-locked` | `ProfileLockedError` | 11 | `no` | `wait` |
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
| `https://gflow-cli.dev/errors/chain-partial` | `ChainPartialError` | 21 | `maybe` | `reconcile` |
| `https://gflow-cli.dev/errors/upscale-unavailable` | `UpscaleUnavailableError` | 22 | `no` | `none` |
| `https://gflow-cli.dev/errors/server-cancel-unavailable` | `ServerCancelUnavailableError` | 22 | `maybe` | `reconcile` |
| `https://gflow-cli.dev/errors/ui-selector-drift` | `UiSelectorDriftError` | 23 | `maybe` | `none` |
| `https://gflow-cli.dev/errors/browser-engine-unavailable` | `BrowserEngineUnavailableError` | 24 | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/flow-agent-ui` | `FlowAgentUiError` | 25 | `maybe` | `retry` |
| `https://gflow-cli.dev/errors/media-attribution` | `MediaAttributionError` | 26 | `maybe` | `reconcile` |
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

### Envelopes built without an error class

These are built by hand in the MCP server or the worker queue, and carry the same
members. They have no exit code of their own.

| `type` | Built by | Exit | `charged` | `next_action` |
|---|---|---|---|---|
| `https://gflow-cli.dev/errors/unknown` | MCP and worker, an unclassified failure | — | `maybe` | `none` |
| `https://gflow-cli.dev/errors/bad-parameter` | MCP, a tool argument it refuses | — | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/no-profile` | MCP, no profile, or its directory is missing | — | `no` | `reauth` |
| `https://gflow-cli.dev/errors/no-default-profile` | MCP, several profiles and no default | — | `no` | `fix_input` |
| `https://gflow-cli.dev/errors/profile-marker-missing` | MCP `gflow_auth_status` | — | `no` | `reauth` |
| `https://gflow-cli.dev/errors/cookie-store-unreadable` | MCP `gflow_auth_status` | — | `no` | `reauth` |
| `https://gflow-cli.dev/errors/verification-error` | MCP `gflow_auth_status` | — | `no` | `retry` |
| `https://gflow-cli.dev/errors/indeterminate-outcome` | Worker, interrupted after a submit was attempted | — | `maybe` | `reconcile` |
| `https://gflow-cli.dev/errors/daemon-recovery` | Worker, interrupted before any submit | — | `no` | `retry` |
| `https://gflow-cli.dev/errors/cancelled` | Worker queue, a task cancelled before any submit: `gflow cancel` / MCP `tasks/cancel` (state `cancelled`), or a server shutdown that stopped it | — | `no` | `none` |

An unclassified CLI failure (`UnexpectedError`) reads `maybe` / `none` too. Every
error `gflow_auth_status` returns reads `charged: no`, including its dead-session
`auth-expired`. A task that ended without a recorded error reads `maybe` / `reconcile`.
A task `gflow cancel` / `tasks/cancel` aborts after its submit is stored `abandoned` with
`server-cancel-unavailable` (`maybe` / `reconcile`); one a server shutdown stopped after
its submit is `indeterminate-outcome`, still `reconcile`, because Flow may have made it.
`TaskCancelledError` is what a worker raises when it finds its row aborted at the submit
gate; it never reaches the queue row, which stays `cancelled`. None of the worker's documents carries an
HTTP `status`: no HTTP call failed.

`tests/test_problem_details_extensions.py` fails when a `type` is missing here or a row
disagrees with what the code emits.
