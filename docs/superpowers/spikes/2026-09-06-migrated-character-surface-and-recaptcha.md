# Migrated host: the character surface works, and it can mint reCAPTCHA

**Date:** 2026-09-06 · **Account:** `ci-probe` (`compiledgrownth.official@gmail.com`,
migrated) · **Cost:** navigation, DOM reads, free tRPC/REST calls, a handful of image
generations (daily quota, **zero credits**).

Scripts: `scripts/dev/spike_migrated_character_surface.py`,
`spike_migrated_character_editor_anchor.py`, `spike_migrated_vs_labs_provenance.py`,
`spike_migrated_character_submit_wire.py`, `spike_migrated_character_ogiz0b_schema.py`,
`spike_migrated_character_body_controls.py`, `spike_migrated_body_reference_chip.py`,
`spike_migrated_recaptcha_mint.py`.

---

## 1. The premise that was false

`flow.google.com` was documented as unable to render the character editor —
"renders no prompt textbox for it, **ever**". Measured, it renders the editor in full.

| | labs.google | flow.google.com |
|---|---|---|
| Framework | React / Next.js | **Angular** (113 `_ngcontent-*` elements, no React fiber, no `__NEXT_DATA__`) |
| Prompt editor | Slate (`[data-slate-editor]`) | **ProseMirror** (`.ProseMirror[contenteditable]`) — Slate matched **0** |
| Ligature carrier | `<i class="google-symbols">` | **`<mat-icon>`** |
| Reference images | `media.getMediaUrlRedirect` | **`flow-content.google/image/<id>`** |
| Slot controls | sibling-of-image buttons | **`<flow-slot-chip-button>`** custom elements |

The character route does **not** redirect and the entity id survives. `input.name-input`,
`textarea.personality-textarea`, a voice picker and an Upload affordance are all present
and visible, with **no occluder**.

**The backend is shared.** The migrated page's own XHRs: `labs.google` tRPC ×7
(`flow.projectInitialData`, `videoFx.getFlowAppConfig`, `general.fetchUserLocale`) and
`aisandbox-pa` ×5 (`v1:checkAppAvailability`, `v1/credits`). Plus its own
`batchexecute` ×15. So: same backend, rebuilt frontend. The hop from labs is a
**client-side handoff**, not a 30x.

## 2. The generation wire

Portrait generation answers on `flow.google.com/.../batchexecute` **rpcid `ogiZ0b`**,
whose response echoes the prompt with two UUIDs. gflow waited 180 s for the labs
`flowMedia:batchGenerateImages`, which that frontend never calls — so it reported
failure over work that had **succeeded**. The entity carried a workflow id and a
thumbnail media id the moment the timeout fired.

**Do not parse `ogiZ0b`.** Read the result off the entity: Flow binds the workflow
itself, so the ids prove the character binding *by construction*, which is stronger
than the labs path's self-reported `parentEntityId`. Each workflow carries its **own**
`metadata.primaryMediaId` in `flow.projectInitialData` — the entity's single
`thumbnail_media_id` is the portrait only, and reusing it across slots corrupts the
body workflow through `commit_workflow`.

## 3. reCAPTCHA CAN be minted on the migrated host

`gflow image` exits 36 at `raise_if_migrated(at="mint_recaptcha_token")` — a guard,
not an observed failure. Measured on the same page pool:

| route | recaptcha scripts | site key | `grecaptcha.enterprise` | mint |
|---|---|---|---|---|
| `flow.google.com/` (root grid) | **0** | — | false | ❌ `RecaptchaError` |
| `flow.google.com/project/<id>` | **2** | `6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV` | **true** | ✅ **OK, 2404-char token** |

**The origin can mint; the pooled page is simply on the wrong route.** The mint runs on
the bootstrap page, which after the handoff is the root grid. This is the mechanism
behind [#692](https://github.com/ffroliva/gflow-cli/issues/692) — the reporter's bundle
showed `route: "/"`, and `diagnostics.py` maps *both* hosts to
`host_category: flow_app`, so the route is the only tell.

**Not yet established:** that fixing the mint makes `gflow image` work. Image generation
is UI-driven (mint → drive the composer), and the migrated project composer inventories
as **video-only** — ligatures `movie`, `apps_spark_2`, `crop_16_9`, `add`,
`accessibility_new`, with no image-mode control and `[role=tab]` = 0. Image generation
demonstrably exists on that host *inside the character editor*, not in the project
composer. So the mint is a necessary, not sufficient, condition — treat "one anchor
sweep away" as **unproven**.

## 4. What this cost, and the rule that comes out of it

The false premise reached four places from one 20 s timeout: a code comment, the
CHANGELOG, `docs/LIVE_VERIFICATION_v0.70.0.md`, and a test class *name*
(`TestCharacterEditorOnMigratedOriginFailsFast`). #701 then added a guard that ran
**before** the DOM probe, which made the claim unfalsifiable — no run could look.

```
A selector that does not match is evidence about the SELECTOR, never about the FEATURE.
Never put a guard in front of a probe.
```

Codified in [`skills/spike/SKILL.md`](../../../skills/spike/SKILL.md).

## 5. Open leads

- **Route the mint to a project page** and re-measure `gflow image` on migrated (#692).
  Free to try; the composer question above decides whether it is enough.
- **`scene`, `movie`, `extend`** were never re-checked after this; their exit-36 status
  rests on the same class of assumption.
- The **labs** path is untested for every anchor changed in #703 — no unmoved account
  exists here.
