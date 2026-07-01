# Prompt Expansion ("Creative Director" mode) Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature prompt-expansion` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Integrate a prompt-expansion flag (`--expand` / `-e`) that utilizes a Gemini Flash model to expand user prompts using Google's official 5-component formula (Subject + Action + Location + Composition + Style) before sending them to Google Flow.

**Architecture:** 
* Create a lightweight, dependency-free `PromptExpander` client that communicates with the public Gemini API (`generativelanguage.googleapis.com`).
* Extend CLI modules ([cli_image.py](file:///C:/development/github/gflow-cli/src/gflow_cli/cli_image.py) and [cli_video.py](file:///C:/development/github/gflow-cli/src/gflow_cli/cli_video.py)) to parse and apply the option.
* Modify the data repository layers to capture both the original and expanded prompts in the SQLite database catalog.

**Predict verdict:** GO — confidence 8.6/10

**Risk Register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Medium | API key is missing or invalid, causing crashes | Fail gracefully: log an `INFO` warning and proceed with the raw, unexpanded prompt. |
| Medium | Rate limiting on public Gemini endpoint (HTTP 429) | Implement exponential backoff retry logic (up to 3 times) before falling back. |
| Low | Database schema drift on existing catalogs | Write a backward-compatible SQLite migration to add the `expanded_prompt` column to operations logs. |

---

## File structure

### New files
```
src/gflow_cli/api/prompt_expander.py
  Lightweight client for communicating with Gemini generateContent endpoint.
tests/api/test_prompt_expander.py
  Unit tests for prompt expander client under mock conditions.
```

### Modified files
```
src/gflow_cli/data/models.py
  Add expanded_prompt field to database mapping classes.
src/gflow_cli/data/repository.py
  Update catalog store writes/queries to record expanded prompts.
src/gflow_cli/cli_image.py
  Expose --expand flag in text-to-image/batch commands.
src/gflow_cli/cli_video.py
  Expose --expand flag in text-to-video/batch/chain commands.
CONFIGURATION.md
  Document GFLOW_CLI_GEMINI_API_KEY environment variable.
USAGE.md
  Document --expand flag usage and examples.
```

---

## Task 1 — Unit Test Scaffold (test-first)

**What:** Write unit test skeletons validating the expander client and mock behaviors.

**Files:**
- `tests/api/test_prompt_expander.py`

**Steps:**
- [ ] Create `tests/api/test_prompt_expander.py`.
- [ ] Add tests for:
  - API key verification.
  - Successful mock JSON response parsing.
  - Graceful degradation on HTTP 401/403/429/500 errors.
  - Truncation boundaries for prompts longer than 4000 characters.

**Tests created (red):**
- [ ] `test_expander_success`
- [ ] `test_expander_missing_key_fallback`
- [ ] `test_expander_http_error_fallback`
- [ ] `test_expander_truncation`

---

## Task 2 — Implement PromptExpander Client

**What:** Write the `PromptExpander` class using stdlib `urllib` to request prompt expansions from Gemini Flash.

**Files:**
- `src/gflow_cli/api/prompt_expander.py`

**Steps:**
- [ ] Create `src/gflow_cli/api/prompt_expander.py`.
- [ ] Implement the `5-component prompt template` as a system instruction/prompt template.
- [ ] Implement standard POST request logic targeting the `gemini-2.5-flash` or `gemini-3.1-flash` REST endpoint.
- [ ] Implement exponential backoff retry on HTTP 429.
- [ ] Clean up any banned keywords and limit prompt output length to 3500 characters.
- [ ] Run `pytest tests/api/test_prompt_expander.py` until tests pass green.

---

## Task 3 — Update SQLite Database Schema

**What:** Add `expanded_prompt` to the SQLite tables to persist expansions in generation history.

**Files:**
- `src/gflow_cli/data/models.py`
- `src/gflow_cli/data/repository.py`

**Steps:**
- [ ] Add `expanded_prompt` column to SQLite schema definition in `models.py`.
- [ ] Write a backward-compatible migration or update the schema initialization SQL so existing databases do not break.
- [ ] Update write queries in `repository.py` / `store.py` to record the expanded prompt.
- [ ] Write tests ensuring database reads/writes work with the new field.

---

## Task 4 — Expose `--expand` option in CLI Commands

**What:** Wire the `--expand` flag into Click commands and execute the expander.

**Files:**
- `src/gflow_cli/cli_image.py`
- `src/gflow_cli/cli_video.py`

**Steps:**
- [ ] Expose `@click.option("--expand", "-e", is_flag=True, help="Expand prompt using Gemini Creative Director")` in image commands.
- [ ] Expose the same option in video commands.
- [ ] Invoke the expander client inside CLI run blocks before triggering the FlowApiClient.
- [ ] Log expansion details via `structlog` (`prompt_expanded` event).

---

## Task 5 — Update Documentation & Verify

**What:** Document the new environment variable and command flag, and run integration checks.

**Files:**
- `CONFIGURATION.md`
- `USAGE.md`
- `CHANGELOG.md`

**Steps:**
- [ ] Add `GFLOW_CLI_GEMINI_API_KEY` description in `CONFIGURATION.md`.
- [ ] Add `--expand` documentation and example runs in `USAGE.md`.
- [ ] Update `CHANGELOG.md` under `[Unreleased]` features.
- [ ] Run the `/gflow:check` gate suite to ensure 100% green status.

---

## Definition of done

- [ ] All task steps checked off.
- [ ] `/gflow:check` green (ruff, format, pyright, pytest).
- [ ] `CHANGELOG.md` updated.
- [ ] User can run `gflow image t2i "cat in space" -e` and see prompt expansion in action.
