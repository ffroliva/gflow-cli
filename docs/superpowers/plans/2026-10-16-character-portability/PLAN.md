# Portable Characters Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature character-portability` to find the
> next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** On a profile Flow serves from flow.google.com, a user can create a character from
their own images with a voice, export it to a plain folder, and import that folder into any
project on any profile — from the CLI and from MCP.

**Architecture:** A new `api/transports/migrated_character.py` drives the character editor
and *observes* the app's own replies (`Zzl0ze` read on project load; `C4BZMd` / `rzMKMb` /
`maseQ` / `cz8Z4b` on writes) — transport C, the pattern `create_project` already uses; no
replayed RPC, no self-minted reCAPTCHA. The existing `FlowApiClient` character methods gain a
host branch (`create_project`-style), so `cli_character.py` and the create saga keep calling
the same methods. The bundle is a pure module (`character_bundle.py`, no Flow imports); export
and import live in `services/character_portability.py` against a small backend Protocol. The
labs arm stays and is never described as "labs-only".

**Predict verdict:** CAUTION — 6/10 (five personas 7/10, all on transport C; −2 for the
simpler shape: no `copy`, no zip). Scenario: [SCENARIO.md](SCENARIO.md). Evidence:
[spike 2026-10-16](../../spikes/2026-10-16-migrated-character-lifecycle-wire.md).

**User decisions (2026-10-16):** export + import only (no `copy`); bundle = plain folder.

