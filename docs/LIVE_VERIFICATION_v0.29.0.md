# Live Verification — v0.29.0

Release date: 2026-07-09. Headline: **persistent `gflow instructions` CRUD + movie-manifest
instructions + `gflow_instructions_*` MCP parity tools** (PRs #269, #270, #271).

Verification run: 2026-07-09, live Flow against profile `ffroliva` (agentic cohort), Windows,
`.venv` build of the release tree. **Credit-free:** brief CRUD is a PATCH (no generation);
the single image generated to mint a fresh project is Imagen (no Veo credits).

## Scope

| Change | Surface | Verdict |
|---|---|---|
| `instructions add` (text card + `--ref <image-UUID>` card) | `cli_instructions.py` | ✅ Live: both cards on server brief |
| `instructions list --json` (live server read) | `cli_instructions.py` | ✅ Live: 3 snapshots, stable shape |
| `instructions enable` / `disable` (title selection) | `cli_instructions.py` | ✅ Live: exit 0, state flips |
| `instructions toggle-mode --off/--on` (master switch) | `cli_instructions.py` | ✅ Live: exit 0 both directions |
| `instructions apply` (TOML full-sync, destructive) | `cli_instructions.py` | ✅ Live: brief replaced, `enabled=false` honored |
| `instructions rm` (read-modify-write, id preserved) | `cli_instructions.py` | ✅ Live: card dropped, sibling id stable |
| `gflow_instructions_*` MCP tools (6) | `mcp/tools.py` | ✅ Unit tests (13, fake client) — thin adapters over the SAME `get_agent_info`/`patch_agent_info` primitives live-verified above |
| MCP↔CLI parity contract | `tests/mcp/test_cli_parity.py` | ✅ CI-enforced (45 CLI leaves, all decided) |
| Agentic-indicator selector consolidation | `drivers/factory.py` | ✅ Symmetry tests + full suite green |
| Movie `[instructions]` / `[scene.instructions]` brief-sync | `movie_manifest.py` / `cli_movie.py` | ⚠️ Code-path-verified (see below) |

## Live scenario

Fresh project `9d7b750f-b4a8-4c2f-b5b0-a059cbfbae73` minted via one credit-free t2i
(`"a single red apple on a white table, studio lighting"`, NARWHAL, portrait), producing media
`886f0d6c-1baa-460c-a2a0-5a5a463f5ae6`. Then the full CRUD sequence (11 commands, each its own
browser session, all exit 0):

1. `add "Crayon style" --text …` → text card created.
2. `add "Mood ref" --text … --ref 886f0d6c-…` → **the generated image's UUID attached as
   `image_media_ids` on the card** (H4 reference path, previously untested live).
3. `list --json` → both cards present, master `enabled=true`.
4. `disable "Crayon style"` / 5. `enable "Crayon style"` → title selection round-trip.
6. `toggle-mode --off` / 7. `toggle-mode --on` → master switch both directions.
8. `apply brief.toml` → **full-sync replaced** the brief with `Palette rule` (enabled) +
   `Framing rule` (`enabled = false` from TOML honored on the server).
9. `list --json` → exactly the 2 applied cards, prior cards gone (destructive sync confirmed).
10. `rm "Palette rule"` → card removed.
11. `list --json` → only `Framing rule` remains, **same server id `efbb029c…` as in snapshot 9**
    — read-modify-write preserves card ids across mutations.

## Evidence ledger (5-layer)

1. **File count:** exactly one image file written (`886f0d6c-…_1.jpg`, 408,791 bytes).
2. **Magic bytes:** `FF D8 FF E0` (JPEG/JFIF).
3. **Dimensions:** 768×1376 valid portrait render (PIL-confirmed).
4. **Structlog invariants:** `ui_driver.bound mode=agentic` on project entry; 11/11 command
   exits 0; zero `level=error` events across the run; three parseable
   `"command": "instructions list"` JSON payloads with consistent card shape.
5. **User-confirmable artifact:** open
   <https://labs.google/fx/en/tools/flow/project/9d7b750f-b4a8-4c2f-b5b0-a059cbfbae73> —
   the brief shows exactly one card, **"Framing rule"**, disabled, matching snapshot 11.

## Movie brief-sync — code-path-verified (deliberate, no credits spent)

`movie run`'s per-scene brief sync (`_BriefSyncCache` full-sync) drives the **same**
`get_agent_info` → build cards → `patch_agent_info` primitives exercised live above (steps 8–9
are exactly its full-sync semantics). A true end-to-end check requires generating clips (Veo
credits); the user chose to defer it this cycle. Unit + BDD suites cover the manifest parsing,
memoization, and re-sync logic.

## Gates

Local: `ruff check` 0 errors · `ruff format --check` clean · `pyright src` 0 errors ·
hygiene + doc-links clean · full suite 2137 passed. CI on develop merge commits
`a0ca311`/`0cc1051`/`ee47c00` (PRs #270/#271/#269): tests (3.11/3.12/3.13) + **SonarCloud
quality gate** all green.

`/gflow:doc-review`: mechanical checks 1–7 PASS (PROJECT_STATUS.md was stale at v0.27.1 —
updated). Council verdict: **YELLOW across all 3 auditors, zero release-blockers**.
14 distinct findings; all Tier 1/Tier 2 fixed in the release-prep commit (README/AGENTS/
llms.txt entry points now surface instructions/movie/MCP; `[scenes.instructions]` key name;
SKILL.md `--project` required; MCP.md image-tool `instructions` param; AGENTS.md exit-code
range 3–25; INDEX §61 dead ref; INSTRUCTIONS.md status pinned to v0.29.0 + orphaned plan
pointer replaced). Tier 3 deferred to backlog (USER_GUIDE instructions journey,
ARCHITECTURE module inventory refresh, JSON `apply` example). Council reports at
`tmp/council/0{1,2,3}-*.md` (local-only). Process note: no release spec existed for this
cycle; the plan was consolidated and removed on ship.

## Known follow-ups (not in this release)

- Movie brief-sync end-to-end with real clip generation (deferred, credits).
- Movie pipeline MCP tools; `gflow_list_characters` is still a stub (both are reasoned
  exemptions in `tests/mcp/test_cli_parity.py`).
- `gflow_generate_video` deliberately has NO `instructions` param — the video pipeline has no
  instructions support (documented in `docs/MCP.md`); adding it requires video-transport work.
