# Agent Instructions — project brief cards for Google Flow's Agent Mode

> **Status.** The ephemeral `-i / --instruction` flag on `gflow image t2i` / `i2i`
> shipped in **v0.28.0** (live-verified). The persistent `gflow instructions`
> command group described here is the **docs-first spec** for an upcoming release
> — it is the acceptance criteria for the implementation, not yet shipped. Sections
> describing unshipped surfaces are marked _(planned)_.

## What an instruction card is

In Google Flow's **Agent Mode**, each project carries a **brief** — a set of
**instruction cards** the agent consults every time it generates. Each card has:

- a **title** (human label),
- a **guideline text** (e.g. _"Every image is a flat 2D children's crayon drawing on textured paper"_),
- zero or more **references** — an attached **image** _or_ a **character** entity,
- an **enabled** toggle.

Setting up cards is **credits-free** — it's a `PATCH` to the project brief, not a
generation. Credits are only spent when you actually generate.

### How cards steer generation (mechanism)

Instruction cards apply **only on agentic-cohort sessions**, and only through the
agent's **reasoning path**: when you make a natural-language request, the agent
rewrites the image prompt to fold in every **enabled** card (live-verified — an
enabled "crayon drawing" card turned a style-neutral _"a red bicycle"_ prompt into
a crayon bicycle; an attached crayon **reference image** did the same). A card
with `enabled = false` stays in the brief but is ignored. On a classic-cohort
session, cards do not apply and `gflow` warns.

## The three-layer pipeline

```
1. SET UP   project context   →  gflow instructions add / apply   (credits-free)
2. GENERATE using that context →  gflow image t2i / i2i --project <id>
3. COMPOSE  per-scene context  →  movie.toml [[…instructions.card]]   (planned)
```

**Agents: always set up the brief (layer 1) before generating (layer 2).** The
brief is what makes generations consistent across a project.

## Ephemeral `-i` vs persistent cards

| | `-i "text"` (shipped) | `gflow instructions` _(planned)_ |
|---|---|---|
| Scope | one generation | persists on the project brief |
| Creates | a fresh enabled text-only card each call | managed cards you can toggle/edit/remove |
| References | text only | image **or** character references |
| Lookup | never — always creates | by **title** (case-insensitive, fail-fast on ambiguity) |

```bash
# Ephemeral: a one-off enabled text card for this generation only.
gflow image t2i "a cat on a chair" -i "flat 2D children's crayon drawing"
```

## `gflow instructions` command surface _(planned)_

Persistent CRUD over a project's brief cards. Card **selection** is always by
**title** — never a card UUID.

```bash
gflow instructions add   TITLE --text TEXT [--ref REF]... [--project ID] [--disabled]
gflow instructions list  [--project ID] [--json]
gflow instructions enable  TITLE [--project ID]
gflow instructions disable TITLE [--project ID]
gflow instructions rm      TITLE [--project ID]
gflow instructions apply   FILE  [--project ID]   # declarative full-sync (TOML/JSON)
gflow instructions toggle-mode [--on/--off] [--project ID]
```

### The generic `--ref` (one attribute for all reference types)

Flow's card reference picker has **All / Images / Characters** tabs — a reference
is an **image** or a **character**, and both are ultimately a `referenceId`. So
there is **one** `--ref` option (repeatable), not separate `--ref-image` /
`--ref-character` flags. `gflow` classifies each value and routes it to the right
wire field:

| `--ref` value | Resolves to |
|---|---|
| local image path (`./hero.png`) | upload → media id → `imageReferenceMediaIds` |
| generated-image UUID | `imageReferenceMediaIds` |
| character id or name | `characterReferenceEntityNames` |

```bash
# A persistent card that anchors style to a reference image AND a character.
gflow instructions add "Hero look" \
  --text "Match the reference image's art style; keep the hero on-model" \
  --ref ./refs/mood.png \
  --ref hero-character \
  --project 6b714c4e-...
```

### `--project` semantics

`gflow instructions` and generation commands take `--project <id>` to target a
specific project's brief. Generation with `--project` uses that project's **active**
(enabled) cards. Without `--project`, generation creates a scratch project (so
persistent cards only make sense with `--project`).

### `apply FILE` — declarative sync

`gflow instructions apply` replaces the project's cards with the file's contents
(idempotent full-sync), so a brief can live in version control:

```toml
# brief.toml
[[card]]
title   = "Cinematic lighting"
text    = "Volumetric cinematic light from camera-left"
ref     = ["./refs/mood.jpg"]
enabled = true

[[card]]
title   = "Hero"
text    = "Keep the hero on-model"
ref     = ["hero-character"]
```

## Typical agent-driven workflow (machine-readable)

1. Discover/choose a project id (`gflow instructions list` needs `--project`; find
   it in the Flow editor URL `…/project/<id>/…`).
2. `gflow instructions apply brief.toml --project <id>` — set up the brief (free).
3. `gflow image t2i "…" --project <id>` — generate using the active cards.
4. Adjust: `gflow instructions disable "Cinematic lighting" --project <id>`, regenerate.
5. (planned) `movie.toml` per-scene overrides for multi-scene consistency.

**DO NOT** rely on `-i` for anything you want to reuse — it's per-generation.
**DO NOT** pass card UUIDs — select cards by title.
**DO** set up the brief before generating; cards only apply on agentic sessions.

## See also

- [USAGE.md](USAGE.md) — full command reference.
- [MOVIE.md](MOVIE.md) — multi-scene movies (`[[…instructions.card]]` blocks, planned).
- `docs/superpowers/plans/2026-07-08-agentic-instructions/` — plan + spike findings
  (mechanism, H4 confirmation, the reference-consolidation decision).
