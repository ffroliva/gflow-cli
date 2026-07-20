# Live verification — production-readiness hardening (v0.40.0)

> **Status: LIVE MATRIX EXECUTED for the core paid/free paths.** Task F1's
> offline half (docs correction + evidence-doc structure) landed first; this
> commit fills in the live-matrix results: a real auth-expiry fail-fast, a
> real re-auth, one credit-free image generation, and one paid T2V
> generation, all serially on profile `ffroliva`, plus post-run artifact,
> provenance, lease, and cookie-DB verification. **This still does not add up
> to an unqualified "production-ready" claim** — the daemon/MCP live
> lifecycle, live queue-claim, live crash-recovery reconciliation (the C5
> credit-free `remote_started` re-entry concern), the POSIX `ProfileLease`
> leg, the omni-flash NULL-operation-id path, and the four D4 live
> cancellation paths were **not** exercised against real Flow this pass — see
> "Skipped or externally blocked paths" below for exactly what remains open
> and why, per the design spec's evidence rule
> (`docs/superpowers/specs/2026-07-19-production-readiness-hardening-design.md`
> § Evidence deliverable).

## Commit and environment

| | |
|---|---|
| Branch | `chore/production-readiness-hardening` |
| Commit at time of writing (offline half, pre-commit) | `a29e1b54ff4599cb4cb868861699ddb1296477be` |
| Docs-only commit (this task) | `28530782a578240c2f963c5cd4d49f4dcbe96168` (`docs: correct runtime documentation for production readiness`) |
| Live-matrix commit | this commit (`docs: record production readiness verification`), landing on top of `2853078` — see `git log -1 --format=%H -- docs/LIVE_VERIFICATION_v0.40.0-production-readiness.md` after it lands |
| `gflow-cli` version | `0.40.0` (`pyproject.toml`) |
| Python | 3.13.7 (CPython, Windows build) |
| `uv` | 0.8.16 |
| Playwright (Python package) | 1.59.0 |
| `patchright` (optional engine) | not installed in this environment (opt-in extra; `GFLOW_CLI_BROWSER_ENGINE` defaults to `playwright`) |
| OS | Windows-11-10.0.26200-SP0 |
| `GFLOW_CLI_HEADLESS` (effective) | `false` (default — headed real Chrome; see `docs/CONFIGURATION.md#gflow_cli_headless`) |
| Live-matrix date | 2026-07-20 (UTC timestamps below) |
| `google-flow-worker` state during the run | not running (port 8000 empty) — no profile contention; `ffroliva` is not the worker's shared profile (`denon82` is, per `memory/project_gflow_worker_video_refactor.md`) |

## Profile strategy and effective locale

**FILLED.**
- Profile: `ffroliva` (Google account `ffroliva@gmail.com`), profile dir
  `C:\Users\ffrol\AppData\Local\ffroliva\gflow-cli\profile_ffroliva`. No
  account secrets are recorded here.
- Chrome strategy: `chrome` (real Chrome channel). Browser engine:
  `playwright` (default; `patchright` not installed in this environment).
- Effective locale: not separately probed this pass — no `GFLOW_CLI_LOCALE`
  override was set, so the default applies (see
  `docs/CONFIGURATION.md#gflow_cli_locale`); no locale-redirect anomaly was
  observed during either the auth probe or the two generations.
- The profile was **not** already authenticated at the start of this run: the
  first live action (a zero-spend image attempt) hit a server-side-expired
  session and failed fast with `AuthExpiredError`. A fresh `gflow auth login
  --profile ffroliva --browser chrome` was then run and is captured as live
  evidence below (see "Live sequence actually executed", step 3) — this is
  itself a confirmation of the documented `cookies_present: True` ≠ live
  session trap (`docs/operations/GFLOW_TROUBLESHOOTING.md`,
  `KNOWN_ISSUES.md`).

## Exact commands and marker gates

