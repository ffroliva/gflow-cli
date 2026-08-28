# Live verification — v0.62.0

Every claim below was **observed**. Claims that could not be observed are stated
as such in §6, not omitted.

## Environment

| | |
|---|---|
| Date | 2026-08-28 |
| Profile | `ci-probe` (one of the two accounts Flow moved to the **agentic** cohort on 2026-08-27) |
| Code under test | `develop` @ `fb8b346` (all five release commits merged; the version bump itself came after, so log lines read `cli_version 0.61.0`) |
| Veo credits spent | **0** — this release's user-facing changes are image-path and read-only surfaces |

## 1. #595 — `--ui-mode auto` requires the classic arm (the headline change)

The whole point of the fix is what happens with **no flags and no env vars**, which
is what a new user gets:

```bash
gflow --verbose image t2i "a single red cube on a white studio table, soft light" --profile ci-probe
```

The decisive pair of events:

```json
{"event": "ui_driver.ui_mode.attempt_exit_agent"}
{"mode": "classic", "ui_mode": "classic", "event": "ui_driver.bound"}
```

`ui_mode` is **`classic`** with nothing on the command line. The day before, the same
command on this same account logged `ui_mode=auto` → `mode=agentic` and failed.

Five-layer ledger for the generation that followed:

| Layer | Observed |
|---|---|
| File count | 1 file in `~/Downloads/gflow-cli/images/2026-08-28/` |
| Magic bytes | `ff d8 ff e0` + `JFIF` at offset 6 — a real JPEG, not an HTML error page |
| Size / dimensions | 418 079 bytes, **768x1376** (the requested portrait 9:16) |
| Structlog invariants | `mode_switch_trigger` matched → `image_mode_tab` matched → `image_mode_entered` → `image_model_selected NARWHAL` → `aspect_ratio_set 9:16` → `count_setter_completed 1` → `prompt_submitted` → `batch_response_captured status=200` |
| User-confirmable artifact | `f0f5ef51-6a45-4c0c-a146-103f18ce9fba_1.jpg`, seed 294067, exit **0** |

`mode_switch_trigger` and `image_mode_tab` both matching is independently the
evidence that closed **#183**, which reported those exact selectors as unfindable.

## 2. #591 — a NULL column no longer reads back as the string `"None"`

Run against the **real catalog** (not a fixture), 500 rows:

```bash
gflow data list images --limit 500 --json
```

| | count |
|---|---|
| rows returned | 500 |
| occurrences of the literal string `"None"` | **0** |
| occurrences of JSON `null` | 31 |

The catalog holds 119 image rows with a NULL in `model` / `aspect_ratio` /
`flow_project_id`, so the null path is genuinely exercised: before the fix those
rows emitted the four-character string `"None"`, indistinguishable from a real value.

## 3. #592 — the account-locale probe runs once per profile

From the same run's log:

```json
{"locale": null, "settle_skipped": true, "event": "client.account_locale_cached"}
```

Exactly **one** `client.account_locale_cached` event for the whole command, and both
navigation settles report `settle_skipped: true` — the per-command ~4 s probe is gone.

## 4. `gflow project list` / `project show` (the release's new feature)

Both are read-only and credit-free. `project list --limit 3` returned the project
created by §1 with a correct rollup (`IMG 1`, `VID 0`), and `project show` on that id
returned title, profile, source `generated`, creation timestamp, and a working Flow URL:

```
Project ID: fdb68253-21a5-4d99-a5f5-091aa2a1de17
Title:      a-single-red-cube-on-a-white-studio-tabl
Profile:    ci-probe
Source:     generated
URL:        https://labs.google/fx/tools/flow/project/fdb68253-21a5-4d99-a5f5-091aa2a1de17
```

Cross-checked against §1: the id, the profile and the image count all agree with the
generation that produced them.

## 5. #593 — the announcement-modal fix

Verified live in the previous cycle (2026-08-27, recorded on PR #594): the ack POST
`videoFx.setLastAcknowledgedChangeLogId` → 200 fired at **+23 s**, well after the
~3 s navigation gate, which is the attribution proving the probe-level guard — not the
boundary dismissal — cleared it. In **this** release's runs the overlay path is
silent (`0` overlay events in the §1 log), which is the correct outcome: all three
accounts have already acknowledged the current announcement.

## 6. NOT verified this cycle — stated, not omitted

- **The exit-28 abort on a genuinely pinned agentic account (#595).** `ci-probe`
  rendered classic on this load. The cohort is server-assigned and flaps, so a pin
  cannot be provoked on demand. The abort path is the same `get_ui_driver` code #299
  shipped for video and is covered offline, but it has not been observed live for
  images.
- **The #597 batch inter-prompt guard.** Reproducing it needs an announcement modal
  to mount *during* a multi-prompt batch generation. All three accounts have acked
  the current announcement, so no modal can be raised at all until Google ships the
  next one. Covered offline against the captured markup
  (`tests/fixtures/changelog_modal_page.html`, real headless Chromium) plus mocks.
- **A fresh announcement modal end-to-end.** Same reason: nothing to dismiss until
  Google ships the next changelog.
