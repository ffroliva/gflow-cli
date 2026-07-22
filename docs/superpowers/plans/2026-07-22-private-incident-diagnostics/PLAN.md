# Private Incident Diagnostics Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature private-incident-diagnostics` to
> find the next unchecked task. Implement one task at a time. Run `/gflow:check` before
> every commit. Every task is an **atomic green TDD task**: write the task's tests, observe
> them red, implement the approved behavior, get the scoped tests and gates green, then
> commit. Never commit a permanently red scaffold.

**Goal:** On a relevant operational failure, gflow automatically writes a bounded,
private incident bundle (structural DOM, browser/network journals, honest HAR state,
sensitive screenshot, lease-owner evidence) under `<GFLOW_CLI_HOME>/incidents/` without
masking the original error, leaking secrets, or exposing local paths to remote surfaces.

**Architecture:** One new session-scoped module `src/gflow_cli/diagnostics.py`
(`IncidentRecorder`) owned by `FlowApiClient`; `ProfileLease` moves owner metadata to
offset 1 (byte 0 reserved for the kernel lock); the retryable classification moves to a
shared helper in `errors.py` consumed by CLI JSON, MCP, and worker envelopes;
`capture_ui_diagnostics` consolidates into the recorder. No new CLI flag, command, exit
code, DB schema, or MCP tool field.

**Design:** `docs/superpowers/specs/2026-07-22-private-incident-diagnostics-design.md`
**Scenarios:** `SCENARIO.md` (same directory) — 9 Critical, 30 High, 4 Medium
**Predict verdict:** GO — confidence 8.3/10

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Critical | Secrets (tokens, prompts, cookies, signed URLs) leak into automatic artifacts/logs | Allowlist-only serialization (Tasks 2, 5, 6); canary negative tests on every surface (S01, S12, S25, S41) |
| Critical | Retention deletes user content or escapes the incidents root | Bounded manifest validation, reparse-point refusal, lock-gated pending pruning (Tasks 4, 7; S27, S37–S39) |
| Critical | Capture masks/reclassifies the original failure or corrupts the incident | Observation-only contract, recorder-local lock, original-exception-wins tests (Tasks 6, 10; S15, S20, S23) |
| High | Windows byte-0 lock makes owner metadata unreadable | Offset-1 versioned metadata, legacy → `unavailable` (Task 8; S08) |
| High | Listener/journal leaks during long video runs | Primitive-only bounded rings/maps, detach/freeze ordering (Tasks 3, 10; S16–S18) |

---

## Global constraints (apply to every task)

- `GFLOW_CLI_INCIDENT_CAPTURE` defaults to **true**; `false` performs no listener or
  bundle work.
- Automatic JSON never contains raw HTML/DOM text, prompts, tokens, headers, cookies,
  bodies, signed URLs, arbitrary upstream key names, unknown hosts/routes, raw titles,
  raw error/console text, or unsalted digests of low-entropy strings.
- Raw HAR is never enabled or copied by the recorder; screenshots live only under
  `sensitive/` and are labelled review-before-sharing.
- Capture is observation-only: never navigates, clicks, types, submits, retries, mints
  tokens, downloads, or changes queue/reconciliation state.
- The original exception, traceback, exit code, cancellation, teardown order, and lease
  release are always authoritative; capture failure is best-effort and sanitized.
- Remote surfaces (MCP/HTTP/worker) receive opaque `{id, capture_status}` only; absolute
  paths and artifact names are local-CLI-only.
- Bounds: 100 network records, 100 console records, 50 page errors, 256 in-flight
  timings (10-min expiry), 3 bundles/command, 8 s capture budget, 50 dirs / 250 MiB
  complete retention, 20 dirs / 100 MiB pending retention, 4 KiB markers, 64 KiB
  manifest parse cap.
- Structured logging only (`structlog`); pyright strict; ruff; 100-char lines; ≥ 80%
  coverage; run `/gflow:check` before every commit.
- Scope tests locally with `-m "not live and not e2e and not smoke"` (full suite OOMs);
  trust CI for the sweep.

Base test command used throughout (from the worktree root, PowerShell):

```powershell
$env:PYTHONUTF8=1
.venv/Scripts/python.exe -m pytest <paths> -q
```

---

## File structure

### New files

```
src/gflow_cli/diagnostics.py
  IncidentRecorder + sanitization primitives, journals, bundle FS, retention, events
tests/test_diagnostics_sanitize.py     — Task 2 (URL/host/route/HMAC/title/body reduction)
tests/test_diagnostics_journal.py      — Task 3 (rings, timing map, listener bookkeeping)
tests/test_diagnostics_bundle.py       — Task 4 (root containment, exclusive create, marker, atomic manifest)
tests/test_diagnostics_events.py       — Task 5 (fixed-field events, manifest allowlist)
tests/test_diagnostics_recorder.py     — Task 6 (triggers, fingerprints, DOM validation, screenshot, budget)
tests/test_diagnostics_retention.py    — Task 7 (prune rules, bounded parse, multiprocess race)
tests/api/test_client_incidents.py     — Task 10 (client wiring, listener ordering, HAR state, teardown)
tests/features/incident_diagnostics.feature + step module — Task 12
```

### Modified files

```
src/gflow_cli/errors.py            — RETRYABLE_ERRORS/is_retryable; IncidentRef on GFlowError;
                                     problem-details `incident` extension; ProfileLockedError owner evidence attr
src/gflow_cli/json_output.py       — _RETRYABLE → errors.is_retryable; local incident enrichment
src/gflow_cli/config.py            — incident_capture bool setting
src/gflow_cli/profile_lease.py     — offset-1 versioned metadata; contender evidence read
src/gflow_cli/api/client.py        — recorder ownership, lifecycle hooks, HAR snapshot, boundaries
src/gflow_cli/api/transports/base.py — TransportSetup.recorder field
src/gflow_cli/api/transports/ui_automation_video.py — mode-switch path uses recorder; bodyTextPreview removed
src/gflow_cli/mcp/tools.py         — retryable in _error_payload; remote-safe envelope only
src/gflow_cli/worker/queue.py, worker/daemon.py — retryable in persisted error payload
src/gflow_cli/_cli_helpers.py      — one-sentence local incident path + review warning
src/gflow_cli/paths.py             — incidents_dir(home) helper
.env.template, docs/CONFIGURATION.md, docs/DEBUGGING.md, docs/SECURITY.md,
docs/ARCHITECTURE.md, KNOWN_ISSUES.md, CHANGELOG.md, docs/INDEX.md, PLAN.md (root)
```

---

## Scenario traceability matrix

Every Critical/High scenario maps to a task and named test(s). Medium scenarios are
covered where they fall out naturally (design §13 gates only Critical + High).

