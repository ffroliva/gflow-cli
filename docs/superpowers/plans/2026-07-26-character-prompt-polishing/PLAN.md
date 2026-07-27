# Feature Implementation Plan: Character Prompt Format Control (#383)

> **For agentic workers:** Run `/gflow:status --feature character-prompt-polishing` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Support Google Flow's native prompt **Format** control during character creation, exposed
via an opt-in `--format-prompt` flag on `gflow character create`.

**Naming (revised 2026-07-26):** the plan originally called this "prompt polishing". The button is
labelled **Format** in the editor and rewrites the typed prompt into Flow's character
prompt-engineering shape, so the flag, driver method, and log events all say *format*, not *polish*.

**Architecture:** Driver & saga extension.
1. `ui_automation.py`: `format_character_prompt(page)` driver helper on a locale-stable
   `personal_recommendations` icon-ligature cascade.
2. `services/character_create.py`: `format_prompt: bool = False` threaded through the saga.
3. `cli_character.py`: `--format-prompt` flag on `gflow character create`.
4. ~~`mcp/tools.py`~~ — **not applicable**, see Task 3.

**Predict verdict:** GO — confidence 9.0/10

---

## Task 1 — Transport Driver Format Support

**What:** `format_character_prompt(page)` on `UiAutomationTransport`.

**Files:**
- `src/gflow_cli/api/transports/ui_automation.py`
- `tests/api/transports/test_ui_character_editor.py`

**Steps:**
- [x] Add `format_character_prompt` driver helper on the `PROMPT_FORMAT_SELECTORS` cascade.
- [x] Wait for the Slate composer to settle after the click.
- [x] Unit test the driver (`TestFormatCharacterPrompt`, 5 cases).

**Deviation — the selector is anchored on the ligature, NOT the "Format" label.** Flow renders UI
text in the Chrome *profile* language, so "Format" is "Formatar" on a pt-BR profile; a text-anchored
button selector is exactly the incident-#56 failure mode (click landed nowhere, 34s silent hang).
EN text survives only as a last-resort cascade entry. The original tuple also used `:has-text()`
inside `:has()`, which Playwright rejects — corrected to `:text-is()`. See
`[[flow-locale-leak-icon-ligatures]]`.

**Deviation — best-effort, not fail-loud.** `format_character_prompt` returns `bool` and never
raises; both call sites previously caught-and-swallowed a `UiSelectorDriftError`, which was pure
ceremony. Matches the `_select_character_model` precedent: formatting is a nicety on a prompt that
already submits fine.

**⚠ OPEN — ligature unverified.** `personal_recommendations` has never been confirmed against live
DOM. `scripts/dev/spike_character_prompt_format.py` dumps every `i.google-symbols` ligature in the
editor; run it before trusting the primary selector. Until then the EN fallback is what will
actually fire on an EN profile, and nothing fires on a localised one.

---

## Task 2 — Character Create Saga Integration

**Files:**
- `src/gflow_cli/services/character_create.py`, `src/gflow_cli/api/client.py`
- `tests/services/test_character_create_saga.py`

**Steps:**
- [x] Thread `format_prompt` through `character_create()` → client → transport.
- [x] Apply to both the face and body slots before submit.
- [x] Unit test forwarding + off-by-default.

Removed a dead `log.info("character_prompt_polished")` that fired before any generation had run.

---

## Task 3 — CLI Option

**Files:**
- `src/gflow_cli/cli_character.py`
- `tests/cli/test_cli_character_create.py`

**Steps:**
- [x] Add the `--format-prompt` click flag.
- [x] Unit test the flag reaches the saga, and defaults off.
- [x] ~~MCP tool parity~~ — **N/A, no such tool exists.** The plan assumed a
  `gflow_character_create` MCP tool; there isn't one. `tests/mcp/test_cli_parity.py:69` records
  `character create` as a deliberate exemption ("character mutations — not yet ported"), so parity
  holds unchanged. Porting the whole mutation surface is out of scope for a flag.

---

## Task 4 — Verification & Documentation

**Steps:**
- [x] `CHANGELOG.md` under `[Unreleased]`.
- [x] `ruff` + `pyright` clean on touched files; `2770 passed` unit suite.
- [ ] Live verification: run the spike, confirm the ligature, then one real
  `gflow character create --format-prompt` end-to-end ([[done-means-e2e-verified]]).

Stub signatures in `test_client_generate_character.py` and `test_character_gen_no_direct_post.py`
were updated to mirror the new kwarg ([[bdd-stubs-mirror-runtime-signatures]]).

---

## Definition of Done

- [x] `--format-prompt` supported on `gflow character create`
- [x] Locale-stable ligature is the primary anchor; EN text last-resort only
- [x] `ruff` / `pyright` / `pytest` green
- [x] `CHANGELOG.md` updated
- [ ] Ligature confirmed against live DOM + one E2E run
