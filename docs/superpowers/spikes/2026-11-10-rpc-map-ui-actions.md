# Spike: which `batchexecute` RPC ids does each Flow UI action send? (#1226)

**Date:** 2026-11-10 · **Profile:** `denon82` (Flow served `flow.google.com`, UI in Portuguese)
· **Build:** `boq_labs-ai-sandbox-frontend_20261110.00_p0` · **Base:** `develop` 78200c21
· **Cost:** $0. Balance 40 before and 40 after. One throwaway project was created and deleted.
· **Script:** [`scripts/dev/spike_rpc_map_1226.py`](../../../scripts/dev/spike_rpc_map_1226.py)

## Question

The served JavaScript holds about 261 RPC ids, and [`FLOW_WIRE_RPCS.md`](../../FLOW_WIRE_RPCS.md)
mapped 17. Which ids does each UI action send, and what are the request and reply shapes?
Which calls are billed? In particular, which RPC deletes a project or an asset? #1227 needs
one to clean up after itself.

## Method

The script opens the profile in gflow's own transport and stays open. Commands come from a
file, so one browser can be explored step by step. A context route sees every page's
`batchexecute` before it leaves and logs a **shape** only: strings become `<uuid>`, `<url>`,
`<tok:N>` or `<s:N>`. Enum-like tokens such as `project_title` and model keys are kept.
Nothing logged holds a token, cookie, `at`, full id or signed URL. Output went to the
gitignored `scripts/dev/_spike_out/1226/`.

The route had three guard modes:

- **always:** abort the known billed submits and upscales;
- **`new`:** also abort any id not yet seen in the session;
- **`strict`:** also abort the known writes.

An aborted id is "seen, not sent". Its request shape is known and its reply is not.
The pre-registered reading is in the script docstring. An id counts for an action only when
it appears in that action's window and not in the idle window just before it.

**Positive control:** opening a project sent `Zzl0ze` and got a 200 in the same run. So the
"no RPC" rows below are findings, not capture misses.

## Results

