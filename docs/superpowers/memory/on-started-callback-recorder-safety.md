---
name: on-started-callback-recorder-safety
description: VideoStartedCallback runs INSIDE the transport — an uncaught exception aborts a paid Flow generation; wrap recorder calls in try/except DataStoreError
---

The `on_started: VideoStartedCallback` argument to `FlowApiClient.generate_video` is invoked by `ui_automation_video.generate_video` immediately after the generation response yields a `media_id`, BEFORE polling completes. An exception from `on_started` propagates out of `generate_video` and aborts the generation — but Flow may have already charged credits.

**Why:** Task 7's transport layer does NOT wrap the callback in try/except. The contract (per spec): data-layer failures must NOT block paid Flow operations. Post-success persistence failures should warn + continue, not propagate.

**How to apply:**
- Any caller passing an `on_started` callback MUST wrap the callback body in `try/except DataStoreError` that calls `_warn_persistence_failed_after_success(...)`. See `cli_video.py::_generate_and_report` for the canonical shape — even though `record_started_video` looks sync-safe, the wrap is non-negotiable.
- The callback can be sync OR async (`VideoStartedCallback = Callable[[VideoStarted], Awaitable[None] | None]`). Prefer sync unless you actually need to await — keeps it out of S7503's "async with no awaits" complaint.
- Reviewer note from Task 7 quality review: "on_started exceptions propagate and abort generation. Wire your recorder callback with an internal try/except or ensure it cannot raise."

Related: [[data-layer-overview]], [[exit-code-16-data-store]].
