# Known Issues

Living list of behaviour that's broken, surprising, or limited by design — alongside workarounds and a pointer to the issue / version where each is tracked or resolved.

> Pair with [CHANGELOG.md](CHANGELOG.md) (what shipped per version) and [DISCLAIMER.md](DISCLAIMER.md) (legal/scope limits).

## Conventions

- **Status: Open** — still happens in latest release. Workaround listed.
- **Status: Mitigated** — partial fix in place; full resolution tracked.
- **Status: Resolved** — fixed in version `X.Y.Z`; row kept here for searchability.

---

## Open

### Flow's new full-page media-library UI breaks entity attach (A/B rollout)

- **Status:** **Open** — Flow-side staged rollout; tracked in
  [#174](https://github.com/ffroliva/gflow-cli/issues/174)
- **Severity:** High · **Affects:** `gflow image t2i --reference-entity` and
  movie R2V entity attach on accounts that received the new UI, any locale

Flow is A/B-rolling a new full-page media-library UI: clicking **Add Media**
in the composer **navigates to a library page** (sidebar: All media /
Characters / Scenes / Tools, with a floating quick-create composer) instead of
opening the resource-picker dialog. On affected accounts the right-click
include action still lands (a chip appears), **but the staged entity never
reaches the submit** — the request carries no `referenceEntities`, so the
submit backstops raise `WireFormatError` (**exit 7**) instead of silently
returning a text-only generation as success.

**How to tell which UI your account has:** in the Flow web editor, click
**Add Media** — a small dialog means the old (working) UI; a navigation to a
full-page library means the affected new UI.

**Note:** the experiment appears to flap — the affected account observed on
2026-06-12 00:13 was back on the old dialog UI by 12:48 the same day (variant
probe, issue #174). If you hit exit 7 on entity attach, re-running later the
same day may simply work again.

**Workaround:** none yet on affected accounts — the attach gesture for the new
UI is being reverse-engineered (recon plan in
[docs/superpowers/plans/2026-06-12-issue-174-library-ui-attach/PLAN.md](docs/superpowers/plans/2026-06-12-issue-174-library-ui-attach/PLAN.md)).
If you have a second profile/account still on the old UI, entity attach works
there. Follow [#174](https://github.com/ffroliva/gflow-cli/issues/174) for
status; please report whether your account shows the dialog or the full-page
library (plus your locale) on that issue.

### 4K image upscale requires a Flow Ultra subscription

- **Status:** **Open** (by design — a Flow platform limit, not a gflow bug)
- **Severity:** Low · **Affects:** `gflow image upscale --scale 4k` on non-Ultra accounts

`gflow image upscale <mediaId> --scale 4k` returns **exit code 22**
(`UpscaleUnavailableError`) on accounts below the Ultra tier — Flow gates 4K
upscaling behind Ultra (the web UI shows an "Upgrade" button instead of a 4K
option). The account tier is reported on the wire as `userPaygateTier` but is
enforced server-side, so gflow cannot grant 4K locally.

**Workaround:** use `--scale 2k` (available on all tiers), or upgrade the Flow
account to Ultra. If you just upgraded, re-run `gflow auth login --profile <name>`
to refresh the session before retrying 4K. Wire detail:
[docs/IMAGE_UPSCALE_RECON.md](docs/IMAGE_UPSCALE_RECON.md) (#171).

### Image generation returns HTTP 401 — `aisandbox-pa` generation endpoint

- **Status:** **RESOLVED in v0.7.0** — moved to [Resolved](#resolved) section
- **Severity:** ~~High~~ · **Was-affecting:** v0.6.0a6 and earlier

> **Resolution (2026-05-20, v0.7.0):** the production `ui_automation` transport
> drives the Flow web UI so Flow's own JS issues `batchGenerateImages` with
> full auth context — bypassing the 401 on the `aisandbox-pa` HTTP path
> entirely. Live-verified end-to-end on the `ffroliva` profile across four
> aspect ratios (`9:16`, `16:9`, `1:1`, `4:3`); see
> [`docs/LIVE_VERIFICATION_v0.7.0.md`](docs/LIVE_VERIFICATION_v0.7.0.md). The
> 401 still hits the experimental HTTP transports
> (`evaluate_fetch` / `bearer` / `sapisidhash`) under
> `src/gflow_cli/api/transports/experimental/` — those are not the
> production path. Historical detail preserved below for searchability.

Image **generation** calls fail with HTTP 401 even on a profile that holds a
fully verified Flow session. Discovered 2026-05-17 while building the e2e test
suite, against a profile probed immediately after a successful
`gflow auth login` (`auth_flow_session_verified`, `[OK] Flow session verified`).

**What works vs. what fails — on the same freshly verified profile:**

| Operation | Endpoint | Result |
|---|---|---|
| `verify_flow_session` | `labs.google/fx/api/auth/session` | ✅ `AUTHENTICATED` |
| `FlowApiClient.health_check()` | Flow page context | ✅ `True` |
| `create_project` | `labs.google/fx/api/trpc/project.createProject` | ✅ 200 |
| **image generation** | `aisandbox-pa.googleapis.com` (private API) | ❌ **HTTP 401** |

The `evaluate_fetch` transport receives a 401 on the generation request, runs
its refresh path (`refresh_auth()` re-navigates to the Flow URL), retries once,
gets 401 again, and raises:

```
AuthExpiredError: evaluate_fetch: HTTP 401 persisted after refresh — session expired
```

from `src/gflow_cli/api/transports/experimental/evaluate_fetch.py`
(`_handle_response`). Call chain: `FlowApiClient.generate_image` /
`generate_images_batch` → `_drive_image_generation` → `transport.generate_images`
→ `_generate_images_inner` → `_handle_response`.

**Distinct from issue #15.** Issue #15 was a 401 on `create_project` caused by
the *profile* being signed in to Google but not the Flow app — fixed on the
`fix/issue-15-i2v-bearer-auth` branch by verifying the real Flow session at
login. That fix is confirmed working: `create_project` now succeeds. **This is
a different 401** — it occurs on a profile that *is* verified and *can* create
projects, specifically on the `aisandbox-pa.googleapis.com` generation
endpoint, a different surface from the `labs.google` tRPC API.

> **Related — L0 aisandbox Bearer auth (`feature/scene-add-clip`, 2026-05-31):**
> the `aisandbox-pa` 401 is NOT a SAPISIDHASH issue — live verification proved
> the real header is **`Authorization: Bearer ya29.<oauth>`** (the SPA's OAuth2
> access token, fetched from `GET /fx/api/auth/session`). `page.request` 401s
> because it sends cookies but not that token. The `gflow scene` groundwork now
> fetches+caches the token and attaches the Bearer to the **`page.request` REST
> path** (`_post_json` / `_patch_json`, host-scoped to `aisandbox-pa`) so
> `uploadImage` / `scenes` / `commit` authenticate — see
> [`docs/superpowers/plans/2026-05-31-l0-bearer-pivot.md`](docs/superpowers/plans/2026-05-31-l0-bearer-pivot.md).
> **Live-verified 2026-05-31:** REST `uploadImage` returns 200
> (`tests/e2e/test_aisandbox_auth_live.py`, credit-free).
> **Liberating follow-up:** the same Bearer likely unlocks the `evaluate_fetch`
> generation 401 above (and REST generation generally, modulo reCAPTCHA) —
> deferred, not yet applied to that transport.

**Scope.** The 401 affects every image-generation path uniformly on the
`evaluate_fetch` transport (the live one): `test_e2e_single_image_gen` (C2,
pre-existing), `test_e2e_generate_image_without_project_id` (PR #20,
pre-existing), and the dropped `test_e2e_generate_images_batch_without_project_id`.
It is **not** caused by recent test changes — `test_transports_e2e.py` is
self-described scaffold ("Task D.1 scaffold; Task D.2 drives the real
execution") that was never run green, and PR #20's e2e tests were merged
without live execution. Whether the production CLI (`gflow image t2i` /
`gflow video i2v`) is equally affected is **unconfirmed** — it uses the same
`FlowApiClient` + transport, so it very likely is, but that has not been
observed directly and should be checked first thing.

**Experimental transports also broken.** The `bearer` and `sapisidhash`
transports (`api/transports/experimental/`) fail before generation is even
reached: `bearer` cannot intercept an OAuth token (`AuthExpiredError: bearer:
failed to intercept Bearer token from Flow page`); `sapisidhash` cascades off
the resulting profile-lock contention. These are obsolete — only
`evaluate_fetch` is viable. Issue-#15 investigation notes had already
disproven the "bearer header" hypothesis for `create_project`.

**Where to investigate.**

- The login OAuth flow *does* request the
  `https://www.googleapis.com/auth/aisandbox` scope (visible in the sign-in
  URL), so the account is authorized — the 401 points at how the credential is
  *presented* to `aisandbox-pa`, not at missing authorization.
- Capture a real generation request from `evaluate_fetch` — the exact URL,
  headers, and credential it sends — and compare with what the Flow web UI
  sends for the same action (browser DevTools network capture).
- The `aisandbox-pa.googleapis.com` host may require a Bearer token: the
  issue-#15 "bearer header" hypothesis was disproven for the `labs.google`
  tRPC `create_project` route, but may hold for this *different* Google API
  host.
- Files: `src/gflow_cli/api/transports/experimental/evaluate_fetch.py`
  (`generate_images`, `_generate_images_inner`, `_handle_response`,
  `refresh_auth`) and `src/gflow_cli/api/client.py` (`_drive_image_generation`).

**Workaround:** none known. Image generation against the live API does not
currently succeed via the e2e transport path.

---

### Browser session expires periodically — manual re-login required

- **Status:** Open · **Severity:** Medium · **Affects:** all versions · **Tracked:** N/A (architectural)

Google's web session cookies aren't permanent. They expire when:
- Long stretch of inactivity (typically months)
- You change your Google password
- You sign out from another device's session manager
- Google flags the session as suspicious (geo-jump, new device fingerprint)

When this happens, the next API call returns 401/403 and `gflow-cli` raises `AuthExpiredError`.

**Workaround:**
```bash
gflow auth login --profile <name>
```

Re-running `auth login` reuses the existing profile dir (you typically just click "Continue as <you>" on the Google account chooser). No data is lost; only the cookie jar is refreshed.

**Why we don't auto-refresh:** Google's session-refresh flow can include CAPTCHA / device verification that only a human can complete. A community SDK can't reliably automate that step. See [docs/AUTHENTICATION.md § Refresh / expiry](docs/AUTHENTICATION.md#refresh--expiry).

**Roadmap:** not scheduled. The Phase 4 hardening pass (v0.4.0a2) added typed `AuthExpiredError` + exit code `3` so scripts can branch on auth expiry deterministically. A periodic "session liveness" check + a `gflow auth refresh` command are still candidates for a later phase, but not committed to a version yet.

---

### Same profile can't be used in parallel

- **Status:** Open (by design) · **Severity:** Low · **Affects:** all versions

Chromium refuses to open two persistent contexts on the same `user-data-dir` simultaneously. So running two concurrent `gflow ...` calls with the same `--profile` will fail the second one with a "ProcessSingleton: profile is locked" error.

**Workaround:** use different profiles for parallel work.

```bash
# Terminal 1
gflow video batch ./batch-a.tsv --profile work

# Terminal 2 — different profile, same time, OK
gflow video batch ./batch-b.tsv --profile personal
```

**Roadmap:** Phase 4 (v0.4.0a2) added concurrency *inside* one `gflow video batch` process via `GFLOW_CLI_CONCURRENCY=N` (per-worker Page pool on one shared BrowserContext). Cross-process same-profile serialization is a Chromium constraint we cannot work around without rewriting the auth model — multiple shells against the same profile remains a "use different profiles" workaround.

---

### Flow's first-upload terms-of-use dialog ("Aviso") blocks the worker (worker-only)

- **Status:** Open · **Severity:** Low · **Affects:** the legacy in-tree Compiled Growth worker, NOT `gflow-cli` itself

Flow shows a one-time "Aviso" / "Notice" terms-of-use confirmation on the first image upload of a new account session. The legacy Playwright worker has to explicitly click "Concordo" / "Agree". `gflow-cli`'s API-driven path bypasses this dialog entirely (the REST endpoint already implies acceptance).

**Workaround in gflow-cli:** none needed.

**Workaround in legacy worker:** see Compiled Growth's `flow_video.py` consent-dismiss block.

---

### Flow's release-notes ("What's new") changelog popup blocks first-run UI automation

- **Status:** Mitigated · **Severity:** Medium · **Tracked:** [#26](https://github.com/ffroliva/gflow-cli/issues/26)

Google Flow ships a release-notes / "What's new" iframe (`changelogs/YYYY-MM-DD-...html`) the first time a logged-in profile visits the page after a Flow deployment. The iframe sits on top of the editor and intercepts pointer events on Flow's own controls — Playwright finds the right selector but cannot click it because the changelog is in the way. Issue [#26](https://github.com/ffroliva/gflow-cli/issues/26) confirmed the same iframe also blocks the settings menu after project navigation.

**Symptom:**
```
playwright._impl._errors.TimeoutError: Locator.click: Timeout 30000ms exceeded.
  - <iframe ... src="https://www.gstatic.com/.../changelogs/...html"></iframe> ... intercepts pointer events
  - retrying click action (57 retries, then timeout)
```

**Mitigation:** `UiAutomationTransport._dismiss_blocking_overlays(page)` detects Flow changelog iframes (`iframe[src*='/flow/changelogs/']`, `iframe[src*='/changelogs/']`) and dismisses them via a close-button selector cascade with an Escape-key fallback. Invoked after `_enter_editor` (image flow) and after `_wait_video_editor_ready` (video flow) so downstream clicks aren't intercepted. Structured logs identify what was dismissed; a debug screenshot is captured if dismissal cannot be confirmed.

**Legacy workaround (no longer required):** open Flow in Chrome with the same profile once, click the `X` on the "What's new" popup, then close Chrome cleanly.

---

### No in-CLI quota visibility

- **Status:** Open · **Severity:** Low · **Roadmap:** v0.5

`gflow-cli` doesn't yet show how many Veo / Imagen credits remain on your Ultra/Pro subscription. You can check at <https://gemini.google/subscriptions/> in the meantime.

**Roadmap:** v0.5 will surface remaining quota via `gflow auth status` once we capture the relevant Google API.

---

### Aspect-ratio support depends on the Veo / Imagen model version

- **Status:** Open · **Severity:** Low

Currently confirmed:
- Veo I2V: `9:16`, `16:9`, `1:1`
- Imagen: `1:1`, `9:16`, `16:9`, `4:3`, `3:4`

Other ratios may be silently rejected or coerced server-side. We validate in the CLI to whitelisted values to fail fast.

---

### `gflow video batch` does not skip already-completed entries

- **Status:** Open · **Severity:** Medium · **Affects:** v0.2.0a1+

If a `gflow video batch` run dies partway through (auth expiry, network blip, Ctrl-C) and you rerun the same TSV manifest, every row is re-submitted to Flow. Flow's private API does not expose a "have I generated this before?" predicate, and `gflow-cli` does not yet maintain a local manifest-of-outputs to compare against.

**Cost implication:** re-running a partially completed manifest **may consume additional Veo / Imagen credits**. We cannot guarantee that Flow de-duplicates server-side — credit accounting on a private API is not contractual.

**Workaround:** before rerunning, trim the manifest down to the rows whose `output_path` does not yet exist on disk:
```bash
awk -F'\t' 'NR==1 || (system("test -e " $NF) != 0)' manifest.tsv > manifest.remaining.tsv
gflow video batch manifest.remaining.tsv
```

**Roadmap:** under consideration for Phase 6 (operations history with a local SQLite ledger — see `PLAN.md`).

---

### REST API 401 — all `aisandbox-pa.googleapis.com` generation endpoints blocked

- **Status:** **RESOLVED in v0.7.0** — image generation live-verified end-to-end
- **Severity:** ~~High~~ · **Was-affecting:** v0.2.0a1 through v0.6.0a6

> **Resolution (2026-05-20, v0.7.0):** `UiAutomationTransport` drives the Flow
> editor so Flow's own JS issues every generation request with full auth
> context — image generation now succeeds end-to-end (see
> [`docs/LIVE_VERIFICATION_v0.7.0.md`](docs/LIVE_VERIFICATION_v0.7.0.md)). Video
> T2V works at the library level via the same transport (Phase A, PR #23);
> CLI wiring (`gflow video t2v/i2v/batch`) is queued for Phase B. The HTTP
> transports under `experimental/` remain blocked by this 401 by design —
> they are not on the production path.

Even with a valid browser session (cookies present), calling Flow's REST API directly via `fetch` or `page.request` against `aisandbox-pa.googleapis.com` returns HTTP 401. This blocks **all** generation routes:

| Endpoint | Status |
|---|---|
| `flowMedia:batchGenerateImages` (image gen) | ❌ 401 |
| `video:batchAsyncGenerateVideoText` (T2V + I2V) | ❌ 401 (confirmed 2026-05-18 e2e run) |
| `flow/uploadImage` (image upload for I2V) | ❓ untested (blocked before reaching this step) |
| `video:batchCheckAsyncVideoGenerationStatus` (status poll) | ❌ 401 (confirmed 2026-05-19 — even via `page.request.post` from the authenticated browser context) |

The Phase 0 video spike (2026-05-19) confirmed the **generation** routes *do*
succeed when driven through the UI (Flow's own JS issues them) — but the
**status-poll** route 401s even from `page.request.post` inside the authed
page. Polling must therefore capture Flow's own status responses, not issue
the request directly. See the video-generation design spec §10.5.

`project.createProject` (on `labs.google/fx/api/trpc`) **does** work — it uses a different domain and auth model.

**Root cause:** Google's backend has tightened security on `aisandbox-pa.googleapis.com`, requiring a browser fingerprint, `Origin`/`Referer` headers, and reCAPTCHA token that raw script-driven requests cannot provide.

**Workaround:** Use the **UI Mimicry** approach — drive the Flow editor by clicking real buttons so the browser itself issues the generation requests with full auth context.

**Roadmap:** v0.6.0a5 will add video generation (T2V + I2V) to the `UiAutomationTransport`, making it the single transport that covers both image and video generation. I2V requires driving the Flow UI's image-upload button so the browser calls `uploadImage` with its own session cookies.

---

### Output dir is not tidied automatically

- **Status:** Open · **Severity:** Low · **By design**

`gflow-cli` never deletes from `$GFLOW_CLI_OUTPUT_DIR`. Generated assets accumulate forever unless you clean them up.

**Workaround:** schedule a cron / Task Scheduler job, e.g.:
```bash
# Delete files older than 30 days
find "$HOME/Downloads/gflow-cli" -type f -mtime +30 -delete
```

---

### `batchGenerateImages` HTTP 403 — WAF / reCAPTCHA `PUBLIC_ERROR_UNUSUAL_ACTIVITY`

- **Status:** Open · **Severity:** High (blocks affected profile until WAF score decays or profile is replaced)
- **First observed:** 2026-05-23 on profile `denon82` during `gflow image batch` runs
- **Surfaces as:** `gflow_cli.errors.WafRejectionError: WAF rejection (HTTP 403): batchGenerateImages HTTP 403 — reCAPTCHA score too low or WAF fingerprint mismatch`
- **structlog signature:** `ui_automation.batch_response_seen` with `status=403` followed by `ui_automation.batch_403_body` containing `'message': 'reCAPTCHA evaluation failed', 'status': 'PERMISSION_DENIED', 'reason': 'PUBLIC_ERROR_UNUSUAL_ACTIVITY'`

Distinct from the historical `aisandbox-pa` 401 (resolved in v0.7.0). The 403
here means Flow accepted the session as authenticated but reCAPTCHA Enterprise
scored the request as bot-like and blocked the generation call. The `denon82`
profile reproducibly 403s on `batchGenerateImages` even after a fresh
`gflow auth login --browser chrome`; the same code path on profile `ffroliva`
(re-authenticated the same day) succeeded end-to-end across one t2i + a 4-image
batch — so it is not a global incompatibility but a per-profile WAF state.

**Likely contributing factors:**
- Repeated automated runs on the same profile within a short window
- Playwright-driven Chrome leaks small fingerprint differences vs. unautomated
  Chrome that reCAPTCHA Enterprise can score
- The image-batch path issues several rapid-fire requests after the
  count-tab clicks, which the scoring may treat as a single fast burst

**Workarounds:**
1. **Use a profile with lower WAF heat** — re-test on a different Chrome-strategy
   profile (`gflow auth login --profile <new> --browser chrome`). The profile
   that has been driven by recent automation runs is usually the hottest.
2. **Let the WAF score decay** — typically hours to a day. Manually using real
   Chrome on the same account in between can help (real interactions lower
   the score).
3. **Avoid same-day repeated batch runs** on a profile after a 403 — each
   rejected request can raise the score further.

**Tracked separately from** the architectural ["first-attempt listener-miss
flake"](https://github.com/ffroliva/gflow-cli/pull/40) — that one was caused
by editor mode confusion and is resolved by PR #40. WAF 403 is a fresh, distinct
issue and not blocked by any code change in this repo.

---

### `UiAutomationTransport` selectors locale-agnostic — issue #24 Phase 5 complete

- **Status:** Resolved (pending owner live e2e on non-EN profile) · **Severity:** Low · **Tracking:** [issue #24](https://github.com/ffroliva/gflow-cli/issues/24), [issue #94](https://github.com/ffroliva/gflow-cli/issues/94), [issue #170](https://github.com/ffroliva/gflow-cli/issues/170)

  `--lang=en-US` removed in PR #127 (2026-05-30). All selector groups now use
  locale-stable anchors: `IMAGE_MODEL_OPTION_SELECTORS` and
  `VIDEO_MODEL_OPTION_SELECTORS` converted to `dict[Model, tuple[str, ...]]`
  cascade structure; branded product names ("Nano Banana 2", "Nano Banana Pro",
  "Imagen 4", "Veo 3.1 - *", "Omni Flash") are confirmed locale-stable
  Google-branded identifiers. Locale is controlled by the `locale=locale_env`
  Playwright kwarg (persists across all in-session navigations). Full resolution
  gate: live e2e with `gflow image t2i` (each model) on a non-EN Chrome profile.

  **2026-06-12 correction (issue #170):** the "all selector groups" claim above
  had two stragglers — `PICKER_INCLUDE_BUTTON` and `PICKER_CONTEXT_INCLUDE`
  hardcoded the pt-BR caption "Incluir no comando", breaking
  `--reference-entity` (image t2i), movie R2V entity attach, and Vozes voice
  attach on every non-Portuguese account (Flow renders in the ACCOUNT language;
  `?hl=en` cannot override it). Fixed by converting both constants to sequential
  tier cascades — context-menu Tier 1 is the locale-free `add`-ligature menuitem
  scoped to the open menu; the Vozes button has no ligature, so pt/ru/en text
  leads with a lone-iconless-dialog-button structural fallback. The matched tier
  is emitted as `ui_automation_video.include_selector_tier` (drift telemetry),
  exhaustion raises typed `TransportTimeoutError` (exit 9) with a locale-neutral
  remediation hint, and an image-side submit backstop now raises
  `WireFormatError` when a requested entity never rode the wire.

**Phase 2 progress (2026-05-25, develop / post-v0.8.1, unreleased):**

- **`ONBOARDING_SELECTORS` restructured** — replaced the original 9 English/PT-BR
  text-only entries with a two-tier cascade:
  1. `_ONBOARDING_STRUCTURAL_SELECTORS` (3 strict entries) — locale-free ARIA/ID
     anchors: `button#L2AGLb` (Google Funding Choices SDK stable ID) plus exact
     ARIA-label matches (`Accept all`, `I agree`). These are programmatic SDK
     constants, not UI strings.
  2. `_ONBOARDING_TEXT_SELECTORS` (~37 entries) — leads with two
     case-insensitive ARIA-partial entries (`aria-label*='Accept' i` /
     `*='Agree' i`) that catch many CMP dialogs (OneTrust, Cookiebot) whose
     aria-label values stay in English even on non-EN pages, followed by
     `:has-text()` selectors covering 14 locales: EN, PT, DE, ES, FR, IT, NL,
     JA, ZH, KO, PL, RU, TR, ID. The ARIA-partial entries live in this tier
     because English aria-label values are not guaranteed across every CMP.
  3. `ONBOARDING_SELECTORS = (*_ONBOARDING_STRUCTURAL_SELECTORS, *_ONBOARDING_TEXT_SELECTORS)`
     so structural entries are always tried first.
  Cascade-ordering invariant is verified by `TestBypassOnboarding` in
  `tests/api/transports/test_ui_automation.py`.

- **`_attach_frame` (I2V/R2V frame slots) flipped to structural-first** — was
  English text-label first (`FRAME_SLOT_BY_LABEL`) with structural fallback; now
  tries `FRAME_SLOTS_STRUCT` first, falls back to `FRAME_SLOT_BY_LABEL` only
  when structural count is insufficient. Slot selection unit tests live in
  `TestAttachFrameSlotSelection` (`tests/api/transports/test_ui_automation_video.py`).

  **Correction (2026-05-26, issue #63):** PR #70's original
  `FRAME_SLOTS_STRUCT = "div:has(> button:has(i.google-symbols:text-is('swap_horiz'))) > div[aria-haspopup='dialog']"`
  matched **zero** elements on real Flow DOMs — the `swap_horiz` icon uses class
  `material-icons` (NOT `google-symbols`) and the slots are `<div type="button">`,
  not children of any `div > button` wrapper. Production I2V therefore relied on
  the English-text fallback and silently broke on non-EN profiles. Discovered
  via DOM probe + LIVE e2e on `ffroliva` (de-DE → pt-BR effective). Replaced
  with `FRAME_SLOTS_STRUCT = "div[type='button'][aria-haspopup='dialog']"` (a
  unique pattern in Flow's editor). Also added a `.first` fallback for the
  End-frame case — after Start is attached, only one structural slot remains
  and the prior `.nth(slot_index)` went out-of-bounds. Both fixes shipped
  together via [#63](https://github.com/ffroliva/gflow-cli/issues/63).

- **`GFLOW_CLI_LOCALE`** — Playwright `locale=` env override from PR #51 remains
  available (default `en-US`).

- **`--lang=en-US` dependency reduced** — `ONBOARDING_SELECTORS` and `_attach_frame`
  no longer require it. The arg is still passed because removing it requires a
  broader live-e2e sweep (I2V/R2V across multiple locales); it will be dropped
  once that completes.

- **Live e2e on `de-DE` (2026-05-25)** — `GFLOW_CLI_LOCALE=de-DE` T2V on
  `ffroliva` (Pro) completed in 70.9 s and returned `MEDIA_GENERATION_STATUS_SUCCESSFUL`
  with a 3.1 MB 1280×720 H.264 mp4 (8 s clip). Confirms the structural-first
  selectors and `GFLOW_CLI_LOCALE` env override work end-to-end on a locale
  outside the original 9-entry English/PT-BR list.

- **Live I2V e2e on `de-DE` (2026-05-26, issue #63 closure)** —
  `GFLOW_CLI_LOCALE=de-DE` I2V (Start + End frames) on `ffroliva` via
  `tests/e2e/test_transports_e2e.py::test_e2e_i2v_start_end_frame_attach`
  completed in 124 s and returned a terminal `SUCCESSFUL` `VideoResult` with a
  downloaded mp4 carrying valid `ftyp` magic bytes. The test asserts on the
  `ui_automation_video.frame_attached` structlog event for both Start and End,
  proving the structural cascade resolved both slots without falling through
  to the EN-text tier. Note: Chrome's UI rendered in pt-BR despite
  `GFLOW_CLI_LOCALE=de-DE` (env affects Playwright's `Accept-Language`, not
  Chrome's profile language) — both are non-EN so the test still verifies the
  locale-leak fix.

**Earlier — Phase 7 multi-image-prompt work** addressed the count-tab selectors:
- `_COUNT_TAB_TEXT_RE = ^(1x|x[2-4])$` only matches the digit+x format Flow
  renders identically in every locale (numbers/symbols are not translated).
- `_set_count` falls back to positional `.nth(count - 1)` when the read-back
  text is unrecognised — locale-invariant.

**Earlier — PR #48:**
- Added `--lang=en-US` Chromium launch arg; parts of `NEW_PROJECT_SELECTORS` /
  `SUBMIT_BUTTON_SELECTORS` tails still match by English text (icon-first selectors
  lead and cover the common path, so these are maintenance debt rather than
  active blockers).

**Remaining gate:** live e2e with `gflow image t2i --model <each>` on a non-EN
Chrome profile (`GFLOW_CLI_LOCALE=<non-EN>`) to confirm model picker resolves
correctly without `--lang=en-US`. `--lang=en-US` has been removed (PR #127);
`locale=locale_env` Playwright kwarg provides locale continuity across navigations.

**Workaround:** with Phase 2 changes, most locales are handled automatically.
For locales outside the 14 covered by `_ONBOARDING_TEXT_SELECTORS`, ARIA-based
structural selectors fire first and cover Google's Funding Choices consent SDK.
For non-standard CMP dialogs not covered, prefer accounts whose Flow renders in
one of the 14 supported locales or in English.

---

### `omni-flash` t2v response omits operation name — `flow_operation_id` persists NULL

- **Status:** Open · **Severity:** Low · **Affects:** v0.9.0+ (data layer)

The data layer's `on_started` callback captures
`operations[0].operation.name` from each `batchAsyncGenerateVideoText`
response and persists it as `operations.flow_operation_id`. The
`omni-flash` model's response shape does not carry that field, so
omni-flash rows end up with `flow_operation_id` NULL while `veo-*` rows
carry the expected `operations/...` identifier. The rest of the row
(prompt, model, aspect, started/completed timestamps, batch ID, output
paths) is recorded normally.

**Impact:** cosmetic for now — `gflow data media <id>` and provenance
lookup by Flow media ID still work. Any future feature that joins on
`flow_operation_id` (none in the current CLI) would miss omni-flash
rows.

**Workaround:** none needed if you don't query by `flow_operation_id`.

**Roadmap:** capture an `omni-flash` `batchAsyncGenerateVideoText`
response sample, identify the equivalent provenance handle (if any), and
either map it into `flow_operation_id` or document that omni-flash
legitimately has no such identifier. Track via a follow-up issue once a
sample is captured.

---

### `gflow video chain` re-exposes the i2v→t2v silent-route risk (issue #125)

- **Status:** Mitigated · **Severity:** Medium · **Affects:** `gflow video chain` (v0.12.0)

Every chain link after the first is an image-to-video (I2V) generation seeded by
the previous clip's last frame. The same silent-route defect that affects
`gflow video i2v` ([issue #125](https://github.com/ffroliva/gflow-cli/issues/125))
applies here: if the chosen model can't do i2v interpolation, Flow drops the
seed frame and routes the request to the plain text-to-video endpoint
(`batchAsyncGenerateVideoText`) — burning a credit for a text-only clip that
breaks continuity, with no error from Flow.

**Mitigation (two layers):**
1. **Model pin.** `omni-flash` (the only model known to silently drop frames) is
   removed from the chain `--model` choices, and the orchestrator rejects any
   model whose `supports_i2v_interpolation()` is false **before any spend**
   (`ModelModeIncompatibilityError`, exit 17).
2. **Per-link wire-route abort.** For each seeded link the transport inspects the
   captured generate-response URL; if it observes `batchAsyncGenerateVideoText`
   for an i2v link it raises `WireFormatError` (logged
   `ui_automation_video.i2v_routed_to_t2v`, issue #125) rather than reporting a
   fake success. The chain aborts and preserves every link completed before the
   failure (`ChainPartialError`).

**Workaround:** stick to the Veo 3.1 models (`veo-lite` / `veo-fast` /
`veo-quality` / `veo-lite-lp`); these are the only accepted chain models.

---

### `gflow video chain` continuity caveat — black / fade-out final frame

- **Status:** Open · **Severity:** Low · **Affects:** `gflow video chain` (v0.12.0)

Chain seeds each link with the **last frame** of the previous clip. If a clip
fades to black (or to a near-empty frame) at its very end — common with
cinematic prompts — the extracted seed frame is mostly black, so the next link
starts from black and continuity visibly breaks.

**Workaround:** pass `--seed-offset MS` to extract the seed frame a few hundred
milliseconds **before** end-of-file, skipping the fade. For example
`--seed-offset 200` seeds from 200 ms before EOF. Tune per the fade length of
your prompts.

---

### `gflow video chain` outputs N clips, not one file — auto-concat is deferred

- **Status:** Open (by design) · **Severity:** Low · **Affects:** `gflow video chain` (v0.12.0)

A chain produces **N separate mp4s** (one per link), not a single stitched
video. Auto-concatenation is deferred: Flow's server-side concatenation
(`runVideoFxConcatenation`, used by `gflow scene`) is **project-scoped** — every
clip must live in the same Flow project to be concatenated. But chain links are
*generated* sequentially and generation cannot pin all links to one shared
project, so there is no clip set the concat endpoint could combine at the end of
a chain run.

**Workaround:** stitch the link clips into one file with `gflow scene`
(server-side, credit-free, no ffmpeg) after the chain completes. The chain
prints its `chain_id` and a reminder to do this on success.

**Roadmap:** wiring chain links into a single Flow project so the chain can
auto-concat its own output is under consideration — tracked as backlog.

---

### `gflow video chain --resume-from` re-seeds the first resumed link as T2V

- **Status:** Open · **Severity:** Low · **Affects:** `gflow video chain` (v0.12.0)

`--resume-from <chain-id>` skips links already paid for in a prior run (they are
**not** re-billed) and continues from the first incomplete link. However, the
first resumed link is generated as a **text-to-video** link, not seeded from the
last frame of the last completed link — there is no cross-run seed-frame
hand-off yet. The resume is **credit-safe** (no double-billing), but visual
continuity restarts at the resume point: the first resumed clip will not flow
seamlessly from the clip before it.

**Workaround:** if seamless continuity across a resume boundary matters, re-run
the affected tail of the chain from scratch rather than resuming, or stitch with
`gflow scene` and accept the cut at the resume boundary.

**Roadmap:** persist and re-extract the boundary seed frame so a resumed link can
continue as a seeded I2V generation — tracked as backlog.

---

## Mitigated

### Auth verification depends on Google's NextAuth session endpoint

- **Status:** Mitigated · **Severity:** Low (degrades fail-closed) · **Affects:** issue #15 fix onward · **Tracked:** issue #15

`gflow auth login` verifies a real Flow sign-in by calling
`https://labs.google/fx/api/auth/session` (see `src/gflow_cli/auth/verification.py`)
and by checking for the Google `SAPISID` cookie. These are **external Google
surfaces** — if Google changes the endpoint path, the response shape, or the
cookie names, verification degrades **fail-closed**: it reports
`VERIFICATION_ERROR` (an honest "could not verify") rather than a false
success. The expected authenticated response shape is pinned by the
`AUTHENTICATED_BODY` fixture in `tests/auth/test_verification.py` — a Google
change surfaces there as a failing test. Start any investigation of a sudden
`gflow auth login` verification failure at that fixture and `verification.py`.

---

## Resolved

### Profile named `default` is opaque — no Google account identity

- **Status:** Resolved · **Severity:** Was-Low (UX confusion, no data loss) · **Was-affecting:** all versions through v0.9.x · **Fixed in:** v0.10.0 via PR #110 (2026-05-28) · **Tracked:** issue #92

The first-run default profile name `default` gave no indication of which Google
account it belonged to, what locale it used, or whether it was valid. On
developer machines with multiple Google Pro/Ultra accounts, this caused confusion
when test profiles, expired sessions, or stale `gflow auth login` runs silently
wrote to the wrong directory.

**Resolution:** `gflow auth login` now writes a `.gflow_account` file to the
profile directory immediately after the session is verified. `profile_store.list_profiles()`
surfaces this as `ProfileMeta.google_account`, and `gflow auth list` (both table
and `--json`) now includes a **Google account** column. The first-run `default`
profile is automatically renamed to the email local-part (e.g. `profile_ffroliva`)
once the email is known, and `config.toml` is updated atomically.

Profiles created before this fix continue to work and display `unknown` in the
account column. Re-running `gflow auth login` against an existing profile backfills
the `.gflow_account` file.

See [AUTHENTICATION.md § Profile naming](AUTHENTICATION.md#profile-naming) for the
new naming convention and [AUTHENTICATION.md § gflow auth list](AUTHENTICATION.md#gflow-auth-list)
for the updated `--json` schema.

---

### `gflow image t2i/i2i --model` was a silent no-op on `ui_automation`

- **Status:** Resolved · **Severity:** Was-Medium (wrong model = wrong cost + quality, silently) · **Was-affecting:** v0.7.0 through v0.8.1 · **Fixed in:** develop post-v0.8.1 via PR #48 (2026-05-24)

Pre-fix, `gflow image t2i --model nano-banana-2` (or any other model) under
`ui_automation` would set the wire-level model field correctly but **never
click the model picker in the editor UI**, so Flow used whichever model the
UI's dropdown was last set to (typically the account default). Users got
their requested model silently substituted for the default — no error, just
wrong output and wrong credit cost. Fixed by `_select_image_model` in
`src/gflow_cli/api/transports/ui_automation.py` which mirrors the new
`_select_video_model` helper. If you ran `gflow image t2i --model <X>`
against v0.7.0–v0.8.1 and noticed the output didn't match `<X>`, this is
why; re-run on the next release (≥ v0.9.0).

---

### aisandbox-pa generation 401 — bypassed by the `ui_automation` transport

- **Status:** Resolved · **Severity:** Was-High (blocked image gen via HTTP transports) · **Fixed in:** v0.7.0

The two long Open-section entries above (*Image generation returns HTTP 401* and *REST API 401 — all `aisandbox-pa.googleapis.com` generation endpoints blocked*) were closed by the same architectural change: `UiAutomationTransport` drives the Flow web UI so Flow's own JavaScript issues every generation request with the full browser auth context (cookies, reCAPTCHA, `Origin`/`Referer` headers). The 401 had affected every direct HTTP call from `evaluate_fetch` / `bearer` / `sapisidhash`; those transports now live under `src/gflow_cli/api/transports/experimental/` and are not on the production path.

End-to-end live-verified on the `ffroliva` profile across `9:16`, `16:9`, `1:1`, and `4:3` aspect ratios; see [`docs/LIVE_VERIFICATION_v0.7.0.md`](docs/LIVE_VERIFICATION_v0.7.0.md) for timing, file sizes, and exact filenames. Video T2V uses the same approach (Phase A — PR #23 — merged 2026-05-19).

---

### G12 "browser not secure" block — Google rejects automated sign-in

- **Status:** Resolved · **Severity:** Critical (blocked `gflow auth login`) · **Fixed in:** v0.6.0a2

Google's sign-in flow (`accounts.google.com/v3/signin/rejected`) detected Playwright's bundled Chromium as an automated browser and refused the login with no user-facing error.

**Root cause (timing race):** Without `--disable-blink-features=AutomationControlled`,
Blink's C++ engine sets `navigator.webdriver = true` as a non-configurable, non-writable
native property at Chrome startup — before any JavaScript (including `add_init_script`)
can run. The `Object.defineProperty` override silently fails. With the flag, the property
is never set; the JS override then works as belt-and-suspenders.

**Resolution:** `v0.6.0a2` adds `RealChromeStrategy` — a new auth strategy that launches
the system's real Google Chrome via Playwright's `channel="chrome"` with stealth flags.

```bash
# Bypass G12 block explicitly:
gflow auth login --browser chrome

# Or rely on auto-detection (default behaviour; picks real Chrome if installed):
gflow auth login
```

A cosmetic "You are using an unsupported command-line flag" notice may appear briefly in
the Chrome window — this is harmless and can be dismissed. It is the accepted trade-off
for bypassing G12.

---

### v0.1 — provider methods are stubs

- **Status:** Resolved · **Severity:** Critical (blocked usage) · **Fixed in:** v0.2.0a1

The v0.1 scaffold left `upload_image`, `start_generation`, `get_job`, `download` raising `NotImplementedError`. v0.2.0a1 wired the video routes (T2V/I2V/batch) on a new `gflow_cli.api.client.FlowApiClient` and removed the legacy `providers/` + `models` modules. v0.3.0a1 added the image routes (`gflow image upload/t2i/i2i`) on the same client.

---

## Reporting a new issue

If you hit something not listed here:

1. Search existing issues at <https://github.com/ffroliva/gflow-cli/issues>.
2. If none match, open a new issue with:
   - `gflow-cli` version (`gflow --version`)
   - Python version (`python --version`)
   - OS + version
   - Exact command that failed + full error output
   - What you expected vs. what happened
3. For **security issues**, see [docs/SECURITY.md § Reporting](docs/SECURITY.md#reporting) — email instead of public issue.