| Scenario | Sev | Task | Test (file :: name) |
|---|---|---|---|
| S01 canary never in artifacts | C | 2, 5, 6, 10 | `test_diagnostics_sanitize.py::test_canary_secrets_never_survive_reduction`; `test_diagnostics_events.py::test_manifest_allowlist_excludes_settings_secrets`; `test_client_incidents.py::test_bundle_json_contains_no_canary` |
| S02 unknown upstream keys | C | 2 | `test_diagnostics_sanitize.py::test_unknown_top_level_keys_reduce_to_count` |
| S03 low-entropy console/title text | H | 2 | `test_diagnostics_sanitize.py::test_text_stored_as_category_and_length_only`, `::test_hmac_identity_uses_random_unpersisted_key` |
| S04 screenshot sensitivity | H | 6, 9 | `test_diagnostics_recorder.py::test_screenshot_lives_under_sensitive_and_manifest_marks_it`; `test_json_output.py::test_local_incident_output_warns_review_before_sharing` |
| S05 HAR unset stays disabled | H | 10 | `test_client_incidents.py::test_har_state_disabled_when_unset_and_never_enabled` |
| S06 auth expiry no-capture | M | 10 | `test_client_incidents.py::test_auth_expired_creates_no_bundle` |
| S07 same-process owner evidence | H | 8 | `test_profile_lease.py::test_same_process_contention_uses_registry_metadata` |
| S08 Windows offset-1 read | H | 8 | `test_profile_lease_subprocess.py::test_cross_process_offset1_metadata_read` (Windows-marked) |
| S09 kernel lock authoritative | C | 8 | `test_profile_lease.py::test_stale_metadata_never_triggers_reclaim_or_unlink` |
| S10 FlowAppError staged pre-close | H | 6, 11 | `test_diagnostics_recorder.py::test_ui_failure_stages_dom_and_screenshot`; `test_client_incidents.py::test_capture_runs_before_context_close_and_exit_code_31_kept` |
| S11 overlay geometry no text | H | 6 | `test_diagnostics_recorder.py::test_overlay_records_bounded_geometry_without_text` |
| S12 hostile DOM rejected | C | 6 | `test_diagnostics_recorder.py::test_structural_result_rejects_non_allowlisted_fields` |
| S13 screenshot timeout fallback | H | 6 | `test_diagnostics_recorder.py::test_fullpage_screenshot_failure_falls_back_to_viewport` |
| S14 batch fingerprint suppression | H | 12 | `incident_diagnostics.feature::A systemic batch failure is captured once` |
| S15 side-effect-free capture | C | 6, 12 | `test_diagnostics_recorder.py::test_capture_is_observation_only`; `incident_diagnostics.feature::Capture failure preserves the operational error` |
| S16 attach-once/detach-once | H | 3, 10 | `test_diagnostics_journal.py::test_listener_bookkeeping_attach_idempotent`; `test_client_incidents.py::test_pooled_pages_each_attached_exactly_once` |
| S17 late callbacks no-op | H | 3, 10 | `test_diagnostics_journal.py::test_events_after_freeze_are_ignored` |
| S18 bounded timing map | H | 3 | `test_diagnostics_journal.py::test_timing_map_bounded_under_10k_synthetic_events` |
| S19 concurrent same-fingerprint | H | 6 | `test_diagnostics_recorder.py::test_concurrent_capture_same_fingerprint_yields_one_bundle` |
| S20 cancellation-safe teardown | H | 10 | `test_client_incidents.py::test_cancellation_during_capture_still_releases_lease` |
| S21 remote path privacy | H | 9 | `test_errors.py::test_problem_details_incident_is_opaque`; `tests/mcp/test_server.py::test_mcp_error_envelope_omits_local_path` |
| S22 queue schema unchanged | M | 9 | `tests/worker/test_queue.py::test_error_payload_gains_retryable_without_schema_change` |
| S23 capture failure → original wins | H | 6 | `test_diagnostics_recorder.py::test_capture_io_failure_preserves_original_exception` |
| S24 retryable parity | H | 1 | `test_json_output.py::test_flow_app_and_agent_ui_errors_retryable`; `tests/mcp/test_server.py::test_mcp_retryable_matches_cli`; `tests/worker/test_queue.py::test_queue_retryable_matches_cli` |
| S25 no raw exception text | C | 5, 6 | `test_diagnostics_events.py::test_capture_failed_event_carries_class_only` |
| S26 unicode/space paths | H | 4 | `test_diagnostics_bundle.py::test_bundle_paths_with_spaces_and_unicode` |
| S27 reparse containment | C | 4 | `test_diagnostics_bundle.py::test_symlink_and_reparse_roots_refused` (junction case Windows-marked) |
| S28 restrictive from creation | H | 4 | `test_diagnostics_bundle.py::test_posix_modes_0700_0600_from_creation` (POSIX-marked) + Windows doc assertion |
| S29 HTTP 400 allowlisted discovery | H | 2 | `test_diagnostics_sanitize.py::test_error_body_reduction_is_allowlist_only` |
| S30 listener ordering | H | 10 | `test_client_incidents.py::test_listeners_attach_before_first_navigation` |
| S31 unknown host reduction | H | 2 | `test_diagnostics_sanitize.py::test_unknown_hosts_and_routes_become_other` |
| S32 honest HAR completion | H | 10 | `test_client_incidents.py::test_har_complete_only_when_session_changed_file` |
| S33 page crash partial finalize | H | 10 | `test_client_incidents.py::test_page_crash_finalizes_partial_and_teardown_completes` |
| S34 metadata-only no display | M | 10 | `test_client_incidents.py::test_partial_setup_metadata_only_capture` |
| S35 setting boundaries | M | 1 | `test_config.py::test_incident_capture_default_and_invalid` |
| S36 disk full / read-only root | H | 6 | `test_diagnostics_recorder.py::test_readonly_root_reports_failed_capture_original_wins` |
| S37 retention validation | C | 7 | `test_diagnostics_retention.py::test_retention_never_deletes_unknown_or_escaping_content` |
| S38 pending marker lifecycle | H | 4, 7 | `test_diagnostics_bundle.py::test_pending_marker_locked_until_finalize`; `test_diagnostics_retention.py::test_stale_pending_pruned_only_after_lock_and_age` |
| S39 multiprocess prune race | H | 7 | `test_diagnostics_retention.py::test_multiprocess_prune_is_race_safe` (subprocess) |
| S40 collision-free creation | H | 4 | `test_diagnostics_bundle.py::test_exclusive_creation_survives_clock_rollback_collision` |
| S41 fixed-field events | C | 5 | `test_diagnostics_events.py::test_event_constructors_accept_fixed_fields_only` |
| S42 correlation binding | H | 5, 10 | `test_diagnostics_events.py::test_incident_id_bound_once_and_generated_when_absent` |
| S43 live Veo lifecycle | H | Release gate | §10.3 step 4 of the design — paid, user-approved; not an offline task |

