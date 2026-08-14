# Live verification — v0.57.1

> Hand-run against real Flow on Windows 11, profile `denon82` (chrome strategy,
> **pt-BR** — the #493 reporter's locale), 2026-08-14. Every check in this
> release is **credit-free**: no Veo or Imagen generation was submitted at any
> point. Recon that led to the fixes was additionally run on `ffroliva`.

## Environment

| | |
|---|---|
| Branch | `chore/release-v0.57.1` (off `develop` @ `6a3f5ff`) |
| Local version | `0.57.1` editable |
| Profiles | `denon82` (pt-BR) primary; `ffroliva` for the capability matrix |
| Date | 2026-08-14 |
| OS | Windows 11 |
| Credits spent | **0** |

## Pre-tag gates

- Full offline suite: **3117 passed / 5 skipped / 89 deselected**.
- `pyright src`: **78 = the accepted baseline** (`mcp/*` + `ui/app.py` only), no delta.
- ruff check + `ruff format --check` (365 files), hygiene (754), doc links (29),
  website PII (23), mirror in sync (18) — all green.
- `/code-review xhigh`: **15 findings, all addressed** — including a hole where
  `model is None` bypassed the new guard (so i2v's *default* path still failed)
  and a shipped `gflow movie template` scaffold that raised at request
  construction.
- `/ponytail:ponytail-review`: **-330 lines** — two superseded spike scripts
  deleted, a triplicated field tuple collapsed to one helper.
- **e2e re-run AFTER the reviews and again AFTER the merge** (the review edits
  touched guard code, so the earlier run was stale): `-m e2e_auth` **18 passed /
  49 deselected** on merged `develop`, 117 s, zero credits.

## Matrix

| # | Feature | Variation | Result |
|---|---|---|---|
| 1 | #493 — expanded-sidebar recovery | real editor, pt-BR | ✅ composer restored |
| 2 | #493 — reporter's cohort simulated | scoped close selector neutered | ✅ fallback rescues |
| 3 | #493 — negative control | scoped **and** fallback neutered | ✅ does **not** recover |
| 4 | #451/#288 — `--duration` on a Veo model | `t2v`/`i2v`/`r2v` × lite/fast/quality | ✅ exit 2, pre-browser |
| 5 | #451/#288 — negative control | `--duration` on `omni-flash` | ✅ passes the guard |
| 6 | `--reference-entity` surface | t2v / i2v / r2v | ✅ present / **absent** / present |
| 7 | Model capability matrix | 5 models × 2 accounts × 2 locales | ✅ identical |

## 1–3. #493 — the expanded chat sidebar (root cause)

Reported as an unrecognized "third editor variant". It is not: **"Inicial"/"Final"
are the Frames sub-mode's Start/End slots and the "Agente" pill is the ordinary
Agent button** — both stock classic-composer features (owner screenshots).

The real trigger is Flow's **expanded chat sidebar**, which removes the classic
composer entirely. Measured live:

| Layer | Evidence |
|---|---|
| Fingerprint | `crop_* triggers = 0` **and** `Agent pill = 0`, simultaneously |
| Why exit 23 | no agentic indicator either → cohort detector matches nothing → drift, not the retryable exit 25 |
| Recovery SPOF | the close X was scoped to the sidebar's `edit_square` affordance |
| Locale | pt-BR reproduced identically — the cascade is ligature-keyed, not text |

**A/B, three configurations** (`scripts/dev/capture_sidebar_state_dom.py`):

| scoped selector | fallback | recovered? |
|---|---|---|
| works | present | yes (baseline) |
| **broken** (reporter cohort) | present | **yes** — the fix |
| **broken** | **broken** | **no** — proves the fallback rescues |

The third row is what makes this a proof rather than a hope. Pinned by
`tests/e2e/test_sidebar_recovery_e2e.py` (`e2e_auth`, $0) and unit tests using a
`sidebar_unscoped` fake state.

**Two hypotheses were refuted first, and are committed so they are not
re-derived:** a `crop_free` sub-mode trigger (the trigger reflects the *aspect*,
not the sub-mode) and a composer hydration race (a wait was written, A/B-neutered,
cold loads passed 3/3 **both ways** — `_probe_selector_cascade` already waits 4 s
per selector, so the wait was reverted rather than shipped).

## 4–5. #451/#288 — `--duration` is model-conditional

Flow's settings popover renders a duration row **only** for `omni_flash`; the
Veo 3.1 models render none. `api/video.py` had claimed they "cap at 8s", which
presumed a control that is never drawn — so `_select_video_duration` hunted a
missing element and died with `UiSelectorDriftError` (exit 23) after ~30 s.

That explains every symptom on those issues: it looked like drift, it reproduced
identically on playwright 1.59 **and** 1.61 (correctly exonerating the version
bound), and the locale hypothesis was correctly refuted. It was never either.

```
$ gflow video t2v "…" --model veo-lite --duration 8
Error: --duration is not supported by --model veo-lite — Flow renders no duration
control for it (verified live; refs #451/#288). Only omni-flash exposes a
duration (4/6/8/10s). …
$ echo $?
2
```

Exit **2 before any browser work**, on all three commands × three Veo models
(10 CLI tests). Negative control: `--model omni-flash --duration 6` **passes**
the guard and proceeds — no over-reject.

## 6. `--reference-entity` surface corrected

The flag was registered on `t2v`/`i2v`/`r2v`, but `_validate_i2v_symmetry`
rejects reference entities — so the i2v form raised for every caller who believed
the help text. Now `t2v` ✅ · `i2v` ❌ · `r2v` ✅, verified by `--help`. The docs
had the mirror-image error (three files plus the `t2v` help text denied a flag
the CLI registered); all corrected.

## 7. Model capability matrix

Verified on **two accounts and two locales** — identical both times:

| Model | Duration tabs | Credits | Ingredients |
|---|---|---|---|
| `omni_flash` | `4s` `6s` `8s` `10s` | 15 @10s / 12 @8s / 7 @4s | accepted |
| `veo_3_1_lite` | none | 10 | accepted |
| `veo_3_1_fast` | none | 20 | accepted |
| `veo_3_1_quality` | none | 100 | **rejected** |

Credits match exactly across accounts; `omni_flash` differs only by selected
duration, which independently confirms **duration-scaled pricing**.

## Not verified this cycle (recorded, not omitted)

- **`veo_3_1_lite_lower_priority`** missed its picker selector on both accounts,
  but the capture that would settle it was taken after the menu had closed —
  **inconclusive**, so the selector was deliberately left untouched rather than
  changed on a guess.
- **`chain.py` per-link `duration`** aborts mid-run *after* earlier links are
  billed, and `_resolve_chain_model` forces a Veo model, so chain durations are
  invalid by construction. Needs manifest-level pre-validation before the run
  starts — its own change, deferred with the finding recorded on #537.
- Credit-spending e2e tiers (`e2e_image`, `e2e_video`) were **not** run: nothing
  in this release touches a generation wire path, and every fix is verifiable at
  $0.

## Post-tag evidence

To be appended after the tag push (release workflow result and PyPI publish).