**Out of scope:** custom voice creation (#826); proving the voice reaches rendered audio
(#738 — docs say "attached, not proven applied"); video `@Name` submit (#723).

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| Critical | Saga rollback reads retired labs routes → orphans on flow.google.com | No delete on this host (user decision; REST delete 401, confirm unanchorable): a failed create/import reports the orphan's `entity_id`; the name is patched immediately after create so the orphan is identifiable (Task 5) |
| Critical | #719: upload reply lost ~1 in 4; a lost reply is **still bound** (measured) | Re-read `Zzl0ze` once it reflects the last write; retry at most once, only if unbound (Task 6) |
| High | Reads lag writes by seconds (measured) | Write outcomes come from `rzMKMb`/`maseQ` replies; decision-driving reads poll until `updated` ≥ last write |
| Critical | Untrusted bundle: path escape, non-image, oversize, hostile text | Pure validator before any network call (Task 7) |
| Critical | rpc bodies (`0cAF…` tokens, `at`) leak to logs / incident bundles | Redaction patterns + tests first (Task 1) |
| High | Export leaks account identity | Manifest allowlist; no URL, email, project or entity id (Task 7) |
| High | Upload-into-entity semantics only half measured (`[1]`, portrait vs body, lost-but-bound) | Measurement gate before code (Task 0) |
| High | Editor selectors drift / locale | Structural anchors only; success read from the rpc reply, not the DOM |
| High | WAF unusual-activity (#906, PR #907) | UI-driven writes only; refuse, never retry, on 403; verify with and without #907 |
| Medium | MCP twins drift from CLI | Task 9; six mirror axes in `skills/check/SKILL.md` step 1b |

---

## File structure

### New files
```
src/gflow_cli/api/transports/migrated_character.py
  editor-driven create/patch/upload/delete + passive Zzl0ze read, each confirmed by its rpcid
src/gflow_cli/api/migrated_project_read.py   (or inside the transport — decide in Task 2)
  pure Zzl0ze parser -> Character / Voice / image refs / media download info
src/gflow_cli/character_bundle.py
  pure: CharacterBundle, BundleImage, schema_version, write_folder / read_folder / validate
src/gflow_cli/services/character_portability.py
  export_character / import_character saga against a CharacterBackend Protocol
scripts/dev/spike_migrated_character_upload_binding.py
  Task 0 measurement gate
tests/api/test_migrated_project_read.py, tests/api/transports/test_migrated_character.py,
tests/test_character_bundle.py, tests/services/test_character_portability.py,
tests/features/character_bundle.feature + tests/features/test_character_bundle_steps.py,
tests/e2e/character_portability.feature + tests/e2e/test_character_portability_bdd.py,
tests/api/fixtures/zzl0ze_reply.json   (redacted)
```

### Modified files
```
src/gflow_cli/api/client.py           host branch in create_entity / list_characters / get_character /
                                      patch_entity / delete_characters; new upload_character_image
src/gflow_cli/api/character.py        VOICES += Enceladus; wire-case helper
src/gflow_cli/services/character_create.py  file-built create; rollback via host-branching methods
src/gflow_cli/cli_character.py        create --image/--body-image (XOR with prompts); export; import
src/gflow_cli/mcp/tools.py            gflow_character_export / _import / _create (#691)
src/gflow_cli/mcp/server.py           spend classification for prompt-built create
src/gflow_cli/redaction.py (+ data/redaction.py)  0cAF… token + at= patterns
src/gflow_cli/data/recorder.py        entity_id + name on completed rows (#737)
tests/mcp/test_cli_parity.py          new leaves mapped; create exemption removed
docs: CHARACTER.md, USAGE.md, MCP.md, REFERENCE_STRATEGIES.md, KNOWN_ISSUES.md:50,
      skills/gflow-cli/SKILL.md, AGENTS.md command bullet, website/docs (generated), CHANGELOG.md
```

---

## Task 0 — Measurement gate: upload-into-entity on a gflow-created character (spike)

**What:** Settle the three unmeasured facts the import design rests on, $0 + a little image quota.

**Files:** `scripts/dev/spike_migrated_character_upload_binding.py`; spike doc addendum.

**Steps:**
- [x] Inventory the editor's structural anchors (new-character control, name input, personality
      textarea, voice picker + option rows, Upload control, delete control) — tags, ligatures, roles
- [x] Upload two files into one entity via the editor's own Upload; record both `maseQ` requests in
      full; settle what `[1]` is and which ref is portrait vs body — **`[N]` = slot index (0 portrait, 1 body)**
- [x] Abort one upload's reply in flight (route) and re-read `Zzl0ze`: is a lost-reply upload bound?
      — **yes, on both accounts**; and reads lag writes by seconds
- [x] Download the images from profile A, upload them into a new character on profile B, then one
      `image t2i "@Name …"` on B (image quota) — identity carries over? — **yes**
- [x] Append findings to the 2026-10-16 spike doc with pre-registered readings

**Gate result (2026-10-16): PASS.** See the spike doc's addendum. Corrections it forces on this
plan are applied below: create = footer Upload (no name field before the first image); body =
slot-preview Upload (the prompt add-menu Upload does not bind); export downloads with `=s0`; any
decision-driving re-read waits for the last write's `updated`; bundle sha256 is bundle-only.

**Gate:** if uploads are not bound on a gflow-created entity, or Flow refuses uploaded faces at
generation time, STOP and re-run `/gflow:predict` on the import design.

---

## Task 1 — Redaction hardening (red → green)

**What:** batchexecute bodies/replies never reach logs or incident bundles with secrets.

**Steps / tests:**
- [x] red: `0cAF…` reCAPTCHA token and `at=` XSRF value are masked by the free-text redactor
      (+ `lh3…/asb/` capability links, + any 120+ token-alphabet run; `at` shape measured first)
- [x] red: a `WireFormatError` built from a `maseQ`/`Zzl0ze` head carries no token, URL signature or email
      — found `_discovery_head` sliced BEFORE scrubbing (fragments survived); now scrub-then-slice
      like `_common._redacted_snippet`
- [x] implement patterns in `redaction.py` / `data/redaction.py`

---

## Task 2 — `Zzl0ze` parser (pure) + live voice catalog

**What:** Parse the project read into existing domain types.

**Steps / tests:**
- [x] redacted fixture `tests/api/fixtures/zzl0ze_reply.json` from the 2026-10-16 capture
- [x] red: slot 5 → `Character` (entity_id, display_name, project_id, workflow_ids, voice, personality, thumbnail)
- [x] red: slot 3 → `Voice` list (30, includes Enceladus); slot 1/2 → per-workflow media id + download URL
- [x] red: drifted shape → `WireFormatError` exit 7 naming `batchexecute:Zzl0ze` (scenario 2)
- [x] red: voice wire-case: canonical `Aoede` ↔ wire `aoede` (scenario 6)
- [x] `VOICES` += Enceladus (`character voices` stays offline — its docstring promises it);
      "29 voices" claims updated in CHARACTER.md, USAGE.md, video-production skill (+ mirrors)

Decided: the parser lives in `api/transports/migrated_character.py` (pure functions; the
editor-driving functions of Tasks 4–6 join it). `batchexecute._discovery_head` → public
`discovery_head` so this module reuses the scrub-then-slice head.

---

## Task 3 — BDD scaffolds (red)

**Steps:**
- [x] `tests/features/character_portability.feature` (`@e2e @e2e_character`) — feature files live in
      `tests/features/` (the guard scans one directory); 5 scenarios: create-from-files + read back,
      MCP list, export→import same account, import on account B, MCP export→import
- [x] `tests/e2e/test_character_portability_bdd.py` binding; opt-in `GFLOW_CLI_E2E_RUN_CHARACTER=1`;
      projects created fresh by `gflow project create`; `GFLOW_CLI_E2E_PROFILE_B` documented in
      docs/E2E_TESTING.md (with the previously undocumented RUN_CHARACTER). **Red, measured live**:
      scenario 1 fails on `No such option '--image'` after its project create succeeded
- [x] `tests/features/character_bundle.feature` + steps (offline) — **moved to Task 7**, done there: its step module
      imports `character_bundle`, and a committed import error would break normal CI collection
- [x] `tests/features/test_e2e_binding_guard.py` stays green

---

## Task 4 — Read path on the migrated host: `list` / `show`

**What:** `client.list_characters` / `get_character` branch on the served host; migrated arm =
navigate to the project and observe `Zzl0ze` (listener attached before `goto`, via `_rpc_reply`).

**Steps / tests:**
- [x] red: integration with a fake page emitting a `Zzl0ze` frame → list parsed; no labs call made
- [x] red: labs-served arm unchanged (existing tests stay green)
- [x] implement in `migrated_character.read_project`; client host branch (`create_project`'s
      observed-refusal rule; `migrated_composer._rpc_reply` → public `rpc_reply`)
- [x] e2e: `character list` / `show` on a flow.google.com-served profile (Iron Law) — CLI live
      (6/6 characters, exit 0) and MCP live (`test_character_mcp_parity_e2e.py` 3/3); image
      `@Name` re-run live (now resolves via the catalog, guard passed 1 char / 0 media);
      video `@Name` now exit 36 at the gate instead of 29
- [x] docs: KNOWN_ISSUES:50, REFERENCE_STRATEGIES flow.google.com section, CHANGELOG Fixed

---

## Task 5 — Writes: name / voice / personality through the editor; orphan reporting; #737

**Scope change (2026-10-16, user decision):** delete is **out**. Measured: the aisandbox REST
writes (`patch_entity`, `delete_characters`) answer 401 on a flow.google.com account, and the
editor's delete confirm has two structurally identical buttons. A character on this host only
comes into being with its first image, so create lives in Task 6 (files) and Task 6c (prompt).

**What:** `patch_character` drives the editor's name input, personality textarea and voice
picker, each confirmed from its own `rzMKMb` reply (mask + echoed value). `client.patch_entity`
falls back to it on the measured REST refusal (or when flow.google.com is pinned). A failed
create/import never deletes: the error carries the orphan's `entity_id` (it is named right after
creation, so it is identifiable in Flow).

**Steps / tests:**
- [x] red: `rzMKMb` echo mismatch → `WireFormatError` (scenario 8) — pure verifier
- [x] red: `patch_entity` falls back to the editor on the REST 401 under `auto`, never when
      labs.google is pinned, never on an unrelated failure
- [x] a field already equal to the requested value is not re-typed (no reply would come) —
      covered live by the e2e's restore step, not offline (faking Playwright locators would
      assert our model of the editor, which is what the e2e exists to check)
