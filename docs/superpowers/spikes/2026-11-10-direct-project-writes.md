# Spike: project create, rename and upload without a page (#1227)

**Date:** 2026-11-10 · **Profile:** `denon82` (Flow served `flow.google.com`)
· **Build:** `boq_labs-ai-sandbox-frontend_20261110.00_p0` · **Base:** `develop` 78200c21
· **Cost:** $0. Balance 40 before and 40 after. One project was created and deleted, and no file
was uploaded.
· **Script:** [`scripts/dev/spike_direct_writes_1227.py`](../../../scripts/dev/spike_direct_writes_1227.py)
· **Depends on:** [#1226 RPC map](2026-11-10-rpc-map-ui-actions.md), for the frames and the delete RPC

## Question

Do `jHPbke` (create a project), `o8DA4` (rename it) and `maseQ` (upload) work as direct
`batchexecute` calls? The question is the same one `src/gflow_cli/api/flow_wire.py` answers
for `nzlxg`: can they run with the profile's saved cookies and the shell's `at`, and no browser?

**Pre-registered reading** (from the issue, and in the script docstring):

| Outcome | Reading |
|---|---|
| 200, and the object reads back in the listing | a direct write tier is possible for that call |
| an error frame, or a non-200 status | refused: record the error |
| anything else | ambiguous: unmeasured |

"The listing" depends on the object. `Zzl0ze` lists records *inside* a project, not projects,
so #1226 supplied two reads:

- **`UpteDb`** is the projects page's own project listing;
- **`ngNC2`** reads one project's title.

The script checks create and rename against both.

## Method

The script uses no browser. The cookies, user agent and shell tokens (`at`, `f.sid`, `bl`) are
obtained exactly as `flow_wire.read_credits` obtains them, through its helpers. `src` is not
changed and its allow-list is not widened. Each request copies the frame the app itself sent
in #1226, with only the title or project id changed. Every title starts with `spike1227-`.
Cleanup deletes every `spike1227-*` project the listing shows, so an ambiguous create cannot
leave one behind.

`maseQ` was judged before the run, from the #1226 capture. The app's upload frame carries a
2425-character token at `[0][10][0]`. The page POSTs `recaptcha/enterprise/reload` just
before it. The docstring rule says such a call is not sent: sending it would mean emulating
reCAPTCHA. So the script has no upload arm, and no file was uploaded.

## Results (one run)

| Call | HTTP | Frame | Read back | Verdict |
|---|---|---|---|---|
| `jHPbke` create | 200 | `[<project id>, [<title>]]` | `ngNC2` title matches; present in `UpteDb` | **direct write possible** |
| `o8DA4` rename | 200 | `[<new title>]`, matching | `ngNC2` title matches the new title; `UpteDb` shows it | **direct write possible** |
| `maseQ` upload | not sent | | | **needs a page-minted reCAPTCHA token**: not a direct call |
| `QI2zvc` delete (cleanup) | 200 | `[]` | `ngNC2` returns no title; absent from `UpteDb` | **direct delete works** |

### Cleanup proof

- **Before the run:** `UpteDb` listed 21 projects, none titled `spike1227-*`.
- **After the delete:** project `ba1309c9…` was absent from `UpteDb`, and `ngNC2` returned no
  title for it.
- **A second, separate run** (`--cleanup-only`) also listed 21 projects with no `spike1227-*`.
- **The #1226 UI project** (`spike1226-ui-a`) was deleted in the UI. After a reload, the
  projects page listed exactly the 21 project ids it showed before that project was made.
- **Nothing was uploaded,** so no asset is left behind.

## Readings

- **Create and rename** need no page and no reCAPTCHA. Cookies, `at` and the app's own frame
  are enough. A page-free write tier is possible for these two.
- **Delete** (`QI2zvc`) is page-free too, so a page-free tier can clean up after itself.
- **Upload is not page-free.** Its token is minted by the page's reCAPTCHA Enterprise client,
  and gflow must not emulate that.
- This is one run on one profile. It shows the calls *can* work direct. It does not show they
  stay accepted over time, or that throttling treats them like `nzlxg`.

## Next

Any page-free write in `src` is a transport change, and it starts with `/gflow:predict`. It
would reuse `flow_wire`'s guards: allow-list, throttle window, no redirects, a reject-all
cookie jar. Each id it adds gets its own entry on the allow-list, and a write whose reply is
lost must read the outcome back before it is retried.
