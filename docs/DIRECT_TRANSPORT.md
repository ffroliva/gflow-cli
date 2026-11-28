# Direct HTTP transport (`direct_http`, the default)

`direct_http` sends the measured image and video request shapes to `flow.google.com` as direct
HTTP requests to Flow's own `batchexecute` RPCs instead of driving the composer page. Chrome is
still started, **headless**, for one thing: it mints the reCAPTCHA token and holds the signed-in
session. It does not type the prompt, open the settings pane or click Generate.

It is the default transport since v0.108.0. A request it cannot build exactly is routed to the
`ui_automation` transport, which drives Flow's UI in a headed browser, **before** the browser
starts. Which command opens which kind of browser is listed in
[BROWSER_MODES](BROWSER_MODES.md).

## Turning it off

```bash
GFLOW_CLI_TRANSPORT=ui_automation gflow image t2i "a paper boat on a pond"
```

`GFLOW_CLI_TRANSPORT=ui_automation` (in the environment or `.env`) is the kill switch: every
request then drives Flow's UI, headed, as before v0.108.0. `--transport ui_automation` does the
same for one image command, and `"transport": "ui_automation"` for one `gflow run` config.

The routing applies to `gflow image t2i` / `i2i` / `generate`, `gflow video t2v` / `i2v` /
`generate` / `chain`, `gflow video upscale`, each row of `gflow run`, and the MCP tools
(in-process and as queued jobs). `gflow image batch` and `gflow movie run` drive Flow's UI.

## What it serves

A request goes direct only when every slot of the frame it needs has been measured against
Flow's own submit. Everything else is routed to `ui_automation` before the browser starts.

### Images (`ogiZ0b`)