- [x] red: completed saga row carries `entity_id` + name (scenario 26, #737) — root cause:
      `update_operation_metadata` replaced the column; now `json_patch` merges (its only caller)
- [x] implement `patch_character` in `migrated_character.py`; client branch in `patch_entity`
- [x] e2e `tests/e2e/test_character_patch_migrated_e2e.py`: rename + voice + personality, read
      back after the read lag, restored — **3/3 live** on a flow.google.com profile. Found on the
      way: the editor's name field is visible but empty ~1.5 s before the app loads it; typing
      earlier is overwritten (`_wait_form_loaded`)
- [ ] orphan reporting → **moved to Task 6/6c**: on this host only those paths create an entity

---

## Task 6 — `character create --image / --body-image`

**What:** Build a character from local files via the editor's own Upload.

**Steps / tests:**
- [x] red: `--image` XOR `--face-prompt`, `--body-image` XOR `--body-prompt`; `--model`/`--format-prompt`
      rejected with `--image` (exit 2, Click usage)
- [x] red: order = footer Upload creates (slot 0) → name → personality → body slot Upload (slot 1) → voice
- [x] red: the prompt add-menu Upload is never used for a character image (it does not bind)
- [x] lost upload reply → re-read; bound → continue; unbound → one retry; still unbound → `MediaUploadRejectedError`
      (scenario 10; **no rollback** — delete was scoped out, the orphan id is reported). Offline tests
      added after the council (`test_migrated_character_decisions.py`); a live lost reply was only
      exercised by the spike, never by the e2e
- [ ] upload refused → exit 27 (13); 403 → exit 10, no retry (14) — **not tested**: no measured refusal
      shape to fake, and none reproducible live at $0. The existing transport error classes apply;
      no rollback (orphan id reported instead)
- [x] labs-served project → `ConfigurationError` "not ported yet" (28) — offline test added after the
      council; no labs-served account exists here to run it live
- [x] implement — as `create_from_files` / `_upload_body` in `migrated_character.py` and
      `client.create_character_from_files` (the names first planned were never used)
- [x] e2e: create from two files with `--voice aoede --personality …`; `show` reports both images

**Done 2026-10-16.** Live: `test_character_portability_bdd.py` scenario 1 (create from two files
with voice + personality, read back) **2/2**, scenario 2 (MCP list of that character) 1/1.
Labs-served host refusal is `ConfigurationError` (exit 11), wording "not ported yet". Orphan
reporting: a failure after `C4BZMd` adds the entity id to the error's remediation hint (no
delete). Offline coverage of the editor-driving code is by the live e2e, not by a faked page.

