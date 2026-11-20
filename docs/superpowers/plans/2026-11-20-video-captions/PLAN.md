# Video Transcription and Burned-in Captions Implementation Plan (#1358)

> **For agentic workers:** Run `/gflow:status --feature 2026-11-20-video-captions` to find
> the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Add `gflow video transcribe` and `gflow video captions` (plus MCP twins
`gflow_video_transcribe` and `gflow_video_captions`) so users and agents can transcribe a local
video clip or catalog video UUID to a SubRip (`.srt`) file via a user-configured
OpenAI-compatible `/audio/transcriptions` endpoint and burn bottom-centred captions into an
`.mp4` clip locally without spending Flow credits or launching a browser.

**Architecture:**
- **Domain service (`src/gflow_cli/services/video_captions.py`):** Owns pure SubRip parsing
  and formatting (`CaptionEntry`, `parse_srt`, `format_srt`), catalog-or-path video source
  resolution (`resolve_video_source` via `services.media_recovery.resolve_asset` and
  `data._repo_base.verified_local_path`), 16 kHz mono WAV audio extraction via PyAV,
  OpenAI-compatible `POST {base_url}/audio/transcriptions` multipart upload via `httpx`
  (`follow_redirects=False`), and per-frame caption rendering using PyAV + Pillow
  (`ImageFont.load_default(size=...)`, `ImageDraw.rounded_rectangle`).
- **Thin CLI adapter (`src/gflow_cli/cli_video_captions.py`, registered in `src/gflow_cli/cli_video.py`):**
  Exposes `@spends_credits(False)` Click commands `gflow video transcribe` and
  `gflow video captions` in a dedicated module so `cli_video.py` (2,437 lines against a
  2,685-line `check_size.py` ratchet) only receives 4 registration lines at the bottom and
  rebases cleanly onto `#1359` (`video assemble`).
- **Thin MCP adapter (`src/gflow_cli/mcp/video_captions_tools.py`, re-exported in `src/gflow_cli/mcp/tools.py`):**
  Exposes `@server.tool` handlers `gflow_video_transcribe` and `gflow_video_captions` wrapped
  in `@_guarded`, offloading blocking work via `asyncio.to_thread` and confining all written
  files inside `GFLOW_CLI_OUTPUT_DIR`. Keeping the tool bodies in a sibling module and trimming
  a 4-line obsolete comment in `mcp/tools.py` preserves `file:src/gflow_cli/mcp/tools.py`
  under its 2,691-line ratchet in `scripts/ci/size_baseline.json`.

---

## Re-verified Spike & Empirical Evidence

1. **PyAV filter availability and Pillow per-frame burn-in:**
   Re-running the caption burn-in spike against a synthetic 2.0 s H.264/AAC clip (50 frames @
   25 fps, 320x240, 440 Hz stereo AAC) and a 2-entry `.srt` file confirmed that prebuilt PyAV
   wheels lack `libass` and `libfreetype` filters, while Pillow per-frame compositing runs at
   ~5.4x real-time speed with zero pixel drift on uncaptioned frames and intact audio:
   ```text
   Font loaded: FreeTypeFont 24
   subtitles in filters: False
   drawtext in filters: False
   aac encoder: True
   wav muxer: True
   Wrote SRT to .local\spike\sample.srt
   Parsed 2 caption entries.
   Burning captions into .local\spike\test_clip1_captions.mp4...
   Burn stats: {'elapsed_s': 0.372, 'total_frames': 50, 'frames_burned': 31}
   Verifying burned video vs original...
   Verification result: {'output_size': 41654, 'total_frames': 50, 'burned_frames': 31, 'unburned_frames': 19, 'avg_diff_burned': 4.398, 'avg_diff_unburned': 0.0, 'has_audio': True, 'verified': True}
   ```
2. **Default Google OpenAI-compatible endpoint probe (`DEFAULT_LLM_BASE_URL`):**
   Probing `POST https://generativelanguage.googleapis.com/v1beta/openai/audio/transcriptions`
   with `httpx` confirmed:
   ```text
   Status: 404 Body:
   ```
   Google's Gemini OpenAI-compatibility surface implements `/chat/completions` and `/models`,
   but does **not** serve `/audio/transcriptions`. Therefore, transcription requires the user
   to configure `GFLOW_CLI_LLM_BASE_URL` to a provider that serves `POST /audio/transcriptions`
   (such as OpenAI `https://api.openai.com/v1`, Groq `https://api.groq.com/openai/v1`, or a
   local `whisper.cpp` / `faster-whisper-server` / `vLLM` / `LiteLLM` server at
   `http://127.0.0.1:8080/v1`), along with `GFLOW_CLI_LLM_API_KEY` (optional for keyless
   loopback servers) and `GFLOW_CLI_LLM_MODEL` (defaulting to `"whisper-1"` when unset on a
   configured endpoint).

---

## Predict verdict: CAUTION — confidence 8.0/10

Five adversarial personas evaluated the proposal before implementation. No persona returned
`STOP`; three returned `CAUTION` with concrete mitigations folded into Tasks 1–7.

### Persona findings

#### 1. Architect — CAUTION (8/10)
- **Hexagonal boundaries:** Pure SubRip parsing/formatting (`CaptionEntry`, `parse_srt`,
  `format_srt`) and PyAV/Pillow/httpx orchestration belong in
  `src/gflow_cli/services/video_captions.py` with no Click or MCP imports.
- **Size-gate hazard (`scripts/ci/check_size.py`):** `src/gflow_cli/mcp/tools.py` sits at
  2,690 lines against a ratchet ceiling of 2,691 lines (1 line of headroom), and
  `src/gflow_cli/cli_video.py` sits at 2,437 lines against 2,685, with `#1359`
  (`video assemble`) landing first in both files. Putting full CLI or MCP bodies into those
  files will breach `check_size.py` and cause merge conflicts with `#1359`.
