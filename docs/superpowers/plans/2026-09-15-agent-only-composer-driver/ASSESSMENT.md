# Assessment of #799: CONFIRMED-BUG (feature gap) — confidence 9/10

**Restated claim:** on an account Flow serves the agent-only composer (flow.google.com, no
`agent-mode-chip`, no classic composer), `gflow video` / `gflow image` cannot generate.

## Findings

- Reproduced 2026-09-15 on a second account (Google AI Pro, locale `en`, Windows 10):
  `gflow image t2i` on 0.75.0 exits 25 `FlowAgentUiError`, `agent-mode-chip` count 0.
  The reporter's account is `ru` — so the cohort is not a locale artefact.
- The v0.74.0 mitigation (#804) is what fires:
  `src/gflow_cli/api/transports/migrated_composer.py:751` `_is_agent_only_composer` →
  `:753` raises `FlowAgentUiError(retryable=False)`. Correct as a mitigation; there is no
  driver behind it.
- Only two callers enter the migrated composer: `run_video` (`migrated_composer.py:2414`)
  and `run_images` (`:2498`), reached from `ui_automation_video.py:3994` and
  `ui_automation.py:3140`. **Both surfaces are affected — CLI and MCP** (direct and queued
  worker paths all end in these two functions), so a fix there fixes both.
- The reporter's framing is right, with one correction from measurement: aspect and count
  are Agent-settings *defaults*, but a natural-language **directive overrides them per
  request** (9:16 beat a 16:9 default for image and video — spike 2026-09-15). So
  `--aspect` has somewhere to go after all.
- The classic migrated wire does **not** carry over: the agent composer never fires
  `ogiZ0b`, and `as29s` records decode uuids that are not this generation's
  (spike § "The wire is not the classic driver's wire"). Completion must be observed in
  the DOM.
- Duplicates / in-flight: none. Open PRs #793, #787, #781 touch migrated auth, models and
  image submit gates, not this composer.
- KNOWN_ISSUES § "Some accounts get an agent-only composer" names the blocker as "no
  account in the cohort" — **now removed**; this account is in it.

## Root cause

Not a defect in existing code: a surface with no driver. Evidence for the drive path
(editor, submit/stop swap, credit gate, output tiles, signed CDN URLs):
[`docs/superpowers/spikes/2026-09-15-agent-only-composer-drive-surface.md`](../../spikes/2026-09-15-agent-only-composer-drive-surface.md).

## e2e gate

Headed-Flow-browser required. **Verifiable in this environment** — a cohort profile is
available; image runs cost daily quota only, video ~7 credits per 4 s clip.

## Next

Phase 2 `/gflow:predict` → [PREDICT.md](PREDICT.md).