The design spec's live matrix (§ "Live matrix") runs **serially**, stopping on
WAF 403, auth expiry, quota exhaustion, or profile contention. This was the
*planned* `pytest -m e2e_*` command sequence; the sequence actually executed
used direct `gflow` CLI invocations instead — see "Live sequence actually
executed" immediately below for what really ran and why (an auth-expiry
fail-fast forced a re-auth step not in this original plan).

```bash
# 1. Zero-credit auth/health/schema probes
uv run pytest -m "e2e_auth or (e2e and e2e_data and not e2e_image and not e2e_video)" -v

# 2. Credit-free daemon/MCP lifecycle, queue claim, lease-release checks
uv run pytest tests/e2e/test_daemon_e2e.py -v
uv run pytest tests/test_profile_lease_subprocess.py -v   # offline leg, already GREEN — see below

# 3. Real-handle / reconciliation observation (credit-free portion only)
#    — exercises the C1 handle-spike seam without spending a credit

# 4. One image generation covering the changed queue/worker boundary
uv run pytest -m "e2e_image and not e2e_batch" -v

# 5. One cheapest stable T2V generation, no explicit --duration
GFLOW_CLI_E2E_RUN_VIDEO=1 uv run pytest -m "e2e_video" -v

# 6. Post-run verification (terminal state, magic bytes, ftyp, provenance,
#    cookie DB health, lease reacquisition, no leftover Chrome process)
```

Cost sub-markers used above are documented in `docs/E2E_TESTING.md`
(`e2e_auth`=0 credits, `e2e_image`≈1 Imagen credit, `e2e_video`≈1 Veo credit).
All commands require `GFLOW_CLI_E2E_PROFILE=<name>` set to an authenticated
profile; see `docs/E2E_TESTING.md § Environment variables`.

### Live sequence actually executed

The pytest `-m e2e_*` invocations above were the plan; what actually ran was a
serial sequence of direct `gflow` CLI invocations on profile `ffroliva`
(stopping-on-failure discipline preserved — the sequence below is exactly
what executed, in order, with nothing skipped for convenience):

1. **Zero-credit auth probe**: `gflow auth status --profile ffroliva` →
   "Profile 'ffroliva' is configured", `cookies_present: True`. (Per the
   documented trap, `cookies_present` alone is not proof of a live session —
   confirmed true one step later.)
2. **First image attempt on the stale session (fail-fast, no spend)**:
   `gflow image t2i ... --profile ffroliva` → correlation `eae884d0`; browser
   launched, `ProfileLease` acquired for `ffroliva` with no contention,
   persistent context launched (60 cookies, `flow_session_cookie_present:
   True`, `SAPISID` present), then `AuthExpiredError` — HTTP 401 at
   `https://labs.google/fx/api/trpc/project.createProject` — exit code 3,
   `retryable: false`. Nothing spent. Live-confirms: `ProfileLease` acquire on
   a real profile, fail-fast typed auth error, no paid action taken on a dead
   session. (~4s.)
3. **Re-auth**: `gflow auth login --profile ffroliva --browser chrome` →
   `auth_flow_session_verified`, `[OK] Flow session verified
   (ffroliva@gmail.com)` at `2026-07-20T10:48:46Z`.
4. **Credit-free image generation**: `gflow image t2i "a single smooth grey
   river stone..." --model nano2 --count 1 --aspect 9:16 --profile ffroliva
   --out <scratch> --json` → correlation `d91f46ff`.
5. **Paid T2V generation**: `gflow video t2v "a single smooth grey river
   stone... gentle ripples..." --model veo-lite --aspect 9:16 --count 1
   --profile ffroliva --out-dir <scratch> --json` (no `--duration`) →
   correlation `a0fc0552`.
6. **Post-run verification** (artifacts, provenance, lease, cookie DB, no
   leftover Chrome) — see the dedicated section below.

## Timings, terminal statuses, artifact sizes and magic-byte checks

**FILLED.**

**Image generation (`d91f46ff`, credit-free)**
- Wire capture: `ui_automation.batch_response_captured` — HTTP 200 on
  `.../flowMedia:batchGenerateImages` at `10:50:01Z`.
