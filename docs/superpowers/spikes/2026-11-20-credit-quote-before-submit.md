# Per-generation credit quote before submit

## Question

Where and how does Flow quote the credit price of a video generation before submission on
`flow.google.com`? Does the quote vary with model, duration, and output count, remain the
same in English and Brazilian Portuguese, or appear in a settings-related wire reply?
Does the image composer quote zero credits or show no quote?

## Method

The read-only survey opens an existing project, opens the settings pane, and uses the
existing settings controls to inspect available video models, durations, and counts. It
does not type a prompt, submit, press Enter in a prompt editor, or approve an agent gate.
The authenticated profile is checked before the browser starts; its credit balance is
read before and after. A Playwright route guard aborts all listed billed RPCs when their
IDs appear in either the `rpcids` URL parameter or decoded request body, including
body-only `nprQif`, and aborts generation POSTs to `aisandbox-pa.googleapis.com`.

The UI language is requested through `?hl=` and `Accept-Language`. Wire observations
retain decoded response shape and integer values with positional paths only; response
bodies, tokens, cookies, prompts, and account identifiers are not retained. Evidence is
written under the gitignored `scripts/dev/_spike_out/spike_1355_<timestamp>/` directory.

DOM quote discovery reads visible text only inside the visible `.cdk-overlay-pane` and
records the containing element's tag, class/id path, and text when a line contains a
number and a credit-unit word. It also inventories descendants with credit/cost class or
test-id attributes and ARIA labels. These are candidate anchors, not an assertion that
any is the eventual production selector. A selector miss is recorded as unmeasured.

## Pre-registered readings

| Reading | Interpretation | Observed |
|---|---|---|
| A number is paired with a credit unit in a visible settings-pane descendant. | A pre-submit DOM quote was observed; record structural path and value. | pending run |
| A selection has no quote match or its control cannot be selected. | Unmeasured for that selection; a miss does not establish absence. | pending run |
| Quote values differ by model, duration, or count. | Flow's observed quote varies across that axis; record the full available matrix. | pending run |
| Matched English and pt-BR selections have equal quotes, and both locale overrides are observed. | Locale invariance is supported for those selections only. | pending run |
| Matched quotes differ across English and pt-BR, or locale was not applied. | Difference is observed, or locale comparison remains unmeasured, respectively. | pending run |
| `HTrJv` or a settings-change reply contains an integer at a path matching the DOM quote. | Candidate wire quote; the RPC and positional path are reported. | pending run |
| No captured integer matches the quote. | No matching wire integer was observed in the captured replies. | pending run |
| Image settings show a credit quote of zero, a nonzero quote, or no quote. | Report the observed image composer state; no quote is not equivalent to zero. | pending run |
| Credit balances before and after are equal. | The run's zero-credit safety check passed. | pending run |
| Credit balances differ or an unguarded billed request is observed. | Safety failure; stop and report the captured balance/request evidence. | pending run |

## Observed matrix

Pending live run. The script records `(model, duration, count) -> quote` for the offered
combinations of `veo-lite`, `veo-fast`, `veo-quality`, `omni-flash`, and `omni-1.2`;
4s, 6s, and 8s; and x1 through x4. Unsupported or unavailable controls remain explicitly
unmeasured. The coordinator should fill the matrix from `summary.json` after the run.

## Verdict per reading

Pending live run for DOM quote presence, model/duration/count dependence, locale equality,
wire carrier and positional path, image quote state, and balance equality.

## Implications for #1355

Pending live run. A measured structural DOM anchor and/or matching wire path would identify
where a per-generation cap can read Flow's declared price before submit. Surfaces with no
observed quote must be named as such only for the specific measured state; unavailable
controls and selector misses remain unmeasured. Image generation is reported separately
from billed video generation.
