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

The setting applies to `gflow image t2i` / `i2i` / `generate` and to the MCP image tool,
in-process and as a queued job. Batch runs (`gflow run`, `gflow image batch`) still use the
UI transport.

## What it serves

A request goes direct only when every slot of the frame it needs has been measured against
Flow's own submit. Everything else is routed to `ui_automation` before any request is sent.

| Request | Direct | Measured by |
|---|---|---|
| Text-to-image, model `nano2` / `nano21` (sent as `BELUGA`) | yes | 10 consecutive live generations |
| Aspect 9:16, 16:9 | yes | live generations (768x1376, 1376x768) |
| Aspect 1:1, 4:3, 3:4 | yes | captures of Flow's own submit |
| Count 1 or 2 | yes | a live two-image generation in one request |
| One `--ref` to an image already in the project (with its display name) | yes | a live generation |
| One `--reference-entity` with its `--reference-entity-name` | yes | a live generation |
| Any other model (`nano-pro`, `nano2-lite`, `imagen4`) | no | wire name not captured |
| Count 3 or 4 | no | not sent directly |
| Several refs, several characters, a ref plus a character | no | not captured |
| A local-file ref (`--ref ./photo.png`, needs an upload) | no | upload not ported |
| A bare `@Name` mention | no | the request carries no character id to send |
| Agent-mode instructions | no | not captured |
| A configured proxy (gflow's, the environment's or the OS's) | no | the browser and the request could leave by different routes |

## Failure behaviour

| What happens | Before the request is sent? | Result |
|---|---|---|
| The browser's cookies, the session tokens or the reCAPTCHA mint are unavailable | yes | the same request runs on `ui_automation` |
| The connection cannot be opened | yes | the same request runs on `ui_automation` |
| Flow answers HTTP 403 | no, but Flow refused it | the same request runs on `ui_automation` |
| A timeout, a dropped connection or any other HTTP status | no | the run stops with a network error (`next_action: reconcile`); gflow does **not** resend, because Flow may have made the image |
| Flow refuses the prompt | no | the same refusal error as the UI path |

A sent request is never retried. A reCAPTCHA mint is retried for up to 10 seconds while the
page loads its reCAPTCHA script, because a mint spends nothing.

## What it records

- Each image's catalog row carries `"transport": "direct_http"` in its `metadata_json`.
- The weekly usage report counts image generations by transport (`direct`, `ui`): see
  [TELEMETRY.md](TELEMETRY.md).
- Log events: `direct.frame_built`, `direct.image_post` (status, `mint_ms`, `post_ms`,
  `total_ms`) and `direct.fallback_to_ui` (reason). Set `GFLOW_CLI_LOG_LEVEL=INFO` to see
  them. Tokens, cookies, prompts and signed URLs are never logged.

## Speed

Measured on one profile, ten generations each, same prompt and model: the direct path took
a median of 18.6 s from mint to reply, the warm UI transport 20.7 s. Flow's own generation
time dominates both. The case for the transport is that it does not depend on the
composer's DOM, so selector changes and UI locale do not affect it.

Evidence: [`docs/superpowers/spikes/2026-11-22-direct-image-gate0.md`](superpowers/spikes/2026-11-22-direct-image-gate0.md).