---

## Task 6c — Create a character from a prompt on flow.google.com

**What:** `character create --face-prompt …` exits 7 there today (labs `createEntity` is
retired). On this host a character is created by its first image: type the face prompt into the
draft page's prompt box (`/project/<id>/character`) and generate — `C4BZMd` then `ogiZ0b`, and
the page moves to `/character/<new id>`. Then the Task 5 patches. Body generation reuses the
existing editor flow (`_CHARACTER_BODY_MODE_SELECTOR`).

**Steps / tests:**
- [ ] spike the draft page's generate gesture (image quota only): which rpcs, and does the URL
      move to the new entity
- [ ] red/green: saga branch — no `create_entity` on this host; the entity id comes from `C4BZMd`
- [ ] e2e: `character create --face-prompt … --voice …` on a flow.google.com profile

---

## Task 6b — Lift the video character-reference gate (#723), after re-measuring it

**What:** `_unported_form` refuses `reference_entities` (exit 36) because `MZZa6b` was measured
with a NULL payload in 2026-09. On 2026-10-16 one run with the gate lifted in-process returned the
media id and completed (spike Addendum 2, 12 credits). One run is "not reproduced", not "fixed".

**Steps / tests:**
- [x] re-measure: 6 entity-bound t2v runs on 2026-10-17 (user-requested café video, 4 clips, plus
      both video e2e scenarios), each with TWO characters; every `MZZa6b` reply named the media id —
      the NULL payload did not reproduce (6/6 named; not reproduced ≠ disproved)
- [x] red: a NULL `MZZa6b` payload (`[5]`, no record) falls through to the app's status poll: the
      first status record for a workflow not already pending is adopted
      (`test_a_queued_submit_adopts_the_new_clip_its_status_poll_names`). Not hit live yet.
- [x] red: `_unported_form` no longer refuses NAMED `reference_entities` (an id without a name is
      still exit 36). `migrated_can_serve` keeps refusing them on purpose: it only routes an
      account served labs, whose driver attaches characters by id.
- [ ] video `@Name` resolves through the Task 4 read path — offline path shared with images; not
      run live on video (the live runs used `--character`)
- [x] e2e (`e2e_video`, opt-in): two-character t2v completes with both attached — CLI across
      accounts and MCP through the queue (`test_character_portability_bdd.py`, 2/2 live)

---

## Task 13 — `--character FOLDER` on video, several per clip; create reliability (2026-10-17)

- [x] `apply_character_bundles` typed for image and video requests
- [x] `gflow video t2v|r2v --character` (repeatable), `gflow_generate_video(characters=…)`, the worker
      reads `payload["characters"]` and creates a project when none is named; i2v refuses it
