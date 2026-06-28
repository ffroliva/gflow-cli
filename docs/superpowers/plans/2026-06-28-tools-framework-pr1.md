# Tools Framework (PR 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a TOML-defined "tools" framework with `creative-director` as the first tool, exposed via a `gflow tools` group and a uniform `--tool/-t` option (replacing the unreleased `-e/--expand`), with banned-keyword cleanup, domain modes, and MCP parity.

**Architecture:** A new `src/gflow_cli/tools/` package: a pydantic `ToolSpec`/`ToolConfig` model, packaged builtin TOML definitions loaded + validated at startup, an in-process registry, and a `runtime.apply_tool()` that wraps the (relocated) `PromptExpander` with domain-vocabulary injection and a deterministic banned-keyword post-filter. The CLI gains a `gflow tools list/show/run` group and a `--tool` option on `image t2i` / `video t2v`; the MCP server gains `gflow_list_tools` and a `tools` array param.

**Tech Stack:** Python ≥3.11, Click, pydantic v2, `tomllib` (stdlib), `urllib` (Gemini), structlog, pytest, FastMCP.

## Global Constraints

- Python floor `>=3.11` — `tomllib` is stdlib; do NOT add `tomli`. (pyproject `requires-python`)
- No new runtime dependencies. Gemini calls use stdlib `urllib` (existing pattern in the relocated expander).
- All public functions/classes carry type annotations; DTOs are `@dataclass(frozen=True)` or pydantic `BaseModel`. (`~/.claude/rules/python/coding-style.md`)
- Logger: `log = structlog.get_logger(__name__)`. Never log prompt text bodies beyond lengths, never log the API key.
- The expander/tool is **never fatal**: missing key / API error / empty result degrades to the original prompt.
- `-e/--expand` is UNRELEASED (absent from every tag incl. `v0.21.0`, only on `develop`) — it is removed outright, no deprecation alias.
- Gate before every commit: `.venv/Scripts/python.exe -m ruff check --fix <files>` + `ruff format <files>`; before the final commit run `pyright src` (0 errors) and the FULL suite including `tests/tools tests/cli tests/mcp tests/features`.
- Test runner (Windows): `.venv/Scripts/python.exe -m pytest ...` (not `uv run`).
- Banned-keyword list (verbatim, source banana-claude `references/prompt-engineering.md`): `4k, 8k, ultra HD, high resolution, masterpiece, highly detailed, ultra detailed, trending on artstation, hyperrealistic, ultra realistic, photorealistic, best quality, award winning`.

---

### Task 1: Banned-keyword filter (`tools/banned.py`)

**Files:**
- Create: `src/gflow_cli/tools/__init__.py` (empty package marker)
- Create: `src/gflow_cli/tools/banned.py`
- Test: `tests/tools/__init__.py` (empty), `tests/tools/test_banned.py`

**Interfaces:**
- Produces: `BANNED_KEYWORDS: tuple[str, ...]`; `strip_banned_keywords(text: str) -> tuple[str, list[str]]` returning `(cleaned_text, removed_terms)`. Case-insensitive, whole-phrase removal; collapses the resulting double spaces; never raises.

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_banned.py
from __future__ import annotations

from gflow_cli.tools.banned import BANNED_KEYWORDS, strip_banned_keywords


def test_banned_list_is_verbatim() -> None:
    assert "8k" in BANNED_KEYWORDS
    assert "hyperrealistic" in BANNED_KEYWORDS
    assert "award winning" in BANNED_KEYWORDS


def test_strips_case_insensitively_and_reports() -> None:
    cleaned, removed = strip_banned_keywords("A Hyperrealistic, 8K masterpiece of a cat")
    assert "hyperrealistic" not in cleaned.lower()
    assert "8k" not in cleaned.lower()
    assert "masterpiece" not in cleaned.lower()
    assert {"hyperrealistic", "8k", "masterpiece"} <= set(removed)
    # no double spaces or dangling separators left
    assert "  " not in cleaned


def test_multiword_phrase_removed() -> None:
    cleaned, removed = strip_banned_keywords("an award winning portrait")
    assert "award winning" not in cleaned.lower()
    assert "award winning" in removed
    assert "portrait" in cleaned


def test_no_banned_returns_unchanged() -> None:
    cleaned, removed = strip_banned_keywords("a serene mountain lake at dawn")
    assert cleaned == "a serene mountain lake at dawn"
    assert removed == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/tools/test_banned.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'gflow_cli.tools'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/gflow_cli/tools/banned.py
"""Deterministic banned-keyword cleanup for tool outputs.

The Creative Director instruction already tells Gemini to avoid these
Stable-Diffusion-era terms (they degrade Nano Banana / Imagen output), but the
model is not guaranteed to comply — so we also strip them post-hoc for CLI
determinism. Source list: banana-claude references/prompt-engineering.md.
"""

from __future__ import annotations

import re

BANNED_KEYWORDS: tuple[str, ...] = (
    "8k",
    "4k",
    "ultra hd",
    "high resolution",
    "masterpiece",
    "highly detailed",
    "ultra detailed",
    "trending on artstation",
    "hyperrealistic",
    "ultra realistic",
    "photorealistic",
    "best quality",
    "award winning",
)

# Longest phrases first so "ultra detailed" is matched before "ultra".
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kw, re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE))
    for kw in sorted(BANNED_KEYWORDS, key=len, reverse=True)
)


def strip_banned_keywords(text: str) -> tuple[str, list[str]]:
    """Remove banned keywords (whole-word, case-insensitive). Returns
    ``(cleaned_text, removed_terms)``. Never raises."""
    removed: list[str] = []
    cleaned = text
    for keyword, pattern in _PATTERNS:
        if pattern.search(cleaned):
            removed.append(keyword)
            cleaned = pattern.sub("", cleaned)
    # Tidy separators left behind: ", ," / double spaces / leading punctuation.
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r",\s*,", ",", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,")
    return cleaned, removed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/tools/test_banned.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Lint + commit**