- Result: `status: ok`, model `NARWHAL`, `project_id
  594b4ae2-9e65-490e-afa5-a9d0820bd86b`, `media_name
  d3fc808c-c641-491b-a503-4091cbcd34a1`, `workflow_id
  79252fa4-5419-4197-ae06-03bc2097dac0`, seed `709471`, aspect
  `IMAGE_ASPECT_RATIO_PORTRAIT`, dimensions `768x1376`.
- Artifact: JPEG, 761.5 KB, magic bytes `ff d8 ff e0` (valid JPEG).

**T2V generation (`a0fc0552`, one paid Veo credit)**
- Classic UI arm: model picker matched "Veo 3.1 - Lite" →
  `model_selected: veo_3_1_lite`; aspect PORTRAIT tab; count 1x tab. Prompt
  submitted `10:51:28Z`.
- Wire capture: `ui_automation_video.generate_captured` — HTTP 200 on
  `.../video:batchAsyncGenerateVideoText` at `10:51:32Z` (`image_inputs`
  parsed, `referenceCount: 0`).
- `poll_terminal`: `MEDIA_GENERATION_STATUS_SUCCESSFUL`, `media_name
  40b25755-5f3f-4701-a61b-a3cff0d637d1` at `10:52:14Z`.
- `video_saved`: 3,252,666 bytes at `10:52:21Z`. Submit → save: ~53s.
- Result: `status: ok`, `succeeded: true`, `generation_status:
  MEDIA_GENERATION_STATUS_SUCCESSFUL`, `request: {model: veo_3_1_lite, mode:
  t2v, aspect: portrait, duration: null, count: 1}`. `project_id
  ad6f6cde-71a4-4326-92c9-40112225d793`.
- Artifact: 3.1 MB (3,252,666 bytes) MP4, bytes 4-8 = `ftyp`, brand `isom`
  (valid MP4 container).

One paid Veo generation was spent in this evidence run; the image generation
was credit-free (Imagen/nano2 free tier, per
`memory/reference_gflow_image_gen_free.md`).

## Queue/checkpoint and lease-release evidence

**PARTIALLY FILLED — direct-CLI client path only, no daemon/MCP queue in this
run.**

- **`generation_queue` checkpoint phase sequence**: **not observed live this
  pass.** All four commands in the live sequence went through the direct
  `gflow image` / `gflow video` client path, not the `gflow serve` /
  `gflow daemon` / MCP queue path — there was no `generation_queue` row to
  checkpoint. The client-path submit→remote_started boundary itself (the C1
  seam) *was* live-exercised: the T2V run's wire captures show
  `generate_captured` (submit) at `10:51:32Z` followed by `poll_terminal`
  reaching `MEDIA_GENERATION_STATUS_SUCCESSFUL` at `10:52:14Z` — i.e. a real
  submit-to-terminal transition was observed, just not through the queue
  codec. See "Skipped or externally blocked paths" for the daemon/MCP queue
  gap.
- **Live daemon/MCP atomic-claim confirmation**: **not run** — no daemon or
  MCP task was submitted this pass (offline coverage only; see below).
- **`ProfileLease` acquire/release evidence**: **confirmed live.** Three
  sequential clean acquisitions of the `ffroliva` lease occurred across the
  live sequence (auth probe step 2, image generation step 4, video generation
  step 5), each with zero contention reported. Each run released the lease
  and closed its Chrome process before the next one started — if release had
  not happened cleanly, the next acquisition would have failed fast with
  `ProfileLockedError` instead of succeeding, so the three clean back-to-back
  acquisitions are themselves the live proof of acquire → hold → release
  working correctly for a real profile.
- **Same-profile contention probe (`ProfileLockedError`, exit code 11)**:
  **not run.** No concurrent contention probe was executed against `ffroliva`
  during this sequence — `google-flow-worker` was stopped for the whole run
  specifically to avoid contention, so there was no second process available
  to trigger the reject-path live. The reject path remains covered offline
  only (`tests/test_profile_lease_subprocess.py`, Windows leg green — see
  Offline gates below).

