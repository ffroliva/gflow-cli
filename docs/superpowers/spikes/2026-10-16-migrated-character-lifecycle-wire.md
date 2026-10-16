# Migrated host: the whole character lifecycle rides five `batchexecute` rpcids

**Date:** 2026-10-16 · **Account:** a profile Flow served `flow.google.com` on every load ·
**Cost:** free — uploads, entity create/patch/delete and project reads only; no generation.
**Script:** `scripts/dev/spike_migrated_character_lifecycle.py` (hand-driven recorder: a human
drove the editor, the script logged every `batchexecute` call). Captures stay in
`scripts/dev/_spike_out/` (gitignored — they carry reCAPTCHA tokens, signed URLs and ids).

Question (issue #639, characters row): labs `flow.createEntity` and `flow.projectInitialData`
are retired (404, KNOWN_ISSUES.md), so `gflow character create|list|show|rm` exit 7 on this
host. What does the migrated frontend itself call — and can a character be built from
**uploaded files** with no generation (the primitive an export/import needs)?

## Verdict — observed

Every lifecycle action has a `batchexecute` rpcid on
`flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute`. No labs tRPC and no
`aisandbox-pa` call fired for any of them.

| Action | rpcid | Request (`f.req` inner JSON) | Reply |
|---|---|---|---|
| Create character | **`C4BZMd`** | `[[<project>, null, null, [1, "<default name>", []]]]` | `[[<project>, <entity_id>, null, [1, name, []], null, null, created, updated]]` |
| Patch a field | **`rzMKMb`** | `[[<project>, <entity_id>, null, [1, <name\|null>, [<imgs>, <voices>, <personality>]]], [[<mask>]]]` | the whole entity, as below |
| Upload image **into** a character | **`maseQ`** | the existing upload body, with `… "<filename>", [null, null, [<entity_id>, [1]]], <uuid>, <uuid>]` appended after mime type | media + workflow; the workflow's last field is `<entity_id>` |
| Delete character | **`cz8Z4b`** | `[null, null, <project>, [<entity_id>, …]]` | `[]` |
| Read project (list) | **`Zzl0ze`** | `["projects/<project>", null, null, null, [1]]` | see below |

`rzMKMb` masks observed, one per edit:

| Field | Value slot | Mask |
|---|---|---|
| name | `[1, "laura"]` | `entity_info.display_name` |
| voice | `[1, null, [null, [[null, "callirrhoe"]]]]` | `entity_info.character_info.audio_references` |
| personality | `[1, null, [null, null, "soft"]]` | `entity_info.character_info.personality_notes` |

Voice ids on the wire are **lowercase** (`callirrhoe`, `aoede`), where gflow's labs PATCH sends
the capitalized name.

### Upload binds the image with no follow-up call

After a `maseQ` upload carrying `[null, null, [<entity_id>, [1]]]`, **no `rzMKMb` fired**, yet a
fresh `Zzl0ze` read showed the new workflow appended to the entity's image references:

```
[1, "<name>", [[["3dd078b8-…"], ["2e8cd0b4-…"]], [[null, "aoede"]], "soft"]]
```

So **a character can be built entirely from local files**: `C4BZMd` → `maseQ` with the entity
binding → `rzMKMb` for name, voice and personality. No generation, no quota.

### `Zzl0ze` — one read returns everything an export needs

Decoded reply slots:

| Slot | Content |
|---|---|
| 1 | workflows: `[workflow_id, null, null, [filename, ts, archived?, null, media_id, …], project, <entity_id>?]` — the trailing entity id marks a character's images |
| 2 | media: `[media_id, project, workflow_id, …, [<signed URLs>…], …, [null, [… mime], [w, h], …]]` — signed `flow-content.google` download URLs |
| 3 | **voice catalog**: `[id, 3, "Name", [id, …, [[Name, description, true, sample_url]]]]` |
| 5 | **characters**: `[project, entity_id, null, [1, name, [[[workflow_id]…], [[null, voice]], personality]], thumbnail_media_id, [w, h], created, updated]` |
| 7 | model availability |

The live catalog lists **30** voices; `src/gflow_cli/api/character.py::VOICES` hardcodes 29 —
**`Enceladus`** ("Male, breathy, lower pitch") is missing. The hardcoded list has drifted.

## Not measured — named, not implied

- **The `[1]` in the upload's entity binding.** Seen on a second (body) image; the portrait
  upload's request was truncated by the recorder cap before the binding. Whether `[1]` is a
  slot index or a kind flag is unmeasured.
- **Custom voice creation** (#826). Every catalog entry carried kind `3`; no custom voice was
  created, so its rpcid and kind value are unknown.
- **Whether a voice reaches rendered audio** (#738) — untouched here.
- **`rzMKMb` setting `image_references` directly** to bind an already-uploaded media item — the
  frontend never did it; the upload binding made it unnecessary.
- **Cross-account media ids.** Not attempted. Media and workflow ids are project-scoped by
  construction, so an import into another account is assumed to need a re-upload — which the
  table above shows is cheap.
- **The `@` mention / generation with the entity** — covered by #723, not re-measured.
- **Locale.** One account, `fr` UI (the default name was the French "Personnage sans titre").
  Nothing above anchors on text: rpcids and JSON positions only.

## Consequence for #639 / export-import

`character create|list|show|rm` can be ported to the migrated host with **no DOM driving** for
the entity itself — only the portrait/body *generation* still needs the editor. And a portable
character bundle (name, voice, personality, images) can be re-created in any project on any
profile from files alone: `C4BZMd` + `maseQ(entity)` + `rzMKMb`.

---

## Addendum — Task 0 of the portability plan: gflow drives it, and it carries across accounts

Same day, **driven from code** (`scripts/dev/spike_migrated_character_upload_binding.py`, modes
`inventory`, `probe`, `export`, `bind`) on two accounts Flow served from `flow.google.com`
(profiles A and B below). Cost: uploads, patches and reads ($0) plus one image generation on B
(image quota, 0 credits).

### Editor anchors (structural; the account renders `fr`)

| Control | Anchor |
|---|---|
| open a draft character | project header `flow-tile-view-header button[aria-haspopup='menu']:has(mat-icon:text-is('add'))` → `[role='menu'] button[role='menuitem']:has(mat-icon:text-is('account_circle'))` → navigates to `/project/<id>/character` |
| **create** (first image) | `flow-create-character-footer button:has(mat-icon:text-is('upload'))` → native file chooser |
| name | `flow-character-form input.name-input` (fill + Tab commits) |
| personality | `flow-character-form textarea.personality-textarea` (fill + Tab commits) |
| select portrait / body slot | `flow-slot-chip-button button` nth 0 / nth 1 |
| upload into the selected slot | `flow-character-image-preview button:has(mat-icon:text-is('upload'))` (shown when the slot is empty) |
| voice picker | `flow-character-voice-button button` first (a plain button with no voice yet; `voice-card-start-btn` once set) → `mat-dialog-container input.search-input` → `[role='option']` → `button[class*='detail-add']` |
| delete character | `flow-character-edit-header button:has(mat-icon:text-is('delete'))` |

Voice option text is `"<ligature>\n<Voice name>\n<description>"`. The name is an untranslated
proper noun (same exemption class as the "Nano Banana" model names); match the **name line**
exactly, never a prefix of the whole text. Search is case-insensitive.

### Findings

1. **The draft page has no name field.** A character comes into existence with its first image:
   the footer Upload fires `C4BZMd` (default localized name) then `maseQ`. "Set the name first so an
   orphan is identifiable" is therefore impossible — patch the name **immediately after** create.
2. **`[N]` in the upload's entity binding is the slot index**: `[<entity>, [0]]` portrait (footer
   upload), `[<entity>, [1]]` body (slot upload). Measured on both.
3. **The prompt box's add-menu Upload does NOT bind** — its `maseQ` carries `null` where the binding
   goes; the image becomes plain project media. Only the footer and slot-preview Uploads bind.
4. **A lost upload reply still binds.** The page dropped `maseQ`'s answer after the server received
   it (route `fetch` then `abort`); a later read showed the image bound, on **both** accounts. A
   blind retry would double-bind.
5. **Reads lag writes.** A `Zzl0ze` read taken seconds after the last write showed the entity
   *before* its body upload and voice; a read ~1 min later was correct. The `rzMKMb` / `maseQ`
   replies are authoritative; any read that drives a decision must wait until it reflects the last
   write's `updated` timestamp.
6. **Export needs `=s0`.** The media row's `lh3.googleusercontent.com/asb/…` FIFE URL returns a
   288×512 preview bare; `=s0` (and `=d`, byte-identical) returns the 720×1280 original.
7. **Flow re-encodes uploads**: the downloaded original is not byte-identical to the uploaded file
   (238 567 vs 236 657 B). A bundle sha256 protects the bundle, never a round-trip comparison.
8. **Cross-account import works and keeps identity.** Exported from A (portrait + body + `aoede` +
   personality) → re-built in a new project on B with the anchors above → B's read shows 2 images
   and `aoede`. `gflow image t2i "@Nora …"` on B attached the entity
   (`migrated.character_entities_attached`) and generated the same person and companion figure.

### Incidental

- The `@Nora` generation echoed its prompt as `"Nora Nora standing …"` — the mention path may
  duplicate the name in the prompt text. Belongs to #723's mention code; not this feature.

### Still not measured

- Custom voice creation (#826); whether the voice reaches rendered audio (#738).
- Replacing an already-filled slot (the empty-state Upload only shows on an empty slot).
- A labs-served account (none available here); the labs arm stays, refusing with "not ported".

---

## Addendum 2 — the imported character in a VIDEO (spends credits)

`scripts/dev/spike_migrated_character_video.py` lifts **only** `_unported_form`'s "character
references" refusal in-process (every other unported form still refused), raises
`SUBMIT_REPLY_BUDGET_S` to 240 s and logs the submit reply, then runs the real CLI:
`video t2v "<French line>" --reference-entity <Nora on B> --reference-entity-name Nora`.
Account B, default model, 8 s. **Cost: 12 credits** (balance 152 → 140, read from the reply).

| Step | Observed |
|---|---|
| attach | `migrated.character_entities_attached` with the requested entity id |
| submit | `MZZa6b` reply **carried the media id** (`[null, 140, [[<media>, …]]]`); `migrated.submit_observed` status 6 at +7 s |
| render | `jwpduf` status 2 → done; downloaded, exit 0 |
| clip | 720×1280 H.264, 8.0 s, AAC stereo 48 kHz |
| identity | same person, wardrobe, companion figure and setting as the portrait the character was built from |
| speech | Whisper (fr): "Bonjour, je m'appelle Nora et je suis ravie de vous rencontrer." — the scripted line exactly |
| voice | median F0 197.5 Hz (IQR 167–216); bound voice Aoede's sample 210.5 Hz, but Kore 210.5 and Callirrhoe 205.1 overlap — **consistent, not identifying** (#738's caveat holds; needs its A/B) |

**Reading against the pre-registered table:** the first row — the observer works as is. The
#723 diagnosis (`MZZa6b` with a NULL payload → 60 s timeout) **did not reproduce** in this run:
1 run, so it is *not reproduced*, not *disproved*. The gate's stated reason should be re-measured
before it is removed; if the payload is still sometimes null, the status-poll fall-through the gate
comment describes is still the right fix.

### Incidental

- **The migrated video download ignores the output directory.** With no `--output`,
  `MigratedComposer` writes `<media_id>.mp4` into the **current working directory**
  (`migrated_composer.py:3404`, `out_dir or Path.cwd()`), while `video t2v --help` says
  "Defaults to tmp/" and USAGE documents `$GFLOW_CLI_OUTPUT_DIR`. Not filed yet.
- The prompt was again echoed as "Nora Nora …" (see Addendum 1); it did **not** reach the speech.
