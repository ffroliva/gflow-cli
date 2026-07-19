# Referencing assets: `@`-mention vs `--reference-entity` vs `--ref`

gflow gives you three ways to tell a generation "use *this* asset, don't invent one." They look
like rivals; they are not. This guide is the one-page decision rule.

Two of them — an inline `@Name` mention and the `--reference-entity <id>` flag — **resolve to the
same wire** (`referenceEntities`) and **dedupe** against each other. The third — `--ref` — attaches
an arbitrary image (`referenceImages`). So the real choice is two independent axes, not three
transports.

## The two axes

1. **WHAT you reference**
   - A **saved, named asset** in the project — a `gflow character` entity, or a generated/uploaded
     media asset with a `display_name` → reference it *by identity*: `@Name` or `--reference-entity`.
   - An **arbitrary / one-off image** (a local file, or a look/style you don't intend to reuse) →
     `--ref` (local path or in-project media UUID).

2. **HOW you author it** (only matters once you've chosen "by identity")
   - Inline, readable, agent-friendly → `@Name` in the prompt.
   - Explicit, scriptable, name-ambiguous → `--reference-entity <entityId>` (image path only — see
     the matrix below).

## Decision table

| I want to… | Use | Why |
|---|---|---|
| Reference a **saved Character** by name, inline | `@Name` in the prompt | Resolves to `referenceEntities`; the model anchors on that entity. Works on every generation path. |
| Reference a Character by **explicit id** in a script | `--reference-entity <entityId>` (`t2i`/`i2i`) | Same wire as `@Name`, no name-resolution ambiguity. **Not available on `t2v`/`r2v`** — use `@Name` there. |
| Reference a **saved media asset** (generated/uploaded) by name | `@Name` in the prompt | Resolves to the asset's UUID → `referenceImages`, zero re-upload. |
| Reference a **one-off / arbitrary image** or a look | `--ref <path-or-uuid>` (`i2i`, `r2v`) | Stages `referenceImages` directly; no saved identity needed. |
| Reuse the **same subject** across many generations | `gflow character` once → then `@Name` everywhere | A Character is the durable, name-addressable identity. See [CHARACTER.md](CHARACTER.md). |
| Anchor an **identity *and* a look** in one shot | Both — a Character mention **and** a `--ref` | They complement (entity + image), and identical references dedupe, within the model's reference cap. |

### Same wire, and they dedupe

`@Name` (character) and `--reference-entity` are not two transports — the mention resolver stages the
**same** `referenceEntities:[{entityId}]` the picker/`--reference-entity` path produces (design spec
§4.3, "H1"). Character mentions dedupe against an explicit `--reference-entity`; media mentions dedupe
against an explicit `--ref` — you get **one staged reference per asset**, however it was named. Model
reference caps are enforced before submit.

`--ref` is the odd one out: it always stages `referenceImages` (an image), never `referenceEntities`
(a saved identity). That's the whole distinction — *identity* vs *image*.

## Per-generation-path support matrix

Which reference methods each path accepts (design spec §2, verified against `cli_image.py` /
`cli_video.py`):

| Path | Character (identity) | Media / ingredient (image) |
|---|---|---|
| `image t2i` | `@Name` · `--reference-entity <id>` | `@Name` (in-project UUID → `referenceImages`) |
| `image i2i` | `@Name` · `--reference-entity <id>` | `@Name` · `--ref <path-or-uuid>` |
| `video t2v` | `@Name` **only** (no `--reference-entity` flag on the video path) | ❌ — media mentions on the video path are Phase 3 |
| `video r2v` | `@Name` **only** (no `--reference-entity` flag on the video path) | `--ref <path>` (local ingredients); `@media` is Phase 3 |

Notes:
- **The video path has no `--reference-entity` flag.** To attach a saved Character on `t2v`/`r2v`,
  `@Name` is the only name-based route. On the image path both forms exist.
- A mention whose resolved kind is unsupported on the invoked path (e.g. `@someMedia` on `t2v`)
  fails fast with a clear exit-11 message naming the Phase-3 limitation — no silent drop, no wasted
  credit.

## Prerequisite: a mention only works if the asset can actually ride

An `@Name` character mention only resolves if the entity **has at least one reference image** (its
`workflow_ids` are non-empty). A bare, image-less Character cannot stage as a `referenceEntity`, so
tagging it fails **early** with a clear error instead of drifting to a cryptic UI-attach abort:

```
@Zoro has no reference images — a character needs at least one reference image before it can be tagged.
```

Give the character a reference image first (`gflow character` create flow), then tag it.

## Command examples

```bash
# 1. Saved Character, inline, on any path — the everyday case
gflow image t2i "@CaptainZoro on a rain-soaked neon rooftop, cinematic"  --project <id>
gflow video t2v "@CaptainZoro walking through the crowd, slow tracking shot" --project <id>

# 2. Two saved Characters in one video prompt (multi-ref, cap-checked)
gflow video r2v "@Zoro hands @Mika the sword" --project <id>

# 3. Explicit id in a script (image path) — no name-resolution ambiguity
gflow image i2i "same man, colder grade" --reference-entity fe_id_abc123 --ref hero.png --project <id>

# 4. One-off image / look, no saved identity — just --ref
gflow image i2i "make it cinematic" --ref hero.png
```

## For agents

Pick with one rule:

- **Referencing a saved, named Character or asset?** Put `@Name` in the prompt. On `t2i`/`i2i` you may
  instead pass `--reference-entity <entityId>` when you have the id and want no name ambiguity — it's
  the **same wire** and dedupes. There is **no** `--reference-entity` flag on `t2v`/`r2v`; use `@Name`.
- **Referencing a one-off image or a look/style you won't reuse?** Use `--ref <path-or-uuid>`
  (`i2i`, `r2v`).
- **Need the subject reused across many generations?** Create it once with `gflow character`, then
  `@Name` it everywhere.
- **Prerequisite:** a Character must have a reference image before `@Name` will resolve — an
  image-less entity fails fast.

## See also

- [CHARACTER.md](CHARACTER.md) — creating and reusing entities (`referenceEntities` wire protocol).
- [Asset-tagging design spec](superpowers/specs/2026-07-18-asset-tagging-design.md) — mention
  grammar, resolution order, dedupe/cap contract, error taxonomy.
- [ASSET_TAGGING_RECON.md](ASSET_TAGGING_RECON.md) — what gflow already owns on the wire.
