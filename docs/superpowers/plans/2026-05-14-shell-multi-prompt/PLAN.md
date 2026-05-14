# Shell Multi-Prompt `t2i` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add shell-friendly multi-prompt input to `gflow image t2i` while preserving the existing single-prompt behavior exactly.

**Architecture:** Keep `t2i` split into a legacy single-prompt path and a new shell multi-prompt path. Extract image batch execution from `cli_run.py` into a small shared module so `gflow run --config` and multi-prompt `t2i` share one-session/one-project orchestration without sharing JSON-specific parsing. Add a focused prompt-source parser that retains source/line metadata and validates before profile resolution or browser/API work.

**Tech Stack:** Python 3.11+, Click, Rich, Playwright-backed `FlowApiClient`, pytest, pytest-bdd, unittest.mock, existing `gflow_cli.errors` / `run_with_handlers`.

---

## Companion Documents

- Spec: [`docs/superpowers/specs/2026-05-14-shell-multi-prompt-design.md`](../../specs/2026-05-14-shell-multi-prompt-design.md)
- Council reviews:
  - [`COUNCIL_REVIEW_CODE.md`](COUNCIL_REVIEW_CODE.md)
  - [`COUNCIL_REVIEW_SECURITY.md`](COUNCIL_REVIEW_SECURITY.md)
  - [`COUNCIL_REVIEW_GEMINI.md`](COUNCIL_REVIEW_GEMINI.md)
- Orchestration: [`2026-05-14-shell-multi-prompt-orchestration.md`](2026-05-14-shell-multi-prompt-orchestration.md)

---

## File Structure

### New Files

```text
src/gflow_cli/image_batch.py
  Shared image-batch dataclasses, prompt-source parsing helpers, display-safe
  prompt preview helpers, output path resolver, one-session batch runner, and
  summary renderer. This module must not import Click.

tests/cli/test_t2i_multi_prompt.py
  CLI/unit tests for prompt-source parsing, preflight validation, multi-prompt
  `t2i` wiring, seed rejection, model alias preservation, output naming, and
  fail-fast/continue semantics.
```

### Modified Files

```text
src/gflow_cli/cli_run.py
  Keep JSON schema parsing local, but import shared batch dataclasses/runner/
  renderer from `image_batch.py`. Existing `gflow run --config` behavior and
  tests must remain green.

src/gflow_cli/cli_image.py
  Change `t2i` positional argument to variadic, add `--prompts-file`, `--stdin`,
  and `--continue-on-error/--fail-fast`; route exactly-one positional prompt
  through the legacy `_run_t2i` path and multi-prompt sources through the shared
  batch runner.

tests/features/image.feature
tests/features/test_image_steps.py
  Add the required BDD scenarios from the spec using mocked helpers; no live
  Playwright.

docs/USAGE.md
README.md
CHANGELOG.md
  Update in the same commit as the CLI behavior change. Include input surfaces,
  file format, seed limitation, output naming, fail-fast behavior, and max
  fan-out of 200 images.

examples/sample_prompts.txt
examples/multi_prompt_t2i.py
examples/README.md
  Add small shell-friendly examples after core behavior is implemented.

tmp/marketing.md
  Gitignored operator launch tracker; add v0.6.0a1 post copy during ship task.

pyproject.toml
src/gflow_cli/__init__.py
  Version bump to 0.6.0a1 during ship task.
```

---

## Phase 1 - Test Scaffold

### Task 1: Unit Test Scaffold for Parser and CLI Preflight

**Goal:** Add failing tests for prompt-line parsing, prompt-file safety, source mutual exclusion, seed rejection, alias preservation, default output path, and single-prompt inert batch flags.

**Files:**
- Create: `tests/cli/test_t2i_multi_prompt.py`
- Read: `tests/cli/test_cli_image.py`
- Read: `tests/cli/test_cli_run.py`

**Exit Gate:** New targeted tests fail for missing `gflow_cli.image_batch` and missing `t2i` options, while existing tests remain runnable.

**Risks:** Over-mocking can miss real Click behavior. Use `CliRunner.invoke()` for CLI-boundary tests and pure function tests only for parser/output helpers.

**Estimated effort:** 45-60 minutes.

- [ ] **Step 1.1: Create parser tests first**

Add `tests/cli/test_t2i_multi_prompt.py` with this initial content:

```python
"""Tests for shell-friendly multi-prompt `gflow image t2i`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner


def test_parse_prompt_lines_skips_blank_and_comment_lines() -> None:
    from gflow_cli.image_batch import parse_prompt_lines

    parsed = parse_prompt_lines(
        "\ufeff first prompt \n\n  # comment\nsecond # literal\n   third   \n",
        source_label="--stdin",
    )

    assert [p.text for p in parsed] == ["first prompt", "second # literal", "third"]
    assert [p.line_number for p in parsed] == [1, 4, 5]
    assert [p.prompt_index for p in parsed] == [0, 1, 2]
    assert all(p.source_label == "--stdin" for p in parsed)


def test_parse_prompt_lines_empty_after_filtering_raises() -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import parse_prompt_lines

    with pytest.raises(ConfigurationError, match="between 1 and 50"):
        parse_prompt_lines("\n# only comment\n   \n", source_label="--stdin")


def test_parse_prompt_lines_over_50_raises() -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import parse_prompt_lines

    text = "\n".join(f"prompt {i}" for i in range(51))
    with pytest.raises(ConfigurationError, match="between 1 and 50"):
        parse_prompt_lines(text, source_label="--stdin")


def test_parse_prompt_lines_long_prompt_reports_source_line() -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import parse_prompt_lines

    with pytest.raises(ConfigurationError) as exc:
        parse_prompt_lines("ok\n" + ("x" * 2001), source_label="--prompts-file prompts.txt")

    msg = str(exc.value)
    assert "--prompts-file prompts.txt" in msg
    assert "line 2" in msg
    assert "2000" in msg
```

- [ ] **Step 1.2: Add prompt-file safety tests**

Append:

```python
def test_read_prompt_file_rejects_oversized_file(tmp_path: Path) -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import read_prompt_file

    prompts = tmp_path / "prompts.txt"
    prompts.write_bytes(b"x" * (512 * 1024 + 1))

    with pytest.raises(ConfigurationError, match="512 KiB"):
        read_prompt_file(prompts)


def test_read_prompt_file_rejects_invalid_utf8(tmp_path: Path) -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import read_prompt_file

    prompts = tmp_path / "prompts.txt"
    prompts.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(ConfigurationError, match="UTF-8"):
        read_prompt_file(prompts)


def test_read_prompt_file_uses_basename_in_error(tmp_path: Path) -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import read_prompt_file

    missing = tmp_path / "private" / "prompts.txt"
    with pytest.raises(ConfigurationError) as exc:
        read_prompt_file(missing)

    assert "--prompts-file prompts.txt" in str(exc.value)
    assert str(tmp_path) not in str(exc.value)


def test_read_prompt_file_sanitizes_basename_in_error(tmp_path: Path) -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import read_prompt_file

    weird = tmp_path / "bad[red]\x1b[31m.txt"
    with pytest.raises(ConfigurationError) as exc:
        read_prompt_file(weird)

    msg = str(exc.value)
    assert "\x1b[31m" not in msg
    assert "\\[red]" in msg


def test_read_prompt_file_read_error_uses_basename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.image_batch import read_prompt_file

    prompts = tmp_path / "prompts.txt"
    prompts.write_text("p1\n", encoding="utf-8")

    def _raise(*_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", _raise)
    with pytest.raises(ConfigurationError) as exc:
        read_prompt_file(prompts)

    assert "--prompts-file prompts.txt" in str(exc.value)
    assert "permission denied" not in str(exc.value)
```

