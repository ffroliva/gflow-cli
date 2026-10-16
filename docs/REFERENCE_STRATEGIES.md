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
     `--ref` (local path; image `i2i` also accepts an in-project media UUID).

2. **HOW you author it** (only matters once you've chosen "by identity")
   - Inline, readable, agent-friendly → `@Name` in the prompt.
   - Explicit, scriptable, name-ambiguous → `--reference-entity <entityId>` (all paths except `i2v` — see
     the matrix below).

## Decision table

| I want to… | Use | Why |
|---|---|---|
| Reference a **saved Character** by name, inline | `@Name` in the prompt | Resolves to `referenceEntities`; the model anchors on that entity. Works on every generation path. |
| Reference a Character by **explicit id** in a script | `--reference-entity <entityId>` (`t2i`/`i2i`/`t2v`/`r2v`) | Same wire as `@Name`, no name-resolution ambiguity. **Not available on `i2v`** (its DTO rejects reference entities). |
| Reference a **saved media asset** (generated/uploaded) by name | `@Name` in the prompt | Resolves to the asset's UUID → `referenceImages`, zero re-upload. |
| Reference a **one-off / arbitrary image** or a look | `--ref <path>` (`i2i`, `r2v`) or `--ref <uuid>` (`i2i` only) | Stages `referenceImages` directly; no saved identity needed. |
| Build a **series from one image** in a single run | `"ref": "batch:N"` (or a local file path) in a `gflow run --config` row | The earlier row's image is already in the run's project: referenced in place, no upload, no duplicate. See [USAGE § Referencing an earlier row](USAGE.md#referencing-an-earlier-row). |
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

For a catalog-backed `--ref <uuid>`, gflow resolves the UUID to Flow's recorded
`display_name`, searches that name in the browser picker, and then verifies the
exact UUID in the surfaced tile. The name is discovery; the UUID remains identity,
so duplicate names cannot select the wrong asset. This path does not scan the
unfiltered picker grid. If the named tile is unavailable, `image i2i` can still
use the catalog's recorded local file as its upload fallback. I2V start/end
frames carry the same fallback even when they have a name, so a picker miss can
upload the exact recorded bytes without scanning. The fallback is discarded if
its catalog byte count or SHA-256 no longer matches the file on disk. A stale
name or missing picker search never enables an unfiltered tile click. Redacted
prompt history deliberately does not persist Flow captions because they may
paraphrase the prompt.

## Per-generation-path support matrix

Which reference methods each path accepts (design spec §2, verified against `cli_image.py` /
`cli_video.py`):

| Path | Character (identity) | Media / ingredient (image) |
|---|---|---|
| `image t2i` | `@Name` · `--reference-entity <id>` (+ `--reference-entity-name` on flow.google.com) | `@Name` (in-project UUID → `referenceImages`) |
| `image i2i` | `@Name` · `--reference-entity <id>` (+ `--reference-entity-name` on flow.google.com) | `@Name` · `--ref <path-or-uuid>` |
| `video t2v` | `@Name` · `--reference-entity <id>` | ❌ — media mentions on the video path are Phase 3 |
| `video i2v` | ❌ — start/end frames replace ingredients | local path or catalog UUID via `IMAGE` / `--initial-frame` · optional `--end-frame` |
| `video r2v` | `@Name` · `--reference-entity <id>` | `--ref <path>` (local ingredients); `@media` is Phase 3 |

Notes:
- **`--reference-entity` IS available on `t2v` and `r2v`** (both carry `referenceEntities` on the
  wire). It is deliberately absent from `i2v`, whose DTO rejects reference entities — an i2v
  request carries start/end frames instead. Corrected 2026-08-14: this section previously denied
  the flag on the whole video path while `cli_video.py` registered it. On the image path both forms exist.
- A mention whose resolved kind is unsupported on the invoked path (e.g. `@someMedia` on `t2v`)
  fails fast with a clear exit-11 message naming the Phase-3 limitation — no silent drop, no wasted
  credit.

## On flow.google.com (images only, #723)

On an account Flow serves from `flow.google.com`, `image t2i` and `image i2i` take a character
mention (`@Name` or `--reference-entity`) through the composer's own `@` picker. **Video is
unchanged:** video characters are still refused there — `--reference-entity` and a video `@Name`
both exit 36 before anything is submitted (a video `@Name` used to exit 29, when the character
catalog could not be read on that host).

- **The catalog is read on that host.** The labs catalog route is retired (401 or 404 measured);
  on that refusal `gflow character list` falls back to flow.google.com's own project read
  (2026-10-16), so an `@Name` resolves to the character's id there as it does on labs. If the
  character source still fails, an image mention that matches no media asset becomes a
  character **by name**: the name is the **first word after `@`**. A character whose name has
  several words needs the catalog to resolve. The name stays in the prompt as plain text
  (`@Sussy cooks` → chip + `Sussy cooks`). Media mentions resolve as before.
- **The option titled exactly the name is picked, not the first option.** The picker
  lists characters and media in one list, and their DOM is identical apart from a translated
  subtitle. An image whose caption starts with the character's name can rank above the
  character (measured 2026-10-13). gflow reads each option's own title (the asset's
  name, which is your data, not a UI label). It then commits the one option whose title
  equals the name, case-insensitive. Nothing is committed on trial: a chip deleted from
  the prompt still goes into the generation. The run is refused before submit (exit 32),
  reporting how many options the picker offered, when no option matches exactly, when several do (for
  example an upload literally named `Sussy`), or when the match turns out to be media.
- **The submit is checked for exactly the requested references.** The `ogiZ0b` body
  carries media at one slot and characters at another. It must hold exactly the
  requested local references plus the committed character: one missing or one extra
  aborts the submit before Flow generates anything.
- **An earlier image of the same project rides in place too ([#995](https://github.com/ffroliva/gflow-cli/issues/995)).**
  `--ref <uuid>` (and an MCP image task's `refs`) is referenced through the same picker
  path as `batch:N`, with no upload, when the local catalog records that image in the
  `--project` being generated in and kept its Flow caption (the default
  `GFLOW_CLI_HISTORY_PROMPTS=store`). The catalog only routes it: the tile must still be in
  the page's project grid, so a stale row fails with exit 32 instead of attaching something
  else. A UUID from another project, or one without a recorded caption, still exits 36.
  Because nothing is uploaded, this path never meets Flow's one-time upload-terms dialog.
  **Behaviour change:** a same-project image since deleted in Flow used to exit 36 at
  once; it now spends up to ~90 s (`EXISTING_REF_WAIT_S`) reloading the grid, then exits 32.
- **What to do on a refusal.**
  - **Exit 32:** no option, or several, carry the exact name. Rename the colliding asset in
    Flow, or give the character a unique one-word name.
  - **Exit 36 on `--reference-entity <id>`:** the picker finds characters by name, so pair
    the id with `--reference-entity-name <Name>`.
  - **Exit 29 on a page served labs:** the character was resolved by name, without an id,
    and that picker attaches by id. Pass `--reference-entity <id>`.

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
  the **same wire** and dedupes. The explicit entity flag is also available on `t2v`/`r2v`; it is
  deliberately absent from `i2v`, whose inputs are start/end frames.
- **Referencing a one-off image or a look/style you won't reuse?** Use `--ref <path>` on `i2i` or
  `r2v`; a media UUID is accepted by image `i2i` only.
- **Need the subject reused across many generations?** Create it once with `gflow character`, then
  `@Name` it everywhere.
- **Prerequisite:** a Character must have a reference image before `@Name` will resolve — an
  image-less entity fails fast.

## See also

- [CHARACTER.md](CHARACTER.md) — creating and reusing entities (`referenceEntities` wire protocol).
- [Asset-tagging design spec](superpowers/specs/2026-07-18-asset-tagging-design.md) — mention
  grammar, resolution order, dedupe/cap contract, error taxonomy.
- [ASSET_TAGGING_RECON.md](ASSET_TAGGING_RECON.md) — what gflow already owns on the wire.
