# 2026-10-08 — #958: Nano Banana 2.1 replaced Nano Banana 2 on flow.google.com

**Question.** #958 reports that every `nano2` image run on flow.google.com aborts with
exit 7 (`the image submit body does not carry requested model NARWHAL`), because the
menu now offers "Nano Banana 2.1" and the `ogiZ0b` submit carries `BELUGA`. Accepting
`BELUGA` for `nano2` is safe only if 2.1 **replaced** 2. If the two were offered side by
side, the "already selected" shortcut in `_select_image_model` would let a `nano2`
request run 2.1 without anyone noticing. So: is 2 still offered beside 2.1?

**Instrument.** `scripts/dev/spike_nano_banana_21_wire.py`. It reads the image-model
menu once **while it is open**. Re-clicking the trigger closes the panel, so it never
does. It also counts model tokens in the project's `HTrJv` catalogue reply. The default
run costs $0. `--submit` adds one nano2 image (daily quota, 0 credits). Output goes to
`scripts/dev/_spike_out/`, which is gitignored. Only token counts are kept, never bodies.

**Observed.**

| Profile (served flow.google.com) | Menu, read while open | Current chip | `HTrJv` tokens |
|---|---|---|---|
| denon82 (2 runs) | Nano Banana Pro · Nano Banana 2 Lite · Nano Banana 2.1 | Nano Banana 2.1 | BELUGA, GEM_PIX_2, HARBOR_SEAL; no NARWHAL |
| ffroliva | same | Nano Banana 2.1 | same |
| test_acc | not measured: Flow redirected to `/about` | — | — |

- **2.1 replaced 2; they were not offered side by side** on either account.
- Flow's catalogue on that host names `BELUGA` and no longer names `NARWHAL`.
- **The submit body was not captured directly.** The one `--submit` run (denon82) failed
  inside the spike: `UiSelectorDriftError … settings pane opened but rendered no option
  groups`. That is the spike's own driving after its menu read, not gflow's generation
  path. So `BELUGA` in the `ogiZ0b` body rests on three things:
  - #958's report.
  - The catalogue no longer listing `NARWHAL`.
  - The e2e `test_e2e_t2i_runs_on_a_moved_account`. On the same profile and project, it
    fails on `develop` (body lacks `NARWHAL`) and passes once `BELUGA` is accepted.

**Update, same day (release v0.83.1).** A hook on `_image_body_problem` during a live nano2
i2i read both submit bodies: `BELUGA` present, `NARWHAL` absent. The body token is now
measured, not inferred ([ledger](../../LIVE_VERIFICATION_v0.83.1.md)).

**Verdict.**
- `nano2` → accept `("NARWHAL", "BELUGA")` in `IMAGE_MODEL_WIRE_TOKENS`. Keep `NARWHAL`:
  only two accounts were measured, and other cohorts may still be served 2.
- The live menu matches the existing `ModelMenuMatcher("Nano Banana 2", excludes=("Lite",))`
  exactly once.

**When this stops being true.** Re-run the spike if Flow ever lists "Nano Banana 2" and
"2.1" together. A fresh menu pick then fails loudly (two hits, exit 11). The "already
selected" chip shortcut does not fail, and the guard would accept either token.

**Not observed.** The labs host. None of our profiles can reach it, so nothing here says
whether labs offers 2.1. The labs request is built by gflow from `NARWHAL`.

**Supersedes.**
- `2026-09-11-nano2-lite-capability.md`: its catalogue listing `NARWHAL`.
- `2026-09-08-migrated-image-submit-wire.md`: the NARWHAL menu row it left unmeasured.