- **Mitigation:** Place Click commands in `src/gflow_cli/cli_video_captions.py` (registered at
  the bottom of `cli_video.py`) and MCP tools in `src/gflow_cli/mcp/video_captions_tools.py`
  (re-exported in `mcp/tools.py` while removing an obsolete 4-line `#499` comment so line count
  does not grow). Guard `av` and `PIL` imports up front so a missing `gflow-cli[chain]` extra
  raises `FrameExtractionError` (exit 20) before any network call.

#### 2. Security / reCAPTCHA — CAUTION (8/10)
- **Zero Flow / WAF / reCAPTCHA touch:** No requests reach `aisandbox-pa.googleapis.com` or
  `flow.google.com`, and no browser profile is opened.
- **Audio privacy & redirect exfiltration:** `Settings.llm_base_url` defaults to Google's
  endpoint. Audio must never leave the machine unless the user has explicitly configured a
  non-default transcription endpoint in `GFLOW_CLI_LLM_BASE_URL`. An HTTP 3xx redirect on the
  transcription POST could leak `Authorization: Bearer` and audio bytes to a third-party host.
- **File-content leak via `--srt` and MCP output confinement (`GHSA-hqfj-4jww-76f2`):** A
  hostile prompt could pass a sensitive file path as `srt` hoping a parse error echoes its lines,
  or pass an input `file` outside `GFLOW_CLI_OUTPUT_DIR` with `out_file=None` hoping the
  default `<stem>.srt` / `<stem>-captions.mp4` writes next to the input file outside the
  output directory.
- **Mitigations:** (1) Refuse transcription pre-flight (exit 11) when `llm_base_url == DEFAULT_LLM_BASE_URL`;
  (2) use `httpx.Client(follow_redirects=False)` and redact error bodies via `redact_error_detail`;
  (3) never echo raw line contents in `.srt` parse errors; (4) on MCP, always resolve both
  explicit and default `.srt` / `.mp4` output paths inside `settings.output_dir.resolve()`.

#### 3. Performance / Playwright — GO (9/10)
- **No Playwright or Page-pool contention:** Needs no browser and acquires no `_profile_lock`
  or `ProfileLease`.
- **Audio extraction & async event loop:** Extracting a 16 kHz mono 16-bit PCM WAV buffer in
  memory (~32 KB/s, ~256 KB for an 8 s clip) avoids uploading multi-megabyte video containers.
  MCP tool handlers must wrap synchronous service execution in `await asyncio.to_thread(...)`
  so PyAV/Pillow frame rendering (~0.37 s for 50 frames at 320x240) never blocks the MCP
  server's `asyncio` loop.

#### 4. CLI and MCP UX / Cross-platform — CAUTION (7/10)
- **Default endpoint UX:** Because Google's default `GFLOW_CLI_LLM_BASE_URL` returns HTTP 404
  on `/audio/transcriptions`, the pre-flight `ConfigurationError` (exit 11, `charged="no"`,
  `next_action="fix_input"`) must give an actionable remediation hint naming
  `GFLOW_CLI_LLM_BASE_URL`, `GFLOW_CLI_LLM_API_KEY`, `GFLOW_CLI_LLM_MODEL`, and the offline
  `--srt` alternative.
- **Six mirror axes (`skills/check/SKILL.md` step 1b):** Map `"video transcribe"` ->
  `"gflow_video_transcribe"` and `"video captions"` -> `"gflow_video_captions"` in
  `tests/mcp/test_cli_parity.py`. Use identical parameter names (`file`, `srt`, `language`,
  `out_file` via `out_file_option`, `profile`) so `test_every_cli_option_reaches_its_mcp_tool`
  passes with zero exemptions. Mark both commands `@spends_credits(False)` and keep both tools
  out of `mcp.server._SPEND_TOOLS`.
- **Cross-platform text & fonts:** Decode `.srt` files with `encoding="utf-8-sig"` (handling
  Windows UTF-8 BOMs and CRLF) and render text with Pillow's built-in scalable
  `ImageFont.load_default(size=max(14, int(round(height * 0.045))))` so rendering is identical
  on Windows, macOS, and Linux without system font lookups.

#### 5. Devil's Advocate — GO (8/10)
- **Why no `faster-whisper` extra in v1 (YAGNI):** `faster-whisper` pulls in `ctranslate2`,
  `onnxruntime`, `tokenizers`, and `huggingface-hub` (>200 MB of native wheels plus 75–1,500 MB
  of model weight downloads on first run and ~500 MB RAM). `httpx` is already an eager
  dependency (`pyproject.toml:69`), works with both hosted APIs (OpenAI, Groq) and local
  self-hosted servers (`whisper.cpp`, `faster-whisper-server`, `vLLM`, `LiteLLM`) through the
  existing `GFLOW_CLI_LLM_*` settings, and users with offline transcripts can pass `--srt`.
- **Why reuse existing error classes:** `FrameExtractionError` (exit 20) already covers a
  missing `gflow-cli[chain]` extra and undecodable video containers; `DataStoreError` (exit 16)
  covers catalog UUID lookups; `ConfigurationError` (exit 11) covers malformed `.srt`,
  audio-less clips, and unconfigured/rejected transcription endpoints; `RateLimitError`
  (exit 4/40) and `NetworkError` (exit 6/42) cover upstream rate limits and transient network
  faults. Adding new error classes would bloat `EXIT_CODE_MAP` without enabling any new caller
  branching decision.
- **Scope discipline:** Keep v1 styling to one readable default (bottom-centred, semi-transparent
  dark rounded pill, white text scaled to frame height). Styling flags (`--font-size`, colours,
  positioning, word-level karaoke) are recorded as out of scope.

### Risk register

