# HAR Capture + Debug Traceback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two opt-in, env-var-driven debug knobs to gflow-cli — `GFLOW_CLI_HAR_PATH` (Playwright HAR network-traffic capture) and `GFLOW_CLI_DEBUG_TRACEBACK` (bypass the SHA-256 hash redaction on unhandled exceptions, in both console and `--json` output) — closing GitHub issue #316.

**Architecture:** Two independent `Settings` fields feed two independent code paths: `har_path` is consumed once, at the single production browser-launch site (`FlowApiClient._persistent_context_kwargs()`), plus a permission-hardening step at context close; `debug_traceback` is consumed at the two `_cli_helpers.py` exception-boundary call sites (console + `--json`), which already hold the live exception object and build their own raw output directly — `observability.emit_unhandled_event()` is untouched and keeps hashing unconditionally forever.

**Tech Stack:** Python 3.11+, pydantic-settings, Playwright (`record_har_path`), Click, structlog, Rich console, pytest.

## Global Constraints

- Python 3.11+ (repo `requires-python = ">=3.11"`) — `traceback.format_exception(exc)` single-arg form is valid.
- No new dependencies — everything here uses stdlib (`os`, `traceback`) or already-installed packages (Playwright, pydantic).
- Env vars are `GFLOW_CLI_*`-prefixed, resolved automatically by `Settings.model_config.env_prefix` — no manual aliasing needed.
- Type annotations on every new/modified signature (project convention, CLAUDE.md).
- Comments explain WHY, not WHAT — no restating obvious code.
- structlog event names follow the existing `<module>.<event>` dotted convention seen throughout `client.py` (e.g. `client.chrome_strategy_downgraded`).
- On Windows, run tests via `.venv/Scripts/python.exe -m pytest` (bare `uv run pytest` is broken in this environment per project convention) — scope pytest invocations to the specific test file/function being worked on, never the full suite (OOMs unscoped).
- Run `ruff check` / `ruff format` on every touched file before each commit (matches CI's `Lint` / `Format check` jobs).

---

### Task 1: `har_path` + `debug_traceback` Settings fields, HAR capture wiring

**Files:**
- Modify: `src/gflow_cli/config.py:452` (insert new `# --- debugging ---` section, right before the existing `# --- logging ---` section)
- Modify: `src/gflow_cli/api/client.py:333-370` (`_persistent_context_kwargs`) and `:678-707` (`_close_browser_resources`)
- Test: `tests/api/test_client_launch_kwargs.py`

**Interfaces:**
- Produces: `Settings.har_path: Path | None` (default `None`), `Settings.debug_traceback: bool` (default `False`) — consumed by Task 2 and Task 3.
- Produces: `FlowApiClient._persistent_context_kwargs()` includes `"record_har_path"` key when `self.settings.har_path` is set (unchanged shape otherwise) — no other task depends on this directly.

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_client_launch_kwargs.py`, right after `test_persistent_context_kwargs_are_unchanged` (after line 57):

```python
def test_persistent_context_kwargs_omits_har_path_when_unset(tmp_path: Path) -> None:
    """No GFLOW_CLI_HAR_PATH -> no record_har_path key at all (not just None)."""
    client = FlowApiClient(profile_dir=tmp_path, headless=True)
    kwargs = client._persistent_context_kwargs()  # noqa: SLF001
    assert "record_har_path" not in kwargs


def test_persistent_context_kwargs_includes_har_path_when_set(tmp_path: Path) -> None:
    """har_path set -> record_har_path passed through + parent dir created."""
    from gflow_cli.config import Settings

    har_path = tmp_path / "captures" / "session.har"
    settings = Settings(har_path=har_path)
    client = FlowApiClient(profile_dir=tmp_path, headless=True, settings=settings)
    kwargs = client._persistent_context_kwargs()  # noqa: SLF001
    assert kwargs["record_har_path"] == str(har_path)
    assert har_path.parent.is_dir()


@pytest.mark.asyncio
async def test_close_browser_resources_chmods_har_file(tmp_path: Path) -> None:
    """After context close, an existing HAR file is hardened to 0o600 on POSIX.

    Windows has no POSIX permission bits, so the assertion is skipped there —
    the chmod call itself is still exercised (must not raise on Windows).
    """
    import stat
    import sys

    from gflow_cli.config import Settings

    har_path = tmp_path / "session.har"
    har_path.write_bytes(b"{}")  # simulate Playwright having already written it
    settings = Settings(har_path=har_path)
    client = FlowApiClient(profile_dir=tmp_path, headless=True, settings=settings)
    # _close_browser_resources only enters its close-block (where the chmod
    # lives) when self._context is not None — a fresh client that never
    # entered __aenter__ has _context=None, so a fake context is required
    # here, matching the _client_with_cookies pattern used elsewhere in this
    # file (e.g. test_context_cookie_state_present_and_unexpired).
    client._context = MagicMock()  # noqa: SLF001
    with patch("gflow_cli.api.client.close_context_bounded", AsyncMock()):
        await client._close_browser_resources()  # noqa: SLF001
    if sys.platform != "win32":
        assert stat.S_IMODE(har_path.stat().st_mode) == 0o600
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/api/test_client_launch_kwargs.py -v -k "har_path or chmods_har"`
Expected: 3 FAIL — `Settings(har_path=...)` raises (unknown field), and `record_har_path` never appears in kwargs.

- [ ] **Step 3: Add the two Settings fields**

In `src/gflow_cli/config.py`, insert immediately before line 453 (`    # --- logging ----------------------------------------------------------`):

```python
    # --- debugging ---------------------------------------------------------
    har_path: Path | None = Field(
        default=None,
        description=(
            "When set, captures full Playwright network traffic (requests, "
            "responses, headers, cookies) to this HAR file for the session. "
            "SECURITY: HAR files can contain auth cookies and bearer tokens — "
            "never share one publicly; the file is chmod 0o600 on POSIX. "
            "Concurrent gflow processes pointed at the same path will overwrite "
            "each other's HAR (last-writer-wins) — use a distinct path per run. "
            "Override via GFLOW_CLI_HAR_PATH."
        ),
    )
    debug_traceback: bool = Field(
        default=False,
        description=(
            "Bypasses hash redaction for unhandled (non-GFlowError) exceptions: "
            "prints the real message + traceback to the console and, under "
            "--json, into the payload's error.traceback field, instead of "
            "SHA-256 hashes. SECURITY: may leak tokens/cookies present in "
            "exception text — for local debugging only. Never pipe --json "
            "output under this flag to a shared/persistent system (CI logs, "
            "log aggregators, webhooks) without redacting it first. "
            "Override via GFLOW_CLI_DEBUG_TRACEBACK."
        ),
    )
```

- [ ] **Step 4: Restructure `_persistent_context_kwargs` and add the chmod step**

In `src/gflow_cli/api/client.py`, replace the entire method — signature, docstring, and the `return { ... }` literal body (lines 333-370) — with:

```python
    def _persistent_context_kwargs(self) -> JsonObject:
        """Keyword arguments for the persistent browser-context launch.

        Extracted as an overridable seam so out-of-core tooling (e.g. a
        dev-scoped recording subclass that adds ``record_video_dir``) can
        augment the launch without any recording/test concern living in this
        core path. The returned dict is identical to the previous inline call,
        plus an optional ``record_har_path`` when ``GFLOW_CLI_HAR_PATH`` is set.
        """
        kwargs: JsonObject = {
            "user_data_dir": str(self.profile_dir),
            "headless": self.headless,
            "viewport": {"width": 1280, "height": 720},
            "locale": "en-US",
            "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
            "channel": channel_for_profile(self.profile_dir),
            "ignore_default_args": [
                "--enable-automation",
                "--no-sandbox",
            ],
            # Pass --password-store=basic EXPLICITLY (issue #222). auth login
            # (auth/real_chrome.py:69) and verification (auth/verification.py:246)
            # seal and read the profile's cookies with Chrome's *basic* store —
            # as does every other launch site in the codebase (auth/cookies.py,
            # auth/internal_chromium.py, browser_manager.py, ui_automation.py).
            # This shared generation context was the ONE path that omitted the
            # flag and merely relied on Playwright's internal default; on macOS
            # that let Chrome read cookies via the OS Keychain ("Chrome Safe
            # Storage"), which cannot decrypt the basic-sealed cookies -> a
            # logged-out context -> HTTP 401 at project.createProject. #225 added
            # a comment but never the flag here. Passing it explicitly keeps all
            # paths symmetric regardless of Playwright's defaults.
            "args": [
                "--password-store=basic",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        }
        if self.settings.har_path is not None:
            self.settings.har_path.parent.mkdir(parents=True, exist_ok=True)
            kwargs["record_har_path"] = str(self.settings.har_path)
            logger.warning(
                "client.har_capture_enabled",
                har_path=str(self.settings.har_path),
                hint="HAR file will contain full request/response bodies, headers, "
                "and cookies — do not share it publicly or attach it to a public "
                "bug report.",
            )
        return kwargs
```

- [ ] **Step 5: Add the post-close chmod hardening**

In `src/gflow_cli/api/client.py`, in `_close_browser_resources` (currently lines 678-707), change the context-close block from:

```python
        try:
            if self._context is not None:
                # Bounded close + force-close fallback (issue #293) — shared
                # with the transports' own-context teardowns via _engine.
                await close_context_bounded(self._context, owner="client")
```

to:

```python
        try:
            if self._context is not None:
                # Bounded close + force-close fallback (issue #293) — shared
                # with the transports' own-context teardowns via _engine.
                await close_context_bounded(self._context, owner="client")
                # HAR files hold live auth cookies/bearer tokens — higher
                # sensitivity than the CDP lockfile _write_lock already hardens
                # in browser_manager.py. Playwright writes the HAR lazily on
                # this close, so this is the earliest point the file exists;
                # best-effort only (never fail teardown over a permission tweak).
                if self.settings.har_path is not None:
                    try:
                        os.chmod(self.settings.har_path, 0o600)
                    except OSError:
                        logger.warning("client.har_chmod_failed", exc_info=True)
```

(the rest of the method — the `if self._pw is not None:` block and the `finally:` — is unchanged.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/api/test_client_launch_kwargs.py -v`
Expected: all PASS (including the 8 pre-existing tests in this file — confirms no regression).

- [ ] **Step 7: Lint + format**

Run: `.venv/Scripts/python.exe -m ruff check src/gflow_cli/config.py src/gflow_cli/api/client.py tests/api/test_client_launch_kwargs.py && .venv/Scripts/python.exe -m ruff format --check src/gflow_cli/config.py src/gflow_cli/api/client.py tests/api/test_client_launch_kwargs.py`
Expected: clean (no findings). If `ruff format` reports changes needed, run without `--check` to apply, then re-run tests.

- [ ] **Step 8: Commit**

```bash
git add src/gflow_cli/config.py src/gflow_cli/api/client.py tests/api/test_client_launch_kwargs.py
git commit -m "feat(config): add GFLOW_CLI_HAR_PATH capture + 0600 hardening (#316)"
```

---

### Task 2: `GFLOW_CLI_DEBUG_TRACEBACK` — console path

**Files:**
- Modify: `src/gflow_cli/_cli_helpers.py:257-266` (`_handle_unhandled_error`)
- Test: `tests/cli/test_error_handling.py`

**Interfaces:**
- Consumes: `Settings.debug_traceback: bool` (Task 1).
- Produces: no new public interface — `_handle_unhandled_error`'s console output changes based on the setting; its signature (`(exc: BaseException, *, cli_command: str) -> int`) is unchanged, so Task 3 and any other caller are unaffected.

- [ ] **Step 1: Write the failing test**

Add to `tests/cli/test_error_handling.py`, right after `test_cli_unhandled_exception_exits_1_and_emits_unhandled_event` (after line 218):

```python
def test_cli_unhandled_exception_debug_traceback_prints_real_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """GFLOW_CLI_DEBUG_TRACEBACK=1 -> console shows the real message + traceback,
    with a yellow leak-risk warning, instead of the generic 'Unexpected error.'"""
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_DEBUG_TRACEBACK", "1")
    reset_settings()

    _patch_profile_resolution(monkeypatch, tmp_path, "gflow_cli.cli_image")
    monkeypatch.setattr(
        "gflow_cli.cli_image._run_t2i",
        _make_raiser(ValueError("bad input")),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["image", "t2i", "test prompt"])
    assert result.exit_code == 1
    assert "bad input" in result.output
    assert "ValueError" in result.output
    assert "GFLOW_CLI_DEBUG_TRACEBACK" in result.output  # the leak-risk warning
    assert "Unexpected error." not in result.output


def test_cli_unhandled_exception_default_hides_real_error_from_console(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """Default (GFLOW_CLI_DEBUG_TRACEBACK unset) -> console never shows the raw
    message, and the hint now points at the real env var, not the misleading
    --verbose claim."""
    _patch_profile_resolution(monkeypatch, tmp_path, "gflow_cli.cli_image")
    monkeypatch.setattr(
        "gflow_cli.cli_image._run_t2i",
        _make_raiser(ValueError("bad input")),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["image", "t2i", "test prompt"])
    assert result.exit_code == 1
    assert "bad input" not in result.output
    assert "GFLOW_CLI_DEBUG_TRACEBACK=1" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_error_handling.py -v -k debug_traceback`
Expected: both FAIL — today's code always prints the generic message and mentions `--verbose`, never `GFLOW_CLI_DEBUG_TRACEBACK`.

- [ ] **Step 3: Implement**

Add near the top of `src/gflow_cli/_cli_helpers.py`, in the import block (after `import sys` at line 39):

```python
import traceback
```

Add to the `from gflow_cli...` import group (after the `from gflow_cli import auth as auth_mod` line, i.e. after line 47):

```python
from gflow_cli.config import get_settings
```

Replace `_handle_unhandled_error` (lines 257-266) with:

```python
def _handle_unhandled_error(exc: BaseException, *, cli_command: str) -> int:
    """Catch-all for non-:class:`GFlowError`. Privacy-safe by default: hashes
    message + stack, never prints the raw payload. Set
    GFLOW_CLI_DEBUG_TRACEBACK=1 to print the real exception + traceback
    instead (local debugging only — the output may contain tokens/cookies).
    Always returns exit code 1.
    """
    emit_unhandled_event(_logger, exc, cli_command=cli_command)
    if get_settings().debug_traceback:
        _console.print(
            "[yellow]GFLOW_CLI_DEBUG_TRACEBACK is set — the output below may "
            "contain tokens/cookies. Do not share it publicly.[/yellow]"
        )
        _console.print("".join(traceback.format_exception(exc)))
    else:
        _console.print(
            "[red]Unexpected error.[/red] Re-run with GFLOW_CLI_DEBUG_TRACEBACK=1 "
            "to see the real error. If this persists, file a bug at "
            "https://github.com/ffroliva/gflow-cli/issues.",
        )
    return 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_error_handling.py -v`
Expected: all PASS (including every pre-existing test in this file — confirms no regression to the GFlowError path or other exit codes).

- [ ] **Step 5: Lint + format**

Run: `.venv/Scripts/python.exe -m ruff check src/gflow_cli/_cli_helpers.py tests/cli/test_error_handling.py && .venv/Scripts/python.exe -m ruff format --check src/gflow_cli/_cli_helpers.py tests/cli/test_error_handling.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/gflow_cli/_cli_helpers.py tests/cli/test_error_handling.py
git commit -m "feat(cli): GFLOW_CLI_DEBUG_TRACEBACK console path (#316)"
```

---

### Task 3: `GFLOW_CLI_DEBUG_TRACEBACK` — `--json` path

**Files:**
- Modify: `src/gflow_cli/json_output.py:93-107` (`unexpected_payload`)
- Modify: `src/gflow_cli/_cli_helpers.py:316-321` (`run_with_handlers`'s `as_json` branch)
- Test: `tests/test_json_output.py`, `tests/cli/test_error_handling.py`

**Interfaces:**
- Consumes: `Settings.debug_traceback: bool` (Task 1); `get_settings` already imported in `_cli_helpers.py` (Task 2).
- Produces: `json_output.unexpected_payload(debug: BaseException | None = None) -> dict[str, Any]` — the default (`debug=None`) return shape is byte-identical to today's zero-arg call, so no other caller needs to change.

- [ ] **Step 1: Write the failing tests**

In `tests/test_json_output.py`, replace `test_unexpected_is_privacy_safe` (lines 142-148) with:

```python
    def test_unexpected_is_privacy_safe_by_default(self) -> None:
        payload = json_output.unexpected_payload()
        assert payload["error"]["class"] == "UnexpectedError"
        assert payload["error"]["retryable"] is False
        assert payload["error"]["exit_code"] == 1
        # The raw exception message/stack must never leak into the payload
        # unless the caller explicitly opts in via debug=.
        assert "detail" not in payload["error"]
        assert "traceback" not in payload["error"]

    def test_unexpected_with_debug_includes_detail_and_traceback(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError as exc:
            payload = json_output.unexpected_payload(debug=exc)
        assert payload["error"]["detail"] == "boom"
        assert "ValueError" in payload["error"]["traceback"]
        assert "boom" in payload["error"]["traceback"]
        # Non-debug fields stay identical to the default shape.
        assert payload["error"]["class"] == "UnexpectedError"
        assert payload["error"]["exit_code"] == 1
```

Add to `tests/cli/test_error_handling.py`, right after `test_cli_unhandled_exception_default_hides_real_error_from_console` (the test added in Task 2):

```python
def test_cli_json_unhandled_exception_debug_traceback_includes_raw_detail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """--json + GFLOW_CLI_DEBUG_TRACEBACK=1 -> the JSON payload's error.detail /
    error.traceback carry the real exception, same observability as console."""
    import json as json_mod

    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_DEBUG_TRACEBACK", "1")
    reset_settings()

    _patch_profile_resolution(monkeypatch, tmp_path, "gflow_cli.cli_image")
    monkeypatch.setattr(
        "gflow_cli.cli_image._run_t2i",
        _make_raiser(ValueError("bad input")),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["image", "t2i", "test prompt", "--json"])
    assert result.exit_code == 1
    payload = json_mod.loads(result.output)
    assert payload["error"]["detail"] == "bad input"
    assert "ValueError" in payload["error"]["traceback"]


def test_cli_json_unhandled_exception_default_stays_privacy_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """--json without the debug flag -> no detail/traceback fields, same as today."""
    import json as json_mod

    _patch_profile_resolution(monkeypatch, tmp_path, "gflow_cli.cli_image")
    monkeypatch.setattr(
        "gflow_cli.cli_image._run_t2i",
        _make_raiser(ValueError("bad input")),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["image", "t2i", "test prompt", "--json"])
    assert result.exit_code == 1
    payload = json_mod.loads(result.output)
    assert "detail" not in payload["error"]
    assert "traceback" not in payload["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_json_output.py tests/cli/test_error_handling.py -v -k "debug or unexpected_with"`
Expected: FAIL — `unexpected_payload()` doesn't accept a `debug` kwarg yet, so the JSON payload never carries `detail`/`traceback`.

- [ ] **Step 3: Implement**

Add to the top of `src/gflow_cli/json_output.py` (after `import json` at line 15):

```python
import traceback
```

Replace `unexpected_payload` (lines 93-107) with:

```python
def unexpected_payload(debug: BaseException | None = None) -> dict[str, Any]:
    """Privacy-safe payload for a non-:class:`GFlowError`, by default.

    Mirrors ``_handle_unhandled_error``: never leaks the raw message or stack —
    only the fact that an unclassified error occurred — unless ``debug`` is
    passed (the caller has already confirmed GFLOW_CLI_DEBUG_TRACEBACK=1), in
    which case ``detail``/``traceback`` carry the real exception. Always exit
    code 1.
    """
    error: dict[str, Any] = {
        "class": "UnexpectedError",
        "title": "Unexpected error",
        "exit_code": 1,
        "retryable": False,
    }
    if debug is not None:
        error["detail"] = str(debug)
        error["traceback"] = "".join(traceback.format_exception(debug))
    return {"status": "fail", "error": error}
```

In `src/gflow_cli/_cli_helpers.py`, replace the `as_json` branch inside `run_with_handlers`'s final `except BaseException as e:` block (lines 316-321):

```python
    except BaseException as e:
        if as_json:
            emit_unhandled_event(_logger, e, cli_command=cli_command)
            json_output.emit(json_output.unexpected_payload())
            sys.exit(1)
        sys.exit(_handle_unhandled_error(e, cli_command=cli_command))
```

with:

```python
    except BaseException as e:
        if as_json:
            emit_unhandled_event(_logger, e, cli_command=cli_command)
            debug_exc = e if get_settings().debug_traceback else None
            json_output.emit(json_output.unexpected_payload(debug=debug_exc))
            sys.exit(1)
        sys.exit(_handle_unhandled_error(e, cli_command=cli_command))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_json_output.py tests/cli/test_error_handling.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint + format**

Run: `.venv/Scripts/python.exe -m ruff check src/gflow_cli/json_output.py src/gflow_cli/_cli_helpers.py tests/test_json_output.py tests/cli/test_error_handling.py && .venv/Scripts/python.exe -m ruff format --check src/gflow_cli/json_output.py src/gflow_cli/_cli_helpers.py tests/test_json_output.py tests/cli/test_error_handling.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/gflow_cli/json_output.py src/gflow_cli/_cli_helpers.py tests/test_json_output.py tests/cli/test_error_handling.py
git commit -m "feat(json): GFLOW_CLI_DEBUG_TRACEBACK --json path (#316)"
```

---

### Task 4: Docs — `docs/CONFIGURATION.md`

**Files:**
- Modify: `docs/CONFIGURATION.md:262-264` (insert two new `###` entries between the end of `GFLOW_CLI_LOCALE` and the `## Output paths` heading)

**Interfaces:**
- Consumes: nothing from earlier tasks (pure documentation of the settings Task 1 already implemented).
- Produces: nothing consumed by other tasks — this is the terminal task.

- [ ] **Step 1: Insert the two entries**

In `docs/CONFIGURATION.md`, insert immediately after line 262 (the last line of the `GFLOW_CLI_LOCALE` entry) and before line 264 (`## Output paths`):

```markdown

### `GFLOW_CLI_HAR_PATH`

**What:** Captures full Playwright network traffic (requests, responses, headers, cookies) to a HAR file for the session — useful for diagnosing wire-format surprises or WAF rejections.
**Default:** unset (no capture).
**Override examples:**
```bash
export GFLOW_CLI_HAR_PATH=/tmp/gflow-debug/session.har       # POSIX
$env:GFLOW_CLI_HAR_PATH = "C:\gflow-debug\session.har"      # PowerShell
```

**SECURITY:** a HAR file contains live auth cookies and bearer tokens — never share one publicly. The file is chmod'd `0o600` on POSIX after Playwright writes it (best-effort; no-op on Windows). Two concurrent `gflow` processes pointed at the same path will overwrite each other's HAR (last-writer-wins, no error) — use a distinct path per run if running more than one profile/command at once.

### `GFLOW_CLI_DEBUG_TRACEBACK`

**What:** Bypasses this CLI's default privacy-safe hash redaction for unhandled (non-typed) errors — prints the real exception message + full traceback to the console, and under `--json`, into the payload's `error.detail` / `error.traceback` fields, instead of SHA-256 hashes.
**Values:** `true` | `false`
**Default:** `false`
**Override examples:**
```bash
GFLOW_CLI_DEBUG_TRACEBACK=1 gflow image t2i "a cat" --profile dev   # POSIX
$env:GFLOW_CLI_DEBUG_TRACEBACK = "1"                                # PowerShell
```

**SECURITY:** the real error text may contain tokens/cookies present in exception state — for local debugging only. `--json` output under this flag is a materially higher-risk surface than the interactive console: a human watches the console live and can react to the yellow warning, but `--json` output is designed to be piped into CI logs, log aggregators, and webhooks that persist or forward it unreviewed. **Never pipe `--json` output under this flag to a shared or persistent system without redacting it first.**
```

- [ ] **Step 2: Verify the doc-links checker still passes**

Run: `.venv/Scripts/python.exe scripts/ci/check_doc_links.py`
Expected: exits 0, no broken links reported (this change adds no links, but the checker also validates overall markdown structure — confirms the insertion didn't break heading nesting).

- [ ] **Step 3: Commit**

```bash
git add docs/CONFIGURATION.md
git commit -m "docs(config): document GFLOW_CLI_HAR_PATH + GFLOW_CLI_DEBUG_TRACEBACK (#316)"
```

---

## Post-plan cleanup (not a task — housekeeping only)

After all 4 tasks land and the PR is merged, delete `docs/superpowers/specs/2026-07-15-har-debug-traceback-design.md` and this plan file per `[[release-spec-plan-memory-consolidation]]`, and add the memory entry D5 flagged during council review: `FlowApiClient._persistent_context_kwargs()` (`api/client.py`) is the sole production browser-launch site — `UiAutomationTransport.setup()`'s standalone `launch_persistent_context` branch only fires when `page=None`, which happens only in dev/e2e scripts. Any future production browser-launch kwarg belongs in `_persistent_context_kwargs()`, not `ui_automation.py`.
