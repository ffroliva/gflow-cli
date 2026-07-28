# Provider-Agnostic LLM Transport Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature provider-agnostic-llm` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Let users drive the prompt tools (`--tool creative-director` / `reverse-engineer` /
`storyboard`) through *any* OpenAI-compatible endpoint — a local proxy such as freellmapi,
OpenRouter, a corporate gateway, or Google direct — by setting `GFLOW_CLI_LLM_BASE_URL`
(plus optional key and model), instead of being hardwired to native Google Gemini.

**Issue:** [#387](https://github.com/ffroliva/gflow-cli/issues/387)

**Architecture:** One OpenAI Chat Completions transport lives in `src/gflow_cli/tools/expander.py`;
the native `generativelanguage…:generateContent` path is deleted outright. The retry/backoff loop,
the 60 s wall-clock budget, the never-raise contract, the stdlib-`urllib`-only constraint and the
`Transport = Callable[[url, payload, timeout], dict]` port all stay **unchanged** — the entire change
lands *inside* the transport function plus the payload/response builders. No new module: one
implementation, one caller (`tools/runtime.py:159`).

**Predict verdict:** CAUTION — confidence 7/10. All five personas returned CONCERNS, none BLOCKING.
Every mandatory mitigation is folded into the tasks below.

**Spike:** `scripts/diag/spike_387_openai_compat.py`, run 2026-07-28 against **both**
`http://127.0.0.1:3001/v1` (freellmapi — non-Google) and
`https://generativelanguage.googleapis.com/v1beta/openai`:

| Probe | freellmapi | Google compat | Consequence |
|---|---|---|---|
| text `chat/completions` | PASS 0.6 s | PASS 0.7 s | design viable |
| `temperature` + `max_tokens` | PASS 0.8 s | PASS 0.6 s | no `max_completion_tokens` fallback needed |
| **multimodal `image_url` data-URI (semantic)** | **PASS** `'Red'` | **PASS** `'Red'` | **native `inlineData` path can be deleted — no dual path** |
| bad key | 401 | 400 | both non-retryable → fail-fast holds |

Latency 0.6–1.0 s across the board ⇒ the existing 20 s/attempt + 60 s total budget is ample.
**Do not add configurable timeout/budget env vars.**

---

## Risk register

| Severity | Risk | Mitigation | Task |
|---|---|---|---|
| **High** | `base_url` becomes user-supplied, voiding the `# noqa: S310 — fixed https Gemini endpoint` justification (`expander.py:124`) — `file://`, SSRF to `169.254.169.254`, plaintext key leak | Scheme allowlist at the Settings boundary: https only + explicit localhost/`127.0.0.1`/`::1` http exception; reject `@` in netloc. Mirror `_validate_storage_uri` (`config.py:303-320`). Update the stale noqa comment. | 3 |
| **High** | `urllib` follows 302 **and re-sends `Authorization` cross-host** → direct key exfiltration by a hostile gateway | Build the opener via `build_opener()` **without** `HTTPRedirectHandler`. Never-raise already turns the resulting `URLError` into a clean fallback, so this costs nothing. | 4 |
| **High** | `expander.py:221` logs up to 500 chars of the raw response body (`detail=exc.detail`); a hostile or debug-mode gateway can echo the Bearer token back into the logs | Wire in `redact_sensitive_text()` from `data/redaction.py` — it already carries a `Bearer\s+…` pattern built for exactly this — at every `expander.py` log site. | 4 |
| **High** | Silent-degradation: the expander never raises, so *every* new misconfiguration (wrong base_url, missing `/v1`, unserved model) looks identical to "prompt just wasn't rewritten" — user keeps paying full credits | One stderr line on fallback at the CLI boundary (reuse the existing `quiet` param in `_cli_helpers.apply_tool_option`), **plus** the loud one-time removal notice. | 3, 5 |
| **High** | Removing `GFLOW_CLI_GEMINI_API_KEY` breaks existing users **silently** — no error, no exit code, degraded output for weeks | ~10-line one-time loud notice when the old var is set and `GFLOW_CLI_LLM_*` is not. Forwards nothing; only makes the break visible. | 3 |
| **High** | TOML `model = "gemini-2.5-flash"` pin makes `GFLOW_CLI_LLM_BASE_URL` **not actually work** — `runtime.py:161` sends it unconditionally, any gateway 400s, silently | `ToolConfig.model: str \| None = None`; delete the `model =` line from all three builtin TOMLs. **This is the change that unblocks the gateway.** | 6 |
| Medium | Test-rewrite trap: the two Gemini fixture helpers are so well isolated that a find/replace goes green while hiding a real behaviour change | Rewrite (not adapt) the system-instruction and multimodal tests; keep `TestExpanderTimeBudget` verbatim as the canary. | 2 |
| Medium | `runtime.py:171-176` text-expands a **file path string** when multimodal returns `None` — pre-existing, in the blast radius | Return `was_expanded=False` instead. | 7 |
| Low | Doc drift is **not** CI-enforced — `check_doc_links.py` validates link targets, not env-var names | Explicit doc task with the complete enumerated surface. | 9 |

---

## File structure

### New files
```
tests/tools/test_expander_integration.py
  Live round-trip against a local OpenAI-compatible gateway (freellmapi).
  Marked `containers` — opt-in via -m containers, skips cleanly when unreachable.
docs/superpowers/plans/2026-07-28-provider-agnostic-llm/PLAN.md
  This file.
scripts/diag/spike_387_openai_compat.py
  Already written. Four-probe endpoint capability spike.
```

### Modified files
```
src/gflow_cli/tools/expander.py
  Transport rewritten to OpenAI Chat Completions. Native path + from_settings deleted.
  Security guards added. GeminiHttpError -> LlmHttpError.
src/gflow_cli/config.py
  gemini_api_key/gemini_model removed; llm_base_url/llm_api_key/llm_model added
  with scheme validation and the one-time removal notice.
src/gflow_cli/tools/spec.py
  ToolConfig.model -> str | None = None
src/gflow_cli/tools/runtime.py
  Model precedence wiring; multimodal fall-through bug fix.
src/gflow_cli/tools/builtin/{creative-director,reverse-engineer,storyboard}.toml
  Drop the `model =` pin; requires_env -> GFLOW_CLI_LLM_API_KEY
src/gflow_cli/_cli_helpers.py
  _TOOL_OPTION_HELP text; stderr warning on fallback.
src/gflow_cli/cli_video.py
  Replace the hand-inlined duplicate help text with the shared tool_option() decorator.
src/gflow_cli/mcp/tools.py
  Docstrings at :656-658 and :825-827.
tests/tools/test_expander.py, tests/tools/test_runtime.py, tests/e2e/test_tools_e2e.py
.env.template, docs/CONFIGURATION.md, docs/TOOLS.md, docs/PROMPT_EXPANSION.md,
CHANGELOG.md, PLAN.md
```

---

## Task 1 — Delete dead Gemini model plumbing (own commit, first)

**What:** Remove `PromptExpander.from_settings` and `Settings.gemini_model`. Both are dead —
`from_settings` has **zero callers** (verified) and is the only reader of `gemini_model`, so
`GFLOW_CLI_GEMINI_MODEL` has no live code path despite being documented as a working override in
four places. Landing this first proves the budget/retry canary tests green *before* the transport moves.

**Files:**
- `src/gflow_cli/tools/expander.py` — delete `from_settings` (:184-192) and the now-unused `Settings` `TYPE_CHECKING` import (:49-50)
- `src/gflow_cli/config.py` — delete `gemini_model` (:355)

**Steps:**
- [ ] Confirm zero callers once more: `grep -rn "from_settings\|gemini_model" src/ tests/ scripts/`
- [ ] Delete both symbols and the orphaned import
- [ ] Run `uv run python -m pytest tests/tools -q` — must be green with **no test edits**
- [ ] Commit: `refactor(tools): drop dead from_settings/gemini_model plumbing`

**Tests:**
- [ ] Existing `tests/tools/` suite passes untouched — any failure here means the symbols were not dead

---

## Task 2 — Test scaffold: OpenAI Chat Completions shape (red)

**What:** Restate the expander's test suite against the OpenAI payload/response shape. Red only;
no production code. Handle each group per its risk class — this is where a mechanical find/replace
would hide a real behaviour change.

**Files:**
- `tests/tools/test_expander.py`
- `tests/tools/test_runtime.py`

**Steps:**
- [ ] Swap the two Gemini fixture helpers: `_candidates()` (:24-26) → `_choices()`, `_sent_text()` (:50-54) → read `messages[-1]["content"]`
- [ ] **REWRITE** `test_custom_system_instruction_is_used` (:283-295) — the instruction moves from concatenated user text (`expander.py:331`) to a separate `{"role":"system"}` message. Assert **two separate facts**: system message carries the instruction, user message carries the raw prompt
- [ ] **REWRITE** `TestExpanderMultimodal` (:330-385) — all five tests currently assert only call counts / `was_expanded` / returned text; **not one asserts the image reached the payload**
- [ ] **KEEP VERBATIM** `TestExpanderTimeBudget` (:205-280) — the canary. Editing anything here beyond the fixture helpers means the retry loop was changed when it should not have been
- [ ] **ADAPT** (rename/fixture only): 401-not-retried, retry-then-fallback, empty-candidates (`{"candidates":[]}` → `{"choices":[]}`)
- [ ] Rename `GeminiHttpError` → `LlmHttpError` in test imports (:19)
- [ ] `tests/tools/test_runtime.py`: four local transport-stub closures return the Gemini envelope (:30-31, :62-63, :148-149, :198-199) — four small edits, **no assertion changes**

**Tests created (red):**
- [ ] `test_system_instruction_is_a_separate_message` — system role carries instruction, user role carries raw prompt, as two independent assertions
- [ ] `test_multimodal_payload_carries_image_data_uri` — a part exists with `image_url.url` starting `data:image/jpeg;base64,` **and** the decoded bytes round-trip to the original file
- [ ] `test_keyless_request_omits_authorization_header` — no key ⇒ no `Authorization` header at all
- [ ] `test_null_content_falls_back` — `content: null` (a refusal); `_extract_text`'s `isinstance` guard (:347) is doing real work
- [ ] `test_markdown_fenced_response_is_cleaned` — non-Gemini models fence where Gemini quotes; decide whether `_clean` strips fences (~4 lines)
- [ ] `test_bad_key_status_is_not_retried` — widened from `{401,403}` to any 4xx (Google returns 400, freellmapi 401)

---

## Task 3 — Config surface: `GFLOW_CLI_LLM_*` + scheme validation + removal notice

**What:** Replace the Gemini settings with the generic triple, validate `base_url` at the trust
boundary, and make the removal of `GFLOW_CLI_GEMINI_API_KEY` loud instead of silent.

**Files:**
- `src/gflow_cli/config.py`
- `.env.template`

**Steps:**
- [ ] Add `llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"` — **the on/off switch**
- [ ] Add `llm_api_key: str | None = None` — **optional**; keyless local gateways must work
- [ ] Add `llm_model: str | None = None` — optional; also the provider selector (gateways route on the model string)
- [ ] Delete `gemini_api_key` (:348)
- [ ] `field_validator` on `llm_base_url`: https only, **plus** explicit http for `localhost`/`127.0.0.1`/`::1`; reject `@` in netloc. Mirror `_validate_storage_uri` (`config.py:303-320`)
- [ ] One-time removal notice: if `GFLOW_CLI_GEMINI_API_KEY` is set **and** no `GFLOW_CLI_LLM_*` is, emit a loud notice that it is no longer read. Forwards nothing. Follow the `_migrate_legacy_env` (:42-63) warn-once shape; emit via **both** `structlog` and stderr, because `structlog` is JSON when piped and invisible to a human
- [ ] Update `.env.template` — replace :59-61, and fix the pre-existing drift on :59 which wrongly says the var is "only required when `GFLOW_CLI_PROVIDER=official`"

**Tests:**
- [ ] `test_llm_base_url_rejects_non_https` — `file://`, `ftp://`, plain `http://example.com`
- [ ] `test_llm_base_url_allows_http_localhost` — `http://127.0.0.1:3001/v1` accepted
- [ ] `test_llm_base_url_rejects_credentials_in_netloc`
- [ ] `test_removal_notice_fires_once_when_only_gemini_key_set`
- [ ] `test_removal_notice_silent_when_llm_vars_set`

---

## Task 4 — Transport rewrite + security guards

**What:** Rewrite the payload builder, response parser, URL and auth header to OpenAI Chat
Completions. Entirely inside the transport function so the retry/budget control flow is untouched.

**Files:**
- `src/gflow_cli/tools/expander.py`

**Steps:**
- [ ] `_ENDPOINT` → `{base_url}/chat/completions` (normalize trailing slash)
- [ ] `_default_transport`: `Authorization: Bearer <key>`, **omitted entirely when no key**
- [ ] Build the opener via `urllib.request.build_opener()` **without** `HTTPRedirectHandler`
- [ ] `_build_payload` → `{"model", "messages":[{"role":"system"},{"role":"user"}], "temperature", "max_tokens"}`
- [ ] `expand_multimodal`: `inlineData` parts → `{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,…"}}`
- [ ] `_extract_text` → `choices[0].message.content`, keeping the defensive `isinstance` guard
- [ ] Wrap every logged `exc.detail` in `redact_sensitive_text()` (`data/redaction.py`)
- [ ] Log the resolved destination **netloc only** once per construction — audit trail for where prompts and base64 image bytes went
- [ ] `GeminiHttpError` → `LlmHttpError`; `_retry_after_gemini_error` → `_retry_after_error`. **Keep the name `PromptExpander`** (names the use case, not the vendor)
- [ ] Rewrite the Gemini-branded module docstring
- [ ] Update the `# noqa: S310` comment — the old justification is now false
- [ ] Gate on `base_url`, **not** on the key (`expander.py:263` and `:291`) — otherwise keyless gateways silently never run
- [ ] Collapse the duplicated payload assembly in `_build_payload` (:327) and the inline multimodal payload (:306-309) into one builder — the file should get **smaller**; if it grows, scope crept

**Must stay unchanged:**
- [ ] Never-raise contract · hand-rolled retry loop · 60 s wall-clock budget + per-attempt clamp · stdlib-`urllib` only · injectable `transport`/`sleep`/`clock` seams · `tenacity` still rejected

**Tests:** Task 2's suite goes green.

---

## Task 5 — Visible failure at the CLI boundary

**What:** One stderr line when a tool falls back, so the ~6 new misconfiguration modes are not
all indistinguishable from silence.

**Files:**
- `src/gflow_cli/_cli_helpers.py`

**Steps:**
- [ ] In `apply_tool_option`, on `was_expanded is False`, emit one `click.secho(..., err=True, fg="yellow")` naming the tool and the reason — reuse the existing `quiet` parameter (:168) so batch paths can still opt out
- [ ] Keep `expander.py` `structlog`-only — the user-facing line belongs at the CLI boundary, not in the library layer

**Tests:**
- [ ] `test_fallback_prints_one_stderr_line`
- [ ] `test_quiet_suppresses_fallback_line`

---

## Task 6 — Model precedence (the change that actually unblocks gateways)

**What:** Stop shipping a vendor model name that 400s on every non-Google endpoint.

**Files:**
- `src/gflow_cli/tools/spec.py`, `src/gflow_cli/tools/runtime.py`, the three builtin TOMLs

**Steps:**
- [ ] `ToolConfig.model: str | None = None` (`spec.py:28`)
- [ ] Delete the `model = "gemini-2.5-flash"` line from all three builtin TOMLs
- [ ] `runtime.py`: `model=spec.config.model or settings.llm_model` — precedence **TOML pin > `GFLOW_CLI_LLM_MODEL` > gateway default**
- [ ] Omit `model` from the payload entirely when all three are unset, so the gateway picks (freellmapi's `auto` chain)
- [ ] `requires_env` in the three TOMLs → `GFLOW_CLI_LLM_API_KEY`. Display-only (verified: only consumers are `cli_tools.py:34` and `:55-56`) — **do not** add "any of" schema semantics

**Tests:**
- [ ] `test_toml_pin_wins_over_env_model`
- [ ] `test_env_model_used_when_no_pin`
- [ ] `test_model_omitted_when_unset`

---

## Task 7 — Fix the multimodal fall-through bug

**What:** `runtime.py:171-176` currently falls through to text-expanding a **file path string**
when `_apply_multimodal_reverse_engineering` returns `None` — producing a confident, useless,
billable prompt. Pre-existing, but squarely in this change's blast radius.

**Files:** `src/gflow_cli/tools/runtime.py`

**Steps:**
- [ ] Return `was_expanded=False` instead of falling through to `expander.expand(prompt)`

**Tests:**
- [ ] `test_multimodal_failure_does_not_text_expand_a_path`

---

## Task 8 — Integration test against a real local gateway

**What:** This feature integrates with an external tool; offline tests cannot prove it works.
Exercise the **real** transport against freellmapi.

**Files:** `tests/tools/test_expander_integration.py` (new)

**Steps:**
- [ ] Mark `containers` — the existing marker for "requires Docker daemon; opt-in via `-m containers`". **No new marker needed**
- [ ] Target `http://127.0.0.1:3001/v1`; skip cleanly (not fail) when unreachable, so CI and other contributors are unaffected
- [ ] Read the key from the environment; never hardcode

**Tests:**
- [ ] `test_real_gateway_text_expansion` — `was_expanded is True` against the live gateway
- [ ] `test_real_gateway_multimodal_round_trip` — send a generated solid-colour image, assert the response names the colour (semantic, not just HTTP 200 — a gateway that drops the image still returns plausible prose)
- [ ] `test_real_gateway_keyless_is_rejected_not_crashed` — never-raise holds against a real 401

---

## Task 9 — Documentation sweep

**What:** Every user-facing mention of `GFLOW_CLI_GEMINI_*`, plus new content for gateways.
Nothing here is CI-enforced (`check_doc_links.py` validates link targets, not env-var names) —
this is manual diligence.

**Files & steps:**
- [ ] `docs/CONFIGURATION.md:116-136` — replace the two GEMINI sections with three LLM sections; document the precedence rule and that `base_url` is the on/off switch
- [ ] `docs/PROMPT_EXPANSION.md:18,28,141,163,225-226` — including the failure-modes table, which must gain the **new** modes (wrong base_url, missing `/v1`, model the gateway doesn't serve)
- [ ] `docs/TOOLS.md:64,176,297`
- [ ] `src/gflow_cli/_cli_helpers.py:131-135` (`_TOOL_OPTION_HELP`)
- [ ] `src/gflow_cli/cli_video.py:918-928` — a hand-inlined duplicate of `_TOOL_OPTION_HELP` that has **already drifted**. Fix by using the shared `tool_option()` decorator, not by updating the copy
- [ ] `src/gflow_cli/mcp/tools.py:656-658` and `:825-827` — MCP & CLI Schema Symmetry rule applies
- [ ] **New content:** document [freellmapi](https://github.com/tashfeenahmed/freellmapi) as a *suggested* local Docker proxy for one entrypoint across providers, while making clear the CLI works with OpenRouter or **any** OpenAI-compatible provider/proxy directly. Worked example for each
- [ ] **Windows gotcha:** use `127.0.0.1`, **not** `localhost` — the gateway binds IPv4 only and Windows dual-stack resolves `::1` first and stalls (source: social-publisher's own compose file). Use `127.0.0.1` in every local-gateway example
- [ ] Document that `urllib` implicitly honours `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` **and Windows registry proxy settings** — a corporate proxy can silently intercept local-gateway traffic
- [ ] `PLAN.md:118` env-var list → add `GFLOW_CLI_LLM_*`
- [ ] `CHANGELOG.md` `[Unreleased]` — flag the **breaking** removal of `GFLOW_CLI_GEMINI_API_KEY`/`_MODEL`
- [ ] Edit `docs/` only — `website/docs/*` is generated. Any **new** page must be added to the mkdocs nav (gate added in `cb38c3c`)

---

## Task 10 — Gates

**Steps:**
- [ ] `/gflow:check` — the Impeccable Routine, all green
- [ ] `/code-review` at **high** effort
- [ ] `/ponytail-review` at **xhigh**
- [ ] `/gflow:sonar <PR>` — zero new issues
- [ ] `/gflow:live-verify` against **both** freellmapi and Google's compat endpoint
- [ ] Extend `tests/e2e/test_tools_e2e.py` to assert `was_expanded is True` with a real endpoint

---

## Explicitly cut (YAGNI — all five predict personas agreed; AGENTS.md D14)

| Cut | Why |
|---|---|
| `GFLOW_CLI_LLM_PROVIDER` | One implementation. Also **collides** with the existing `GFLOW_CLI_PROVIDER` (`config.py:347`). The model string is already the provider selector. |
| Deprecation/alias machinery | Maintainer chose hard removal; only the one-time notice remains. |
| `response_format` / structured JSON | Nothing parses JSON — every tool returns one string. |
| `gflow tools doctor` | `gflow tools run <name> "x" --json` already reports `was_expanded`. A new CLI leaf would force a mirrored MCP tool per the parity rule. |
| Configurable timeout/budget env vars | Spike measured 0.6–1.0 s against a 60 s budget. |
| Any new module | One implementation, one caller (`runtime.py:159`). Edit `expander.py` in place. |
| Renaming `PromptExpander` | Names the use case, not the vendor. Renaming costs a doc sweep for nothing. |

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` updated, breaking change flagged
- [ ] Docs updated per Task 9 — every enumerated file
- [ ] Integration test green against a live freellmapi; skips cleanly when absent
- [ ] Live-verified against both a non-Google gateway and Google's compat endpoint
- [ ] `/code-review` high and `/ponytail-review` xhigh both clean
- [ ] SonarCloud gate green
- [ ] No `# TODO` in diff without a tracked issue link
