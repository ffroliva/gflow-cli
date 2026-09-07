---
name: ligature-carrier-differs-by-host
description: Flow's two frontends render the same Material Symbols ligature under different carrier tags — labs `<i class="google-symbols">`, migrated `<mat-icon>`. A cascade anchored on one carrier returns a flat zero on the other host while the control is visible, and degrades silently.
---

Flow ships two frontends over one backend. They render the **same** Material Symbols
ligature under **different carrier tags**:

| host | framework | carrier |
|---|---|---|
| `labs.google` | React / Next.js | `<i class="google-symbols">` |
| `flow.google.com` (migrated) | Angular | `<mat-icon class="… google-symbols …">` |

`mat-icon` **also** carries the `google-symbols` class, so the class was never the
discriminator — the **tag** is. `button:has(i.google-symbols:text-is('x'))` therefore
matches zero elements on the migrated host while the control sits there fully visible,
and `button:has(.google-symbols:text-is('x'))` would have matched both all along.

**Why:** #703 swept three constants for exactly this (`add_2`, `arrow_drop_down`,
`accessibility_new`) and missed `PROMPT_FORMAT_SELECTORS`. That miss made
`character create --format-prompt` a **silent no-op** for an unknown period — the flag
logged `ui_automation.format_button_not_found`, submitted the prompt as typed and exited
**0**, having spent image quota on a prompt-engineering step that never ran (#727). The
failure mode is the dangerous one: not an error, a degrade behind a green exit code.

**How to apply:**

- Touching **any** selector constant: sweep both carriers, not just the one your account's
  host renders. `git grep "i\.google-symbols" src/gflow_cli` returned **55** single-carrier
  literals after #727 — across `ui_automation.py`, `ui_automation_video.py`,
  `mode_control.py`, `diagnostics.py` and `drivers/`. Two consecutive PRs have now fixed
  one of these by hand, which makes it a **registry/sweep** problem, not a selector problem;
  `src/gflow_cli/flow_selectors/registry.py` (driven nightly by `selector-probe.yml`) is
  where a real fix belongs.
- **Prefer a custom element over either carrier.** `<flow-format-prompt-button>`,
  `<flow-slot-chip-button>`, `<flow-generate-icon-button>` are component boundaries, not
  layout accidents — they survive a carrier change AND a translation. Pin uniqueness with a
  captured-DOM fixture, never with a comment ([[flow-locale-leak-icon-ligatures]]).
- A cascade that misses is evidence about the **cascade**. Probe the live DOM before
  concluding the control is gone — [[flow-locale-leak-icon-ligatures]] and
  `skills/spike/SKILL.md`. The carrier split looks exactly like a removed feature, and has
  twice been mistaken for one.

Related: [[flow-locale-leak-icon-ligatures]], [[migrated-host-driver-wire-lessons]],
[[ui-selector-drift-error-exit-23]].
