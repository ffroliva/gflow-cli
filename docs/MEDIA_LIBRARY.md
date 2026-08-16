# Flow's Media Library — component reference

> How Flow stores, names, lists, and surfaces project media — and every pitfall
> gflow-cli has hit driving it. All claims here are reverse-engineered from
> live recon (2026-08-15/16, evidence in [#529](https://github.com/ffroliva/gflow-cli/issues/529)
> / [#543](https://github.com/ffroliva/gflow-cli/issues/543) and the
> [#529 picker spike](superpowers/spikes/2026-08-15-picker-tile-alt-text.md));
> Google documents none of it publicly and may change any of it without notice.

## Why this doc exists

Media management touches every generation surface: image refs (`--ref`), i2v
frame slots, r2v ingredients, character entities, voices, uploads, and the
catalog that mirrors them locally. Most historical gflow bugs in this area
(#237, #245, #287, #529, #541) came from wrong assumptions about *one* of the
facts below. Read this before touching any picker, listing, or reference code.

## The library model

Each Flow **project** owns a media library. There is no global cross-project
library: an asset generated in project A is not reachable from project B's
picker ([#287](https://github.com/ffroliva/gflow-cli/issues/287), confirmed
live). The library holds, per the `flow.projectInitialData` payload
(`projectContents`):

| Payload field | What it is |
| --- | --- |
| `media[]` | Every project asset. Kind is the discriminating sub-key: `image` (with `generatedImage` **or** `userUploadedImage`) or `video` (`generatedVideo` + `operation`). Observed split on a 267-asset project: 241 images / 26 videos. |
| `workflows[]` | One per generation batch/upload. `metadata.displayName` (the caption), `metadata.primaryMediaId` (→ the media UUID), `batchId`, create/update times. |
| `externalReferenceMedia[]` | Flow's **preset voices** ("Vozes" tab): 30 star-named entries (`achernar` … `zubenelgenubi`), `audio.generatedAudio` with `isPresetAudioSample: true` and a gstatic sample URL. These are platform presets, not user assets. |
| `agentInfo` | `agentToggleState` (the [#299](https://github.com/ffroliva/gflow-cli/issues/299) classic/agentic arm) + default generation settings. |

**Characters** (`gflow character`, "Personagens") are project-scoped entities
managed through their own routes — they are referenced *by* media generation
but are not `media[]` rows. User-uploaded audio was not observed in recon;
audio appears only as the preset voices and as `reference_audio` on video
requests.

Per-asset metadata worth knowing: `mediaMetadata.createTime`,
`mediaBlobSize`, `visibility`, and the full `requestData` (including the
original prompt) for generated media.

## Identity and naming

**The media UUID (`media[].name`) is the only stable identifier.** Everything
else is display sugar. Rules, each live-verified:

1. **`displayName` is an AI-generated semantic caption of the result, not the
   prompt.** Across 136 prompt-bearing assets: only 2 captions were substrings
   of their prompt, none were prefixes, but ~82% of caption words come from
   the prompt — Flow *summarizes*. A 1,345-character prompt produced the
   26-character caption "Fruit vendor holding mango".
2. **Hard caption cap ≈ 35 characters** (observed 17–35, mean 30; some end in
   a server-side ellipsis). Long prompts are summarized, never clipped.
3. **Uploads keep their original filename verbatim** — no captioning pass.
4. **Captions are computed asynchronously.** A fresh generation's response may
   not carry `displayName` yet; the recorder then (correctly) stores no name.
   ([#543](https://github.com/ffroliva/gflow-cli/issues/543) tracks backfill.)
5. **Names are mutable**: users can rename assets via the Flow Agent (per
   [Google's own docs](https://support.google.com/flow/answer/16935308)) —
   so a cached name can go stale.
6. **Names are not unique.** Two assets can share a caption (live-confirmed in
   the #529 spike); disambiguation is only possible via the UUID.

Consequence for all tooling: **search by name, verify by UUID, never treat a
name as identity.** gflow's picker code asserts the exact UUID in the selected
tile's thumbnail URL (`media.getMediaUrlRedirect?name=<uuid>`).

### Rename staleness and the freshness model

Because names are mutable, every cached name is a liability with a defined
blast radius. The invariant that bounds it:

> **Cached name = optimization. Listing = truth. UUID = identity.**

What a stale name can and cannot do:

- It **cannot** attach the wrong asset — a stale (or colliding) name search
  either surfaces no tile or surfaces a tile whose UUID fails the exact-match
  assertion; both are misses, never substitutions.
- It **can** silently downgrade the run: miss → integrity-verified local
  re-upload (duplicate upload) or, with no verified file, a typed error.

Freshness is layered, cheapest-first:

1. **Write-through** — every path that learns a name (generation response,
   listing fetch, sync) writes it to the catalog immediately, with
   provenance. A gflow-initiated rename (if ever built) must update the
   catalog in the same operation, never as a follow-up.
2. **Refresh-on-miss** ([#546](https://github.com/ffroliva/gflow-cli/issues/546),
   planned) — on a picker miss, one `projectInitialData` GET (~0.5 s)
   resolves the *current* name by UUID, the search retries once, and the
   fresh name is written back. A user rename then costs one extra request,
   once.
3. **Bulk reconciliation** — `gflow data sync --names`
   ([#543](https://github.com/ffroliva/gflow-cli/issues/543), planned) for
   cold catalogs and ghost detection; `gflow doctor`
   ([#542](https://github.com/ffroliva/gflow-cli/issues/542), planned)
   reports the gap. Neither is load-bearing for a working generation once
   layers 1–2 exist.

## The listing endpoint

`flow.projectInitialData` (tRPC GET,
`https://labs.google/fx/api/trpc/flow.projectInitialData?input={"json":{"projectId":"<id>","toolName":"PINHOLE"}}`):

- Fires on **plain project load**, regardless of UI cohort — including the
  [#174](https://github.com/ffroliva/gflow-cli/issues/174) full-page-library
  shape and the agentic arm, because no UI interaction is involved.
- **Complete, not paginated** at the scales observed: a 267-media project
  returned every asset in one 650 KB payload with zero pagination markers.
  (Treat larger projects as unverified; check `media[]` count ≥ expectation
  before concluding an asset was deleted.)
- **Directly callable** on an authenticated browser context (cookies only, no
  navigation, no rendering): measured 458–610 ms per project. This is the
  cheap path for any bulk read of a project's media
  ([#543](https://github.com/ffroliva/gflow-cli/issues/543)).
- The `media[]` item shape **differs from `batchGenerateImages`** (notably no
  `fifeUrl`), so `GeneratedImage.from_response_dict` does not parse it — use
  the `workflows[].metadata.primaryMediaId → displayName` join instead.

## The picker UI (reference/ingredient dialog)

Tabs observed (pt locale): **Tudo** (all), **Imagens**, **Vídeos**, **Vozes**
(preset voices), **Enviar mídia** (upload) — plus **Personagens** on composer
surfaces that support character entities (the tab set varies by mode), and a
**Recentes** project dropdown. Facts that shape all automation:

1. **The picker has its own active project** (the Recentes dropdown), which is
   *not* the editor's project ([#287](https://github.com/ffroliva/gflow-cli/issues/287)):
   `--project` navigates the editor only. gflow aligns the picker before every
   lookup (`_sync_picker_project`).
2. **The grid is virtualized.** On a 267-asset project the scroller spans the
   full content (13,348 px for a 515 px viewport — every asset is reachable by
   *user* scrolling), but only **19–27 `[role='option']` tiles exist in the
   DOM at any instant**, recycled as the window moves. A DOM scan can never
   enumerate the library, and scroll-and-scan loops are both slow and
   WAF-noisy — this is why the #287-era scroll tiers were removed in v0.58.0.
3. **Search indexes the caption, not the prompt** — searching prompt text
   never matches ([#493 recon](https://github.com/ffroliva/gflow-cli/issues/493),
   `ReferenceNotFoundError`).
4. **The picker dialog exposes no accessible tree** (`aria_snapshot` returns a
   bare `dialog`): ARIA role+name matching cannot find tiles. Match tiles by
   text — anchored, tolerating the localized media-type badge the tile text
   appends with no separator (`…mapImagem` on a pt profile).
5. **Clicking a result tile attaches it directly and closes the dialog**
   (2026-08-16 redesign). The include button ("Incluir no comando") exists
   only in the hover-preview pane; gflow treats a closed dialog as success and
   keeps the include-button flow as a legacy fallback.
6. **Thumbnail URLs carry the media UUID** — the one DOM-visible identity
   anchor, used for exact-tile verification.

## Pitfall summary

| Pitfall | Consequence | Mitigation in gflow |
| --- | --- | --- |
| Caption ≠ prompt | Prompt-text searches never match | Search recorded `display_name` only; typed `ReferenceNotFoundError` explains it |
| Caption is async | Fresh assets may have no name to search | Verified local-file upload fallback; #543 backfill |
| Names mutable + non-unique | Stale/ambiguous lookups; silent downgrade to re-upload after a rename | Exact-UUID tile assertion; freshness model above (write-through + refresh-on-miss #546 + sync #543) |
| Virtualized grid | DOM scans see ≤ ~27 of N tiles | Never scroll-scan; search-first (#529 contract) |
| Per-picker project state | Asset "missing" though it exists | `_sync_picker_project` before every lookup |
| Per-project library | Cross-project UUIDs unreachable in picker | Integrity-verified local re-upload fallback |
| No accessible tree | ARIA role+name matching silently fails | Text-matched tiles (anchored regex) |
| Localized type badge in tile text | Exact text match fails off-English | Badge-tolerant anchored regex |
| Single-click attach | Include-button waits time out | Dialog-closed = success; legacy fallback kept |
| Listing shape ≠ generation shape | Reusing the generation parser crashes | Dedicated `primaryMediaId → displayName` join |
| `redacted` history mode | Captions deliberately not stored locally | Picker path skipped by design; see [CONFIGURATION § GFLOW_CLI_HISTORY_PROMPTS](CONFIGURATION.md#gflow_cli_history_prompts) |

## How gflow-cli maps onto this

- **Catalog**: `assets.flow_media_id` (identity), `metadata_json.display_name`
  (search key, store-mode only), `local_files` (SHA-256-verified fallback
  bytes). See [DATA_LAYER.md](DATA_LAYER.md).
- **Reference resolution** (v0.58.0, #529):
  `catalog UUID → display name → picker search → exact-UUID tile → attach`,
  falling back to a verified local upload, else a typed error. See
  [REFERENCE_STRATEGIES.md](REFERENCE_STRATEGIES.md).
- **Planned**: `gflow doctor` ([#542](https://github.com/ffroliva/gflow-cli/issues/542))
  reports nameless/ghost rows; `gflow data sync --names`
  ([#543](https://github.com/ffroliva/gflow-cli/issues/543)) reconciles the
  catalog from the listing endpoint.