- [ ] **Step 1.3: Add CLI preflight tests**

Append:

```python
def _invoke_t2i(args: list[str]):
    from gflow_cli.cli import main

    return CliRunner().invoke(main, ["image", "t2i", *args], catch_exceptions=False)


def test_t2i_rejects_multiple_prompt_sources_before_profile_resolution(tmp_path: Path) -> None:
    prompts = tmp_path / "prompts.txt"
    prompts.write_text("p1\n", encoding="utf-8")

    with patch("gflow_cli.cli_image._resolve_profile") as resolve_profile:
        result = _invoke_t2i(["positional", "--prompts-file", str(prompts)])

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output.lower()
    resolve_profile.assert_not_called()


def test_t2i_rejects_seed_in_multi_prompt_before_profile_resolution() -> None:
    with patch("gflow_cli.cli_image._resolve_profile") as resolve_profile:
        result = _invoke_t2i(["p1", "p2", "--seed", "123"])

    assert result.exit_code == 2
    assert "seed" in result.output.lower()
    resolve_profile.assert_not_called()


def test_t2i_rejects_empty_stdin_before_profile_resolution() -> None:
    with patch("gflow_cli.cli_image._resolve_profile") as resolve_profile:
        result = CliRunner().invoke(
            __import__("gflow_cli.cli").cli.main,
            ["image", "t2i", "--stdin"],
            input="# none\n\n",
            catch_exceptions=False,
        )

    assert result.exit_code == 2
    resolve_profile.assert_not_called()


def test_t2i_rejects_51_positional_prompts_before_profile_and_output_dir() -> None:
    from gflow_cli.cli import main

    with (
        patch("gflow_cli.cli_image._resolve_profile") as resolve_profile,
        patch("gflow_cli.cli_image.resolve_t2i_batch_output_dir") as resolve_output,
    ):
        result = CliRunner().invoke(
            main,
            ["image", "t2i", *[f"p{i}" for i in range(51)]],
            catch_exceptions=False,
        )

    assert result.exit_code == 2
    assert "between 1 and 50" in result.output
    resolve_profile.assert_not_called()
    resolve_output.assert_not_called()


def test_t2i_rejects_long_positional_prompt_before_profile_and_output_dir() -> None:
    from gflow_cli.cli import main

    with (
        patch("gflow_cli.cli_image._resolve_profile") as resolve_profile,
        patch("gflow_cli.cli_image.resolve_t2i_batch_output_dir") as resolve_output,
    ):
        result = CliRunner().invoke(
            main,
            ["image", "t2i", "ok", "x" * 2001],
            catch_exceptions=False,
        )

    assert result.exit_code == 2
    assert "2000" in result.output
    resolve_profile.assert_not_called()
    resolve_output.assert_not_called()


@pytest.mark.parametrize(
    "filename, content, expected",
    [
        ("invalid_utf8.txt", b"\xff\xfe\x00", "UTF-8"),
        ("empty.txt", b"# comment\n\n", "between 1 and 50"),
        ("long.txt", ("x" * 2001).encode("utf-8"), "2000"),
        ("too_many.txt", "\n".join(f"p{i}" for i in range(51)).encode("utf-8"), "between 1 and 50"),
    ],
)
def test_t2i_rejects_invalid_prompt_files_before_profile_and_output_dir(
    tmp_path: Path, filename: str, content: bytes, expected: str
) -> None:
    from gflow_cli.cli import main

    path = tmp_path / filename
    path.write_bytes(content)
    with (
        patch("gflow_cli.cli_image._resolve_profile") as resolve_profile,
        patch("gflow_cli.cli_image.resolve_t2i_batch_output_dir") as resolve_output,
    ):
        result = CliRunner().invoke(
            main,
            ["image", "t2i", "--prompts-file", str(path)],
            catch_exceptions=False,
        )

    assert result.exit_code == 2
    assert expected in result.output
    resolve_profile.assert_not_called()
    resolve_output.assert_not_called()


def test_t2i_rejects_missing_prompt_file_before_profile_and_output_dir(tmp_path: Path) -> None:
    from gflow_cli.cli import main

    missing = tmp_path / "missing.txt"
    with (
        patch("gflow_cli.cli_image._resolve_profile") as resolve_profile,
        patch("gflow_cli.cli_image.resolve_t2i_batch_output_dir") as resolve_output,
    ):
        result = CliRunner().invoke(
            main,
            ["image", "t2i", "--prompts-file", str(missing)],
            catch_exceptions=False,
        )

    assert result.exit_code == 2
    assert "--prompts-file missing.txt" in result.output
    assert str(tmp_path) not in result.output
    resolve_profile.assert_not_called()
    resolve_output.assert_not_called()


def test_t2i_rejects_prompt_file_directory_before_profile_and_output_dir(tmp_path: Path) -> None:
    from gflow_cli.cli import main

    directory = tmp_path / "prompts.txt"
    directory.mkdir()
    with (
        patch("gflow_cli.cli_image._resolve_profile") as resolve_profile,
        patch("gflow_cli.cli_image.resolve_t2i_batch_output_dir") as resolve_output,
    ):
        result = CliRunner().invoke(
            main,
            ["image", "t2i", "--prompts-file", str(directory)],
            catch_exceptions=False,
        )

    assert result.exit_code == 2
    assert "regular file" in result.output
    resolve_profile.assert_not_called()
    resolve_output.assert_not_called()
```

- [ ] **Step 1.4: Add CLI wiring tests for multi-prompt mode**

Append:

```python
def test_t2i_multi_positional_delegates_to_batch_runner(tmp_path: Path) -> None:
    from gflow_cli.cli import main

    async def _fake_run_batch(**_kwargs):
        return []

    out = tmp_path / "out"
    with (
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "profile"),
        patch("gflow_cli.cli_image.run_image_batch", side_effect=_fake_run_batch) as run_batch,
        patch("gflow_cli.cli_image.render_image_batch_summary", return_value=0),
    ):
        result = CliRunner().invoke(
            main,
            ["image", "t2i", "p1", "p2", "p3", "--aspect", "16:9", "--model", "image4", "--out", str(out)],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    kwargs = run_batch.call_args.kwargs
    assert [p.text for p in kwargs["prompts"]] == ["p1", "p2", "p3"]
    assert [p.output_filename for p in kwargs["prompts"]] == ["prompt_0", "prompt_1", "prompt_2"]
    assert all(p.aspect_ratio == "16:9" for p in kwargs["prompts"])
    assert all(p.model == "image4" for p in kwargs["prompts"])
    assert kwargs["output_dir"] == out
    assert kwargs["project_title"] == "gflow-cli t2i"


def test_t2i_multi_prompt_prints_fanout_before_batch_runner(tmp_path: Path) -> None:
    from gflow_cli.cli import main

    async def _fake_run_batch(**_kwargs):
        return []

    with (
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "profile"),
        patch("gflow_cli.cli_image.run_image_batch", side_effect=_fake_run_batch),
        patch("gflow_cli.cli_image.render_image_batch_summary", return_value=0),
    ):
        result = CliRunner().invoke(
            main,
            ["image", "t2i", "p1", "p2", "-n", "4", "--out", str(tmp_path / "out")],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert "up to 8 image(s)" in result.output


def test_t2i_single_prompt_fail_fast_is_inert(tmp_path: Path) -> None:
    from gflow_cli.cli import main

    with (
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "profile"),
        patch("gflow_cli.cli_image._run_t2i") as run_t2i,
        patch("gflow_cli.cli_image.run_image_batch") as run_batch,
    ):
        result = CliRunner().invoke(
            main,
            ["image", "t2i", "one prompt", "--fail-fast"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    run_t2i.assert_called_once()
    run_batch.assert_not_called()
```

