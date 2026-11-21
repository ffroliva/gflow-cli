# Audio Narration Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature audio-narrate` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Generate a local narration audio file with edge-tts or Gemini TTS and use it as the existing video assembly voiceover input.

**Architecture:** Flow provides Character voice metadata and samples but no standalone narration track endpoint, so use external TTS to create a local track. Add immutable speech request/result values and a `SpeechSynthesizer` Protocol to `services/speech_ports.py`, with isolated edge and Gemini adapters under `services/speech_synthesizers/`. A small narration service owns provider selection and output handling; `cli_audio.py` and `mcp/audio_tools.py` expose direct, no-Flow-credit paths. Keep `video assemble --voiceover` as the consumer and omit timing recovery from v1.

**Predict verdict:** CAUTION — confidence 7/10

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| High | `edge-tts` uses an unofficial Microsoft consumer endpoint and carries endpoint/license uncertainty | Keep `gflow-cli[tts]` optional, lazy-loaded, explicitly best-effort/no SLA; disclose in usage/config docs and provide Gemini alternative |
| High | Narration text and API credentials cross a third-party boundary | Disclose provider transfer in CLI/MCP help; dedicated `SecretStr` setting; never log text or secrets |
| High | Provider failure could leave a corrupt/partial or misleading output | Validate before request, write atomically, refuse overwrite, expose typed network/configuration errors |
| Medium | Gemini does not establish native rate/pitch controls | Reject non-default rate/pitch clearly for Gemini; pass natively to edge |
| Medium | Network e2e can flap independently of Flow | Add `e2e_tts`, separate from local-only `e2e_media`, and run installed CLI and MCP paths against edge endpoint |
| Low | Edge Read Aloud can vocalize typographic punctuation or emoji | Send the text unchanged; a unit test pins that the adapter does not rewrite it (see Decision log) |

---

## File structure

### New files
```text
src/gflow_cli/services/speech_synthesizers/__init__.py
  Provider adapters and lazy dependency checks.
src/gflow_cli/services/speech_synthesizers/edge.py
  edge-tts adapter with native rate/pitch and bounded NoAudioReceived retry.
src/gflow_cli/services/speech_synthesizers/gemini.py
  httpx Gemini TTS adapter using the dedicated key and bounded response parsing.
src/gflow_cli/services/audio_narration.py
  Provider selection, validation, safe output handling, and synthesis orchestration.
src/gflow_cli/cli_audio.py
  `gflow audio narrate` command and JSON response.
src/gflow_cli/mcp/audio_tools.py
  Direct `gflow_narrate` twin; provider disclosure and no-spend availability.
tests/services/test_speech_synthesizers.py
  Adapter contract, missing dependency/key, network errors, and redaction tests.
tests/services/test_audio_narration.py
  Validation, text-file input, atomic output, and provider option tests.
tests/features/audio_narrate.feature
  Offline CLI behavioral scenarios.
tests/features/test_audio_narrate_steps.py
  Single binding for offline narration feature.
tests/e2e/test_audio_narrate_bdd.py
  Installed CLI and MCP edge flows plus Gemini keyless skip.
tests/features/audio_narrate_e2e.feature
  Network-bound synthesis and assembly scenarios tagged `@e2e @e2e_tts`.
```

### Modified files
```text
src/gflow_cli/services/speech_ports.py
  Add synthesis DTOs and the SpeechSynthesizer Protocol.
src/gflow_cli/config.py
  Add dedicated GFLOW_CLI_GEMINI_TTS_API_KEY SecretStr.
src/gflow_cli/cli.py
  Register audio group and its no-Flow-spend leaf command.
src/gflow_cli/mcp/server.py
  Import tool module without adding gflow_narrate to spend-gated tools.
tests/mcp/test_cli_parity.py
tests/mcp/test_no_spend.py
tests/mcp/test_audio_narrate.py
  Map the CLI leaf and verify schema, disclosure, no-spend registration, and errors.
pyproject.toml
  Add optional tts extra and e2e_tts marker.
tests/test_marker_registry.py
tests/features/test_e2e_binding_guard.py
tests/e2e/conftest.py
  Register the network marker and binding/cost semantics.
docs/USAGE.md, docs/MCP.md, docs/CONFIGURATION.md, docs/E2E_TESTING.md
  Document command, third-party text transfer, key, terms risk, and network e2e.
README.md
  Change Narration audio row from Planned to `Yes (audio narrate: edge-tts or Gemini voices)`.
CHANGELOG.md
  Add narration under Unreleased > Added.
docs/CAPABILITIES.md and capability source/tests (if the current catalog enumerates media features)
  Add the capability only if it is represented by existing entries.
skills/gflow-cli/SKILL.md and its plugin byte copy
  Mirror the shipped command/tool surface.
```

