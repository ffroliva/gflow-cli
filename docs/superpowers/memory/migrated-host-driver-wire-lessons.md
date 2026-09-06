---
name: migrated-host-driver-wire-lessons
description: "What the flow.google.com (migrated) driver builds got wrong and how each was pinned — poster vs mp4 URL slots, status-3-before-URL, labs redirect route 404s for migrated media ids, CSS :text-matches escaping, direct load works for unflagged accounts; plus the i2v slice-1 frame-attach lessons (late picker index, no media id in DOM, empty Frames submit goes out as t2v)"
metadata: 
  type: project
---

Built and live-verified 2026-09-05 (`docs/LIVE_VERIFICATION_v0.67.0.md`, recon
`docs/superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md`). Each of these
cost a real run to learn; each is now a unit test in `tests/api/transports/`.

- **Record slots:** in the shared `YhhmEf`/`jwpduf`/`as29s` record, `DETAILS[10]` is the
  **poster JPEG** signed URL and `MEDIA_INFO[0][8]` is the **mp4** — the first build
  had them swapped and downloaded a 37 KB JPEG named `.mp4`. `download()` now checks
  `ftyp` at offset 4 and falls back to the other URL; never trust the slot alone.
- **Status 3 arrives before the URL:** the app's `jwpduf` poll reports 3 first; the
  record with the signed URLs (`as29s`) follows 2–8 s later. Treating the first 3 as
  terminal loses the URL — wait a grace (20 s) for the URL-carrying record.
- **Labs redirect route is dead for migrated media:** `media.getMediaUrlRedirect?name=<id>`
  answers **404** for a migrated media id — the signed CDN URL (`flow-content.google`,
  `KeyName=labs-flow-prod-cdn-key`) is the download path; the host is in the allowlist.
- **`:text-matches('^\s*8s\s*$')` is silently wrong:** Playwright's CSS string escaping
  turns `\s` into `s`. Use `locator.filter(has_text=re.compile(...))`; ligature anchors
  (`mat-icon:text-is('videocam')`) carry no backslash and are fine.
