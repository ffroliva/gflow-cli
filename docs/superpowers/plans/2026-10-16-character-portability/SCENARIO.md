# Scenario: portable characters — create with image + voice, export/import across projects and profiles

Inputs: spike [`2026-10-16-migrated-character-lifecycle-wire.md`](../../spikes/2026-10-16-migrated-character-lifecycle-wire.md);
`/gflow:predict` verdict **CAUTION 6/10** (transport C: passive `Zzl0ze` reads, UI-driven
writes observed by rpcid; no replayed RPCs, no self-minted reCAPTCHA). User decisions
2026-10-16: **export + import only** (no `copy`), bundle is a **plain folder**.

## Coverage map

| Dim | Active | Why |
|---|---|---|
| D1 Auth & session | ✅ | import runs under a *different* `--profile`; expired session on either side |
| D2 WAF / reCAPTCHA | ✅ | the editor's Upload mints a token; profiles already see unusual-activity (#906) |
| D3 Selectors / locale | ✅ | name input, voice picker, personality textarea, editor Upload — `fr` account measured |
| D4 Batch & resume | ✅ | import is a multi-step saga: create → name → uploads → voice/personality |
| D5 Concurrency / page pool | ✅ | one page held for the whole saga; rpc listeners page-scoped |
| D6 Data layer | ✅ | saga rows (`record_character_*`), #737 entity id loss |
| D7 Errors / exit codes | ✅ | reuse 11 / 27 / 10 / 36; partial-import orphan reporting |
| D8 Cross-platform paths | ✅ | bundle folder written on Linux, read on Windows; UTF-8 names |
| D9 Transport | ✅ | `Zzl0ze` slot shapes, upload `maseQ` lost reply (#719), signed-URL expiry |
| D10 Headed vs headless | ➖ | unchanged: same headed Chrome as every migrated path |
| D11 Input validation | ✅ | untrusted bundle content: schema, voice, sizes, magic bytes, text |
| D12 Observability | ✅ | new `character.export.*` / `character.import.*` events; redaction of rpc bodies |
| D13 MCP parity | ✅ | `gflow_character_export` / `_import`, `create` (#691), read twins already exist |

## Scenario table

| # | Dim | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D9 | `character list` on a flow.google.com-served profile | **Critical** | parsed from the `Zzl0ze` reply observed on project load (slot 5); exit 0; no labs `projectInitialData` call | Unit (parser) + E2E (`e2e_character`) |
| 2 | D9 | `Zzl0ze` reply shape drifts (slot 5 not a list of `[project, entity, …]`) | High | `WireFormatError` exit 7 naming `batchexecute:Zzl0ze`, redacted head; never an empty list reported as "no characters" | Unit |
| 3 | D9 | two `Zzl0ze` frames on one load (re-init) | Medium | first frame for list/show; export re-reads immediately before downloading (signed URLs expire) | Unit |
| 4 | D7 | `show --name X` where two characters share the name | High | `ConfigurationError` exit 11 listing ids; unchanged from labs | Unit |
| 5 | D11 | `--voice enceladus` (live, missing from the hardcoded list) | High | accepted when present in the live catalog; `VOICES` gains Enceladus; `character voices` stays offline | Unit |
| 6 | D11 | voice case: `Aoede` / `aoede` / `AOEDE` | High | one canonical name in our model and bundle; lowercased only in the migrated wire adapter; labs keeps capitalized | Unit |
| 7 | D3 | name / personality / voice set through the editor on a non-English UI | **Critical** | structural anchors only (`input.name-input`, `textarea.personality-textarea`, voice picker by component/ligature); success read from the `rzMKMb` reply's mask + echoed value, not from the DOM | Unit (mocked page) + E2E |
| 8 | D9 | `rzMKMb` reply echoes a different value than requested | High | `WireFormatError` exit 7; the saga does not report success | Unit |
| 9 | D9 | `create --image P` on a gflow-created entity | **Critical** | uploaded through the **editor's own** Upload; `maseQ` reply's trailing entity id == ours; a fresh `Zzl0ze` shows the workflow in the entity's image refs | Integration + E2E |
| 10 | D9 | upload `maseQ` reply never arrives (#719, ~1 in 4) | **Critical** | re-read `Zzl0ze`; if the image IS bound → continue; if not → one retry; never a blind retry that double-binds | Unit (both branches) + E2E opportunistic |
| 11 | D4 | import fails after `C4BZMd` (upload refused, timeout, crash) | **Critical** | rollback via `Zzl0ze` check + `cz8Z4b` delete when nothing bound; name is set **first** so a surviving orphan is identifiable; if rollback fails the error/JSON carries the orphan `entity_id` | Unit (saga) |
| 12 | D4 | existing labs saga rollback on a migrated host | **Critical** | today it reads labs `projectInitialData` (retired) → `_entity_has_bound_work` True → never deletes. Rollback goes through the host-branching client methods | Unit |
| 13 | D7 | upload refused by Flow (policy / unsupported image) | High | `MediaUploadRejectedError` exit 27 with the file named; entity rolled back | Unit |
| 14 | D2 | reCAPTCHA / unusual-activity on the editor Upload | High | exit 10 path unchanged; **no retry** on 403 (ACCOUNT_SAFETY); entity rolled back | Unit |
| 15 | D11 | bundle with unknown `schema_version` | High | `ConfigurationError` exit 11 before any network call | Unit |
| 16 | D11 | bundle image is not an image / >20 MB / sha256 mismatch | High | refused before `C4BZMd` (`validate_image_file`, `MAX_IMAGE_BYTES`, sha256) — exit 11 | Unit |
| 17 | D11 | manifest `images[].file` = `../../etc/passwd` or absolute | **Critical** | refused: every image path must resolve inside the bundle folder | Unit |
| 18 | D11 | name/personality with control, bidi or zero-width chars, or oversized | Medium | stripped/limited on import; MCP output labels them as bundle data | Unit |
| 19 | D11 | bundle voice not in the target account's catalog | High | exit 11 naming the voice and suggesting `--voice`; `--voice` overrides | Unit |
| 20 | D1 | import with `--profile B` while B's session is expired | High | existing auth error path before any write; nothing created | Unit |
| 21 | D1 | export from A and import into B in one shell sequence | High | two separate processes/clients; each takes its own lease; never two profiles open at once inside gflow | E2E (two profiles) |
| 22 | D8 | bundle written on Linux, imported on Windows; name `Laura é` | High | fixed filenames (`manifest.json`, `portrait.<ext>`, `body.<ext>`); manifest UTF-8, `ensure_ascii=False`; display name never used as a path | Unit |
| 23 | D8 | `--out` folder exists and is non-empty | Medium | refuse (exit 11) unless it is an earlier bundle of the same character? → **refuse**, keep it simple | Unit |
| 24 | D11 | export leaks account identity | **Critical** | manifest allowlist: name, voice, personality, images (role, file, sha256, mime). No signed URL, no email; source project/entity ids omitted | Unit |
| 25 | D12 | rpc bodies or replies reach logs / incident bundles | **Critical** | redaction covers `0cAF…` reCAPTCHA tokens and the `at` XSRF value; only rpcid + lengths logged | Unit |
| 26 | D6 | create/import saga rows carry `entity_id` + name (#737) | High | `operations.metadata_json` holds both on success | Unit |
| 27 | D5 | import holds one page for the whole saga; concurrency > 1 elsewhere | Medium | listeners page-scoped; no per-step checkout | Integration |
| 28 | D3 | labs-served profile asks for `create --image` / `import` | High | labs arm kept; refused with `FlowHostMigratedError`-family wording "not ported to the labs composer yet" — **never** "migrated-only" (host-membership discipline) | Unit |
| 29 | D9 | export on a character whose image is a generated (not uploaded) portrait | High | same download path; the bundle does not care how the image was made | E2E |
| 30 | D9 | export of a character with **no** image | Medium | refused, exit 11: import needs ≥1 reference image (REFERENCE_STRATEGIES.md:117) | Unit |
| 31 | D13 | `gflow_character_export` / `gflow_character_import` | **Critical** | inline (no worker), not in `_SPEND_TOOLS`; paths are server-filesystem strings, docstring says so; same validation and errors as CLI | Unit + MCP e2e |
| 32 | D13 | `gflow_character_create` (#691) | High | file-built create is free and exposed (**done**, live ✅); prompt-generated create stays GAP #691 | Unit + MCP e2e |
| 33 | D13 | `gflow_character_list/show` docstrings | Medium | still truthful once the migrated arm exists; `gflow_character_voices` keeps "static lookup" | Unit (docstring assertions) |
| 34 | D9 | does the imported character keep its identity on B | High | `image t2i "@Name …"` on B attaches the entity (#723 path) and generates — image quota only | E2E (`e2e_character` + `e2e_image`) |
| 35 | D9 | does the voice reach rendered audio | Low (here) | **not claimed**: docs say "attached, not proven applied" (#738) | — |

## Must-cover before merge (Critical + High)

Rows 1, 2, 4–17, 19–22, 24–26, 28, 29, 31, 32, 34.

## Deferred (Medium + Low)

- 3 (double `Zzl0ze`) and 27 (page pool) — covered by construction; test if cheap.
- 18 (text sanitising) — implement the strip; full fuzzing is not a blocker.
- 23 (non-empty `--out`) — refuse; no merge semantics.
- 30, 33 — small, do them.
- 35 — #738, out of scope.
- Custom voice creation — #826.

## Suggested BDD scenarios

Browser-only behaviour → `tests/e2e/test_character_portability_bdd.py`:

```gherkin
@e2e @e2e_character
Feature: Portable characters on a flow.google.com-served profile

  Scenario: Create a character from local images with a voice
    Given a throwaway project on profile A
    When I run "gflow character create --image portrait.jpg --body-image body.jpg --name Laura --voice aoede --personality soft"
    Then the command exits 0
    And "gflow character show --name Laura --json" reports voice "Aoede" and 2 images

  Scenario: Export then import into another project on the same profile
    Given a character "Laura" with an uploaded portrait on profile A
    When I export it to a bundle folder
    And I import the bundle into a second project on profile A
    Then the second project lists "Laura" with the same voice and personality
    And the imported portrait's sha256 matches the bundle

  Scenario: Import into another profile
    Given a bundle exported from profile A
    When I import it into a project on profile B
    Then profile B lists "Laura" with the bundled voice

  Scenario: A failed import leaves no orphan
    Given a bundle whose image Flow refuses
    When I import it
    Then the command exits 27
    And the target project lists no new character
```

Our-code behaviour → `tests/features/character_bundle.feature`, bound from
`tests/features/test_character_bundle_steps.py` (no tags):

```gherkin
Feature: Character bundle validation

  Scenario: An image path escaping the bundle is refused
    Given a bundle whose manifest names "../secret.jpg"
    When I validate it
    Then it is refused with exit code 11 before any network call

  Scenario: An exported manifest carries no account identifiers
    Given a character read from a project
    When I write its bundle
    Then the manifest has only name, voice, personality and images
    And it contains no URL, email, project id or entity id
```

## Known-issues cross-reference

- `KNOWN_ISSUES.md:50` (character create/list exit 7 on flow.google.com) — **resolved** by rows 1, 7, 9.
- #719 (upload loses its reply ~1 in 4) — **mitigated** by row 10 (verify-then-retry-once).
- #737 (entity id lost from saga metadata) — **resolved** by row 26.
- #691 (MCP `character create` unported) — **resolved** by row 32.
- #723 (entity `@` mention) — relied on for row 34 (image path only; video stays gated).
- #738 (voice applied to audio) — explicitly **not** claimed (row 35).
- #906 / PR #907 (unusual-activity / WAF) — writes are UI-driven to stay inside the app's own behaviour; verify with and without #907 if it merges first.

---

## Addendum — `--character FOLDER` on image generation (option C, 2026-10-16)

User decisions: generate directly from a bundle in any project/account; a character already
in the project **with the bundle's name is reused** (by name, even if it differs); images now,
video after Task 6b. No `/gflow:predict`: no transport/auth/selector/schema change — a new
option over services already live-verified (read, import, `@Name` attach).

| # | Dim | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| C1 | D11 | `--character nora/` and the project has no "Nora" | Critical | bundle validated offline, imported (free), its id+name added to the request's character references, image generated with it | Unit + E2E |
| C2 | D11 | the project already has exactly one "Nora" | High | reused by name, nothing imported (user decision: even if it differs from the bundle) | Unit + E2E |
| C3 | D11 | the project has two characters named "Nora" | High | exit 11 listing both ids — never a guess | Unit |
| C4 | D11 | a bad bundle (path escape, sha mismatch, missing) | Critical | exit 11 before any project is created or any Flow call | Unit |
| C5 | D11 | the bundle's voice is not offered by the target account | High | exit 11 before anything is created (import's rule) | Unit |
| C6 | D4 | no `--project` | High | the project is created first, then the import, then the generation (one command) | Unit + E2E |
| C7 | D13 | MCP `gflow_generate_image(characters=[...])` | Critical | the payload key the tool writes is the key the worker reads; the worker applies the bundles after resolving the project | Unit (codec round trip) + MCP E2E |
| C8 | D9 | the just-imported character is not yet offered by the `@` picker (read lag) | High | the existing attach retries the asset search; if it still fails → exit 32 naming the character, and the orphan-free path holds (the character exists, the generation did not run) | E2E (observe) |
| C9 | D11 | `--character` with `@Name` of the same character in the prompt | Medium | deduped by entity id (existing rule) | Unit |
| C10 | D11 | multi-word name ("Nora from B") | Medium | attach by exact option title; unmeasured with spaces → measure live, refuse cleanly if the picker cannot find it | E2E (observe) |
| C11 | D11 | video `--character` | — | not offered until Task 6b; the option does not exist on video commands | — |