---

## Task 1 — Synthesis port contract (red tests)

**What:** Pin the provider-neutral request/result contract and verify an adapter can return audio.

**Files:** `tests/services/test_speech_ports.py` (new), `src/gflow_cli/services/speech_ports.py` (later task).

**Steps:**
- [x] Add frozen request and result expectations to tests.
- [x] Assert protocol accepts a minimal fake synthesizer.

**Tests created (red):**
- [x] `test_synthesis_values_are_frozen` — values cannot be mutated.
- [x] `test_synthesizer_returns_audio` — contract has no provider dependency.

## Task 2 — Adapter behavior tests (red)

**What:** Specify edge/Gemini provider behavior, failure boundaries, and secret/text redaction before adapters exist.

**Files:** `tests/services/test_speech_synthesizers.py`.

**Steps:**
- [x] Test missing edge extra gives the install hint while importing the base CLI remains possible.
- [x] Test edge stream audio chunks; test one transient `NoAudioReceived` retry and final failure.
- [x] Test that edge receives the text unchanged, including curly quotes and emoji.
- [x] Test Gemini request uses the configured dedicated key and voice, parses provider audio, and handles error/malformed/timeout responses.
- [x] Assert captured logs/errors contain neither input text nor key/header values.

**Tests created (red):**
- [x] `test_edge_missing_extra_names_install_command`.
- [x] `test_edge_retries_transient_no_audio`.
- [x] `test_edge_returns_typed_error_after_retry_bound`.
- [x] `test_edge_preserves_text`.
- [x] `test_gemini_uses_dedicated_key_and_parses_audio`.
- [x] Gemini missing-key, malformed-response, timeout, and rate/pitch refusals.
- [x] `test_provider_failures_are_typed_and_redacted`.

## Task 3 — Port and Gemini setting

**What:** Add the minimal immutable port DTOs and dedicated Gemini key setting.

**Files:** `src/gflow_cli/services/speech_ports.py`, `src/gflow_cli/config.py`, `tests/services/test_speech_ports.py`, `tests/test_config.py`.

**Steps:**
- [x] Add `SynthesisRequest`, `SynthesisResult`, and `SpeechSynthesizer`.
- [x] Include rate and pitch in the request contract; use only fields needed for synthesis.
- [x] Add `gemini_tts_api_key: SecretStr | None` mapped only to `GFLOW_CLI_GEMINI_TTS_API_KEY`.
- [x] Do not fall back to `GFLOW_CLI_LLM_API_KEY`.

**Tests:**
- [x] Task 1 contract tests pass.
- [x] Setting parses from env, is absent by default, and does not expose its value in repr/serialization.
- [x] `test_llm_key_is_not_reused_for_gemini_tts` — dedicated setting remains required.

## Task 4 — Edge and Gemini adapters

**What:** Implement the two v1 adapters behind the port with no new base dependency.

**Files:** `src/gflow_cli/services/speech_synthesizers/{__init__,edge,gemini}.py`, `pyproject.toml`, `tests/services/test_speech_synthesizers.py`.

**Steps:**
- [x] Add `tts = ["edge-tts>=7.3.1"]`; lazy import edge-tts and raise a typed configuration error with the install hint when absent.
- [x] Stream audio; retry connection-class failures and `NoAudioReceived` a small bounded number of times, and let any other error propagate.
- [x] Send edge text unchanged (no normalization).
- [x] Call Gemini TTS with httpx, a bounded timeout, `gemini-3.8-flash-tts`, canonical voice IDs from `api.character.VOICES`, and the dedicated key.
- [x] Reject non-default rate/pitch for Gemini before sending the request; edge passes both natively.
- [x] Map provider failures to typed network errors without retaining raw text, key, headers, or response body in public details.

**Tests:**
- [x] Task 2 adapter tests pass using mocks; no live provider call.
- [x] `test_gemini_rejects_non_default_rate_or_pitch_without_request`.

## Task 5 — Narration service tests (red)

**What:** Pin safe text/file input and output semantics before adding CLI/MCP callers.

**Files:** `tests/services/test_audio_narration.py`.

**Steps:**
- [x] Cover text positional input and mutually exclusive UTF-8 text-file input.
- [x] Cover empty input, unreadable input, invalid provider options, and parent path errors.
- [x] Cover existing destination preservation and no partial destination after provider/write failure.

