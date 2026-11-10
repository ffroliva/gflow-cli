# Flow `batchexecute` RPC ids

`flow.google.com` does not call a REST API. The app sends every backend call as
`POST https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=<id>&bl=<build label>&…`,
with the request in an `f.req` form field and the CSRF token in `at`. The reply is a
`)]}'` envelope of `wrb.fr` frames, one per RPC, each holding a positional JSON payload.
The id usually rides in the URL's `rpcids` parameter, but one submit has been seen naming
it only in `f.req` (`nprQif` on Omni 1.2), so a listener must read the URL **or** the body.
The [2026-09-05 wire spike](superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md)
describes the format.

An RPC id such as `YhhmEf` is Google's internal name for one backend method. Google does
not publish these names, and any Flow release can rename or drop one. The `bl` query
parameter names the frontend build that sent the call, so a new `bl` is the moment to
expect a change. Nightly tracking of the ids against the served app is issue #1218.

**"In served JS"** means the id was found as a literal string in the JavaScript
flow.google.com serves. That check needs no cookies, no browser and no RPC call, so it
covers billed submits without spending anything. The last check ran on build
`boq_labs-ai-sandbox-frontend_20261108.05_p0` and found all 17 ids, with 0 of 200 random
controls matching. The method is in the
[served-JS spike](superpowers/spikes/2026-11-10-rpc-ids-in-served-javascript.md). Being
present in the JavaScript doesn't prove the payload shape is unchanged. Payload shape is
checked by the e2e tests.

This page lists every id gflow knows: one row each. A cell is either backed by the doc
in its row's Evidence column or by the code line named in it, or it says `unknown`.
Nothing here is inferred. "Billed" is `yes` only where a spike or live-verification
record says a run on that id spent credits or quota, and `no` only where a record says
a run that sent it cost nothing.

`tests/test_flow_wire_rpcs_catalog.py` fails when a module-level `*_RPC`, `*_RPCID`,
`*_RPCS` or `*_RPCIDS` constant in `src/gflow_cli/` names an id with no row here.

