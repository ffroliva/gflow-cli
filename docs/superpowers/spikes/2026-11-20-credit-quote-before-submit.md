# Per-generation credit quote before submit (#1355)

**Date:** 2026-11-20 · **Cost:** $0 (credits 978 before → 978 after; 0 billed requests attempted) · **Host served:** `flow.google.com` · **Locales:** `en`, `pt-BR`
**Script:** [`scripts/dev/spike_1355_credit_quote.py`](../../../scripts/dev/spike_1355_credit_quote.py)

## Question

Where and how does Flow quote the credit price of a video generation before submission on
`flow.google.com`? Does the quote vary with model, duration, and output count, remain the
same in English and Brazilian Portuguese, or appear in a settings-related wire reply?
Does the image composer quote zero credits or show no quote?

## Method

The read-only survey opens an existing project on an authenticated profile served
`flow.google.com`, opens the settings pane, and uses the existing settings controls to
inspect available video models, durations, and counts. It does not type a prompt, submit,
press Enter in a prompt editor, or approve an agent gate. The authenticated profile is
checked before the browser starts; its credit balance is read before and after via
`gflow credits user --json`. A Playwright route guard aborts all listed billed RPCs
(`YhhmEf`, `eb1hJf`, `MZZa6b`, `nprQif`, `fZytfe`, `ogiZ0b`) when their IDs appear in
either the `rpcids` URL parameter or decoded request body, including body-only `nprQif`,
and aborts generation POSTs to `aisandbox-pa.googleapis.com`.

The UI language is requested through `?hl=` and `Accept-Language` (`en` and `pt-BR`). Wire
observations retain decoded response shape and integer values with positional paths only;
response bodies, tokens, cookies, prompts, and account identifiers are not retained.
Evidence is written under the gitignored `scripts/dev/_spike_out/spike_1355_<timestamp>/`
directory.

DOM quote discovery reads visible text only inside the visible `.cdk-overlay-pane` and
records the containing element's tag, class/id path, and text when a line contains a
number and a credit-unit word (`credit`, `crédito`, `créditos`). It also inventories
descendants with credit/cost class or test-id attributes and ARIA labels. A control that
cannot be selected or a selector miss is recorded as `unmeasured`.

## Pre-registered readings

| Reading | Interpretation | Observed |
|---|---|---|
| A number is paired with a credit unit in a visible settings-pane descendant. | A pre-submit DOM quote was observed; record structural path and value. | **Yes (24/24 selectable video combinations + 1/1 image in both `en` and `pt-BR`).** Anchor: `.cdk-overlay-pane flow-prompt-box-settings div.settings-content > flow-credit-cost-label.settings-credit-cost > span.credit-cost-label > span` (and child `a.credit-cost-link`). |
| A selection has no quote match or its control cannot be selected. | Unmeasured for that selection; a miss does not establish absence. | **Yes (36/60 video combinations per locale).** `veo-lite`, `veo-fast`, and `veo-quality` with explicit `4s`/`6s`/`8s` raised `ConfigurationError` (exit 11) in `apply_video_settings` before the quote read; recorded as `unmeasured`. |
| Quote values differ by model, duration, or count. | Flow's observed quote varies across that axis; record the full available matrix. | **Yes across all three axes.** Quotes scale linearly with count (`x1..x4`), increase with duration (`4s`/`6s`/`8s`), and differ between `omni-flash` and `omni-1.2` at `6s` and `8s`. |
| Matched English and pt-BR selections have equal quotes, and both locale overrides are observed. | Locale invariance is supported for those selections only. | **Yes (25/25 matched selections: 24 video + 1 image).** Both locale overrides took effect in the settings pane (`Generating will use N credits` in `en`, `A geração vai usar N créditos` in `pt-BR`) with identical integer values. |
| Matched quotes differ across English and pt-BR, or locale was not applied. | Difference is observed, or locale comparison remains unmeasured, respectively. | **No.** Every matched selection produced the same integer in both locales. |
| `HTrJv` or a settings-change reply contains an integer at a path matching the DOM quote. | Candidate wire quote; the RPC and positional path are reported. | **Not established.** `HTrJv` on project load carries integers at `$[0][4][0][1][k][4][j][1][0][1]` (e.g., `12`, `18`, `24`, `30`, `60`, `9`), but whether those positional integers are credit prices or how they map to this profile's `4`/`5`/`6`/`8`-credit unit quotes is not established. No `batchexecute` price RPC fired on individual settings changes. |
| No captured integer matches the quote. | No matching wire integer was observed in the captured replies. | Settings toggles updated the DOM quote client-side without a per-change price RPC; `HTrJv` integer correlation is not established. |
| Image settings show a credit quote of zero, a nonzero quote, or no quote. | Report the observed image composer state; no quote is not equivalent to zero. | **Quoted zero (`0`).** In Image mode (Nano Banana 2.1), the same `flow-credit-cost-label` element renders `Generating will use 0 credits` (`en`) / `A geração vai usar 0 créditos` (`pt-BR`). |
| Credit balances before and after are equal. | The run's zero-credit safety check passed. | **Yes.** `credits_before = 978`, `credits_after = 978`, `credits_unchanged = true`, `aborted_billed = []` (zero billed requests attempted). |
| Credit balances differ or an unguarded billed request is observed. | Safety failure; stop and report the captured balance/request evidence. | **No.** Zero credits spent and zero billed requests attempted. |

