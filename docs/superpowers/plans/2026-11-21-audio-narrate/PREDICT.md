# Predict: Audio narration and voiceover generation (#1357)

## Verdict: CAUTION
**Confidence:** 7/10

## Summary
The required spike is already answered by `.local/voice-ports-evaluation.md`: Flow exposes preset Character voice metadata and samples, but no standalone text-to-audio track endpoint. The port and adapter design fits the existing `Transcriber` boundary, and the video assembler already accepts a local voiceover track. CAUTION remains because endpoint terms and the `edge-tts` license fit have not been independently reviewed; the endpoint is unofficial and its operation can change. Keep it optional and clearly disclose the terms/availability risk in USAGE and CONFIGURATION beside the Gemini alternative.

## Persona findings

### Architect — GO (8/10)
Place immutable synthesis request/result/timing values and `SpeechSynthesizer` in `services/speech_ports.py`; keep edge and Gemini adapters in `services/transcribers/`'s sibling adapter layout (a dedicated `services/speech_synthesizers/` directory is clearer than expanding transcription-specific names). A separate narration service, CLI module and MCP module preserve module boundaries. Reuse the existing local assembly input; do not add timing recovery in v1.

### Security / reCAPTCHA — GO (8/10)
No Flow auth, browser, or reCAPTCHA path is involved. Narration text leaves the machine for either selected provider, so disclose that in CLI help and the MCP description. Use a dedicated `GFLOW_CLI_GEMINI_TTS_API_KEY` setting: reusing the general LLM key could send a credential intended for an unrelated endpoint. Never log text, keys, or authorization headers; use `SecretStr` and redact provider exceptions.

### Performance / Playwright — CAUTION (7/10)
No browser or generation hot path is involved. Use bounded HTTP timeouts for Gemini and edge-tts' async stream; retry only the observed `NoAudioReceived` transient, with a small bounded retry. Stream audio chunks and write through a same-directory temporary file, then publish without overwriting; clean up on cancellation or provider/write failure. Keep blocking file work off the event loop.

### CLI UX / Cross-platform — CAUTION (6/10)
Use the standalone `gflow audio narrate TEXT --out-file FILE` surface with `--text-file` alternative, `--provider`, `--voice`, `--rate`, `--pitch`, and JSON output. Preserve `gflow video assemble --voiceover`. Edge supports native rate/pitch. Gemini has no equivalent controls established by the supplied evidence; reject non-default rate/pitch with a typed, actionable error rather than imply a style prompt guarantees those controls. Add an MCP `gflow_narrate` twin; explain it sends text to the selected third party and keep it available under `--no-spend` because it spends no Flow credits.

### Devil's Advocate — CAUTION (5/10)
The user-facing need is one narration file, so omit timing recovery, script alignment, acoustic onset snapping, caption generation, OpenAI-compatible provider support, config-level provider defaults, and a broad voice discovery abstraction from v1. A `voices` command is optional and only worthwhile if it can list the existing `VOICES` and edge voice catalog cheaply without network calls. The edge endpoint is unofficial and can break or change terms; keep it optional and document that risk beside the install and provider instructions. Gemini is the supported-key path when that risk is unacceptable.

## High-confidence risks (flagged by 2+ personas)
1. Narration text and credentials cross a provider boundary: disclose destinations and redact text/secrets from logs and errors.
2. Provider contracts can change: use bounded failures and avoid retrying non-transient failures; edge is explicitly best-effort.

## Conflicts resolved
- Gemini rate/pitch: style prompting may influence delivery but does not guarantee parameter semantics. Refuse non-default values for Gemini in v1.
- Flow voice-track spike: the evaluation confirms Flow has voice metadata/samples for Characters but no standalone narration audio endpoint. Use external/local TTS for the standalone track rather than changing Flow surfaces.
- LLM key reuse vs dedicated key: use the dedicated Gemini TTS setting to prevent an unrelated key from being forwarded.
- Voice listing: defer unless static Gemini voices plus a cheap edge catalog can be presented without adding a second provider interaction path.

## Required mitigations before EXECUTE
1. Add a dedicated `GFLOW_CLI_GEMINI_TTS_API_KEY` `SecretStr`; never fall back to `GFLOW_CLI_LLM_API_KEY`.
2. Document that edge-tts uses Microsoft's unofficial consumer Read Aloud endpoint, has no SLA, may break, and is an optional dependency; provide Gemini as the alternative.
3. Define bounded timeouts, typed provider errors, non-overwriting output behavior, and no logging of narration text or credentials.
4. Register a network-dependent `e2e_tts` marker; the edge CLI and MCP live runs must also assemble the resulting audio and assert non-silent voice output. Gemini e2e skips explicitly when its dedicated key is unset.

## Recommended next step
Proceed to scenario and plan with the mitigations above; no live API or terms claim is verified by this planning pass.