---

## Task 1 — Shared retryable classification + `incident_capture` setting

**What:** Fix the §6.5 retryable-contract drift and add the boolean setting, before any
recorder code exists.

**Files:**
- Modify: `src/gflow_cli/errors.py` — add near `EXIT_CODE_MAP`:

  ```python
  RETRYABLE_ERRORS: tuple[type[GFlowError], ...] = (
      WafRejectionError, RateLimitError, TransportTimeoutError, NetworkError,
      BrowserSessionClosedError, FlowAppError, FlowAgentUiError,
  )

  def is_retryable(exc: GFlowError) -> bool:
      """Shared retry classification — single source for CLI JSON, MCP, and worker."""
      return isinstance(exc, RETRYABLE_ERRORS)
  ```

- Modify: `src/gflow_cli/json_output.py` — delete the module-level `_RETRYABLE` tuple;
  `error_payload` uses `"retryable": is_retryable(exc)`.
- Modify: `src/gflow_cli/mcp/tools.py` — every `_error_payload(dict(exc.to_problem_details()))`
  call site adds `"retryable": is_retryable(exc)` into the error dict (both the generate
  path near line 334 and the instructions path near line 1134).
- Modify: `src/gflow_cli/worker/queue.py` (~line 213) and `src/gflow_cli/worker/daemon.py`
  (~line 415) — persisted `error_payload` dict gains the same `"retryable"` key (JSON blob
  value; no schema/column change).
- Modify: `src/gflow_cli/config.py` — in the `--- debugging ---` block next to `har_path`:

  ```python
  incident_capture: bool = Field(
      default=True,
      description=(
          "Automatically write a private incident bundle under "
          "<GFLOW_CLI_HOME>/incidents/ on relevant operational failures. "
          "Bundles contain structural metadata only; screenshots are stored "
          "under sensitive/ and must be reviewed before sharing. Never uploads. "
          "Override via GFLOW_CLI_INCIDENT_CAPTURE."
      ),
  )
  ```

**Steps:**
- [ ] Write the red tests below; run them; observe FAIL (`is_retryable` undefined, MCP/queue payloads lack `retryable`, `incident_capture` unknown field).
- [ ] Implement the changes above.
- [ ] Run: `.venv/Scripts/python.exe -m pytest tests/test_json_output.py tests/test_config.py tests/mcp tests/worker -q` → expected: all pass.
- [ ] `/gflow:check` scoped gates green; commit `fix(errors): share retryable classification across CLI, MCP, and worker` .

**Tests (red first):**
- [ ] `tests/test_json_output.py::test_flow_app_and_agent_ui_errors_retryable` — `error_payload(FlowAppError(...))["error"]["retryable"] is True`; same for `FlowAgentUiError`; `ContentPolicyError` stays `False`. (S24)
- [ ] `tests/mcp/test_server.py::test_mcp_retryable_matches_cli` — for each class in `RETRYABLE_ERRORS` + two terminal classes, the MCP error envelope's `retryable` equals `is_retryable(exc)`. (S24)
- [ ] `tests/worker/test_queue.py::test_queue_retryable_matches_cli` — persisted failure payload carries the same flag; existing columns/keys otherwise unchanged. (S22, S24)
- [ ] `tests/test_config.py::test_incident_capture_default_and_invalid` — default `True`; `GFLOW_CLI_INCIDENT_CAPTURE=false` → `False`; `=notabool` → pydantic `ValidationError` before any browser work. (S35)

---

## Task 2 — `diagnostics.py`: sanitization primitives

**What:** Create the module with the pure reduction functions everything else builds on.
No I/O, no Playwright.

**Files:**
- Create: `src/gflow_cli/diagnostics.py` (module start)
- Create: `tests/test_diagnostics_sanitize.py`

**Interfaces (produced — later tasks consume these exact names):**

```python
class CommandHasher:
    """Per-command HMAC identity. Key is random, held in memory, never persisted."""
    def __init__(self) -> None: ...          # key = secrets.token_bytes(32)
    def identity(self, value: str) -> str: ...  # hex HMAC-SHA256, truncated to 16 chars

def sanitize_url(url: str, hasher: CommandHasher) -> SanitizedUrl: ...
    # strips query/fragment; known hosts -> host_category + canonical_route with
    # identifier segments replaced by placeholders/HMAC ids; unknown -> ("other", "other")

def classify_title(title: str) -> TitleClass: ...
    # {"category": "flow" | "flow_app_crash" | "other", "length": int} — never raw text

def reduce_error_body(parsed: object) -> ErrorBodySummary: ...
    # numeric error code, stable status enum, known-key booleans, unknown_key_count,
    # message_length, waf_signature bool — never key names or message text

def text_summary(text: str, category: str) -> TextSummary: ...
    # {"category": category, "length": len(text)} — the ONLY way console/error text is stored
```

`SanitizedUrl`, `TitleClass`, `ErrorBodySummary`, `TextSummary` are frozen dataclasses (or
TypedDicts) with exactly the fields named above. Known-host allowlist: `labs.google`,
`aisandbox-pa.googleapis.com`, `accounts.google.com`, Google CDN/media hosts already named
in `api/routes.py` — reduced to categories like `flow_app`, `aisandbox`, `google_auth`,
`google_cdn`.

**Steps:**
- [ ] Write red tests (below) with canary fixtures: a fake reCAPTCHA token, cookie string, signed URL (`?X-Goog-Signature=...`), email, prompt text, ANSI escapes, astral-plane Unicode.
- [ ] Run: `.venv/Scripts/python.exe -m pytest tests/test_diagnostics_sanitize.py -q` → expected: FAIL (module missing).
- [ ] Implement; re-run → all pass.
- [ ] `/gflow:check` green; commit `feat(diagnostics): sanitization primitives for incident capture`.