## Observed matrix

Measured on 2026-11-20 across `en` and `pt-BR`. In every cell where a quote was observed,
the integer value was identical in `en` and `pt-BR`:

| Model | Duration | `x1` | `x2` | `x3` | `x4` | `en` text (`span.credit-cost-label > span`) | `pt-BR` text (`span.credit-cost-label > span`) |
|---|---|---|---|---|---|---|---|
| `omni-flash` (Omni 1.1 Flash) | `4s` | **4** | **8** | **12** | **16** | `Generating will use 4..16 credits` | `A geração vai usar 4..16 créditos` |
| `omni-flash` (Omni 1.1 Flash) | `6s` | **5** | **10** | **15** | **20** | `Generating will use 5..20 credits` | `A geração vai usar 5..20 créditos` |
| `omni-flash` (Omni 1.1 Flash) | `8s` | **6** | **12** | **18** | **24** | `Generating will use 6..24 credits` | `A geração vai usar 6..24 créditos` |
| `omni-1.2` (Omni 1.2 Flash) | `4s` | **4** | **8** | **12** | **16** | `Generating will use 4..16 credits` | `A geração vai usar 4..16 créditos` |
| `omni-1.2` (Omni 1.2 Flash) | `6s` | **6** | **12** | **18** | **24** | `Generating will use 6..24 credits` | `A geração vai usar 6..24 créditos` |
| `omni-1.2` (Omni 1.2 Flash) | `8s` | **8** | **16** | **24** | **32** | `Generating will use 8..32 credits` | `A geração vai usar 8..32 créditos` |
| `veo-lite` (Veo 3.1 - Lite) | `4s`, `6s`, `8s` | unmeasured | unmeasured | unmeasured | unmeasured | `ConfigurationError` (exit 11) | `ConfigurationError` (exit 11) |
| `veo-fast` (Veo 3.1 - Fast) | `4s`, `6s`, `8s` | unmeasured | unmeasured | unmeasured | unmeasured | `ConfigurationError` (exit 11) | `ConfigurationError` (exit 11) |
| `veo-quality` (Veo 3.1 - Quality) | `4s`, `6s`, `8s` | unmeasured | unmeasured | unmeasured | unmeasured | `ConfigurationError` (exit 11) | `ConfigurationError` (exit 11) |
| Image (`nano2`, Nano Banana 2.1) | — | **0** | unmeasured | unmeasured | unmeasured | `Generating will use 0 credits` | `A geração vai usar 0 créditos` |

### Structural DOM anchor

Inside the active settings overlay (`.cdk-overlay-pane`), Flow renders a dedicated custom
element for the generation cost quote:

```text
.cdk-overlay-pane
  > flow-prompt-box-settings.settings-content-overlay
  > div.settings-content
  > flow-credit-cost-label.settings-credit-cost
  > span.credit-cost-label
  > span
    > a.credit-cost-link
```

- **Outer span** (`flow-credit-cost-label.settings-credit-cost span.credit-cost-label > span`):
  renders `"Generating will use N credits"` in `en` and `"A geração vai usar N créditos"` in
  `pt-BR`.
- **Inner link** (`flow-credit-cost-label.settings-credit-cost a.credit-cost-link`):
  renders `"N credits"` in `en` and `"N créditos"` in `pt-BR`.
- Neither element carries a numeric data attribute; the attributes on
  `flow-credit-cost-label` are Angular encapsulation markers (`_ngcontent-*`, `_nghost-*`)
  and `class="settings-credit-cost"`.
