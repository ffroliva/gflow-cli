# Scenario: Video Transcription and Burned-in Captions (#1358)

Scope: `gflow video transcribe FILE [--out-file X.srt] [--language L] [--profile NAME] [--json]`
and `gflow video captions FILE [--srt X.srt] [--language L] [--out-file Y.mp4] [--profile NAME] [--json]`,
together with their MCP twins `gflow_video_transcribe` and `gflow_video_captions`.

Both commands accept a local video path or a catalog media UUID, spend zero Flow credits,
and launch no browser. Frame rendering uses PyAV and Pillow from the existing optional
`gflow-cli[chain]` extra (`av>=12`, `pillow>=13.0.0`) because prebuilt PyAV wheels ship
without `libass` (`subtitles`) or `libfreetype` (`drawtext`). Transcription uses `httpx`
against an explicitly configured OpenAI-compatible `POST {base_url}/audio/transcriptions`
endpoint via the existing `GFLOW_CLI_LLM_*` settings.

## Coverage map

| Dimension | Active | Reason |
|---|---|---|
| D1 Auth & session lifecycle | No | No Flow API calls or browser sessions; catalog UUID lookups read local SQLite only. |
| D2 WAF / reCAPTCHA scoring | No | Zero traffic to `aisandbox-pa.googleapis.com` or `flow.google.com`. |
| D3 Selector drift & locale invariance | No | No DOM selectors or Playwright pages. |
| D4 Batch & resume | No | Single-clip local post-processing commands; idempotent overwrite on explicit or default output path. |
| D5 Concurrency & Page pool | Partial | No Page pool, but CPU-bound PyAV/Pillow work on MCP must run via `asyncio.to_thread` to avoid stalling the server event loop. |
| D6 Data layer (SQLite / DataStore) | Yes | Catalog media UUID resolution via `services.media_recovery.resolve_asset` and `verified_local_path`. |
| D7 Error propagation & exit codes | Yes | Reuse `ConfigurationError` (11), `DataStoreError` (16), `FrameExtractionError` (20), `RateLimitError` (4/40), `NetworkError` (6/42), all with `charged: "no"`. |
| D8 Cross-platform paths | Yes | Windows/macOS/Linux paths with spaces and Unicode, UTF-8 BOM in `.srt` files, and OS-independent font loading via `ImageFont.load_default(size=...)`. |
| D9 Transport edge cases | Yes | OpenAI-compatible `/audio/transcriptions` wire handling: Google default 404 pre-flight, raw SRT vs JSON responses, redirect refusal, and HTTP error mapping. |
| D10 Headless vs headed environment | No | Runs identically in headless CI and desktop terminals; no display server needed. |
| D11 Input validation & boundary values | Yes | Malformed/empty `.srt`, inverted timestamps, silent/audio-less clips, video-less containers, cloud storage URI conflicts. |
| D12 Observability & structured log contract | Yes | `structlog` events on stderr (`video.transcribe.*`, `video.captions.*`), clean stdout under `--json` / `-o id`, redacted error details. |
| D13 MCP surface parity | Yes | `gflow_video_transcribe` and `gflow_video_captions`, output-directory confinement (`GHSA-hqfj-4jww-76f2`), `--no-spend` availability, and `test_cli_parity.py`. |

## Scenario table