**Tests (red first):**
- [ ] `test_canary_secrets_never_survive_reduction` — every canary run through every primitive; assert canary substring not in `repr()` of any output. (S01)
- [ ] `test_unknown_top_level_keys_reduce_to_count` — body `{"prompt_leak_xyz": 1, "token_abc": 2, "error": {...}}` → `unknown_key_count == 2`, no key names anywhere. (S02)
- [ ] `test_text_stored_as_category_and_length_only` — email/token/account-name inputs → `{category, length}` only. (S03)
- [ ] `test_hmac_identity_uses_random_unpersisted_key` — two `CommandHasher` instances give different identities for the same input; same instance is stable; the key attribute is not JSON-serializable output. (S03)
- [ ] `test_error_body_reduction_is_allowlist_only` — arbitrary 400 body → numeric code, enums, booleans, counts, lengths only. (S29)
- [ ] `test_unknown_hosts_and_routes_become_other` — `https://evil.example/acct-12345/token` → `("other", "other")`, raw host/path absent. (S31)
- [ ] `test_known_flow_routes_reduce_to_canonical` — a real `aisandbox` generation URL with a UUID path segment → category + route with placeholder, query stripped.

---

## Task 3 — `diagnostics.py`: journals, timing map, listener bookkeeping

**What:** Bounded in-memory collection: event records, rings, the primitive timing map,
attach/detach/freeze state.

**Files:**
- Modify: `src/gflow_cli/diagnostics.py`
- Create: `tests/test_diagnostics_journal.py`

**Interfaces (produced):**

```python
@dataclass(frozen=True, slots=True)
class NetworkRecord: ...   # ts_monotonic, ts_utc, method, host_category, route,
                           # resource_type, status_or_failure, duration_ms | None
@dataclass(frozen=True, slots=True)
class ConsoleRecord: ...   # ts_utc, level, category, length, source_category, line, column
@dataclass(frozen=True, slots=True)
class PageErrorRecord: ... # ts_utc, error_class, length

class IncidentJournal:
    network: deque[NetworkRecord]      # maxlen=100
    console: deque[ConsoleRecord]      # maxlen=100
    page_errors: deque[PageErrorRecord]  # maxlen=50
    def freeze(self) -> None: ...      # after freeze, every add_* is a no-op
    def add_network(self, rec: NetworkRecord) -> None: ...
    def add_console(self, rec: ConsoleRecord) -> None: ...
    def add_page_error(self, rec: PageErrorRecord) -> None: ...
    def snapshot(self) -> JournalSnapshot: ...  # immutable primitive copy

class RequestTimingMap:
    """maxsize=256, entry expiry 600 s; keys are primitive (str), values floats."""
    def start(self, key: str, monotonic_ts: float) -> None: ...
    def finish(self, key: str, monotonic_ts: float) -> float | None: ...

class ListenerBookkeeping:
    def mark_attached(self, target_id: int) -> bool: ...  # False if already attached
    def mark_detached(self, target_id: int) -> bool: ...
```

**Steps:**
- [ ] Red tests → run `.venv/Scripts/python.exe -m pytest tests/test_diagnostics_journal.py -q` → FAIL.
- [ ] Implement; green; `/gflow:check`; commit `feat(diagnostics): bounded journals, timing map, and listener bookkeeping`.

**Tests (red first):**
- [ ] `test_rings_enforce_exact_caps` — push 150/150/80 records; lengths are 100/100/50; oldest evicted. (S18 support)
- [ ] `test_timing_map_bounded_under_10k_synthetic_events` — 10,000 starts with 30% finishes: map size ≤ 256 always; expired entries dropped (inject a fake clock); values are primitives (assert no attribute named `request`/`response` anywhere). (S18)
- [ ] `test_events_after_freeze_are_ignored` — freeze, then add_* calls; snapshot unchanged. (S17)
- [ ] `test_listener_bookkeeping_attach_idempotent` — second `mark_attached(id)` returns False; detach exactly once. (S16)
- [ ] `test_snapshot_is_primitive_only` — walk the snapshot recursively; every leaf is `str|int|float|bool|None`.

---

## Task 4 — `diagnostics.py`: bundle filesystem layer

**What:** Incidents root resolution/containment, exclusive bundle creation, pending
marker with advisory lock, atomic manifest-last write, permissions.

**Files:**
- Modify: `src/gflow_cli/diagnostics.py`, `src/gflow_cli/paths.py` (add `incidents_dir(home: Path) -> Path`)
- Create: `tests/test_diagnostics_bundle.py`

**Interfaces (produced):**

```python
def validated_incidents_root(home: Path) -> Path | None: ...
    # <home>/incidents, created 0o700; returns None (with one warning event) if home or
    # the root resolves through a symlink/junction/reparse point or escapes home

class BundleDir:
    path: Path
    @classmethod
    def create_exclusive(cls, root: Path, incident_id: str) -> BundleDir: ...
        # <root>/<YYYY-MM-DD>/<utc>-<correlation>-<fingerprint>-<6 random hex>/
        # os.mkdir exclusive; retry with fresh randomness on FileExistsError (max 3)
    def write_pending_marker(self) -> None: ...   # .pending ≤4096 bytes, versioned JSON
        # {"schema": "gflow-incident-pending-v1", "pid": ..., "created_utc": ...};
        # advisory lock (same msvcrt/fcntl pattern as profile_lease) held until finalize
    def write_artifact(self, name: str, payload: bytes) -> None: ...  # 0o600, O_EXCL
    def finalize(self, manifest: dict[str, object]) -> None: ...
        # write manifest.json.tmp → os.replace → manifest.json; release+remove marker
```

Reparse detection: `Path.is_symlink()` plus, on Windows, `os.lstat(p).st_file_attributes
& stat.FILE_ATTRIBUTE_REPARSE_POINT`. Every create/delete re-validates
`p.resolve().is_relative_to(root.resolve())`.

**Steps:**
- [ ] Red tests → `.venv/Scripts/python.exe -m pytest tests/test_diagnostics_bundle.py -q` → FAIL.
- [ ] Implement; green; `/gflow:check`; commit `feat(diagnostics): contained, exclusive, atomic incident bundle filesystem`.

**Tests (red first):**
- [ ] `test_bundle_paths_with_spaces_and_unicode` — home under `tmp_path / "gflow höme with spaces"`; create + finalize succeeds; JSON is UTF-8. (S26)
- [ ] `test_symlink_and_reparse_roots_refused` — symlinked `incidents` root (and, `@pytest.mark.skipif(sys.platform != "win32")`, a junction) → `validated_incidents_root` returns None; nothing written. (S27)
- [ ] `test_posix_modes_0700_0600_from_creation` — POSIX-marked; stat immediately after create. Windows branch asserts only that no `chmod`-based DACL claim is made (doc note test). (S28)
- [ ] `test_exclusive_creation_survives_clock_rollback_collision` — pre-create the exact candidate name; `create_exclusive` yields a different directory; original untouched. (S40)
- [ ] `test_pending_marker_locked_until_finalize` — marker exists and its lock is unacquirable while staged; after `finalize`, `manifest.json` exists, marker gone. (S38)
- [ ] `test_atomic_manifest_last` — inject failure between artifact write and finalize → directory has `.pending` and no `manifest.json`.