## Post-run verification

**FILLED.**

- Image artifact: 761.5 KB, magic bytes `ff d8 ff e0` (JPEG), 768x1376.
- Video artifact: 3.1 MB (3,252,666 bytes), bytes 4-8 = `ftyp`, brand `isom`
  (valid MP4).
- Provenance / local history: `gflow data media 40b25755-... --profile
  ffroliva` returned the video record with profile `ffroliva`, media ID,
  `project_id ad6f6cde-...`, `kind: video`, and `local_path` — confirming the
  data-layer `OperationRecorder` wrote provenance for the live paid
  generation.
- No leftover gflow-owned Chrome process: `Get-CimInstance Win32_Process`
  filtered on `chrome.exe` with `profile_ffroliva` in the command line →
  count 0 after the run.
- Cookie DB health: `profile_ffroliva/Default/Network/Cookies` exists, 49152
  bytes, valid `SQLite format 3\0` header.

## CDP keep/remove decision

**FILLED — offline-derivable, decision already made and implemented.**

**Verdict: packaged CDP lifecycle REMOVED. Discovery/channel helpers KEPT.**
Decided and implemented in commit `ca450b4` ("chore: resolve research CDP
lifecycle"), evidence recorded in `.superpowers/sdd/cdp-decision.md`:

- All four decision-gate criteria failed: no production consumer in the
  shipped package (`src/gflow_cli`), no safe ownership model for an
  unauthenticated debug port, a no-lock branch that would attach to *any*
  Chrome answering on the port (a hijack vector), and no positive prior
  evidence — the 2026-05-12 record shows Flow's WAF (`aisandbox-pa.googleapis.com`)
  itself rejects an externally-discoverable CDP debug port.
- Removed: `_pid_alive`, `is_browser_running`, `_connect_cdp`,
  `_check_chrome_singleton_lock`, `_spawn_chrome`, `_write_lock`,
  `_read_lock`, `_remove_lock`, `_find_available_cdp_port`,
  `_is_logged_in_to_flow`, `_attach_and_verify_login`, `_wait_chrome_ready`,
  `_resolve_race_loss`, `get_or_launch_browser`, `close_browser`, and the CDP
  port-range/lock-filename constants from `src/gflow_cli/browser_manager.py`;
  `scripts/smoke_real_chrome_image.py` deleted outright (existed only to
  exercise the removed code); 14 CDP-lifecycle test classes removed from
  `tests/test_browser_manager.py`.
- Kept (production dependencies): Chrome binary discovery and channel
  selection — `_find_chrome_binary`, `_is_playwright_chrome_channel_available`,
  `is_chrome_available`, `resolved_chrome_binary`, `channel_for_profile`,
  `chrome_strategy_requested` — none of which involve a debug port or an
  attach lifecycle.
- No live CDP-attach probe was run to reach this verdict: the design spec
  forbids submitting a generation merely to justify unused code, and the
  production-consumer gate alone already fails. CDP-attach itself remains a
  **parked backlog idea** (ADR #13 in `PLAN.md`), not resurrected by this
  decision.

## Skipped or externally blocked paths

**FILLED.** The live matrix did not hit any of the design spec's stop
conditions (no WAF 403, no quota exhaustion, no profile contention) — every
step in the live sequence above ran to completion. What was deliberately
**not** run live this pass, and why:

- **Daemon/MCP live lifecycle, live queue-claim, and live crash-recovery
  handle-reconciliation** were not exercised against real Flow. These
  (queue codec, atomic-claim, indeterminate-recovery — tasks C2–C5) are
  covered offline by the 2513 passing tests, including the real
  two-subprocess `ProfileLease` contention test and the multiprocess
  queue-claim race test. The C1 client-path checkpoint seam **was** exercised
  live, via the real `batchAsyncGenerateVideoText` capture (the
  submit → `remote_started` boundary — see "Timings" and "Queue/checkpoint"
  above). The C5 concern specifically — credit-free `remote_started`
  project-page re-entry reconciliation — remains **LIVE-UNCONFIRMED**: this
  run observed the handle capture but did not drive a crash-and-reconcile
  scenario live. Keep the underlying issue open; this is remaining work, not
  a regression.
- **Remote macOS/Linux legs of the D2 `profile-lease-matrix` CI job** are
  pending an authorized push — the POSIX `fcntl.flock` lease branch has not
  been executed anywhere yet (Windows leg ran locally and is GREEN, see
  Offline gates below). Per design-spec guidance, remote execution requires
  explicit push/PR authorization and must not be reported as locally
  executed.
- **omni-flash NULL-operation-id path** was not exercised — `veo-lite` was
  used deliberately for the T2V generation instead.
- **D4's four live cancellation paths** (mid-launch cancel, mid-context.close
  wedged page, Ctrl-C during passive capture, daemon SIGINT in-flight) were
  **not** driven live this pass — unit-proven only.
- No live probe of the removed CDP-attach lifecycle (see decision above —
  deliberately not exercised; this was already decided offline and stands).

## Remaining known Flow-side limitations

Carried forward from `KNOWN_ISSUES.md` (`## Open` / `## Mitigated`) — none of
these are resolved by this branch, and the live matrix should not attempt to
"fix" them, only avoid tripping over them:

- **Duration control absent on some cohorts** — the Frames-submode settings
  popover does not render a duration tab on every profile/cohort; gflow fails
  fast (`UiSelectorDriftError`, exit 23) rather than silently defaulting
  (`KNOWN_ISSUES.md § Video duration tab probe misses`). The live matrix's T2V
  generation intentionally omits `--duration` to sidestep this.
- **Full-page media-library A/B rollout** — some Flow projects render a
  full-page media-library UI where entity/frame attach selectors miss
  (`KNOWN_ISSUES.md § Flow's new full-page media-library UI breaks entity attach`).
- **WAF heat / `PUBLIC_ERROR_UNUSUAL_ACTIVITY`** — Flow's WAF can reject
  `batchGenerateImages` under cumulative submission cadence; `GFLOW_CLI_JITTER_RANGE`
  is the mitigation, not a guarantee (`KNOWN_ISSUES.md § batchGenerateImages HTTP 403`).
- **No in-CLI quota visibility** — remaining Veo/Imagen credits aren't shown
  locally; check <https://gemini.google/subscriptions/> (`KNOWN_ISSUES.md § No in-CLI quota visibility`).
- **Metadata-sensitive JPEG rejection** — Flow's `uploadImage` endpoint
  sometimes 400s on a JPEG with a specific metadata segment, root cause
  unidentified; re-encode with `ffmpeg -q:v 2 -map_metadata -1` if it happens
  during the live matrix (`KNOWN_ISSUES.md § Flow's uploadImage endpoint rejects some JPEGs`).
- **Agentic/classic UI cohort flapping** — the composer arm is server-assigned
  and can flap per page load (issue #299); `GFLOW_CLI_UI_MODE` fails fast
  (exit 28) rather than silently degrading.
- **No automated post-interruption reconciliation** — an `indeterminate`
  queue task (post-submit interruption) requires a manual Flow-project check;
  see `KNOWN_ISSUES.md § gflow serve / MCP worker queue: interrupted post-submit tasks`.
  If the live matrix's daemon/MCP leg is interrupted after a submit, follow
  that entry's manual reconciliation steps rather than assuming failure.

## Offline gates (this task — F1 offline half)

All commands run from `C:\development\github\gflow-cli\.worktrees\production-readiness-hardening`
at commit `a29e1b5` plus the docs-only changes in this task:

| Gate | Command | Result |
|---|---|---|
| Documentation gate | `uv run pytest tests/test_documentation_gate.py tests/test_marker_registry.py -q` | 33 passed |
| Doc link check | `uv run python scripts/ci/check_doc_links.py` | All links resolved across 27 files |
| Repo hygiene | `PYTHONUTF8=1 uv run python scripts/ci/check_repo_hygiene.py` | 630 tracked files checked — no violations |
| Lint | `uv run ruff check src tests` | All checks passed |
| Format | `uv run ruff format --check src tests` | 309 files already formatted |
| Types | `uv run pyright src` | 0 errors, 0 warnings, 0 informations |
| Full offline suite + coverage | `PYTHONUTF8=1 uv run python -m pytest -m "not live and not e2e and not smoke" -q --cov=gflow_cli` | 2513 passed, 13 skipped, 62 deselected, **1 failed** (see below), 91% coverage (floor: 80%) |
| Focused subprocess lease tests (Windows leg) | `uv run python -m pytest tests/test_profile_lease_subprocess.py -v` | 2 passed (`test_holder_wins_and_second_process_fails_fast`, `test_process_exit_releases_kernel_lock`) |
| `uv build` | `uv build --wheel --sdist --out-dir <scratch dir>` | sdist + wheel built successfully |
| Wheel content check | zip-inspect `gflow_cli-0.40.0-py3-none-any.whl` | `gflow_cli/data/migrations/0001_initial.sql` present (115 files total) |
| Sdist content check | tar-inspect `gflow_cli-0.40.0.tar.gz` | `src/gflow_cli/data/migrations/0001_initial.sql` present (707 files total) |
| Clean worktree | `git status --short` | Only the intended doc/test files modified; no stray build artifacts |

**One pre-existing flake, confirmed unrelated to this task's changes:**
`tests/data/test_packaging.py::test_built_distributions_contain_sql_migrations`
failed twice when run as part of the full suite, but **passed in isolation**
(`uv run python -m pytest tests/data/test_packaging.py::test_built_distributions_contain_sql_migrations -q`
→ 1 passed). This task touched no `src/gflow_cli` code — `git status --short`
before these edits was clean except for docs/tests. `progress.md`'s Task D3
entry already recorded this exact flake ("Packaging flake (uv build subprocess
vs parent uv lock) confirmed unrelated") from an earlier task on this branch,
consistent with what's observed here: the test's own `uv build` subprocess
call contends with the parent test runner's `uv`-managed environment when many
other tests are running concurrently in the same suite invocation.

## Production-readiness claim status

The offline gates are green (modulo the confirmed pre-existing packaging
flake), the CDP decision is final and implemented, the known Flow-side
limitations are catalogued, and — as of this commit — the core direct-CLI
paid/free live paths are now confirmed against real Flow: auth-expiry
fail-fast with zero spend, live re-auth, a credit-free image generation, a
paid T2V generation, terminal-state and artifact verification (magic bytes,
container structure), provenance recording, `ProfileLease` acquire/release
across three sequential real acquisitions, cookie-DB health, and no leftover
gflow-owned Chrome process.

This is **not** a blanket "production-ready" claim, per the design spec's
evidence rule. What remains explicitly open (see "Skipped or externally
blocked paths" above for detail, not just offline-covered):

- **C5 — credit-free `remote_started` re-entry reconciliation**:
  LIVE-UNCONFIRMED. Offline-covered via unit/multiprocess tests only.
- **Daemon/MCP live lifecycle and live queue-claim**: not exercised this
  pass — the live sequence used the direct `gflow image`/`gflow video`
  client path, not `gflow serve`/MCP.
- **D2 remote CI legs (POSIX `fcntl` lease branch)**: pending an authorized
  push; unexecuted anywhere in this branch's history.
- **D4's four live cancellation paths**: unit-proven only, not driven live.
- **omni-flash NULL-operation-id path**: not exercised (veo-lite used
  deliberately instead).

These are tracked, pre-existing gaps rather than new regressions introduced
by this branch, and none of them blocked or degraded the live paths that
*were* exercised. A future task should close the C5, daemon/MCP, D2, and D4
gaps specifically before an unqualified production-ready claim is made.
