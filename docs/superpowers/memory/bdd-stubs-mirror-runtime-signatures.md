---
name: bdd-stubs-mirror-runtime-signatures
description: pytest-bdd stubs in tests/features/ use TYPED signatures — adding a new kwarg to a _run_* runner breaks them with TypeError at CLI invocation
---

`tests/features/test_image_steps.py` (and likely the other `test_*_steps.py` files) monkeypatch CLI runners like `cli_image._run_t2i` with TYPED stubs:

```python
async def _fake_t2i(
    *,
    profile_dir: Path,
    headless: bool,
    req: Any,
    count: int,
    ...
) -> None: ...
```

If you add a new kwarg to the real runner (e.g., Task 5 added `profile_name: str`), the stub raises `TypeError: unexpected keyword argument 'profile_name'` when the Click callback invokes it. The structured logger only shows `exception_class: TypeError, message_hash: ...` — the actual TypeError message is suppressed, so the failure looks mysterious in CI.

**Why:** Forgotten in the Task 5 dispatch — the focused suite (`tests/cli/`) caught the new kwarg via FakeRecorder; BDD tests under `tests/features/` were out of the focused scope and only failed on full CI.

**How to apply:**
- When adding a kwarg to any `_run_*` runner in `cli_image.py` / `cli_video.py`, grep `tests/features/` for the runner name and update each typed stub to accept the new kwarg.
- Stubs using `async def _raise(*args: Any, **kwargs: Any)` are forgiving and won't break.
- Stubs in `tests/cli/test_error_handling.py` use `_make_raiser` which accepts `**kwargs` — also forgiving.
- If a CI structured-log assertion shows `exception_class: TypeError` and the focused suite passed, this is almost certainly the culprit.

Related: [[stale-test-discovery]], [[data-layer-overview]].