- [ ] **Step 1.5: Verify red**

Run:

```bash
uv run pytest tests/cli/test_t2i_multi_prompt.py -q
```

Expected: failures are about missing `gflow_cli.image_batch`, missing `--prompts-file` / `--stdin` / `--fail-fast`, or missing imports in `cli_image`. If any test passes unexpectedly because it is not exercising new behavior, tighten it now.

- [ ] **Step 1.6: Commit the red scaffold**

```bash
git add tests/cli/test_t2i_multi_prompt.py
git commit -m "test(cli): scaffold shell multi-prompt t2i tests"
```

### Task 2: BDD Scenario Scaffold

**Goal:** Add the required user-facing BDD scenarios for shell multi-prompt `t2i`.

**Files:**
- Modify: `tests/features/image.feature`
- Modify: `tests/features/test_image_steps.py`

**Exit Gate:** New BDD scenarios fail because production support is absent, not because step bindings are missing.

**Risks:** Existing image feature steps patch `_run_t2i`; multi-prompt scenarios should patch the new batch runner seam so tests prove the CLI chooses the correct path.

**Estimated effort:** 45 minutes.

- [ ] **Step 2.1: Add scenarios to `tests/features/image.feature`**

Append these scenarios:

```gherkin

  Scenario: Shell multi-prompt positional batch
    Given the mocked t2i batch runner writes one image per prompt
    When I run "gflow image t2i p1 p2 p3 --aspect 16:9 --model image4"
    Then the exit code is 0
    And 3 image files are created
    And every batch prompt used aspect "16:9" and model "image4"

  Scenario: Prompt file skips blanks and comments
    Given a prompt file with 3 valid prompts, 1 blank line, and 1 comment
    And the mocked t2i batch runner writes one image per prompt
    When I run "gflow image t2i --prompts-file prompts.txt"
    Then the exit code is 0
    And 3 image files are created

  Scenario: Multiple prompt sources are rejected
    Given a prompt file with 3 valid prompts, 1 blank line, and 1 comment
    When I run "gflow image t2i p1 --prompts-file prompts.txt"
    Then the exit code is 2
    And the output contains "mutually exclusive"

  Scenario: Stdin prompts use batch path
    Given the mocked t2i batch runner writes one image per prompt
    When I pipe 3 prompts into "gflow image t2i --stdin"
    Then the exit code is 0
    And 3 image files are created

  Scenario: Shell multi-prompt upper bound
    When I run "gflow image t2i" with 51 positional prompts
    Then the exit code is 2
    And the output contains "between 1 and 50"
```

- [ ] **Step 2.2: Add step fixtures for batch state**

In `tests/features/test_image_steps.py`, add:

```python
@pytest.fixture
def batch_state() -> dict[str, Any]:
    return {"prompts": []}
```

- [ ] **Step 2.3: Add batch runner Given step**

Append:

```python
@given("the mocked t2i batch runner writes one image per prompt")
def _mock_t2i_batch_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, batch_state: dict[str, Any]
) -> None:
    from gflow_cli.image_batch import BatchOutcome

    async def _fake_batch(**kwargs: Any) -> list[Any]:
        prompts = list(kwargs["prompts"])
        batch_state["prompts"] = prompts
        output_dir = kwargs["output_dir"]
        output_dir.mkdir(parents=True, exist_ok=True)
        outcomes = []
        for prompt in prompts:
            path = output_dir / f"{prompt.output_filename}_0.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n")
            outcomes.append(
                BatchOutcome(
                    index=prompt.index,
                    prompt=prompt,
                    status="ok",
                    saved_paths=[path],
                    error=None,
                    exit_code=0,
                )
            )
        return outcomes

    monkeypatch.setattr("gflow_cli.cli_image.run_image_batch", _fake_batch)
```

- [ ] **Step 2.4: Add prompt-file Given step**

Append:

```python
@given("a prompt file with 3 valid prompts, 1 blank line, and 1 comment")
def _prompt_file(tmp_path: Path) -> None:
    (tmp_path / "prompts.txt").write_text(
        "p1\n\n# comment\np2\np3\n",
        encoding="utf-8",
    )
```

- [ ] **Step 2.5: Add When steps**

Append:

```python
@when('I run "gflow image t2i p1 p2 p3 --aspect 16:9 --model image4"')
def _run_t2i_three_positional(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(
        main, ["image", "t2i", "p1", "p2", "p3", "--aspect", "16:9", "--model", "image4"]
    )


@when('I run "gflow image t2i --prompts-file prompts.txt"')
def _run_t2i_prompt_file(
    runner: CliRunner, cli_result_holder: dict[str, Any], tmp_path: Path
) -> None:
    cli_result_holder["result"] = runner.invoke(
        main, ["image", "t2i", "--prompts-file", str(tmp_path / "prompts.txt")]
    )


@when('I run "gflow image t2i p1 --prompts-file prompts.txt"')
def _run_t2i_multiple_sources(
    runner: CliRunner, cli_result_holder: dict[str, Any], tmp_path: Path
) -> None:
    cli_result_holder["result"] = runner.invoke(
        main, ["image", "t2i", "p1", "--prompts-file", str(tmp_path / "prompts.txt")]
    )


@when('I pipe 3 prompts into "gflow image t2i --stdin"')
def _run_t2i_stdin(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(
        main, ["image", "t2i", "--stdin"], input="p1\np2\np3\n"
    )


@when('I run "gflow image t2i" with 51 positional prompts')
def _run_t2i_51_prompts(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(
        main, ["image", "t2i", *[f"p{i}" for i in range(51)]]
    )
```

- [ ] **Step 2.6: Add Then steps**

Append:

```python
@then('every batch prompt used aspect "{aspect}" and model "{model}"')
def _check_batch_prompt_options(
    batch_state: dict[str, Any], aspect: str, model: str
) -> None:
    prompts = batch_state["prompts"]
    assert prompts, "batch prompts were not recorded"
    assert all(p.aspect_ratio == aspect for p in prompts)
    assert all(p.model == model for p in prompts)


@then('the output contains "mutually exclusive"')
def _check_mutually_exclusive_output(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "mutually exclusive" in result.output.lower()


@then('the output contains "between 1 and 50"')
def _check_between_1_and_50_output(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "between 1 and 50" in result.output
```

- [ ] **Step 2.7: Verify red**

Run:

```bash
uv run pytest tests/features/test_image_steps.py -q
```

Expected: existing scenarios still pass or collect, new scenarios fail because `gflow_cli.image_batch` or new CLI options are missing.

- [ ] **Step 2.8: Commit the BDD scaffold**

```bash
git add tests/features/image.feature tests/features/test_image_steps.py
git commit -m "test(bdd): scaffold shell multi-prompt t2i scenarios"
```

---

## Phase 2 - Implementation

### Task 3: Extract Shared Image Batch Module

**Goal:** Move batch execution primitives out of `cli_run.py` into `src/gflow_cli/image_batch.py` without changing `gflow run --config` behavior.

