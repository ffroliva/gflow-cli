# Live verification — v0.83.1

> Evidence for this release, gathered on 2026-10-08 (PR #959, first found and fixed by
> @omid-io in #958). Each row is a run someone watched, not an inference. Where a check could
> not be run, the blocker is named.

## Environment

| | |
|---|---|
| Profile | `denon82` (real-browser Chrome strategy); `ffroliva` for the menu read |
| Host Flow served | `flow.google.com` (classic composer) |
| Transport | `ui_automation` → migrated composer |
| OS | Windows 11 |
| Veo credits spent | 0 (images use daily quota only) |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| `nano2` accepts Nano Banana 2.1's `BELUGA` submit token (#958, PR #959) | migrated t2i `ogiZ0b` submit guard | ✅ e2e `test_e2e_t2i_runs_on_a_moved_account`: **1 failed** on `develop` before the fix (exit 7, `WireFormatError … does not carry requested model NARWHAL`), **1 passed** on the branch and again on the release tree (27 s) | quota only |
| Same, CLI door | `gflow image t2i` (default model) | ✅ exit 0; event `migrated.image_model_already_selected` with model "Nano Banana 2.1" and requested `NARWHAL` | quota only |
| Same, MCP door | `gflow_generate_image` (default `model="nano2"`, `wait=true`) | ✅ `status: completed`, `file_count=1`, through the queue and worker (`worker t2i`) | quota only |
| Same, local-file i2i (`nano2`) | `gflow image i2i --ref <local jpg>` | ✅ exit 0; the result keeps the reference's scene and recolours the cube as prompted | quota only |
| The `ogiZ0b` body carries `BELUGA` | submit body, read by a hook on `_image_body_problem` during the i2i run | ✅ both submit-body reads: `BELUGA` present, `NARWHAL` absent | (same run) |
| 2.1 replaced 2 instead of appearing beside it | image model menu, read while open | ✅ denon82 and ffroliva: Pro · 2 Lite · 2.1, no "Nano Banana 2"; `HTrJv` lists `BELUGA`, no `NARWHAL` ([spike](superpowers/spikes/2026-10-08-nano-banana-21-replaced-2.md)) | $0 |

## 5-layer ledger (release tree, 2026-10-08)

| Layer | CLI `gflow image t2i` | MCP `gflow_generate_image` |
|---|---|---|
| File count | 1 | 1 (`file_count=1`) |
| Magic bytes | `ffd8ff` (JPEG) | `ffd8ff` (JPEG) |
| Dimensions | 768 × 1376 (CLI default aspect 9:16), 547,732 B | 1024 × 1024 (MCP default aspect 1:1) |
| structlog | `migrated.image_model_already_selected` (model "🍌 Nano Banana 2.1", requested `NARWHAL`); no `WireFormatError` | `migrated.image_settings_applied`, `migrated.prompt_typed`, `mcp.tool.task_completed` |
| User-confirmable artifact | the image shows a small red cube on a white table, in a fresh project gflow created (#864) | a blue glass sphere on a white table (prompt: "a small blue sphere on a white table"), same project |

## Not verified

- **Accounts served labs.** Blocker: no profile we hold can reach labs — every one is
  redirected to flow.google.com — so nothing here says what labs offers. The labs request
  is built from `NARWHAL` and is unchanged.

## Pre-tag gates

| Gate | Result |
|---|---|
| `/gflow:changelog` | `[Unreleased]` → `[0.83.1] — 2026-10-08`: Fixed (#958) |
| `/gflow:check` | ruff, format, pyright 0, hygiene, doc links, PII, mirror, council memory, release artifacts green; pytest 4913 passed, 24 skipped, coverage 93% |
| SonarCloud (PR #959 @ 72396282) | gate passed |
| `/gflow:doc-review` | council: Auditor 1 YELLOW (an old-version user could not find the fix from the exit-7 message) → CHANGELOG now quotes the message and names `gflow update` and a workaround, KNOWN_ISSUES says fixed in v0.83.1; ledger states exit 7 and why CLI and MCP dimensions differ. Auditor 2 GREEN (8 version sites, footer, mirrors, `<details>` balance). Auditor 3 GREEN (every claim matched to code). Deferred: `skills/gflow-cli/SKILL.md:284` "(Narwhal)" is still true and gets no 2.1 note. Reports local at `tmp/council/` |

## Post-tag evidence

| Evidence | Result |
|---|---|
| Signed tag | pending |
| Release workflow | pending |
| GitHub Release | pending |
| PyPI | pending |
| Clean install | pending |
| Release PR | pending |