| Severity | Risk | Mitigation |
|---|---|---|
| Critical | Audio sent to Google's default endpoint (HTTP 404) or without explicit user configuration | Pre-flight guard refuses transcription with `ConfigurationError` (exit 11, `charged="no"`) whenever `settings.llm_base_url` equals `DEFAULT_LLM_BASE_URL` (SCENARIO #1) |
| Critical | HTTP 3xx redirect forwards `Authorization: Bearer` and audio bytes to an untrusted host | Use `httpx.Client(follow_redirects=False)` and raise `ConfigurationError` (exit 11) on 3xx responses (SCENARIO #2) |
| Critical | MCP tool writes default `<stem>.srt` or `<stem>-captions.mp4` next to an input file outside `GFLOW_CLI_OUTPUT_DIR` | On MCP, resolve both explicit `out_file` and default output paths inside `settings.output_dir.resolve()` via `_confine_to_output_dir` (SCENARIO #3) |
| Critical | `.srt` parse error echoes raw file text from a path-traversal or prompt-injection target | Report only the file name and 1-based block/line number in `ConfigurationError`, never raw line text (SCENARIO #4) |
| High | Adding CLI/MCP functions directly in `cli_video.py` / `mcp/tools.py` breaches `check_size.py` (2,691-line limit on `mcp/tools.py`) or conflicts with `#1359` | Put implementations in `cli_video_captions.py` and `mcp/video_captions_tools.py`; register/re-export with minimal lines and trim a 4-line obsolete comment in `mcp/tools.py` |
| High | Missing `gflow-cli[chain]` extra fails after transcription has already run | Check `av` and `PIL` availability at the start of `transcribe_video` and `caption_video`, raising `FrameExtractionError` (exit 20, `charged="no"`) up front (SCENARIO #5) |

---

## Severity-Ranked Scenario Table

Full 13-dimension coverage map: [`SCENARIO.md`](SCENARIO.md). Gherkin specification:
[`tests/features/video_captions.feature`](../../../../tests/features/video_captions.feature).

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D9 / D7 | `video transcribe` (or `video captions` without `--srt`) when `GFLOW_CLI_LLM_BASE_URL` is at the Google default | **Critical** | Fails pre-flight before extracting/sending audio with `ConfigurationError` (exit 11, `charged: "no"`, `next_action: "fix_input"`); hint names `GFLOW_CLI_LLM_BASE_URL`, `GFLOW_CLI_LLM_API_KEY`, `GFLOW_CLI_LLM_MODEL`, and `--srt` | Unit + BDD + E2E |
| 2 | D9 / D7 | Configured transcription endpoint returns an HTTP 3xx redirect | **Critical** | `follow_redirects=False` refuses the redirect and raises `ConfigurationError` (exit 11, `charged: "no"`) | Unit |
| 3 | D13 / D8 | MCP `out_file` escapes `GFLOW_CLI_OUTPUT_DIR`, or `out_file` is omitted when source `file` is outside `GFLOW_CLI_OUTPUT_DIR` | **Critical** | Escaping `out_file` returns 400 `Invalid Output`; omitted `out_file` writes `<output_dir>/<stem>.srt` and `<output_dir>/<stem>-captions.mp4` inside `GFLOW_CLI_OUTPUT_DIR` | Unit (MCP) + BDD |
| 4 | D11 / D7 | Malformed `.srt` file or non-SRT file passed to `--srt` / MCP `srt` | **Critical** | Raises `ConfigurationError` (exit 11, `charged: "no"`) naming block/line index only, never raw file content | Unit + BDD |
| 5 | D7 | Optional `gflow-cli[chain]` extra (`av` or `pillow`) is absent | **High** | Raises `FrameExtractionError` (exit 20, `charged: "no"`) with `pip install 'gflow-cli[chain]'` hint before any network call | Unit |
| 6 | D11 | Offline burn-in: `gflow video captions clip.mp4 --srt sample.srt` | **High** | Burns captions into frames within `[start_s, end_s]`, leaves uncaptioned frames untouched, preserves audio stream, writes `clip-captions.mp4`, exits 0 | Unit + BDD + E2E |
| 7 | D9 | Combined transcription + burn-in: `gflow video captions clip.mp4` (no `--srt`) | **High** | Extracts 16 kHz mono WAV, POSTs to `{base_url}/audio/transcriptions`, writes `clip.srt` and `clip-captions.mp4`, exits 0 | Unit + BDD |
| 8 | D6 / D7 | `FILE` is a catalog media UUID (`8-4-4-4-12` hex) | **High** | Resolves via `resolve_asset` + `verified_local_path`; missing/ambiguous/non-video/unverified UUID raises `DataStoreError` / `ConfigurationError` (exit 16 / 11, `charged: "no"`) | Unit + BDD |
| 9 | D11 / D7 | Input clip has no audio stream (`container.streams.audio` empty) | **High** | `transcribe` (and `captions` without `--srt`) fails pre-network with `ConfigurationError` (exit 11, `charged: "no"`); `captions --srt` succeeds on silent video | Unit |
| 10 | D9 / D7 | Transcription endpoint returns HTTP 400/401/403/404/405/422, 429, or 5xx / timeout | **High** | 400/401/403/404/405/422 -> `ConfigurationError` (exit 11); 429 -> `RateLimitError` (exit 4/40); 5xx / timeout -> `NetworkError` (exit 6/42); all `charged: "no"`, detail redacted | Unit |
| 11 | D13 | MCP parity (`test_cli_parity.py`) and `--no-spend` mode | **High** | `gflow_video_transcribe` and `gflow_video_captions` match CLI flags with zero exemptions and remain registered under `GFLOW_MCP_NO_SPEND=1` | Unit (MCP) + E2E |
| 12 | D9 | Transcription endpoint returns JSON (`segments` or `text`) instead of raw SRT | **Medium** | Normalizes JSON segments/text into `list[CaptionEntry]` and writes canonical `.srt` | Unit |
| 13 | D8 / D11 | `.srt` with UTF-8 BOM, CRLF, dot milliseconds (`00:00:01.200`), multi-line text, or `end_s <= start_s` | **Medium** | Accepts BOM, CRLF, dot/comma ms, and multi-line text; rejects `end_s <= start_s` with `ConfigurationError` (exit 11) | Unit |
| 14 | D8 | Cross-platform font rendering and Unicode / space file paths | **Medium** | `ImageFont.load_default(size=...)` renders without OS font paths; Unicode/space paths succeed | Unit |
| 15 | D11 / D7 | `GFLOW_CLI_STORAGE_URI` set (`s3://` / `gs://`) with `--out-file` | **Medium** | `refuse_cloud_storage_for_local_file` raises `ConfigurationError` (exit 11, `charged: "no"`) | Unit |
| 16 | D12 | `--json` and `-o id` stdout purity | **Medium** | `--json` emits one JSON object; `-o id` emits the output file path; logs stay on stderr | Unit + E2E |
| 17 | D5 | Blocking work in async MCP tools | **Low** | Offloaded via `await asyncio.to_thread(...)` | Unit (MCP) |

---

## File structure

### New files
```text
src/gflow_cli/services/video_captions.py
  SRT parser/formatter, catalog/file resolver, PyAV WAV extractor, httpx transcription client, Pillow/PyAV caption burner
src/gflow_cli/cli_video_captions.py
  Click commands `gflow video transcribe` and `gflow video captions` (@spends_credits(False))
src/gflow_cli/mcp/video_captions_tools.py
  MCP tools `gflow_video_transcribe` and `gflow_video_captions` (@_guarded, output-dir confined)
tests/services/test_video_captions.py
  Unit tests for SRT parsing/formatting, catalog resolution, transcription client, and Pillow/PyAV burn-in
tests/features/video_captions.feature
  Offline Gherkin scenarios for video transcribe and video captions (CLI and MCP)
tests/features/test_video_captions_steps.py
  pytest-bdd step bindings for video_captions.feature
tests/cli/test_cli_video_captions.py
  Click runner tests for `gflow video transcribe` and `gflow video captions`
tests/mcp/test_video_captions_mcp.py
  MCP tool unit tests for `gflow_video_transcribe` and `gflow_video_captions`
tests/e2e/test_video_captions_e2e.py
  End-to-end verification of installed CLI subprocess and MCP tool on real/synthetic video clips
```

### Modified files
```text
src/gflow_cli/cli_video.py
  Register `transcribe` and `captions` on the `video` Click group (4 lines at bottom; rebases onto #1359)
src/gflow_cli/mcp/tools.py
  Re-export `gflow_video_transcribe` and `gflow_video_captions` (trimming 4 obsolete comment lines to stay <= 2691 lines)
tests/mcp/test_cli_parity.py
  Map `"video transcribe": "gflow_video_transcribe"` and `"video captions": "gflow_video_captions"` in `CLI_TO_MCP`
README.md
  Update comparison table row "Captions" from `Planned (#1358)` to `Yes (\`video captions\`, \`.srt\` export)`
AGENTS.md, skills/gflow-cli/SKILL.md, plugins/gflow/skills/gflow-cli/SKILL.md
  Add `transcribe` and `captions` to `gflow video` command surface
docs/USAGE.md, docs/MCP.md, docs/CONFIGURATION.md, .env.template, website/docs/*
  Document `gflow video transcribe`, `gflow video captions`, MCP tools, and `GFLOW_CLI_LLM_*` audio privacy
CHANGELOG.md
  Add `[Unreleased]` -> `### Added` entry for #1358
```

---

## Task 1 — Unit test scaffold (red)

**What:** Write failing unit tests for SRT parsing/formatting, source resolution, audio
extraction, OpenAI-compatible transcription, and Pillow/PyAV caption burn-in before creating
`src/gflow_cli/services/video_captions.py`.

**Files:**
- `tests/services/test_video_captions.py` — comprehensive unit test suite targeting `gflow_cli.services.video_captions`

**Steps:**
- [x] Create `tests/services/test_video_captions.py` with synthetic PyAV H.264/AAC `.mp4` and video-only `.mp4` fixtures.
- [x] Run `uv run pytest tests/services/test_video_captions.py` and confirm it fails red with `ModuleNotFoundError: No module named 'gflow_cli.services.video_captions'`.
- [x] Commit the red test scaffold noting the failure line in the commit message.

**Tests created (red):**
- [x] `test_parse_and_format_srt_roundtrip` — parses standard SRT blocks, comma and dot millisecond separators (`00:00:00,500` and `00:00:01.200`), UTF-8 BOM (`\ufeff`), CRLF (`\r\n`), and multi-line text, then formats back to canonical SRT (SCENARIO #13).
- [x] `test_parse_srt_rejects_empty_malformed_or_inverted_timestamps_without_echoing_content` — raises `ConfigurationError` (exit 11, `charged="no"`, `next_action="fix_input"`) on empty files, non-SRT text, or `end_s <= start_s`, and asserts a sentinel secret line in the malformed input never appears in `exc.detail` (SCENARIO #4, #13).
- [x] `test_missing_chain_extra_raises_frame_extraction_error_exit_20` — monkeypatching `sys.modules["av"] = None` (or `PIL = None`) causes `transcribe_video` and `caption_video` to raise `FrameExtractionError` (exit 20, `charged="no"`) with the `pip install 'gflow-cli[chain]'` hint before any HTTP call (SCENARIO #5).
- [ ] `test_resolve_video_source_local_path_and_catalog_uuid` — resolves an existing local `.mp4` path and a catalog `AssetKind.VIDEO` UUID with a verified local file; raises `ConfigurationError` (exit 11, `charged="no"`) on a missing file or non-video catalog UUID and `DataStoreError` (exit 16, `charged="no"`) on an unknown UUID or missing on-disk copy (SCENARIO #8).
- [x] `test_transcribe_video_refuses_default_google_endpoint_before_sending_audio` — with `GFLOW_CLI_LLM_BASE_URL` at `DEFAULT_LLM_BASE_URL`, raises `ConfigurationError` (exit 11, `charged="no"`) naming `GFLOW_CLI_LLM_BASE_URL` and `--srt`, with zero calls to the HTTP transport (SCENARIO #1).
- [x] `test_transcribe_video_refuses_clip_without_audio_stream` — on a video-only `.mp4`, raises `ConfigurationError` (exit 11, `charged="no"`) naming the missing audio track before any HTTP request (SCENARIO #9).
- [ ] `test_transcribe_video_posts_wav_and_parses_srt_and_json_responses` — extracts 16 kHz mono WAV bytes, calls `POST {base_url}/audio/transcriptions` with `follow_redirects=False`, handles both raw SRT text and JSON `{"segments": [...]}` / `{"text": "..."}` responses, and writes `<stem>.srt` (SCENARIO #7, #12).
- [ ] `test_transcribe_video_maps_http_errors_and_refuses_redirects` — maps 302 redirect and HTTP 400/401/403/404/405/422 to `ConfigurationError` (exit 11), HTTP 429 to `RateLimitError` (exit 4/40), and HTTP 503 / timeout to `NetworkError` (exit 6/42), all with `charged="no"` and redacted error details (SCENARIO #2, #10).
- [x] `test_caption_video_burns_frames_and_preserves_audio` — burns captions from an `.srt` file (and on a silent clip without audio), verifying `frames_burned > 0`, pixel difference on captioned frames, zero pixel difference on uncaptioned frames, and preserved audio stream (SCENARIO #6, #9, #14).
- [ ] `test_caption_video_without_srt_transcribes_first_and_writes_both_files` — when `srt_path=None`, invokes transcription, writes `<stem>.srt`, and burns `<stem>-captions.mp4` (SCENARIO #7).

---

## Task 2 — BDD scaffold (red)

**What:** Bind the offline Gherkin feature [`tests/features/video_captions.feature`](../../../../tests/features/video_captions.feature)
from `tests/features/test_video_captions_steps.py`.

**Files:**
- `tests/features/video_captions.feature` — 7 offline Gherkin scenarios (committed in Phase 3)
- `tests/features/test_video_captions_steps.py` — step definitions driving the CLI runner and MCP tools with a fake transcription transport

**Steps:**
- [x] Write `tests/features/test_video_captions_steps.py` calling `scenarios("video_captions.feature")`.
- [x] Run `uv run pytest tests/features/test_video_captions_steps.py tests/features/test_e2e_binding_guard.py` and verify `test_e2e_binding_guard.py` is green while the 7 BDD scenarios fail red awaiting Task 3–5 implementations.

**Tests created (red):**
- [ ] `test_burning_an_authored_srt_file_into_a_local_video_clip_runs_offline` (SCENARIO #6)
- [ ] `test_burning_captions_into_a_catalog_video_resolves_the_media_uuid_to_its_verified_local_file` (SCENARIO #8)
- [ ] `test_transcribing_a_video_clip_sends_extracted_audio_to_a_configured_openaicompatible_endpoint` (SCENARIO #7)
- [ ] `test_omitting_srt_on_video_captions_transcribes_first_and_writes_both_the_srt_and_the_captioned_mp4` (SCENARIO #7)
- [ ] `test_transcription_is_refused_before_sending_audio_when_gflow_cli_llm_base_url_is_left_at_the_google_default` (SCENARIO #1)
- [ ] `test_malformed_or_empty_srt_files_are_refused_with_exit_code_11_without_echoing_raw_file_content` (SCENARIO #4)
- [ ] `test_the_mcp_tools_transcribe_and_burn_captions_inside_the_configured_output_directory` (SCENARIO #3, #11)

---

## Task 3 — Core service & SRT parser (`src/gflow_cli/services/video_captions.py`)

**What:** Implement `src/gflow_cli/services/video_captions.py` to make Task 1's unit tests green.

**Files:**
- `src/gflow_cli/services/video_captions.py` — domain types, SRT parser/formatter, catalog source resolver, WAV audio extractor, OpenAI-compatible httpx transcription client, and Pillow/PyAV frame burner

**Steps:**
- [x] Define frozen dataclasses `CaptionEntry(index: int, start_s: float, end_s: float, text: str)`, `TranscribeResult(source_file: Path, srt_file: Path, language: str | None, duration_seconds: float, segments: tuple[CaptionEntry, ...])`, and `CaptionsResult(source_file: Path, output_file: Path, srt_file: Path, duration_seconds: float, total_frames: int, frames_burned: int, bytes: int)`.
- [x] Implement `parse_srt(content: str, *, source_name: str = "<srt>") -> list[CaptionEntry]` and `format_srt(entries: Sequence[CaptionEntry]) -> str`. Strip UTF-8 BOM (`\ufeff`), normalize `\r\n`, accept `,` or `.` millisecond separators, validate `0 <= start_s < end_s`, and raise `ConfigurationError(..., charged="no", next_action="fix_input")` naming only `source_name` and block/line numbers on invalid input.
- [x] Implement `_require_chain_extra() -> tuple[Any, Any]` importing `av` and `PIL` (`Image`, `ImageDraw`, `ImageFont`) and raising `FrameExtractionError(detail=..., charged="no", next_action="fix_input")` when either package is missing.
- [x] Implement `resolve_video_source(file_or_id: str, *, profile: str | None, route: str) -> Path`: if `is_media_uuid(file_or_id)`, call `resolve_asset(file_or_id, profile=profile, route=route)` inside `nothing_submitted()`, verify `asset.kind is AssetKind.VIDEO` (else raise `ConfigurationError(..., charged="no")`), and return the first `verified_local_path(lf)` (else raise `DataStoreError(..., charged="no", remediation_hint=f"Run `gflow data download {file_or_id}` first, or pass a local video file path.")`); otherwise validate `Path(file_or_id).expanduser().resolve()` is an existing file (else raise `ConfigurationError(..., charged="no")`).
- [x] Implement `extract_wav_bytes(video_path: Path) -> tuple[bytes, float]`: decode the video container's first audio stream with PyAV, resample to 16 kHz mono `s16` PCM into an in-memory `io.BytesIO` WAV container, and return `(wav_bytes, duration_seconds)`. Raise `ConfigurationError(..., charged="no")` if the container has no audio stream or decodes zero audio frames, and `FrameExtractionError(..., charged="no")` if the container is corrupt/undecodable.
- [x] Implement `transcribe_video(...) -> TranscribeResult`:
  - Check `_require_chain_extra()` and `refuse_cloud_storage_for_local_file` when `out_file` is given.
  - Enforce the privacy & default-endpoint gate: if `(settings.llm_base_url or DEFAULT_LLM_BASE_URL).rstrip("/") == DEFAULT_LLM_BASE_URL`, raise `ConfigurationError` (exit 11, `charged="no"`, `next_action="fix_input"`) explaining that Google's default endpoint does not offer `/audio/transcriptions` and gflow never sends audio to an unconfigured endpoint, with remediation naming `GFLOW_CLI_LLM_BASE_URL`, `GFLOW_CLI_LLM_API_KEY`, `GFLOW_CLI_LLM_MODEL`, and `--srt`.
  - Extract 16 kHz mono WAV bytes via `extract_wav_bytes` and POST multipart form data (`file=("audio.wav", wav_bytes, "audio/wav")`, `model=settings.llm_model or "whisper-1"`, `response_format="srt"`, plus optional `language`) to `{base_url}/audio/transcriptions` using `httpx.Client(follow_redirects=False, timeout=120.0)`.
  - Map 3xx and HTTP 400/401/403/404/405/422 to `ConfigurationError(..., charged="no")`, HTTP 429 to `RateLimitError(..., charged="no")`, and HTTP 5xx / `httpx.HTTPError` to `NetworkError(..., charged="no")`, redacting response text with `redact_error_detail`.
  - Parse either raw SRT response text or JSON (`segments` list or `text` fallback), write the `.srt` file with `encoding="utf-8"`, log `video.transcribe.completed` (host only, never keys/URLs), and return `TranscribeResult`.
- [x] Implement `caption_video(...) -> CaptionsResult`:
  - Check `_require_chain_extra()` and `refuse_cloud_storage_for_local_file` when `out_file` is given.
  - When `srt_path` is provided, read it with `encoding="utf-8-sig"` and parse via `parse_srt`; when `srt_path` is `None`, call `transcribe_video` first to produce `<default_srt_path>` and its `CaptionEntry` list.
  - Decode video frames with PyAV, render active captions (`start_s <= pts_s <= end_s`) at the bottom centre using Pillow (`ImageFont.load_default(size=max(14, int(round(height * 0.045))))`, semi-transparent dark rounded box `fill=(0, 0, 0, 180)`, white text `fill=(255, 255, 255, 255)`), encode video to H.264 (`libx264`, `yuv420p`), re-encode/mux the audio stream when present, write `<stem>-captions.mp4` (or `out_file`), and return `CaptionsResult`.
- [x] Run `uv run pytest tests/services/test_video_captions.py` and confirm all unit tests pass green.

---

## Task 4 — CLI surface (`src/gflow_cli/cli_video_captions.py` + `src/gflow_cli/cli_video.py`)

**What:** Add `gflow video transcribe` and `gflow video captions` Click commands in
`src/gflow_cli/cli_video_captions.py` and register them in `src/gflow_cli/cli_video.py` so the
adapter rebases cleanly onto `#1359` (`video assemble`) without breaching `check_size.py`.

**Files:**
- `src/gflow_cli/cli_video_captions.py` — Click command definitions and Rich/JSON/ID emitters
- `src/gflow_cli/cli_video.py` — 4 lines at the bottom registering `transcribe` and `captions` on `video`
- `tests/cli/test_cli_video_captions.py` — Click `CliRunner` tests for both commands

**Steps:**
- [x] Write failing CLI tests in `tests/cli/test_cli_video_captions.py` covering `--json`, `-o id`, table output, `-o out.srt` remediation (exit 2 `use --out-file`), catalog UUID `--profile` resolution, and exit codes 11 / 16 / 20.
- [x] Implement `src/gflow_cli/cli_video_captions.py`:
  - `@spends_credits(False)` on both `@click.command("transcribe", ...)` and `@click.command("captions", ...)`.
  - `transcribe` arguments/options: `@click.argument("file")`, `@out_file_option(help="Explicit output .srt file path (defaults to <name>.srt).")`, `@click.option("--language", default=None, help="ISO-639-1 language hint for transcription (e.g. 'en', 'es', 'pt').")`, `@click.option("--profile", default=None, help="Profile name when FILE is a catalog media UUID.")`, `@output_format_option(has_out_file=True)`.
  - `captions` arguments/options: `@click.argument("file")`, `@click.option("--srt", default=None, type=click.Path(dir_okay=False, path_type=Path), help="Existing .srt subtitle file to burn in. When omitted, transcribes FILE first and writes <name>.srt.")`, `@click.option("--language", default=None, help="ISO-639-1 language hint when transcribing (ignored when --srt is given).")`, `@out_file_option(help="Explicit output .mp4 file path (defaults to <name>-captions.mp4).")`, `@click.option("--profile", default=None, help="Profile name when FILE is a catalog media UUID.")`, `@output_format_option(has_out_file=True)`.
  - Route execution through `run_with_handlers(..., cli_command="video transcribe" | "video captions", as_json=is_json, output_format=output_format)`.
- [x] Register `video.add_command(transcribe)` and `video.add_command(captions)` at the bottom of `src/gflow_cli/cli_video.py`.
- [x] Run `uv run pytest tests/cli/test_cli_video_captions.py` and confirm all CLI tests pass.

---

## Task 5 — MCP surface mirror (`src/gflow_cli/mcp/video_captions_tools.py` + `src/gflow_cli/mcp/tools.py`)

**What:** Mirror `gflow video transcribe` and `gflow video captions` as MCP tools
`gflow_video_transcribe` and `gflow_video_captions`, wire `tests/mcp/test_cli_parity.py`, and
keep `src/gflow_cli/mcp/tools.py` within its 2,691-line `check_size.py` ratchet.

**Files:**
- `src/gflow_cli/mcp/video_captions_tools.py` — `gflow_video_transcribe` and `gflow_video_captions` tool definitions
- `src/gflow_cli/mcp/tools.py` — re-export both tools in module scope and `__all__` (trimming the obsolete 4-line `#499` comment at lines 2275–2278)
- `tests/mcp/test_cli_parity.py` — add `"video transcribe": "gflow_video_transcribe"` and `"video captions": "gflow_video_captions"` to `CLI_TO_MCP`
- `tests/mcp/test_video_captions_mcp.py` — MCP unit tests for both tools

**Steps:**
- [ ] Write failing MCP tests in `tests/mcp/test_video_captions_mcp.py` asserting tool registration, schema descriptions, output-directory confinement on both explicit `out_file` and omitted `out_file` (when `file` is outside `GFLOW_CLI_OUTPUT_DIR`), RFC 9457 error envelopes (`charged: "no"`), and availability under `GFLOW_MCP_NO_SPEND=1`.
- [ ] Implement `src/gflow_cli/mcp/video_captions_tools.py`:
  - `gflow_video_transcribe(file: str, out_file: OutFileParam = None, language: str | None = None, profile: str = _DEFAULT_PROFILE) -> dict[str, Any]`
  - `gflow_video_captions(file: str, srt: str | None = None, language: str | None = None, out_file: OutFileParam = None, profile: str = _DEFAULT_PROFILE) -> dict[str, Any]`
  - Both decorated with `@server.tool(...)` and `@_guarded`.
  - Resolve `out_file` via `_resolve_out_file(out_file, local_only=True)`; when `out_file` is `None`, confine default output paths (`<output_dir>/<stem>.srt` and `<output_dir>/<stem>-captions.mp4`) inside `get_settings().output_dir.resolve()`.
  - When `file` is a catalog media UUID, resolve `profile` via `_resolve_and_validate_profile(profile)` (when `file` is a local path, no profile is required).
  - Run `transcribe_video` / `caption_video` via `await asyncio.to_thread(...)`.
- [ ] Re-export `gflow_video_transcribe` and `gflow_video_captions` in `src/gflow_cli/mcp/tools.py` and update `CLI_TO_MCP` in `tests/mcp/test_cli_parity.py`.
- [ ] Run `uv run pytest tests/mcp/test_cli_parity.py tests/mcp/test_server.py tests/mcp/test_video_captions_mcp.py tests/features/test_video_captions_steps.py` and `uv run python scripts/ci/check_size.py` — all green.

---

## Task 6 — E2E verification (`tests/e2e/test_video_captions_e2e.py`)

**What:** Verify both the installed CLI (`python -m gflow_cli`) and the MCP tool surface end-to-end
under the Iron Law.

**Files:**
- `tests/e2e/test_video_captions_e2e.py` — `@pytest.mark.e2e` and `@pytest.mark.e2e_auth` ($0, no browser, no Flow credits)

**Steps:**
- [ ] Create `tests/e2e/test_video_captions_e2e.py` with markers `pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]`.
- [ ] **Burn-in E2E (CLI subprocess + MCP tool, always runs at $0):** Use a real `.mp4` clip from `out/` if present on disk, otherwise synthesise a 2.0 s H.264/AAC `.mp4` clip in `tmp_path` with PyAV. Run `python -m gflow_cli video captions <clip> --srt <sample.srt> --out-file <out.mp4> --json` and call `gflow_video_captions` over MCP. Decode both resulting `.mp4` files with PyAV and assert exit code 0, valid single-document JSON on stdout, intact audio stream, `frames_burned > 0`, positive pixel delta on captioned frames, and zero/near-zero delta on uncaptioned frames.
- [ ] **Transcription pre-flight E2E (CLI subprocess + MCP tool, always runs at $0):** With `GFLOW_CLI_LLM_BASE_URL` unset (defaulting to Google's endpoint), invoke `python -m gflow_cli video transcribe <clip> --json` and `gflow_video_transcribe`, asserting exit code 11, `type: "https://gflow-cli.dev/errors/configuration"`, `charged: "no"`, and `next_action: "fix_input"`.
- [ ] **Live transcription E2E (opt-in via `GFLOW_CLI_E2E_TRANSCRIPTION_BASE_URL`):** When `GFLOW_CLI_E2E_TRANSCRIPTION_BASE_URL` is set, run `video transcribe` and `gflow_video_transcribe` against that live endpoint and assert a non-empty `.srt` file is written; when unset, skip that single live-endpoint test naming the external blocker (`GFLOW_CLI_LLM_BASE_URL` defaults to Google's compat endpoint which returns HTTP 404 on `/audio/transcriptions`, and no live Whisper/Groq/OpenAI transcription endpoint is configured in the environment).
- [ ] Run `uv run pytest -m e2e tests/e2e/test_video_captions_e2e.py -v` and confirm the CLI and MCP e2e tests pass.

---

## Task 7 — Documentation & surface blast-radius sweep

**What:** Update all user-facing and agent-facing documentation across the six mirror axes
(`skills/check/SKILL.md` step 1b) and regenerate the website docs and plugin skill mirrors.

**Files:**
- `docs/USAGE.md` — add `gflow video transcribe` and `gflow video captions` sections and synopsis rows
- `docs/MCP.md` — document `gflow_video_transcribe` and `gflow_video_captions` under §2 Tools
- `docs/CONFIGURATION.md` and `.env.template` — document `GFLOW_CLI_LLM_*` usage and privacy rule for `video transcribe` / `video captions`
- `README.md` — update comparison table row `Captions` from `Planned ([#1358](...))` to `Yes (\`video captions\`, \`.srt\` export)` and update tool count / overview bullet
- `AGENTS.md` and `skills/gflow-cli/SKILL.md` — add `transcribe` and `captions` to the `gflow video` command surface
- `CHANGELOG.md` — add `[Unreleased]` -> `### Added` entry for `#1358`
- `website/docs/*` and `plugins/gflow/skills/gflow-cli/SKILL.md` — regenerated via CI scripts

**Steps:**
- [ ] Update `docs/USAGE.md` with synopsis entries and dedicated sections for `gflow video transcribe` and `gflow video captions`, including examples, `--json` output shape, exit codes (11, 16, 20), and the privacy statement that audio is never sent unless `GFLOW_CLI_LLM_BASE_URL` is configured to a transcription endpoint.
- [ ] Update `docs/MCP.md`, `docs/CONFIGURATION.md`, and `.env.template`.
- [ ] Update `README.md` comparison table row `| Captions | Planned ([#1358](https://github.com/ffroliva/gflow-cli/issues/1358)) |` to `| Captions | Yes (\`video captions\`, \`.srt\` export) |`.
- [ ] Update `AGENTS.md` and `skills/gflow-cli/SKILL.md` command lists.
- [ ] Verify `gflow docs` (`src/gflow_cli/docs_catalog.py`) and `gflow capabilities` (`src/gflow_cli/cli_capabilities.py`): `docs_catalog.py` discovers `docs/*.md` dynamically and `cli_capabilities.py` reports Flow host/generation capabilities only, so no capability key change is required.
- [ ] Add the `[Unreleased]` -> `### Added` entry in `CHANGELOG.md`.
- [ ] Run `uv run python scripts/ci/generate_website_docs.py` and `uv run python scripts/ci/generate_plugin_skills.py`.

---

## Task 8 — Full quality gates (`/gflow:check`)

**What:** Run the complete Impeccable Routine and verify all six mirror axes before final commit.

**Steps:**
- [ ] Run all hygiene, size, link, PII, website-mirror, council-memory, and plugin-skill gates:
  ```bash
  PYTHONUTF8=1 uv run python scripts/ci/check_repo_hygiene.py
  PYTHONUTF8=1 uv run python scripts/ci/check_size.py
  PYTHONUTF8=1 uv run python scripts/ci/check_doc_links.py
  PYTHONUTF8=1 uv run python scripts/ci/check_website_docs_pii.py
  PYTHONUTF8=1 uv run python scripts/ci/generate_website_docs.py --check
  PYTHONUTF8=1 uv run python scripts/ci/check_council_memory.py
  PYTHONUTF8=1 uv run python scripts/ci/generate_plugin_skills.py --check
  ```
- [ ] Verify Step 1b surface blast-radius sweep (`tests/mcp/test_cli_parity.py`, `tests/mcp/test_server.py`, and repo-wide grep for `transcribe` / `captions`).
- [ ] Run `uv run ruff check src tests`, `uv run ruff format --check src tests`, and `uv run pyright src`.
- [ ] Run scoped unit/BDD/CLI/MCP tests and `pytest -m e2e tests/e2e/test_video_captions_e2e.py`.

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (hygiene / size / doc links / website mirror / plugin skills / ruff / format / pyright / pytest >= 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
- [ ] Docs updated (`USAGE.md`, `MCP.md`, `CONFIGURATION.md`, `README.md`, `AGENTS.md`, `skills/gflow-cli/SKILL.md`)
- [ ] BDD feature file [`tests/features/video_captions.feature`](../../../../tests/features/video_captions.feature) covers all Critical + High offline scenarios from `SCENARIO.md`
- [ ] E2E test `tests/e2e/test_video_captions_e2e.py` passes on both the CLI subprocess and the MCP tool
- [ ] No `# TODO` in diff without a tracked issue link
- [ ] Task 1's red tests failed before Task 3
- [ ] Worktree clean: no untracked scratch files, no leftover clones or temp dirs

## Decision log

| Date | Decision | Why | Rejected |
|---|---|---|---|
| 2026-11-20 | Render captions per frame with Pillow (`PIL.ImageDraw`) and mux with PyAV (`av`) from existing `gflow-cli[chain]` extra | Prebuilt PyAV wheels ship without `libass` (`subtitles` filter) or `libfreetype` (`drawtext` filter); Pillow `ImageFont.load_default(size=...)` renders scalable TrueType captions at ~5.4x real-time speed with zero new dependencies | Shelling out to a system `ffmpeg` binary (not guaranteed installed) or PyAV filter graph (`ValueError: no filter subtitles`) |
| 2026-11-20 | Use `httpx` against OpenAI-compatible `POST {base_url}/audio/transcriptions` via existing `GFLOW_CLI_LLM_*` settings; no `faster-whisper` extra in v1 | `httpx` is already an eager dependency (0 MB overhead) and supports both hosted Whisper APIs (OpenAI, Groq) and local servers (`whisper.cpp`, `vLLM`, `LiteLLM`); `faster-whisper` adds >200 MB of native wheels plus model downloads (YAGNI) | Shipping a `gflow-cli[transcribe]` extra with `faster-whisper` in v1 |
| 2026-11-20 | Refuse transcription pre-flight (exit 11) when `GFLOW_CLI_LLM_BASE_URL` is still at `DEFAULT_LLM_BASE_URL` (`generativelanguage.googleapis.com/v1beta/openai`) | Empirical probe confirmed Google's default OpenAI-compat endpoint returns HTTP 404 on `/audio/transcriptions`, and privacy requires never sending audio to an endpoint the user did not explicitly configure | Sending audio to the default Google endpoint and letting it fail with an opaque HTTP 404 |
| 2026-11-20 | Extract 16 kHz mono PCM WAV in memory before uploading to `/audio/transcriptions` | Keeps uploads compact (~32 KB/s, ~256 KB for an 8 s clip), avoids uploading multi-megabyte video containers, and fails fast on silent/audio-less clips before any network request | Uploading the raw `.mp4` container directly |
| 2026-11-20 | Place CLI commands in `cli_video_captions.py` and MCP tools in `mcp/video_captions_tools.py`, with 4-line registration/re-export in `cli_video.py` and `mcp/tools.py` | `mcp/tools.py` is at 2,690 lines against a 2,691-line `check_size.py` baseline and `#1359` (`video assemble`) lands first in both `cli_video.py` and `mcp/tools.py`; separate modules avoid size-gate failures and rebase conflicts | Putting full command and tool bodies directly in `cli_video.py` and `mcp/tools.py` |
| 2026-11-20 | Reuse `ConfigurationError` (11), `DataStoreError` (16), `FrameExtractionError` (20), `RateLimitError` (4/40), and `NetworkError` (6/42) with `charged="no"` | Every failure mode maps cleanly onto existing RFC 9457 classes and exit codes without expanding `EXIT_CODE_MAP` | Adding bespoke `TranscriptionError` or `CaptionFormatError` classes |
| 2026-11-20 | One readable default caption style in v1 (bottom-centred, semi-transparent rounded box, white text scaled to ~4.5% of frame height); no style flags | Satisfies the short-form video use case with minimal CLI/MCP surface area; style flags are recorded as out of scope for v1 | Adding `--font-size`, `--font-color`, `--position`, or karaoke animation flags in v1 |
