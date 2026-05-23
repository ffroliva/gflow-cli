# Video Download + T2V CLI Restoration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class video download (issue #29) and restore the `gflow video t2v` CLI command so `generate_video` saves the mp4 automatically.

**Architecture:** Add `VideoResult(status, local_path)` as the new return type of `generate_video`. The `VideoGenerationMixin._download_video` helper calls `page.request.get(routes.media_download_url(media_id))` — the authenticated page follows the 302 to GCS transparently. `FlowApiClient.download_video` wraps the existing `self.download()`. `_run_t2v` in `cli_video.py` uses `UiAutomationTransport` directly (proven by `tmp/fetch_video.py`).

**Tech Stack:** Python 3.11+, Playwright (page.request.get), `structlog`, Click, Rich, pytest

---

## File Map

| File | Change |
|---|---|
| `src/gflow_cli/api/video.py` | Add `VideoResult` frozen dataclass |
| `src/gflow_cli/api/transports/ui_automation_video.py` | Add `_download_video`; update `generate_video` + `_generate_video_locked` to return `VideoResult` and accept `download: bool = True` |
| `src/gflow_cli/api/client.py` | Add `download_video(media_id, out_path) -> Path` |
| `src/gflow_cli/cli_video.py` | Replace `_run_t2v` stub; add `--aspect`/`--profile`/`--out-dir` options to `t2v` command; make `PROMPT` required |
| `CHANGELOG.md` | Add `[Unreleased]` entries |
| `tests/api/test_video.py` | `VideoResult` unit tests (create if absent) |
| `tests/api/transports/test_ui_automation_video.py` | `_download_video` unit tests |
| `tests/api/test_client.py` | `download_video` unit test |
| `tests/cli/test_cli_video.py` | `t2v` command unit tests |

---

## Task 1: `VideoResult` dataclass

**Files:**
- Modify: `src/gflow_cli/api/video.py` (after `VideoStatus` class, ~line 110)
- Test: `tests/api/test_video.py`

- [ ] **Step 1: Write the failing test**

Check for existing `tests/api/test_video.py`; create it if absent.

```python
# tests/api/test_video.py
from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.video import VideoResult, VideoStatus


def test_video_result_holds_fields() -> None:
    status = VideoStatus(media_id="abc-123", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")
    result = VideoResult(status=status, local_path=Path("/tmp/abc-123.mp4"))
    assert result.status is status
    assert result.local_path == Path("/tmp/abc-123.mp4")


def test_video_result_no_path_when_failed() -> None:
    status = VideoStatus(media_id="abc-123", status="MEDIA_GENERATION_STATUS_FAILED")
    result = VideoResult(status=status, local_path=None)
    assert result.local_path is None
    assert not result.status.succeeded


def test_video_result_is_frozen() -> None:
    status = VideoStatus(media_id="x", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")
    result = VideoResult(status=status, local_path=None)
    with pytest.raises(Exception):  # FrozenInstanceError
        result.local_path = Path("/tmp/other.mp4")  # type: ignore[misc]
```

- [ ] **Step 2: Run to confirm RED**

```
uv run pytest tests/api/test_video.py -v
```
Expected: `ImportError: cannot import name 'VideoResult' from 'gflow_cli.api.video'`

- [ ] **Step 3: Add `VideoResult` to `src/gflow_cli/api/video.py`**

Append after the `VideoStatus` class (after the `parse_video_status` function is fine too — keep it near `VideoStatus`):

```python
@dataclass(frozen=True)
class VideoResult:
    """Return value of :meth:`generate_video` after Phase B download wiring.

    ``local_path`` is ``None`` when ``download=False`` was passed, or when
    the generation failed — callers should check ``status.succeeded`` first.
    """

    status: VideoStatus
    local_path: Path | None
```

`Path` is already imported at the top of the module.

- [ ] **Step 4: Run to confirm GREEN**