| Request | Direct | Measured by |
|---|---|---|
| Text-to-image, model `nano2` / `nano21` (sent as `BELUGA`) | yes | 10 consecutive live generations |
| Text-to-image, model `nano2-lite` (`HARBOR_SEAL`) or `nano-pro` (`GEM_PIX_2`), 9:16 aspect (the CLI default; the MCP image tool defaults to 1:1, so an MCP call goes direct only with `aspect="9:16"`), count 1, no reference, no character | yes | one live generation each on direct `ogiZ0b`, 768x1376, with each model name sent in the measured request and an image returned; the spike did not separately prove which model rendered it ([#1549](https://github.com/ffroliva/gflow-cli/issues/1549)) |
| Any other shape of `nano2-lite` or `nano-pro` (another aspect, count above 1, a reference, a character) | no | those slots were measured with `BELUGA` only |
| Aspect 9:16, 16:9 | yes | live generations (768x1376, 1376x768) |
| Aspect 1:1, 4:3, 3:4 | yes | captures of Flow's own submit |
| Count 1 to 4 | yes | live generations: 2, 3 and 4 entries in one request returned 2, 3 and 4 images |
| One image already in the project, referenced through its catalog record, count 1 to 4 | yes | live ref generations (2, 3 and 4 entries returned 2, 3 and 4 images) |
| One `--reference-entity` with its `--reference-entity-name`, count 1 to 4 | yes | live generations: 2, 3 and 4 entries returned 2, 3 and 4 images |
| A local `--ref` file | no | the UI path uploads it |
| A `gflow run` row with a `"ref"` or a character | no | whether the reference can be sent direct is known only once the referenced image exists in the project, after the route is chosen |
| An `@Name` mention in the prompt | no | the UI picker resolves it |
| Any other model (`imagen4`) | no | wire name not captured |
| Several refs, several characters, or a ref plus a character | no | not captured |
| Agent-mode instructions, `--thinking-level`, or `--ui-mode agentic` | no | not captured |

### Video (`YhhmEf`, `eb1hJf`) and upscale (`p0UkFb`)

| Request | Direct | Measured by |
|---|---|---|
| Text-to-video, `omni-1.2-flash`, 4 s, 360p, 9:16 or 16:9, count 1 | yes | the app's captured frame replayed and accepted, headless and headed ([spike](superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md)) |
| Text-to-video with no `--model` | when the model selected in the account's Flow settings forms a measured key | the default frame sends no model; Flow uses the account's last selection |
| Image-to-video from a **local** start frame, Veo 3.1 Lite (the default i2v model), 9:16, no `--duration` / `--resolution` | yes | the app's captured default frame replayed and accepted; the frame is uploaded with `maseQ` first |
| A start frame whose JPEG EXIF orientation is a quarter turn | no | the crop box for a rotated image is not measured |
| An end frame, a start frame already in the project, references, a character, count above 1, or any other model, duration, resolution or aspect | no | not measured |
| Any video with a credit cap (`--max-credits`, `GFLOW_CLI_MAX_CREDITS`, MCP `max_credits`) | no | the cap is checked against Flow's quote, which only the UI shows |
| `video upscale --scale 1080p` of a clip the local catalog records as Omni 1.2 Flash, 4 s, 16:9 | yes | one live upscale of a 360p clip |
| Any other upscale (4K, 720p, 270p GIF, an uncatalogued or other source) | no | the download menu decides; a scale Flow does not offer is exit 22 |

### Every request

| Condition | Direct |
|---|---|
| A configured proxy (gflow's, the environment's or the OS's) | no: the browser and the request could leave by different routes |
| An account served on labs.google, or `GFLOW_CLI_FLOW_HOST=labs.google` | no |

## Failure behaviour

The outcome of a direct attempt is decided by **phase**: whether any byte of the billed request
had left the process when it failed.

| What happens | Charged | Result |
|---|---|---|
| The browser's cookies, the session tokens or the reCAPTCHA mint are unavailable, or the connection cannot be opened | no: nothing was sent | the same request runs on `ui_automation` in a new headed browser; the submit checkpoint is written first, so a cancel still applies |
| Flow answers HTTP 200 with an error frame carrying `PUBLIC_ERROR_UNUSUAL_ACTIVITY` and no generation record | no | the request is sent **once** more, with a token from a new headless browser process ([the retry rule](BROWSER_MODES.md#the-retry-rule)) |
| The second attempt is refused the same way, or the new browser process cannot start | no | final: `WafRejectionError` (exit 10), `charged: "no"`, `retryable: false`, `next_action: "none"` |
| A redirect, HTTP 401 or HTTP 403 after the request was sent | maybe | the run stops; it is not resent |
| A timeout, a dropped connection, any other HTTP status, or a failure while polling or downloading | maybe | the run stops with `next_action: "reconcile"`; gflow does **not** resend, because Flow may have made the media |
| Flow refuses the prompt | no | the same refusal error as the UI path |
| The reply holds fewer images than requested | yes | the returned images are saved and the run succeeds with a `client.images_missing` warning, as when the UI path reads Flow's reply ([USAGE](USAGE.md#json-output---json)) |
| A planned direct video meets a balance of 0 | no | exit 37 before any browser starts |

Every rejected reply drops the cached session token, so a stale one cannot stick in a
long-running MCP server or worker. A reCAPTCHA mint is retried for up to 10 seconds while the
page loads its reCAPTCHA script, because a mint spends nothing. The armed page is reused for the
life of one command, or of one MCP tool call or worker job (a long-running `gflow serve` or
`gflow mcp run` opens a new browser session per job); a page idle for more than 600 s re-arms
before it mints, and a page Flow refused is discarded. At most 4 direct submits are in flight per
command or job, and submits refused together share one relaunch.

## What it records

- Each catalog row carries `"transport": "direct_http"` in its `metadata_json`, with `route`,
  `visibility` and `retried`; the same three fields are in `--json` output and MCP results.
- Log events: `direct.frame_built`, `direct.image_post`, `direct.upload`, `direct.video_post`,
  `direct.video_poll`, `direct.upscale_post` (status and timings), `direct.indeterminate` (a
  failure after the send), `direct.fallback_to_ui` (reason), `direct.start_rate_high` (more than
  8 submits started within 120 s on one profile), `mint.rearmed` and `mint.retried`. Set
  `GFLOW_CLI_LOG_LEVEL=INFO` to see them. Tokens, cookies, prompts and signed URLs are never
  logged.

## Refusals observed

On 2026-11-23 the test profile made about 90 image generations in one day. Late in that day
Flow answered some requests with a WAF refusal (exit 10, `WafRejectionError`), on direct and
plain requests alike, including a plain text-to-image with no reference; the identical
request usually went through minutes later. The refusals followed the day's volume, not a
request shape: Flow's own submit for each refused shape was captured and matches what the
direct path sends. Those refusals came back as a refusal reply (exit 10) and nothing was
billed. In the 2026-11-27 spike a refused page stayed refused for 20 minutes, while fresh pages
in new browser processes were accepted, headless and headed alike; that is why such a refusal is
now sent once more from a new headless browser process, and why a second refusal is final.

## Speed

Measured on one profile, ten generations each, same prompt and model: the direct path took
a median of 18.6 s from mint to reply, the warm UI transport 20.7 s. Flow's own generation
time dominates both. The case for the transport is that it does not depend on the
composer's DOM, so selector changes and UI locale do not affect it, and that it needs no
visible browser window.

Evidence: direct video, upscale and the headless mint: [`docs/superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md`](superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md); images: [`docs/superpowers/spikes/2026-11-22-direct-image-gate0.md`](superpowers/spikes/2026-11-22-direct-image-gate0.md); counts 2 to 4 with one reference or character: [`docs/superpowers/spikes/2026-11-25-direct-image-multi-count-ref-character.md`](superpowers/spikes/2026-11-25-direct-image-multi-count-ref-character.md).