| Action (UI trigger) | Ids sent | Billed | Request shape | Reply shape |
|---|---|---|---|---|
| Load the projects page (`/`) | `UpteDb`, `nzlxg`, `o30O0e`, `cPZSdc`, `Yizz8d`, `NfrxTb`, `KV2T2d`, `xI9TVb`, `WuwhI` | no | `UpteDb`: `["projects/*", 21, null, null, null, null, [1]]` | `UpteDb`: `[[[<project id>, [<title>, null, [<ts>], <thumb url>?, <media id>?, [<url>]?]], …], <s>, <s>]`. This is the **project listing** (21 per page). |
| Create a project (projects page `add` button) | `jHPbke`, then `UpteDb` | no | `["projects/*", [null, [<title>]], [null, 22]]` | `[<project id>, [<title>]]` |
| Open a project (card thumbnail, or right after a create) | `Zzl0ze`, `ngNC2`, `HTrJv`, `LPzVkd`, `tRARke`, `qJcgMc`, `mrlkwd`, `yBhWQ`, `NfrxTb`, `ve2Lsc` | no | `ngNC2`: `["tools/PINHOLE/projects/<id>"]`; `Zzl0ze`: `["projects/<id>", null, null, null, [1]]`; `mrlkwd`: `[<id>]`; `LPzVkd`: `[[4, 8, 5, 6, 9]]` | `ngNC2`: `[<project id>, [<title>, <thumbnail media id>?]]` (**project read**); `HTrJv`: the model catalog (model keys and display names); `yBhWQ`: model families and their availability; `tRARke`: community tools (`applets/community-<uuid>`); `ve2Lsc`: `[null, ["REGION"]]` |
| Rename a project (header title, then Enter) | `o8DA4` | no | `["projects/<id>", [<title>], [["project_title"]], [null, 22]]` | `[<title>]` |
| **Delete a project** (card `delete` icon, then confirm) | **`QI2zvc`** | no | `["projects/<id>"]` | `[]` (read in #1227; the page's own reply body was unreadable). After a reload, the listing matched the pre-create snapshot id for id. |
| Open a clip (thumbnail in the project grid) | `as29s` | no | `[<workflow id>]` | The generation record (model key, status, signed URLs) |
| Download menu: open it | none | no | (client-side) | Items: 270p GIF, 720p original, 1080p upscaled, 4K upscaled (4K disabled on this account) |
| Download 720p original | none: one `GET flow-content.google/video/<id>` | no | | |
| Download 270p GIF | `QZasKb` (aborted): the first call after the click. The flow stopped at the abort, so the later calls were not observed. gflow's own GIF path expects none (`_EXPORT_RPCIDS` in `migrated_video_upscale.py` is `p0UkFb`, `jwpduf`, and it says the GIF renders client-side). | unknown | `[null, <media id>]` | not sent |
| Download 1080p | **not driven**: the menu item click timed out, so nothing was observed. `p0UkFb` is catalogued from earlier spikes. | | | |
| **Move a clip to trash, candidate** (clip page `delete` icon) | **`pGCYOe`** (aborted), then `o8DA4` (aborted) | unknown | `pGCYOe`: `[[[<media id>, null, null, [null, null, 1], <project id>]], [[<s:17>]]]`, a field-mask update. `o8DA4`: `[…, [["thumbnail_media_key", "is_thumbnail_set_by_user"]], …]` resets the project thumbnail. | not sent. A reload showed the clip still there, so nothing was trashed. |
| Characters (side nav `accessibility_new`; the project had none) | none: opens `/project/<id>/character`, the create page | no | | |
| Character create (describe, then `arrow_forward`) | `C4BZMd` (aborted) | unknown | `[[<uuid>, null, null, [1, <s:19>, []]]]` | not sent. The flow stopped there, so the later calls were not observed. |
| Toggle the Agent chip | `DA4VGb`, `Kcr7Ub` (both aborted) | unknown | `DA4VGb`: `[[null ×11, 1], [["is_agent_mode_toggled"]]]` (user setting); `Kcr7Ub`: `["projects/<id>", [null ×4, 1], [["agent_toggle_state"]]]` (project setting) | not sent |
| Upload an image (toolbar `add` > `upload`) | on this run a rights dialog appeared. Confirming it sent `JJH6Ub` `[14, <s:7>]` → `[]`, plus three `NfrxTb` (`[16]`, `[17]`, `[14]`); one reply was unreadable and replies are not paired to requests. Then `recaptcha/enterprise/reload`, then `maseQ` (aborted) | no for `JJH6Ub` | `maseQ`: `[[null, 22, null, null, null, <uuid>, null ×4, [<tok:2425>, 1]], <png base64>, <s:9>, 1, null ×4, <s:13>, null, <project id>, <uuid>]` | not sent |
| Settings (gear) | none | no | (client-side) | |
| Trash view, tools view (side-nav links, second session) | none: the URL changed to `/project/<id>/trash` and `/tools`, and the tools view rendered the user's tools, so its data came from the project load (`tRARke`) | no | (client-side) | |

`NfrxTb [N]` and `JJH6Ub [N, …]` take the same small integer, and `JJH6Ub` was sent when the
upload-rights dialog was confirmed. On page load `NfrxTb [16]` and `[17]` answered `[true]`. What
`NfrxTb [14]` answered after the confirm is **unknown**, because replies are not paired. So
"`JJH6Ub` dismisses notice N" is a candidate reading, not a measurement.

### Readings

- **Delete project = `QI2zvc`**, proven: sent in the UI, the project gone from the next
  listing, and sent again directly in #1227 with the same result.
- **Move to trash = `pGCYOe`**, a candidate only: seen, not sent, and nothing was confirmed
  trashed. Permanent delete from the trash was not driven.
- **The app's `maseQ` frame carries a token** at `[0][10][0]` (2425 characters), sent right
  after the page POSTs `recaptcha/enterprise/reload`. Whether Flow *enforces* it is unmeasured,
  because the call was never sent.
- **Instructions list: unmeasured.** No UI entry point for Agent-Mode instruction cards was
  found on this build. Clicking the Agent chip sent two setting writes, both aborted, so
  nothing was learned about agent mode. gflow itself reads the cards through labs' tRPC
  `flow.projectInitialData` (`FlowApiClient.get_agent_info`), not through `batchexecute`, so
  no flow.google.com id is known to follow. To settle it, capture with the Agent chip's two
  writes allowed, on a throwaway project.
- **Character list: unmeasured, blocked.** denon82 has no characters. A page-free `Zzl0ze` scan
  of all 21 listed projects showed 19 with the identical empty reply. The other two were opened
  in the UI, and the Characters entry led straight to the create page in both, sending
  nothing. Making a character to list is a create, and creates were allowed only through
  route-abort. To settle it, use a profile that holds a character.
- **Two RPCs share one shape.** `UpteDb` is the projects page's listing *and* (per the 2026-09-05
  frames spike) the `@` picker's asset search.

### Billing

`QZasKb`, `pGCYOe`, `C4BZMd`, `DA4VGb` and `Kcr7Ub` were aborted, so their billing is
"unknown". Every id that was sent cost nothing: the balance was 40 before and after
(`gflow credits user --json`).

## Account state this spike changed

- `JJH6Ub` was **sent**: the upload-rights dialog was confirmed on denon82 and is not shown again.
  This was not reverted.
- One project was created, renamed and deleted (`QI2zvc`), as #1227 also did. No file was uploaded.

## Side finding: the served-JS descriptor key changed

On build `20261110.00` the RPC descriptors read `{Wa:"<id>", …}`. The served-JS spike and
`scripts/canary/rpc_drift.py` (`_XA_RE`) expect `{Xa:"<id>", …}`. Run against today's build,
`_measure` returns `PARSE-CHANGED` ("bundle has no {Xa:...} descriptors"), but every id in
this spike, old and new, is still found as a quoted literal in the four feature modules. This
is a minifier rename, not a removed id. The canary's descriptor regex needs to accept any
two-letter key. Reported, not fixed here.

## Scope and limits

- One profile, one locale, one build, one run per action. The capture ran in-process, not
  through the HAR harness the issue names. Shapes come from the same route, so they are
  equivalent, but no `.har` exists.
- Idle windows were logged only before the create and the rename. For other actions,
  attribution rests on the id appearing right after the click.
- The pre-registered readings were committed together with the results, so their timestamp
  does not prove they were written first.
- No UI action was repeated N times, so these are "seen once" observations, not stability claims.
- The new rows in `FLOW_WIRE_RPCS.md` cite this doc. Ids with no action role stay in its
  "seen on the wire" list.
