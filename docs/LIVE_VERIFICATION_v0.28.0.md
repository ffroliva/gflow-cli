# Live Verification — v0.28.0

Release date: 2026-07-08. Headline: **Agent instructions (`-i` / `--instruction`) now actually steer agentic image generation** (PR #263).

Verification run: 2026-07-08, live Flow against profile `ffroliva` (agentic cohort), Windows, `.venv` build of the release tree. **Credit-spending:** this release changes the live agentic generation path, so it was exercised end-to-end against real Flow (image generation).

## Scope

| Change | Surface | Verdict |
|---|---|---|
| Conversational `_compose_directive` (engages the agent's reasoning path) | `api/transports/drivers/agentic.py` | ✅ Live: enabled card applied |
| Reconcile sets brief master switch `project_brief.enabled=true` + `text/plain` content-type + status-checked | `api/transports/drivers/agentic.py` | ✅ Live: 200, no `patch_failed` |
| `AgentInstruction.title` / `resolved_title()` + shared `build_agent_brief_cards()` | `api/image.py` | ✅ Unit tests |
| `patch_agent_info` returns echoed `projectBrief` | `api/client.py` | ✅ Unit tests |
| Warn when `-i` used on a classic-cohort session | `api/transports/ui_automation.py` | ✅ Unit tests (both cohorts) |

## Live scenario

`tests/e2e/test_live_agentic_instructions.py` (`-m e2e_image`, `GFLOW_CLI_FORCE_AGENT_UI=1`)
drove the REAL transport: create project → force agent → reconcile PATCH → conversational
submit → DOM scrape. Prompt was **style-neutral** ("a cat sitting on a wooden chair next to
a window") with a single **enabled** instruction card: "Every image MUST be rendered as a
flat 2D children's crayon drawing on textured paper…". Any crayon styling can therefore only
have come from the card.

## Evidence ledger (5-layer)

1. **File count:** exactly one image file written per generation (`<media_id>.jpg`).
2. **Magic bytes:** the downloaded file passed the container sniff (`_image_kind` → `jpeg`).
3. **Shape:** a valid landscape (16:9) render; agentic DOM scrape reports `(0,0)` sentinel
   dimensions by design (wire dims are Web-Worker-delegated), so shape is confirmed visually.
4. **Structlog invariants:** `ui_driver.bound mode=agentic` fired (not classic);
   `agentic_driver.reconcile_instructions.patch` fired; **no**
   `agentic_driver.reconcile_instructions.patch_failed` (content-type/auth regression guard).
5. **User-confirmable artifact:** the generated image is an **unmistakable children's crayon
   drawing** (waxy strokes, textured paper, primary palette) — visually confirmed to match the
   enabled card, from a prompt containing zero style words. A control generation with the crayon
   instruction in the *prompt* (not the card) confirmed the model can render crayon, isolating
   the card (not a model limitation) as the applied factor.

### Negative control (mechanism proof)

- Imperative `"Generate one image: …"` directive + same enabled card → **photorealistic**
  (brief bypassed). Conversational phrasing → **crayon** (brief applied). This isolated the
  phrasing fix. A follow-up run isolated the second fix: with cards synced but
  `project_brief.enabled` unset → photorealistic; with the master switch on → crayon.

## Gates

Local: `ruff check` 0 errors · `ruff format` clean · `pyright src` 0 errors · unit + BDD suites
green. CI on PR #263: tests (3.11/3.12/3.13) + **SonarCloud quality gate** all green
(new-code coverage ≥ 80%).

## Known follow-ups (not in this release)

- `gflow instructions` persistent CRUD subcommand.
- `movie.toml` per-scene instruction wiring.
- `GFLOW_CLI_FORCE_AGENT_UI` binding is ~50/50 flaky on fresh projects (may bind classic).
- H4: reference images (`imageReferenceMediaIds`) on cards — untested.
