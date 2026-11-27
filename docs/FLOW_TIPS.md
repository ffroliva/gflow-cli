# Google Flow tips for automation

Measured behaviours of Google Flow (flow.google.com) that help anyone automating it, with or
without gflow-cli. Each tip names the evidence it rests on and the conditions it was measured
under. Flow is a private, undocumented service and can change at any deploy: treat every tip as
true on its measured date, and re-check it before building on it. Only accounts served
flow.google.com were measured; no account served labs.google was available, so nothing here
says how that host behaves. Counts are per the cited spike: most browser measurements are
headless system Chrome on Windows, on one or two accounts.

The RPC ids below are Flow's `batchexecute` ids; [FLOW_WIRE_RPCS.md](https://github.com/ffroliva/gflow-cli/blob/main/docs/FLOW_WIRE_RPCS.md) lists
each with its payload shape and evidence.

## Cost

| Tip | Evidence |
|---|---|
| **The 1080p video upscale costs 0 credits.** Flow's "Download → 1080p" is one `p0UkFb` request; the upscaled clip is a new record `<workflow id>_upsampled`. Free in all three observations recorded in [FLOW_WIRE_RPCS.md](https://github.com/ffroliva/gflow-cli/blob/main/docs/FLOW_WIRE_RPCS.md) (the tier of those accounts is not recorded; [USAGE.md](USAGE.md) cites the first, 2026-10-07). 4K is offered on Ultra only (Pro shows an upgrade prompt) and spends credits (50 measured on Omni 1.2 Flash, see [USAGE.md](USAGE.md)). | [upscale spike](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-09-30-migrated-upscale-wire.md), [direct video spike](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md), [`USAGE.md`](USAGE.md) |
| **A submit refused as `PUBLIC_ERROR_UNUSUAL_ACTIVITY` is not billed.** The balance read after a refusal equals the balance before it, and Flow's own grid says "You have not been charged". A refusal arrives as an HTTP 200 `batchexecute` reply carrying an error frame and no generation record. | [direct video spike](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md), [refusal spike](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-09-27-migrated-refusal-is-on-the-wire.md), [refusal card note](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/memory/migrated-refusal-is-a-dom-card-not-a-wire-record.md) |
| **Flow's model catalog lists model families and, for families Flow is retiring, the retirement date.** The `HTrJv` reply (sent on every project load) carries each family and its usage keys. One capture also held cost per tier, durations and limits; those slots were seen once and are not relied on (the fixture does not assert them). The fixture holds the image families `BELUGA`, `HARBOR_SEAL` and `GEM_PIX_2` and no Imagen family, so it does not show that the reply lists every model. | [`api/model_catalog.py`](https://github.com/ffroliva/gflow-cli/blob/main/src/gflow_cli/api/model_catalog.py), [`tests/fixtures/flow_htrjv_model_families.json`](https://github.com/ffroliva/gflow-cli/blob/main/tests/fixtures/flow_htrjv_model_families.json) |

## Reading results

| Tip | Evidence |
|---|---|
| **Read a finished clip with its bare workflow id to get the original mp4.** `as29s` with `[workflow_id]` returns a signed `flow-content.google/video/<workflow_id>` URL to the original file. The form the web app sends, `[workflow_id, null, [1]]`, returns only an `lh3.googleusercontent.com/asb/…` link, which resolves to the poster image. | [direct video spike](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md), [thumbnail spike](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-11-05-clip-url-is-a-thumbnail.md) |
| **Check a download against the size Flow reports.** A generation record carries the mp4's byte size at index 13 of the record's details list (`record[5][13]`, where `record` is `[workflow_id, project_id, media_id, marker, null, details, null, media_info]`); every direct download in the spikes matched it exactly. | [direct video spike](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md) |
| **`jwpduf` answers a stub for an id it does not know.** Polling a derived id that does not exist (such as `<workflow id>_upsampled` before an upscale) returns a record with status `4` and no media id, not an empty reply. Read "exists" as "carries a media id". | [direct video spike](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md) |

## reCAPTCHA and sessions

