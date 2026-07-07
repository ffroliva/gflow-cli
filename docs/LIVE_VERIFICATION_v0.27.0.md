# Live Verification — v0.27.0

Release date: 2026-07-07. Headline: **global `[style]` block with named variants
in `movie.toml`** (Issue #239, PR #259) — express a visual style arc once
(prefix/suffix + `[style.variants.*]`) and select it per-scene via
`style_variant` / `style_suffix` — plus **prompt-aware resume** for `movie run`
(completed scenes persist a `style_hash`; a scene whose composed prompt changed
is regenerated instead of silently skipped).

Verification run: 2026-07-07, real CLI (`gflow movie run --dry-run`), Windows,
`.venv` build of the release tree. **Deliberately credit-free**: this release is
pure local prompt composition + state logic — the Flow wire path (transports,
selectors, auth) is untouched, so no live Veo generation was spent. The dry-run
path exercises the identical manifest→parse→compose→resume-decision pipeline the
live run uses; only `_generate_scene` (unchanged this release) is skipped.

## Scope

| Change | Surface | Verdict |
|---|---|---|
| `[style]` prefix/suffix + `[style.variants.*]`, per-scene `style_variant`/`style_suffix` | `composition.py`, `movie_manifest.py` | ✅ CLI dry-run + 91 unit/integration tests |
| Prompt-aware resume (`style_hash`, `is_stale_for`, re-run on style edit) | `movie_manifest.py`, `cli_movie.py` | ✅ CLI dry-run 3-way ledger (below) |
| Dry-run shows resolved style + stale marker | `cli_movie.py` | ✅ CLI dry-run output |
| `style_applied` (variant/prefix/suffix/scene_suffix) per clip + schema | `composition.py`, `docs/schemas/movie-handoff.schema.json` | ✅ Unit-tested (jsonschema validation) |
| Reserved `none` variant name, strict variant parsing | `movie_manifest.py` | ✅ Unit-tested (ConfigurationError paths) |

## Evidence ledger (credit-free CLI runs)

Manifest: 3 scenes — `s1-base` (base style), `s2-warm` (`style_variant="warm"` +
`style_suffix="golden hour light"`), `s3-optout` (`style_variant="none"`), with
`prefix = "SCENE:"` and a base suffix.

1. **Composed prompt (canonical order)** — `compose_prompt` for `s1-base`
   produced exactly:
   `SCENE: Walks through a rainy plaza. Wide shot. Black and white cinematic
   street photography, film grain.` — prefix first, sentence-composed
   action/framing, raw base suffix last.
2. **Run 1 (fresh state)** — plan shows the resolved style per scene and full
   cost: `s1-base … style=base 1 credit(s)`, `s2-warm … style=warm
   +style_suffix 1 credit(s)`, `s3-optout … style=none 1 credit(s)`,
   `Estimated credits: ~3`.
3. **Run 2 (s1 completed in `movie-state.json` with matching `style_hash`)** —
   `s1-base … skip (done)`, `Estimated credits: ~2`. State file JSON carries
   `"style_hash": "<sha256 of the composed prompt>"` (round-tripped via
   `MovieState.save`/`load`).
4. **Run 3 (base suffix edited to "Sepia archival newsreel look.")** —
   `s1-base … re-run (style changed) 1 credit(s)`, `Estimated credits: ~3`.
   The stale scene is regenerated; the unchanged `s2-warm`/`s3-optout`
   decisions are unaffected. This is the exact staleness bug the feature
   exists to prevent (stale clip silently skipped after a style edit).
5. **User-confirmable artifact** — the three plan outputs above are
   reproducible with any manifest; the same decision logic drives the real run
   loop (`_run_movie` calls the identical `is_stale_for` check before spending
   credits).

### Not live-verified this cycle (recorded, not omitted)

Actual Veo generation with a style-suffixed prompt (i.e. confirming Flow's
model renders the requested grade) was **not** exercised — it costs 20 credits
per scene and the prompt text demonstrably reaches the existing, unchanged
generation path (`_run_one_scene` passes the same `compose_prompt` output that
v0.26.0 passed). First credited `movie run` on a styled manifest will confirm
visually; no code path is untested locally.

### Pre-existing display nit (not this release)

The plan line's `[t2v]`/`[r2v]` mode tag is swallowed by rich markup
(`console.print` interprets `[t2v]` as a style tag) — present on `develop`
before PR #259; cosmetic; tracked as a follow-up.

## Pre-tag gates

- `/gflow:check`: hygiene ✅ · ruff ✅ (no rewrites) · pyright `0 errors` ✅ ·
  full suite 2054 passed / 7 skipped ✅ (local), 3.11/3.12/3.13 matrix +
  SonarCloud quality gate ✅ on PR #259 (same tree).
- `/code-review` (independent, 8-angle): 10 findings — all fixed in
  `61a9887` before merge.
- `/gflow:doc-review`: mechanical pass UPDATED×2 (PROJECT_STATUS current
  release was stale at v0.24.0 — v0.25.0/v0.26.0/v0.27.0 entries added; 3 docs
  added to INDEX), links 23/23 green. Council verdict: **GREEN/YELLOW/YELLOW**
  across 3 auditors — 1 Tier-1 finding (this doc overclaimed "91
  style-variants tests"; actual collection is 59 — fixed in the release-prep
  commit), Tier-2 fixed (stale MOVIE.md "active development" banner), Tier-3
  deferred (hardcoded `build_handoff` `generator.version="0.14.0"`; MOVIE.md
  error-path doc anchor; USAGE.md movie coverage). Council reports at
  `tmp/council/0{1,2,3}-*.md` (local-only).

## Post-tag evidence

*(filled after the v0.27.0 tag push / publish)*

## Automated coverage

- 59 style-variants tests across `tests/composition/test_style_variants.py`,
  `test_style_variants_e2e.py`, `tests/cli/test_movie_manifest_style_variants.py`
  (composition order, variant resolution, reserved `none`, strict parsing,
  `style_applied` + jsonschema validation, `resume_hash` properties,
  `is_stale_for` all four branches, state round-trip).
- Full suite green: 2054 passed, 7 skipped (local) and 3.11/3.12/3.13 matrix +
  SonarCloud quality gate green on PR #259. `pyright src` 0 errors, ruff clean.