Severity: **Critical** (privacy leak / arbitrary file write / unredacted credential leak) · **High** (broken core path, wrong exit code, or MCP parity drift) · **Medium** (edge-case media/SRT input or degraded UX) · **Low** (cosmetic or minor formatting detail).

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D9 / D7 | User runs `video transcribe` (or `video captions` without `--srt`) with `GFLOW_CLI_LLM_BASE_URL` left at the built-in Google default (`generativelanguage.googleapis.com/v1beta/openai`) | **Critical** | Fails fast before extracting or sending audio: raises `ConfigurationError` (exit 11, `charged: "no"`, `next_action: "fix_input"`) explaining that Google's default endpoint does not provide `/audio/transcriptions` (HTTP 404) and gflow never sends audio to an unconfigured endpoint; remediation names `GFLOW_CLI_LLM_BASE_URL`, `GFLOW_CLI_LLM_API_KEY`, `GFLOW_CLI_TRANSCRIBE_MODEL`, and `--srt` | Unit + BDD + E2E |
| 2 | D9 / D7 | Configured transcription endpoint returns a 3xx redirect | **Critical** | `httpx.Client(follow_redirects=False)` refuses the redirect and raises `ConfigurationError` (exit 11, `charged: "no"`), never forwarding `Authorization: Bearer` or audio bytes to a redirect target | Unit |
| 3 | D13 / D8 | MCP caller invokes `gflow_video_transcribe` or `gflow_video_captions` with an `out_file` escaping `GFLOW_CLI_OUTPUT_DIR` (e.g. `../escape.mp4` or `/tmp/outside.srt`), or omits `out_file` when the source `file` lives outside `GFLOW_CLI_OUTPUT_DIR` | **Critical** | Explicit escaping `out_file` returns 400 `Invalid Output` (`https://gflow-cli.dev/errors/bad-parameter`, `charged: "no"`); omitted `out_file` on MCP defaults inside `settings.output_dir.resolve()` (`<output_dir>/<stem>.srt` and `<output_dir>/<stem>-captions.mp4`), never writing outside `GFLOW_CLI_OUTPUT_DIR` | Unit (MCP) + BDD |
| 4 | D11 / D7 | Malformed `.srt` file (or a non-SRT sensitive file passed via `--srt` / MCP `srt`) fails parsing | **Critical** | Raises `ConfigurationError` (exit 11, `charged: "no"`, `next_action: "fix_input"`) naming the file and line/block index only — never echoing raw unparsed line text into the error detail or logs | Unit + BDD |
| 5 | D7 | Optional `gflow-cli[chain]` extra (`av` or `pillow`) is not installed | **High** | Fails at the entry guard before any transcription HTTP call with `FrameExtractionError` (exit 20, `charged: "no"`, `next_action: "fix_input"`) and remediation hint `pip install 'gflow-cli[chain]'` | Unit |
| 6 | D11 | Golden-path offline burn-in: `gflow video captions clip.mp4 --srt sample.srt` | **High** | Decodes video frames with PyAV, renders bottom-centred rounded-pill captions with Pillow on frames where `start_s <= pts_s <= end_s`, preserves audio stream without drift, writes `clip-captions.mp4`, exits 0 | Unit + BDD + E2E |
| 7 | D9 | Golden-path transcription + burn-in: `gflow video captions clip.mp4` (no `--srt`) against a configured endpoint | **High** | Extracts 16 kHz mono WAV buffer, POSTs multipart form to `{base_url}/audio/transcriptions`, writes `clip.srt`, burns subtitles into `clip-captions.mp4`, exits 0 | Unit + BDD |
| 8 | D6 / D7 | `FILE` is a catalog media UUID (`8-4-4-4-12` hex) | **High** | Resolves via `resolve_asset` + `verified_local_path`; if the UUID is absent, ambiguous across profiles, not an `AssetKind.VIDEO`, or has no verified local file on disk, raises `DataStoreError` / `ConfigurationError` (exit 16 / 11, `charged: "no"`) with remediation pointing to `gflow data download <media_id>` | Unit + BDD |
| 9 | D11 / D7 | Input clip has no audio stream (`container.streams.audio` is empty) | **High** | `video transcribe` (and `video captions` without `--srt`) fails fast before any network call with `ConfigurationError` (exit 11, `charged: "no"`, detail naming the missing audio track and suggesting `--srt`); `video captions --srt` succeeds on silent video clips and writes a valid video-only `.mp4` | Unit |
| 10 | D9 / D7 | Configured transcription endpoint returns HTTP 401/403 (bad key), 404/405 (unsupported route), 400/422 (bad model/payload), 429 (rate limit), or 5xx / timeout | **High** | 400/401/403/404/405/422 -> `ConfigurationError` (exit 11, `charged: "no"`); 429 -> `RateLimitError` (exit 4 / 40 under `GFLOW_CLI_EXIT_CODES=2`, `charged: "no"`); 5xx / timeout / connection error -> `NetworkError` (exit 6 / 42, `charged: "no"`). Response bodies are redacted via `redact_error_detail` | Unit |
| 11 | D13 | MCP parity and `--no-spend` mode (`GFLOW_MCP_NO_SPEND=1`) | **High** | `gflow_video_transcribe` and `gflow_video_captions` match CLI parameters (`file`, `srt`, `language`, `out_file`, `profile`), pass `tests/mcp/test_cli_parity.py`, and stay registered under `--no-spend` because both are credit-free (`@spends_credits(False)`) | Unit (MCP parity) + E2E |
| 12 | D9 | Transcription endpoint returns JSON (`{"segments": [{"start": 0.5, "end": 1.2, "text": "..."}]}` or `{"text": "..."}`) instead of raw SRT text | **Medium** | Client normalizes both raw SRT and JSON segment/text payloads into `list[CaptionEntry]` and writes canonical `.srt` via `format_srt` | Unit |
| 13 | D8 / D11 | `.srt` file uses UTF-8 BOM (`\ufeff`), Windows CRLF (`\r\n`), dot millisecond separators (`00:00:01.200`), or multi-line subtitle text | **Medium** | `parse_srt` strips BOM, normalizes line endings, accepts `,` or `.` before milliseconds, preserves multi-line text, and rejects blocks where `end_s <= start_s` | Unit |
| 14 | D8 | Windows / macOS / Linux font resolution and Unicode / space paths | **Medium** | `ImageFont.load_default(size=max(14, int(round(height * 0.045))))` renders scalable TrueType text without OS-specific font paths; file paths with spaces and non-ASCII characters succeed | Unit |
| 15 | D11 / D7 | `GFLOW_CLI_STORAGE_URI` is set to `s3://...` or `gs://...` when `--out-file` is passed | **Medium** | `refuse_cloud_storage_for_local_file` raises `ConfigurationError` (exit 11, `charged: "no"`) before processing | Unit |
| 16 | D12 | `--json` and `-o id` stdout purity | **Medium** | `--json` / `-o json` emits a single parseable JSON document on stdout; `-o id` emits only the output file path on stdout; all `structlog` events go to stderr | Unit + E2E |
| 17 | D5 | Blocking PyAV/Pillow/HTTP execution inside async MCP server | **Low** | MCP tool handlers offload synchronous service calls with `await asyncio.to_thread(...)` so the event loop remains responsive | Unit (MCP) |

