# Design Spec — `gflow` Tools Framework (S2) + Creative Director tool

> **Strategy:** S2 "Thin discovery seam" (see
> [`../research/2026-06-27-tool-abstraction-evaluation.md`](../research/2026-06-27-tool-abstraction-evaluation.md)
> for the full multi-angle evaluation and the deferred S3 design).
> **Branch base:** `develop`. **Packaging:** 3 sequential PRs.

## 1. Goal

Introduce a lightweight, **TOML-defined** "tool" concept to gflow-cli — the CLI analogue of
Google Flow's **Tools** menu — and make the (unreleased) prompt-expansion feature the first
registered tool, **`creative-director`**, fully aligned with the banana-claude "Creative
Director" patterns (5-component formula, banned-keyword cleanup, domain modes). Broaden the
tool across all generation commands. Comprehensively document it.

A **tool** = a named, single-purpose transform layered on base generation (a prompt/workflow
preset). It is invoked uniformly as `--tool/-t <name>[:k=v]` on generation commands, or
standalone via `gflow tools run <name>`.

## 2. Non-goals (deferred; seams left ready)

- User-authored **My Tools** TOML scanning + override/merge (loader seam present but dormant).
- The `Tool.apply(ToolContext)→ToolOutcome` **dispatch framework** (S3).
- Entry-point/plugin discovery; a tools DB table; a `--json` cross-command envelope standard.
- Porting Flow's catalogue (Simple Sketch, Mockup, Scene Explorer, …).

## 3. Disposition of `--expand` (unreleased → free to refactor)

`-e/--expand` and `api/prompt_expander.py` exist only on `develop` (absent from every tag
incl. `v0.21.0`; under CHANGELOG `[Unreleased]`). **Decision:** remove `-e/--expand`
entirely; the only surface is `--tool creative-director`. The `PromptExpander` class and
`expand_prompt()` helper are **ported/refactored** into the tools package — no throw-away,
no back-compat alias, no breaking change (never shipped).

## 4. Architecture

```
src/gflow_cli/tools/
  __init__.py
  spec.py            # ToolSpec + ToolConfig pydantic models (metadata + behavior)
  loader.py          # load + validate packaged builtin TOMLs; dormant user-dir scan seam
  registry.py        # in-process dict[name -> ToolSpec]; iter_tools(), get(name)
  runtime.py         # apply a resolved tool to a prompt (wraps the expander)
  expander.py        # PromptExpander (moved from api/prompt_expander.py), + domain injection
                     #   + deterministic banned-keyword post-filter
  banned.py          # strip_banned_keywords() + the verbatim banned list + prestige anchors
  builtin/
    creative-director.toml   # the tool definition (single source of truth)
```

`api/prompt_expander.py` is deleted (unreleased); imports updated. `_cli_helpers.expand_prompt`
is replaced by a tools-aware `apply_tools()` helper (same never-fatal, `quiet=as_json` contract).

### 4.1 `ToolSpec` / `ToolConfig` (pydantic)

```python
class DomainMode(BaseModel):
    name: str
    vocabulary: str                 # injected guidance text

class ToolConfig(BaseModel):
    model: str = "gemini-2.5-flash"
    system_template: str            # the 5-component instruction (multiline)
    banned_keywords: tuple[str, ...] = ()
    domains: tuple[DomainMode, ...] = ()
    max_input_chars: int = 4000
    max_output_chars: int = 3500

class ToolSpec(BaseModel):
    name: str                        # slug, e.g. "creative-director"
    title: str                       # "Creative Director"
    description: str                 # one-line (Flow card text)
    category: Literal["image", "video", "both"]
    author: str = "gflow"
    version: str                     # TOOL_VERSION, e.g. "1"
    requires_env: tuple[str, ...] = ()   # ("GFLOW_CLI_GEMINI_API_KEY",)
    options_schema: dict[str, str] = {}  # {"style": "domain mode name"}
    config: ToolConfig
```

### 4.2 Loader & registry

- `loader.load_builtin_tools()` reads every `tools/builtin/*.toml` via
  `importlib.resources`, parses with `tomllib`, validates into `ToolSpec`. Invalid TOML →
  `ConfigurationError` (fail loud at startup of any `tools`/`--tool` path).
- **Dormant My-Tools seam:** `loader.load_user_tools(config_dir)` exists but is not called
  this cycle (a single call site activates it later).
- `registry` builds the name→spec dict once (module-level, lazy).

### 4.3 Runtime

- `runtime.apply_tool(spec, prompt, options, *, settings) -> ExpansionResult` — reuses the
  expander's existing frozen `ExpansionResult(original, expanded, was_expanded)`; no new
  result type at S2 (the richer `ToolOutcome` envelope is reserved for the S3 dispatch
  framework — see the research doc).
- For `creative-director`: builds the Gemini system instruction from `config.system_template`
  + the selected `options["style"]` domain vocabulary, calls the expander (never-fatal),
  then runs `strip_banned_keywords()` on the result (belt-and-braces with the instruction).

## 5. banana-claude alignment (tool config content)

### 5.1 Banned-keyword cleanup (plan Task 2, previously missed)
Two layers: (a) `system_template` forbids them; (b) `strip_banned_keywords(text)` removes/
neutralizes any that slip through (case-insensitive, whole-word), logged via structlog
`prompt_banned_keywords_stripped`. Verbatim list:
`4k, 8k, ultra HD, high resolution, masterpiece, highly detailed, ultra detailed,
trending on artstation, hyperrealistic, ultra realistic, photorealistic, best quality,
award winning`. (Resolution intent is redirected to generation params, not the prompt.)

### 5.2 5-component formula
`Subject → Action → Location/Context → Composition → Style (lighting nested in Style)`,
written as narrative paragraphs (never keyword lists), per banana-claude
`references/prompt-engineering.md`. Stored verbatim in `creative-director.toml`'s
`system_template`.

