# Direct HTTP image transport (`direct_http`, experimental)

`direct_http` generates images on `flow.google.com` with one direct HTTP request to Flow's
`ogiZ0b` RPC instead of driving the composer page. The browser is still started and still
needed: it mints the reCAPTCHA token and holds the signed-in session. It no longer types
the prompt, opens the settings pane or clicks Generate.

It is experimental. It is off unless you select it, and any request it cannot build
exactly goes to the default `ui_automation` transport before anything is sent.

## Turning it on

```bash
GFLOW_CLI_TRANSPORT=direct_http gflow image t2i "a paper boat on a pond"
```

The setting applies to `gflow image t2i` / `i2i` / `generate`, to the MCP image tool
(in-process and as a queued job), and to each row of `gflow run` (from the environment, or
`"transport": "direct_http"` in the config; a `"ref": "batch:N"` row is served too).
`gflow image batch` resolves to the same transport, but its rows run the inherited UI batch
method, which flow.google.com refuses on every transport.

## What it serves

A request goes direct only when every slot of the frame it needs has been measured against
Flow's own submit. Everything else is routed to `ui_automation` before any request is sent.

| Request | Direct | Measured by |
|---|---|---|
| Text-to-image, model `nano2` / `nano21` (sent as `BELUGA`) | yes | 10 consecutive live generations |
| Aspect 9:16, 16:9 | yes | live generations (768x1376, 1376x768) |
| Aspect 1:1, 4:3, 3:4 | yes | captures of Flow's own submit |
| Count 1 to 4 | yes | live generations: 2, 3 and 4 entries in one request returned 2, 3 and 4 images |
| One ref made in this run: a `gflow run` row's `"ref": "batch:N"`, one image | yes | a live `gflow run` |
| One `--ref ./photo.png` (a local file not yet in the catalog), one image | yes | uploaded by one direct `maseQ` request (reCAPTCHA action `UPLOAD_IMAGE`), then generated directly; a live generation |
| One `--reference-entity` with its `--reference-entity-name` | yes | a live generation |
| One `@Name` mention of a character in the project | yes, when exactly one character has that name (trimmed, case-insensitive, as the UI picker matches) | the id is read from the project; a live generation |
| Any other model (`nano-pro`, `nano2-lite`, `imagen4`) | no | wire name not captured |
| Several refs, several characters, a ref plus a character, or a ref or character with count 2-4 | no | not captured |
| A mention whose name matches no character, or several | no | the UI picker decides |
| Agent-mode instructions, or `--ui-mode agentic` | no | not captured |
| A configured proxy (gflow's, the environment's or the OS's) | no | the browser and the request could leave by different routes |

## Failure behaviour

| What happens | Before the request is sent? | Result |
|---|---|---|
| The browser's cookies, the session tokens or the reCAPTCHA mint are unavailable | yes | the same request runs on `ui_automation` |
| The connection cannot be opened (or opening it timed out) | yes | the same request runs on `ui_automation` |
| The account is served on labs.google, or `GFLOW_CLI_FLOW_HOST=labs.google` | yes | the same request runs on `ui_automation` |
| Flow answers a redirect, HTTP 401 or HTTP 403 | no: it was sent once and refused | the request is submitted a second time, through `ui_automation`'s own page |
| A timeout, a dropped connection or any other HTTP status | no | the run stops with a network error (`next_action: reconcile`); gflow does **not** resend, because Flow may have made the image |
| Flow refuses the prompt | no | the same refusal error as the UI path |
| A `--ref` found in the local catalog (a media id, or a file used before) | yes: the selector routes it before any request | the request runs on `ui_automation`; the catalog may be stale, and the UI path proves the image still exists before it submits, refusing at $0 |

A sent request is never retried. Every rejected reply drops the cached session token, so a
stale one cannot stick in a long-running MCP server or worker. A reCAPTCHA mint is retried
for up to 10 seconds while the page loads its reCAPTCHA script, because a mint spends
nothing. The page is parked after every direct run, as after a UI run.

## What it records

- Each image's catalog row carries `"transport": "direct_http"` in its `metadata_json`.
- Log events: `direct.frame_built`, `direct.image_post` (status, `mint_ms`, `post_ms`,
  `total_ms`) and `direct.fallback_to_ui` (reason). Set `GFLOW_CLI_LOG_LEVEL=INFO` to see
  them. Tokens, cookies, prompts and signed URLs are never logged.

## Refusals observed

On 2026-11-23 the test profile made about 90 image generations in one day. Late in that day
Flow answered some requests with a WAF refusal (exit 10, `WafRejectionError`), on direct and
plain requests alike, including a plain text-to-image with no reference; the identical
request usually went through minutes later. The refusals followed the day's volume, not a
request shape: Flow's own submit for each refused shape was captured and matches what the
direct path sends. Those refusals came back as a refusal reply (exit 10): nothing was billed,
and gflow did not resend them. This differs from an HTTP 403, a redirect or a 401 on the
direct POST, where Flow accepted nothing and the request is submitted once more through
`ui_automation` (see the failure table).

## Speed

Measured on one profile, ten generations each, same prompt and model: the direct path took
a median of 18.6 s from mint to reply, the warm UI transport 20.7 s. Flow's own generation
time dominates both. The case for the transport is that it does not depend on the
composer's DOM, so selector changes and UI locale do not affect it.

Evidence: [`docs/superpowers/spikes/2026-11-22-direct-image-gate0.md`](superpowers/spikes/2026-11-22-direct-image-gate0.md).