- The custom element tag `flow-credit-cost-label.settings-credit-cost` (scoped inside the
  visible settings pane `flow-prompt-box-settings div.settings-content`) is a Tier 1
  structural anchor. Only the integer token `N` is parsed from its text, and that integer
  is identical across `en` and `pt-BR` while the surrounding prose is translated — meeting
  the project's Locale-Invariance Discipline bar alongside `migrated_composer._duration`
  (#963) and `migrated_upscale.menu_token_pattern` (#922).

## Verdict per reading

1. **DOM quote presence:** Confirmed. The composer settings pane displays the total credit
   cost for the current settings inside
   `.cdk-overlay-pane flow-prompt-box-settings div.settings-content > flow-credit-cost-label.settings-credit-cost > span.credit-cost-label > span`
   before the pane is closed and before any submit click.
2. **Model, duration, and count dependence:** Confirmed. The DOM quote already reflects
   the product of the selected model/duration unit price and the selected output count
   (`x1..x4`), updating in place as settings are toggled.
3. **Locale invariance (`en` vs `pt-BR`):** Confirmed for all 25 matched selections. Both
   `?hl=en` and `?hl=pt-BR` rendered translated settings labels (`Video`/`Length` vs
   `Vídeo`/`Duração`) while `flow-credit-cost-label` kept the exact same structure and
   integer values.
4. **Wire carrier (`HTrJv` and settings-change traffic):** Not established. `HTrJv`
   replies captured during project load carry integers at positional paths such as
   `$[0][4][0][1][k][4][j][1][0][1]` (values `12`, `18`, `24`, `30`, `60`, `9`), but
   whether those integers represent prices or how they map to the `4`/`5`/`6`/`8`-credit
   unit costs quoted in the DOM for this account is not established. Changing model,
   duration, or count in the settings pane does not trigger a price-quoting RPC; the pane
   computes or looks up the quote client-side.
5. **Image quote state:** Quoted zero (`0`). In Image mode with Nano Banana 2.1 (`x1`),
   `flow-credit-cost-label` explicitly renders `0` credits in both `en` and `pt-BR`.
6. **Zero-credit safety proof ($0):** Confirmed. The profile's credit balance read `978`
   before the run and `978` after (`credits_unchanged: true`), and `aborted_billed` was
   empty (`[]`) — zero billed requests were attempted.

## What is unmeasured and why

- **`veo-lite`, `veo-fast`, and `veo-quality` in the migrated settings pane:** Every step
  for these three models passed an explicit duration (`4`, `6`, `8`) into
  `SettingsPaneMixin.apply_video_settings`, which raised `ConfigurationError` (exit 11)
  before reaching `dom_quotes(page)` (either the model was not selectable or the cohort
  rendered no duration control for Veo 3.1 models). In addition, Flow retires `veo-fast`
  and `veo-quality` (alongside `omni-flash`) on 2026-12-05 (#1375). Passing `duration=None`
  for `veo-lite` was not part of this script's loop and remains unmeasured in this capture.
- **`omni-flash` 10s and `omni-1.2` 3s/5s/7s/9s–15s:** The script probed the shared
  `(4, 6, 8)` duration grid; other durations on the Omni slider/radios and `360p` vs
  `720p` resolution toggles were not stepped through in this run.
- **`HTrJv` integer semantics:** Positional integers at `$[0][4][0][1][k][4][j][1][0][1]`
  were observed on the wire, but their relationship to per-tier pricing is not established.
- **Clip-editor `video extend` on `flow.google.com` and the `labs.google` composer DOM:**
  Not probed by this script. Prior measurements (`docs/superpowers/spikes/2026-10-17-migrated-extend-ui-path.md`,
  `cli_video.py:2185`) found no price quote on the `flow.google.com` extend menu or prompt
  box, and `labs.google/fx/tools/flow` answers HTTP 308 to `flow.google.com` on available
  test profiles.

## Implications for #1355

1. **Zero-extra-navigation enforcement on `flow.google.com` classic composer:**
   `SettingsPaneMixin.apply_video_settings` already opens `.cdk-overlay-pane` to bind mode,
   submode, model, aspect, duration, resolution, and count. Reading
   `flow-credit-cost-label.settings-credit-cost` inside that open pane right after count is
   selected — and before `_close_pane` and `send_prompt` — adds zero extra overlay opens
   and runs strictly inside `with nothing_submitted():` ($0 on refusal).
2. **Single integer parse, no local price table:** Because the DOM quote already multiplies
   by output count (`x1..x4`) and reflects the account's active model, duration, and plan
   tier, gflow does not need to maintain or guess from a static local price table or
   unestablished `HTrJv` wire positions.
3. **Fail-closed contract where a quote cannot be read:** When `--max-credits` /
   `GFLOW_CLI_MAX_CREDITS` / MCP `max_credits` is set and a route or surface provides no
   readable pre-submit quote (for example, `flow-credit-cost-label` is absent or carries no
   integer, `video extend` on `flow.google.com` where the extend UI shows no price, or the
   `labs.google` UI composer where no DOM quote anchor is measured), gflow must fail closed
   before submit with `charged: "no"`, never guessing a price.
4. **Per-route quote sources:**
   - **Migrated classic composer (`flow.google.com`):** DOM quote from
     `flow-credit-cost-label.settings-credit-cost` at the end of `apply_video_settings`
     before `_close_pane`.
   - **Agent-only composer (`flow.google.com`, #799):** Permission gate title (`LIVE_GATE`
     `p.title`, trailing integer parsed alongside `gate_quotes_more`), rejecting the gate
     via `REJECT_ROW` before approval when the quoted credits exceed `max_credits` (and
     requiring confirmation to be active when a cap is set).
   - **Labs direct-wire extend (`labs.google`):** `resolve_extend_model` unit cost from
     `projectInitialData` `creditMapping[serviceTier].cost` multiplied by `segments`,
     checked before scene creation, reCAPTCHA mint, or `POST`.
   - **Unquoted routes (`video extend` on `flow.google.com`, `labs.google` UI video
     composer, or a missing/unreadable quote anchor):** Fail closed before submit when
     `max_credits` is set.