---

## Task 5 — Manifest allowlist + fixed-field observability events

**What:** The explicit manifest builder and the stable `incident.*` /
`profile_lease.owner_evidence_read` event constructors.

**Files:**
- Modify: `src/gflow_cli/diagnostics.py`
- Create: `tests/test_diagnostics_events.py`

**Interfaces (produced):**

```python
def build_manifest(*, incident_id: str, settings: Settings, exc_class: str,
                   problem_type: str, exit_code: int, retryable: bool, route: str,
                   phase: str, artifacts: dict[str, str], har_state: str,
                   suppressed_count: int, timestamps: ManifestTimestamps) -> dict[str, object]:
    # keyword-only, explicit fields; reads ONLY named scalar settings
    # (cli version, browser engine, locale, ui mode, model alias, aspect, count,
    # transport name, headed flag, presence/count of inputs) — never Settings.model_dump()

def emit_capture_started(incident_id: str) -> None: ...
def emit_capture_completed(incident_id: str, status: str, artifact_kinds: list[str], duration_ms: int) -> None: ...
def emit_capture_failed(incident_id: str, exc_class: str, artifact_kind: str) -> None: ...
def emit_capture_suppressed(incident_id: str, count: int) -> None: ...
def emit_retention_pruned(complete_count: int, pending_count: int, bytes_freed: int) -> None: ...
def emit_owner_evidence_read(valid: bool) -> None: ...
```

Incident id: `f"{correlation_id}-{fingerprint}"`; correlation id copied once from the
structlog contextvars at recorder construction, generated (`uuid4().hex[:12]`) when absent.

**Steps:**
- [ ] Red tests → FAIL; implement; green; `/gflow:check`; commit `feat(diagnostics): allowlisted manifest and fixed-field incident events`.

**Tests (red first):**
- [ ] `test_event_constructors_accept_fixed_fields_only` — signatures take no `**kwargs` (assert via `inspect.signature`); captured structlog output for each event contains only the fixed keys. (S41)
- [ ] `test_capture_failed_event_carries_class_only` — raise `RuntimeError("token=SECRETXYZ")` inside a capture step; the emitted event and all log lines lack `SECRETXYZ`. (S25)
- [ ] `test_manifest_allowlist_excludes_settings_secrets` — Settings with canary `gemini_api_key`, `daemon_token`, `storage_uri`, `har_path`, profile path → canaries absent from `json.dumps(build_manifest(...))`. (S01)
- [ ] `test_incident_id_bound_once_and_generated_when_absent` — with bound contextvar the id embeds it; without, a generated id is reused across events and manifest of the same command. (S42)

---

## Task 6 — `IncidentRecorder`: triggers, fingerprints, capture orchestration

**What:** The recorder class itself — trigger classification, fingerprint suppression,
structural DOM capture with validation, sensitive screenshot with fallback, capture
budget, observation-only guarantee, original-error-wins.

**Files:**
- Modify: `src/gflow_cli/diagnostics.py`
- Create: `tests/test_diagnostics_recorder.py` (uses fake `page` objects — plain classes
  with async `evaluate`/`screenshot` methods; no Playwright dependency)

**Interfaces (produced — Task 9/10 consume exactly these):**

```python
@dataclass(frozen=True, slots=True)
class IncidentRef:
    id: str
    capture_status: str            # "complete" | "partial" | "failed"
    path: Path | None              # local-only
    artifacts: tuple[str, ...]     # local-only

class IncidentRecorder:
    def __init__(self, settings: Settings) -> None: ...
    enabled: bool                                    # settings.incident_capture
    journal: IncidentJournal
    timing: RequestTimingMap
    def should_capture(self, exc: BaseException) -> bool: ...
        # True: FlowAppError, FlowAgentUiError, UiModeUnavailableError,
        # UiSelectorDriftError, TransportTimeoutError, BrowserSessionClosedError,
        # WireFormatError, WafRejectionError, NetworkError, unexpected non-GFlowError
        # while a page is available; ProfileLockedError → metadata-only.
        # False: ContentPolicyError, AuthExpiredError, ConfigurationError (non-lock),
        # UsageError, and everything when not enabled.
    async def capture_failure(self, exc: BaseException, *, page: object | None,
                              phase: str, route: str | None = None) -> IncidentRef | None: ...
    async def capture_metadata_only(self, exc: BaseException, *, phase: str) -> IncidentRef | None: ...
    def detach_and_freeze(self) -> None: ...
    def note_har_pre_launch(self, har_path: Path | None) -> None: ...
    def resolve_har_state(self, close_ok: bool) -> str: ...
        # "disabled" | "pending_flush" | "complete" | "possibly_incomplete"
    async def finalize_all(self, *, close_ok: bool) -> None: ...
```

Fingerprint: `sha1(f"{type(exc).__name__}|{problem_type}|{route}|{phase}")[:10]`.
Per-command cap: 3 distinct fingerprints; later duplicates only increment suppression.
All of `capture_failure` / suppression / `finalize_all` serialize on one
`asyncio.Lock`. Total budget 8 s (`asyncio.timeout`); per-artifact bounds: DOM 3 s,
screenshot 4 s (one full-page attempt, one viewport fallback). Screenshot only for
UI-state failures (`FlowAppError`, agent/UI-mode, selector drift, `TransportTimeoutError`
on a UI phase), never WAF/network/wire/auth/lock. The structural DOM JS returns ONLY:
allowlisted signal booleans, ligature array, tag/role counts, viewport/scroll,
bounded overlay records (tag, role, aria-modal, visible, rect, z-index, pointer-events,
inner ligatures) — Python-side validation drops any unexpected key and rejects
non-primitive values before serialization.

**Steps:**
- [ ] Red tests → FAIL; implement; green; `/gflow:check`; commit `feat(diagnostics): session-scoped IncidentRecorder with bounded private capture`.