```
uv run pytest tests/api/test_video.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 5: Add `VideoResult` to public re-exports**

In `src/gflow_cli/api/video.py`, `VideoResult` will be imported by `ui_automation_video.py` and `client.py`. No `__all__` exists, so no further change needed — but verify it's importable:

```
uv run python -c "from gflow_cli.api.video import VideoResult; print('ok')"
```

- [ ] **Step 6: Commit**

```bash
git add src/gflow_cli/api/video.py tests/api/test_video.py
git commit -m "feat(video): add VideoResult dataclass (download path + status)"
```

---

## Task 2: `_download_video` transport helper

**Files:**
- Modify: `src/gflow_cli/api/transports/ui_automation_video.py`
- Test: `tests/api/transports/test_ui_automation_video.py`

The method belongs on `VideoGenerationMixin`. It uses `page.request.get()` — the authenticated Playwright page follows the `media.getMediaUrlRedirect` 302 to GCS automatically.

- [ ] **Step 1: Write the failing test**

Open `tests/api/transports/test_ui_automation_video.py`. Add:

```python
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_download_video_saves_mp4(tmp_path: Path) -> None:
    """_download_video writes response bytes to <out_dir>/<media_id>.mp4."""
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport

    transport = UiAutomationTransport()

    fake_page = MagicMock()
    fake_resp = AsyncMock()
    fake_resp.status = 200
    fake_resp.body = AsyncMock(return_value=b"fake-mp4-content")
    fake_page.request.get = AsyncMock(return_value=fake_resp)

    out_path = await transport._download_video("test-uuid-123", tmp_path, fake_page)

    assert out_path == tmp_path / "test-uuid-123.mp4"
    assert out_path.read_bytes() == b"fake-mp4-content"
    fake_page.request.get.assert_awaited_once()
    call_url = fake_page.request.get.call_args[0][0]
    assert "test-uuid-123" in call_url
    assert "getMediaUrlRedirect" in call_url


@pytest.mark.asyncio
async def test_download_video_raises_on_http_error(tmp_path: Path) -> None:
    """_download_video raises WireFormatError on non-2xx response."""
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport
    from gflow_cli.errors import WireFormatError

    transport = UiAutomationTransport()

    fake_page = MagicMock()
    fake_resp = AsyncMock()
    fake_resp.status = 403
    fake_page.request.get = AsyncMock(return_value=fake_resp)

    with pytest.raises(WireFormatError):
        await transport._download_video("test-uuid-456", tmp_path, fake_page)
```

- [ ] **Step 2: Run to confirm RED**

```
uv run pytest tests/api/transports/test_ui_automation_video.py::test_download_video_saves_mp4 tests/api/transports/test_ui_automation_video.py::test_download_video_raises_on_http_error -v
```
Expected: `AttributeError: '_download_video'`

- [ ] **Step 3: Add `_download_video` to `VideoGenerationMixin` in `ui_automation_video.py`**

First, ensure `routes` is imported at the top of `ui_automation_video.py`. Add if missing:
```python
from gflow_cli.api import routes
```

Also ensure `WireFormatError` is imported (check existing imports; add if absent):
```python
from gflow_cli.errors import ..., WireFormatError
```

Then add the method to `VideoGenerationMixin` (after `_poll_video_status`):

```python
async def _download_video(
    self,
    media_id: str,
    out_dir: Path | None,
    page: Any,
) -> Path:
    """Download a generated video to disk using the authenticated page.

    Calls ``media.getMediaUrlRedirect?name=<media_id>`` which 302s to a
    signed GCS URL; Playwright follows the redirect automatically.
    """
    url = routes.media_download_url(media_id)
    effective_dir = out_dir or self._out_dir or Path("tmp")
    effective_dir.mkdir(parents=True, exist_ok=True)
    out_path = effective_dir / f"{media_id}.mp4"
    resp = await page.request.get(url, max_redirects=5, timeout=180_000)
    if resp.status >= 400:
        raise WireFormatError(
            detail=(
                f"video download returned HTTP {resp.status} for {media_id!r} "
                f"via media.getMediaUrlRedirect"
            ),
            status=resp.status,
            route="media.getMediaUrlRedirect",
        )
    body = await resp.body()
    out_path.write_bytes(body)
    log.info(
        "ui_automation_video.video_saved",
        path=str(out_path),
        bytes=len(body),
        media_id=media_id,
    )
    return out_path