**Count:** 17 scenarios. Critical 4 · High 7 · Medium 5 · Low 1.

## Must-cover before merge (Critical + High)

Scenarios 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 (plus Medium scenarios 12–16 in unit tests).

## Deferred (Medium + Low — out of scope for v1)

- Custom caption styling flags (`--font-size`, `--font-color`, `--position`, word-by-word karaoke animations): v1 ships one readable default (bottom-centred, semi-transparent rounded box, white text sized at ~4.5% of frame height).
- Local `faster-whisper` optional dependency extra (`gflow-cli[transcribe]`): omitted in v1 under YAGNI (>200 MB of native wheels + model downloads); local offline transcription is already supported by pointing `GFLOW_CLI_LLM_BASE_URL` at a local `whisper.cpp` / `faster-whisper-server` / `vLLM` / `LiteLLM` endpoint or passing `--srt`.

## Suggested BDD scenarios

The feature has no browser or live Google Flow surface, so the Gherkin file [`tests/features/video_captions.feature`](../../../../tests/features/video_captions.feature) carries no `@e2e` tag and is bound offline from `tests/features/test_video_captions_steps.py`. The installed-CLI and MCP end-to-end verification runs in `tests/e2e/test_video_captions_e2e.py`.

## Known-issues cross-reference

- No existing `KNOWN_ISSUES.md` entry blocks local video captioning or transcription.
- `KNOWN_ISSUES.md` § "A Veo extend segment is 7 seconds, not the 8 Flow advertises": `burn_in_captions` decodes actual frame presentation timestamps (`frame.pts * time_base`) rather than nominal metadata duration, so extended clips are captioned accurately across seams.
