# Live verification — v0.73.1

**Date:** 2026-09-11 · **Profile:** `ci-probe` (migrated host, `flow.google.com`) ·
**Project:** `1e4efe0d-…` · **Cost:** one image generation (daily quota; **zero Veo
credits**), everything else navigation and DOM reads.

v0.73.1 is a two-part patch on the migrated `flow.google.com` driver. Both parts are on
the path every image and video generation takes, so the whole release is live-verifiable
and none of it is exempt.

---

## 1. The defect, measured before the fix

The claim under test is that Google's `glue` consent bar covers the composer's controls.
Nothing in this repo had ever observed it on this host, and the
[2026-09-10 spike](superpowers/spikes/2026-09-10-migrated-click-blocked.md) had read 159
DOM samples on this exact profile and project and watched the click land 3/3.

`scripts/dev/spike_migrated_cookie_bar.py`, 6 samples across the composer-mount window:

| Reading | Result |
|---|---|
| bar elements matched / visible | 6 / 6 |
| settings trigger hit-testable | **0 of 5 rendered** |
| image submit hit-testable | **0 of 5 rendered** |
| what `elementFromPoint` returned over each | `span#glue-cookie-notification-bar-1-label` |
| bar geometry | `position: fixed`, `z-index: 1000`, y 547→655 of a 655 px viewport |

Both controls sit inside that band. Full writeup:
[`superpowers/spikes/2026-09-11-migrated-cookie-bar-blocks-the-composer.md`](superpowers/spikes/2026-09-11-migrated-cookie-bar-blocks-the-composer.md).

## 2. The cure, measured with a control arm

Same script, `--dismiss`, which imports the driver's own `_dismiss_cookie_bar` rather
than clicking the bar itself — so the arm always measures what ships.

| Reading | Before | After |
|---|---|---|
| bar elements visible | 4 / 4 | **0 / 4** |
| settings trigger hit-testable | 0 / 3 rendered | **4 / 4** |
| image submit hit-testable | 0 / 3 rendered | **4 / 4** |
| dismissal error | — | none, one attempt |

---

## 3. Five-layer ledger — `gflow image t2i` end-to-end through the fixed path

The command a user actually runs, pure CLI, on the merged code:

```
gflow image t2i "a single white ceramic cup on a grey stone table, soft daylight" \
  --profile ci-probe --project 1e4efe0d-… --model nano-pro --aspect 16:9 --json
```

| Layer | Evidence |
|---|---|
| **1. File count** | 1 file written, `images/2026-09-11/2c3ed966-…_1.jpg` |
| **2. Magic bytes** | `ffd8ffe0` — JPEG SOI + APP0 |
| **3. Dimensions / shape** | Pillow reads `JPEG (1376, 768) RGB`; 807,761 bytes. Matches the requested `--aspect 16:9` and the reply's `dimensions` block |
| **4. Structlog invariants** | `migrated.navigate` → `migrated.editor_ready` (2.0 s) → `migrated.image_model_already_selected` (`GEM_PIX_2`) → `migrated.image_settings_applied` (`IMAGE_ASPECT_RATIO_LANDSCAPE`, count 1) → `migrated.prompt_typed` (63 chars). **No `migrated.cookie_bar_not_dismissed`**, and no `UiSelectorDriftError` |
| **5. User-confirmable artifact** | `status: ok`, media `2c3ed966-c851-48b5-99c2-1b3f5846c4dc`, workflow `43d6138a-…`, seed 998497784, signed `flow-content.google` URL, local JPEG on disk |

`migrated.cookie_bar_dismissed` is **absent** in this run and that is the expected
reading: the spike's control arm had already consumed consent on this profile, so the
run exercised the bar-free path. That is the regression risk the new call introduces —
a dismissal that runs on every generation — and it is the half this release can prove
live.

## 4. A no-submit repeat of the same path

`ensure_editor` → `apply_image_settings` → `_close_pane` driven directly, $0, no prompt
typed and nothing submitted: editor ready in 2 s, `🍌 Nano Banana Pro` bound, aspect and
count bound, pane closed clean. Confirms the new call is inert when no bar is present,
without spending quota a second time.

---

## What was NOT verified, and why

**The cure against a live bar, outside the browser.** The consent state is not summonable
on demand, and this release's own control arm consumed it on the only profile that had it.
The dismissal is therefore proven live in the *before/after* of §2 and in the browser by
`tests/e2e/test_click_attribution_bdd.py` (8/8, with a stashed-source control run where
both new scenarios fail), but no post-fix CLI generation has yet run against a bar that
was up at the time.

**Prevalence.** One account observed blocked, one (`denon82`) observed already-consented —
and that second run never reached an editor, because both arms redirected to
`flow.google.com/about`. How widely the bar fires is unmeasured, and `KNOWN_ISSUES.md`
says so rather than generalising from n=1.

**The video path, live.** `_dismiss_cookie_bar` sits in `_open_pane`, which video shares,
and the offline suite pins that (`test_the_consent_bar_is_cleared_on_the_video_path_too`).
No live video run was made: it spends Veo credits and exercises the identical function on
the identical control. Named here rather than implied.

## Gates

| Gate | Result |
|---|---|
| `ruff check` / `ruff format --check` | clean, 467 files |
| `pyright src` | 0 errors |
| `pytest -m "not live and not e2e and not smoke"` | 4234 passed, 24 skipped |
| `pytest -m e2e` (click attribution) | 8 passed |
| repo hygiene · doc links · website PII · mirror drift · council memory | all green |
| SonarCloud (PR #782) | gate passed |