```

Note: `log` and `self._out_dir` are already accessible on `VideoGenerationMixin` (inherited from / mixed into `UiAutomationTransport`). `Any` for the `page` type is fine — same pattern as other methods in this file.

- [ ] **Step 4: Run to confirm GREEN**

```
uv run pytest tests/api/transports/test_ui_automation_video.py::test_download_video_saves_mp4 tests/api/transports/test_ui_automation_video.py::test_download_video_raises_on_http_error -v
```
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/transports/ui_automation_video.py tests/api/transports/test_ui_automation_video.py
git commit -m "feat(video): add _download_video helper to VideoGenerationMixin"
```

---

## Task 3: Update `generate_video` to return `VideoResult`

**Files:**
- Modify: `src/gflow_cli/api/transports/ui_automation_video.py`
- Test: `tests/api/transports/test_ui_automation_video.py`

This is a **breaking change**: `generate_video` and `_generate_video_locked` return `VideoResult` instead of `VideoStatus`. Add `download: bool = True` parameter.

- [ ] **Step 1: Write the failing test**

Add to `tests/api/transports/test_ui_automation_video.py`:

```python
@pytest.mark.asyncio
async def test_generate_video_returns_video_result_type() -> None:
    """generate_video raises NotImplementedError for I2V but the signature
    must declare VideoResult as return type — verify via import."""
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport
    from gflow_cli.api.video import GenerateVideoRequest, Mode, Aspect, VideoResult
    import inspect

    transport = UiAutomationTransport()
    hints = {}
    try:
        import typing
        hints = typing.get_type_hints(transport.generate_video)
    except Exception:
        pass

    # The return annotation must be VideoResult (may be a string forward ref
    # before Python evaluates it; accept both forms).
    ret = hints.get("return")
    assert ret is VideoResult or str(ret) == "VideoResult", (
        f"generate_video must return VideoResult, got {ret!r}"
    )
```

- [ ] **Step 2: Run to confirm RED**

```
uv run pytest tests/api/transports/test_ui_automation_video.py::test_generate_video_returns_video_result_type -v
```
Expected: FAIL (return hint is still `VideoStatus`).

- [ ] **Step 3: Update imports and signatures in `ui_automation_video.py`**

Add `VideoResult` to the import from `gflow_cli.api.video`:
```python
from gflow_cli.api.video import (
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoResult,   # ← ADD
    VideoStatus,
    media_name_from_generate_response,
    parse_video_status,
)
```

Update `generate_video` signature:
```python
async def generate_video(
    self,
    *,
    request: GenerateVideoRequest,
    out_dir: Path | None = None,
    poll_timeout_s: float = 600.0,
    download: bool = True,
) -> VideoResult:
```

Update `_generate_video_locked` signature:
```python
async def _generate_video_locked(
    self,
    request: GenerateVideoRequest,
    out_dir: Path | None,
    poll_timeout_s: float,
    download: bool,
) -> VideoResult:
```

Update the `generate_video` call to `_generate_video_locked`:
```python
async with self._generate_lock:
    return await self._generate_video_locked(request, out_dir, poll_timeout_s, download)
```

- [ ] **Step 4: Update the return logic in `_generate_video_locked`**

Find where `_generate_video_locked` currently returns the `VideoStatus` (after `_poll_video_status` returns). Replace the return statement:

**Before (approximate):**
```python
status = await VideoGenerationMixin._poll_video_status(
    captured_status, media_name=media_name, ...
)
return status
```

**After:**
```python
status = await VideoGenerationMixin._poll_video_status(
    captured_status, media_name=media_name, ...
)
if download and status.succeeded:
    local_path = await self._download_video(status.media_id, out_dir, page)
    return VideoResult(status=status, local_path=local_path)
return VideoResult(status=status, local_path=None)
```

