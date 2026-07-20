# Live verification — production-readiness hardening (v0.40.0)

> **Status: PARTIALLY COMPLETE.** This document was scaffolded by task F1's
> **offline** half (docs correction + evidence-doc structure). Every section
> below is either filled from evidence available without touching the live
> Flow API (commit, environment, offline gate results, the CDP decision, and
> the known Flow-side limitations already recorded in `KNOWN_ISSUES.md`), or
> marked `PENDING EXECUTION` for the separate live matrix pass. **No
> "production-ready" claim is made until every `PENDING EXECUTION` section
> below is filled in with real evidence** — per the design spec's evidence
> rule (`docs/superpowers/specs/2026-07-19-production-readiness-hardening-design.md`
> § Evidence deliverable), a skipped changed-paid-path or an unclassified
> post-submit task blocks that claim.

## Commit and environment

| | |
|---|---|
| Branch | `chore/production-readiness-hardening` |
| Commit at time of writing (offline half, pre-commit) | `a29e1b54ff4599cb4cb868861699ddb1296477be` |
| Docs-only commit (this task) | see `git log -1 --format=%H -- docs/CONFIGURATION.md` after commit `docs: correct runtime documentation for production readiness` |
| Live-matrix commit | **PENDING** — the controller's live-evidence commit (`docs: record production readiness verification`) will follow this one; record its hash here when the live matrix runs |
| `gflow-cli` version | `0.40.0` (`pyproject.toml`) |
| Python | 3.13.7 (CPython, Windows build) |
| `uv` | 0.8.16 |
| Playwright (Python package) | 1.59.0 |
| `patchright` (optional engine) | not installed in this environment (opt-in extra; `GFLOW_CLI_BROWSER_ENGINE` defaults to `playwright`) |
| OS | Windows-11-10.0.26200-SP0 |
| `GFLOW_CLI_HEADLESS` (effective) | `false` (default — headed real Chrome; see `docs/CONFIGURATION.md#gflow_cli_headless`) |

## Profile strategy and effective locale

**PENDING EXECUTION.** No account secrets or profile names are recorded here in
advance. When the live matrix runs, record (without secrets):
- which profile was used (a name only — never cookies/tokens),
- its Chrome strategy (`real_chrome` vs `internal_chromium`),
- effective `GFLOW_CLI_LOCALE` / Accept-Language and whether Flow rendered in
  `/fx/en/` or a redirected locale (see `docs/CONFIGURATION.md#gflow_cli_locale`),
- and confirmation the profile was already authenticated (no fresh `auth login`
  captured as part of this evidence run).

## Exact commands and marker gates

The design spec's live matrix (§ "Live matrix") runs **serially**, stopping on
WAF 403, auth expiry, quota exhaustion, or profile contention. Planned command
sequence (results — PENDING EXECUTION — go in the sections below):

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

## Timings, terminal statuses, artifact sizes and magic-byte checks

**PENDING EXECUTION.** To be filled in by the live matrix with, per generation:
- wall-clock time from submit to terminal state,
- terminal status (`MEDIA_GENERATION_STATUS_SUCCESSFUL` or the observed failure),
- downloaded file size,
- magic bytes (PNG `89 50 4E 47`; MP4 `ftyp` box) and `ffprobe` dimensions/duration,
- correlation ID from `--json` output for cross-referencing structured logs.

## Queue/checkpoint and lease-release evidence

**PENDING EXECUTION.** To be filled in by the live matrix:
- the `generation_queue` checkpoint phase sequence observed for each submitted
  task (`claimed` → `submit_attempted` → `remote_started` → terminal), per
  `src/gflow_cli/worker/queue.py::classify_interrupted`;
- confirmation that a live daemon/MCP task path shows exactly one atomic claim
  (no double-claim across daemon and MCP);
- `ProfileLease` acquire/release evidence for the run — lock file created,
  held for the duration of Chrome ownership, released cleanly at teardown
  (`release_does_not_unlink_lock_file` — the lock file itself is expected to
  persist after release; only the kernel lock and process-local registry
  entry are cleared);
- confirmation that a same-profile contention probe run concurrently (or
  immediately after, before release) is rejected with `ProfileLockedError`
  (exit code 11) rather than a raw Chromium "profile locked" crash.

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

**PENDING EXECUTION** for the live-matrix portion (which paid paths were
skipped during the live run and why — e.g. stopped early on WAF 403, quota
exhaustion, or profile contention per the design spec's stop conditions).

Already known and out of scope for this task by design (not "skipped" so much
as never attempted here):
- No live probe of the removed CDP-attach lifecycle (see decision above —
  deliberately not exercised).
- Remote macOS/Linux legs of the two-subprocess `ProfileLease` contention test
  (`tests/test_profile_lease_subprocess.py`) — the Windows leg ran locally and
  is GREEN (see Offline gates below); the POSIX `fcntl.flock` branch has not
  been executed anywhere in this branch's history yet. Per design-spec
  guidance, remote execution requires explicit push/PR authorization and must
  not be reported as locally executed.
- D4's four cancellation-path live confirmations (mid-launch, mid-context.close
  wedged page, Ctrl-C during passive capture, daemon SIGINT in-flight) —
  flagged in `progress.md` as F1 live work, not covered by this offline task.
- C5's e2e daemon tests requiring `GFLOW_CLI_E2E_PROFILE` — deselected in the
  offline run (see Offline gates below); a real profile is required to
  confirm the `remote_started` credit-free re-entry path.

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

## No "production-ready" claim yet

Per the design spec's evidence rule, this document does **not** claim
production readiness. The offline gates above are green (modulo the confirmed
pre-existing flake), the CDP decision is final and implemented, and the known
Flow-side limitations are catalogued — but every live, credit-spending check
(image generation, video generation, queue/lease evidence under real
contention, terminal-state and artifact verification, cookie DB health, and
confirmation that no gflow-owned Chrome process is left behind) remains
`PENDING EXECUTION` until the live matrix runs and this document is updated
in a separate commit (`docs: record production readiness verification`).