- [x] create-from-files on a cold profile: recover a lost `C4BZMd`, retry an empty create once,
      clear overlays, settle the editor, redo an unanswered edit once, confirm the voice only when
      enabled, accept a voice the editor shows after a lost confirm. 8/8 cold creates.
- [x] `-o` / MCP `output` across filesystems (`shutil.move`, was EXDEV → exit 1 on a billed clip)
- [x] live: 32 s, 4-beat café dialogue with Nora + Mini (vraisykamel), transcripts word for word

---

## Task 7 — `character_bundle.py` (pure)

**What:** Folder format: `manifest.json` + `portrait.<ext>` [+ `body.<ext>`].

Manifest (allowlist): `{schema_version: 1, name, voice, personality, images: [{role, file, sha256, mime}]}`.

**Steps / tests:**
- [x] red: write → read round-trip; UTF-8 name, `ensure_ascii=False`; fixed filenames (22)
- [x] red: unknown schema_version (15); path escape / absolute (17); non-image, >20 MB, sha mismatch (16)
- [x] red: control/bidi/zero-width stripped, length limits (18)
- [x] red: manifest contains no URL / email / project or entity id (24)
- [x] red: `--out` exists and non-empty → refused (23)
- [x] implement; offline BDD from Task 3 goes green

**Done 2026-10-16.** The image rule (four containers, 20 MB, extension from the bytes) moved
from `api/client.py` into `gflow_cli/image_files.py` so the upload and the bundle refuse the
same files. Offline BDD `tests/features/character_bundle.feature` (moved here from Task 3).
Text over its limit is refused, not truncated.

---

## Task 8 — Export / import service + CLI

**What:** `gflow character export --project P (--id|--name) --out DIR [--profile] [--json]` and
`gflow character import DIR --project P [--profile] [--name NEW] [--voice V] [--json]`.

**Steps / tests:**
- [x] red: export re-reads `Zzl0ze` then downloads at once, with `=s0` (bare FIFE = 288×512 preview),
      through the `_validate_fife_url` allowlist (add `lh3.googleusercontent.com/asb/` if absent)
- [x] red: bundle sha256 is checked against the bundle file only — Flow re-encodes uploads
- [x] character with no image → exit 11 (30) — offline test added after the council
- [x] red: import validates the bundle fully before any network call; voice missing from target
      catalog → exit 11 with `--voice` hint (19); expired target session → no writes (20)
- [x] red: import reuses the Task 6 saga (create from files) — no second code path
- [x] `--json` shapes: export `{status, bundle, character, images}`, import `{status, project, character}`;
      consolidate `_char_to_dict` / `_character_to_dict` first
- [x] e2e: export → import into another project (same profile); export on A → import on B — live ✅
- [ ] e2e "a failed import leaves no orphan" — **not written**, and superseded: delete was scoped out,
      so a failed import cannot leave none; it reports the orphan's id (offline test for the hint added)

**Done 2026-10-16.** Export downloads through an exact check (`original_image_url`: https,
`lh3.googleusercontent.com`, `/asb/`) instead of widening the shared `_validate_fife_url`.
Import reads the target's LIVE voice catalog before creating anything (`client.list_voices`),
so an unknown voice is exit 11 with no orphan. The parity gate refused the new leaves, so the
MCP twins `gflow_character_export` / `gflow_character_import` landed here, not in Task 9.
**Live, all on flow.google.com profiles:** export→import same account ✅, export (sykamel) →
import (vraisykamel) ✅, MCP export→import ✅ — plus Task 6's scenarios: the whole
`character_portability.feature` (5/5) has passed live.

---

## Task 9 — MCP surface mirror

**Steps / tests:**
- [x] `gflow_character_export`, `gflow_character_import` — inline, not in `_SPEND_TOOLS`; paths are
      server-filesystem strings (docstring says so); bundle text labelled as data — **done in Task 8**
