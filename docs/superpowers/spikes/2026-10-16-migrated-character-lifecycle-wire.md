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
