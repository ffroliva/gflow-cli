# Stay-mounted batch session — design

**Status:** Design, **v3 — 5-reviewer council findings applied**, awaiting user review
**Author:** Flavio Oliva
**Date:** 2026-05-22
**Revisions:**
- v1 — initial design after brainstorming.
- v2 — applied 3-Sonnet council findings (listener detach mechanism, post-attach-time filter realism, drop client wrapper, fail-fast default, project_id on result, multi-listener test, send_prompt-clears-field verification, drop transport out_dir).
- v3 — applied 2-Sonnet additional council findings (production/operations + maintainability lenses): partial-results salvage on fail-fast, prompt_idx + prompt_hash mandatory in structlog and on `BatchSubmissionResult`, structlog warn on orphaned project on setup failure, post-download `BatchIntegrityError` check, fix §6 diagram annotation about project_id filter (it does not segregate between prompts in same-project mode), fix §8.2 and §11 v3-3 ghost references to the dropped client wrapper, clarify §5.6 `_attach_check_time` is new wiring (not Playwright-native), remove redundant §10 placement question.

**Branch:** `feature/multi-image-prompt`
**Supersedes the v4-amended portion of:** [`docs/superpowers/specs/2026-05-21-multi-image-prompt-design.md`](2026-05-21-multi-image-prompt-design.md) (the §8 jitter matrix + the `--same-project` flag surface)
**Memory cross-link:** [`batch-submission-cadence`](file:///C:/Users/ffrol/.claude/projects/C--development-github-gflow-cli/memory/batch-submission-cadence.md)

## 1. Problem

`gflow image batch --same-project=1` claims to put all prompts in one Flow project but doesn't. Confirmed live 2026-05-22 on profile `denon82`: each prompt of a batch landed in its own Flow project. Root cause: `src/gflow_cli/api/transports/ui_automation.py::generate_images` accepts `project_id` for Protocol parity but discards it (`_ = project_id  # accepted for Protocol parity; UI creates its own project`) and runs a full `gallery → "+ New project" → editor → submit → close` cycle on every call.

The orchestrating code in `image_batch.run_manifest_image_batch` correctly creates a shared project via `client.create_project` and threads the id through, but the transport drops it. The jitter matrix that depended on this same-project mode was invalidated mid-run (see `docs/LIVE_VERIFICATION_image_batch.md` § Verdict).

## 2. Goal

`gflow image batch` must keep all its prompts in one Flow project, with jitter between submissions. The browser stays mounted on that project's editor page across all submissions and closes only at the end of the batch. Per-prompt outcomes (ok / fail) survive a single prompt's failure — the remaining prompts continue.

## 3. Scope

### In scope

- Refactor `UiAutomationTransport.generate_images` into a stay-mounted shape via a new method `generate_images_batch(prompts, jitter_range, …)` that opens the editor once, submits N prompts with jitter, awaits and parses responses in submission order, and closes only at the very end.
- Drop the `--same-project` flag from `gflow image batch` CLI. The flag collapses to the only behaviour.
- Drop the `same_project: bool` parameter and the entire `same_project=False` branch from `image_batch.run_manifest_image_batch`.
- Drop the `client.create_project` call from the orchestrator. The transport owns project creation as the first step of its stay-mounted session.
- Update `docs/USAGE.md`, `CHANGELOG.md [Unreleased]`, `--help` text, and the public docstring of `run_manifest_image_batch` to describe always-same-project semantics and the submission-cadence rationale for jitter.
- Refactor `tests/e2e/test_image_batch_e2e.py` to assert all returned outcomes share one `project_id`. Drop the `GFLOW_CLI_E2E_BATCH_SAME_PROJECT` env var.
- One credit-spending end-to-end verification on profile `denon82` after the refactor lands.

### Out of scope (deferred)

- **Persistent asset layer.** `(profile, project_id, generation_id, output_idx) → (file, sha256)` registry. Tracked as item #10 in `phase-b-followups` memory. User-confirmed deferred 2026-05-22.
- **Per-project Chrome-session multiplexing.** The user-confirmed model is: batch = same-project; for different-project results, loop `gflow image t2i` externally.
- **Re-running the jitter matrix.** The matrix as designed cannot be repaired; if cadence tuning is later needed it will be sized against the working implementation.
- **Single-prompt path.** `UiAutomationTransport.generate_images` and `gflow image t2i` are not modified. Their per-call open-submit-close cycle remains; only the batch path changes.
- **Video transports.** `Mode.I2V` / `Mode.R2V` and `gflow video batch` (still stubbed per Phase B) are independent.

## 4. Architecture

```
image_batch.run_manifest_image_batch  ← orchestrator (simplified)
  └── transport.generate_images_batch(prompts, jitter, continue_on_error)  ← new stay-mounted impl
        ├── _enter_editor                  (existing — runs ONCE per batch)
        ├── _dismiss_blocking_overlays     (existing — once)
        ├── project_id ← _extract_project_id(page.url)  (captured ONCE at batch start)
        ├── for each prompt in prompts:
        │     ├── _configure_generation_settings  (existing — per prompt; aspect/count may differ)
        │     ├── (captured_list, detach_fn) ← _attach_batch_response_listener
        │     │   (EXTENDED, see §5.6; post-attach-time is the primary intra-batch
        │     │    segregation — project_id is only a cross-project guard in same-project
        │     │    mode, per Council Finding M2)
        │     ├── _send_prompt                    (verified to fill() not type(), see §5.7)
        │     ├── record PendingSubmission(idx, captured_list, detach_fn, expected_count)
        │     └── asyncio.sleep(uniform(*jitter_range)) if not last prompt
        └── for each pending in submission order:
              ├── _await_captured                 (existing — per prompt, per-prompt timeout)
              ├── pending.detach_fn()             (guarantees listener removed even on timeout)
              ├── if len(captured) < expected: status="fail" with TimeoutError
              └── else: _images_from_responses → BatchSubmissionResult
  └── for each result with status=ok:
        └── client.download_image(image, target)        (existing pattern)
```

Orchestrator calls `transport.generate_images_batch` directly. The `FlowApiClient` layer is bypassed for batches (Council Finding A1 — the wrapper was net-negative). A capability check (`isinstance(self.transport, UiAutomationTransport)`) guards the call; only `UiAutomationTransport` implements this method.

Key points:

- **`_enter_editor` runs once.** This is the real bug fix.
- **All existing helpers reused** per prompt: settings, listener, send, await, parse. No duplicated logic.
- **Single-prompt `generate_images` unchanged.** `gflow image t2i` and any direct callers continue to use the existing per-call flow.
- **`_generate_lock`** is acquired at batch entry and released only after all `_await_captured` calls complete. Any concurrent `generate_images` call on the same transport instance blocks for the full batch duration. (One batch at a time per transport instance.)
- **`project_id` is captured once** at batch start via `_extract_project_id(page.url)` and stored on the transport's batch-local state. The editor URL is stable for the batch lifetime because the page never navigates after `_enter_editor` returns.

## 5. Components

### 5.1 New: `UiAutomationTransport.generate_images_batch`

```python
async def generate_images_batch(
    self,
    *,
    prompts: list[GenerateImageRequest],
    jitter_range: tuple[float, float],
    continue_on_error: bool = False,
) -> list[BatchSubmissionResult]:
    """Submit all prompts into one Flow project and return per-prompt results.

    Opens the editor once, configures+submits each prompt with jitter between
    submissions, awaits and parses responses in submission order, closes the
    editor only at the very end. By default (`continue_on_error=False`),
    the first per-prompt failure stops further submissions and raises after
    detaching any in-flight listeners. With `continue_on_error=True`, all
    prompts are submitted regardless of per-prompt failures, and failures
    surface as `BatchSubmissionResult(status="fail")`.

    `project_id` is NOT a parameter. The transport creates the project via the
    Flow UI on the first action and reuses it for the lifetime of the batch.
    The captured `project_id` is returned on every `BatchSubmissionResult` so
    callers and tests can assert shared-project semantics structurally.

    `out_dir` is NOT a parameter — downloads are the orchestrator's
    responsibility (Council Finding A3 nitpick).

    Raises only for batch-setup failures (browser launch, `_enter_editor`,
    overlay dismiss). Per-prompt failures surface as `BatchSubmissionResult`
    with `status="fail"` when `continue_on_error=True`; with
    `continue_on_error=False`, the first per-prompt failure causes the method
    to detach all listeners and re-raise.
    """
```

Reuses existing `self._page`, `self._generate_lock`, all existing helper methods. **Locks scope:** `_generate_lock` is held for the entire batch (open + all submits + all awaits) so any concurrent `generate_images` call on the same instance blocks for the batch duration.

### 5.2 New: `BatchSubmissionResult` dataclass

`src/gflow_cli/api/dto.py`:

```python
@dataclass(frozen=True)
class BatchSubmissionResult:
    status: Literal["ok", "fail"]
    project_id: str           # shared across the batch (Council Finding T3)
    prompt_idx: int           # 0-based; self-describing position (Council Finding O3 nitpick)
    prompt_hash: str          # truncated sha256 of prompt text — lets logs/results correlate (Council Finding O2)
    images: tuple[GeneratedImage, ...]  # () when status="fail"
    error: GFlowError | None = None
```

One per submitted prompt, returned in submission order. `project_id` is identical on every result of a single batch — that lets the e2e assert `len({r.project_id for r in results}) == 1` structurally without relying on structlog. `prompt_idx` makes each result self-describing so `zip(prompts, results)` mis-alignment is detectable. `prompt_hash` (matching the existing `_prompt_hash` helper in `image_batch.py`) lets support correlate "which prompt failed" without leaking the full prompt text into logs.

### 5.3 ~~`FlowApiClient.generate_images_batch`~~ — **dropped per Council Finding A1**

The earlier v1 draft proposed a thin client-layer delegate. The 3-Sonnet council unanimously flagged it as net-negative:

- The delegate does nothing except relay arguments.
- Keeping it forces `FlowTransportStrategy` (in `src/gflow_cli/api/transports/base.py`) to grow a `generate_images_batch` member, which would require stub implementations on all three experimental HTTP transports (`bearer.py`, `evaluate_fetch.py`, `sapisidhash.py`) — transports that have no stay-mounted concept.
- Without adding to the Protocol, the delegate becomes an untyped dispatch (`self.transport.generate_images_batch(...)`), which `pyright --strict` correctly flags.

**Decision:** orchestrator calls `transport.generate_images_batch` directly, with a capability check:

```python
# inside run_manifest_image_batch, after async with factory(...) as client:
if not isinstance(client.transport, UiAutomationTransport):
    raise RuntimeError(
        "gflow image batch requires the ui_automation transport; "
        f"got {type(client.transport).__name__}"
    )
results = await client.transport.generate_images_batch(
    prompts=prompts,
    jitter_range=jitter_range,
    continue_on_error=continue_on_error,
)
```

This keeps the batch capability transport-local and the public API surface unchanged outside the orchestrator.

### 5.4 Refactored: `image_batch.run_manifest_image_batch`

- Removes parameter `same_project: bool`.
- Removes the `if same_project: client.create_project(...)` block.
- Removes the per-prompt `for idx, item in enumerate(prompts): … run_one_image_prompt(...)` loop.
- Adds: capability check (`isinstance(client.transport, UiAutomationTransport)` — see §5.3).
- Adds: single call `results = await client.transport.generate_images_batch(prompts=[...], jitter_range=jitter_range, continue_on_error=continue_on_error)`.
- Adds: download loop iterating `results` zipped with `prompts`; for each `ok` result, downloads via `client.download_image`; for each `fail` result, records `BatchOutcome.status="fail"` with the captured error.
- Public return type `list[BatchOutcome]` unchanged. Existing observability events (`image_batch.submission_attempt`, `image_batch.row_completed`, `image_batch.inter_submission_latency_ms`, `image_batch.submission_result`) preserved — emitted at the same logical points. `image_batch.submission_attempt` now uses the `project_id` from `results[idx].project_id` rather than a `shared_project_id` variable.

### 5.5 Removed: CLI surface

- `gflow image batch --same-project` flag removed (Click option deleted from `src/gflow_cli/cli_image.py`).
- `GFLOW_CLI_E2E_BATCH_SAME_PROJECT` env var removed from the e2e test.
- `same_project` parameter removed from `run_manifest_image_batch`'s signature (BREAKING for direct callers — there are none outside the CLI and tests; council confirmed grep returned zero non-spec/non-test hits outside this branch).

### 5.6 Extended: `_attach_batch_response_listener` — listener detach mechanism

The existing helper returns just the capture list. The stay-mounted design needs N concurrent listeners that get cleanly detached, so the helper is extended to return a `(captured, detach_fn)` pair (Council Finding P2):

```python
# in ui_automation.py
def _attach_batch_response_listener(
    self,
    page: Page,
    *,
    project_id: str | None = None,
) -> tuple[list[Any], Callable[[], None]]:
    """Attach a response handler that captures matching batchGenerateImages
    responses into a list. Returns the list and a detach callback.

    The returned callable removes the handler from the Page when invoked;
    it is idempotent (safe to call twice).
    """
    captured: list[Any] = []
    attach_time = time.monotonic()

    def on_response(response: Any) -> None:
        # ... existing filter logic ...
        if hasattr(response, "_monotonic_at_arrival"):
            return  # already-processed event safeguard (Playwright reuse)
        # Skip responses that arrived before this listener was attached.
        # This prevents prompt N's listener from absorbing a late-arriving
        # response that belongs to prompt N-1 (Council Finding P1).
        # NOTE: `_attach_check_time` is NOT a Playwright-native attribute.
        # v3-3 either (a) records the time in a sidecar dict keyed by response
        # ID, or (b) skips this guard entirely and relies on submission-order
        # arrival per the §10 "Open question" decision. The line below is
        # placeholder pseudocode showing the intent (Council Finding M nitpick 1).
        if getattr(response, "_attach_check_time", attach_time) < attach_time:
            return
        # ... existing capture logic ...

    page.on("response", on_response)

    def detach() -> None:
        try:
            page.remove_listener("response", on_response)
        except Exception:  # noqa: BLE001 — idempotent on already-removed
            pass

    return captured, detach
```

Single-prompt callers (`generate_images`, line 991) update their call site from `captured = self._attach_batch_response_listener(...)` to `captured, _detach = self._attach_batch_response_listener(...)` and ignore the detach handle (the per-call page is closed shortly after, so detach is implicit). Batch callers store both halves.

**Open question (decided during implementation):** the precise post-attach-time semantics. Two viable shapes:
1. **Closure-captured `attach_time = time.monotonic()`** with a per-response timestamp check. Requires monkey-patching or wrapping Playwright responses to carry an arrival timestamp — *the current code does not capture arrival time on responses, so this needs a small piece of new wiring*.
2. **Submission-order arrival assumption** — accept that Flow responses arrive after their submission's click event, and rely on the JavaScript event loop to keep listener registration ordered with click events. Simpler, no new timestamping wiring needed, but unverified.

v3-3 starts with option 2 (simpler) and adds option 1 only if the live e2e shows cross-contamination. Documented as Risk row 1 in §9.

### 5.7 Verified: `_send_prompt` clears the editor text field

Council Finding P3 flagged that the stay-mounted design assumes `_send_prompt` clears the prompt input before typing the new prompt. If the current implementation uses Playwright `type()` without `fill()` (or without a prior `select_all + delete`), prompts would concatenate in the persistent editor.

**Action before v3-3 lands:** read `_send_prompt` in `ui_automation.py` and confirm it uses `fill()` (which replaces the field's value) rather than `type()` (which appends). If it uses `type()`, the v3-3 commit must convert it to `fill()` and add a unit test that mocks two sequential `_send_prompt` calls and asserts the second call's typed value does NOT include the first.

## 6. Data flow

```
[orchestrator]                [transport]                          [Flow editor]
                                                                   gallery
generate_images_batch ──────► generate_images_batch
                              │
                              ├── _enter_editor ───────────────────► click "+ New project"
                              │                                  ◄── editor mounted, URL carries project_id
                              ├── _dismiss_blocking_overlays
                              │
                              ├── for prompt in prompts:
                              │     ├── _configure_generation_settings(aspect, count)
                              │     ├── _attach_batch_response_listener
                              │     │   (captures into list[i], filters by project_id)
                              │     ├── _send_prompt ─────────────► type + click submit
                              │     │                              ◄── Flow begins generation (async)
                              │     ├── pending.append(idx, list[i], expected_count)
                              │     └── if not last: asyncio.sleep(uniform(*jitter_range))
                              │
                              ├── for pending in pending_submissions:  (submission order)
                              │     ├── _await_captured ──────────► poll until len(list[i])==expected
                              │     │                              ◄── responses with image URLs arrive
                              │     └── _images_from_responses → tuple[GeneratedImage,...]
                              │
                              └── return list[BatchSubmissionResult]
                                  ◄────────────────────────────
for result in results:
  if result.status == "ok":
    for image in result.images: client.download_image(image, target)
  else:
    record fail outcome with result.error
```

### Listener-per-prompt rationale

Each prompt's listener is attached just before its `_send_prompt` call. Playwright supports multiple concurrent `page.on("response", ...)` handlers. Each handler captures into its own list. Responses for prompt N arrive *after* the click that sends prompt N, so each list naturally collects only its own prompt's responses (filtered by post-attach time + project_id). Detach happens when `_await_captured` returns or the per-prompt timeout fires.

This avoids any "demultiplex one shared list into per-prompt buckets" logic and avoids relying on response payload identifiers (which we don't currently extract).

## 7. Error handling

**Default: `continue_on_error=False` — fail-fast on first per-prompt failure** (Council Finding A3). Silent partial failures with a success exit code are dangerous: a 10-prompt batch where prompts 3-10 all fail to transient auth issues should NOT exit 0 with 2 images delivered. The CLI surfaces `--continue-on-error` as an explicit opt-in for the tolerant mode.

**Partial-results salvage on fail-fast (Council Finding O1):** when `continue_on_error=False` triggers, prompts 0..N-1 have already been submitted to Flow — credits are spent regardless of whether the CLI raises. Before re-raising, `generate_images_batch` MUST drain any captures that have already completed (i.e., for each prior pending submission, briefly poll the captured list and produce a `BatchSubmissionResult` if `len(captured) >= expected_count`) and attach them to the raised exception. The exception type is a new `BatchPartialError(GFlowError)` carrying `partial_results: tuple[BatchSubmissionResult, ...]` plus the original `cause: Exception`. The orchestrator unpacks `partial_results` and downloads everything inside before re-surfacing the error to the CLI, so the user keeps the images their credits paid for. Listeners for prompts not yet awaited are detached (no salvage attempt — those captures may be empty or incomplete).

Three failure layers:

| Layer | Symptoms | Behaviour |
|---|---|---|
| Per-prompt submit | `_configure_generation_settings` raises, `_send_prompt` times out clicking, listener attach fails | This prompt: `BatchSubmissionResult(status="fail", project_id=…, images=(), error=<exc>)`. **If `continue_on_error=False`**: transport detaches all in-flight listeners and re-raises immediately. **If `True`**: subsequent prompts continue. |
| Per-prompt await | `_await_captured` times out (returns partial list shorter than `expected_count`), non-2xx batchGenerateImages response, content-policy block (200 with no images) | Same shape: `status="fail"`. `_await_captured` returns partial — the caller in `generate_images_batch` MUST check `len(captured) < expected` and promote to `status="fail"` itself (Council Finding P3 nitpick). Continue-on-error gating as above. |
| Batch setup | Browser launch fails, `_enter_editor` fails, `_dismiss_blocking_overlays` fails | Fatal. Raises before any outcomes returned. No `continue_on_error` gating. **If `_enter_editor` succeeded and the project URL was reachable BEFORE a later setup step (e.g. `_dismiss_blocking_overlays`) raises**, the transport MUST emit a `ui_automation.orphaned_project_warning` structlog event bound with the extracted `project_id` and the URL before re-raising, so the user can find their server-side project record (Council Finding O3). The Flow project itself is not deleted; only the local CLI loses track of it. |

Cleanup invariant: under both `continue_on_error` values, every listener that was attached MUST have its `detach_fn()` invoked exactly once before `generate_images_batch` returns or raises. Implemented via a `try/finally` that walks the pending-submissions list and calls each `detach_fn` regardless of completion path.

Timeouts: per-prompt `_await_captured` reuses its existing default. No new knob.

## 8. Testing

### 8.1 Unit — `tests/api/test_ui_automation_batch.py` (new)

Mock `Page` and existing helper methods on the transport instance. Cover:

- `_enter_editor` called exactly once for a batch of N prompts (the bug-fix invariant).
- `_dismiss_blocking_overlays` called exactly once.
- `_configure_generation_settings`, `_attach_batch_response_listener`, `_send_prompt` called N times each, in order.
- Jitter sleep called N-1 times (between prompts), not before the first or after the last.
- Jitter delays drawn from `random.uniform(*jitter_range)` — use DI for `random.uniform` and `asyncio.sleep` per `e2e-tests-parameterize` memory so the test can assert exact call args and bypass the actual sleep.
- Pending-submission ordering: results returned in submission order regardless of mocked completion order. Mock setup uses per-index coroutines on `_await_captured` that resolve out of order (e.g., prompt 2 first, then prompt 0, then prompt 1); the returned list must still be `[result_0, result_1, result_2]`.
- **Multi-listener concurrency invariant (Council Finding T1, highest-stakes test):** attach two real listeners on a mocked Page (or stub Page), fire mocked response events that arrive in interleaved order (prompt-0-response, prompt-1-response, prompt-0-response again), and assert each listener's `captured` list contains exactly its own prompt's responses with no cross-contamination. This test exercises the post-attach-time filter and the project_id filter together.
- **Listener detach invariant:** every attached listener has its `detach_fn` invoked exactly once before the method returns or raises, on both happy-path and error-path. Verified by attaching mocked listeners and counting detach calls in a `try/finally`-style fixture.
- **Continue-on-error coverage (Council Finding T2):**
  - one prompt's mocked `_send_prompt` raise → that result has `status="fail"`, others `status="ok"` (with `continue_on_error=True`)
  - one prompt's mocked `_configure_generation_settings` raise *after* its listener has been attached → that prompt's `detach_fn` is invoked before the loop continues (no dangling listener)
  - one prompt's mocked `_await_captured` returns partial list (timeout) → result `status="fail"` with `TimeoutError`, others continue
  - `continue_on_error=False`: first per-prompt failure raises after detaching all in-flight listeners; subsequent prompts never submitted
- Batch-setup failure (mocked `_enter_editor` raise) → propagates, no results returned, no listener leaks (vacuously true since none attached).
- `BatchSubmissionResult.project_id` is populated from `_extract_project_id(page.url)` after `_enter_editor` and is identical across all results in a batch (assert `len({r.project_id for r in results}) == 1`).

### 8.2 Unit — `tests/image_batch/test_run_manifest_image_batch.py` (refactored)

Mock the `FlowApiClient` so that `client.transport` is a stub `UiAutomationTransport`-shaped mock (Council Finding M1 — the orchestrator calls `client.transport.generate_images_batch` directly per §5.3, NOT `client.generate_images_batch`). Cover:

- Capability check: orchestrator raises a clear error when `client.transport` is not a `UiAutomationTransport`.
- Orchestrator calls `client.transport.generate_images_batch` exactly once (not per prompt).
- Download loop invokes `client.download_image` once per image of each `ok` result.
- `BatchOutcome.status` mirrors `BatchSubmissionResult.status` per prompt; `BatchOutcome` carries the `project_id` (or equivalent telemetry) per prompt for downstream debugging.
- `continue_on_error=False`: when `generate_images_batch` raises `BatchPartialError`, the orchestrator still downloads every `BatchSubmissionResult` in `error.partial_results` BEFORE re-raising (Council Finding O1).
- `continue_on_error=True`: returns all outcomes including fails.
- `image_batch.submission_attempt`, `row_completed`, `inter_submission_latency_ms`, `submission_result` events fire at the same logical points as today, AND every per-prompt event carries `prompt_idx: int` and `prompt_hash: str` (Council Finding O2).
- **Post-download integrity check (Council Finding O2 nitpick):** the orchestrator asserts `len(downloaded_files) == sum(p.count for p in prompts if status=="ok")`. If the count mismatches, raises `BatchIntegrityError(prompt_indices=[...])`. This catches the rare listener cross-contamination scenario where the transport reports `status="ok"` but the file count is wrong.

### 8.3 Live e2e — `tests/e2e/test_image_batch_e2e.py` (refactored)

One credit-spending run gated by `GFLOW_CLI_E2E_PROFILE`. Drop `GFLOW_CLI_E2E_BATCH_SAME_PROJECT` (same-project is now the only mode). Keep `GFLOW_CLI_E2E_BATCH_JITTER` (lets tests opt into zero-jitter for faster/cheaper runs without changing production defaults). Asserts:

- **Primary fix verification (structural, via `BatchSubmissionResult`):** all returned results share one `project_id`. Test asserts `len({r.project_id for r in results}) == 1`. This survives any structlog instrumentation change because it reads `BatchSubmissionResult.project_id` directly (Council Finding T3).
- **Secondary fix verification (structlog, kept for instrumentation regression detection):** all `image_batch.submission_attempt` structlog events also share one `project_id`. Test asserts `len({e["project_id"] for e in attempt_events}) == 1`.
- `len(image_files) == sum(p.count for p in prompts)`.
- Magic-byte and aspect-ratio checks per existing pattern.
- `image_batch.row_completed` event count == sum(p.count for p in prompts).
- `image_batch.submission_attempt` event count == len(prompts).
- `ui_automation.batch_response_seen` >= sum(p.count) (existing lower-bound assertion stays).

### 8.4 Coverage

Maintain CLAUDE.md floors: ≥80 % overall, ≥90 % on the new transport method. The new dataclass and client delegate are trivially covered by the unit tests above.

## 9. Risks

| Risk | Mitigation |
|---|---|
| **Multiple Playwright `page.on("response", ...)` listeners interfering with each other** (Council Finding P1). With all prompts in one project, the existing `project_id` substring filter no longer segregates between prompts — it only excludes responses from previously-visited projects. The only segregation between concurrent listeners is "responses arrive after their submission's click event", which is an *unverified ordering assumption* in v3-3. | **Primary mitigation:** dedicated unit test for two-listener interleaved-response scenarios (§8.1). **Secondary mitigation:** v3-3 implements the post-attach-time filter as documented in §5.6 — record `attach_time = time.monotonic()` per listener and skip responses received before it. **Fallback:** if the first e2e shows cross-contamination, switch to a single shared listener + post-process assignment by arrival timestamp vs each submission's recorded click timestamp. |
| **Listener detach not implemented in current code** (Council Finding P2 — promoted to first-class risk). `_attach_batch_response_listener` does not return a detach handle today; in the stay-mounted design N listeners accumulate. | Extended helper signature in §5.6 (`tuple[captured, detach_fn]`). Cleanup invariant enforced in `try/finally` per §7. Single-prompt callers update their call site to ignore the detach handle (harmless — page is closed shortly after). Unit-tested explicitly per §8.1 "Listener detach invariant". |
| **`_send_prompt` may not clear the editor text field between prompts** (Council Finding P3). If it uses Playwright `type()` without `fill()` (or without a prior `select_all + delete`), prompts will concatenate in the persistent editor. | **Verify before v3-3 lands** by reading the existing `_send_prompt` implementation in `ui_automation.py`. If it uses `type()`, the v3-3 commit converts it to `fill()`. Unit test added per §8.1 asserting the second sequential call's typed value does NOT include the first prompt's text. |
| **Editor state drift between prompts** (overlay re-appearing, scroll position, focus loss). | Re-emerging overlays: invoke `_dismiss_blocking_overlays` per prompt if the first live e2e shows recurrence. Focus loss: reverify by re-clicking the prompt input before each `fill()`. Not built speculatively; first e2e reveals what's actually needed. |
| **One prompt's content-policy block leaving the editor in a "blocked" state** that breaks subsequent prompts. | First e2e run will reveal this. If a content-policy outcome corrupts subsequent prompts, fall back to a one-time editor reset (refresh the project URL) before continuing. Not built speculatively. |
| **Jitter sleep on a stale Page** (page navigated/closed during sleep). | Sleep is just `asyncio.sleep`; no Page interaction during sleep. If Page state is lost, the next `_send_prompt` will fail loudly and surface as that prompt's outcome. |
| **Same-project quota / rate limit** kicking in mid-batch on Flow's side. | Out of our control; jitter cadence is the only mitigation. Failures surface per prompt as `BatchSubmissionResult(status="fail")` when `continue_on_error=True`; with the safe default `False`, the first such failure stops the batch. Documented behaviour, not a regression. |
| **One credit-spending e2e run is statistically weak** for verifying the fix. | First run verifies the bug fix structurally (all `BatchSubmissionResult.project_id` values identical). Statistical confidence on Flow-side reliability is explicitly out of scope (no second matrix). If the first run fails the project-id assertion, that's a clear signal; if it passes, we accept the one-data-point evidence. |

## 10. Open questions

- **Should the e2e manifest stay at 3 rows × 4 images, or be simplified for the verification?** Recommendation: keep as-is. The current sample exercises count=1, count=2, and a non-default model — all useful coverage for the new path.
- **Does `_await_captured`'s existing timeout need extending for batch use?** Per-prompt timeout is unchanged, but a slow batch (3 prompts × 60 s each) is still within the existing timeout. Re-evaluate only if the live e2e hits a timeout on a normally-completing batch.
- **`_generate_lock` deadline:** the lock is held for the full batch (potentially many minutes). The current code has no outer deadline on the lock acquire. Should the batch carry its own wall-clock budget (e.g., `batch_timeout = sum(per_prompt_timeout for p in prompts) * 1.5`) that releases the lock and surfaces a partial-error if exceeded? Decided at v3-3 review.

## 11. Implementation commit chain

Per the v3 plan banner in `2026-05-21-multi-image-prompt.md`, the commit chain that lands this design:

1. `v3-1` — test assertion relaxation (already uncommitted on disk).
2. `v3-2` — evidence-file verdict retraction (already uncommitted on disk).
3. `v3-3` — stay-mounted refactor in `ui_automation.py` + `BatchSubmissionResult` (with `prompt_idx`/`prompt_hash`/`project_id`) + extended `_attach_batch_response_listener` (returns detach handle) + orchestrator capability-check wiring in `image_batch.py` + `BatchPartialError` + `BatchIntegrityError` + `ui_automation.orphaned_project_warning` event. **This commit lands the bug fix.** (Council Finding M3 — no client-layer wrapper is created.)
4. `v3-4` — drop `--same-project` flag + drop `same_project` param + simplify `run_manifest_image_batch`.
5. `v3-5` — update e2e test; drop `GFLOW_CLI_E2E_BATCH_SAME_PROJECT`; add shared-project-id assertion.
6. `v3-6` — docs: `USAGE.md`, `CHANGELOG.md`, `--help` text, public docstrings.
7. `v3-7` — record post-refactor live verification in `docs/LIVE_VERIFICATION_image_batch.md`.

Each commit independently passes `/gflow:check`. The live e2e in v3-7 is the only credit-spending step.

---

## Appendix: not-decided-here

- The exact internal layout of `PendingSubmission` (named tuple, dataclass, or just a 4-tuple of `idx, captured, detach_fn, expected_count`) is an implementation choice, not a design one.
- The post-attach-time filter has two viable shapes (see §5.6 "Open question"); v3-3 starts with the simpler submission-order-arrival assumption and adds the explicit timestamp filter only if the first live e2e shows cross-contamination.