**Tests (red first):**
- [ ] `test_trigger_classification_matches_design` — table-driven `should_capture` over every class in §4.2 plus the non-triggers. (S06/S35 support)
- [ ] `test_ui_failure_stages_dom_and_screenshot` — fake page; `capture_failure(FlowAppError(...))` stages `ui.json` + `sensitive/screenshot.png` + journals; manifest not yet present. (S10)
- [ ] `test_screenshot_lives_under_sensitive_and_manifest_marks_it` — after `finalize_all`, manifest classifies the png `sensitive`. (S04)
- [ ] `test_structural_result_rejects_non_allowlisted_fields` — fake `evaluate` returns hostile extras (`bodyText`, `ariaLabel`, prompt canary); persisted `ui.json` has no canary and no unexpected key. (S12)
- [ ] `test_overlay_records_bounded_geometry_without_text` — overlay record keeps rect/z-index/ligatures, drops text. (S11)
- [ ] `test_fullpage_screenshot_failure_falls_back_to_viewport` — fake full-page raises/hangs; viewport shot written; artifact status `partial`; original error unchanged. (S13)
- [ ] `test_concurrent_capture_same_fingerprint_yields_one_bundle` — `asyncio.gather` of 10 `capture_failure` calls, same exception shape → one staged dir; suppression 9. (S19, S14 unit)
- [ ] `test_bundle_cap_three_distinct_fingerprints` — 5 distinct failure shapes → 3 dirs.
- [ ] `test_capture_is_observation_only` — fake page records every method call; capture uses only `evaluate`/`screenshot`; no `goto`/`click`/`fill`/`reload`. (S15)
- [ ] `test_capture_io_failure_preserves_original_exception` — read-only bundle dir; `capture_failure` returns ref with `capture_status="failed"` (or None) and raises nothing. (S23)
- [ ] `test_readonly_root_reports_failed_capture_original_wins` — root unwritable → no recursion, one `incident.capture_failed`. (S36)
- [ ] `test_bundle_json_contains_no_canary` — end-to-end recorder run with canaries in URL/console/error body; grep every written file. (S01)

---

## Task 7 — Retention

**What:** Startup pruning under the non-blocking retention lock, with bounded validation
and the pending-marker protocol.

**Files:**
- Modify: `src/gflow_cli/diagnostics.py`
- Create: `tests/test_diagnostics_retention.py`

**Interfaces (produced):**

```python
def run_retention(root: Path) -> None: ...
    # non-blocking <root>/.retention lock; if held elsewhere → return silently.
    # Complete bundles: direct children with valid ≤64 KiB gflow-incident-v1 manifest and
    #   exact allowlisted artifact set → prune oldest beyond 50 dirs / 250 MiB.
    # Pending: acquire marker lock non-blocking; unacquirable → active, untouched;
    #   acquirable + valid manifest → treat complete, remove stale marker;
    #   acquirable + no manifest → prune if >24 h old or beyond 20 dirs / 100 MiB.
    # Anything else (unknown dirs, oversized/invalid manifests, reparse points,
    # escaping paths) → untouched. Never follows links while measuring or deleting.
```

**Steps:**
- [ ] Red tests → FAIL; implement; green; `/gflow:check`; commit `feat(diagnostics): lock-gated bounded incident retention`.

**Tests (red first):**
- [ ] `test_retention_never_deletes_unknown_or_escaping_content` — plant an unrelated dir, a dir with a 1 MiB manifest, a manifest with wrong schema, a symlinked child pointing at a user dir → all survive; only valid oldest bundles pruned. (S37)
- [ ] `test_stale_pending_pruned_only_after_lock_and_age` — locked marker survives; unlocked young marker survives; unlocked >24 h marker pruned; unlocked marker + valid manifest → marker removed, bundle kept. (S38)
- [ ] `test_count_and_byte_limits_enforced` — 60 valid bundles / oversized payloads → ≤50 and ≤250 MiB after pruning; `incident.retention_pruned` counts match.
- [ ] `test_retention_skips_when_lock_held` — hold `.retention` in-process; run → no deletion.
- [ ] `test_multiprocess_prune_is_race_safe` — subprocess (pattern from `tests/test_profile_lease_subprocess.py`): two processes prune the same seeded root concurrently; no crash, no double-delete error, active pending dir intact. (S39)

---

## Task 8 — `ProfileLease` offset-1 owner metadata + private evidence

**What:** Reserve byte 0 for the kernel lock; versioned metadata at bytes 1–4095;
contender-side evidence read; private exception attribute; no remote/log leak.

**Files:**
- Modify: `src/gflow_cli/profile_lease.py`, `src/gflow_cli/errors.py` (ProfileLockedError attr)
- Modify: `tests/test_profile_lease.py`, `tests/test_profile_lease_subprocess.py`

**Implementation contract:**

```python
class OwnerMetadata(TypedDict):
    version: int            # 1
    pid: int
    process_start_time: float
    profile_name: str
    owner_token: str

# Writer (_acquire_kernel_lock): byte 0 = b"\0" sentinel (the ONLY locked byte);
# json payload written at offset 1; ftruncate(fd, 1 + len(payload)); payload > 4095
# bytes is a programming error (metadata is fixed-shape, but guard + truncate names).
#
# Contender (on lock failure, before _raise_locked):
#   same-process: _registry[canonical].owner_metadata (in-memory, trusted)
#   cross-process: os.pread/lseek+read of at most 4095 bytes from offset 1 on the
#     already-open fd; json.loads; validate version==1, exact keys, primitive types;
#     any failure (legacy byte-0 files, torn write, junk) -> evidence = None
# Evidence carried as ProfileLockedError.owner_evidence:
#   OwnerEvidence(pid: int, process_start_time: float,
#                 profile_identity: str, owner_token_identity: str)  # HMAC ids, never raw
#   — a private typed attribute, excluded from to_problem_details() and __str__.
#   Identities are produced with diagnostics.CommandHasher: profile_lease accepts an
#   optional hasher (default: one module-level per-process instance from diagnostics —
#   import direction diagnostics <- profile_lease only, no cycle) so the raw token never
#   leaves this module.
# emit_owner_evidence_read(valid=...) on every cross-process read attempt.
```

**Steps:**
- [ ] Red tests → FAIL; implement; green (including the existing lease suite unchanged in behavior); `/gflow:check`; commit `feat(lease): offset-1 owner metadata with private contention evidence`.

**Tests (red first):**
- [ ] `test_lock_file_layout_byte0_sentinel_metadata_at_offset1` — acquire; file bytes: `b"\0"` then valid JSON; release leaves file (never unlinks).
- [ ] `test_same_process_contention_uses_registry_metadata` — second acquire in-process → `ProfileLockedError` with evidence PID == `os.getpid()`; no file read needed. (S07)
- [ ] `test_cross_process_offset1_metadata_read` (subprocess; Windows path exercises the locked byte 0) — child holds lease; parent acquire fails with populated evidence; owner token identity ≠ raw token. (S08)
- [ ] `test_legacy_byte0_metadata_reports_unavailable` — hand-write an old-format file (JSON at byte 0), hold its lock in a subprocess → contention evidence is None, no exception besides `ProfileLockedError`. (S08)
- [ ] `test_stale_metadata_never_triggers_reclaim_or_unlink` — malicious metadata (huge PID, dead PID, junk) → lock file still present, no `os.kill`/unlink called (assert via monkeypatch spies), error remediation unchanged. (S09)
- [ ] `test_owner_evidence_absent_from_problem_details_and_logs` — `to_problem_details()`, `error_payload`, MCP envelope, and captured logs contain neither raw token, PID field names, lock path, nor profile path. (S21 support, design §6.4)