- [x] `gflow_character_create` (#691): the file-built path, free — ported after the council, live ✅;
      prompt-built create stays a tracked GAP #691 (option exemptions in the parity test)
- [x] `tests/mcp/test_cli_parity.py`: map export/import/create; #691 blanket exemption replaced by
      per-option GAP entries for the prompt-only options; options gate green
- [x] docstrings of `gflow_character_list/show` truthful for both hosts (no host claim); `voices` keeps "static lookup"
- [x] six mirror axes walked (`skills/check/SKILL.md` step 1b) — by council D15
- [x] MCP e2e: export + import through the tools (separate surface → separate run) — live ✅

---

## Task 12 — `--character FOLDER` on image generation (option C)

**What:** generate directly from a character bundle in any project on any profile:
`gflow image t2i "…" --character nora/ [--project P]`. The shared
`services/character_portability.apply_character_bundles` validates each bundle offline,
reuses a same-named character in the project (user decision) or imports it, and adds its
id + name to the request's character references — then the existing `@Name` attach runs.
Called from `cli_image._run_t2i`, `_run_i2i` and the worker's image task (MCP queue).
Scenario rows C1–C11 (SCENARIO.md addendum). Video waits for Task 6b.

**Steps / tests:**
- [x] red: apply_character_bundles — import when absent (C1), reuse when one exists (C2),
      refuse when ambiguous (C3), bad bundle before any Flow call (C4), dedupe with a mention (C9)
      (C5, voice refusal, is import_character's own test)
- [x] red: CLI `--character` (repeatable) on t2i and i2i reaches the runner; single-prompt
      only on t2i; counted in i2i's reference budget (C6 creation path: live, below)
- [x] red: MCP `gflow_generate_image(characters=…)` → `payload["characters"]` → the worker
      applies it after resolving the project (C7, both sides pinned)
- [x] implement; parity gate green (`characters` on both surfaces)
- [x] e2e (live, sykamel → vraisykamel): CLI t2i `--character` with no project (import) and
      again into that project (reuse, still listed once), asserting the character was
      attached to the generation; MCP twin through the queue, same assertion
- [x] docs: USAGE (t2i/i2i), MCP.md, CHARACTER.md, CHANGELOG, gflow-cli skill
- [ ] C8/C10 (picker lag right after import; multi-word names) — not observed failing; the
      e2e names are one word. Unmeasured, not claimed.

**Found by this task's live runs and fixed:**
1. `ensure_editor` / `rename_project` compared routes by PREFIX: a page left on
   `/project/<id>/character/<eid>` passed as the project, so image generation drove the character
   editor (misreported as agent-only, exit 25) and a rename would have typed into the
   character's title. Now `migrated_composer.is_at` (exact host+path).
2. One `character create` timed out on `rzMKMb` (not reproduced in 5 runs), while the in-app
   editor after create needed a reload in 4 of 5. The create now always reopens the editor by
   direct navigation: 5/5 clean.

---

## Task 10 — Docs

- [x] `docs/CHARACTER.md` (30 voices, files-built create, export/import recipe incl. cross-profile, voice "attached, not proven applied"; lowercase wire voice ids on flow.google.com)
- [x] `docs/USAGE.md`, `docs/MCP.md`, `docs/REFERENCE_STRATEGIES.md`, `skills/gflow-cli/SKILL.md`, AGENTS.md command bullet, README
- [x] `KNOWN_ISSUES.md:50` — list/show/create --image/export/import ported, `--face-prompt` and `rm` not; #639 comment drafted (Refs, not Closes) — posting awaits the user
- [x] `uv run python scripts/ci/generate_website_docs.py`; CHANGELOG `[Unreleased]`

---

## Task 11 — Gates + verification

- [x] `/gflow:check` green — incl. 4c duplication proxy (3 pre-existing pairs, none in new code)
- [x] `pytest -m e2e` for `e2e_character` (+ `e2e_image` for the `@Name` identity check) on two profiles —
      consolidated run 13/13 at 12253fac
- [x] `/gflow:branch-review` — re-run at 80de9b39 after the limit cleared: consensus YELLOW (D0, D12
      GREEN), 12 must-fixes, all addressed (`.planning/branch-review-…md`, gitignored)
- [x] `/gflow:live-verify` ledger (`tmp/live-verify/character-portability.md`, gitignored). Found and
      fixed during it: (1) the `at` redaction regex was quadratic (8k chars 610 ms → 0.2 ms after);
      (2) a just-created character's editor can stall after the in-app switch → one reload (3/3 after)

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` updated
- [ ] Docs updated (Task 10)
- [ ] BDD covers every Critical + High scenario in SCENARIO.md
- [ ] e2e run on the affected surface for CLI **and** MCP (Iron Law), or a named external blocker
- [ ] No `# TODO` without a tracked issue link
