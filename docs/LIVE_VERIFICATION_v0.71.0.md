# Live verification — v0.71.0

> Offline green proves our code does what we think it does. It cannot prove Flow still
> behaves the way we captured it. This is the record of what was exercised against real
> Flow for this release, and — just as importantly — what was **not**.

**Date:** 2026-09-07 · **Profiles:** `ffroliva` (migrated `flow.google.com`, 501→ credits),
`ci-probe` (**migrated `flow.google.com`**, freemium, **no credit**) · **Host lane matters
here**: several items below are migrated-host-specific.

> **Host label corrected 2026-09-08.** This line said `ci-probe` was on **labs**, which
> contradicted [LIVE_VERIFICATION_v0.70.0](LIVE_VERIFICATION_v0.70.0.md) one day earlier and
> was wrong. Measured: `labs.google/fx/tools/flow` on that profile redirects to
> `flow.google.com/` — the one-way migration redirect. In a repo where "Flow's UI shows X"
> is not a fact until the host is named, a mislabelled host in a verification ledger
> silently re-scopes every conclusion keyed to it — and this one helped make a credit
> theory look plausible for #719 when the real cause was a one-time consent dialog
> ([spike](superpowers/spikes/2026-09-08-migrated-upload-fails-two-ways.md)).

---

## 1. `gflow character create --voice` / `--personality` — VERIFIED ✅

The headline item, and the one that had never been exercised once: a repo-wide grep for
`--voice` across `tests/e2e/` matched **nothing** before this release.

| layer | evidence |
|---|---|
| command | `gflow character create --voice Charon --personality "<accented, unique marker>" --json` |
| exit code | `0` |
| structlog invariants | `character` op row written; create payload carried a non-empty `voice` |
| read-back from Flow | `gflow character show --json` → `voice` present and non-null |
| **user-confirmable artifact** | `[voice-case-evidence] sent='Charon' stored='Charon' identical=True` |
| runtime | `1 passed in 227.92s` |

**What this settled.** `docs/CHARACTER.md` and `docs/CHARACTER_RECON.md` contradicted each
other on the wire case — Capitalized vs `"gacrux"` lowercase — with CHARACTER.md flagging it
UNVERIFIED. The Capitalized canonical form round-trips unchanged. Both docs corrected.

**Deliberately not asserted:** whether a *lowercase* id is also accepted. gflow never sends
one (it normalises before the PATCH), so the question is untested rather than answered.

Independently visible in `gflow character list`:

```
castprobe7f3a          57ac6373…  voice Algenib
voice-attach-4d346e66  6dd71824…  voice Charon
Kael                   75cd2b39…  voice Algenib
```

## 2. Character entity attach + submit on the migrated host — VERIFIED ✅ (behaviour), gated ⛔ (product path)

`MigratedComposer.attach_character_entities` was driven live against `flow.google.com`:

| layer | evidence |
|---|---|
| chip committed | `{'text': 'castprobe7f3a', 'entity_id': '57ac6373…', 'reference_type': 'entity'}` |
| structlog | `migrated.character_entities_attached count=1` |
| submit accepted | Flow's own gallery shows the job **Queued**, then rendering |
| model key on the wire | `abra_r2v_8s` — Flow types it as reference-to-video, correctly |

**This overturned a claim that was in the code.** The guard said the submit *"never produces
a reply"* and that the backend refuses. Neither holds: `MZZa6b` replies with a null payload,
and the job queues and renders. The failure is the **observer** — a null payload never names
a media id, so `submit_and_observe` waits out `SUBMIT_REPLY_BUDGET_S` (60 s, calibrated on
4.0–4.6 s replies against an idle queue) and exits 9 `TransportTimeoutError` while the video
is still rendering.

The `_unported_form` guard therefore **remains** in v0.71.0: until the observer is fixed the
CLI would report a timeout on a healthy generation, which is worse for a user than an
explicit refusal. Tracked in #723; the missing status surface in #741.

## 3. Credit shortfall reports exit 37, not selector drift — VERIFIED ✅

Wiring checked in shipped code, not merely existence:

- `errors.py:637` `InsufficientCreditsError` → exit **37** (`errors.py:1226`)
- `migrated_composer.py:99` `CREDITS_WARNING` anchors on `button.prompt-warning-button,
  [aria-label*='Insufficient credits']`
- `_raise_if_out_of_credits()` is **called from both give-up paths** (`:1310`, `:1321`)
- silent when the warning is absent, so genuine drift still reports 23
- pinned by `tests/e2e/test_insufficient_credits_e2e.py` plus unit tests

**The correction that matters for users:** it is *short for the selected model*, not empty.
The measured account held **50** credits and asked for `--model veo-quality`, which costs
**100**. "You have no credits" is a dead end for someone holding 50; "short for this model"
has a remedy (`veo-lite` is 10).

## 4. "+ New project" CTA anchored structurally — VERIFIED ✅

Measured on the live migrated gallery: the Tier-1 `add` anchor matches **1** where every
previous entry matched **0** (control: 47 ligature nodes). Verified by content at the release
SHA rather than by ancestry — the retracted `raise FlowHostMigratedError` is **gone**, and
`button:has(.google-symbols:text-is('add'))` is **present**.

> This release contains a mistake and its retraction. #739 asserted the migrated gallery
> renders no "+ New project" control gflow can drive; a $0 run created a project there in one
> click. The true summary is: **the CTA is now anchored structurally on `add` instead of by
> English text**, which also fixes a non-EN migrated profile.

## 5. Incident-bundle DOM dump de-blinded — VERIFIED ✅

`querySelectorAll('.google-symbols')` present in `diagnostics.py`. Previously the dump queried
`i.google-symbols` only, so on `flow.google.com` — where every ligature rides a `<mat-icon>` —
**every bundle a migrated user sent us carried an empty ligature list.** The instrument used
to diagnose selector drift was blind to the host the drift lives on, which is why #727 and
#731 stayed invisible.

---

## NOT verified this cycle — recorded, not omitted

**Whether a bound character's voice reaches rendered audio (#738).** Attachment is proven
(§1); application is not. No entity-bound generation has yet returned a file to measure,
because every run exited 9 on the timeout in §2 while the job was still queued.

The blocker is **not** credits or account access, which is a change from how #738 was
originally filed: `ffroliva` can bind an entity and pay for the generation. What is missing is
the ability to retrieve the result — #723's observer fix, or the status surface in #741.

The measurement is already calibrated and the prediction is falsifiable: plate-bound takes of
one character gave **88 / 103 / 118 Hz** against a **4.3 Hz** engine noise floor on an
identical prompt; a genuinely applied Algenib should sit near its own sample's **163.3 Hz**
rather than scattering. One retrievable generation settles it.

**Queue conditions during this cycle.** Flow allows five concurrent generations and reduces
per-minute throughput after heavy daily use. This session submitted ~20, so later runs sat
queued for tens of minutes — which is precisely the condition that makes the 60 s budget
fail, and precisely when a user is doing real work.