---

## Task 9 — Local/remote incident presentation

**What:** Carry the `IncidentRef` on the error, expose the remote-safe RFC 9457
extension, enrich local CLI output only.

**Files:**
- Modify: `src/gflow_cli/errors.py` — `GFlowError` gains `incident_ref: IncidentRef | None = None`
  (set post-construction by the capture boundary; excluded from `__init__` churn);
  `to_problem_details()` adds `"incident": {"id": ..., "capture_status": ...}` when set —
  never path/artifacts.
- Modify: `src/gflow_cli/json_output.py` — `error_payload` adds the local `incident`
  object `{id, path, capture_status, artifacts}` when `exc.incident_ref` has a path.
- Modify: `src/gflow_cli/_cli_helpers.py` — human error path prints exactly one extra
  sentence: `Incident bundle: <path> — review before sharing; sensitive artifacts may
  contain account or media data.`
- Tests: `tests/test_errors.py`, `tests/test_json_output.py`, `tests/mcp/test_server.py`,
  `tests/worker/test_queue.py`

**Steps:**
- [ ] Red tests → FAIL; implement; green; `/gflow:check`; commit `feat(errors): local-rich, remote-opaque incident references`.

**Tests (red first):**
- [ ] `test_problem_details_incident_is_opaque` — with a ref whose path contains a username canary: `to_problem_details()["incident"]` has exactly `{id, capture_status}`; canary absent. (S21)
- [ ] `tests/mcp/test_server.py::test_mcp_error_envelope_omits_local_path` — MCP envelope for the same error: no path/username/artifact names. (S21)
- [ ] `test_local_cli_json_includes_path_and_artifacts` — `error_payload` carries the full local object. (S21 local half)
- [ ] `test_local_incident_output_warns_review_before_sharing` — human renderer sentence includes the path and the review warning. (S04)
- [ ] `tests/worker/test_queue.py::test_error_payload_gains_retryable_without_schema_change` — persisted payload uses only the shared problem-details extension; no new columns. (S22)

---

## Task 10 — `FlowApiClient` + transport wiring

**What:** Recorder lifecycle inside the client: construct pre-lease, metadata-only on
contention, listeners attached post-launch/pre-navigation and to every pooled/new page,
`TransportSetup.recorder`, image/video boundary capture, detach/freeze before close,
honest HAR state, finalize after close, cancellation-safe order preserved.

**Files:**
- Modify: `src/gflow_cli/api/client.py`:
  - construct `self._recorder = IncidentRecorder(self.settings)` in `__init__`/`__aenter__`
    before `_enter_setup`; `run_retention` best-effort at construction.
  - wrap `ProfileLease(...).acquire()` (line ~603): on `ProfileLockedError` →
    `await self._recorder.capture_metadata_only(exc, phase="profile_lease")`, attach ref, re-raise.
  - `self._recorder.note_har_pre_launch(self.settings.har_path)` before launch.
  - after `_launch_persistent_context`: `context.on("request"/"response"/"requestfailed", ...)`
    handlers → journal only; `context.on("page", ...)` + per-pool-page
    `page.on("console"/"pageerror", ...)` — all before the bootstrap `goto`.
  - generation boundaries (image/video/batch `except` paths) call
    `await self._recorder.capture_failure(exc, page=..., phase=..., route=...)` while the
    page is alive, attach the returned ref to the exception.
  - `_close_browser_resources`: `detach_and_freeze()` first; after context close,
    `await self._recorder.finalize_all(close_ok=...)` via the same bounded
    `run_teardown_step` pattern; driver stop and lease release order unchanged.
- Modify: `src/gflow_cli/api/transports/base.py` — `TransportSetup` gains
  `recorder: object | None = None` (typed `IncidentRecorder | None` via TYPE_CHECKING);
  `_build_transport_setup` passes it; `UiAutomationTransport` uses it for its own failure
  boundaries; other transports ignore it.
- Create: `tests/api/test_client_incidents.py` (fake context/page pattern from
  `tests/api/test_client.py`).

**Steps:**
- [ ] Red tests → FAIL; implement; green; `/gflow:check`; commit `feat(client): wire IncidentRecorder through the browser lifecycle`.

**Tests (red first):**
- [ ] `test_listeners_attach_before_first_navigation` — fake context records call order; `on(...)` registrations precede the bootstrap `goto`. (S30)
- [ ] `test_pooled_pages_each_attached_exactly_once` — pool of 3 + one late `page` event → 4 attachments, no duplicates; teardown detaches each once. (S16)
- [ ] `test_late_callbacks_after_freeze_are_noops` — invoke saved handlers after `detach_and_freeze` → journals unchanged, no exception. (S17)
- [ ] `test_har_state_disabled_when_unset_and_never_enabled` — settings without `har_path`: launch kwargs lack `record_har_path`; manifest `har_state == "disabled"`. (S05)
- [ ] `test_har_complete_only_when_session_changed_file` — pre-existing unchanged file + close OK → `possibly_incomplete`; file changed by session + close OK → `complete`; close raises → `possibly_incomplete`. (S32)
- [ ] `test_capture_runs_before_context_close_and_exit_code_31_kept` — `FlowAppError` in a generation boundary: `capture_failure` observed before `context.close`; mapped exit code still 31. (S10)
- [ ] `test_cancellation_during_capture_still_releases_lease` — inject `CancelledError` during a capture step: cancellation re-raised, context closed, driver stopped, lease released, manifest `possibly_incomplete`. (S20)
- [ ] `test_page_crash_finalizes_partial_and_teardown_completes` — page `evaluate` raises TargetClosed: journals still finalize `partial`; teardown completes. (S33)
- [ ] `test_auth_expired_creates_no_bundle` and `test_content_policy_creates_no_bundle` — no incidents dir entry. (S06)
- [ ] `test_partial_setup_metadata_only_capture` — failure before any page exists → manifest-only bundle; original error authoritative. (S34)
- [ ] `test_profile_lock_metadata_only_incident_before_chrome` — contention: no `launch_persistent_context` call, bundle contains owner evidence fields (HMAC ids), exit 11. (S07/S09 wiring)
- [ ] `test_callbacks_retain_no_playwright_objects` — after events, `gc.get_referrers` /attribute walk of recorder state finds no fake Request/Response/ConsoleMessage instances. (S17/S18 integration)

---

## Task 11 — Consolidate `capture_ui_diagnostics`