### 5.3 Domain modes (`--tool creative-director:style=<mode>`)
- **Image (banana's 9):** cinema, product, portrait, editorial, ui, logo, landscape,
  infographic, abstract — each with its vocabulary library.
- **Video (small set):** cinematic, documentary, product, animation, abstract, social.
- A domain injects its `vocabulary` into the instruction. Unknown style → ignored with a
  warning (never fatal). `category` gates which domains apply (image vs video tools).

## 6. CLI surface

- `gflow tools list [--json]` — Discover analogue: name, title, description, category,
  requires-env. (Rich table / JSON.)
- `gflow tools show <name> [--json]` — full spec incl. available styles.
- `gflow tools run <name> "<input>" [--style <mode>] [--json]` — standalone; emits
  `{name, original, expanded, was_expanded}` (pipeable). Pure tools only (no generation).
- `--tool/-t <name>[:k=v]` (repeatable) on `image t2i/i2i` + batch, `video t2v/i2v/r2v/chain`.
  Applied as pre-processing before the transport. Single-prompt-only guard **removed**.

## 7. MCP surface (AGENTS.md §61 symmetry)

- `gflow_list_tools()` → registry listing (mirrors `gflow tools list`).
- A `tools` array param `[{name, options}]` on `gflow_generate_image` / `gflow_generate_video`
  (replaces the scaffolded `expand: bool`). The §61 parity test asserts the **container**
  `tools` param exists on both CLI and MCP — keeps symmetry fixed as tools accrue.
- Param description enumerates valid tool names + styles so agents self-educate.

## 8. Broaden surface + data

- Add `original_prompt: str | None = None` to `GenerateImageRequest` (`api/image.py`) and
  `GenerateVideoRequest` (`api/video.py`) — both frozen dataclasses, field appended last.
- Recorders read `request.original_prompt` (retire the separate `original_prompt` kwarg
  threading flagged by the prior review as a silent-misrecord hazard). `prompt` column =
  original; `expanded_prompt` = submitted expansion (redaction-gated, unchanged).
- Record `metadata_json.tool = {name, version, model, params}` via the recorder
  (redaction-gated; in redacted mode store `{name, version, params_hash}` only). No migration
  (reuses `metadata_json`).
- Wire `--tool` into every generation path: `i2i` (`_run_i2i`), batch t2i (per item,
  sequential ≤50, non-fatal), manifest `image batch` (≤5), `i2v`/`r2v`
  (`_generate_and_report` already supports it via the DTO), `chain` (per-link, sequential).

## 9. Config

- `gemini_model` Settings field already added. `GFLOW_CLI_GEMINI_API_KEY` already documented.
- The tool's default model lives in the TOML (`config.model`), overridable per-run later via
  options; `GFLOW_CLI_GEMINI_MODEL` still wins as the global override.

## 10. Documentation

- `docs/TOOLS.md` — the tool framework: concept (Flow-Tools lineage), `gflow tools` commands,
  `--tool` usage, the TOML schema, "how a tool is defined", the dormant My-Tools seam,
  MCP exposure. Indexed in `docs/INDEX.md`.
- `docs/PROMPT_EXPANSION.md` — the Creative Director tool: domain model, 5-component formula,
  banned-keyword policy, domain modes, Gemini endpoint + JSON I/O, the `expanded_prompt`
  column + `metadata_json.tool`, never-fatal contract, config, banana-claude credit (PR #202).
- `docs/superpowers/research/2026-06-27-tool-abstraction-evaluation.md` — the owl analysis
  (already written).
- `CHANGELOG.md` `[Unreleased]` updated; `PLAN.md` backlog reconciled.

## 11. Packaging (3 PRs to `develop`)

- **PR 1 — Tools framework + Creative Director:** `tools/` package, `creative-director.toml`,
  banned filter, domains, `gflow tools list/show/run`, refactor `--expand`→`--tool` on
  t2i/t2v, MCP `gflow_list_tools` + `tools` param (replace `expand`), §61 test update.
- **PR 2 — Broaden surface:** DTO `original_prompt` field + recorder reads it + retire kwarg;
  `metadata_json.tool` recording; wire `--tool` into i2i/batch/i2v/r2v/chain; remove guard.
- **PR 3 — Docs:** `docs/TOOLS.md`, `docs/PROMPT_EXPANSION.md`, INDEX entries, CHANGELOG/PLAN.

Each PR: TDD, `ruff`/`ruff format`/`pyright src` clean, full suite green (**incl.
`tests/mcp` + `tests/features`** — both have signature/parity gates that scoped runs miss).

## 12. Definition of done

- `gflow tools list` shows Creative Director; `gflow tools run creative-director "cat" --json`
  emits the expansion; banned keywords stripped deterministically; `--tool creative-director`
  works on t2i/i2i/batch/t2v/i2v/r2v/chain.
- MCP `gflow_list_tools` + `tools` param present; §61 parity test green.
- Catalog records `expanded_prompt` + `metadata_json.tool`; redaction honored.
- Docs complete + indexed; full CI green on each PR.
- No `--expand`/`-e` remnants; no `api/prompt_expander.py`; no dead code.

## 13. Key risks

- **Test scope:** CLI-param/`_run_*` signature changes break `tests/features` BDD stubs and
  `tests/mcp` parity — run the full suite, not a scoped subset (bit prompt-expansion PR #209).
- **Batch cost:** `--tool` on a 50-prompt batch = up to 50 sequential Gemini calls; bounded
  and non-fatal, but document the latency; concurrency is a later optimization.
- **§61 sprawl:** keep tool params inside the opaque `options` blob — never as top-level
  generate flags — so the MCP container param stays fixed.