**Files:**
- Create: `src/gflow_cli/image_batch.py`
- Modify: `src/gflow_cli/cli_run.py`
- Test: `tests/cli/test_cli_run.py`
- Test: `tests/cli/test_t2i_multi_prompt.py`

**Exit Gate:** `uv run pytest tests/cli/test_cli_run.py tests/cli/test_t2i_multi_prompt.py -q` passes for shared helper tests that do not depend on `cli_image` wiring.

**Risks:** Moving private helpers can break imports or output naming. Keep JSON schema parsing in `cli_run.py`; only move execution/outcome primitives.

**Estimated effort:** 75-90 minutes.

- [ ] **Step 3.1: Implement `src/gflow_cli/image_batch.py`**

Create the module with these public pieces:

```python
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from rich.console import Console
from rich.markup import escape as rich_escape
from rich.table import Table

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.errors import EXIT_CODE_MAP, GFlowError
from gflow_cli.paths import image_output_path

if TYPE_CHECKING:
    from gflow_cli.api.dto import GeneratedImage

PROMPT_FILE_MAX_BYTES = 512 * 1024
MIN_PROMPTS = 1
MAX_PROMPTS = 50
MIN_TEXT_LEN = 1
MAX_TEXT_LEN = 2000
MIN_COUNT = 1
MAX_COUNT = 4

log = structlog.get_logger(__name__)
console = Console()
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class ParsedPrompt:
    text: str
    source_label: str
    line_number: int
    prompt_index: int


@dataclass(frozen=True)
class BatchPromptItem:
    index: int
    text: str
    aspect_ratio: str = "9:16"
    model: str = "nano2"
    count: int = 1
    output_filename: str | None = None
    source_label: str = "prompt"
    line_number: int | None = None


@dataclass
class BatchOutcome:
    index: int
    prompt: BatchPromptItem
    status: str
    saved_paths: list[Path] = field(default_factory=list)
    error: str | None = None
    exit_code: int = 0


def safe_terminal_text(text: str, *, limit: int | None = None) -> str:
    visible = _CONTROL_RE.sub("\uFFFD", text)
    if limit is not None and len(visible) > limit:
        visible = visible[: max(0, limit - 3)] + "..."
    return rich_escape(visible)


def safe_prompt_preview(text: str, *, limit: int = 60) -> str:
    return safe_terminal_text(text, limit=limit)


def resolve_exit_code(exc: GFlowError) -> int:
    for cls, code in EXIT_CODE_MAP.items():
        if isinstance(exc, cls):
            return code
    return 1


def _prompt_config_error(message: str) -> GFlowError:
    from gflow_cli.errors import ConfigurationError

    return ConfigurationError(message)


def _validate_prompt_count(count: int, *, source_label: str) -> None:
    if not (MIN_PROMPTS <= count <= MAX_PROMPTS):
        raise _prompt_config_error(
            f"{source_label}: prompts must have between {MIN_PROMPTS} and "
            f"{MAX_PROMPTS} entries (got {count})."
        )


def prompt_items_from_texts(
    prompts: tuple[str, ...],
    *,
    aspect_ratio: str,
    model: str,
    count: int,
    source_label: str,
) -> tuple[BatchPromptItem, ...]:
    _validate_prompt_count(len(prompts), source_label=source_label)
    for i, text in enumerate(prompts):
        if not (MIN_TEXT_LEN <= len(text) <= MAX_TEXT_LEN):
            raise _prompt_config_error(
                f"{source_label} prompt {i}: prompt length must be between "
                f"{MIN_TEXT_LEN} and {MAX_TEXT_LEN} characters (got {len(text)})."
            )
    return tuple(
        BatchPromptItem(
            index=i,
            text=text,
            aspect_ratio=aspect_ratio,
            model=model,
            count=count,
            output_filename=f"prompt_{i}",
            source_label=source_label,
        )
        for i, text in enumerate(prompts)
    )
```

- [ ] **Step 3.2: Move `_run_batch` equivalent into `image_batch.py`**

Add:

```python
async def run_image_batch(
    *,
    profile_dir: Path,
    headless: bool,
    transport: str | None,
    prompts: tuple[BatchPromptItem, ...],
    output_dir: Path,
    continue_on_error: bool,
    project_title: str,
) -> list[BatchOutcome]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outcomes: list[BatchOutcome] = []
    async with FlowApiClient(
        profile_dir=profile_dir, headless=headless, transport=transport
    ) as client:
        project = await client.create_project(title=project_title)
        log.info("image_batch.project_created", project_id=project.project_id, n_prompts=len(prompts))
        for idx, item in enumerate(prompts):
            outcome = await run_one_image_prompt(
                client=client,
                project_id=project.project_id,
                idx=idx,
                item=item,
                output_dir=output_dir,
            )
            outcomes.append(outcome)
            if outcome.status == "fail" and not continue_on_error:
                for skip_idx in range(idx + 1, len(prompts)):
                    outcomes.append(
                        BatchOutcome(index=skip_idx, prompt=prompts[skip_idx], status="skipped")
                    )
                break
    return outcomes


async def run_one_image_prompt(
    *,
    client: FlowApiClient,
    project_id: str,
    idx: int,
    item: BatchPromptItem,
    output_dir: Path,
) -> BatchOutcome:
    req = GenerateImageRequest(
        prompt=item.text,
        aspect=Aspect.from_cli(item.aspect_ratio),
        model=Model.from_cli(item.model),
    )
    stem = item.output_filename or f"prompt_{idx}"
    try:
        if item.count == 1:
            img = await client.generate_image(project_id=project_id, req=req)
            images: list[GeneratedImage] = [img]
        else:
            images = await client.generate_images_batch(
                project_id=project_id, req=req, count=item.count
            )
        saved: list[Path] = []
        for img_idx, img in enumerate(images):
            target = output_dir / f"{stem}_{img_idx}.png"
            path = await client.download_image(img, target)
            saved.append(path)
        return BatchOutcome(index=idx, prompt=item, status="ok", saved_paths=saved)
    except GFlowError as e:
        return BatchOutcome(
            index=idx,
            prompt=item,
            status="fail",
            error=f"{type(e).__name__}: {e}",
            exit_code=resolve_exit_code(e),
        )
```

- [ ] **Step 3.3: Move summary renderer into `image_batch.py`**

Add:

```python
def render_image_batch_summary(outcomes: list[BatchOutcome], *, title: str) -> int:
    table = Table(title=title)
    table.add_column("#", justify="right")
    table.add_column("prompt", overflow="fold")
    table.add_column("ratio")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    for o in outcomes:
        if o.status == "ok":
            detail = " · ".join(safe_terminal_text(str(p)) for p in o.saved_paths)
            status_str = "[green]OK[/green]"
        elif o.status == "fail":
            detail = safe_terminal_text(o.error or "")
            status_str = "[red]FAIL[/red]"
        else:
            detail = "(not attempted)"
            status_str = "[yellow]SKIPPED[/yellow]"
        table.add_row(
            str(o.index),
            safe_prompt_preview(o.prompt.text),
            o.prompt.aspect_ratio,
            status_str,
            detail,
        )
    console.print(table)
    succeeded = sum(1 for o in outcomes if o.status == "ok")
    failed = sum(1 for o in outcomes if o.status == "fail")
    skipped = sum(1 for o in outcomes if o.status == "skipped")
    console.print(f"\n{succeeded}/{len(outcomes)} succeeded · {failed} failure(s) · {skipped} skipped")
    return max((o.exit_code for o in outcomes), default=0)
```