> **Important:** `page` is already in scope as a local variable in `_generate_video_locked` — the method starts with `page: Page = self._page`.

- [ ] **Step 5: Run the full video transport test suite**

```
uv run pytest tests/api/transports/test_ui_automation_video.py -v
```
Expected: all previously-passing tests still pass; new test passes.

If any test fails because it expects `VideoStatus` directly (e.g. `assert result.media_id == ...`), update those tests to use `result.status.media_id`.

- [ ] **Step 6: Run full quality gates**

```
uv run ruff check src tests && uv run pyright src
```
Fix any type errors. Common fix: callers that stored the return of `generate_video` in a variable typed `VideoStatus` must be re-typed to `VideoResult`.

- [ ] **Step 7: Commit**

```bash
git add src/gflow_cli/api/transports/ui_automation_video.py tests/api/transports/test_ui_automation_video.py
git commit -m "feat(video): generate_video returns VideoResult; add download=True param"
```

---

## Task 4: `FlowApiClient.download_video`

**Files:**
- Modify: `src/gflow_cli/api/client.py` (after `download_image`, ~line 560)
- Test: `tests/api/test_client.py`

- [ ] **Step 1: Write the failing test**

Open `tests/api/test_client.py`. Add:

```python
@pytest.mark.asyncio
async def test_download_video_delegates_to_download(tmp_path: Path) -> None:
    """download_video(media_id, out_path) delegates to self.download()."""
    from unittest.mock import AsyncMock, patch
    from gflow_cli.api.client import FlowApiClient

    out_path = tmp_path / "my_video.mp4"

    with patch.object(FlowApiClient, "download", new_callable=AsyncMock) as mock_dl:
        mock_dl.return_value = out_path
        client = object.__new__(FlowApiClient)  # bypass __init__
        result = await client.download_video("media-uuid-abc", out_path)

    mock_dl.assert_awaited_once_with("media-uuid-abc", out_path)
    assert result == out_path
```

- [ ] **Step 2: Run to confirm RED**

```
uv run pytest tests/api/test_client.py::test_download_video_delegates_to_download -v
```
Expected: `AttributeError: 'FlowApiClient' object has no attribute 'download_video'`

- [ ] **Step 3: Add `download_video` to `FlowApiClient` in `client.py`**

Add directly after the `download_image` method:

```python
async def download_video(self, media_id: str, out_path: Path) -> Path:
    """Download a generated video by media ID to disk.

    Wraps :meth:`download` — ``media.getMediaUrlRedirect`` is followed
    transparently; the response body (mp4) is written to ``out_path``.

    Args:
        media_id: The UUID returned in :attr:`VideoStatus.media_id`.
        out_path: Destination file path. Parent directories are created
            if missing.

    Returns:
        ``out_path`` for ergonomic chaining.
    """
    return await self.download(media_id, out_path)
```

- [ ] **Step 4: Run to confirm GREEN**

```
uv run pytest tests/api/test_client.py::test_download_video_delegates_to_download -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/client.py tests/api/test_client.py
git commit -m "feat(video): add FlowApiClient.download_video (mirrors download_image)"
```

---

## Task 5: `gflow video t2v` CLI restoration

**Files:**
- Modify: `src/gflow_cli/cli_video.py`
- Test: `tests/cli/test_cli_video.py`

Replace the `_run_t2v` stub and add proper options to the `t2v` command. Use `UiAutomationTransport` directly (same pattern as `tmp/fetch_video.py`).

- [ ] **Step 1: Write the failing tests**

Open (or create) `tests/cli/test_cli_video.py`. Add:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.cli_video import video


def _make_result(succeeded: bool, local_path: Path | None = None) -> MagicMock:
    """Build a fake VideoResult."""
    from gflow_cli.api.video import VideoResult, VideoStatus
    status = VideoStatus(
        media_id="test-uuid",
        status=(
            "MEDIA_GENERATION_STATUS_SUCCESSFUL"
            if succeeded
            else "MEDIA_GENERATION_STATUS_FAILED"
        ),
    )
    return VideoResult(status=status, local_path=local_path)