| Tip | Evidence |
|---|---|
| **Generation needs a reCAPTCHA Enterprise token, minted by Flow's own page.** `grecaptcha.enterprise.execute(siteKey, {action})` on a loaded Flow project page; the actions seen are `IMAGE_GENERATION`, `VIDEO_GENERATION` and `UPLOAD_IMAGE`. A token is single-use and short-lived. gflow obtains tokens only by calling that function in a loaded Flow page. | [`api/recaptcha.py`](https://github.com/ffroliva/gflow-cli/blob/main/src/gflow_cli/api/recaptcha.py) (the in-page call), [`direct_http.py`](https://github.com/ffroliva/gflow-cli/blob/main/src/gflow_cli/api/transports/direct_http.py) and [`_flow_media_edit.py`](https://github.com/ffroliva/gflow-cli/blob/main/src/gflow_cli/api/_flow_media_edit.py) (the action names) |
| **A token is not bound to the project whose page minted it.** Tokens minted on a page parked on one project were accepted for submits to another (3/3, one account, headless), so one armed page can serve every project without navigating. | [direct video spike](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md) |
| **A warm page mints in about 0.2 s; a fresh browser takes about 6 s to launch.** Keeping one page loaded removes most of a generation's browser overhead (one observation, one account for the 0.2 s figure). **A warm page needs a re-arm policy.** Both headless refusals of the day came on long-used warm pages (one after twelve earlier mints, one on a 90-minute-old page) and none from a fresh browser; the spike concludes a warm page should be re-armed by age and use count, and a refused page must be discarded and replaced by a new page, never reused. The threshold is not measured. Each `PUBLIC_ERROR_UNUSUAL_ACTIVITY` refusal counts against the profile (see [ACCOUNT_SAFETY.md](ACCOUNT_SAFETY.md#when-google-does-push-back)). | [direct video spike](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md), [warm vs cold](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-11-22-t2i-warm-vs-cold-timing.md); soak and video A/B sections of the direct video spike |
| **Re-read the browser's cookies before each direct request.** Session cookies (`SIDCC`, `__Secure-{1,3}PSIDCC`, `__Secure-{1,3}PSIDTS`, `__Secure-{1,3}PSIDRTS`) rotate every few minutes; reading them from the live browser context before each request absorbed the rotation over a 90-minute session. | [direct video spike](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md) |
| **After a refusal, do not reuse the refused page.** A page whose token was refused was still refused 20 minutes later, while new pages were accepted at that moment. One refused headless image-to-video was followed by an accepted one in a fresh session (one observation, timing unknown), so a retry must come from a new page; for repeated refusals, follow the 30-60 minute cooldown in [ACCOUNT_SAFETY.md](ACCOUNT_SAFETY.md#when-google-does-push-back). A page resumed after a system sleep also minted a refused token. | [direct video spike](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md) |
| **Headless system Chrome minting a token for a direct request was accepted at a comparable rate to headed Chrome** (the difference is not significant at these counts) on two accounts on Windows (fresh-browser images 15/15 in each mode across two accounts; fresh-browser videos 6/6 in each mode on one account; all video submits that day: headless 11/13, headed 11/11). The UI transport still requires headed Chrome. The headless browser reports itself as `HeadlessChrome`: it is not a stealth technique. macOS and Linux were not measured. | [direct video spike](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md) |

## Pacing

| Tip | Evidence |
|---|---|
| **Pace bursts.** Once, fourteen generations in about 90 seconds were followed by a short refusal window even on a fresh page; a faster re-run did not reproduce it, so the cause is unexplained. Eight requests in flight on one page were accepted three times (8/8 each; one run was the tail of the burst above). No safe concurrency level has been measured. | [direct video spike](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md) |

## UI automation

| Tip | Evidence |
|---|---|
| **One-time or recurring modals cover the page and block clicks, but not direct requests.** Seen: the release-notes changelog, the first-upload terms dialog, and a bonus-credits reward modal (a recurring three-day streak reward; its blocking of one click is suspected, not proven). A request sent over HTTP clicks nothing and is unaffected. | [KNOWN_ISSUES.md](https://github.com/ffroliva/gflow-cli/blob/main/KNOWN_ISSUES.md), [direct video spike](https://github.com/ffroliva/gflow-cli/blob/main/docs/superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md) |

## Acknowledgements

The direct-transport measurements were accelerated by [flowfast](https://github.com/JvanLeeuwen4/flowfast),
an independent MIT-licensed project by JvanLeeuwen4 that builds on gflow-cli and published its own
wire map and measurements. gflow-cli re-derived every shape above from its own captures.
