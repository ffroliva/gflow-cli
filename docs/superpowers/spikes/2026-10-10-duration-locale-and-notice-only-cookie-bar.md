# Spike — duration radios outside English, and the notice-only consent bar (#963)

**Date:** 2026-10-10 · **Cost:** $0 (settings pane only, nothing typed or submitted)
**Host served:** flow.google.com · **Profile:** a maintainer test profile, Omni 1.1 Flash
**Instrument:** [`scripts/dev/spike_963_duration_locale.py`](../../../scripts/dev/spike_963_duration_locale.py)
(UI language forced per pass with `?hl=` plus an `Accept-Language` header; `html_lang`
read back on every pass, so a pass whose override did not take is visible as such)

## Question

#963 reports that a Vietnamese pane labels the durations `4 giây … 10 giây`, so the
driver's `_exact("8s")` matches nothing and every `--duration` run stops at exit 11.
Before replacing the exact match with a number-token match, the locale rule in
AGENTS.md requires two answers from the live DOM:

1. Does any attribute carry the length, so no text match is needed at all?
2. Does a number-token matcher bind the right radio in a translated pane?

## Observations

| `hl` | `html_lang` | duration labels | `_duration("4s")` / `("8s")` | read-back |
|---|---|---|---|---|
| default | `pt` | `4s 6s 8s 10s` | `4s` / `8s` | checked |
| `vi` | `vi` | `4 giây 6 giây 8 giây 10 giây` | `4 giây` / `8 giây` | checked |
| `pt` | `pt` | `4s 6s 8s 10s` | `4s` / `8s` | checked |

- **No structural anchor.** Each radio carries `type`, an Angular instance id
  (`mat-button-toggle-13-button`) and a group name (`mat-button-toggle-group-3`). Both
  are construction counters, not a length, so they cannot be matched on.
- The reporter's labels are confirmed verbatim. Portuguese keeps the `s` suffix, which
  makes it a control rather than a second translated sample.
- Sibling labels sharing a leading digit exist in the same pane (`360pinfo`, `720p`),
  which is why the matcher refuses a `p`/`k` suffix.

**Consequence:** the number is a format token that survives translation where the unit
does not, and a miss still raises exit 11 rather than clicking a wrong radio. It meets
the same bar as `migrated_upscale.menu_token_pattern` (#922). Shipped as
`migrated_composer._duration`.

## Unplanned finding — a consent bar with no reject button

On the first `vi` pass the run stopped at exit 23 before reaching the settings pane:
`.settings-trigger-button did not accept a click — it is covered by
div.glue-cookie-notification-bar`. `_dismiss_cookie_bar` had logged
`migrated.cookie_bar_not_dismissed` with a click timeout on its reject selector.

The bar under `hl=vi` is the **notice-only** variant. It has one button, `OK, got it`,
class `glue-cookie-notification-bar__accept`, and no `__reject`. The driver only ever
looked for reject, so this variant could never be cleared, and a fresh user served it
would fail every generation at exit 23, before #963's duration error is even reachable.

Shipped: reject is still preferred wherever the bar offers it, and `__accept` is clicked
only when there is no reject button. A notice asks no consent question, so
acknowledging it answers nothing on the operator's behalf.

**Not established:** which accounts, regions or languages get the notice-only variant.
It was seen under a forced `hl=vi`; the reporter's own run got past it, so their
profile had either already acknowledged it or was served the other variant.