def test_t2v_requires_prompt() -> None:
    runner = CliRunner()
    result = runner.invoke(video, ["t2v"])
    assert result.exit_code != 0


def test_t2v_invokes_transport_and_prints_path(tmp_path: Path) -> None:
    runner = CliRunner()
    expected_path = tmp_path / "test-uuid.mp4"
    expected_path.touch()
    fake_result = _make_result(succeeded=True, local_path=expected_path)

    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video._run_t2v", new_callable=AsyncMock) as mock_run,
    ):
        mock_run.return_value = None
        result = runner.invoke(video, ["t2v", "a golden sunset"])

    assert result.exit_code == 0
    mock_run.assert_awaited_once()


def test_t2v_accepts_aspect_option(tmp_path: Path) -> None:
    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video._run_t2v", new_callable=AsyncMock),
    ):
        result = runner.invoke(video, ["t2v", "prompt", "--aspect", "16:9"])
    assert result.exit_code == 0


def test_t2v_rejects_invalid_aspect(tmp_path: Path) -> None:
    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
    ):
        result = runner.invoke(video, ["t2v", "prompt", "--aspect", "4:3"])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run to confirm RED**

```
uv run pytest tests/cli/test_cli_video.py -v
```
Expected: `test_t2v_requires_prompt` FAILS (current command has `required=False`); `test_t2v_accepts_aspect_option` FAILS (no `--aspect` option yet).

- [ ] **Step 3: Rewrite `cli_video.py`**

Replace the entire file content with:

```python
"""`gflow video` command group.

Phase B wires `t2v` to `UiAutomationTransport.generate_video` with
auto-download. `i2v` and `batch` remain stubbed pending Phase B I2V work.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click
from rich.console import Console

from gflow_cli._cli_helpers import (
    _make_provider_dir,
    _resolve_profile,
    run_with_handlers,
)
from gflow_cli.config import get_settings

console = Console()

_I2V_UNAVAILABLE = (
    "[yellow]`gflow video i2v` is not yet available.[/yellow]\n"
    "I2V on UiAutomationTransport lands in a later Phase B release."
)

_BATCH_UNAVAILABLE = (
    "[yellow]`gflow video batch` is not yet available.[/yellow]\n"
    "Batch video on UiAutomationTransport lands in a later Phase B release."
)


async def _run_t2v(
    *,
    profile_dir: Path,
    prompt: str,
    aspect: str,
    out_dir: Path | None,
) -> None:
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport
    from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode

    request = GenerateVideoRequest(
        prompt=prompt,
        mode=Mode.T2V,
        aspect=Aspect.from_cli(aspect),
    )
    transport = UiAutomationTransport()
    try:
        await transport.setup(profile_dir)
        console.print("[dim]Generating video — this takes ~2 minutes…[/dim]")
        result = await transport.generate_video(
            request=request,
            out_dir=out_dir,
            download=True,
        )
    finally:
        await transport.teardown()

    if not result.status.succeeded:
        reasons = (
            ", ".join(result.status.failure_reasons)
            or result.status.error_message
            or "unknown reason"
        )
        console.print(f"[red]Video generation failed:[/red] {reasons}")
        raise SystemExit(1)

    console.print(f"[bold green]Saved:[/bold green] {result.local_path}")


async def _run_i2v(**kwargs: Any) -> None:  # pragma: no cover
    console.print(_I2V_UNAVAILABLE)
    raise SystemExit(1)


async def _run_batch(**kwargs: Any) -> None:  # pragma: no cover
    console.print(_BATCH_UNAVAILABLE)
    raise SystemExit(1)


@click.group()
def video() -> None:
    """Generate and manage videos via Google Flow Veo."""


@video.command(
    "t2v",
    short_help="Generate a video from a text prompt.",
    help=(
        "Generate a video from a text prompt using Google Flow's Veo model.\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow video t2v "a golden sunset over mountains"\n'
        '  gflow video t2v "timelapse of a city" --aspect 16:9\n'
        '  gflow video t2v "portrait of a dancer" --out-dir ./videos\n'
    ),
)
@click.argument("prompt")
@click.option(
    "--aspect",
    default="9:16",
    show_default=True,
    type=click.Choice(["9:16", "16:9"]),
    help="Video aspect ratio (portrait 9:16 or landscape 16:9).",
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@click.option(
    "--out-dir",
    "out_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to save the generated mp4. Defaults to tmp/.",
)
def t2v(prompt: str, aspect: str, profile: str | None, out_dir: Path | None) -> None:
    """Generate a video from PROMPT."""
    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_t2v(
            profile_dir=provider_dir,
            prompt=prompt,
            aspect=aspect,
            out_dir=out_dir,
        ),
        cli_command="video t2v",
    )


@video.command("i2v")
@click.argument("image", required=False)
@click.argument("prompt", required=False)
def i2v(image: str | None, prompt: str | None) -> None:
    """Generate a video from a start image + prompt (not yet available)."""
    profile_name = _resolve_profile(None)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_i2v(image=image, prompt=prompt, provider_dir=provider_dir),
        cli_command="video i2v",
    )


@video.command("batch")
@click.argument("manifest", required=False)
def batch(manifest: str | None) -> None:
    """Run a manifest of video generations (not yet available)."""
    profile_name = _resolve_profile(None)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_batch(manifest=manifest, provider_dir=provider_dir),
        cli_command="video batch",
    )
```