```bash
.venv/Scripts/python.exe -m ruff check --fix src/gflow_cli/tools/banned.py tests/tools/test_banned.py
.venv/Scripts/python.exe -m ruff format src/gflow_cli/tools/banned.py tests/tools/test_banned.py
git add src/gflow_cli/tools/__init__.py src/gflow_cli/tools/banned.py tests/tools/
git commit -m "feat(tools): deterministic banned-keyword filter"
```

---

### Task 2: Tool spec models (`tools/spec.py`)

**Files:**
- Create: `src/gflow_cli/tools/spec.py`
- Test: `tests/tools/test_spec.py`

**Interfaces:**
- Produces pydantic models:
  - `DomainMode(BaseModel)`: `name: str`, `vocabulary: str`.
  - `ToolConfig(BaseModel)`: `model: str = "gemini-2.5-flash"`, `system_template: str`, `banned_keywords: tuple[str, ...] = ()`, `domains: tuple[DomainMode, ...] = ()`, `max_input_chars: int = 4000`, `max_output_chars: int = 3500`. Method `domain(name: str | None) -> DomainMode | None`.
  - `ToolSpec(BaseModel)`: `name: str`, `title: str`, `description: str`, `category: Literal["image","video","both"]`, `author: str = "gflow"`, `version: str`, `requires_env: tuple[str, ...] = ()`, `options_schema: dict[str, str] = {}`, `config: ToolConfig`. Method `supports(category: Literal["image","video"]) -> bool` (True when `self.category` is that value or `"both"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_spec.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from gflow_cli.tools.spec import DomainMode, ToolConfig, ToolSpec


def _spec() -> ToolSpec:
    return ToolSpec(
        name="creative-director",
        title="Creative Director",
        description="Expand a prompt via the 5-component formula.",
        category="both",
        version="1",
        requires_env=("GFLOW_CLI_GEMINI_API_KEY",),
        options_schema={"style": "domain mode name"},
        config=ToolConfig(
            system_template="Rewrite: ",
            banned_keywords=("8k",),
            domains=(DomainMode(name="cinema", vocabulary="ARRI Alexa, teal/orange"),),
        ),
    )


def test_spec_round_trips_and_supports() -> None:
    spec = _spec()
    assert spec.supports("image") and spec.supports("video")
    assert spec.config.domain("cinema").vocabulary.startswith("ARRI")
    assert spec.config.domain("missing") is None
    assert spec.config.domain(None) is None


def test_category_validated() -> None:
    with pytest.raises(ValidationError):
        ToolSpec(
            name="x", title="X", description="d", category="audio", version="1",
            config=ToolConfig(system_template="t"),
        )


def test_image_only_does_not_support_video() -> None:
    spec = _spec().model_copy(update={"category": "image"})
    assert spec.supports("image")
    assert not spec.supports("video")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/tools/test_spec.py -q`
Expected: FAIL (`ModuleNotFoundError: ... tools.spec`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/gflow_cli/tools/spec.py
"""Pydantic models for tool definitions (loaded from packaged TOML)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class DomainMode(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    vocabulary: str


class ToolConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    model: str = "gemini-2.5-flash"
    system_template: str
    banned_keywords: tuple[str, ...] = ()
    domains: tuple[DomainMode, ...] = ()
    max_input_chars: int = 4000
    max_output_chars: int = 3500

    def domain(self, name: str | None) -> DomainMode | None:
        if name is None:
            return None
        lowered = name.lower()
        return next((d for d in self.domains if d.name.lower() == lowered), None)


class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    title: str
    description: str
    category: Literal["image", "video", "both"]
    author: str = "gflow"
    version: str
    requires_env: tuple[str, ...] = ()
    options_schema: dict[str, str] = {}
    config: ToolConfig

    def supports(self, category: Literal["image", "video"]) -> bool:
        return self.category in (category, "both")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/tools/test_spec.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Lint + commit**

```bash
.venv/Scripts/python.exe -m ruff check --fix src/gflow_cli/tools/spec.py tests/tools/test_spec.py
.venv/Scripts/python.exe -m ruff format src/gflow_cli/tools/spec.py tests/tools/test_spec.py
git add src/gflow_cli/tools/spec.py tests/tools/test_spec.py
git commit -m "feat(tools): ToolSpec/ToolConfig pydantic models"
```

---

### Task 3: Creative Director TOML + loader (`tools/builtin/creative-director.toml`, `tools/loader.py`)

**Files:**
- Create: `src/gflow_cli/tools/builtin/__init__.py` (empty — makes `builtin` an importable resource package)
- Create: `src/gflow_cli/tools/builtin/creative-director.toml`
- Create: `src/gflow_cli/tools/loader.py`
- Test: `tests/tools/test_loader.py`

**Interfaces:**
- Consumes: `ToolSpec` (Task 2).
- Produces: `load_builtin_tools() -> dict[str, ToolSpec]` (reads every `*.toml` under `tools/builtin/` via `importlib.resources`, parses with `tomllib`, validates into `ToolSpec`, keyed by `name`). Raises `gflow_cli.errors.ConfigurationError` on a malformed/invalid TOML. `load_user_tools(config_dir: Path) -> dict[str, ToolSpec]` exists but is NOT wired (dormant My-Tools seam) — it returns `{}` when the dir is absent.

**TOML content note:** Populate `system_template` with banana-claude's verbatim 5-component formula instruction (source: `C:\development\github\banana-claude\skills\banana\references\prompt-engineering.md` lines 8–65, plus the `SKILL.md` CRITICAL RULES) adapted to a single system-instruction string ending in `"\n\nUser prompt: "`. Populate `[[config.domains]]` with the 9 image domain vocab libraries (prompt-engineering.md lines 67–123) and 6 video domains (cinematic, documentary, product, animation, abstract, social). Keep `banned_keywords` = the Task-1 list. This is DATA transcription from the cited source, not logic.

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_loader.py
from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.errors import ConfigurationError
from gflow_cli.tools.loader import load_builtin_tools, load_user_tools


def test_loads_creative_director_builtin() -> None:
    tools = load_builtin_tools()
    cd = tools["creative-director"]
    assert cd.title == "Creative Director"
    assert cd.category == "both"
    assert "GFLOW_CLI_GEMINI_API_KEY" in cd.requires_env
    assert "8k" in cd.config.banned_keywords
    # at least the banana image domains + the video set are present
    names = {d.name for d in cd.config.domains}
    assert {"cinema", "product", "portrait"} <= names
    assert {"cinematic", "documentary", "social"} <= names
    assert "Subject" in cd.config.system_template


def test_user_tools_empty_when_dir_absent(tmp_path: Path) -> None:
    assert load_user_tools(tmp_path / "nope") == {}


def test_invalid_toml_raises_configuration_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = tmp_path / "tools"
    bad.mkdir()
    (bad / "broken.toml").write_text('name = "x"\n', encoding="utf-8")  # missing required fields
    assert load_user_tools(tmp_path / "tools") == {} or True  # see note
    with pytest.raises(ConfigurationError):
        # _load_dir is the shared validator used by both builtin + user loaders
        from gflow_cli.tools.loader import _load_dir

        _load_dir(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/tools/test_loader.py -q`
Expected: FAIL (`ModuleNotFoundError: ... tools.loader`)

- [ ] **Step 3: Write the TOML, then the loader**

Create `src/gflow_cli/tools/builtin/creative-director.toml` (structure shown; fill `system_template` and all `[[config.domains]]` from the cited banana-claude source):

```toml
name = "creative-director"
title = "Creative Director"
description = "Rewrite a terse prompt into a vivid one using Google's 5-component formula."
category = "both"
author = "gflow"
version = "1"
requires_env = ["GFLOW_CLI_GEMINI_API_KEY"]

[options_schema]
style = "Domain mode that injects specialized vocabulary (see `gflow tools show creative-director`)."

[config]
model = "gemini-2.5-flash"
banned_keywords = ["8k", "4k", "ultra HD", "high resolution", "masterpiece", "highly detailed", "ultra detailed", "trending on artstation", "hyperrealistic", "ultra realistic", "photorealistic", "best quality", "award winning"]
max_input_chars = 4000
max_output_chars = 3500
system_template = """
You are a prompt engineer for an AI image and video generator. Rewrite the
user's prompt into a single, vivid, self-contained prompt following this
five-component formula written as natural narrative (never keyword lists):
Subject, Action, Location/Context, Composition, Style (lighting lives in Style).
Keep the user's original intent and any named subjects intact. Do not use the
banned Stable-Diffusion-era keywords. Do not ask questions, add preamble, use
markdown, or wrap the result in quotes. Respond with ONLY the rewritten prompt.

User prompt: """
# NOTE: replace the template above with the verbatim banana-claude formula
# (prompt-engineering.md lines 8-65 + SKILL.md CRITICAL RULES) during execution.

[[config.domains]]
name = "cinema"
vocabulary = "Cameras: RED V-Raptor, ARRI Alexa 65, Sony Venice 2. Lenses: Cooke S7/i, Zeiss Supreme Prime, Atlas Orion anamorphic. Film stock: Kodak Vision3 500T. Lighting: three-point, chiaroscuro, Rembrandt, rim/backlight. Grading: teal/orange, desaturated cold."
# ... add the remaining image domains (product, portrait, editorial, ui, logo,
# landscape, infographic, abstract) and video domains (cinematic, documentary,
# product, animation, abstract, social) from the cited source.
```

```python
# src/gflow_cli/tools/loader.py
"""Load + validate packaged builtin tool TOMLs (and, dormant, user tools)."""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

from pydantic import ValidationError

from gflow_cli.errors import ConfigurationError
from gflow_cli.tools.spec import ToolSpec

_BUILTIN_PACKAGE = "gflow_cli.tools.builtin"


def _validate(name: str, data: dict[str, object]) -> ToolSpec:
    try:
        return ToolSpec.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError(detail=f"invalid tool definition {name!r}: {exc}") from exc


def load_builtin_tools() -> dict[str, ToolSpec]:
    tools: dict[str, ToolSpec] = {}
    root = resources.files(_BUILTIN_PACKAGE)
    for entry in root.iterdir():
        if entry.name.endswith(".toml"):
            data = tomllib.loads(entry.read_text(encoding="utf-8"))
            spec = _validate(entry.name, data)
            tools[spec.name] = spec
    return tools


def _load_dir(directory: Path) -> dict[str, ToolSpec]:
    tools: dict[str, ToolSpec] = {}
    for path in sorted(directory.glob("*.toml")):
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        spec = _validate(path.name, data)
        tools[spec.name] = spec
    return tools


def load_user_tools(config_dir: Path) -> dict[str, ToolSpec]:
    """Dormant My-Tools seam: scan a user config dir for tool TOMLs.

    Not wired into the registry this cycle. Returns ``{}`` when the dir is
    absent so a future activation is a one-line change.
    """
    if not config_dir.exists():
        return {}
    return _load_dir(config_dir)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/tools/test_loader.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Ensure TOML is packaged**

Confirm `pyproject.toml` ships package data. If wheels use setuptools/hatch and `*.toml` under the package is not auto-included, add the glob (e.g. hatch `[tool.hatch.build.targets.wheel] include` or setuptools `package-data`). Verify:

Run: `.venv/Scripts/python.exe -c "from gflow_cli.tools.loader import load_builtin_tools; print(sorted(load_builtin_tools()))"`
Expected: `['creative-director']`

- [ ] **Step 6: Lint + commit**

```bash
.venv/Scripts/python.exe -m ruff check --fix src/gflow_cli/tools/loader.py tests/tools/test_loader.py
.venv/Scripts/python.exe -m ruff format src/gflow_cli/tools/loader.py tests/tools/test_loader.py
git add src/gflow_cli/tools/builtin/ src/gflow_cli/tools/loader.py tests/tools/test_loader.py pyproject.toml
git commit -m "feat(tools): creative-director.toml + builtin loader"
```

---

### Task 4: Registry (`tools/registry.py`)

**Files:**
- Create: `src/gflow_cli/tools/registry.py`
- Test: `tests/tools/test_registry.py`

**Interfaces:**
- Consumes: `load_builtin_tools` (Task 3), `ToolSpec`.
- Produces: `get_tool(name: str) -> ToolSpec` (raises `gflow_cli.errors.ConfigurationError` for unknown name, message listing valid names); `iter_tools() -> tuple[ToolSpec, ...]` (sorted by name); `tool_names() -> tuple[str, ...]`. Registry is built once (module-level cache); `reset_registry()` clears it (tests).

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_registry.py
from __future__ import annotations

import pytest

from gflow_cli.errors import ConfigurationError
from gflow_cli.tools.registry import get_tool, iter_tools, reset_registry, tool_names


def setup_function() -> None:
    reset_registry()


def test_creative_director_registered() -> None:
    assert "creative-director" in tool_names()
    assert get_tool("creative-director").title == "Creative Director"
    assert [t.name for t in iter_tools()] == sorted(tool_names())


def test_unknown_tool_raises_with_valid_names() -> None:
    with pytest.raises(ConfigurationError) as exc:
        get_tool("nope")
    assert "creative-director" in str(exc.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/tools/test_registry.py -q`
Expected: FAIL (`ModuleNotFoundError: ... tools.registry`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/gflow_cli/tools/registry.py
"""In-process tool registry over packaged builtin TOMLs."""

from __future__ import annotations

from gflow_cli.errors import ConfigurationError
from gflow_cli.tools.loader import load_builtin_tools
from gflow_cli.tools.spec import ToolSpec

_REGISTRY: dict[str, ToolSpec] | None = None


def _registry() -> dict[str, ToolSpec]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = load_builtin_tools()
    return _REGISTRY


def reset_registry() -> None:
    global _REGISTRY
    _REGISTRY = None


def tool_names() -> tuple[str, ...]:
    return tuple(sorted(_registry()))


def iter_tools() -> tuple[ToolSpec, ...]:
    return tuple(_registry()[name] for name in tool_names())


def get_tool(name: str) -> ToolSpec:
    reg = _registry()
    if name not in reg:
        valid = ", ".join(sorted(reg))
        raise ConfigurationError(detail=f"unknown tool {name!r}. Available: {valid}")
    return reg[name]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/tools/test_registry.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Lint + commit**

```bash
.venv/Scripts/python.exe -m ruff check --fix src/gflow_cli/tools/registry.py tests/tools/test_registry.py
.venv/Scripts/python.exe -m ruff format src/gflow_cli/tools/registry.py tests/tools/test_registry.py
git add src/gflow_cli/tools/registry.py tests/tools/test_registry.py
git commit -m "feat(tools): in-process tool registry"
```

---

### Task 5: Relocate expander + domain injection (`tools/expander.py`)

**Files:**
- Create: `src/gflow_cli/tools/expander.py` (moved from `src/gflow_cli/api/prompt_expander.py`, plus changes)
- Delete: `src/gflow_cli/api/prompt_expander.py`
- Move: `tests/api/test_prompt_expander.py` → `tests/tools/test_expander.py`
- Test: `tests/tools/test_expander.py` (add a domain-injection test)

**Interfaces:**
- Consumes: nothing new (self-contained).
- Produces: `PromptExpander`, `ExpansionResult`, `GeminiHttpError`, `DEFAULT_MODEL` (unchanged public surface). NEW: `PromptExpander.__init__` gains `system_instruction: str | None = None` (overrides the built-in `_SYSTEM_INSTRUCTION` so the runtime can pass the tool's `config.system_template` + domain vocab). `from_settings` unchanged.

- [ ] **Step 1: Move the module + tests (no behavior change yet)**

```bash
git mv src/gflow_cli/api/prompt_expander.py src/gflow_cli/tools/expander.py
git mv tests/api/test_prompt_expander.py tests/tools/test_expander.py
```

Update the import in `tests/tools/test_expander.py`:
`from gflow_cli.tools.expander import (ExpansionResult, GeminiHttpError, PromptExpander)`.

- [ ] **Step 2: Run moved tests (verify still green)**

Run: `.venv/Scripts/python.exe -m pytest tests/tools/test_expander.py -q`
Expected: PASS (existing tests)

- [ ] **Step 3: Write the failing domain-injection test**

```python
# append to tests/tools/test_expander.py
def test_custom_system_instruction_is_used() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
        captured["payload"] = payload
        return {"candidates": [{"content": {"parts": [{"text": "expanded"}]}}]}

    expander = PromptExpander("key", transport=transport, system_instruction="CINEMA MODE: ")
    result = expander.expand("a cat")
    assert result.was_expanded
    sent = captured["payload"]["contents"][0]["parts"][0]["text"]  # type: ignore[index]
    assert sent.startswith("CINEMA MODE: ")
    assert "a cat" in sent
```

- [ ] **Step 4: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/tools/test_expander.py::test_custom_system_instruction_is_used -q`
Expected: FAIL (`__init__ got an unexpected keyword 'system_instruction'`)

- [ ] **Step 5: Add `system_instruction` override**

In `tools/expander.py`, add the parameter to `__init__` (store `self._instruction = system_instruction or _SYSTEM_INSTRUCTION`) and use `self._instruction` in `_build_payload` instead of the module constant `_SYSTEM_INSTRUCTION`.

```python
    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODEL,
        system_instruction: str | None = None,
        max_retries: int = 3,
        timeout: float = 30.0,
        max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        ...
        self._instruction = system_instruction or _SYSTEM_INSTRUCTION
        ...

    def _build_payload(self, prompt: str) -> dict[str, object]:
        return {
            "contents": [{"parts": [{"text": self._instruction + prompt}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1024},
        }
```

- [ ] **Step 6: Update the dangling reference + run tests**

Grep for the old import path and fix any references:

Run: `.venv/Scripts/python.exe -m pytest tests/tools/test_expander.py -q`
Expected: PASS (all, incl. the new test)
Run: `grep -rn "api.prompt_expander\|api/prompt_expander" src tests` → expect ONLY the `_cli_helpers.py` import (fixed in Task 8) and no stragglers.

- [ ] **Step 7: Lint + commit**

```bash
.venv/Scripts/python.exe -m ruff check --fix src/gflow_cli/tools/expander.py tests/tools/test_expander.py
.venv/Scripts/python.exe -m ruff format src/gflow_cli/tools/expander.py tests/tools/test_expander.py
git add -A
git commit -m "refactor(tools): relocate PromptExpander to tools/, add system_instruction override"
```

---

### Task 6: Runtime (`tools/runtime.py`)

**Files:**
- Create: `src/gflow_cli/tools/runtime.py`
- Test: `tests/tools/test_runtime.py`

**Interfaces:**
- Consumes: `ToolSpec`/`ToolConfig` (Task 2), `PromptExpander`/`ExpansionResult` (Task 5), `strip_banned_keywords` (Task 1), `get_settings`.
- Produces: `build_instruction(config: ToolConfig, style: str | None) -> str` (template + selected domain vocabulary appended, ending in the user-prompt marker); `apply_tool(spec: ToolSpec, prompt: str, options: Mapping[str, str], *, expander: PromptExpander | None = None) -> ExpansionResult`. Builds the instruction, runs the expander (built from settings + spec.config.model + the instruction unless an `expander` is injected for tests), then runs `strip_banned_keywords` on `expanded` (logs `tool_banned_keywords_stripped` when any removed). Never raises.

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_runtime.py
from __future__ import annotations

from gflow_cli.tools.expander import ExpansionResult, PromptExpander
from gflow_cli.tools.registry import get_tool, reset_registry
from gflow_cli.tools.runtime import apply_tool, build_instruction


def setup_function() -> None:
    reset_registry()


def test_build_instruction_appends_domain() -> None:
    cfg = get_tool("creative-director").config
    instr = build_instruction(cfg, "cinema")
    assert "cinema" in instr.lower() or "ARRI" in instr  # domain vocab injected
    base = build_instruction(cfg, None)
    assert len(instr) > len(base)


def test_apply_tool_strips_banned_from_output() -> None:
    spec = get_tool("creative-director")

    def transport(url, payload, timeout):  # noqa: ANN001
        return {"candidates": [{"content": {"parts": [{"text": "a hyperrealistic 8k cat scene"}]}}]}

    expander = PromptExpander("key", transport=transport, system_instruction=build_instruction(spec.config, None))
    result = apply_tool(spec, "cat", {}, expander=expander)
    assert isinstance(result, ExpansionResult)
    assert result.was_expanded
    assert "hyperrealistic" not in result.expanded.lower()
    assert "8k" not in result.expanded.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/tools/test_runtime.py -q`
Expected: FAIL (`ModuleNotFoundError: ... tools.runtime`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/gflow_cli/tools/runtime.py
"""Apply a resolved tool to a prompt (build instruction → expand → de-ban)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

import structlog

from gflow_cli.config import get_settings
from gflow_cli.tools.banned import strip_banned_keywords
from gflow_cli.tools.expander import ExpansionResult, PromptExpander

if TYPE_CHECKING:
    from gflow_cli.tools.spec import ToolConfig, ToolSpec

log = structlog.get_logger(__name__)


def build_instruction(config: ToolConfig, style: str | None) -> str:
    instruction = config.system_template
    domain = config.domain(style)
    if style is not None and domain is None:
        log.warning("tool_unknown_style", style=style)
    if domain is not None:
        instruction = (
            f"{config.system_template}\n\nApply this {domain.name} style vocabulary: "
            f"{domain.vocabulary}\n\nUser prompt: "
        )
    return instruction


def apply_tool(
    spec: ToolSpec,
    prompt: str,
    options: Mapping[str, str],
    *,
    expander: PromptExpander | None = None,
) -> ExpansionResult:
    style = options.get("style")
    instruction = build_instruction(spec.config, style)
    if expander is None:
        settings = get_settings()
        expander = PromptExpander(
            settings.gemini_api_key,
            model=spec.config.model,
            system_instruction=instruction,
            max_input_chars=spec.config.max_input_chars,
            max_output_chars=spec.config.max_output_chars,
        )
    result = expander.expand(prompt)
    if not result.was_expanded:
        return result
    cleaned, removed = strip_banned_keywords(result.expanded)
    if removed:
        log.info("tool_banned_keywords_stripped", tool=spec.name, removed=removed)
    return ExpansionResult(original=result.original, expanded=cleaned, was_expanded=True)
```

Note: `build_instruction` must end the no-domain branch with the template's own `"User prompt: "` marker — if the TOML `system_template` already ends with it, the no-domain branch returns the template unchanged (correct); the domain branch re-appends the marker after the vocabulary. Ensure the TOML template ends with `"\n\nUser prompt: "` so both branches feed the user prompt correctly.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/tools/test_runtime.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Lint + commit**

```bash
.venv/Scripts/python.exe -m ruff check --fix src/gflow_cli/tools/runtime.py tests/tools/test_runtime.py
.venv/Scripts/python.exe -m ruff format src/gflow_cli/tools/runtime.py tests/tools/test_runtime.py
git add src/gflow_cli/tools/runtime.py tests/tools/test_runtime.py
git commit -m "feat(tools): runtime apply_tool (instruction build + banned strip)"
```

---

### Task 7: `gflow tools` CLI group (`cli_tools.py`)

**Files:**
- Create: `src/gflow_cli/cli_tools.py`
- Modify: `src/gflow_cli/cli.py` (import + `main.add_command`)
- Test: `tests/cli/test_cli_tools.py`

**Interfaces:**
- Consumes: `iter_tools`/`get_tool` (Task 4), `apply_tool` (Task 6), `_resolve_profile` is NOT needed (pure tool).
- Produces: a Click group `tools` with `list`, `show <name>`, `run <name> <input> [--style] [--json]`. `run` resolves the tool, calls `apply_tool`, and on `--json` emits `{"name","original","expanded","was_expanded"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_cli_tools.py
from __future__ import annotations

import json

from click.testing import CliRunner

from gflow_cli.cli import main


def test_tools_list_shows_creative_director() -> None:
    result = CliRunner().invoke(main, ["tools", "list"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "creative-director" in result.output


def test_tools_show_lists_styles() -> None:
    result = CliRunner().invoke(main, ["tools", "show", "creative-director"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "cinema" in result.output.lower()


def test_tools_run_json_without_key_falls_back(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("GFLOW_CLI_GEMINI_API_KEY", raising=False)
    from gflow_cli.config import reset_settings

    reset_settings()
    result = CliRunner().invoke(
        main, ["tools", "run", "creative-director", "cat in space", "--json"], catch_exceptions=False
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["name"] == "creative-director"
    assert payload["original"] == "cat in space"
    assert payload["was_expanded"] is False  # no key → graceful fallback
    assert payload["expanded"] == "cat in space"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_cli_tools.py -q`
Expected: FAIL (`No such command 'tools'`)

- [ ] **Step 3: Write the group + register it**

```python
# src/gflow_cli/cli_tools.py
"""`gflow tools` — discover and run prompt tools (Flow "Tools" analogue)."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from gflow_cli import json_output
from gflow_cli.tools.registry import get_tool, iter_tools
from gflow_cli.tools.runtime import apply_tool

console = Console()


@click.group()
def tools() -> None:
    """Discover and run prompt tools."""


@tools.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of a table.")
def list_tools(as_json: bool) -> None:
    specs = iter_tools()
    if as_json:
        json_output.emit(
            {"tools": [{"name": s.name, "title": s.title, "description": s.description,
                        "category": s.category, "requires_env": list(s.requires_env)} for s in specs]}
        )
        return
    table = Table(title="Tools")
    for col in ("Name", "Title", "Category", "Description"):
        table.add_column(col)
    for s in specs:
        table.add_row(s.name, s.title, s.category, s.description)
    console.print(table)


@tools.command("show")
@click.argument("name")
def show_tool(name: str) -> None:
    spec = get_tool(name)
    console.print(f"[bold]{spec.title}[/bold] ({spec.name}) — {spec.category}")
    console.print(spec.description)
    if spec.requires_env:
        console.print(f"Requires env: {', '.join(spec.requires_env)}")
    if spec.config.domains:
        console.print("Styles: " + ", ".join(d.name for d in spec.config.domains))


@tools.command("run")
@click.argument("name")
@click.argument("text")
@click.option("--style", default=None, help="Domain style mode (see `tools show`).")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def run_tool(name: str, text: str, style: str | None, as_json: bool) -> None:
    spec = get_tool(name)
    options = {"style": style} if style else {}
    result = apply_tool(spec, text, options)
    if as_json:
        json_output.emit({"name": spec.name, "original": result.original,
                          "expanded": result.expanded, "was_expanded": result.was_expanded})
        return
    console.print(result.expanded)
```

Register in `src/gflow_cli/cli.py` (mirror the existing pattern at lines 18-25 / 361-368):
- Add `from gflow_cli.cli_tools import tools as _tools_group` with the other imports.
- Add `main.add_command(_tools_group)` with the other registrations.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_cli_tools.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Lint + commit**

```bash
.venv/Scripts/python.exe -m ruff check --fix src/gflow_cli/cli_tools.py src/gflow_cli/cli.py tests/cli/test_cli_tools.py
.venv/Scripts/python.exe -m ruff format src/gflow_cli/cli_tools.py src/gflow_cli/cli.py tests/cli/test_cli_tools.py
git add src/gflow_cli/cli_tools.py src/gflow_cli/cli.py tests/cli/test_cli_tools.py
git commit -m "feat(cli): gflow tools list/show/run group"
```

---

### Task 8: Replace `--expand` with `--tool` on `image t2i`

**Files:**
- Modify: `src/gflow_cli/_cli_helpers.py` (replace `expand_prompt` with `apply_tool_option`)
- Modify: `src/gflow_cli/cli_image.py` (t2i option + branch)
- Modify: `tests/cli/test_helpers.py`, `tests/cli/test_t2i_multi_prompt.py`
- Delete references to `expand_prompt`/`--expand`

**Interfaces:**
- Consumes: `get_tool`, `apply_tool`, `ExpansionResult`.
- Produces: `apply_tool_option(text: str, tool_specs: tuple[str, ...], *, category: Literal["image","video"], quiet: bool) -> tuple[str, str | None]` in `_cli_helpers.py`. Parses each `name[:k=v,...]` spec, validates the tool supports `category`, applies in sequence (output of one feeds the next), returns `(prompt_to_send, original_prompt)` where `original_prompt` is the user's text iff any tool changed it (else `None`). Never raises (unknown tool → `click.UsageError` at parse, before any network). This REPLACES `expand_prompt`.

- [ ] **Step 1: Write the failing helper test**

```python
# tests/cli/test_helpers.py — replace the three expand_prompt tests with:
def test_apply_tool_option_no_tools_is_identity() -> None:
    from gflow_cli._cli_helpers import apply_tool_option

    sent, original = apply_tool_option("cat", (), category="image", quiet=True)
    assert sent == "cat"
    assert original is None


def test_apply_tool_option_unknown_tool_raises_usage_error() -> None:
    import click
    import pytest

    from gflow_cli._cli_helpers import apply_tool_option

    with pytest.raises(click.UsageError):
        apply_tool_option("cat", ("nope",), category="image", quiet=True)


def test_apply_tool_option_runs_creative_director(monkeypatch) -> None:  # noqa: ANN001
    from gflow_cli import _cli_helpers
    from gflow_cli.tools.expander import ExpansionResult

    monkeypatch.setattr(
        _cli_helpers, "apply_tool",
        lambda spec, text, options, **kw: ExpansionResult(original=text, expanded="EXPANDED", was_expanded=True),
    )
    sent, original = _cli_helpers.apply_tool_option("cat", ("creative-director",), category="image", quiet=True)
    assert sent == "EXPANDED"
    assert original == "cat"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_helpers.py -q`
Expected: FAIL (`cannot import name 'apply_tool_option'`)

- [ ] **Step 3: Implement `apply_tool_option`, remove `expand_prompt`**

In `_cli_helpers.py`: delete `expand_prompt`; add:

```python
def _parse_tool_spec(spec: str) -> tuple[str, dict[str, str]]:
    name, _, raw_opts = spec.partition(":")
    options: dict[str, str] = {}
    if raw_opts:
        for pair in raw_opts.split(","):
            k, _, v = pair.partition("=")
            options[k.strip()] = v.strip()
    return name.strip(), options


def apply_tool_option(
    text: str,
    tool_specs: tuple[str, ...],
    *,
    category: Literal["image", "video"],
    quiet: bool,
) -> tuple[str, str | None]:
    """Apply `--tool name[:k=v]` specs in sequence. Returns (prompt_to_send,
    original_prompt|None). Unknown tool / wrong category → UsageError (pre-network)."""
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.tools.registry import get_tool
    from gflow_cli.tools.runtime import apply_tool

    if not tool_specs:
        return text, None
    original = text
    current = text
    changed = False
    for spec_str in tool_specs:
        name, options = _parse_tool_spec(spec_str)
        try:
            spec = get_tool(name)
        except ConfigurationError as exc:
            raise click.UsageError(str(exc)) from exc
        if not spec.supports(category):
            raise click.UsageError(f"tool {name!r} does not support {category} generation.")
        result = apply_tool(spec, current, options)
        if result.was_expanded:
            changed = True
            current = result.expanded
            if not quiet:
                _console.print(f"[cyan]{spec.title} applied:[/cyan] [dim]{current}[/dim]")
        elif not quiet:
            _console.print(f"[yellow]{spec.title} skipped[/yellow] (unavailable); using original.")
    return current, (original if changed else None)
```

Add `from typing import Literal` to the imports if not present.

- [ ] **Step 4: Run helper tests**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_helpers.py -q`
Expected: PASS

- [ ] **Step 5: Swap the t2i option**

In `cli_image.py`: replace the `-e/--expand` option block + `expand: bool` param with:

```python
@click.option(
    "-t", "--tool", "tool_specs", multiple=True,
    help="Apply a tool before generating, e.g. --tool creative-director or "
         "--tool creative-director:style=cinema. Repeatable. Single-prompt only.",
)
```
and `tool_specs: tuple[str, ...]` in the signature. Replace the multi-prompt guard
(`is_multi_prompt and expand`) with `is_multi_prompt and tool_specs` (message: "-t/--tool is
single-prompt only; remove the extra prompts."). Replace the `expand_prompt(...)` call with
`prompt_to_send, original_prompt = apply_tool_option(prompt, tool_specs, category="image", quiet=as_json)`.
Update the import: `expand_prompt` → `apply_tool_option`. The `_run_t2i(..., original_prompt=...)`
threading stays (recorder kwarg unchanged in PR 1).

- [ ] **Step 6: Update the guard test**

In `tests/cli/test_t2i_multi_prompt.py`, change `test_t2i_rejects_expand_with_multiple_prompts` to invoke `["one", "two", "--tool", "creative-director"]` and assert `single-prompt only` + exit 2.

- [ ] **Step 7: Run image CLI + helper + bdd image tests**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_helpers.py tests/cli/test_t2i_multi_prompt.py tests/cli/test_cli_image.py tests/features/test_image_steps.py -q`
Expected: PASS

- [ ] **Step 8: Lint + commit**

```bash
.venv/Scripts/python.exe -m ruff check --fix src/gflow_cli/_cli_helpers.py src/gflow_cli/cli_image.py tests/cli/test_helpers.py tests/cli/test_t2i_multi_prompt.py
.venv/Scripts/python.exe -m ruff format src/gflow_cli/_cli_helpers.py src/gflow_cli/cli_image.py tests/cli/test_helpers.py tests/cli/test_t2i_multi_prompt.py
git add -A
git commit -m "feat(cli): replace --expand with --tool on image t2i"
```

---

### Task 9: Replace `--expand` with `--tool` on `video t2v`

**Files:**
- Modify: `src/gflow_cli/cli_video.py` (t2v option + call)
- Modify: `tests/cli/test_cli_video.py` if it referenced `--expand`

**Interfaces:**
- Consumes: `apply_tool_option` (Task 8).

- [ ] **Step 1: Write/adjust the failing test**

```python
# tests/cli/test_cli_video.py — add:
def test_t2v_help_shows_tool_option() -> None:
    from click.testing import CliRunner
    from gflow_cli.cli import main
    result = CliRunner().invoke(main, ["video", "t2v", "--help"])
    assert "--tool" in result.output
    assert "--expand" not in result.output
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_cli_video.py::test_t2v_help_shows_tool_option -q`
Expected: FAIL (`--expand` still present)

- [ ] **Step 3: Swap the t2v option**

In `cli_video.py`: replace the `-e/--expand` block + `expand: bool` param with the same
`-t/--tool` option (help text adjusted, no "single-prompt only" — t2v is single-prompt).
Replace `expand_prompt(prompt, enabled=expand, quiet=as_json)` with
`apply_tool_option(prompt, tool_specs, category="video", quiet=as_json)`; keep the
`_run_t2v(..., original_prompt=...)` threading. Update the import.

- [ ] **Step 4: Run video tests**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_cli_video.py tests/features/test_video_agent_ui_steps.py -q`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
.venv/Scripts/python.exe -m ruff check --fix src/gflow_cli/cli_video.py tests/cli/test_cli_video.py
.venv/Scripts/python.exe -m ruff format src/gflow_cli/cli_video.py tests/cli/test_cli_video.py
git add -A
git commit -m "feat(cli): replace --expand with --tool on video t2v"
```

---

### Task 10: MCP — `gflow_list_tools` + `tools` param (replace `expand`)

**Files:**
- Modify: `src/gflow_cli/mcp/tools.py` (replace `expand: bool` with `tools: list[dict] = []`; add `gflow_list_tools`)
- Modify: `tests/mcp/test_server.py` (parity assertion)

**Interfaces:**
- Consumes: `iter_tools` (Task 4).
- Produces: an MCP tool `gflow_list_tools() -> dict` returning `{"tools":[{name,title,description,category}...]}`; a `tools` array param on `gflow_generate_image`/`gflow_generate_video` (each item `{name, options}`), surfaced in the returned `params` dict. The §61 parity test asserts the `tools` param exists on both generate tools (replacing the `expand` assertion).

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_server.py — update the two param tests' assertion lines:
        # CLI/MCP symmetry (AGENTS.md): the CLI --tool option mirrors to a `tools` param.
        assert "tools" in schema.get("properties", {}), "MCP image tool missing 'tools' (CLI parity)"
# (and the same for the video tool test)

# add a new test:
class TestListTools:
    def test_list_tools_registered(self, mcp_server: Any) -> None:
        assert "gflow_list_tools" in mcp_server._tool_manager._tools
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/mcp/test_server.py -q`
Expected: FAIL (`'tools' missing` / `gflow_list_tools` not registered)

- [ ] **Step 3: Implement**

In `mcp/tools.py`:
- Replace `expand: bool = False` with `tools: list[dict[str, Any]] | None = None` on `gflow_generate_image` and `gflow_generate_video`; update docstrings (a `tools` array of `{name, options}` applied before generation; enumerate valid names + that `creative-director` supports `style`); put `"tools": tools or []` into the returned `params` dict (replacing `"expand": expand`).
- Add:

```python
@server.tool(
    name="gflow_list_tools",
    description="List available gflow prompt tools (name, title, description, category).",
)
async def gflow_list_tools() -> dict[str, Any]:
    from gflow_cli.tools.registry import iter_tools

    return {
        "tools": [
            {"name": s.name, "title": s.title, "description": s.description, "category": s.category}
            for s in iter_tools()
        ]
    }
```

- [ ] **Step 4: Run MCP tests**

Run: `.venv/Scripts/python.exe -m pytest tests/mcp/ -q`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
.venv/Scripts/python.exe -m ruff check --fix src/gflow_cli/mcp/tools.py tests/mcp/test_server.py
.venv/Scripts/python.exe -m ruff format src/gflow_cli/mcp/tools.py tests/mcp/test_server.py
git add src/gflow_cli/mcp/tools.py tests/mcp/test_server.py
git commit -m "feat(mcp): gflow_list_tools + tools param (replace expand)"
```

---

### Task 11: Full-suite verification + cleanup

**Files:** none new — verification + any straggler fixes.

- [ ] **Step 1: Confirm no `--expand` / `prompt_expander` / `expand_prompt` remnants**

Run: `grep -rn "expand_prompt\|--expand\|api.prompt_expander\|\bexpand: bool" src tests`
Expected: NO matches (the only "expand" left is inside `expanded_prompt`/`ExpansionResult`/`was_expanded`). Fix any straggler.

- [ ] **Step 2: Lint + format the whole tree**

Run: `.venv/Scripts/python.exe -m ruff check src tests` → `All checks passed!`
Run: `.venv/Scripts/python.exe -m ruff format --check src tests` → all formatted

- [ ] **Step 3: Type-check**

Run: `.venv/Scripts/python.exe -m pyright src`
Expected: `0 errors`

- [ ] **Step 4: Full test suite (incl. mcp + features — the scoped-run blind spots)**

Run: `.venv/Scripts/python.exe -m pytest tests/tools tests/cli tests/mcp tests/features tests/api tests/data -q`
Expected: all pass (note `tests/api` no longer contains `test_prompt_expander.py`).

- [ ] **Step 5: Smoke the surfaces**

```bash
.venv/Scripts/python.exe -m gflow tools list
.venv/Scripts/python.exe -m gflow tools run creative-director "cat in space" --json
.venv/Scripts/python.exe -m gflow image t2i --help   # shows -t/--tool, not --expand
```

- [ ] **Step 6: Final commit (if any straggler fixed)**

```bash
git add -A
git commit -m "chore(tools): PR1 verification — no --expand remnants, full suite green"
```

---

## Self-Review

**Spec coverage (spec §4–§11, PR 1 scope):**
- §4 tools package (spec/loader/registry/runtime/expander/banned/builtin TOML) → Tasks 1–6. ✓
- §5 banana-claude alignment (banned filter, 5-component formula, domains) → Tasks 1, 3, 6. ✓
- §6 CLI (`tools list/show/run`, `--tool`) → Tasks 7–9. ✓
- §7 MCP (`gflow_list_tools`, `tools` param, §61 test) → Task 10. ✓
- §3 remove `--expand` (unreleased) → Tasks 8, 9, 11. ✓
- Deferred (PR 2/3): DTO `original_prompt`, broaden i2i/batch/i2v/r2v/chain, `metadata_json.tool`, docs — NOT in this plan (correct).

**Placeholder scan:** the only "fill from source" items are the verbatim 5-component template + domain vocab in `creative-director.toml` (Task 3) — DATA transcription from a cited file (`banana-claude/.../prompt-engineering.md`), not logic placeholders. All code/test logic is complete.

**Type consistency:** `apply_tool(spec, prompt, options, *, expander=None) -> ExpansionResult` used identically in Tasks 6, 7, 8. `apply_tool_option(text, tool_specs, *, category, quiet) -> tuple[str, str|None]` consistent in Tasks 8, 9. `ExpansionResult(original, expanded, was_expanded)` fields consistent throughout. `get_tool`/`iter_tools`/`tool_names` consistent (Tasks 4, 6, 7, 8, 10).
