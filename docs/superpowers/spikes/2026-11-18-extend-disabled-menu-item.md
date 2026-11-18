# Spike: the disabled Extend menu item on flow.google.com (#1334)

**Question.** `gflow video extend` timed out after 30 s on a raw `Locator.click` on one
account and passed on another. Why is the Extend menu item disabled, what does the page say
about it, and does the submit path's credits check (`button.prompt-warning-button`) see it?

**Harness.** `scripts/dev/spike_extend_disabled_menu.py` (in-process, $0). It opens the clip
editor (`/project/<pid>/edit/<media>`), clicks the timeline add-clip button, and records
every menu item's state, the controls beside it, the tooltips after hovering Extend, and the
credits warning, without clicking any menu item. Every generation and scene rpc is aborted
at the context route.

## Observations (2026-11-18, same code, about 2 minutes apart)

| | Account A | Account B |
|---|---|---|
| Balance (`gflow credits user --json`) | 12 | 562 |
| Account locale | pt | en |
| Source clip | Omni 1.2 Flash, 9:16, 360p | a finished clip |
| Extend item (`keyboard_double_arrow_right`) | `disabled="true"`, `mat-mdc-menu-item-disabled`, `tabindex=-1`; Playwright `is_disabled()` true | enabled |
| Control beside the item | `flow-menu-item-actions > a[target=_blank]`, an upgrade link to `one.google.com/ai` (Google AI plans) | none |
| Add-clip item | enabled | enabled |
| `aria-label` / `title` / tooltip on Extend | none | none |
| `button.prompt-warning-button` (credits warning) | absent, before and with the menu open | absent |

## Readings

- **C1 (state tracks the account):** confirmed. Only the Extend item differs between the
  two accounts; the add-clip item is enabled on both.
- **C2 (the page names the cause):** no text names credits. The only stated remedy is the
  plan-upgrade link. The page does not say whether the plan or the balance disables the
  item; one measured Omni extend cost 12 credits (#1184), equal to account A's balance.
- **C3 (credits warning visible):** no. `_raise_if_out_of_credits` alone would stay silent
  here, so the check cannot be the only guard.

## Consequence for the driver

The item is visible, so `_anchor` (which waits for `visible`) returns it, and the click then
waits for an enabled state that never comes: `migrated_extend.run_extend` clicked
`EXTEND_MENU_ITEM` without reading `disabled`. The fix reads the state with the existing
`is_menu_item_disabled` (the same check the upscale menus use for their plan-gated items),
allows it up to 3 s to become enabled (both accounts were read only after the page had
settled), closes the menu, runs the credits check (exit 37 if Flow shows the warning; not
observed on either account), and otherwise raises `ExtendUnavailableError` (exit 35,
`charged: no`) naming the disabled item and both possible remedies. Whether a higher balance on account A's plan enables the item is not measured.