**What:** One DOM/screenshot engine. The mode-switch failure path routes through the
recorder; the legacy raw signature (raw URL/title/bodyTextPreview) disappears from the
generalized path.

**Files:**
- Modify: `src/gflow_cli/api/transports/ui_automation_video.py` — `_UI_DIAG_JS` loses
  `url`, `title`, `bodyTextPreview` raw fields (recorder's sanitized structural JS is the
  engine); `capture_ui_diagnostics` becomes a thin wrapper calling the recorder when one
  is present on the transport (from `TransportSetup.recorder`), else the bounded
  structural JSON only; the call site at line ~1293 (`diag_mode_switch_miss`) passes
  through the recorder; error messages that name a diagnostic path keep naming the real
  written path.
- Modify: existing transport tests that asserted the old fields.

**Steps:**
- [ ] Red tests → FAIL; implement; green; `/gflow:check`; commit `refactor(ui): route mode-switch diagnostics through the incident recorder`.

**Tests (red first):**
- [ ] `tests/api/transports/...::test_mode_switch_diag_uses_incident_bundle` — mode-switch failure with recorder present → one incident bundle, no legacy side-by-side `diag_mode_switch_miss.json`/`.png` duplicate pair. (S10 consolidation, design §6.3)
- [ ] `test_legacy_wrapper_emits_no_raw_url_title_or_body_text` — wrapper output (recorder absent) has no `url`/`title`/`bodyTextPreview` keys. (S12 support)

---

## Task 12 — BDD feature + batch suppression integration

**What:** The six SCENARIO.md gherkin scenarios as executable BDD, plus the image-batch
suppression path.

**Files:**
- Create: `tests/features/incident_diagnostics.feature` — copy the six `Scenario:` blocks
  from SCENARIO.md §"Suggested BDD scenarios" verbatim.
- Create: step module beside the existing feature step files (pattern from
  `tests/features/conftest.py` and existing `image.feature` steps), using fake
  transports/pages — no live Flow.

**Steps:**
- [ ] Red (feature file + missing steps) → FAIL; implement steps against Tasks 1–11 surfaces; green; `/gflow:check`; commit `test(bdd): incident diagnostics feature scenarios`.

**Tests (red first):**
- [ ] `Capture failure preserves the operational error` — exit 31, read-only incident dir, no raw text, no retry. (S15, S23, S36)
- [ ] `A systemic batch failure is captured once` — 50-row manifest, 1 staged bundle, 49 suppressed, ≤3 bundles. (S14)
- [ ] `Profile contention reports evidence but never reclaims` — exit 11 pre-Chrome, metadata-only bundle, nothing deleted. (S07/S09)
- [ ] `Remote errors do not expose local incident paths` — MCP/HTTP path+username absent. (S21)
- [ ] `Cancellation leaves no browser or lease` — HAR possibly_incomplete, cancellation propagates, lease released. (S20)
- [ ] `Successful generation creates no incident` — valid artifact, no incident dir. (S43 offline analogue)

---

## Task 13 — Documentation + truth-source drift

**What:** All §11 documentation, in one commit.

**Files:**
- `.env.template` + `docs/CONFIGURATION.md` — `GFLOW_CLI_INCIDENT_CAPTURE` (default true,
  what a bundle contains, sensitive/ warning, no-upload).
- `docs/DEBUGGING.md` — bundle layout, trigger list, HAR escalation path,
  review-before-sharing, retention bounds.
- `docs/SECURITY.md` — automatic vs sensitive tiers, POSIX 0700/0600, Windows
  inherited-ACL truth (no chmod-DACL claim), no-upload guarantee.
- `docs/ARCHITECTURE.md` — recorder ownership, teardown ordering diagram update.
- `KNOWN_ISSUES.md` — #369 (one-time banner), #370 (reported stale lock), unexplained
  image HTTP 400: open evidence targets, what v0.43.0 captures for each.
- `CHANGELOG.md` `[Unreleased]` — incident bundles, retryable correction, lease evidence.
- Drift fixes: root `PLAN.md` MCP-server backlog entry marked shipped (v0.21.0/v0.23.0);
  image-batch docs — serial (not parallel) execution and continue-on-error default
  corrected; 2026-07-22 live-attempt ledger count reconciled before citation.
- `docs/INDEX.md` routing entries.

**Steps:**
- [ ] Write docs; run `uv run python scripts/ci/check_repo_hygiene.py`, `check_doc_links.py`, `check_website_docs_pii.py` → expected: all pass.
- [ ] `tests/test_documentation_gate.py` green.
- [ ] Commit `docs: incident diagnostics operator documentation + truth-source drift fixes`.

---

## Task 14 — Full gates + scenario audit

**What:** Whole-feature verification before PR.

**Steps:**
- [ ] Full Impeccable Routine (PowerShell, worktree venv):
  `check_repo_hygiene` / `check_doc_links` / `check_website_docs_pii` / `ruff check` /
  `ruff format --check` / `pyright src` / `python -m pytest -q --cov=gflow_cli`
  → expected: all green, coverage ≥ 91% baseline zone (floor 80%), and the packaging test
  passes **in the aggregate run** (an aggregate-only failure = lifecycle/resource
  regression per design §13.2 — bisect, do not waive).
- [ ] Audit this matrix: every Critical/High row's named test exists and passes; fix any drift.
- [ ] Self-review diff for YAGNI/over-engineering (`/gflow:branch-review` optional but recommended before PR).
- [ ] Push branch; open PR to `develop`; drive `/gflow:sonar` to zero new issues.

---

## Release gate (outside this plan's offline scope)

Per design §13 — v0.43.0 remains NO-GO until, additionally:

- [ ] Live matrix §10.3: (1) recorder-on-real-page bundle inspection, (3) real
  two-process lease contention — runnable without spend; (2) one real T2I — confirm
  credit behavior first; (4) one paid `veo-lite` T2V — **separate explicit user
  approval**. Record all in `docs/LIVE_VERIFICATION_v0.43.0.md` + INDEX entry; an
  unapproved paid gate is recorded as an explicit unverified risk, never omitted.
- [ ] Documentation council review (`/gflow:doc-review`) passes.
- [ ] Signed-tag publication requires separate explicit user approval.
- [ ] S43 statistical/availability claims require a separately approved budgeted soak.

## Definition of done

- [ ] All task steps checked; every task committed atomically green.
- [ ] `/gflow:check` green at every commit; final full-suite + coverage green.
- [ ] Scenario matrix: 9 Critical + 30 High all passing named tests (S43 = release gate).
- [ ] CHANGELOG + docs updated; doc-link/PII/hygiene gates green.
- [ ] No `# TODO` in diff without a tracked issue link.
- [ ] SonarCloud zero new issues on the PR.
