# Plan: Slice 3 of #987 — `gflow character create --image` on `flow.google.com`
> Created: 2026-11-21 | Status: COMPLETED | Branch: `feature/639-character-create-from-images`

## Context
- **Goal:** Port slice 3 of contributor PR #987 (`@ziyedbe`) onto current `develop`: build a character from local image files through Flow's character editor on `flow.google.com`, from the CLI (`gflow character create --image FILE [--body-image FILE]`) and from MCP (`gflow_character_create`). Nothing is generated, so the path spends no credits and no image quota.
- **Assessment reference:** `docs/superpowers/spikes/2026-11-15-987-takeover-assessment.md` (§2 rows 4 and 13, §5 slice 3, §6 items 4 and 5).
- **Wire reference:** `docs/superpowers/spikes/2026-10-16-migrated-character-lifecycle-wire.md` (`C4BZMd` create, `maseQ` upload, `rzMKMb` field write, `Zzl0ze` read).
- **Out of scope:** export/import and the bundle format (slice 4), `--character FOLDER` on generation (slice 5), routing the prompt-generated create's `patch_entity` through the editor, the generic 120-token redaction rule and the queued-submit (`[5]`) clip adoption (both dropped, §5 and §6 of the assessment).

## Scenario Matrix

| # | Dimension | Given / When | Expected (CLI and API) | MCP twin |
|---|---|---|---|---|
| S1 | Happy path | `--image face --body-image body --voice aoede --personality …` on a project served `flow.google.com` | Portrait upload creates the entity; name and personality confirmed from their `rzMKMb` echoes; body bound to slot 1; voice set; JSON result lists two workflow ids | `gflow_character_create` returns `status="ok"` with the same fields |
| S2 | Input validation | Neither or both of `--face-prompt` / `--image`; `--body-image` without `--image`; `--body-prompt`, `--model`, `--format-prompt` or `--locale` with `--image`; a missing file | Exit 2 before any client opens | Unknown voice refused before a browser opens |
| S3 | Trust boundary | A non-image file (for example a private key) or a missing body image | `ConfigurationError` (exit 11) before any page is used | Same error envelope |
| S4 | Host | The draft page opens on a host other than `flow.google.com` | `ConfigurationError`, "not ported yet", before any upload | Same |
| S5 | Lost reply | `C4BZMd` lost, `maseQ` names the entity; or the page URL names it | The entity is adopted, not created again | Same |
| S6 | Both replies lost | Neither reply arrives | The project is re-read; one new character is adopted, two are refused (exit 7), none within the settle window earns one retry | Same |
| S7 | Lost body reply | `maseQ` lost on the body upload | A re-read that shows the image bound returns it; an unbound one is retried once, then `MediaUploadRejectedError` | Same |
| S8 | Cold profile | An edit goes unanswered; a voice confirm never enables; a confirm click times out while the voice is set | The edit is redone once after a reload; the pick is redone once; a voice the editor shows is accepted | Same |
| S9 | Orphan | Any failure after the create | The remediation hint names the entity id when it is known, otherwise says a character may exist; the error is not retryable; nothing is deleted | Same |
| S10 | Drift before the create | A browser timeout before the entity exists | `UiSelectorDriftError` (exit 23), no orphan hint | Same |

## Tasks
- [x] Transport (`api/transports/migrated_character.py`): reply readers (`verify_patch`, `created_entity`, `bound_upload`), editor gestures, `patch_character`, and `create_from_files` split by job (draft and host check, one create attempt, id recovery, post-create fill, orphan-hint wrapper) within `check_size.py` (C901 <= 10, PLR0915 <= 50).
- [x] `clear_overlays` in `_mc_editor_gate.py`, reusing the composer's dialog and cookie-bar dismissals.
- [x] `CharacterSurface.create_character_from_files` validates every file before a page is checked out.
- [x] `services/character_create.character_create_from_files` records the operation after the transport returns; a catalog failure warns and continues.
- [x] CLI `--image` / `--body-image`, mutually exclusive with the prompt flags; output shared with the generated create (`-o json|id|table`).
- [x] MCP `gflow_character_create` in `mcp/character_tools.py` (the file-length ratchet on `mcp/tools.py`); parity test maps the leaf, records the prompt-only options as GAP #691, records the leaf as a free twin of a spending leaf, and checks options of tools defined outside `mcp.tools`.
- [x] Offline tests: `tests/api/transports/test_migrated_character_create.py`, `tests/api/test_client_character_host.py`, `tests/services/test_character_create_from_files.py`, `tests/cli/test_cli_character_create.py`, `tests/mcp/test_character_create_tool.py`, `tests/mcp/test_cli_parity.py`.
- [x] E2E: `tests/features/character_create_from_files.feature` bound by `tests/e2e/test_character_create_from_files_bdd.py` (CLI and MCP, `e2e_character`, credit-free).
- [x] Docs: CHANGELOG, USAGE, CHARACTER, MCP, KNOWN_ISSUES, ROADMAP, AGENTS.md overview, the `gflow-cli` skill; generated mirrors regenerated.

## Known gaps
- **Voice picker, one locale measured.** The option is matched by the voice's own name, an untranslated proper noun; the picker has been measured on one locale only. A miss is reported as selector drift (exit 23), not as a missing voice, until a second locale is measured.
- **Cold-profile paths.** The retries for an unanswered edit, a voice confirm that never enables, both create replies lost, an upload refused by an error frame and the one-time upload-terms dialog are modelled offline from the measured replies; the live e2e runs did not hit them.