**Tests created (red):**
- [x] `test_empty_text_is_rejected_before_provider_call`.
- [x] `test_text_file_reads_utf8_and_preserves_unicode`.
- [x] `test_existing_output_is_not_overwritten`.
- [x] `test_failed_synthesis_leaves_no_output_file`.
- [x] Invalid provider and rate options fail before synthesis.

## Task 6 — Narration service

**What:** Implement the small orchestration service and safe audio file output.

**Files:** `src/gflow_cli/services/audio_narration.py`, `tests/services/test_audio_narration.py`.

**Steps:**
- [x] Select edge by default and Gemini only when requested.
- [x] Validate text/provider/rate/pitch and output destination before network calls.
- [x] Write audio atomically without replacing an existing file; retain provider-native output unless conversion is required for WAV compatibility with the assembler.
- [x] Return path/provider and typed outcome; report `charged: no` when nothing reached a provider or the provider is Edge, and `charged: maybe` when a Gemini request may have been billed (read timeout, HTTP 5xx, malformed reply, or a local write failure after synthesis).

**Tests:**
- [x] Task 5 service tests pass with fake adapters and temporary files.
- [x] `test_provider_error_is_not_reported_as_success`.

## Task 7 — Offline BDD and CLI contract tests (red)

**What:** Bind offline scenarios to CLI behavior before implementing the command surface.

**Files:** `tests/features/audio_narrate.feature`, `tests/features/test_audio_narrate_steps.py`, `tests/cli/test_cli_audio.py`.

**Steps:**
- [x] Add untagged Gherkin scenarios for missing extra, existing output, unsupported Gemini controls, and redacted provider errors.
- [x] Bind feature exactly once; verify no `@e2e` tags.
- [x] Add Click runner contract tests for help, JSON output, text file input, and exit mapping.

**Tests created (red):**
- [x] Offline feature scenarios fail for the expected missing CLI command.
- [x] `test_audio_narrate_help_discloses_provider_data_transfer`.
- [x] `test_audio_narrate_json_reports_provider_and_output`.

## Task 8 — CLI surface

**What:** Expose `gflow audio narrate TEXT --out-file FILE` with the agreed options.

**Files:** `src/gflow_cli/cli_audio.py`, `src/gflow_cli/cli.py`, `tests/cli/test_cli_audio.py`, `tests/features/test_audio_narrate_steps.py`.

**Steps:**
- [x] Register `audio` group with `narrate TEXT` or `--text-file FILE`, required `--out-file`, provider `edge|gemini` default edge, optional voice, rate, pitch, and JSON output.
- [x] Follow existing noun-group/verb-leaf conventions in `docs/USAGE.md` and command help; disclose third-party processing.
- [x] Use existing error-to-JSON/exit handling and preserve the established video assemble interface.
- [x] Do not add a `voices` command in v1; it requires an edge catalog network call and is outside the core job.

**Tests:**
- [x] Task 7 CLI and BDD tests pass.
- [x] `test_audio_narrate_default_provider_is_edge`.
- [x] `test_audio_narrate_text_and_text_file_are_exclusive`.

## Task 9 — MCP twin and parity

**What:** Add direct `gflow_narrate` with matching fields, disclosure, and no-spend availability.

**Files:** `src/gflow_cli/mcp/audio_tools.py`, `src/gflow_cli/mcp/server.py`, `tests/mcp/test_audio_narrate.py`, `tests/mcp/test_cli_parity.py`, `tests/mcp/test_no_spend.py`.

**Steps:**
- [x] Register direct tool with `text`, `out_file`, `provider`, `voice`, `rate`, and `pitch` fields mapped to service inputs.
- [x] State in the tool description that narration text is sent to the selected third-party provider; state that Flow credits are not used.
- [x] Keep tool out of `_SPEND_TOOLS`, and import/register it under `--no-spend`.
- [x] Add CLI leaf mapping and assert each CLI option reaches the corresponding MCP field; no queued payload is introduced.

**Tests:**
- [x] `test_gflow_narrate_is_registered_under_no_spend`.
- [x] `test_mcp_narration_provider_errors_are_typed_and_redacted`.
- [x] CLI parity and MCP schema tests pass.

## Task 10 — Network E2E marker and BDD binding

**What:** Add a network-only TTS marker distinct from local-only media tests and exercise both surfaces through assembly.

**Files:** `pyproject.toml`, `tests/test_marker_registry.py`, `tests/features/test_e2e_binding_guard.py`, `tests/e2e/conftest.py`, `tests/features/audio_narrate_e2e.feature`, `tests/e2e/test_audio_narrate_bdd.py`, `docs/E2E_TESTING.md`.

