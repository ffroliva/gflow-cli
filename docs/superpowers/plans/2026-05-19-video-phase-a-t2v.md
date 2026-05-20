# Video Phase A: T2V Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the 401-dead HTTP video path and deliver text-to-video (T2V) generation on `UiAutomationTransport` — pure `video.py` value objects + response parsers, plus a new `ui_automation_video.py` mixin that drives the Flow video editor, submits a prompt, and polls Flow's own status traffic to a terminal `VideoStatus`.

**Architecture:** Video generation mirrors `generate_images`: the transport drives the Flow editor UI and Flow's own JavaScript builds the request, sends it, and mints reCAPTCHA on submit (the transport never POSTs a generate body). The status endpoint returns HTTP 401 to `page.request.post`, so polling captures Flow's *own* `batchCheckAsyncVideoGenerationStatus` responses via a `page.on("response")` listener instead of issuing the POST. New video transport code lives in `src/gflow_cli/api/transports/ui_automation_video.py` (a mixin) because `ui_automation.py` is already over the 800-line cap.

**Tech Stack:** Python 3.11+, `pyright --strict`, Click + Rich, Playwright (async, `page.request` + `page.on`), `structlog`, `pytest` + `pytest-asyncio`. Build tooling: `uv`.

**Source spec:** `docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md` (rev 5). This plan implements spec §10.3 **Phase A** — §8 increments 1-7 and the §9 file list.

**Revision:** rev 3 — revised across LLM-council rounds (5 dimensions: correctness, completeness, compliance, robustness, security). **Round 1** (1 APPROVE / 4 APPROVE-WITH-CHANGES): the `_VideoHost` Protocol replaced by a pyright-clean host contract, response listeners returned with handles + removed in a `finally`, mid-stall `bring_to_front` polling, sanitized error `route` names, an editor-readiness gate, explicit 403/500/empty-media tests. **Round 2** (3 APPROVE / 2 APPROVE-WITH-CHANGES): `_poll_video_status` now distinguishes "Flow never polled" from "Flow stalled" in its warning + `TimeoutError`, the empty-media `WireFormatError` carries safe-keys-only `discovery=`, `_EDITOR_READY_ANCHOR` deduplicated to a module constant, stale `_VideoHost` wording and a stale `base.py` comment cleared. See "Deviations" for details.

---

## Prerequisites — READ FIRST