The shared renderer deliberately makes `gflow run` prompt previews, error
details, and saved paths terminal-safe. It must otherwise preserve the existing
`gflow run` table columns, title when called with `title="gflow run"`,
aggregate counts, skipped semantics, output filenames, and exit-code behavior.

- [ ] **Step 3.4: Add output-dir helper for `t2i` multi-prompt**

Add:

```python
def resolve_t2i_batch_output_dir(*, out: Path | None, output_root: Path) -> Path:
    if out is not None:
        return out
    sample = image_output_path(output_root, job_id="prompt_0", index=0)
    return sample.parent
```

- [ ] **Step 3.5: Update `cli_run.py` imports and adapters**

Modify imports:

```python
from gflow_cli.image_batch import (
    BatchOutcome as _PromptOutcome,
    BatchPromptItem,
    render_image_batch_summary,
    resolve_exit_code as _resolve_exit_code,
    run_image_batch,
)
```

Keep `BatchConfig` in `cli_run.py`. Re-export `BatchPromptItem` by importing it
at module scope so existing internal imports from `gflow_cli.cli_run` continue
to work. Remove the local `_PromptOutcome`, `_run_batch`, `_run_one_prompt`, and
`_render_summary` definitions after adapting call sites.

- [ ] **Step 3.6: Adapt `BatchConfig._parse_prompt` to new `BatchPromptItem`**

When returning from `_parse_prompt`, set `index=idx`:

```python
return BatchPromptItem(
    index=idx,
    text=text_raw,
    aspect_ratio=aspect_ratio,
    model=model,
    count=count,
    output_filename=output_filename,
    source_label="config",
)
```

- [ ] **Step 3.7: Adapt `run()` in `cli_run.py`**

Replace the old `_run_batch` / `_render_summary` calls with:

```python
outcomes = asyncio.run(
    run_image_batch(
        profile_dir=provider_dir,
        headless=settings.headless,
        transport=cfg.transport,
        prompts=cfg.prompts,
        output_dir=output_dir,
        continue_on_error=continue_on_error,
        project_title="gflow-cli run",
    )
)
exit_code = render_image_batch_summary(outcomes, title="gflow run")
```

- [ ] **Step 3.8: Run focused tests**

Run:

```bash
uv run pytest tests/cli/test_cli_run.py tests/cli/test_t2i_multi_prompt.py -q
```

Expected: `test_cli_run.py` passes; parser tests still fail until Task 4; CLI wiring tests still fail until Task 5.

- [ ] **Step 3.9: Run formatting/type checks for touched modules**

Run:

```bash
uv run ruff check src/gflow_cli/image_batch.py src/gflow_cli/cli_run.py tests/cli/test_cli_run.py tests/cli/test_t2i_multi_prompt.py
uv run ruff format --check src/gflow_cli/image_batch.py src/gflow_cli/cli_run.py tests/cli/test_cli_run.py tests/cli/test_t2i_multi_prompt.py
uv run pyright src
```

Expected: all pass or only fail on still-missing Task 4/5 symbols in test modules. Fix production type/lint failures before committing.

- [ ] **Step 3.10: Commit shared extraction**

```bash
git add src/gflow_cli/image_batch.py src/gflow_cli/cli_run.py tests/cli/test_cli_run.py tests/cli/test_t2i_multi_prompt.py
git commit -m "refactor(batch): share image batch execution"
```

### Task 4: Prompt Source Parsing and Validation

**Goal:** Implement safe line parsing, prompt-file reading, display-safe previews, and conversion from parsed prompts to batch items.

**Files:**
- Modify: `src/gflow_cli/image_batch.py`
- Test: `tests/cli/test_t2i_multi_prompt.py`

**Exit Gate:** Parser and prompt-file tests from Task 1 pass.

**Risks:** Reading file content before size/type checks. Validate `Path.is_file()` and `stat().st_size` before `read_text()`.

**Estimated effort:** 45 minutes.

- [ ] **Step 4.1: Implement parser validation in `parse_prompt_lines`**

Add or complete:

```python
def parse_prompt_lines(text: str, *, source_label: str) -> tuple[ParsedPrompt, ...]:
    if text.startswith("\ufeff"):
        text = text.removeprefix("\ufeff")
    parsed: list[ParsedPrompt] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not (MIN_TEXT_LEN <= len(stripped) <= MAX_TEXT_LEN):
            raise _prompt_config_error(
                f"{source_label} line {line_number}: prompt length must be between "
                f"{MIN_TEXT_LEN} and {MAX_TEXT_LEN} characters (got {len(stripped)})."
            )
        parsed.append(
            ParsedPrompt(
                text=stripped,
                source_label=source_label,
                line_number=line_number,
                prompt_index=len(parsed),
            )
        )
    _validate_prompt_count(len(parsed), source_label=source_label)
    return tuple(parsed)
```

- [ ] **Step 4.2: Verify count and error helpers**

Task 3 should already have added these helpers because positional prompt
validation depends on them. Verify they exist in `image_batch.py` before adding
`parse_prompt_lines()`:

```python
def _prompt_config_error(message: str) -> GFlowError:
    from gflow_cli.errors import ConfigurationError

    return ConfigurationError(message)


def _validate_prompt_count(count: int, *, source_label: str) -> None:
    if not (MIN_PROMPTS <= count <= MAX_PROMPTS):
        raise _prompt_config_error(
            f"{source_label}: prompts must have between {MIN_PROMPTS} and "
            f"{MAX_PROMPTS} entries (got {count})."
        )
```

- [ ] **Step 4.3: Implement safe file reader**

Add:

```python
def _prompt_file_label(path: Path) -> str:
    return f"--prompts-file {safe_terminal_text(path.name)}"


def read_prompt_file(path: Path) -> tuple[ParsedPrompt, ...]:
    label = _prompt_file_label(path)
    try:
        stat = path.stat()
    except OSError as exc:
        raise _prompt_config_error(f"{label}: file not found or not readable.") from exc
    if not path.is_file():
        raise _prompt_config_error(f"{label}: must be a regular file.")
    if stat.st_size > PROMPT_FILE_MAX_BYTES:
        raise _prompt_config_error(f"{label}: file exceeds 512 KiB limit.")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise _prompt_config_error(f"{label}: must be valid UTF-8 text.") from exc
    except OSError as exc:
        raise _prompt_config_error(f"{label}: failed to read file.") from exc
    return parse_prompt_lines(text, source_label=label)
```

- [ ] **Step 4.4: Implement parsed prompt conversion**

Add:

```python
def prompt_items_from_parsed(
    prompts: tuple[ParsedPrompt, ...],
    *,
    aspect_ratio: str,
    model: str,
    count: int,
) -> tuple[BatchPromptItem, ...]:
    return tuple(
        BatchPromptItem(
            index=p.prompt_index,
            text=p.text,
            aspect_ratio=aspect_ratio,
            model=model,
            count=count,
            output_filename=f"prompt_{p.prompt_index}",
            source_label=p.source_label,
            line_number=p.line_number,
        )
        for p in prompts
    )
```

- [ ] **Step 4.5: Ensure display-safe preview test covers raw API preservation**

Add this test if not already present:

```python
def test_safe_prompt_preview_escapes_markup_and_controls() -> None:
    from gflow_cli.image_batch import safe_prompt_preview, safe_terminal_text

    preview = safe_prompt_preview("[red]hello[/red]\x1b[31m\r\nnext")
    assert "\\[red]" in preview
    assert "\x1b" not in preview
    assert "\r" not in preview
    assert "\n" not in preview

    path_preview = safe_terminal_text("out/[red]\x1b[31m/file.png")
    assert "\\[red]" in path_preview
    assert "\x1b" not in path_preview
```