**Steps:**
- [x] Register `e2e_tts` as network-dependent, zero Flow credits, no browser/profile requirement; make selection explicit like other e2e markers.
- [x] Bind the feature once; use synthetic local clip and installed-wheel CLI invocation for edge synthesis then `video assemble --voiceover`.
- [x] Run MCP `gflow_narrate` under no-spend, then assemble returned audio with the synthetic clip.
- [x] Assert generated and assembled voice audio is non-silent, not merely that files exist.
- [x] Add Gemini marked case that skips with a reason naming `GFLOW_CLI_GEMINI_TTS_API_KEY` when unset.

**Tests:**
- [x] Marker registry and binding guards cover `e2e_tts`.
- [x] Live edge CLI and MCP tests pass when explicitly selected; no Flow account or credits required.
- [x] Gemini e2e makes no request when its setting is absent.

## Task 11 — Documentation and shipped skill mirrors

**What:** Document operation, provider disclosure, key configuration, and edge endpoint risk.

**Files:** `docs/USAGE.md`, `docs/MCP.md`, `docs/CONFIGURATION.md`, `.env.template`, `README.md`, `CHANGELOG.md`, `docs/CAPABILITIES.md` and its source if applicable, `skills/gflow-cli/SKILL.md` and generated plugin byte copy.

**Steps:**
- [x] Document `gflow audio narrate`, options, supported formats, output behavior, and `gflow video assemble --voiceover` workflow.
- [x] Document Gemini key name and that it is separate from `GFLOW_CLI_LLM_API_KEY`.
- [x] State edge-tts uses Microsoft's unofficial consumer Read Aloud endpoint, is best-effort/no SLA, can break, and is an optional install with a distinct license; provide Gemini alternative.
- [x] Document third-party text transfer in CLI/MCP guides and provider errors as not using Flow credits.
- [x] Update README comparison from Planned to `Yes (audio narrate: edge-tts or Gemini voices)`; update capabilities only if catalog enumerates this feature.
- [x] Update changelog Unreleased > Added and regenerate/check the plugin byte copy.

**Tests:**
- [x] Doc link, website mirror, skill-copy, and capability checks pass.

## Task 12 — Full gates and Flow-surface classification

**What:** Complete project verification and record that the feature does not touch Flow UI/generation surfaces.

**Files:** Documentation/checklist only if a gate requires a change.

**Steps:**
- [x] Run the project quality gates and the six mirror axes in `skills/check/SKILL.md` step 1b.
- [x] Run the `e2e_tts` CLI and MCP network tests; do not substitute unit tests or local-only `e2e_media`.
- [x] Confirm no Flow auth, Flow credits, generation API, or selectors are exercised; a live Flow run is out of scope for this local TTS capability.

**Tests:**
- [x] All offline gates pass and edge CLI/MCP E2E assertions pass.
- [x] Gemini provider E2E either passes with the dedicated key or is skipped with its reason.

---

## Definition of done

- [x] All task steps checked off.
- [x] Project quality gates green, with CLI and MCP parity across the six mirror axes.
- [x] `CHANGELOG.md` `[Unreleased]` section updated and required documentation/mirrors current.
- [x] Offline BDD covers all High cases from `SCENARIO.md`.
- [x] Installed CLI and MCP network E2E each synthesize edge narration, assemble a synthetic clip, and verify non-silent voice audio; Gemini keyless run skips with a stated reason.
- [x] No `# TODO` without a tracked issue link.
- [x] Red tests fail on the missing implementation for the expected reason.
- [x] Git status is clean.

## Decision log

| Date | Decision | Why | Rejected |
|---|---|---|---|
| 2026-11-21 | Use `audio narrate`, a dedicated Gemini TTS key, and edge default | Matches existing noun command groups; prevents unrelated LLM credentials crossing to Gemini; keyless edge is the v1 default provider | Reuse `GFLOW_CLI_LLM_API_KEY` only with Google base URL; config-level provider defaults |
| 2026-11-21 | Gemini rejects non-default rate/pitch; no timing recovery or `audio voices` in v1 | The Gemini TTS API documents no native rate/pitch parameters; timings are not required for assembly and voice listing adds provider-specific surface | Style prompt emulation, STT timing decorator, network voice catalog command |
| 2026-11-21 | Add `e2e_tts` | TTS requires network but no Flow profile or credits; `e2e_media` is explicitly local-only | Mislabel as `e2e_media`, which would hide its network dependency |
| 2026-11-21 | Send edge text unchanged; no `WordTiming` or word-boundary metadata | Normalization would rewrite the caller's text, and nothing in v1 consumes timings | Typographic and emoji normalization; a timing type with no consumer |
