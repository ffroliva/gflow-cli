# Movie P0 — `asyncio` Multi-Scene Hotfix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the release-blocking `NameError` that aborts every multi-scene `gflow movie run` after the first scene, and add the regression tests that should have caught it.

**Architecture:** `gflow_cli/cli_movie.py` calls `await asyncio.sleep(5)` as a reCAPTCHA cooldown between scenes, but never imports `asyncio`. The call sits *outside* the per-scene `try/except`, so on the 2nd scene it raises `NameError` and aborts the whole run (and on resume, the first already-completed scene makes the first *new* scene hit it → zero forward progress). Fix = add the import and relocate the cooldown inside the `try` so any failure there is handled per-scene. The existing async-orchestrator tests only ever use single-scene manifests, so the bug was invisible — we add a 2-scene run test and a resume test.

**Tech Stack:** Python 3.13, pytest (`pytest-asyncio`), `unittest.mock`. Run tests with the worktree venv: `.venv/Scripts/python.exe -m pytest` (do **not** use `uv run pytest` on Windows — it's broken here).

---

### Task 1: Add the failing 2-scene regression test, then fix the import

**Files:**
- Test: `tests/cli/test_cli_movie.py` (add a method to `class TestRunMovieOrchestrator`, near line 400)
- Modify: `src/gflow_cli/cli_movie.py` (imports ~line 19; cooldown block ~lines 341-346)

- [ ] **Step 1: Write the failing test**

Add this method inside `class TestRunMovieOrchestrator` in `tests/cli/test_cli_movie.py` (it reuses the file's existing helpers `_mock_client_cm`, `_make_video_result` and imports `MovieManifest`, `SceneDef`, `MovieState`, `AsyncMock`, `MagicMock`, `patch`, already imported at the top of that test module):

```python
    async def test_two_scene_run_does_not_crash_on_cooldown(self, tmp_path: Path) -> None:
        """Regression: the reCAPTCHA cooldown on scene 2+ must not NameError.

        Before the fix, `cli_movie` calls `asyncio.sleep` without importing
        asyncio, so the 2nd scene aborts the whole run. `patch("asyncio.sleep")`
        makes the (post-fix) cooldown instant; pre-fix it raises NameError.
        """
        from gflow_cli.cli_movie import _run_movie

        manifest = MovieManifest(
            title="T",
            project="p",
            characters=(),
            scenes=(
                SceneDef(title="S1", type="t2v", prompt="x"),
                SceneDef(title="S2", type="t2v", prompt="y"),
            ),
        )
        state = MovieState(title="T", project="p")
        state_path = tmp_path / "state.json"

        with (
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as mock_recorder_cls,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch(
                "gflow_cli.cli_movie._generate_scene",
                new=AsyncMock(return_value=_make_video_result()),
            ),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            mock_recorder_cls.open.return_value = MagicMock()
            await _run_movie(
                manifest=manifest,
                state=state,
                state_path=state_path,
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
                continue_on_error=True,
            )

        assert state.scenes["S1"].status == "completed"
        assert state.scenes["S2"].status == "completed"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_cli_movie.py::TestRunMovieOrchestrator::test_two_scene_run_does_not_crash_on_cooldown -v`
Expected: **FAIL** — `NameError: name 'asyncio' is not defined` (raised from the cooldown in `_run_movie`, so scene `S2` never completes).

- [ ] **Step 3: Add the `import asyncio` and relocate the cooldown into the try**

In `src/gflow_cli/cli_movie.py`, add the import (alphabetical, before `import sys`). Change:

```python
from __future__ import annotations

import sys
from pathlib import Path
```

to:

```python
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
```

Then relocate the cooldown so a failure there is handled per-scene rather than aborting the run. Change this block (currently ~lines 340-346):

```python
                refs = _collect_refs(scene_def, state)

                # reCAPTCHA cooldown
                if completed_scene_ids:
                    await asyncio.sleep(5)

                try:

                    video_result = await _generate_scene(
```

to:

```python
                refs = _collect_refs(scene_def, state)

                try:
                    # reCAPTCHA cooldown between scenes — inside the try so any
                    # failure here is handled per-scene and never aborts the run.
                    if completed_scene_ids:
                        await asyncio.sleep(5)

                    video_result = await _generate_scene(
```

(Leave the rest of the `try` body and the `except`/`state.save` unchanged.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_cli_movie.py::TestRunMovieOrchestrator::test_two_scene_run_does_not_crash_on_cooldown -v`
Expected: **PASS** (both `S1` and `S2` complete; `asyncio.sleep` is patched so no real delay).

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/cli_movie.py tests/cli/test_cli_movie.py
git commit -m "fix(movie): import asyncio so multi-scene runs don't crash after scene 1"
```

---

### Task 2: Add the resume regression test

**Files:**
- Test: `tests/cli/test_cli_movie.py` (add a method to `class TestRunMovieOrchestrator`)

This proves the *resume* path makes forward progress: with scene 1 already completed in state, scene 2 must still generate (pre-fix, the completed-scene append made the first new scene hit the un-imported cooldown and crash → zero progress).

- [ ] **Step 1: Write the test**

Add inside `class TestRunMovieOrchestrator`:

```python
    async def test_resume_generates_first_new_scene(self, tmp_path: Path) -> None:
        """Regression: on resume, the first NEW scene must generate (not crash
        on the cooldown triggered by the resumed completed scene)."""
        from gflow_cli.cli_movie import _run_movie

        manifest = MovieManifest(
            title="T",
            project="p",
            characters=(),
            scenes=(
                SceneDef(title="S1", type="t2v", prompt="x"),
                SceneDef(title="S2", type="t2v", prompt="y"),
            ),
        )
        state = MovieState(title="T", project="p")
        state.scenes["S1"] = SceneState(
            media_id="m",
            flow_operation_id="op-old",
            local_path="/out/v.mp4",
            status="completed",
        )
        state_path = tmp_path / "state.json"
        gen = AsyncMock(return_value=_make_video_result())

        with (
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as mock_recorder_cls,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch("gflow_cli.cli_movie._generate_scene", new=gen),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            mock_recorder_cls.open.return_value = MagicMock()
            await _run_movie(
                manifest=manifest,
                state=state,
                state_path=state_path,
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
                continue_on_error=True,
            )

        gen.assert_awaited_once()  # S1 skipped, S2 generated
        assert state.scenes["S2"].status == "completed"
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_cli_movie.py::TestRunMovieOrchestrator::test_resume_generates_first_new_scene -v`
Expected: **PASS** (with Task 1's fix in place). `_generate_scene` is awaited exactly once (for `S2`).

- [ ] **Step 3: Run the full movie test module to confirm no regressions**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_cli_movie.py -q`
Expected: all tests **PASS**.

- [ ] **Step 4: Commit**

```bash
git add tests/cli/test_cli_movie.py
git commit -m "test(movie): cover multi-scene run + resume (guards asyncio cooldown)"
```

---

## Self-Review

**Spec coverage:** Implements spec §11 P0 (`import asyncio`, relocate cooldown) and §14 (multi-scene run test that "catches P0" + resume test). No other spec section is in P0 scope.

**Placeholder scan:** None — every step has exact paths, full test code, exact commands, and expected output.

**Type consistency:** Reuses the test module's existing helpers verbatim (`_mock_client_cm`, `_make_video_result`) and `_run_movie`'s real keyword signature (`manifest`, `state`, `state_path`, `profile_name`, `profile_dir`, `out_dir`, `continue_on_error`), matching the surrounding tests (e.g. `test_happy_path_no_characters`). `SceneState` fields (`media_id`, `flow_operation_id`, `local_path`, `status`) match `movie_manifest.py`.