| RPC id | Action | UI trigger | Billed | Needs reCAPTCHA | Response shape (short) | Code constant (file:line) | Page-free tested | First seen | Last verified | Status | Renamed from | In served JS | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `YhhmEf` | Text-to-video submit | Composer submit (`arrow_forward`) in video mode, no frame or reference bound | yes | yes (token in `f.req`) | `[null, <balance>, [[<media…>]], [[<record>]]]`; record at `[3][0]`, status `6` | `SUBMIT_RPCS`, `api/transports/migrated_composer.py:222` | no | 2026-09-05 | 2026-11-04 | live | - | yes (2026-11-10) | [wire spike](superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md), [#948 spike](superpowers/spikes/2026-10-06-948-record-slot3-null.md), [lost reply spike](superpowers/spikes/2026-11-04-t2v-lost-submit-reply.md) |
| `eb1hJf` | Image-to-video submit (start frame) | Composer submit with a start frame bound | yes | unknown | Generation record, status `6` at submit | `SUBMIT_RPCS`, `api/transports/migrated_composer.py:222` | no | 2026-09-05 | 2026-10-06 | live | - | yes (2026-11-10) | [frames spike](superpowers/spikes/2026-09-05-migrated-frames-attach.md), [v0.69.0](LIVE_VERIFICATION_v0.69.0.md), [v0.83.0](LIVE_VERIFICATION_v0.83.0.md) |
| `MZZa6b` | Reference-to-video submit | Composer submit with references attached through the `@` picker | yes | unknown | Generation record; null payload on refusal | `SUBMIT_RPCS`, `api/transports/migrated_composer.py:222` | no | 2026-09-05 | 2026-11-07 | live | - | yes (2026-11-10) | [r2v spike](superpowers/spikes/2026-09-05-migrated-r2v-attach-surface.md), [v0.71.0](LIVE_VERIFICATION_v0.71.0.md), [v0.83.0](LIVE_VERIFICATION_v0.83.0.md), [lost submit spike](superpowers/spikes/2026-10-27-lost-video-submit-recovery.md), [Omni 1.2 slider spike](superpowers/spikes/2026-11-07-omni12-duration-slider.md) |
| `nprQif` | Start+end frame (interpolation) video submit; the id was in the URL `rpcids` on 2026-09-17 (Omni 1.1) and only in the body on 2026-11-08 (Omni 1.2) | Composer submit with a start and an end frame bound | yes | unknown | Embeds the standard generation record | `INTERPOLATION_SUBMIT_RPC`, `api/transports/migrated_composer.py:228` | no | 2026-09-17 | 2026-11-08 | live | - | yes (2026-11-10) | [end-frame spike](superpowers/spikes/2026-09-17-migrated-end-frame-submit-contract.md), [interpolation key spike](superpowers/spikes/2026-11-08-omni12-interpolation-key.md), [v0.96.1](LIVE_VERIFICATION_v0.96.1.md) |
| `ogiZ0b` | Image generation submit (text-to-image and image-to-image) | Composer submit in image mode | yes (image quota) | yes (token in `f.req`) | One held-open reply carrying the finished image's signed URLs; a refusal is a null payload with `ErrorInfo` reasons | `IMAGE_SUBMIT_RPC`, `api/transports/migrated_composer.py:229` | no | 2026-09-06 | 2026-11-04 | live | - | yes (2026-11-10) | [character spike](superpowers/spikes/2026-09-06-migrated-character-surface-and-recaptcha.md) (first seen), [image submit spike](superpowers/spikes/2026-09-08-migrated-image-submit-wire.md), [no push channel spike](superpowers/spikes/2026-09-14-generation-wire-no-push-channel.md), [refusal spike](superpowers/spikes/2026-09-27-migrated-refusal-is-on-the-wire.md), [external PRs spike](superpowers/spikes/2026-11-04-external-prs-1142-1145.md) |
| `jwpduf` | Status poll for a generation record; also polls a 1080p video export | None: the page sends it on its own fixed 5 s timer | no | unknown | `[null, N, [[<record>]]]`; status `2` running, `3` done | `STATUS_RPCS`, `api/transports/migrated_composer.py:230`; `_EXPORT_RPCIDS`, `api/transports/migrated_video_upscale.py:54` | no | 2026-09-05 | 2026-11-04 | live | - | yes (2026-11-10) | [wire spike](superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md), [poll timer spike](superpowers/spikes/2026-09-14-video-poll-is-a-fixed-client-timer.md), [r2v spike](superpowers/spikes/2026-09-05-migrated-r2v-attach-surface.md) ($0 run), [lost reply spike](superpowers/spikes/2026-11-04-t2v-lost-submit-reply.md) |
| `as29s` | Result read for one clip: status, signed video URL and size, keyed by workflow id | None: the page sends it after `jwpduf` reports done, and when a clip's page loads (`api/transports/migrated_recover.py:12`) | no | no (page-free read with cookies and `at`) | The bare generation record with signed `flow-content.google` URLs | `STATUS_RPCS`, `api/transports/migrated_composer.py:230` | yes (2026-11-09) | 2026-09-05 | 2026-11-09 | live | - | yes (2026-11-10) | [wire spike](superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md), [r2v spike](superpowers/spikes/2026-09-05-migrated-r2v-attach-surface.md) ($0 run), [thumbnail spike](superpowers/spikes/2026-11-05-clip-url-is-a-thumbnail.md); page-free: PR #1205 spike doc `2026-11-09-page-free-reads.md` |
| `Zzl0ze` | Project listing: every generation record in the project, queued ones included | Project page load | no | no (page-free read with cookies and `at`) | List of the project's generation records | `LISTING_RPC`, `api/transports/migrated_composer.py:116` | yes (2026-11-09) | 2026-09-08 | 2026-11-09 | live | - | yes (2026-11-10) | [queue spike](superpowers/spikes/2026-09-08-migrated-queue-is-readable-on-zzl0ze.md), [lost submit spike](superpowers/spikes/2026-10-27-lost-video-submit-recovery.md); page-free: PR #1205 spike doc `2026-11-09-page-free-reads.md` |
| `maseQ` | Local file upload into the project | Toolbar `add` menu, `upload` item, file chooser | no | unknown | `[<media id>, <project id>, …]` | `UPLOAD_RPC`, `api/transports/migrated_composer.py:231` | no | 2026-09-05 | 2026-10-27 | live | - | yes (2026-11-10) | [frames spike](superpowers/spikes/2026-09-05-migrated-frames-attach.md) ($0 run), [upload spike](superpowers/spikes/2026-09-08-migrated-upload-fails-two-ways.md), [lost submit spike](superpowers/spikes/2026-10-27-lost-video-submit-recovery.md) |
| `jHPbke` | Create a project | Projects page floating `add` button | no | no (captured `f.req` has no token slot) | `[<project uuid>, [<title>]]` | `CREATE_PROJECT_RPC`, `api/transports/migrated_composer.py:4282` | no | 2026-09-17 | 2026-09-17 | live | - | yes (2026-11-10) | [project spike](superpowers/spikes/2026-09-17-project-create-on-flow-google-com.md), [v0.78.0](LIVE_VERIFICATION_v0.78.0.md) |
| `o8DA4` | Rename a project | Edit the title in the project header, then Enter | no | no (captured `f.req` has no token slot) | `[<title>]` | `RENAME_PROJECT_RPC`, `api/transports/migrated_composer.py:4284` | no | 2026-09-17 | 2026-09-17 | live | - | yes (2026-11-10) | [project spike](superpowers/spikes/2026-09-17-project-create-on-flow-google-com.md), [v0.78.0](LIVE_VERIFICATION_v0.78.0.md) |
| `nzlxg` | Credit balance and its daily refresh time | Page load | no | no (page-free read with cookies and `at`) | Balance at `[0]`, refresh time at `[6][0]` | `CREDITS_RPC`, `api/transports/migrated_composer.py:4570` (browser read); `_ALLOWED_RPCIDS`, `api/flow_wire.py:51` (read directly by `gflow credits` / `gflow_get_credits` with the profile's saved cookies, falling back to the browser read, #1229) | yes (2026-11-09) | 2026-09-05 | 2026-11-09 | live | - | yes (2026-11-10) | [wire spike](superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md) (first seen), [balance spike](superpowers/spikes/2026-10-22-credits-balance-on-nzlxg.md), [v0.87.1](LIVE_VERIFICATION_v0.87.1.md); page-free: PR #1205 spike doc `2026-11-09-page-free-reads.md` |
| `fZytfe` | Video extend submit | Extend prompt box submit on a clip | yes | yes (token in `f.req`) | unknown | `EXTEND_SUBMIT_RPC`, `api/transports/migrated_extend.py:67` | no | 2026-09-19 | 2026-11-08 | live | - | yes (2026-11-10) | [extend wire spike](superpowers/spikes/2026-09-19-migrated-video-extend-wire.md), [extend UI spike](superpowers/spikes/2026-10-17-migrated-extend-ui-path.md), [Omni extend spike](superpowers/spikes/2026-11-08-extend-no-scene-create-omni-key.md) |
| `rqZuUc` | Create a scene from a clip's media id, sent before an extend | Extend submit on a clip (not sent on the 2026-11-08 run) | no | no (captured body has no token slot) | Scene record `[<scene id>, "<title>", …]` with one clip | `SCENE_CREATE_RPC`, `api/transports/migrated_extend.py:68` | no | 2026-10-17 | 2026-10-17 | live | - | yes (2026-11-10) | [extend UI spike](superpowers/spikes/2026-10-17-migrated-extend-ui-path.md) ($0), [v0.86.0](LIVE_VERIFICATION_v0.86.0.md), [Omni extend spike](superpowers/spikes/2026-11-08-extend-no-scene-create-omni-key.md) |
| `uwAyfb` | Scene read, sent before the extend submit on the 2026-10-17 path; the 2026-11-08 extend submit went out without it | Extend submit on a clip, after `rqZuUc` (not sent on the 2026-11-08 run) | no | no (captured body has no token slot) | The scene's clips and the project's generation records | none (named in the docstring, `api/transports/migrated_extend.py:11`) | no | 2026-10-17 | 2026-10-17 | live | - | yes (2026-11-10) | [extend UI spike](superpowers/spikes/2026-10-17-migrated-extend-ui-path.md) ($0), [Omni extend spike](superpowers/spikes/2026-11-08-extend-no-scene-create-omni-key.md) |
| `SPrCad` | 2K image upscale | Image download menu, `2K` item | no | unknown | `[[<metadata>], "<base64 JPEG>"]` | `UPSCALE_RPCID`, `api/transports/migrated_upscale.py:37` | no | 2026-09-30 | 2026-10-22 | live | - | yes (2026-11-10) | [upscale spike](superpowers/spikes/2026-09-30-migrated-upscale-wire.md), [lost body spike](superpowers/spikes/2026-10-22-image-upscale-lost-body.md), [v0.87.1](LIVE_VERIFICATION_v0.87.1.md) |
| `p0UkFb` | 1080p video export submit; `jwpduf` then polls `<media id>_upsampled` | Video download menu, `1080p` item | no | unknown | unknown | `_EXPORT_RPCIDS`, `api/transports/migrated_video_upscale.py:54` | no | 2026-09-30 | 2026-09-30 | live | - | yes (2026-11-10) | [upscale spike](superpowers/spikes/2026-09-30-migrated-upscale-wire.md) (0 credits, 1 observation) |

Code paths are relative to `src/gflow_cli/`.

## Request shape fingerprints (#1228)

The table records response shapes only. These are the first recorded request shapes, taken
by letting the page build a submit and aborting it before it left the browser, at $0
([route-abort spike](superpowers/spikes/2026-11-10-route-abort-submit-shape-drift.md)). A
fingerprint keeps structure only: `N` null, `I` int, `U` UUID, `S` any other string, and
`K:` a model key with its digits masked to `9`. It never keeps a value, an id, a token or a
prompt. Each was identical across 3 runs on `denon82`, build
`boq_labs-ai-sandbox-frontend_20261110.00_p0`. Every request carried one `f.req` frame, the id
in both the URL and the body, query keys `_reqid bl f.sid hl rpcids rt source-path` and form
fields `at f.req`.

| RPC id | Driven as | Hash | Inner `f.req` skeleton |
|---|---|---|---|
| `ogiZ0b` | t2i, `NARWHAL` | `d54b2262cb4ee746` | `["N",[["N","N","N","I","I","S","N",["N","I","N","N","N","U","N","N","N","N",["S","I"]],[[["S"]]],"N","N","N","U","U","I"]],"I",["N","I","N","N","N","U","N","N","N","N",["S","I"]],["U"]]` |
| `YhhmEf` | t2v, `veo_3_1_lite`, portrait | `97b4e08cc0790271` | `[[[["N","N",[[["S"]]]],"K:veo_9_9_t9v_lite","I","N",["N","N","N","N","U","U"]]],["N","I","N","N","N","U","N","N","N","N",["S","I"]],["U","I"]]` |
| `eb1hJf` | i2v, `veo_3_1_lite`, portrait, local start frame | `2ed406289469bcf1` | `[[[["N","N",[[["S"]]]],"K:veo_9_9_i9v_lite","I","N",["N","U","N","N","N",["N","N","I","I"]],["N","N","N","N","U","U"]]],["N","I","N","N","N","U","N","N","N","N",["S","I"]],["U","I"]]` |
| `SPrCad` | image upscale, 2K | `402e517f0d9c5709` | `["U","I",["N","I","N","N","N","U","N","N","N","N",["S","I"]]]` |

`fZytfe` (extend) and `p0UkFb` (video upscale) were not reached on that account: the extend
menu item stayed disabled on three clips (#1244), and the video upscale was refused with
`UpscaleUnavailableError` before any click, cause not observed. `MZZa6b` and `nprQif` were
not driven.

## Keeping this table current

| When | What | Who enforces it |
|---|---|---|
| Every commit and CI run | Every RPC id constant in `src/` has a row | `tests/test_flow_wire_rpcs_catalog.py` |
| A spike sees an id that matters | Add a row with its evidence, or list it below | the spike skill |
| Every night | Check every id in this table against the served JS with no cookies and no browser, record the build label, and archive the build's full descriptor list with what changed since the last build | the canary's `RPC-DRIFT` section (`scripts/canary/rpc_drift.py`, #1218); see [E2E_TESTING.md](E2E_TESTING.md#rpc-drift-check-1218) |
| `RPC-DRIFT` reports `ID-GONE` | First confirm with a page load (the served-JS spike's arm 2) that the id did not move to a module outside the four. If it is still absent, set `Status` to `gone` or `renamed -> X`, fill `Renamed from` on the new row, and update `Last verified` | a maintainer, from the canary comment. The canary never edits this file. |

The served app holds about 261 ids in the RPC descriptor shape (`{Xa:"<id>", …}`). This
table and the list below cover the ones gflow has a reason to know.

## Ids seen on the wire that gflow does not use

These appear in spike captures. gflow sends none of them and reads none of them, so they
have no row above.

- With a role a spike measured: `UpteDb` (the `@` picker's server-side asset search,
  [frames spike](superpowers/spikes/2026-09-05-migrated-frames-attach.md)), `eAenfb` (the
  composer's Format prompt rewrite, [format spike](superpowers/spikes/2026-09-07-format-click-is-not-a-format.md)),
  `WuwhI` (carries the prompt after a submit and replies `[]`,
  [wire spike](superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md)), `DTaVef`
  (sent while the editor is idle, [r2v spike](superpowers/spikes/2026-09-05-migrated-r2v-attach-surface.md)),
  `o30O0e` (a page-load reply that carried the account email in one run,
  [session spike](superpowers/spikes/2026-10-22-labs-session-oracle-gone.md)).
- With no measured role: `tRARke`, `HTrJv`, `yBhWQ`, `mrlkwd`, `qJcgMc`, `ve2Lsc`,
  `NfrxTb`, `Yizz8d`, `cPZSdc`, `KV2T2d`, `LPzVkd`, `ngNC2`
  ([queue spike](superpowers/spikes/2026-09-08-migrated-queue-is-readable-on-zzl0ze.md),
  [upload spike](superpowers/spikes/2026-09-08-migrated-upload-fails-two-ways.md)).