- [ ] **Step 4.6: Add summary terminal-safety test**

Append this test to `tests/cli/test_t2i_multi_prompt.py`:

```python
def test_render_summary_escapes_saved_paths_and_errors(capsys: pytest.CaptureFixture[str]) -> None:
    from gflow_cli.image_batch import BatchOutcome, BatchPromptItem, render_image_batch_summary

    item = BatchPromptItem(index=0, text="prompt", output_filename="prompt_0")
    outcomes = [
        BatchOutcome(
            index=0,
            prompt=item,
            status="ok",
            saved_paths=[Path("out/[red]\x1b[31m/file.png")],
        ),
        BatchOutcome(
            index=1,
            prompt=item,
            status="fail",
            error="bad [red]\x1b[31m",
            exit_code=5,
        ),
    ]

    render_image_batch_summary(outcomes, title="test")
    output = capsys.readouterr().out
    assert "\x1b[31m" not in output
    assert "[red]" in output
```

- [ ] **Step 4.7: Add raw prompt preservation test**

Append this test to `tests/cli/test_t2i_multi_prompt.py`:

```python
@pytest.mark.asyncio
async def test_run_one_image_prompt_passes_raw_prompt_to_api(tmp_path: Path) -> None:
    from gflow_cli.api.dto import GeneratedImage
    from gflow_cli.image_batch import BatchPromptItem, run_one_image_prompt, safe_prompt_preview

    raw_prompt = "[red]hello[/red]\x1b[31m\r\nsecond line"
    image = GeneratedImage(
        media_name="m1",
        workflow_id="wf1",
        seed=1,
        prompt=raw_prompt,
        model_name_type="NARWHAL",
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url="https://flow-content.google/x",
        dimensions=(1, 1),
    )
    client = MagicMock()
    client.generate_image = AsyncMock(return_value=image)
    client.download_image = AsyncMock(side_effect=lambda _img, path: path)

    item = BatchPromptItem(index=0, text=raw_prompt, output_filename="prompt_0")
    await run_one_image_prompt(client=client, project_id="proj", idx=0, item=item, output_dir=tmp_path)

    req = client.generate_image.await_args.kwargs["req"]
    assert req.prompt == raw_prompt
    assert safe_prompt_preview(raw_prompt) != raw_prompt
```

- [ ] **Step 4.8: Run focused tests**

Run:

```bash
uv run pytest tests/cli/test_t2i_multi_prompt.py -q
```

Expected: parser/file/preview tests pass; CLI wiring tests may still fail until Task 5.

- [ ] **Step 4.9: Commit parser implementation**

```bash
git add src/gflow_cli/image_batch.py tests/cli/test_t2i_multi_prompt.py
git commit -m "feat(batch): parse shell prompt sources safely"
```

### Task 5: Wire `gflow image t2i` Multi-Prompt Mode and User Docs

**Goal:** Add the new `t2i` input surfaces, route multi-prompt mode through shared batch execution, preserve single-prompt behavior, and update user-facing docs in the same commit.

**Files:**
- Modify: `src/gflow_cli/cli_image.py`
- Modify: `docs/USAGE.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Test: `tests/cli/test_t2i_multi_prompt.py`
- Test: `tests/cli/test_cli_image.py`
- Test: `tests/cli/test_cli_run.py`

**Exit Gate:** CLI unit tests pass, existing `test_cli_image.py` and `test_cli_run.py` pass, docs updated in same commit.

**Risks:** Click variadic argument can consume option-like text if not quoted. Use `nargs=-1` and document quoting requirements.

**Estimated effort:** 90-120 minutes.

- [ ] **Step 5.1: Update imports in `cli_image.py`**

Add:

```python
import asyncio
import sys
```

Add shared imports:

```python
from gflow_cli.errors import ConfigurationError
from gflow_cli.image_batch import (
    parse_prompt_lines,
    prompt_items_from_parsed,
    prompt_items_from_texts,
    read_prompt_file,
    render_image_batch_summary,
    resolve_t2i_batch_output_dir,
    safe_terminal_text,
    run_image_batch,
)
```

- [ ] **Step 5.2: Change `t2i` Click argument/options**

Replace:

```python
@click.argument("prompt")
```

with:

```python
@click.argument("prompts", nargs=-1, required=False)
@click.option(
    "--prompts-file",
    "prompts_file",
    default=None,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Read prompts from a UTF-8 text file: one prompt per non-empty line; whole-line # comments skipped.",
)
@click.option(
    "--stdin",
    "read_stdin",
    is_flag=True,
    help="Read prompts from stdin using the same format as --prompts-file.",
)
@click.option(
    "--continue-on-error/--fail-fast",
    default=True,
    show_default=True,
    help="In multi-prompt mode, continue after per-prompt failures or stop at the first failure.",
)
```

Keep existing options. Ensure options are placed before `def t2i(...)` in Click decorator order.

- [ ] **Step 5.3: Update `t2i` signature**

Use:

```python
def t2i(
    prompts: tuple[str, ...],
    prompts_file: Path | None,
    read_stdin: bool,
    model: str,
    aspect: str,
    count: int,
    seed: int | None,
    out: Path | None,
    profile: str | None,
    transport: str | None,
    continue_on_error: bool,
) -> None:
```

- [ ] **Step 5.4: Add prompt source resolver helper in `cli_image.py`**

Add near the t2i command:

```python
def _count_t2i_sources(
    prompts: tuple[str, ...], prompts_file: Path | None, read_stdin: bool
) -> int:
    return int(bool(prompts)) + int(prompts_file is not None) + int(read_stdin)


def _as_usage_error(exc: ConfigurationError) -> click.UsageError:
    return click.UsageError(str(exc))
```

- [ ] **Step 5.5: Implement preflight branching in `t2i`**

At the start of `t2i`, before `_resolve_profile`, add:

```python
source_count = _count_t2i_sources(prompts, prompts_file, read_stdin)
if source_count == 0:
    raise click.UsageError("Provide a prompt, multiple prompts, --prompts-file, or --stdin.")
if source_count > 1:
    raise click.UsageError(
        "Prompt sources are mutually exclusive: use positional prompts, --prompts-file, or --stdin."
    )

is_multi_prompt = len(prompts) > 1 or prompts_file is not None or read_stdin

if seed is not None and count != 1:
    raise click.UsageError(
        "--seed is only valid when generating a single image (-n 1). "
        "For multi-image runs, omit --seed and let each shot get its own."
    )
if seed is not None and is_multi_prompt:
    raise click.UsageError(
        "--seed is not supported for multi-prompt `gflow image t2i`. "
        "Use one single-prompt command per seed today; per-prompt seeds belong "
        "to a future `gflow run --config` schema update."
    )
```

- [ ] **Step 5.6: Preserve legacy single-prompt path**

For `len(prompts) == 1` and no file/stdin, keep the existing body, replacing `prompt` with `prompts[0]`:

```python
if not is_multi_prompt:
    prompt = prompts[0]
    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_t2i(
            profile_dir=provider_dir,
            headless=settings.headless,
            req=GenerateImageRequest(
                prompt=prompt,
                aspect=Aspect.from_cli(aspect),
                model=Model.from_cli(model),
            ),
            count=count,
            seed=seed,
            out=out,
            output_root=settings.output_dir,
            transport=transport,
        ),
        cli_command="image t2i",
    )
    return