- **Composer is the `contenteditable`,** the sibling `textarea` times out on click.
- **Direct load works for everyone:** `https://flow.google.com/project/<id>` served the
  Angular editor to the UNFLAGGED `denon82` (pt) account too — the new host IS the
  default (since #664's round-7 commit) for every request it can serve — t2v with a project —
  on moved and unmoved accounts alike under `auto`; `flow.google.com` forces everything,
  `labs.google` is the kill switch. denon82 itself moved on 2026-09-05.
- **The settings pane is not `.cdk-overlay-pane.last`** (#665): after the model menu — a second
  overlay — opens and closes, a detached menu pane can still be the LAST overlay, so every axis
  after `--model` read "0 option groups". Resolve the pane as the overlay that CONTAINS a
  `[role='radiogroup']`; the fake page keeps a stale menu pane as `.last` so the regression
  cannot pass vacuously. Found by the $0 #650 check, not by any test.
- **Dispatch timing:** on a flagged account the bootstrap page has already hopped when
  `_generate_video_locked` starts (`migrated.dispatch` at ~6.8 s), so the composer is
  chosen BEFORE any labs project entry; the after-entry check exists for the case where
  the hop lands during project navigation.
- **Submit-enable race after `insert_text`** (#670, PR #672): Angular flips the
  `arrow_forward` button from `disabled` ~100 ms AFTER `keyboard.insert_text` lands (measured
  107 ms; `keyboard.type` never shows it). A synchronous `is_enabled()` read straight after
  typing gets the stale state and raised exit 23 on every run for a slow account, while the
  maintainer accounts submitted at ~200 ms and never saw it — a green release ledger is not
  proof the race is absent. `submit_and_observe` now polls up to 5 s at 100 ms; a button that
  never enables is still selector drift ("stayed disabled for 5s"). Note `is_enabled()` with no
  `timeout=` retry-polls until the locator resolves (Playwright 1.61 `_callOnElementOnceMatches`),
  so a button that detaches mid-loop waits out the default timeout — pre-existing, not fixed.
- **Session hook + heredoc apostrophes, reconfirmed:** a bash heredoc whose body carries
  apostrophes in prose fails with "unexpected EOF while looking for matching quote" —
  write edit scripts to a file with the Write tool and run them.
- **i2v (slice 1, 2026-09-05, `docs/LIVE_VERIFICATION_v0.69.0.md`):** the i2v submit is rpc
  **`eb1hJf`** (t2v stays `YhhmEf`); an EMPTY Frames submit goes out on `YhhmEf` with a
  `_t2v_` key — the labs #125 shape — so the submit *request* body is asserted to carry the
  uploaded media id and an `_i2v_` key before the run is trusted. The Frames picker
  (`flow-add-menu-popover-content`) exposes **no media id in its DOM**: uploads are listed
  under their file name, the id comes from the app's own `maseQ` upload reply. **The picker
  search is server-side and a fresh upload is not always indexed on the first query** — on a
  32-asset project two runs missed it within 8 s and a third listed it, so the composer
  reopens the popover and re-searches (3 attempts). In the Frames submode this cohort's
  pane renders **no duration row for Veo 3.1 Lite** (#650 shape): forcing `--duration` is a
  $0 exit 11, so i2v runs pass none. The toolbar `+` is found by XPath
  (`//button[.//mat-icon[normalize-space()='add']][not(ancestor::flow-prompt-box)]`) — the
  prompt box carries its own `add` icons and CSS cannot exclude an ancestor.

**The reCAPTCHA mint sits one layer ABOVE the transport (#673, PR #678).**
`FlowApiClient._mint_recaptcha_token` mints on the pool's bootstrap page before
`transport.generate_images` / upscale / extend ever run, so transport-level
`raise_if_migrated` guards never cover it. On a moved account that page is the
`flow.google.com` grid (client-side handoff), which loads no `recaptcha/enterprise.js`,
so `discover_site_key` raised `RecaptchaError` — a `RuntimeError` unmapped in
`EXIT_CODE_MAP` — as exit 1 "unexpected" instead of exit 36. The guard now runs at the
mint too (`client.py`, `at="mint_recaptcha_token"`); `git grep raise_if_migrated` is
the current list of sites. Reviewing anything that adds a pre-transport step: ask
"which page is the pool holding at that moment on a moved account?"

Related: [[flow-recon-must-run-on-denon82-ffroliva-migrated]],
[[flow-google-com-batchexecute-headless-proven]], [[predict-2026-09-04-migrated-host-driver]],
[[credit-free-route-abort-verification]].

## Frame attach — i2v slice 1 (v0.69.0, #639)

Folded out of `docs/superpowers/plans/2026-09-05-migrated-i2v/` when that plan shipped.
Evidence: `docs/LIVE_VERIFICATION_v0.69.0.md`; recon
`docs/superpowers/spikes/2026-09-05-migrated-frames-attach.md`.

- **The Start-frame picker exposes no media id in its DOM.** Uploads are listed by *file
  name*, so binding is a name search — not an id lookup. Plan for name collisions rather
  than assuming identity.
- **The picker's search is server-side (`UpteDb`) and indexes a fresh upload late.** On a
  32-asset project both e2e tests missed an upload within 8 s and the identical test passed
  minutes later. The composer reopens the popover and searches up to
  `FRAME_SEARCH_ATTEMPTS = 3` times. **A "not found" against a just-uploaded asset is a
  timing claim, not an absence claim** — the same shape as
  [[reference-menu-panel-shared-trigger]].
- **An empty Frames submit silently goes out as text-to-video** (the labs #125 shape) and
  bills a clip that is not what was asked for. Refuse an unbound chip *before* the click —
  exit 23, zero credits. Never let this one degrade into a warning.
- **Inspect the submit request as it leaves.** i2v is rpc `eb1hJf`, t2v is `YhhmEf`; the
  upload is `maseQ`. A body missing the uploaded media id, or carrying a `_t2v_` model key,
  fails the run — after the fact, since the request is already on the wire, but it names
  what Flow was actually asked to make instead of leaving a wrong clip unexplained.
- **A request built without a model does not merely default — it inherits the editor's
  remembered tier.** Runs built directly left `request.model` as `None`, so the composer
  used whatever the editor last had; a queued MCP request (whose payload also carries no
  model) could have inherited a 100-credit tier. Bind the documented default explicitly, and
  log the *effective* model, not the requested one. Found by review, not by a run.
- **An outer stage timeout smaller than the sum of the waits it wraps converts a
  stage-named failure into a generic timeout.** Bound each leg on its own instead.