- [ ] **Step 4: Run to confirm GREEN**

```
uv run pytest tests/cli/test_cli_video.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 5: Run full suite to check for regressions**

```
uv run pytest -q --cov=gflow_cli
```
Expected: no regressions; coverage ≥ 80% overall.

- [ ] **Step 6: Run quality gates**

```
uv run ruff check src tests && uv run ruff format --check src tests && uv run pyright src
```
Fix any issues before committing.

- [ ] **Step 7: Commit**

```bash
git add src/gflow_cli/cli_video.py tests/cli/test_cli_video.py
git commit -m "feat(cli): restore gflow video t2v with auto-download (#29)"
```

---

## Task 6: CHANGELOG + docs

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add entries to `[Unreleased]`**

In `CHANGELOG.md`, under `## [Unreleased]`, add:

```markdown
### Added
- `VideoResult` dataclass — return type of `generate_video`, carries `status` and `local_path` ([#29])
- `UiAutomationTransport._download_video` — downloads a generated mp4 via `media.getMediaUrlRedirect` using the authenticated page ([#29])
- `FlowApiClient.download_video(media_id, out_path)` — public API, mirrors `download_image` ([#29])
- `gflow video t2v PROMPT` restored — generates and downloads a video end-to-end; supports `--aspect`, `--profile`, `--out-dir` ([#29])

### Changed
- `generate_video` now accepts `download: bool = True` and returns `VideoResult` instead of `VideoStatus` — **breaking change for direct transport callers**
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): add video download + t2v CLI entries for #29"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** All AC from issue #29 covered — `_download_video` (transport-internal), `download_video` (public client), `generate_video` auto-downloads with opt-out, `gflow video t2v` end-to-end
- [x] **No placeholders:** All tasks have concrete code
- [x] **Type consistency:** `VideoResult` imported in Tasks 3, 4, 5; `VideoStatus` still available where needed; `Aspect.from_cli()` used in CLI (not a hand-rolled mapping)
- [x] **`download` param threaded:** `generate_video` → `_generate_video_locked` — both signatures updated in Task 3
- [x] **Error path:** FAILED `VideoStatus` returns `VideoResult(local_path=None)` — not an exception; CLI checks `result.status.succeeded`
- [x] **`_out_dir` fallback:** `_download_video` uses `out_dir or self._out_dir or Path("tmp")`