```

- [ ] **Step 5.7: Implement multi-prompt parsing in `t2i`**

After legacy branch:

```python
try:
    if prompts_file is not None:
        parsed = read_prompt_file(prompts_file)
        batch_prompts = prompt_items_from_parsed(
            parsed,
            aspect_ratio=aspect,
            model=model,
            count=count,
        )
    elif read_stdin:
        parsed = parse_prompt_lines(sys.stdin.read(), source_label="--stdin")
        batch_prompts = prompt_items_from_parsed(
            parsed,
            aspect_ratio=aspect,
            model=model,
            count=count,
        )
    else:
        batch_prompts = prompt_items_from_texts(
            prompts,
            aspect_ratio=aspect,
            model=model,
            count=count,
            source_label="positional",
        )
except ConfigurationError as exc:
    raise _as_usage_error(exc) from exc
```

- [ ] **Step 5.8: Implement multi-prompt execution in `t2i`**

Still before any `FlowApiClient` work:

```python
profile_name = _resolve_profile(profile)
provider_dir = _make_provider_dir(profile_name)
settings = get_settings()
output_dir = resolve_t2i_batch_output_dir(out=out, output_root=settings.output_dir)

console.print(
    f"\n[bold]gflow image t2i[/bold] · profile=[bold]{profile_name}[/bold] "
    f"· {len(batch_prompts)} prompt(s) · up to {len(batch_prompts) * count} image(s)"
)
console.print(f"  output_dir: [dim]{safe_terminal_text(str(output_dir))}[/dim]")
if not continue_on_error:
    console.print("  mode: [yellow]fail-fast[/yellow]")

outcomes = asyncio.run(
    run_image_batch(
        profile_dir=provider_dir,
        headless=settings.headless,
        transport=transport,
        prompts=batch_prompts,
        output_dir=output_dir,
        continue_on_error=continue_on_error,
        project_title="gflow-cli t2i",
    )
)
exit_code = render_image_batch_summary(outcomes, title="gflow-cli t2i")
if exit_code != 0:
    sys.exit(exit_code)
```

- [ ] **Step 5.9: Update `docs/USAGE.md` in same commit**

In the `gflow image t2i` section, update synopsis to:

```text
gflow image t2i PROMPT [PROMPT ...] [OPTIONS]
gflow image t2i --prompts-file FILE [OPTIONS]
gflow image t2i --stdin [OPTIONS]
```

Add bullets:

```markdown
Multi-prompt shortcut:

- Positional multi-prompt: `gflow image t2i "p1" "p2" "p3"`.
- `--prompts-file FILE`: UTF-8 text, one prompt per non-empty line, whole-line `#` comments skipped.
- `--stdin`: same format as `--prompts-file`.
- Sources are mutually exclusive.
- Output names use `prompt_<prompt-index>_<variation-index>.png`.
- `--seed` is not supported in multi-prompt mode; use separate single-prompt commands for seeded work today.
- With `-n 4`, each prompt produces four images; the maximum shell shortcut fan-out is 50 prompts * 4 = 200 images.
- `--continue-on-error` is default; `--fail-fast` stops after the first failed prompt.
```

- [ ] **Step 5.10: Update `README.md` and `CHANGELOG.md` in same commit**

README project status table: add a v0.6 row for shell multi-prompt `t2i`.

CHANGELOG `[Unreleased]` add:

```markdown
- Shell-friendly multi-prompt `gflow image t2i`: variadic prompts, `--prompts-file`, and `--stdin`, all reusing one Flow session/project for the batch.
```

- [ ] **Step 5.11: Run focused tests**

Run:

```bash
uv run pytest tests/cli/test_t2i_multi_prompt.py tests/cli/test_cli_image.py tests/cli/test_cli_run.py -q
```

Expected: all pass.

- [ ] **Step 5.12: Run lint/type checks**

Run:

```bash
uv run ruff check src/gflow_cli/cli_image.py src/gflow_cli/cli_run.py src/gflow_cli/image_batch.py tests/cli/test_t2i_multi_prompt.py
uv run ruff format --check src/gflow_cli/cli_image.py src/gflow_cli/cli_run.py src/gflow_cli/image_batch.py tests/cli/test_t2i_multi_prompt.py
uv run pyright src
```

Expected: all pass.

- [ ] **Step 5.13: Commit behavior and docs together**

```bash
git add src/gflow_cli/cli_image.py src/gflow_cli/image_batch.py src/gflow_cli/cli_run.py tests/cli/test_t2i_multi_prompt.py docs/USAGE.md README.md CHANGELOG.md
git commit -m "feat(image): add shell multi-prompt t2i"
```

---

## Phase 3 - Docs, Examples, and BDD Green

### Task 6: Make BDD Scenarios Green

**Goal:** Update image BDD step bindings so the required shell multi-prompt scenarios pass with mocked batch execution.

**Files:**
- Modify: `tests/features/test_image_steps.py`
- Test: `tests/features/test_image_steps.py`

**Exit Gate:** Image feature scenarios pass.

**Risks:** The BDD seam must not start real Playwright. Keep the autouse profile patch and patch `run_image_batch`.

**Estimated effort:** 45 minutes.

- [ ] **Step 6.1: Fix imports after implementation**

Ensure `tests/features/test_image_steps.py` imports `BatchOutcome` only inside functions or from the real module after Task 5 exists.

- [ ] **Step 6.2: Ensure batch stub uses real output directory**

If the Task 2 stub does not match production signature after Task 5, replace it with:

```python
async def _fake_batch(**kwargs: Any) -> list[Any]:
    from gflow_cli.image_batch import BatchOutcome

    prompts = list(kwargs["prompts"])
    batch_state["prompts"] = prompts
    output_dir = kwargs["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    outcomes = []
    for prompt in prompts:
        path = output_dir / f"{prompt.output_filename}_0.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        outcomes.append(BatchOutcome(prompt.index, prompt, "ok", [path], None, 0))
    return outcomes
```

- [ ] **Step 6.3: Run image BDD**

Run:

```bash
uv run pytest tests/features/test_image_steps.py -q
```

Expected: all image scenarios pass.

- [ ] **Step 6.4: Commit BDD green fixes**

```bash
git add tests/features/image.feature tests/features/test_image_steps.py
git commit -m "test(bdd): cover shell multi-prompt t2i"
```

### Task 7: Examples and Documentation Polish

**Goal:** Add runnable examples and polish docs after core behavior is green.

**Files:**
- Create: `examples/sample_prompts.txt`
- Create: `examples/multi_prompt_t2i.py`
- Modify: `examples/README.md`
- Modify: `docs/USAGE.md`
- Modify: `README.md`

**Exit Gate:** Examples are syntactically valid and docs link them.

**Risks:** Examples must not hardcode private profile names or imply billing guarantees.

**Estimated effort:** 45 minutes.

- [ ] **Step 7.1: Add `examples/sample_prompts.txt`**

```text
# One prompt per non-empty line. Whole-line comments are skipped.
a quiet mountain lake at dawn, cinematic photography
a sunlit forest path in autumn, shallow depth of field
a neon-lit market street at night, wide angle
```

- [ ] **Step 7.2: Add `examples/multi_prompt_t2i.py`**

```python
"""Run a shell multi-prompt image batch through the installed gflow CLI.

Usage:
    python examples/multi_prompt_t2i.py --profile <profile-name>
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None)
    parser.add_argument("--out", default="gflow-output/example-multi-prompt")
    args = parser.parse_args()

    prompts_file = Path(__file__).with_name("sample_prompts.txt")
    cmd = [
        "gflow",
        "image",
        "t2i",
        "--prompts-file",
        str(prompts_file),
        "--aspect",
        "9:16",
        "--model",
        "nano2",
        "--out",
        args.out,
    ]
    if args.profile:
        cmd.extend(["--profile", args.profile])
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7.3: Update `examples/README.md`**

Add a row or section:

```markdown
### Multi-prompt `t2i`

```bash
python examples/multi_prompt_t2i.py --profile <your-profile>
```

This uses `examples/sample_prompts.txt`, which follows the same format as
`gflow image t2i --prompts-file`.
```

- [ ] **Step 7.4: Run syntax check**

Run:

```bash
uv run python -m py_compile examples/multi_prompt_t2i.py
```

Expected: exits 0.

- [ ] **Step 7.5: Commit examples**

```bash
git add examples/sample_prompts.txt examples/multi_prompt_t2i.py examples/README.md docs/USAGE.md README.md
git commit -m "docs(examples): add multi-prompt t2i example"
```

---

## Phase 4 - Review, Acceptance, and Ship

### Task 8: Full Quality Gates and Implementation Council Review

**Goal:** Verify all tests and quality gates, then run two implementation council reviews and address findings.

**Files:**
- Create: `docs/superpowers/plans/2026-05-14-shell-multi-prompt/IMPLEMENTATION_REVIEW_PYTHON.md`
- Create: `docs/superpowers/plans/2026-05-14-shell-multi-prompt/IMPLEMENTATION_REVIEW_SECURITY.md`
- Modify as needed based on findings.

**Exit Gate:** Four quality gates pass; both implementation reviews have no outstanding major findings.

**Risks:** Reviewers may find issues requiring code changes. Fix in follow-up atomic commits; do not rewrite history.

**Estimated effort:** 60-120 minutes depending on findings.

- [ ] **Step 8.1: Run full gates**

Run:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run pytest -q --cov=gflow_cli
```

Expected: all pass; coverage remains at or above 80%.

- [ ] **Step 8.2: Dispatch Python implementation reviewer**

Use a worker/reviewer agent with this brief:

```text
Review implementation against docs/superpowers/specs/2026-05-14-shell-multi-prompt-design.md.
Focus: Python idioms, type hints, async correctness, shared image batch extraction,
`gflow run --config` regression risk, and single-prompt behavior preservation.
Write findings to docs/superpowers/plans/2026-05-14-shell-multi-prompt/IMPLEMENTATION_REVIEW_PYTHON.md.
Verdict must be PROCEED-AS-IS / MINOR-EDITS / MAJOR-REVISION.
```

- [ ] **Step 8.3: Dispatch Security implementation reviewer**

Use a second reviewer agent with this brief:

```text
Review implementation against docs/superpowers/specs/2026-05-14-shell-multi-prompt-design.md.
Focus: prompt-file validation, stdin handling, terminal-safe prompt previews,
path disclosure, preflight ordering before browser/API work, and accidental
credit-spend messaging.
Write findings to docs/superpowers/plans/2026-05-14-shell-multi-prompt/IMPLEMENTATION_REVIEW_SECURITY.md.
Verdict must be PROCEED-AS-IS / MINOR-EDITS / MAJOR-REVISION.
```

- [ ] **Step 8.4: Address findings**

For each `MAJOR-REVISION`, make a code/doc fix and run the focused tests named
by the reviewer plus the four gates. For `MINOR-EDITS`, either fix immediately
or record an explicit deferral in the relevant review file.

- [ ] **Step 8.5: Commit review artifacts and fixes**

```bash
git add docs/superpowers/plans/2026-05-14-shell-multi-prompt/IMPLEMENTATION_REVIEW_*.md src tests docs README.md CHANGELOG.md examples
git commit -m "chore(review): apply shell multi-prompt implementation review"
```

### Task 9: Release Prep for v0.6.0a1

**Goal:** Bump version, promote changelog, update marketing notes, tag locally, and leave push/release as the user gate.

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/gflow_cli/__init__.py`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `tmp/marketing.md`

**Exit Gate:** Version is `0.6.0a1`, changelog has `[0.6.0a1] - 2026-05-14`, tag exists locally, full gates pass.

**Risks:** Tagging the wrong commit. Run `git status --short` and full gates before tag.

**Estimated effort:** 45-60 minutes.

- [ ] **Step 9.1: Bump version**

Set:

```toml
version = "0.6.0a1"
```

and:

```python
__version__ = "0.6.0a1"
```

- [ ] **Step 9.2: Promote changelog**

Change `[Unreleased]` feature bullets for shell multi-prompt into:

```markdown
## [0.6.0a1] - 2026-05-14
```

Keep a fresh empty `[Unreleased]` section above it.

- [ ] **Step 9.3: Update `tmp/marketing.md`**

Append concise post copy:

```markdown
## v0.6.0a1 - shell multi-prompt t2i

New in gflow-cli: `gflow image t2i` now accepts multiple prompts directly,
from a text file, or from stdin. It reuses one Flow session/project for the
batch, supports `--fail-fast`, and keeps the richer JSON config path for
per-prompt overrides.
```

- [ ] **Step 9.4: Run full gates**

Run:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run pytest -q --cov=gflow_cli
```

Expected: all pass.

- [ ] **Step 9.5: Commit release prep**

```bash
git add pyproject.toml src/gflow_cli/__init__.py CHANGELOG.md README.md tmp/marketing.md
git commit -m "chore(release): v0.6.0a1"
```

- [ ] **Step 9.6: Tag locally**

```bash
git tag v0.6.0a1
```

- [ ] **Step 9.7: Report release gate to user**

Report:

```text
v0.6.0a1 tag created locally at <sha>.
To publish: git push origin <branch> && git push origin v0.6.0a1
```

---

## Definition of Done

- [ ] Spec v2 is committed and all council review files are present.
- [ ] `gflow image t2i "one prompt"` behavior is unchanged, including seed and output naming.
- [ ] `gflow image t2i "p1" "p2" "p3"` runs as a one-session/one-project batch.
- [ ] `--prompts-file` and `--stdin` use the same safe line parser.
- [ ] Prompt sources are mutually exclusive and all prompt-source validation happens before profile resolution/browser/API work.
- [ ] Multi-prompt output names are `prompt_<prompt-index>_<variation-index>.png`.
- [ ] `--seed` is rejected in multi-prompt mode and preserved in single-prompt mode.
- [ ] `--continue-on-error` / `--fail-fast` match `gflow run` semantics in multi-prompt mode and are inert in single-prompt mode.
- [ ] Existing `gflow run --config` JSON schema and tests are unchanged semantically.
- [ ] Docs describe the feature, seed limitation, file format, output naming, and max 200-image fan-out.
- [ ] Full quality gates pass: ruff, format, pyright, pytest with coverage.
- [ ] Implementation council reviews have no outstanding major findings.
- [ ] Version bumped and local tag `v0.6.0a1` created.

---

## Self-Review

- [x] Plan follows the approved spec v2.
- [x] Every task has files, steps, exit gate, risks, and estimated effort.
- [x] TDD red/green sequence is explicit.
- [x] Docs are updated in the same commit as user-facing CLI behavior.
- [x] No new dependency is introduced.
- [x] No `gflow run --config` schema change is planned.
- [x] No daemon/cross-command session reuse is planned.
- [x] Release remains user-gated after local tag creation.

End of plan.
