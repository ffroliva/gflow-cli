# The character-editor Format button never left — its carrier changed

**Date:** 2026-09-07 · **Issue:** [#727](https://github.com/ffroliva/gflow-cli/issues/727)
· **Account:** `denon82` (migrated `flow.google.com`, funded, idle machine) ·
**Cost:** `$0` — navigation, one free tRPC `flow.createEntity`, DOM reads and typing.
Nothing submitted, nothing generated, no credit and no image quota spent.

**Script:** `scripts/dev/spike_character_prompt_format.py` (rewritten for this run) ·
**Capture:** `scripts/dev/_spike_out/char_format_anchor_denon82_20260907_141440.json`
(gitignored)

---

## 1. The question, and the control that makes the answer readable

`gflow character create --format-prompt` had degraded to a silent no-op:
`ui_automation.format_button_not_found` on every run, exit 0, prompt submitted as
typed. All three entries of `PROMPT_FORMAT_SELECTORS` missed.

A cascade returning zero is evidence about the cascade. To make a zero mean anything,
the probe hard-errors unless the editor-ready anchor resolves first:

```
control_check  count=1  selector='div[role="textbox"][data-slate-editor="true"],
                                  div.ProseMirror[contenteditable="true"]'
```

One prompt box. The probe was standing in the editor. Every count below is therefore a
measurement, not a failure to arrive.

## 2. What was measured

Three shipped selectors, both before and after typing (the button ships `disabled` on
an empty box, so the empty read is the control for the enabled one):

| selector | empty | typed |
|---|---|---|
| `button:has(i.google-symbols:text-is('personal_recommendations'))` | **0** | **0** |
| `button:has(i:text-is('personal_recommendations'))` | **0** | **0** |
| `button:has(span:text-is('Format'))` | **0** | **0** |
| `button:has-text("Format")` *(locator only, never a candidate)* | 1, `enabled=False` | 1, `enabled=True` |

The button was visible the entire time. Its actual DOM:

```html
<flow-format-prompt-button>
  <button flow-button matbutton class="… format-chip-button …" aria-label="Formatar">
    <mat-icon class="mat-icon notranslate flow-icon-s google-symbols …">
      personal_recommendations
    </mat-icon>
    <span>Formatar</span>
  </button>
</flow-format-prompt-button>
```

## 3. Two independent misses, either one fatal

**a. The carrier tag.** The migrated Angular frontend renders the ligature in
`<mat-icon>`, not `<i>`. The ligature itself is unchanged — `personal_recommendations`,
still unique document-wide (1 of 17 ligatures in the editor). `mat-icon` even carries
the `google-symbols` class, so the *class* was never the problem; the `i` tag was.

This is the same carrier split fixed for `add_2`, `arrow_drop_down` and
`accessibility_new` in #703. `PROMPT_FORMAT_SELECTORS` was simply not swept with them —
a retraction that reached the places someone remembered and missed the one nobody
looked at.

**b. The localised label.** The button now *has* an `aria-label`, which it did not on
2026-07-27 — but Flow localises it: `aria-label="Formatar"`, `<span>Formatar</span>`, on
an account whose Flow locale is `pt` even though the run passed `--locale en-US`
(account locale wins). The EN text fallback could never have matched this profile.

## 4. The anchor that replaces them

`<flow-format-prompt-button>` — a **custom element**, i.e. a component boundary rather
than a layout accident, and unique: one of six buttons in the composer subtree.

| composer button | host element | aria-label (pt) |
|---|---|---|
| clear prompt | `div` | `Apagar comando` |
| add elements | `div` | `Adicionar elementos à caixa de comando` |
| **format** | **`flow-format-prompt-button`** | `Formatar` |
| model family | `div` | `Selecionar família de modelos` |
| settings | `div` | `Gatilho de configurações` |
| submit | `flow-generate-icon-button` | `Iniciar geração` |

Shipped cascade:

```python
PROMPT_FORMAT_SELECTORS = (
    "flow-format-prompt-button button",                                  # migrated: component boundary
    "button:has(mat-icon:text-is('personal_recommendations'))",          # migrated: ligature
    "button:has(i.google-symbols:text-is('personal_recommendations'))",  # labs
    "button:has(i:text-is('personal_recommendations'))",                 # labs
)
```

`button:has(span:text-is('Format'))` is **deleted**, not translated. It is banned as an
anchor by the locale-invariance rule in AGENTS.md and it demonstrably matches nothing on
a non-EN account.

## 5. Also confirmed, not assumed

The disabled-until-typed behaviour from 2026-07-27 still holds on the migrated host:
`enabled=False` on an empty box, `enabled=True` after `insert_text`. `_send_prompt`
types before calling `format_character_prompt`, so the enabled check is correct as
written and needed no change.

## 6. What this did NOT measure

- **The labs frontend.** No unmoved account exists here, so the two `<i>` entries are
  carried forward on the 2026-07-27 capture alone. They were not re-verified today and
  are not asserted to still work.
- **The content of Flow's rewrite.** The probe never submits. That the button is found,
  enabled and clicked is what the e2e test asserts; whether Flow's reshaped prompt is
  *better* stays a human read.
- **The network.** No request/response capture was taken, so nothing here establishes
  that clicking Format reaches a backend — only that the button is found, enabled and
  clickable. A click is not proof of a downstream effect
  ([[playwright-click-no-downstream-event-signature]]); the e2e's
  `ui_automation.prompt_formatted` event is the click, not the rewrite.
- **Occlusion of the Format button.** The probe's `elementFromPoint` read was pointed at
  the *prompt box*, not the button — it answered a question nobody asked, and the check
  has been removed rather than left to be misread. The live e2e click landing is the
  stronger evidence anyway.
- **Every other selector constant.** This spike swept one, and many more remain
  single-carrier — the #703 sweep demonstrably missed at least one, and this is the
  second consecutive PR to fix one by hand, which makes it a registry/sweep problem
  rather than a selector problem. Current count and file list live in
  [[ligature-carrier-differs-by-host]] and #730, not here — it is a number that rots.

## 7. The rule this re-earns

```
A SELECTOR THAT DOES NOT MATCH IS EVIDENCE ABOUT THE SELECTOR.
```

Nothing here was absent. The feature, the button, the ligature and the backend were all
exactly where they were in July. One tag name changed, one label got translated, and a
paid-for prompt-engineering step quietly stopped happening behind a green exit code for
an unknown number of runs — because `e2e_character` is opt-in and the nightly canary
runs `e2e_auth` only.

See also: [2026-09-06 — migrated character surface](2026-09-06-migrated-character-surface-and-recaptcha.md)
(the carrier split), [`skills/spike/SKILL.md`](../../../skills/spike/SKILL.md).
