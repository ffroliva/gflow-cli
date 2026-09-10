# Live verification — v0.73.0

> What was exercised against **real** Flow for this release, and — just as importantly —
> what was **not**.

**Dates:** 2026-09-10 · **Profiles:** `ci-probe` (`compiledgrowth…`, **migrated**,
`flow.google.com`) and `denon82` (**migrated**) · **Host:** Windows 11, real Google
Chrome · **Cost: $0** — every run below is navigation, DOM reads, an image generation
(zero Veo credits, daily quota only), or route-interception. No Veo credit was spent.

**Seven arms. Four verified live, one verified in a real browser against a synthetic
page, two not verified — with reasons.** Nothing is left blank.

| # | Arm | Live? |
|---|---|---|
| 1 | CLI boundary → exit 38 with a stripped URL (#777) | ✅ real run, twice |
| 2 | Token redaction A/B (#777) | ✅ measured, 5 → 0 |
| 3 | Google chooser named, not blamed on the selector (#775) | ✅ A/B on `denon82` |
| 4 | Click attribution — **happy path** (#776) | ✅ real `image t2i`, exit 0 |
| 5 | Click attribution — **failure path** (#776) | ⚠️ real browser, synthetic page |
| 6 | `/about` landing on the migrated host (#775) | ❌ does not reproduce |
| 7 | `/fx/api/auth/*` landing on a Flow host (#775) | ❌ state moved first |
| — | Chooser autoselect (#764, external PR) | ❌ not re-verified this cycle |

---

## 1. Chooser boundary → exit 38, URL stripped — VERIFIED ✅

A real `gflow image t2i --profile <name>` on a profile parked at Google's account
chooser, run twice. Both exited **38** (`FlowAccountChooserError`) and named the landing
page — scheme + host + path only.

## 2. Token redaction — VERIFIED ✅ (A/B, not inspection)

The pre-fix run printed `accounts.google.com/v3/signin/challenge/pwd?TL=ACv9tzFkh8ZJ…`
together with the OAuth `state` and `client_id`. Re-running the identical command after
the fix: **same exit 38, same landing named, zero secret matches** (5 → 0).

The A/B matters more than the count. Reading the post-fix output alone would only show
that *these* secrets are absent; running the same command with the fix stashed is what
shows the message ever carried them.

## 3. A known Flow landing is named, not blamed on the selector — VERIFIED ✅

A pytest A/B on `denon82` while that account was genuinely sitting at a Google chooser
mid-run. With the fix neutered the run reported `UiSelectorDriftError` naming
`.settings-trigger-button`; with the fix it reported the landing.

> **This arm falsified a claim already written into PR #775's body.** The body said the
> change "fixes the RED canary". It did not: `denon82` had moved to `accounts.google.com`
> mid-run, a landing `flow_landing_kind` excluded **by design**, with a unit test encoding
> the wrong reason. Found by the live run, fixed in `aac0ef83`, and the PR body corrected.
> The offline suite could not have caught it — it was green throughout.

## 4. Click attribution, happy path — VERIFIED ✅

The #776 change routes **four** click sites through a new `_click` helper. If that helper
were wrong, every generation on the migrated host would break, so the happy path is the
load-bearing regression risk.

```
gflow image t2i "a single matte grey cube on a plain white studio backdrop" \
  --profile ci-probe --project 1e4efe0d-… --json
```

**Exit 0.** Event trace, in order:

```
migrated.navigate → migrated.editor_ready → migrated.image_model_selected
→ migrated.image_settings_applied → migrated.prompt_typed → (submit) → image returned
```

5-layer ledger:

| Layer | Evidence |
|---|---|
| File count | 1 |
| Magic bytes | JPEG, downloaded from a signed `flow-content.google` URL |
| Size | 367 135 bytes |
| Structlog invariants | `image_settings_applied` and `prompt_typed` both present — i.e. `_open_pane` and `send_prompt` both clicked successfully through the new helper |
| User-confirmable artifact | `…/Downloads/gflow-cli/images/2026-09-10/45726039-…_1.jpg` |

**Three of the four converted sites are covered by this run** — `_open_pane`,
`send_prompt`, and `submit_images_and_observe`. The fourth,
`submit_and_observe` (video), is the same helper with the same arguments but spends Veo
credits, so it was **not** run. Named here rather than implied.

## 5. Click attribution, failure path — REAL BROWSER, SYNTHETIC PAGE ⚠️

Six BDD scenarios in real headless Chromium via route interception, **$0**
(`tests/e2e/test_click_attribution_bdd.py`). Each breaks a *different* Playwright
actionability condition for real — a stacked `div` that intercepts pointers, a CSS
animation that never lets the box settle, a pressed agent-mode chip. Playwright's own log
confirms the mechanism:

```
element is visible, enabled and stable
<div id="cover" class="cdk-overlay-backdrop …"> intercepts pointer events
```

Went **RED 5/6 before the fix** (the pass was the A/B control) and **6/6 after**.

**What it does not prove:** that Flow itself still produces this state. The page is ours.
That is the honest limit of route interception, and it is why this row is ⚠️ and not ✅.

## 6. `/about` landing on the migrated host — NOT VERIFIED ❌

**Reason: it stopped reproducing.** A dedicated spike ran the navigation 5× on `ci-probe`
and got **0/5** — recorded as *unmeasured*, not as *transient*
([`2026-09-10-about-redirect-stability.md`](superpowers/spikes/2026-09-10-about-redirect-stability.md)).
A disappearance is equally consistent with state having changed underneath it.

The `/about` branch is covered by unit tests and by a route-intercepted e2e; what is
unverified is that **Flow still redirects there**. Settling it needs an account Flow
actually serves `/about` to, which is not a state we can summon.

## 7. `/fx/api/auth/*` landing on a Flow host — NOT VERIFIED ❌

**Reason: the state moved before it could be reached.** The 03:00 canary run that was
sitting on this landing had progressed by the time the run was attempted. Not blocked by
anything structural — simply missed, and recorded rather than dropped.

## — Chooser autoselect (#764) — NOT RE-VERIFIED THIS CYCLE ❌

**Reason: external contribution, verified by its author, not re-run here.** PR #764
(thanks @stgmt) ships the auto-selection itself. This cycle's work sat *downstream* of it
— arms 1–3 verify the **error boundary** when auto-selection cannot proceed, not the
successful selection.

Re-running the success path needs a profile parked at a chooser with a matching
`.gflow_account`, and `denon82` — the account that reaches a chooser — is currently behind
a Google password challenge that needs a human sign-in. Follow-up hardening is tracked in
[#773](https://github.com/ffroliva/gflow-cli/issues/773), items 3–5 of which remain open.

---

## What a reader should take from this

Four arms verified against real Flow, one against a real browser driving a page we wrote,
two not verified with named reasons, and one inherited from an external PR.

The pattern worth keeping: **arm 3 falsified a claim that was already in a PR body**, and
arm 6 returned "unmeasured" and was recorded as such rather than argued into a retry flag.
Offline green was never the thing that caught either.
