# Flow Characters — reverse-engineered wire protocol & implementation plan

> Recon source: live HARs `labs.google20.har` (build) + `labs.google21.har` (Concluir + redirect),
> denon82 profile, project `96e81f06-…039e`, character `5e97a720-…909` ("Denidra"), 2026-06-02.
> Motivated by issue #145 (create + reuse a consistent character per project). Supersedes the held
> Avatar work (PR #123) — see "Avatar vs Character" below.

> ⚠️ **This file is raw recon provenance and predates the final transport decision.** Where it frames
> generation as "the existing image transport" / "mostly REST", that is SUPERSEDED: generation is
> **reCAPTCHA-walled (direct POST → 403)** and must be **UI-driven/passive-capture** — see
> [`CHARACTER.md`](CHARACTER.md) §11 (Option B). For the current CLI surface (flag-based `--id`/`--name`,
> `rm` + image `--character` deferred to v2) see `CHARACTER.md` §8. Trust `CHARACTER.md` on transport/scope.

## TL;DR

A **Character is a Flow _entity_** (`entityType: CHARACTER`) scoped to a project. Building one is a
**hybrid**: image generation is reCAPTCHA-gated (browser path, costs credits); everything else —
naming, voice, personality, picking the primary image, persisting the character — is **pure Bearer
REST** (no reCAPTCHA, no credits). This matches the REST-path capability matrix.

## Entity model

```
entity {
  projectId
  entityId                       # the characterId in the URL /project/{p}/character/{entityId}
  entityInfo {
    entityType: "CHARACTER"
    displayName                  # "Denidra" (default "Untitled Character")
    characterInfo {
      personalityNotes           # free text — guides actions when not specified in a prompt
      audioReferences: [ { presetVoiceId: "gacrux" } ]      # voice; preset id is lowercased name
      imageReferences: [ { workflowId }, { workflowId } ]   # face + body (point to WORKFLOWS, not media)
    }
  }
  thumbnailMediaId               # primary face image
  thumbnailDimensions {width,height}
  createTime / updateTime
}
```

Critically, `imageReferences` reference **workflowIds**, and each workflow carries a
`metadata.primaryMediaId` (the chosen image). So: character → workflows → primaryMediaId → media.

## Endpoints (in build order)

| # | Call | Auth | Cost | Purpose |
|---|------|------|------|---------|
| 0 | `POST /fx/api/trpc/flow.createEntity` — req `{json:{projectId}}` → resp `{…json:{entityId, entityInfo{entityType:CHARACTER, displayName:"Untitled Character", characterInfo:{}}}}` | session (tRPC) | free | **Mint** a new character entity, returns fresh `entityId` |
| 1 | `POST /v1/projects/{projectId}/flowMedia:batchGenerateImages` | reCAPTCHA (browser) | **credits** | Generate a reference image (face, body, refinements) |
| 2 | `PATCH /v1/flowWorkflows/{workflowId}` (`updateMask: metadata.primaryMediaId`) | Bearer REST | free | Pick which generated image is the workflow's primary |
| 3 | `PATCH /v1/flow/entities` (`updateMask: entityInfo.displayName,…personalityNotes,…audioReferences,…imageReferences`) | Bearer REST | free | Save the character (name, voice, personality, image refs) — the **Concluir** action |
| R | `GET /fx/api/trpc/flow.projectInitialData` → `projectContents.entities[]` (filter `entityType==CHARACTER`) | session | free | **list/show** characters |
| opt | `POST /fx/api/trpc/flow.generateCharacterPrompt` — req `{json:{archetype:"THE_FAMILIAR"}}` → `{generatedPrompt}` | session | free | Optional archetype→starter-prompt helper (enum incl. THE_FAMILIAR, …) |
| — | `GET /v1/flow/likeness:checkEligibility` → `{ineligibilityReasons:["REGION"]}` | Bearer | free | Proves **Avatar/likeness is region-locked** here — why we hold #123 |

**Voices:** preset library served as `<Name>.wav` previews (Callirrhoe.wav, Algenib.wav…); persisted as
lowercased `presetVoiceId`. UI also offers "Criar nova voz" (custom) — out of scope for v1.

### 1. Generate reference image — `flowMedia:batchGenerateImages`
```
clientContext: { recaptchaContext{token, applicationType:RECAPTCHA_APPLICATION_TYPE_WEB},
                 projectId, tool:"PINHOLE", workflowId, sessionId }
mediaGenerationContext: { batchId }
useNewMedia: true
requests: [ {
  clientContext{…},
  imageModelName: "NARWHAL",                       # = "Nano Banana 2" in the UI model picker
  imageAspectRatio: "IMAGE_ASPECT_RATIO_LANDSCAPE",
  structuredPrompt: { parts: [ { text } ] },
  seed,
  imageInputs: [                                   # absent for the first (face) gen; present for refine/body
    { imageInputType: "IMAGE_INPUT_TYPE_BASE_IMAGE", name: <mediaId> },   # image being edited
    { imageInputType: "IMAGE_INPUT_TYPE_REFERENCE", name: <mediaId> } ]   # identity to preserve (the face)
} ]
```
Response → `media[{name(mediaId), image{generatedImage{fifeUrl, mediaGenerationId, modelNameType}, dimensions}}]`,
`workflows[{name(workflowId), metadata{displayName, primaryMediaId, batchId}, projectId, parentEntityId: <characterId>}]`.
**The workflow's `parentEntityId` binds the generation to the character.** Multi-step refinement = repeated
calls on the same `workflowId`, chaining `imageInputs` (BASE_IMAGE = prior output, REFERENCE = the face).

This is the SAME endpoint family gflow's existing image transport already drives — the new bits are
`tool:"PINHOLE"`, `imageInputs` types, and the `parentEntityId`/workflow binding.

## Entity binding: `entityContext` (captured live 2026-07-28)

**The generation request must carry `entityContext`, or Flow files the image as a
plain project image and the character stays empty.** Captured from Flow's own UI
via a CDP-attached real Chrome (`gflow-agent-browser-spike`, `navigator.webdriver:
false`) while driving the plain **New Character** flow.

`POST /v1/projects/{projectId}/flowMedia:batchGenerateImages`

```jsonc
{
  "clientContext": { "projectId": "…", "tool": "PINHOLE", "sessionId": "…" },
  "mediaGenerationContext": {
    "batchId": "…",
    "entityContext": {
      "entityId": "1e6c558e-87be-4aa7-8a21-ffb7efa43bbd",
      "characterSlot": { "imageReferenceIndex": 0 }   // 0 = portrait/face, 1 = body
    }
  },
  "useNewMedia": true,
  "requests": [ { "imageModelName": "NARWHAL", "structuredPrompt": {…}, "seed": …, "imageInputs": [] } ]
}
```

Response — bound on the first try:

```jsonc
"workflows": [ { "name": "30551aa7-…", "projectId": "…", "parentEntityId": "1e6c558e-…" } ]
```

### Which surface produces it

The composer Flow renders depends on whether the character already HAS a
portrait, not on which URL you arrived by:

| Character state | Composer placeholder | Sends `entityContext`? |
|---|---|---|
| Empty (no portrait yet) | *"Describe your character…"* | **Yes** — this is the creation composer, on `/characters` **and** on `/character/{entityId}` |
| Populated (portrait exists) | *"What do you want to change?"* | Edit surface — refines the existing portrait |

Both entry points reach the creation composer for an empty character, and both
bind. Verified live 2026-07-28: driving `/project/{id}/character/{entityId}` for
a pre-created empty entity produced `entityContext` in the request and
`parentEntityId` in the response — including for an entity gflow itself had
created minutes earlier. Entity age is not a factor.

`flow.createEntity` may be called by Flow (the **New Character** flow does it
itself, returning `displayName: "Untitled Character"`) or by the client
beforehand; gflow pre-creates and deep-links, which is fine. After the
generation Flow issues `PATCH /v1/flowWorkflows/{id}` twice
(`metadata.displayName`, then `metadata.primaryMediaId`) — the same commit gflow
already performs. Flow never asks for a name up front: the user renames
afterwards via the ✏️ next to the title, and "Character Info (optional)"
(*"Describe how your character acts…"*) is a separate free-text field.

**What actually broke gflow (#395)** was therefore NOT the choice of entry
point. Two client-side defects suppressed `entityContext` on a surface that
would otherwise have sent it:

1. **Overlay dismissal pressed Escape on the composer.** `[role='dialog']` and
   `[role='alert']` in the overlay detector matched Flow's own character
   composer (and the media picker), so gflow dismissed the app itself.
2. **The character route could bounce back to the project page** while the
   entity was not yet queryable. The project page also mounts a prompt box, so
   the readiness gate passed on the wrong surface and the prompt was typed into
   the **project** composer.

With both fixed, the deep-linked editor binds reliably. See
[LIVE_VERIFICATION_v0.45.0 §2](LIVE_VERIFICATION_v0.45.0.md).

## Gaps

1. ✅ **RESOLVED — Entity create.** `POST /fx/api/trpc/flow.createEntity` `{json:{projectId}}` →
   returns fresh `entityId` (empty `characterInfo`, `entityType:CHARACTER`). Then gen → PATCH workflows →
   PATCH entity. Captured `labs.google22.har` (new char "Personagem sem título", 2026-06-02 ~11:10).
2. ✅ **RESOLVED — Reuse / consumer field = `referenceEntities`.** Attach via the resource picker
   (`Pesquisar recursos` → **Personagens** → **Incluir no comando**), then generate. Captured
   `labs.google23.har` (video "Woman ordering espresso", 2 characters, 2026-06-02 ~10:20).

   **Video reuse — `POST /v1/video:batchAsyncGenerateVideoReferenceImages`** (async; poll
   `video:batchCheckAsyncVideoGenerationStatus`):
   ```
   mediaGenerationContext: { batchId, audioFailurePreference:"BLOCK_SILENCED_VIDEOS" }
   clientContext: { projectId, tool:"PINHOLE", userPaygateTier, sessionId, recaptchaContext{token} }
   requests: [ {
     aspectRatio: "VIDEO_ASPECT_RATIO_PORTRAIT",
     textInput: { structuredPrompt:{ parts:[{text}] } },
     videoModelKey: "abra_r2v_10s",                # "Omni Flash" R2V 10s
     seed, metadata:{},
     referenceImages: [ { mediaId, imageUsageType:"IMAGE_USAGE_TYPE_ASSET" } ],   # optional plain assets
     referenceEntities: [ { entityId } ]           # <-- THE CHARACTER REUSE FIELD (list → multi-character)
   } ]
   useV2ModelConfig: true
   ```
   Resp → `workflows[]`, `media[].mediaMetadata.requestData.videoGenerationRequestData`:
   `videoModelControlInput{ videoGenerationMode:"VIDEO_GENERATION_MODE_REFERENCE_TO_VIDEO",
   videoModelCapabilities:["VIDEO_MODEL_CAPABILITY_MULTI_REFERENCE"] }`, `videoGenerationEntityInputs:[{entityId}]`.
   reCAPTCHA-gated, **costs credits** (`remainingCredits` returned).

   *Image reuse* not separately captured but mirrors this (`referenceEntities` on the image path) — confirm
   during impl if `gflow image --character` is wanted; **video `--character` is fully specified.**

## Avatar (#123) vs Character (#145)

| | Avatar / likeness (held) | Character (build now) |
|---|---|---|
| Wire | `referenceLikenesses` | flow **entity** `entityType:CHARACTER` |
| Availability | region/Pro/A-B gated → `checkEligibility: REGION` | broadly available |
| Reusable refs | single identity | per-project, named, multi-image + voice + personality |
| Reuse from PR #123 | UI-automation attach pattern, CLI/API scaffolding, `OperationKind`, mode plumbing | same scaffolding, retargeted to entities REST + PINHOLE image-gen |

## Proposed gflow surface (`gflow character`)

- `gflow character create <name> --face-prompt "…" [--body-prompt "…"] [--voice gacrux] [--personality "…"] [--aspect landscape] [--model nano-banana-2]`
  → `createEntity` (0) → gen face (1) → optional gen body using face as REFERENCE (1) → PATCH primaryMediaId per workflow (2) → PATCH entity displayName/voice/personality/imageReferences (3).
    Generation via existing reCAPTCHA image transport; createEntity/list via tRPC session; workflow/entity PATCH via Bearer REST.
- `gflow character list` / `gflow character show <id|name>` → read `projectInitialData.entities`.
- `gflow character rm <id>` → entity delete (capture verb later).
- Reuse: **`--character <id>` (repeatable → multi-reference)** on `gflow video` → adds
  `referenceEntities:[{entityId}]` to `video:batchAsyncGenerateVideoReferenceImages` (R2V mode, async + poll).
  Same flag on `gflow image` pending an image-path confirm.

## Reuse from PR #123 (kittinan)

`api/image.py`, `api/video.py` (request flags), `cli_image.py`/`cli_video.py` (subcommand scaffolding),
`data/models.py` `OperationKind`, and the UI-automation attach pattern in `ui_automation_video.py`.
Credit to kittinan per the PR #123 / issue #145 comments.

## Data layer

New `OperationKind.CHARACTER`; persist entityId, workflowIds, primaryMediaIds, voice, personality so a
character is recoverable/reusable across sessions (mirrors scene persistence, migration pattern).
```