- **Uncommitted working-tree change.** `git status` shows `M tests/api/transports/test_ui_automation.py`. Commit or stash it **before** starting so each task's commit stays atomic. This plan does not modify that file.
- **Branch:** `chore/video-wire-captures` (PR #23). All Phase 0 work (the `scripts/smoke_video_editor.py` spike, the captures, spec rev 5) is already committed and pushed.
- **Verified selectors:** the video-editor selectors used here were confirmed against live Flow by the Phase 0 spike — see `scripts/smoke_video_editor.py` and spec §10.5. Selector strings are copied **verbatim** from that script where noted.
- **Capture fixtures** under `samples/captured/` are committed. Each file is a flat envelope — the response body the parsers consume lives under the top-level key **`response_body_parsed`** (tests must read that key, not the file root).
- **Quality gate** (run before every commit; CI runs the same — see `CLAUDE.md`):
  ```bash
  uv run python scripts/ci/check_repo_hygiene.py
  uv run ruff check src tests
  uv run ruff format --check src tests
  uv run pyright src
  uv run pytest -q --cov=gflow_cli
  ```
  Coverage floor: 80% overall, **90% on `api/`** (`video.py` and `ui_automation_video.py` must hit 90%).

## Decisions resolved before planning

Three spec ambiguities were resolved with the project owner; this plan follows the chosen answers:

1. **The 401-dead HTTP *status* path is retired too.** Spec §9 only names `client.generate_video` + `dto.VideoOperation`, but `client.get_video_status` + `dto.VideoStatus` are the same dead path (Phase 0 confirmed the status endpoint returns 401) and would otherwise collide with the new `video.py:VideoStatus` (§4.4). Task 3/4 remove `client.get_video_status`, `dto.VideoStatus`, and `tests/api/test_dto.py::TestVideoStatus`.
2. **The existing video BDD is deleted now, rebuilt in Phase B.** `tests/features/video.feature` + `test_video_steps.py` exercise the old HTTP `gflow video` commands; Task 1 removes them. Spec §8 makes a video BDD feature file a Phase B deliverable.
3. **All e2e is deferred to Phase B.** Phase A is unit-only (§8 increments 1-7 are all `unit`). The obsolete `tests/e2e/test_video_i2v_e2e.py` is **removed** (Task 2) — the §9-specified `xfail` cannot survive symbol removal; it would `ImportError` at collection.

## Deviations from the spec (intentional, with rationale)

- **`_attach_video_response_listener` filters by route substring only, not `project_id`** (spec §5.4 says "filtered by project_id"). The video generate routes are `video:batchAsyncGenerateVideoText` etc. — capture `02`'s URL carries **no** `/projects/{id}/` path segment (the project id is in the request body). A URL project-filter is therefore impossible; route-substring filtering is what the Phase 0 spike used and verified.
- **`_poll_video_status` takes a pre-attached captured list, not `(page, media_name, project_id)`** (spec §5.5's signature is internally inconsistent — it says "attach the listener *before* `_send_prompt`" yet also names `media_name`, which is only known *after* submit). The listener is split out into `_attach_status_response_listener(page)` (attached before submit); `_poll_video_status(page, captured, media_name, ...)` consumes its list afterward. `project_id` is dropped — `media_name` is the unique key and the status URL has no project path segment either. Both attach helpers return `(captured, handler)` so the orchestrator can `page.remove_listener` them in a `finally` — the Page is pooled and persistent, so an un-removed handler would leak across calls.
- **The `_VideoHost` Protocol (spec §5.0) is not used.** `pyright --strict` rejects an explicit `self: _VideoHost` annotation on a `VideoGenerationMixin` method, because the mixin class does not itself satisfy that Protocol (an explicit `self` type must be a supertype of the defining class). Instead `VideoGenerationMixin` declares the host contract on itself — bare attribute annotations (`_page`, `_setup_done`, `_generate_lock`) plus an `if TYPE_CHECKING` block stubbing `_enter_editor` / `_send_prompt`. These create no runtime members; the real values come from `UiAutomationTransport`. This is the standard pyright-clean mixin pattern and realizes §5.0's intent.
- **`MAX_REFERENCE_IMAGES = 3` is a flagged estimate.** Spec §4.2 says "planning must not hardcode a guess" and expected Phase 0 to resolve it — but §10.5 records Q6 as **not answered**. The constant must exist for `GenerateVideoRequest.__post_init__` to compile. Phase A is T2V-only, so the R2V branch is never exercised in production; the value is commented as a Phase B / spec §10.2 Q6 confirmation point.
- **`Aspect.SQUARE` rejection lives in `generate_video`**, per spec §4.3 / §10.5 Q5 ("`generate_video` must reject `Aspect.SQUARE`"), not in `__post_init__` (§4.2's `__post_init__` code has no SQUARE check — `Aspect` is shared with image generation, which *does* support square).
- **`GFLOW_CLI_VIDEO_POLL_TIMEOUT` env wiring is deferred to Phase B.** `generate_video` exposes a `poll_timeout_s` parameter (default 600.0); no production caller exists in Phase A (the CLI is stubbed), so reading the env var is premature — Phase B wires it when `cli_video.py` is rewired.
- **`scripts/smoke_e2e.py` is deleted whole**, not line-trimmed (spec §9 says "remove the video block"). The *entire* file is the retired HTTP T2V smoke; `scripts/smoke_video_editor.py` is its UI-drive successor.
- **`src/gflow_cli/api/routes.py` is left untouched.** `GENERATE_VIDEO` / `CHECK_VIDEO_STATUS` become unreferenced but are harmless documented URL constants; §9 does not list `routes.py`, and the transport filters by route-name substring, not by these constants. `tests/api/test_routes.py` is likewise unchanged.

## File Structure

| File | Phase A change |
|---|---|
| `src/gflow_cli/cli_video.py` | **Stubbed** — `t2v`/`i2v`/`batch` print "temporarily unavailable" + exit 1; all `_run_*`/`_poll_and_download` helpers removed (Phase B rewires to the UI transport). |
| `src/gflow_cli/api/video.py` | Remove HTTP builders (`build_generate_body`, `model_key`, wire constants); add `Mode.R2V`, validated `GenerateVideoRequest`, `MAX_REFERENCE_IMAGES`, `VideoStatus`, `parse_video_status`, `media_name_from_generate_response`. |
| `src/gflow_cli/api/client.py` | Remove `generate_video()` + `get_video_status()` and now-dead imports. |
| `src/gflow_cli/api/dto.py` | Remove `VideoOperation` and `VideoStatus`. |
| `src/gflow_cli/api/__init__.py` | Drop `VideoOperation` / `VideoStatus` from imports + `__all__`. |
| `src/gflow_cli/api/transports/ui_automation_video.py` | **New** — `VideoGenerationMixin`: typed host contract, listeners, polling, mode switching, `generate_video` (T2V). |
| `src/gflow_cli/api/transports/ui_automation.py` | One-line change — `UiAutomationTransport` inherits `VideoGenerationMixin`. |
| `tests/api/test_video.py` | Replace HTTP-body tests with value-object + parser tests vs captured JSON. |
| `tests/api/transports/test_ui_automation_video.py` | **New** — unit tests for the video mixin. |
| `tests/test_cli_video.py` | Rewritten to pin the stub behavior. |
| `tests/api/test_dto.py` | Remove `TestVideoStatus` + the `VideoStatus` import. |
| `tests/api/test_client_image.py` | Remove the one `generate_video` test. |
| **Deleted:** `tests/api/test_client_generate_video.py`, `tests/e2e/test_video_i2v_e2e.py`, `scripts/smoke_e2e.py`, `tests/features/video.feature`, `tests/features/test_video_steps.py`. | |
| `tests/features/conftest.py` | Remove the `generate_video` / `get_video_status` mock lines. |
| `PLAN.md`, `CHANGELOG.md`, `README.md` | Phase A entry / `[Unreleased]` note / video-usage section. |

---

## Task 1: Stub `cli_video.py`; rewrite `test_cli_video.py`; delete video BDD

Retires the 401-dead HTTP video CLI behind a clear message and removes the BDD scenarios that exercised it. Keeping the tree green here requires deleting the video BDD in the same task — those scenarios assert "one video file is created", which a stub cannot satisfy.

**Files:**
- Rewrite: `src/gflow_cli/cli_video.py`
- Rewrite: `tests/test_cli_video.py`
- Delete: `tests/features/video.feature`, `tests/features/test_video_steps.py`
- Modify: `tests/features/conftest.py`

- [ ] **Step 1: Replace `cli_video.py` with the stub**

Overwrite `src/gflow_cli/cli_video.py` entirely with:

```python
"""`gflow video` command group — temporarily stubbed.

The HTTP video-generation path (the aisandbox-pa `video:*` routes) returns
HTTP 401 and has been retired. Video generation is being rebuilt on
`UiAutomationTransport`: Phase A delivers the T2V transport; Phase B rewires
these commands to it. See
docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md.
"""

from __future__ import annotations

import click
from rich.console import Console

console = Console()

_UNAVAILABLE = (
    "[yellow]`gflow video` is temporarily unavailable.[/yellow]\n"
    "The HTTP video path returned HTTP 401 and was retired. Video generation "
    "is being rebuilt on the UI-automation transport; the `gflow video` "
    "commands return in a later release."
)


@click.group()
def video() -> None:
    """Generate and manage videos via Google Flow Veo (temporarily unavailable)."""


@video.command("t2v")
@click.argument("prompt", required=False)
def t2v(prompt: str | None) -> None:
    """Generate a video from a text prompt (temporarily unavailable)."""
    _ = prompt
    console.print(_UNAVAILABLE)
    raise SystemExit(1)


@video.command("i2v")
@click.argument("image", required=False)
@click.argument("prompt", required=False)
def i2v(image: str | None, prompt: str | None) -> None:
    """Generate a video from a start image + prompt (temporarily unavailable)."""
    _ = (image, prompt)
    console.print(_UNAVAILABLE)
    raise SystemExit(1)


@video.command("batch")
@click.argument("manifest", required=False)
def batch(manifest: str | None) -> None:
    """Run a manifest of video generations (temporarily unavailable)."""
    _ = manifest
    console.print(_UNAVAILABLE)
    raise SystemExit(1)
```

- [ ] **Step 2: Rewrite `tests/test_cli_video.py`**

Overwrite `tests/test_cli_video.py` entirely with:

```python
"""`gflow video` is stubbed in Phase A — these tests pin the stub behavior.

Phase B reintroduces real `video` command tests once `cli_video.py` is rewired
to the UI-automation transport.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from gflow_cli.cli_video import video


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestVideoStub:
    def test_t2v_reports_unavailable(self, runner: CliRunner) -> None:
        result = runner.invoke(video, ["t2v", "a prompt"])
        assert result.exit_code == 1
        assert "temporarily unavailable" in result.output

    def test_i2v_reports_unavailable(self, runner: CliRunner) -> None:
        result = runner.invoke(video, ["i2v", "img.png", "a prompt"])
        assert result.exit_code == 1
        assert "temporarily unavailable" in result.output

    def test_batch_reports_unavailable(self, runner: CliRunner) -> None:
        result = runner.invoke(video, ["batch", "manifest.tsv"])
        assert result.exit_code == 1
        assert "temporarily unavailable" in result.output
```

- [ ] **Step 3: Delete the video BDD files**

```bash
git rm tests/features/video.feature tests/features/test_video_steps.py
```

- [ ] **Step 4: Drop the video mocks from `tests/features/conftest.py`**

In `tests/features/conftest.py`, inside the `mock_flow_client` fixture, delete these two lines:

```python
    client.generate_video = AsyncMock()
    client.get_video_status = AsyncMock()
```

Leave the rest of the fixture (`create_project`, `upload_image`, `generate_image`, `generate_images_batch`, `download`, `download_image`) unchanged.

- [ ] **Step 5: Verify and commit**

Run:
```bash
uv run gflow video --help
uv run pytest -q tests/test_cli_video.py tests/features
uv run ruff check src tests && uv run pyright src
```
Expected: `gflow video --help` lists `t2v`/`i2v`/`batch`; the three stub tests pass; the BDD suite passes without the video scenarios; lint + types clean. (The `video` Click group object name is preserved, so `cli.py`'s registration of the group is unaffected.)

```bash
git add src/gflow_cli/cli_video.py tests/test_cli_video.py tests/features/conftest.py
git commit -m "refactor(video): stub the gflow video commands pending the UI transport"
```

---

## Task 2: Delete dead HTTP-video tests & scripts

Removes every remaining reference to `client.generate_video` so Task 3 can delete the method without breaking collection.

**Files:**
- Delete: `tests/api/test_client_generate_video.py`, `tests/e2e/test_video_i2v_e2e.py`, `scripts/smoke_e2e.py`
- Modify: `tests/api/test_client_image.py`

- [ ] **Step 1: Delete the dead test files and the HTTP smoke script**

```bash
git rm tests/api/test_client_generate_video.py tests/e2e/test_video_i2v_e2e.py scripts/smoke_e2e.py
```

`test_client_generate_video.py` tests the retired `generate_video`; `test_video_i2v_e2e.py` is the HTTP-transport video e2e (its CV1/CV2 call `generate_video`); `smoke_e2e.py`'s entire body is the retired HTTP T2V smoke.

- [ ] **Step 2: Remove the `generate_video` test from `test_client_image.py`**

In `tests/api/test_client_image.py`, delete the test `test_generate_video_recaptcha_token_re_minted_every_attempt` (around lines 668-712) in its entirety, including its `@pytest.mark.asyncio` decorator and the explanatory comment block above it (around line 649 — "The video route (generate_video) still uses page.request.post directly…"). Then confirm nothing else in the file references the symbol:


```bash
grep -n generate_video tests/api/test_client_image.py
```
Expected: no matches. If a helper was used *only* by the deleted test, delete that helper too.

- [ ] **Step 3: Verify and commit**

Run:
```bash
uv run pytest -q tests/api
```
Expected: green — no collection errors, no failures.

```bash
git add tests/api/test_client_image.py
git commit -m "test(video): drop tests and scripts for the retired HTTP video path"
```

---

## Task 3: Remove `generate_video` / `get_video_status` from `client.py`

**Files:**
- Modify: `src/gflow_cli/api/client.py`

- [ ] **Step 1: Delete the two methods**

In `src/gflow_cli/api/client.py`:
- Delete `async def get_video_status(...)` (the full method, around lines 457-464).
- Delete `async def generate_video(...)` (the full method, around lines 561-621, ending at `return VideoOperation.from_generate_response(data)`).

- [ ] **Step 2: Fix the imports and module docstring**

In `client.py`:
- Change the dto import from
  `from gflow_cli.api.dto import AssetInfo, GeneratedImage, ProjectInfo, VideoOperation, VideoStatus`
  to
  `from gflow_cli.api.dto import AssetInfo, GeneratedImage, ProjectInfo`
- Delete the line `from gflow_cli.api.video import GenerateVideoRequest, build_generate_body` entirely (no other code in `client.py` uses either symbol).
- In the module docstring, delete the sentence fragment that mentions `generate_video()` ("The video-generation route requires a fresh reCAPTCHA token per call; that piece lives in `gflow_cli.api.recaptcha` and `generate_video()`…"). Replace that paragraph with a single sentence: `All HTTP goes through page.request so Google's session cookies attach automatically.`
- In `src/gflow_cli/api/recaptcha.py`, the module docstring (around line 5) says reCAPTCHA tokens are "minted per `generate_video()` call" — change `generate_video()` to `generate_image()` (image generation is the surviving reCAPTCHA caller).

- [ ] **Step 3: Remove any now-orphaned imports**

Run:
```bash
uv run ruff check src/gflow_cli/api/client.py
```
Ruff (`F401`) flags any import left unused by the deletions. Delete exactly the imports Ruff reports — do not guess. (Note: `secrets` is **not** a candidate — `secrets.randbelow` is still used by `generate_image` / `generate_images_batch`; leave it.) Re-run until clean. Also fix the stale comment around the original line ~893 ("`generate_video`, `_drive_image_generation`…") to drop the `generate_video` mention.

- [ ] **Step 4: Verify and commit**

Run:
```bash
uv run ruff check src tests && uv run pyright src
uv run pytest -q tests/api
```
Expected: green.

```bash
git add src/gflow_cli/api/client.py
git commit -m "refactor(api): remove the 401-dead generate_video and get_video_status methods"
```

---

## Task 4: Remove `VideoOperation` / `VideoStatus` from `dto.py`

**Files:**
- Modify: `src/gflow_cli/api/dto.py`
- Modify: `src/gflow_cli/api/__init__.py`
- Modify: `tests/api/test_dto.py`

- [ ] **Step 1: Delete the two DTOs**

In `src/gflow_cli/api/dto.py`, delete the entire `@dataclass(frozen=True) class VideoStatus:` block (around lines 168-201) and the entire `@dataclass(frozen=True) class VideoOperation:` block (around lines 204-232). Leave `ProjectInfo`, `AssetInfo`, `UploadedImage`, `GeneratedImage` untouched.

- [ ] **Step 2: Update `api/__init__.py`**

Overwrite `src/gflow_cli/api/__init__.py` entirely with:

```python
"""Low-level REST client for Flow's private aisandbox-pa API."""

from gflow_cli.api.client import FlowApiClient, FlowApiError
from gflow_cli.api.dto import AssetInfo, ProjectInfo
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, Tier

__all__ = [
    "Aspect",
    "AssetInfo",
    "FlowApiClient",
    "FlowApiError",
    "GenerateVideoRequest",
    "Mode",
    "ProjectInfo",
    "Tier",
]
```

- [ ] **Step 3: Update `tests/api/test_dto.py`**

In `tests/api/test_dto.py`:
- Change the import `from gflow_cli.api.dto import AssetInfo, ProjectInfo, VideoStatus` to `from gflow_cli.api.dto import AssetInfo, ProjectInfo`.
- Delete the entire `class TestVideoStatus:` block (around lines 57-92).

- [ ] **Step 4: Verify and commit**

Run:
```bash
uv run ruff check src tests && uv run pyright src
uv run pytest -q tests/api
```
Expected: green. (`tests/api/test_routes.py` still passes — `routes.GENERATE_VIDEO` / `CHECK_VIDEO_STATUS` are intentionally retained.)

```bash
git add src/gflow_cli/api/dto.py src/gflow_cli/api/__init__.py tests/api/test_dto.py
git commit -m "refactor(api): remove the VideoOperation and VideoStatus DTOs"
```

---

## Task 5: Remove HTTP body builders from `video.py`; trim `test_video.py`

After this task `video.py` holds only the value objects (`Mode`, `Tier`, `Aspect`, the *old* `GenerateVideoRequest`) — the type swap happens in Task 6. This keeps increment 1 a self-contained green checkpoint (spec §8 increment 1).

**Files:**
- Modify: `src/gflow_cli/api/video.py`
- Modify: `tests/api/test_video.py`

- [ ] **Step 1: Reduce `video.py` to value objects only**

Overwrite `src/gflow_cli/api/video.py` entirely with:

```python
"""Value objects for video generation.

This module is pure — no I/O. The video transport drives Flow's editor UI;
Flow's own JavaScript builds and sends the generate request, so this module
no longer carries HTTP body builders (the 401-dead HTTP video path was
retired — see the Phase A plan).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Mode(StrEnum):
    T2V = "t2v"
    I2V = "i2v"


class Tier(StrEnum):
    FAST = "fast"
    QUALITY = "quality"


class Aspect(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    SQUARE = "square"

    def wire(self) -> str:
        return f"VIDEO_ASPECT_RATIO_{self.value.upper()}"

    @classmethod
    def from_cli(cls, value: str) -> Aspect:
        mapping = {"9:16": cls.PORTRAIT, "16:9": cls.LANDSCAPE, "1:1": cls.SQUARE}
        if value not in mapping:
            raise ValueError(f"Unsupported aspect ratio {value!r}; choose from {sorted(mapping)}")
        return mapping[value]


@dataclass(frozen=True)
class GenerateVideoRequest:
    """Inputs for ONE video generation. T2V if start_asset_uuid is None, else I2V.

    NOTE: replaced by an explicit-`mode`, validated value object in Phase A
    Task 6 — this shape is transitional.
    """

    prompt: str
    aspect: Aspect = Aspect.PORTRAIT
    tier: Tier = Tier.FAST
    start_asset_uuid: str | None = None

    @property
    def mode(self) -> Mode:
        return Mode.I2V if self.start_asset_uuid else Mode.T2V
```

- [ ] **Step 2: Trim `tests/api/test_video.py`**

Overwrite `tests/api/test_video.py` entirely with:

```python
"""Pure tests for video value objects."""

from __future__ import annotations

import pytest

from gflow_cli.api.video import Aspect


class TestAspectEnum:
    def test_portrait_wire_value(self) -> None:
        assert Aspect.PORTRAIT.wire() == "VIDEO_ASPECT_RATIO_PORTRAIT"

    def test_landscape_wire_value(self) -> None:
        assert Aspect.LANDSCAPE.wire() == "VIDEO_ASPECT_RATIO_LANDSCAPE"

    def test_square_wire_value(self) -> None:
        assert Aspect.SQUARE.wire() == "VIDEO_ASPECT_RATIO_SQUARE"

    def test_from_cli_value(self) -> None:
        assert Aspect.from_cli("9:16") == Aspect.PORTRAIT
        assert Aspect.from_cli("16:9") == Aspect.LANDSCAPE
        assert Aspect.from_cli("1:1") == Aspect.SQUARE

    def test_from_cli_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="3:2"):
            Aspect.from_cli("3:2")
```

(`TestModelKey` and `TestBuildGenerateBody` are dropped — the symbols they tested are gone. `TestAspectEnum` survives unchanged: `Aspect` keeps `wire()` / `from_cli()`.)

- [ ] **Step 3: Verify and commit**

Run:
```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run pyright src
uv run pytest -q
```
Expected: the **full** suite is green — increment 1 (retire the dead HTTP video path) is complete.

```bash
git add src/gflow_cli/api/video.py tests/api/test_video.py
git commit -m "refactor(video): remove the HTTP generate-body builders"
```

---

## Task 6: `video.py` — `Mode.R2V`, validated `GenerateVideoRequest`, `MAX_REFERENCE_IMAGES`

Increment 2 (spec §4.1, §4.2). Replaces the transitional `GenerateVideoRequest` with an explicit-`mode`, validated value object whose image inputs are local `Path`s.

**Files:**
- Modify: `src/gflow_cli/api/video.py`
- Modify: `tests/api/test_video.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_video.py` (and add `from pathlib import Path` + `GenerateVideoRequest, MAX_REFERENCE_IMAGES, Mode` to the imports):

```python
class TestMode:
    def test_has_r2v(self) -> None:
        assert Mode.R2V == "r2v"

    def test_three_modes(self) -> None:
        assert {m.value for m in Mode} == {"t2v", "i2v", "r2v"}


class TestGenerateVideoRequest:
    def test_t2v_defaults(self) -> None:
        req = GenerateVideoRequest(prompt="a calm forest at dawn")
        assert req.mode is Mode.T2V
        assert req.aspect is Aspect.PORTRAIT
        assert req.tier is Tier.FAST
        assert req.start_image is None
        assert req.reference_images == ()

    def test_empty_prompt_rejected(self) -> None:
        with pytest.raises(ValueError, match="prompt must not be empty"):
            GenerateVideoRequest(prompt="   ")

    def test_t2v_must_not_carry_image_inputs(self) -> None:
        with pytest.raises(ValueError, match="T2V request must not carry image inputs"):
            GenerateVideoRequest(prompt="x", mode=Mode.T2V, start_image=Path("a.png"))

    def test_i2v_requires_start_image(self) -> None:
        with pytest.raises(ValueError, match="I2V request requires start_image"):
            GenerateVideoRequest(prompt="x", mode=Mode.I2V)

    def test_i2v_accepts_start_and_optional_end(self) -> None:
        req = GenerateVideoRequest(
            prompt="x", mode=Mode.I2V, start_image=Path("a.png"), end_image=Path("b.png")
        )
        assert req.start_image == Path("a.png")
        assert req.end_image == Path("b.png")

    def test_i2v_must_not_carry_reference_images(self) -> None:
        with pytest.raises(ValueError, match="must not carry reference_images"):
            GenerateVideoRequest(
                prompt="x", mode=Mode.I2V, start_image=Path("a.png"),
                reference_images=(Path("r.png"),),
            )

    def test_r2v_requires_a_reference_image(self) -> None:
        with pytest.raises(ValueError, match="R2V request requires at least one"):
            GenerateVideoRequest(prompt="x", mode=Mode.R2V)

    def test_r2v_must_not_carry_start_end(self) -> None:
        with pytest.raises(ValueError, match="must not carry start/end"):
            GenerateVideoRequest(
                prompt="x", mode=Mode.R2V,
                reference_images=(Path("r.png"),), start_image=Path("a.png"),
            )

    def test_too_many_reference_images_rejected(self) -> None:
        too_many = tuple(Path(f"r{i}.png") for i in range(MAX_REFERENCE_IMAGES + 1))
        with pytest.raises(ValueError, match="at most"):
            GenerateVideoRequest(prompt="x", mode=Mode.R2V, reference_images=too_many)

    def test_seed_range_enforced(self) -> None:
        with pytest.raises(ValueError, match="seed out of range"):
            GenerateVideoRequest(prompt="x", seed=-1)
        GenerateVideoRequest(prompt="x", seed=0)  # boundary OK
        GenerateVideoRequest(prompt="x", seed=2**31 - 1)  # boundary OK

    def test_post_init_does_not_touch_the_filesystem(self) -> None:
        # Structural validation only — a non-existent path must NOT raise.
        GenerateVideoRequest(prompt="x", mode=Mode.I2V, start_image=Path("does/not/exist.png"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/api/test_video.py`
Expected: FAIL — `ImportError` on `GenerateVideoRequest, MAX_REFERENCE_IMAGES` (the new fields/constant do not exist yet).

- [ ] **Step 3: Replace the value-object section of `video.py`**

In `src/gflow_cli/api/video.py`: add `from pathlib import Path` under `from dataclasses import dataclass`; add `R2V = "r2v"` to `Mode`; and replace the entire `@dataclass(frozen=True) class GenerateVideoRequest:` block with:

```python
# Flow's R2V ("Elementos") reference-image slot cap. ESTIMATE — spec §10.2 Q6
# was NOT resolved by the Phase 0 spike (§10.5); Phase B confirms the real
# upper bound. R2V is not wired in Phase A, so this value is never exercised
# in production yet.
MAX_REFERENCE_IMAGES = 3


@dataclass(frozen=True)
class GenerateVideoRequest:
    """Inputs for ONE video generation. Mode is explicit; image inputs are
    local file paths the transport attaches through Flow's catalog UI.

    `__post_init__` validates STRUCTURE only — it does not check that image
    paths exist on disk (that is I/O; this module is pure). Path existence is
    validated by the transport at the boundary.
    """

    prompt: str
    mode: Mode = Mode.T2V
    aspect: Aspect = Aspect.PORTRAIT
    tier: Tier = Tier.FAST  # meaningful for T2V only — I2V/R2V model keys are fixed
    seed: int | None = None
    start_image: Path | None = None  # I2V
    end_image: Path | None = None  # I2V (optional)
    reference_images: tuple[Path, ...] = ()  # R2V

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        if self.mode is Mode.T2V and (
            self.start_image or self.end_image or self.reference_images
        ):
            raise ValueError("T2V request must not carry image inputs")
        if self.mode is Mode.I2V:
            if self.start_image is None:
                raise ValueError("I2V request requires start_image")
            if self.reference_images:
                raise ValueError("I2V request must not carry reference_images")
        if self.mode is Mode.R2V:
            if not self.reference_images:
                raise ValueError("R2V request requires at least one reference image")
            if self.start_image or self.end_image:
                raise ValueError("R2V request must not carry start/end images")
        if len(self.reference_images) > MAX_REFERENCE_IMAGES:
            raise ValueError(f"at most {MAX_REFERENCE_IMAGES} reference images")
        if self.seed is not None and not (0 <= self.seed <= 2**31 - 1):
            raise ValueError("seed out of range")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/api/test_video.py`
Expected: PASS — `TestAspectEnum`, `TestMode`, `TestGenerateVideoRequest` all green.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/video.py tests/api/test_video.py
git commit -m "feat(video): add R2V mode and the validated GenerateVideoRequest"
```

---

## Task 7: `video.py` — `VideoStatus` value object

Increment 2 continued (spec §4.4).

**Files:**
- Modify: `src/gflow_cli/api/video.py`
- Modify: `tests/api/test_video.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_video.py` (add `VideoStatus` to the `gflow_cli.api.video` import):

```python
class TestVideoStatus:
    def test_pending_is_not_terminal(self) -> None:
        s = VideoStatus(media_id="m", status="MEDIA_GENERATION_STATUS_PENDING")
        assert s.is_terminal is False
        assert s.succeeded is False

    def test_active_is_not_terminal(self) -> None:
        s = VideoStatus(media_id="m", status="MEDIA_GENERATION_STATUS_ACTIVE")
        assert s.is_terminal is False

    def test_successful_is_terminal_and_succeeded(self) -> None:
        s = VideoStatus(media_id="m", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")
        assert s.is_terminal is True
        assert s.succeeded is True

    def test_failed_is_terminal_not_succeeded(self) -> None:
        s = VideoStatus(
            media_id="m",
            status="MEDIA_GENERATION_STATUS_FAILED",
            failure_reasons=("IP_PROHIBITED",),
            error_message="PUBLIC_ERROR_IP_INPUT_IMAGE",
        )
        assert s.is_terminal is True
        assert s.succeeded is False
        assert s.failure_reasons == ("IP_PROHIBITED",)
        assert s.error_message == "PUBLIC_ERROR_IP_INPUT_IMAGE"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/api/test_video.py -k VideoStatus`
Expected: FAIL — `ImportError` on `VideoStatus`.

- [ ] **Step 3: Add `VideoStatus` to `video.py`**

Append to `src/gflow_cli/api/video.py`:

```python
@dataclass(frozen=True)
class VideoStatus:
    """Terminal-or-not status of one in-flight video generation."""

    media_id: str
    status: str  # a MEDIA_GENERATION_STATUS_* wire value
    failure_reasons: tuple[str, ...] = ()
    error_message: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            "MEDIA_GENERATION_STATUS_SUCCESSFUL",
            "MEDIA_GENERATION_STATUS_FAILED",
        }

    @property
    def succeeded(self) -> bool:
        return self.status == "MEDIA_GENERATION_STATUS_SUCCESSFUL"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/api/test_video.py -k VideoStatus`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/video.py tests/api/test_video.py
git commit -m "feat(video): add the VideoStatus value object"
```

---

## Task 8: `video.py` — `parse_video_status` + `media_name_from_generate_response`

Increment 3 (spec §4.4). Pure response parsers, tested against the committed captures. Capture files wrap the response body under `response_body_parsed`.

**Files:**
- Modify: `src/gflow_cli/api/video.py`
- Modify: `tests/api/test_video.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_video.py` (add `import json` and `media_name_from_generate_response, parse_video_status` to the imports):

```python
_CAPTURES = Path(__file__).parent.parent.parent / "samples" / "captured"


def _body(filename: str) -> dict:
    """Load the response body the parsers consume from a committed capture.

    NOTE: the capture sanitizer redacts media ids inconsistently — capture 02
    uses `<UUID>`, captures 08/09/10/11 use `<GENERATED_MEDIA_ID>`. The
    assertions below match each file's actual token; a re-sanitization that
    unifies them would require updating these expected values.
    """
    raw = json.loads((_CAPTURES / filename).read_text(encoding="utf-8"))
    return raw["response_body_parsed"]


class TestMediaNameFromGenerateResponse:
    def test_t2v_capture(self) -> None:
        name = media_name_from_generate_response(_body("02_batchAsyncGenerateVideoText.json"))
        assert name == "<UUID>"

    def test_i2v_capture(self) -> None:
        name = media_name_from_generate_response(
            _body("08_batchAsyncGenerateVideoStartAndEndImage.json")
        )
        assert name == "<GENERATED_MEDIA_ID>"

    def test_r2v_capture(self) -> None:
        name = media_name_from_generate_response(
            _body("09_batchAsyncGenerateVideoReferenceImages.json")
        )
        assert name == "<GENERATED_MEDIA_ID>"

    def test_missing_media_raises(self) -> None:
        with pytest.raises(ValueError, match="no media"):
            media_name_from_generate_response({"workflows": []})


class TestParseVideoStatus:
    def test_successful_capture(self) -> None:
        s = parse_video_status(
            _body("10_batchCheckAsyncVideoGenerationStatus_successful.json"),
            media_id="<GENERATED_MEDIA_ID>",
        )
        assert s.status == "MEDIA_GENERATION_STATUS_SUCCESSFUL"
        assert s.is_terminal is True
        assert s.succeeded is True
        assert s.failure_reasons == ()
        assert s.error_message is None

    def test_failed_capture(self) -> None:
        s = parse_video_status(
            _body("11_batchCheckAsyncVideoGenerationStatus_failed.json"),
            media_id="<GENERATED_MEDIA_ID>",
        )
        assert s.status == "MEDIA_GENERATION_STATUS_FAILED"
        assert s.is_terminal is True
        assert s.succeeded is False
        assert s.failure_reasons == ("IP_PROHIBITED",)
        assert s.error_message == "PUBLIC_ERROR_IP_INPUT_IMAGE"

    def test_media_id_not_in_response_raises(self) -> None:
        with pytest.raises(ValueError, match="not found"):
            parse_video_status(
                _body("10_batchCheckAsyncVideoGenerationStatus_successful.json"),
                media_id="no-such-id",
            )

    def test_malformed_status_raises(self) -> None:
        with pytest.raises(ValueError, match="mediaGenerationStatus"):
            parse_video_status({"media": [{"name": "m", "mediaMetadata": {}}]}, media_id="m")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/api/test_video.py -k "ParseVideoStatus or MediaName"`
Expected: FAIL — `ImportError` on `parse_video_status, media_name_from_generate_response`.

- [ ] **Step 3: Add the parsers to `video.py`**

Add `from typing import Any` to the imports of `src/gflow_cli/api/video.py`, then append:

```python
def media_name_from_generate_response(response_json: dict[str, Any]) -> str:
    """Return `media[0].name` from a batchAsyncGenerateVideo* response.

    Shapes: captures 02 (T2V), 08 (I2V), 09 (R2V). The T2V response also
    carries a top-level `operations[]`; this parser deliberately reads
    `media[0].name` (spec §2.4 — the candidate ids collapse to one uuid).
    """
    try:
        media = response_json["media"]
        return str(media[0]["name"])
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"generate response carries no media[0].name: {e}") from e


def parse_video_status(response_json: dict[str, Any], *, media_id: str) -> VideoStatus:
    """Parse one batchCheckAsyncVideoGenerationStatus response into a VideoStatus.

    Selects the `media[]` entry whose `name == media_id`, then reads
    `mediaMetadata.mediaStatus.{mediaGenerationStatus, failureReasons,
    error.message}`. Shapes: captures 10 (SUCCESSFUL), 11 (FAILED).
    Raises ValueError if `media_id` is absent or the status is malformed.
    """
    media = response_json.get("media")
    if not isinstance(media, list):
        raise ValueError("status response has no media[] array")
    for item in media:
        if not isinstance(item, dict) or item.get("name") != media_id:
            continue
        media_status = (item.get("mediaMetadata") or {}).get("mediaStatus") or {}
        status = media_status.get("mediaGenerationStatus")
        if not isinstance(status, str):
            raise ValueError(f"status entry for {media_id} has no mediaGenerationStatus")
        reasons = tuple(media_status.get("failureReasons") or ())
        error_message = (media_status.get("error") or {}).get("message")
        return VideoStatus(
            media_id=media_id,
            status=status,
            failure_reasons=reasons,
            error_message=error_message,
        )
    raise ValueError(f"media_id {media_id!r} not found in status response")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/api/test_video.py`
Expected: PASS — the full `test_video.py` is green (`api/` coverage of `video.py` should now be ≥90%).

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/video.py tests/api/test_video.py
git commit -m "feat(video): add pure response parsers for video status and media id"
```

---

## Task 9: New `ui_automation_video.py` — `_VideoHost` Protocol + `_attach_video_response_listener`

Increment 4 (spec §5.0, §5.4). Creates the video transport module with its typing scaffold and the generate-response listener, modeled on `UiAutomationTransport._attach_batch_response_listener` (`ui_automation.py:623-664`).

**Files:**
- Create: `src/gflow_cli/api/transports/ui_automation_video.py`
- Create: `tests/api/transports/test_ui_automation_video.py`

- [ ] **Step 1: Write the failing test**

Create `tests/api/transports/test_ui_automation_video.py`:

```python
"""Unit tests for the video-generation mixin (ui_automation_video.py)."""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin


def _make_listener_page() -> tuple[MagicMock, list]:
    """A fake page that records the handlers registered via page.on()."""
    page = MagicMock()
    handlers: list = []
    page.on = MagicMock(side_effect=lambda event, cb: handlers.append((event, cb)))
    page.remove_listener = MagicMock()
    return page, handlers


def _make_response(*, url: str, status: int = 200, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.url = url
    resp.status = status
    resp.json = AsyncMock(return_value=body if body is not None else {"media": []})
    return resp


_T2V_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText"


class TestAttachVideoResponseListener:
    @pytest.mark.asyncio
    async def test_captures_a_generate_route_response(self) -> None:
        page, handlers = _make_listener_page()
        captured, _handler = VideoGenerationMixin._attach_video_response_listener(page)
        assert handlers and handlers[0][0] == "response"
        await handlers[0][1](_make_response(url=_T2V_URL, body={"media": [{"name": "m"}]}))
        assert len(captured) == 1
        assert captured[0]["status"] == 200
        assert captured[0]["body"]["media"][0]["name"] == "m"

    @pytest.mark.asyncio
    async def test_ignores_unrelated_routes(self) -> None:
        page, handlers = _make_listener_page()
        captured, _handler = VideoGenerationMixin._attach_video_response_listener(page)
        await handlers[0][1](_make_response(url="https://example.com/other"))
        assert captured == []

    @pytest.mark.asyncio
    async def test_parse_failure_is_non_fatal(self) -> None:
        page, handlers = _make_listener_page()
        captured, _handler = VideoGenerationMixin._attach_video_response_listener(page)
        resp = _make_response(url=_T2V_URL)
        resp.json = AsyncMock(side_effect=ValueError("bad json"))
        await handlers[0][1](resp)  # must not raise
        assert captured == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/api/transports/test_ui_automation_video.py`
Expected: FAIL — `ModuleNotFoundError: gflow_cli.api.transports.ui_automation_video`.

- [ ] **Step 3: Create `ui_automation_video.py` with the scaffold + listener**

Create `src/gflow_cli/api/transports/ui_automation_video.py`:

```python
"""Video-generation methods for UiAutomationTransport.

Mixed into `UiAutomationTransport` via `VideoGenerationMixin` — kept in its own
module because `ui_automation.py` is already over the 800-line cap.

Video generation mirrors `generate_images`: the transport drives the Flow
editor UI and Flow's own JavaScript builds the request, sends it, and mints
reCAPTCHA on submit — the transport never POSTs a generate body. The status
endpoint returns HTTP 401 to `page.request.post`, so polling captures Flow's
own `batchCheckAsyncVideoGenerationStatus` responses instead of issuing the
POST (spec §5.5).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from gflow_cli.api.video import (
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoStatus,
    media_name_from_generate_response,
    parse_video_status,
)
from gflow_cli.errors import AuthExpiredError, WafRejectionError, WireFormatError

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

log = structlog.get_logger(__name__)

# The three mode-specific generate routes (spec §2.1). The listener filters on
# these substrings only — video generate URLs carry no /projects/{id}/ path
# segment, so a project-id URL filter is impossible (deviation from §5.4).
VIDEO_GENERATE_ROUTES = (
    "batchAsyncGenerateVideoText",
    "batchAsyncGenerateVideoStartAndEndImage",
    "batchAsyncGenerateVideoReferenceImages",
)
# Status-poll route — Flow's SPA polls this itself while a generation runs.
VIDEO_STATUS_ROUTE = "batchCheckAsyncVideoGenerationStatus"


async def _capture_debug_screenshot(page: Any, out_dir: Path | None, filename: str) -> Path | None:
    """Best-effort viewport screenshot for debugging. Duplicated from
    `ui_automation.py` to keep this module free of a circular import."""
    if out_dir is None:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    shot_path = out_dir / filename
    try:
        await page.screenshot(path=str(shot_path), full_page=False)
        log.warning(
            "ui_automation_video.debug_screenshot_may_contain_pii",
            path=str(shot_path),
            note="viewport may include the account avatar / email from the Google session",
        )
    except Exception as e:  # noqa: BLE001 — screenshot is best-effort
        log.debug("ui_automation_video.screenshot_capture_failed", error=str(e))
    return shot_path


class VideoGenerationMixin:
    """Video-generation methods mixed into `UiAutomationTransport`.

    The mixin depends on host state and helpers that `UiAutomationTransport`
    supplies; they are declared below as a TYPE-ONLY contract so
    `pyright --strict` resolves `self._page` / `self._enter_editor` etc. The
    bare annotations and `if TYPE_CHECKING` stubs create no runtime members —
    the real values come from `UiAutomationTransport.__init__` and its methods.
    This replaces a separate `_VideoHost` Protocol: pyright rejects an explicit
    `self: _VideoHost` annotation on a mixin method because `VideoGenerationMixin`
    itself does not satisfy that Protocol.
    """

    # --- host contract: supplied by UiAutomationTransport (type-only) ---
    _page: Page | None
    _setup_done: bool
    _generate_lock: asyncio.Lock

    if TYPE_CHECKING:

        async def _enter_editor(self, page: Page, out_dir: Path | None = None) -> None: ...
        async def _send_prompt(
            self, page: Page, prompt_text: str, out_dir: Path | None = None
        ) -> None: ...

    @staticmethod
    def _attach_video_response_listener(page: Page) -> tuple[list[dict[str, Any]], Any]:
        """Register a `page.on('response')` listener for the three
        batchAsyncGenerateVideo* routes (spec §2.1). Returns `(captured, handler)`
        — the caller awaits `captured` after submitting the prompt and MUST
        `page.remove_listener('response', handler)` in a `finally` (the Page is
        pooled and persistent; an un-removed handler leaks across calls).
        Registered synchronously before `_send_prompt` so a fast response is
        never missed.

        The captured `body` is kept for parsing only — it carries
        `remainingCredits` and media UUIDs and MUST NOT be logged.
        """
        captured: list[dict[str, Any]] = []

        async def on_response(response: Any) -> None:
            if not any(route in response.url for route in VIDEO_GENERATE_ROUTES):
                return
            try:
                body = await response.json()
            except Exception as e:  # noqa: BLE001 — parse failures are non-fatal
                log.warning("ui_automation_video.generate_parse_failed", error=str(e))
                return
            captured.append({"status": response.status, "url": response.url, "body": body})
            log.info(
                "ui_automation_video.generate_captured",
                status=response.status,
                url=response.url,
            )

        page.on("response", on_response)
        return captured, on_response
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest -q tests/api/transports/test_ui_automation_video.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/transports/ui_automation_video.py tests/api/transports/test_ui_automation_video.py
git commit -m "feat(video): add the video-generate response listener"
```

---

## Task 10: `ui_automation_video.py` — `_attach_status_response_listener` + `_poll_video_status`

Increment 5 (spec §5.5). Polling captures Flow's own status traffic; `_poll_video_status` consumes the captured list.

**Files:**
- Modify: `src/gflow_cli/api/transports/ui_automation_video.py`
- Modify: `tests/api/transports/test_ui_automation_video.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/transports/test_ui_automation_video.py`:

```python
_STATUS_URL = (
    "https://aisandbox-pa.googleapis.com/v1/video:batchCheckAsyncVideoGenerationStatus"
)


def _status_resp(media_id: str, status: str, *, failure_reasons: list | None = None) -> dict:
    """Build a captured-status dict shaped like Flow's check-status response."""
    media_status: dict = {"mediaGenerationStatus": status}
    if failure_reasons:
        media_status["failureReasons"] = failure_reasons
        media_status["error"] = {"message": "PUBLIC_ERROR_IP_INPUT_IMAGE"}
    body = {"media": [{"name": media_id, "mediaMetadata": {"mediaStatus": media_status}}]}
    return {"status": 200, "url": _STATUS_URL, "body": body}


class TestAttachStatusResponseListener:
    @pytest.mark.asyncio
    async def test_captures_status_route_only(self) -> None:
        page, handlers = _make_listener_page()
        captured, _handler = VideoGenerationMixin._attach_status_response_listener(page)
        await handlers[0][1](_make_response(url=_STATUS_URL, body={"media": []}))
        await handlers[0][1](_make_response(url=_T2V_URL, body={"media": []}))
        assert len(captured) == 1


class TestPollVideoStatus:
    @pytest.mark.asyncio
    async def test_returns_on_successful(self) -> None:
        page = MagicMock()
        captured = [
            _status_resp("m", "MEDIA_GENERATION_STATUS_SCHEDULED"),
            _status_resp("m", "MEDIA_GENERATION_STATUS_ACTIVE"),
            _status_resp("m", "MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        ]
        result = await VideoGenerationMixin._poll_video_status(
            page, captured, "m", timeout_s=2.0, poll_interval_s=0.05
        )
        assert result.succeeded is True

    @pytest.mark.asyncio
    async def test_returns_failed_status(self) -> None:
        page = MagicMock()
        captured = [
            _status_resp("m", "MEDIA_GENERATION_STATUS_FAILED", failure_reasons=["IP_PROHIBITED"])
        ]
        result = await VideoGenerationMixin._poll_video_status(
            page, captured, "m", timeout_s=2.0, poll_interval_s=0.05
        )
        assert result.is_terminal is True
        assert result.succeeded is False
        assert result.failure_reasons == ("IP_PROHIBITED",)

    @pytest.mark.asyncio
    async def test_waits_for_a_late_terminal_status(self) -> None:
        page = MagicMock()
        captured: list[dict] = [_status_resp("m", "MEDIA_GENERATION_STATUS_SCHEDULED")]

        async def _append_later() -> None:
            await asyncio.sleep(0.1)
            captured.append(_status_resp("m", "MEDIA_GENERATION_STATUS_SUCCESSFUL"))

        asyncio.create_task(_append_later())
        result = await VideoGenerationMixin._poll_video_status(
            page, captured, "m", timeout_s=2.0, poll_interval_s=0.05
        )
        assert result.succeeded is True

    @pytest.mark.asyncio
    async def test_timeout_raises(self) -> None:
        page = MagicMock()
        with pytest.raises(TimeoutError, match="no terminal status"):
            await VideoGenerationMixin._poll_video_status(
                page, [], "m", timeout_s=0.2, poll_interval_s=0.05
            )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/api/transports/test_ui_automation_video.py -k "StatusResponseListener or PollVideoStatus"`
Expected: FAIL — `AttributeError`: `VideoGenerationMixin` has no `_attach_status_response_listener` / `_poll_video_status`.

- [ ] **Step 3: Add the status listener and the poll loop**

Append to `class VideoGenerationMixin` in `src/gflow_cli/api/transports/ui_automation_video.py`:

```python
    @staticmethod
    def _attach_status_response_listener(page: Page) -> tuple[list[dict[str, Any]], Any]:
        """Register a `page.on('response')` listener for the status route. Flow's
        SPA polls `batchCheckAsyncVideoGenerationStatus` itself while a
        generation runs; this captures that traffic. Returns `(captured, handler)`
        — the caller MUST `page.remove_listener('response', handler)` in a
        `finally`. Attached BEFORE `_send_prompt` so no early status response is
        missed (spec §5.5)."""
        captured: list[dict[str, Any]] = []

        async def on_response(response: Any) -> None:
            if VIDEO_STATUS_ROUTE not in response.url:
                return
            try:
                body = await response.json()
            except Exception as e:  # noqa: BLE001 — parse failures are non-fatal
                log.warning("ui_automation_video.status_parse_failed", error=str(e))
                return
            captured.append({"status": response.status, "url": response.url, "body": body})

        page.on("response", on_response)
        return captured, on_response

    @staticmethod
    async def _poll_video_status(
        page: Page,
        captured_status: list[dict[str, Any]],
        media_name: str,
        *,
        timeout_s: float = 600.0,
        poll_interval_s: float = 2.0,
        stall_nudge_s: float = 120.0,
    ) -> VideoStatus:
        """Read terminal status from Flow's own captured status traffic.

        `captured_status` is the list filled by `_attach_status_response_listener`.
        Each tick scans the WHOLE list for a terminal status of `media_name`
        (Flow appends chronologically; a terminal status is the last it emits) —
        no early `break`, so a terminal entry is never skipped. Returns the
        `VideoStatus` once terminal; the caller maps a FAILED status to a typed
        error (spec §7).

        If Flow stops polling (a backgrounded tab can throttle its timers) the
        captured list stops growing; after `stall_nudge_s` with no new capture
        this brings the page to the foreground ONCE and keeps waiting (spec
        §5.5). Raises `TimeoutError` only at the hard `timeout_s` deadline.
        """
        deadline = time.monotonic() + timeout_s
        last_status: str | None = None
        seen_count = len(captured_status)
        last_progress = time.monotonic()
        nudged = False
        while time.monotonic() < deadline:
            terminal: VideoStatus | None = None
            for response in captured_status:
                try:
                    status = parse_video_status(response.get("body") or {}, media_id=media_name)
                except ValueError:
                    continue  # this response is for other media — skip
                last_status = status.status
                if status.is_terminal:
                    terminal = status
            if terminal is not None:
                log.info(
                    "ui_automation_video.poll_terminal",
                    media_name=media_name,
                    status=terminal.status,
                )
                return terminal
            # Stall detection: nudge the tab to the foreground ONCE if Flow's
            # own polling has stopped — or never started — appending responses.
            if len(captured_status) != seen_count:
                seen_count = len(captured_status)
                last_progress = time.monotonic()
            elif not nudged and time.monotonic() - last_progress > stall_nudge_s:
                nudged = True
                # Distinguish "Flow never polled the status route at all" from
                # "Flow stalled mid-run" — the former is the single most likely
                # production failure (spec §5.5 flags it as unconfirmed).
                event = (
                    "ui_automation_video.poll_no_status_traffic"
                    if seen_count == 0
                    else "ui_automation_video.poll_stall_nudge"
                )
                log.warning(event, media_name=media_name, status_responses_seen=seen_count)
                try:
                    await page.bring_to_front()
                except Exception as e:  # noqa: BLE001 — best-effort
                    log.debug("ui_automation_video.bring_to_front_failed", error=str(e))
            await asyncio.sleep(poll_interval_s)
        cause = (
            "Flow never polled the status route"
            if seen_count == 0
            else "Flow stopped polling before a terminal status"
        )
        raise TimeoutError(
            f"no terminal status for {media_name!r} within {timeout_s:.0f}s — "
            f"{seen_count} status response(s) seen, last status: {last_status}. {cause}."
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/api/transports/test_ui_automation_video.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/transports/ui_automation_video.py tests/api/transports/test_ui_automation_video.py
git commit -m "feat(video): add status polling that captures Flow's own traffic"
```

---

## Task 11: `ui_automation_video.py` — selectors, `_probe_selector_cascade`, mode switching

Increment 6, part 1 (spec §5.2, §6). Selector strings are copied **verbatim** from the Phase-0-verified `scripts/smoke_video_editor.py`. The generic cascade helper is the first of the ≥3 mockable seams §8 increment 6 requires.

**Files:**
- Modify: `src/gflow_cli/api/transports/ui_automation_video.py`
- Modify: `tests/api/transports/test_ui_automation_video.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/transports/test_ui_automation_video.py`:

```python
def _cascade_page(visible: set[str]) -> MagicMock:
    """A fake page whose locator(sel) is 'visible' only for sel in `visible`."""
    page = MagicMock()

    def _locator(sel: str) -> MagicMock:
        loc = MagicMock()
        loc.first = loc
        if sel in visible:
            loc.wait_for = AsyncMock()
        else:
            loc.wait_for = AsyncMock(side_effect=Exception("not visible"))
        loc.click = AsyncMock()
        return loc

    page.locator = MagicMock(side_effect=_locator)
    page.wait_for_timeout = AsyncMock()
    page.screenshot = AsyncMock()
    return page


class TestProbeSelectorCascade:
    @pytest.mark.asyncio
    async def test_returns_first_visible_match(self) -> None:
        page = _cascade_page({"b"})
        loc = await VideoGenerationMixin._probe_selector_cascade(
            page, "x", ("a", "b", "c"), timeout_ms=10
        )
        assert loc is not None

    @pytest.mark.asyncio
    async def test_returns_none_when_all_miss(self) -> None:
        page = _cascade_page(set())
        loc = await VideoGenerationMixin._probe_selector_cascade(
            page, "x", ("a", "b"), timeout_ms=10
        )
        assert loc is None


class TestSwitchToVideoMode:
    @pytest.mark.asyncio
    async def test_opens_dropdown_then_clicks_video_tab(self) -> None:
        from gflow_cli.api.transports import ui_automation_video as mod

        trigger = mod.MODE_SWITCH_TRIGGER_SELECTORS[0]
        video_tab = mod.VIDEO_TAB_IN_MENU_SELECTORS[0]
        page = _cascade_page({trigger, video_tab})
        await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)
        # both the trigger and the in-menu video tab were located
        assert page.locator.call_count >= 2

    @pytest.mark.asyncio
    async def test_raises_when_trigger_missing(self) -> None:
        page = _cascade_page(set())
        with pytest.raises(RuntimeError, match="mode-switch"):
            await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)

    @pytest.mark.asyncio
    async def test_raises_when_video_tab_missing(self) -> None:
        from gflow_cli.api.transports import ui_automation_video as mod

        page = _cascade_page({mod.MODE_SWITCH_TRIGGER_SELECTORS[0]})
        with pytest.raises(RuntimeError, match="Video tab"):
            await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)


class TestWaitVideoEditorReady:
    @pytest.mark.asyncio
    async def test_returns_when_anchor_visible(self) -> None:
        from gflow_cli.api.transports import ui_automation_video as mod

        page = _cascade_page({mod._EDITOR_READY_ANCHOR})
        await VideoGenerationMixin._wait_video_editor_ready(page)  # must not raise

    @pytest.mark.asyncio
    async def test_timeout_is_non_fatal(self) -> None:
        page = _cascade_page(set())
        await VideoGenerationMixin._wait_video_editor_ready(page)  # logs, must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/api/transports/test_ui_automation_video.py -k "Cascade or SwitchToVideoMode"`
Expected: FAIL — missing selectors / `AttributeError` on `_probe_selector_cascade` / `_switch_to_video_mode`.

- [ ] **Step 3: Add the selectors and the mode-switch helpers**

In `src/gflow_cli/api/transports/ui_automation_video.py`, add these module-level constants after `VIDEO_STATUS_ROUTE` (verbatim from `scripts/smoke_video_editor.py` — Phase 0 verified):

```python
# Mode switching is a 2-step dropdown (spec §6, §10.5). The trigger is the
# unified generation-settings button — the only button[aria-haspopup='menu']
# carrying an aspect-ratio crop_* icon; clicking it opens a role='menu' with
# the Imagem/Vídeo role='tablist' (the tabs are not in the DOM until it opens).
MODE_SWITCH_TRIGGER_SELECTORS = (
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_16_9'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_9_16'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_square'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_portrait'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_landscape'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_original'))",
)
VIDEO_TAB_IN_MENU_SELECTORS = (
    "[role='menu'] [role='tab'][aria-controls*='VIDEO']",
    "[role='menu'] [role='tab']:has(i:text('play_circle'))",
    "[role='tab'][aria-controls*='VIDEO']",
    "[role='menu'] [role='tab']:has-text('Vídeo')",
    "[role='menu'] [role='tab']:has-text('Video')",
)
# Output-count tabs in the same dropdown. Flow defaults output count to x2
# (two videos = double credits — spec §10.5); generate_video forces count=1.
COUNT_ONE_SELECTORS = (
    "[role='menu'] [role='tab'][aria-controls*='-content-1']",
    "[role='menu'] [role='tab'][id*='-trigger-1']",
    "[role='menu'] [role='tab']:text-is('1x')",
)
# Aspect tabs inside the open menu. §6 best-effort — the Phase 0 spike confirmed
# video offers 9:16 / 16:9 only but did not lock an exact aspect-set selector;
# Phase B e2e hardens these. A miss is non-fatal (Flow's default applies).
VIDEO_ASPECT_TAB_SELECTORS: dict[Aspect, tuple[str, ...]] = {
    Aspect.PORTRAIT: (
        "[role='menu'] [role='tab'][aria-controls*='9_16']",
        "[role='menu'] [role='tab']:text-is('9:16')",
        "[role='tab']:has-text('9:16')",
    ),
    Aspect.LANDSCAPE: (
        "[role='menu'] [role='tab'][aria-controls*='16_9']",
        "[role='menu'] [role='tab']:text-is('16:9')",
        "[role='tab']:has-text('16:9')",
    ),
}

# The editor SPA's ready anchor — the Slate prompt textbox. The /project/ URL
# nav fires before the UI mounts; this is the readiness gate (used by
# _wait_video_editor_ready and asserted in its test).
_EDITOR_READY_ANCHOR = (
    "div[role='textbox'][data-slate-editor='true'], div[contenteditable='true']"
)
```

Then append to `class VideoGenerationMixin`:

```python
    @staticmethod
    async def _probe_selector_cascade(
        page: Page,
        label: str,
        candidates: tuple[str, ...],
        *,
        timeout_ms: int = 4000,
    ) -> Locator | None:
        """Try each selector in order; return the first visible match or None.
        Logs every attempt so a failed probe is diagnosable from the structured
        log alone."""
        for selector in candidates:
            try:
                loc = page.locator(selector).first
                await loc.wait_for(state="visible", timeout=timeout_ms)
                log.info("ui_automation_video.selector_matched", probe=label, selector=selector)
                return loc
            except Exception:  # noqa: BLE001 — selector miss; try the next
                log.debug("ui_automation_video.selector_miss", probe=label, selector=selector)
        log.warning("ui_automation_video.selector_probe_failed", probe=label)
        return None

    @staticmethod
    async def _switch_to_video_mode(page: Page, *, out_dir: Path | None) -> None:
        """Open the 2-step mode dropdown and switch to Video mode. The menu
        stays open afterward so the caller can also set aspect + count."""
        trigger = await VideoGenerationMixin._probe_selector_cascade(
            page, "mode_switch_trigger", MODE_SWITCH_TRIGGER_SELECTORS
        )
        if trigger is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_mode_trigger.png")
            raise RuntimeError(
                f"mode-switch dropdown trigger not found on the Flow editor. Screenshot: {shot}"
            )
        await trigger.click()
        await page.wait_for_timeout(800)
        video_tab = await VideoGenerationMixin._probe_selector_cascade(
            page, "video_mode_tab", VIDEO_TAB_IN_MENU_SELECTORS
        )
        if video_tab is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_video_tab.png")
            raise RuntimeError(
                f"Video tab not found in the mode dropdown. Screenshot: {shot}"
            )
        await video_tab.click()
        await page.wait_for_timeout(1200)
        log.info("ui_automation_video.video_mode_entered")

    @staticmethod
    async def _wait_video_editor_ready(page: Page) -> None:
        """Wait for the editor SPA to mount before probing video controls. The
        /project/ URL nav fires before the UI renders — the Phase 0 spike found
        probes taken right after it see only the page shell. The prompt textbox
        is the ready anchor. Non-fatal on timeout (the cascade probes still
        have their own per-selector waits)."""
        try:
            await page.locator(_EDITOR_READY_ANCHOR).first.wait_for(
                state="visible", timeout=20_000
            )
            await page.wait_for_timeout(1000)
            log.info("ui_automation_video.editor_ready")
        except Exception as e:  # noqa: BLE001 — non-fatal readiness gate
            log.warning("ui_automation_video.editor_ready_timeout", error=str(e))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/api/transports/test_ui_automation_video.py -k "Cascade or SwitchToVideoMode"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/transports/ui_automation_video.py tests/api/transports/test_ui_automation_video.py
git commit -m "feat(video): add the video-editor selector cascade and mode switching"
```

---

## Task 12: `ui_automation_video.py` — `_set_output_count_one` + `_select_video_aspect`

Increment 6, part 2. Both run while the mode dropdown is still open (after `_switch_to_video_mode`). A miss is non-fatal — generation proceeds with Flow's defaults — mirroring the image transport's `_configure_generation_settings`.

**Files:**
- Modify: `src/gflow_cli/api/transports/ui_automation_video.py`
- Modify: `tests/api/transports/test_ui_automation_video.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/transports/test_ui_automation_video.py`:

```python
class TestSetOutputCountOne:
    @pytest.mark.asyncio
    async def test_clicks_the_count_one_tab(self) -> None:
        from gflow_cli.api.transports import ui_automation_video as mod

        sel = mod.COUNT_ONE_SELECTORS[0]
        page = _cascade_page({sel})
        await VideoGenerationMixin._set_output_count_one(page)
        page.locator.assert_any_call(sel)

    @pytest.mark.asyncio
    async def test_missing_count_tab_is_non_fatal(self) -> None:
        page = _cascade_page(set())
        await VideoGenerationMixin._set_output_count_one(page)  # must not raise


class TestSelectVideoAspect:
    @pytest.mark.asyncio
    async def test_clicks_the_landscape_tab(self) -> None:
        from gflow_cli.api.transports import ui_automation_video as mod
        from gflow_cli.api.video import Aspect

        sel = mod.VIDEO_ASPECT_TAB_SELECTORS[Aspect.LANDSCAPE][0]
        page = _cascade_page({sel})
        await VideoGenerationMixin._select_video_aspect(page, Aspect.LANDSCAPE)
        page.locator.assert_any_call(sel)

    @pytest.mark.asyncio
    async def test_missing_aspect_tab_is_non_fatal(self) -> None:
        from gflow_cli.api.video import Aspect

        page = _cascade_page(set())
        await VideoGenerationMixin._select_video_aspect(page, Aspect.PORTRAIT)  # must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/api/transports/test_ui_automation_video.py -k "OutputCount or SelectVideoAspect"`
Expected: FAIL — `AttributeError` on `_set_output_count_one` / `_select_video_aspect`.

- [ ] **Step 3: Add the two helpers**

Append to `class VideoGenerationMixin` in `src/gflow_cli/api/transports/ui_automation_video.py`:

```python
    @staticmethod
    async def _set_output_count_one(page: Page) -> None:
        """Force the output count to 1. Flow defaults to x2 (two videos =
        double credits — spec §10.5). Non-fatal on miss."""
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page, "count_one_tab", COUNT_ONE_SELECTORS
        )
        if tab is None:
            log.warning("ui_automation_video.count_not_set", note="Flow default (x2) applies")
            return
        await tab.click()
        await page.wait_for_timeout(400)
        log.info("ui_automation_video.output_count_set", count=1)

    @staticmethod
    async def _select_video_aspect(page: Page, aspect: Aspect) -> None:
        """Click the aspect-ratio tab for `aspect` in the open mode dropdown.
        Non-fatal on miss — generation proceeds with Flow's default ratio."""
        candidates = VIDEO_ASPECT_TAB_SELECTORS.get(aspect)
        if candidates is None:
            log.warning("ui_automation_video.aspect_unsupported", aspect=aspect.value)
            return
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page, "video_aspect_tab", candidates
        )
        if tab is None:
            log.warning("ui_automation_video.aspect_not_set", aspect=aspect.value)
            return
        await tab.click()
        await page.wait_for_timeout(400)
        log.info("ui_automation_video.aspect_set", aspect=aspect.value)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/api/transports/test_ui_automation_video.py -k "OutputCount or SelectVideoAspect"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/transports/ui_automation_video.py tests/api/transports/test_ui_automation_video.py
git commit -m "feat(video): add video output-count and aspect-ratio controls"
```

---

## Task 13: `generate_video` orchestration; mix into `UiAutomationTransport`

Increment 7 (spec §5.1). Wires every helper into the public T2V entry point and mixes `VideoGenerationMixin` into `UiAutomationTransport`.

**Files:**
- Modify: `src/gflow_cli/api/transports/ui_automation_video.py`
- Modify: `src/gflow_cli/api/transports/ui_automation.py`
- Modify: `tests/api/transports/test_ui_automation_video.py`

- [ ] **Step 1: Write the failing tests**

Add these imports to the **top** of `tests/api/transports/test_ui_automation_video.py` (alongside the existing imports — not mid-file, or `ruff` flags `E402`):

```python
from pathlib import Path

from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoStatus
```

Then append the test classes and helpers:

```python
def _mock_async_page() -> MagicMock:
    """A MagicMock page whose AWAITED methods are AsyncMock (so `await page.x()`
    works) and whose `remove_listener` is a plain MagicMock."""
    page = MagicMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.bring_to_front = AsyncMock()
    page.remove_listener = MagicMock()
    return page


def _stub_video_helpers(monkeypatch: pytest.MonkeyPatch, *, generate_resp: dict) -> None:
    """Stub every VideoGenerationMixin helper `generate_video` drives, so the
    orchestration is testable without a browser. The listener stubs return
    `(captured, handler)` tuples to match the real signatures."""
    monkeypatch.setattr(VideoGenerationMixin, "_wait_video_editor_ready", AsyncMock())
    monkeypatch.setattr(VideoGenerationMixin, "_switch_to_video_mode", AsyncMock())
    monkeypatch.setattr(VideoGenerationMixin, "_set_output_count_one", AsyncMock())
    monkeypatch.setattr(VideoGenerationMixin, "_select_video_aspect", AsyncMock())
    monkeypatch.setattr(
        VideoGenerationMixin,
        "_attach_video_response_listener",
        staticmethod(lambda page: ([generate_resp], object())),
    )
    monkeypatch.setattr(
        VideoGenerationMixin,
        "_attach_status_response_listener",
        staticmethod(lambda page: ([], object())),
    )


class TestGenerateVideoGuards:
    @pytest.mark.asyncio
    async def test_requires_setup(self) -> None:
        transport = UiAutomationTransport()
        with pytest.raises(RuntimeError, match="setup"):
            await transport.generate_video(request=GenerateVideoRequest(prompt="x"))

    @pytest.mark.asyncio
    async def test_rejects_non_t2v(self) -> None:
        transport = UiAutomationTransport()
        transport._page = MagicMock()
        transport._setup_done = True
        req = GenerateVideoRequest(prompt="x", mode=Mode.I2V, start_image=Path("a.png"))
        with pytest.raises(NotImplementedError, match="T2V"):
            await transport.generate_video(request=req)

    @pytest.mark.asyncio
    async def test_rejects_square_aspect(self) -> None:
        transport = UiAutomationTransport()
        transport._page = MagicMock()
        transport._setup_done = True
        req = GenerateVideoRequest(prompt="x", aspect=Aspect.SQUARE)
        with pytest.raises(ValueError, match="SQUARE"):
            await transport.generate_video(request=req)


class TestGenerateVideoOrchestration:
    @pytest.mark.asyncio
    async def test_t2v_happy_path_returns_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        _stub_video_helpers(
            monkeypatch,
            generate_resp={
                "status": 200,
                "url": _T2V_URL,
                "body": {"media": [{"name": "vid-1"}]},
            },
        )

        async def _fake_poll(page, captured, media_name, **_k):  # type: ignore[no-untyped-def]
            assert media_name == "vid-1"
            return VideoStatus(media_id="vid-1", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")

        monkeypatch.setattr(VideoGenerationMixin, "_poll_video_status", staticmethod(_fake_poll))
        result = await transport.generate_video(request=GenerateVideoRequest(prompt="a forest"))
        assert result.succeeded is True
        # both response listeners were detached in the finally block
        assert transport._page.remove_listener.call_count == 2

    @pytest.mark.asyncio
    async def test_t2v_401_raises_auth_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        _stub_video_helpers(monkeypatch, generate_resp={"status": 401, "url": _T2V_URL, "body": {}})
        from gflow_cli.errors import AuthExpiredError

        with pytest.raises(AuthExpiredError):
            await transport.generate_video(request=GenerateVideoRequest(prompt="x"))
        assert transport._page.remove_listener.call_count == 2  # detached on the error path too

    @pytest.mark.asyncio
    async def test_t2v_403_raises_waf_rejection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        _stub_video_helpers(monkeypatch, generate_resp={"status": 403, "url": _T2V_URL, "body": {}})
        from gflow_cli.errors import WafRejectionError

        with pytest.raises(WafRejectionError):
            await transport.generate_video(request=GenerateVideoRequest(prompt="x"))

    @pytest.mark.asyncio
    async def test_t2v_500_raises_wire_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        _stub_video_helpers(monkeypatch, generate_resp={"status": 500, "url": _T2V_URL, "body": {}})
        from gflow_cli.errors import WireFormatError

        with pytest.raises(WireFormatError):
            await transport.generate_video(request=GenerateVideoRequest(prompt="x"))

    @pytest.mark.asyncio
    async def test_t2v_200_empty_media_raises_wire_format(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        _stub_video_helpers(
            monkeypatch,
            generate_resp={"status": 200, "url": _T2V_URL, "body": {"media": []}},
        )
        from gflow_cli.errors import WireFormatError

        with pytest.raises(WireFormatError):
            await transport.generate_video(request=GenerateVideoRequest(prompt="x"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/api/transports/test_ui_automation_video.py -k GenerateVideo`
Expected: FAIL — `AttributeError`: `UiAutomationTransport` has no `generate_video`.

- [ ] **Step 3: Add `generate_video` + `_generate_video_locked` + `_await_generate_response`**

Append to `class VideoGenerationMixin` in `src/gflow_cli/api/transports/ui_automation_video.py`:

```python
    @staticmethod
    async def _await_generate_response(
        captured: list[dict[str, Any]],
        *,
        timeout_s: float = 180.0,
        poll_interval_s: float = 0.5,
    ) -> dict[str, Any]:
        """Wait for the first captured batchAsyncGenerateVideo* response."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and not captured:
            await asyncio.sleep(poll_interval_s)
        if not captured:
            raise TimeoutError(
                f"no batchAsyncGenerateVideo* response within {timeout_s:.0f}s — "
                "did the submit fire? did reCAPTCHA fail silently?"
            )
        return captured[0]

    async def generate_video(
        self,
        *,
        request: GenerateVideoRequest,
        out_dir: Path | None = None,
        poll_timeout_s: float = 600.0,
    ) -> VideoStatus:
        """Generate ONE video by driving the Flow editor UI (Phase A: T2V only).

        Returns a `VideoStatus` for both SUCCESSFUL and FAILED terminal states;
        the caller maps a FAILED status to a typed error (spec §7). Raises
        `RuntimeError` (no setup / editor control missing), `NotImplementedError`
        (non-T2V), `ValueError` (SQUARE aspect), `AuthExpiredError` (401),
        `WafRejectionError` (403), `WireFormatError` (other non-200 / no media),
        or `TimeoutError`.
        """
        if not self._setup_done or self._page is None:
            raise RuntimeError(
                "UiAutomationTransport.setup() must be called before generate_video()"
            )
        if request.mode is not Mode.T2V:
            raise NotImplementedError(
                "Phase A supports T2V only; I2V and R2V land in Phase B"
            )
        if request.aspect is Aspect.SQUARE:
            raise ValueError(
                "video generation does not support the SQUARE aspect; "
                "use PORTRAIT (9:16) or LANDSCAPE (16:9)"
            )
        async with self._generate_lock:
            return await self._generate_video_locked(request, out_dir, poll_timeout_s)

    async def _generate_video_locked(
        self,
        request: GenerateVideoRequest,
        out_dir: Path | None,
        poll_timeout_s: float,
    ) -> VideoStatus:
        """Serialized body of `generate_video` — runs under `self._generate_lock`
        (shared with `generate_images`: one Page, one DOM)."""
        page: Page = self._page  # type: ignore[assignment]  # guarded in generate_video

        await self._enter_editor(page, out_dir)
        await VideoGenerationMixin._wait_video_editor_ready(page)
        await VideoGenerationMixin._switch_to_video_mode(page, out_dir=out_dir)
        await VideoGenerationMixin._select_video_aspect(page, request.aspect)
        await VideoGenerationMixin._set_output_count_one(page)
        await page.keyboard.press("Escape")  # close the mode dropdown
        await page.wait_for_timeout(400)

        # Attach BOTH listeners synchronously BEFORE the prompt is submitted so
        # neither the generate response nor an early status poll is missed.
        generate_captured, generate_handler = (
            VideoGenerationMixin._attach_video_response_listener(page)
        )
        status_captured, status_handler = (
            VideoGenerationMixin._attach_status_response_listener(page)
        )
        try:
            await self._send_prompt(page, request.prompt, out_dir)

            generate_resp = await VideoGenerationMixin._await_generate_response(generate_captured)
            http_status = generate_resp.get("status")
            url = str(generate_resp.get("url", ""))
            # errors.py documents `route` as a sanitized route NAME, not a URL.
            route = next((r for r in VIDEO_GENERATE_ROUTES if r in url), "video:generate")
            if http_status == 401:
                raise AuthExpiredError(
                    detail="batchAsyncGenerateVideo* returned HTTP 401 — session expired",
                    status=401,
                    route=route,
                )
            if http_status == 403:
                raise WafRejectionError(
                    detail=(
                        "batchAsyncGenerateVideo* returned HTTP 403 — WAF / reCAPTCHA rejection"
                    ),
                    status=403,
                    route=route,
                )
            if http_status != 200:
                raise WireFormatError(
                    detail=f"batchAsyncGenerateVideo* returned HTTP {http_status}",
                    status=http_status if isinstance(http_status, int) else None,
                    route=route,
                )
            # A video 200 ALWAYS carries media[0] (the asset slot — capture 02);
            # content rejection surfaces later as a FAILED *status*, not empty
            # media. So a missing media[0] here is a genuine wire anomaly —
            # WireFormatError, NOT ContentPolicyError (the image-flow pattern).
            try:
                media_name = media_name_from_generate_response(generate_resp.get("body") or {})
            except ValueError as e:
                # discovery carries only the route + the body's top-level KEY
                # NAMES (not values) — enough to diagnose the anomaly without
                # logging `remainingCredits`, media UUIDs, or any token.
                anomaly_body = generate_resp.get("body") or {}
                raise WireFormatError(
                    detail=f"video generate response carries no media id: {e}",
                    route=route,
                    discovery={"route": route, "top_level_keys": sorted(anomaly_body)},
                ) from e

            return await VideoGenerationMixin._poll_video_status(
                page, status_captured, media_name, timeout_s=poll_timeout_s
            )
        finally:
            # The Page is pooled and persistent — remove both listeners so they
            # never leak across calls.
            page.remove_listener("response", generate_handler)
            page.remove_listener("response", status_handler)
```

- [ ] **Step 4: Mix `VideoGenerationMixin` into `UiAutomationTransport`**

In `src/gflow_cli/api/transports/ui_automation.py`:
- Add the import (after the existing `from gflow_cli.errors import (...)` block):
  ```python
  from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin
  ```
- Change the class declaration from `class UiAutomationTransport:` to:
  ```python
  class UiAutomationTransport(VideoGenerationMixin):
  ```
- In `src/gflow_cli/api/transports/base.py` (around line 55), a transport-Protocol docstring says it is "keeping the Protocol media-agnostic for future `generate_video()`." `generate_video` now exists (on `VideoGenerationMixin`) — reword to drop the "future" framing (e.g. "media-agnostic across image and video generation").

`UiAutomationTransport.__init__` already sets `self._page`, `self._setup_done`, and `self._generate_lock`, and the class defines `_enter_editor` / `_send_prompt` — the five members the `VideoGenerationMixin` type-only host contract declares. No `__init__` change is needed.

- [ ] **Step 5: Run the tests to verify they pass**

Run:
```bash
uv run pytest -q tests/api/transports/test_ui_automation_video.py tests/api/transports/test_ui_automation.py
uv run pyright src
```
Expected: PASS — both transport test files green, types clean. (`pyright --strict` confirms `UiAutomationTransport` supplies the type-only host contract that `VideoGenerationMixin` declares.)

- [ ] **Step 6: Run the full quality gate**

Run:
```bash
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src tests && uv run ruff format --check src tests
uv run pyright src
uv run pytest -q --cov=gflow_cli
```
Expected: all green; coverage ≥80% overall and ≥90% on `api/` (`video.py`, `ui_automation_video.py`). The 401/403/500/empty-media branches are covered by the explicit `TestGenerateVideoOrchestration` tests and the timeout branch by `TestPollVideoStatus`; if `ui_automation_video.py` is still below 90%, add tests for the remaining uncovered lines before committing.

- [ ] **Step 7: Commit**

```bash
git add src/gflow_cli/api/transports/ui_automation_video.py src/gflow_cli/api/transports/ui_automation.py tests/api/transports/test_ui_automation_video.py
git commit -m "feat(video): add generate_video T2V orchestration on UiAutomationTransport"
```

---

## Task 14: Docs — `PLAN.md`, `CHANGELOG.md`, `README.md`

Spec §9 lists `PLAN.md` (phase entries) and `README.md` (video-usage section). `CHANGELOG.md [Unreleased]` records the user-visible change (the `gflow video` commands are temporarily unavailable).

**Files:**
- Modify: `PLAN.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`

- [ ] **Step 1: Add the Phase A entry to `PLAN.md`**

In `PLAN.md`, add a Phase A entry alongside the existing video phase entries (follow the file's existing phase-entry format). Content to capture: *Phase A — Video T2V transport. Retired the 401-dead HTTP video path (`client.generate_video`/`get_video_status`, `build_generate_body`/`model_key`, `VideoOperation`/`VideoStatus` DTOs). Added pure `video.py` value objects + parsers and a `ui_automation_video.py` mixin delivering `generate_video` for T2V via the Flow editor UI, with status polling that captures Flow's own traffic. `gflow video` CLI commands stubbed pending Phase B (I2V + R2V + CLI rewire).* If the project keeps a Decision Log, add one line: *the HTTP status path (`get_video_status`) was retired alongside `generate_video` — it is the same 401-dead path and would collide with the new `video.py:VideoStatus`.*

- [ ] **Step 2: Add a `CHANGELOG.md [Unreleased]` note**

Under `[Unreleased]` in `CHANGELOG.md`, add (matching the file's existing section style — `Added` / `Changed` / `Removed`):
- **Changed:** `gflow video t2v/i2v/batch` now report "temporarily unavailable" — video generation is being rebuilt on the UI-automation transport.
- **Removed:** the 401-dead HTTP video API path (`FlowApiClient.generate_video`, `get_video_status`).

- [ ] **Step 3: Update the `README.md` video section**

In `README.md`, replace the Python video example (the `make_clip()` snippet around lines 178-201 that calls `client.generate_video` / `get_video_status` / uses `start_asset_uuid`) and the `gflow video` mention in the Commands block with a short note:

```markdown
> **Video generation is being rebuilt.** The HTTP video path returned HTTP 401
> and was retired. Video generation now runs on the UI-automation transport:
> Phase A ships the text-to-video transport; the `gflow video` CLI commands
> return in a later release (Phase B). See
> `docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md`.
```

Do not leave a broken code sample in the README.

- [ ] **Step 4: Verify and commit**

Run:
```bash
uv run python scripts/ci/check_repo_hygiene.py
uv run pytest -q --cov=gflow_cli
```
Expected: hygiene gate passes; full suite green.

```bash
git add PLAN.md CHANGELOG.md README.md
git commit -m "docs(video): record Phase A and stub the video-usage section"
```

---

## Self-Review

**1. Spec coverage** (spec §8 increments → tasks):
- Increment 1 (retire the dead HTTP video path) → Tasks 1-5. Includes the §9-mandated caller sweep: `cli_video.py` (T1), `smoke_e2e.py` (T2), `test_video.py` (T5), `test_client_generate_video.py` (T2), `test_client_image.py` (T2), plus the resolved-decision additions (`get_video_status`/`dto.VideoStatus`/`TestVideoStatus`, video BDD).
- Increment 2 (`video.py` value objects: `Mode.R2V`, `GenerateVideoRequest`, `VideoStatus`, `MAX_REFERENCE_IMAGES`) → Tasks 6-7.
- Increment 3 (parsers `parse_video_status`, `media_name_from_generate_response`) → Task 8.
- Increment 4 (`_attach_video_response_listener`) → Task 9.
- Increment 5 (`_poll_video_status`) → Task 10.
- Increment 6 (mode switching, ≥3 mockable seams: `_probe_selector_cascade`, `_switch_to_video_mode`, `_set_output_count_one`, `_select_video_aspect`) → Tasks 11-12.
- Increment 7 (`generate_video` orchestration, pre-setup guard, `_generate_lock`) → Task 13.
- §9 files: `video.py`, `client.py`, `dto.py`, `api/__init__.py`, `ui_automation_video.py` (new), `ui_automation.py`, `cli_video.py`, `smoke_e2e.py`, `README.md`, `test_video.py`, `test_client_generate_video.py`, `test_ui_automation*.py`, `test_video_i2v_e2e.py`, `PLAN.md` — all covered. `KNOWN_ISSUES.md` already updated (§9). E2E (`test_video_ui_automation_e2e.py`) and `test_assets/` fixtures are deferred to Phase B per the resolved decision; `routes.py` intentionally untouched (see Deviations).
- §5.0's `_VideoHost` Protocol is replaced by a type-only host contract on `VideoGenerationMixin` itself (bare attribute annotations + an `if TYPE_CHECKING` method-stub block) — `pyright --strict` rejects an explicit `self: _VideoHost` annotation on a mixin method whose class does not satisfy that Protocol. Same intent, a working realization (see Deviations).

**2. Placeholder scan:** none — every code step carries complete code; every command carries expected output. Removal tasks (1-5) use delete → run-gate → commit; TDD tasks (6-13) use write-test → run-fail → implement → run-pass → commit.

**3. Type/name consistency:** `VideoStatus` fields `media_id` / `status` / `failure_reasons` / `error_message` are consistent across `video.py` (T7), `parse_video_status` (T8), `_poll_video_status` (T10), and the T13 tests. `VideoGenerationMixin` static helpers `_attach_video_response_listener`, `_attach_status_response_listener`, `_poll_video_status`, `_probe_selector_cascade`, `_switch_to_video_mode`, `_set_output_count_one`, `_select_video_aspect`, `_await_generate_response`, `_generate_video_locked`, and the instance method `generate_video` are referenced under those exact names everywhere. `media_name_from_generate_response` / `parse_video_status` names match between `video.py` and the transport import. `Mode.T2V/I2V/R2V`, `Aspect.PORTRAIT/LANDSCAPE/SQUARE` consistent throughout.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-19-video-phase-a-t2v.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints for review.

Which approach?
