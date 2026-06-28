# Tools Framework

`gflow-cli` ships a lightweight, **TOML-defined "tool"** concept — the CLI analogue of Google
Flow's **Tools** menu. A **tool** is a named, single-purpose transform layered on top of base
generation (think: a reusable prompt/workflow preset). The first built-in tool is
[`creative-director`](PROMPT_EXPANSION.md), which rewrites a terse prompt into a vivid one using
Google's five-component formula.

This document covers the framework: the `gflow tools` command group, the `--tool` option on
generation commands, the TOML schema, how a tool is defined, and the MCP exposure. For the
Creative Director tool itself (formula, domain styles, banned keywords, Gemini wiring), see
[PROMPT_EXPANSION.md](PROMPT_EXPANSION.md).

---

## 1. Concept

A tool is invoked uniformly in two ways:

- **Standalone** — `gflow tools run <name> "<input>"` runs the transform and prints the result
  (pipeable). No generation, no credits.
- **Inline on a generation command** — `--tool/-t <name>[:k=v]` (repeatable) on `image t2i` /
  `i2i` / `batch`, `video t2v` / `i2v` / `r2v` / `chain`. The tool is applied as
  **pre-processing**, before the prompt reaches the transport.

Tools are **pure prompt transforms** today: they read a prompt and return a (possibly rewritten)
prompt. The richer `Tool.apply(ctx) → outcome` dispatch framework (workflow tools beyond prompt
rewriting) is a deferred design — see
[the tool-abstraction evaluation](superpowers/research/2026-06-27-tool-abstraction-evaluation.md).

### Lineage

The design mirrors Google Flow's **Tools** menu (Discover / Simple Sketch / Mockup / Scene
Explorer / …). gflow-cli does **not** port Flow's catalogue; it provides the *seam* and ships one
tool. The CLI verbs map onto Flow's affordances: `tools list` ≈ Discover, `tools show` ≈ the tool
card, `tools run` / `--tool` ≈ invoking the tool.

---

## 2. The `gflow tools` command group

### `gflow tools list [--json]`

Discover the registered tools.

```console
$ gflow tools list
Name               Title              Category   Description
creative-director  Creative Director  both       Rewrite a terse prompt into a vivid one using Google's 5-component formula.
```

`--json` emits one object per tool with `{name, title, description, category, requires_env}`.

### `gflow tools show <name> [--json]`

Inspect a single tool — title, description, category, required env vars, and the available
**styles** (domain modes). A style that is image-only or video-only is rendered with its category
in parentheses so you can tell same-named image/video styles apart:

```console
$ gflow tools show creative-director
Creative Director (creative-director) — both
Rewrite a terse prompt into a vivid one using Google's 5-component formula.
Requires env: GFLOW_CLI_GEMINI_API_KEY
Styles: cinema (image), product (image), portrait (image), editorial (image), ui (image),
        logo (image), landscape (image), infographic (image), abstract (image),
        cinematic (video), documentary (video), animation (video), social (video),
        product (video), abstract (video)
```

`--json` emits the full spec.

### `gflow tools run <name> "<input>" [--style <mode>] [--json]`

Run a tool standalone. Useful for previewing an expansion, scripting, or piping into another
command:

```console
$ gflow tools run creative-director "a cat on a couch" --style cinema
A sleek black domestic shorthair cat reclining on a worn leather couch, ...

$ gflow tools run creative-director "a cat" --json
{"name": "creative-director", "original": "a cat", "expanded": "...", "was_expanded": true}
```

`tools run` is **category-agnostic** — it does not gate styles by image/video, so you can preview
any style. (Gating happens only on the real generation commands, where the command knows whether
it is producing an image or a video — see §4.)

The JSON shape is `{name, original, expanded, was_expanded}`. `was_expanded` is `false` when the
tool left the prompt unchanged (e.g. missing API key, network fault — tools are **never fatal**,
see §6).

---

## 3. The `--tool` option on generation commands

`--tool/-t` is **repeatable** and accepts an inline option string:

```
--tool <name>[:k=v[,k2=v2]]
```

Examples:

```console
# Single image, Creative Director with the "cinema" style
gflow image t2i "a fox in the snow" --tool creative-director:style=cinema

# Video, cinematic style
gflow video t2v "a city at night" -t creative-director:style=cinematic

# Repeatable — tools chain left-to-right, each consuming the previous output
gflow image t2i "a fox" --tool creative-director --tool some-other-tool
```

Where it is wired:

| Command | Category | Notes |
|---|---|---|
| `image t2i` | image | Single prompt **and** multi-prompt (`--prompts-file` / `--stdin`) batch; applied per item on batch. |
| `image i2i` | image | Applied to the resolved prompt. |
| `image batch` | image | Manifest rows expanded per row; unknown tool/style fails fast pre-network. |
| `video t2v` | video | |
| `video i2v` | video | Applied to the resolved prompt. |
| `video r2v` | video | |
| `video chain` | video | Applied **per link**, *after* the dry-run / cost-confirmation gate — a rejected or `--dry-run` chain spends nothing and makes no Gemini calls. |

On multi-item paths (batch, chain), the tool runs **once per prompt/link, sequentially** — so a
50-prompt batch with `--tool creative-director` is up to 50 sequential Gemini calls. This is
bounded and non-fatal; concurrency is a later optimization. Plan for the added latency on large
batches.

### Cost note

Generation credits are spent by Flow, not by tools. Tool *application* is free of Flow credits but
does call the configured Gemini API (which has its own quota/billing). For `video chain`, tool
application is deliberately deferred until **after** the cost gate so an aborted run is truly free.

---

## 4. Category gating

Each tool declares a `category` (`image`, `video`, or `both`), and each **style** (domain mode)
also carries a category. This lets a tool offer image styles and video styles under the same name
without collision — e.g. `creative-director` has both a `product` *image* style and a `product`
*video* style.

- A generation command knows its own category (`image t2i` → `image`, `video t2v` → `video`).
- `--tool` rejects a tool that does not `supports()` the command's category
  (`tool 'X' does not support video generation.`).
- A `style` value is validated against the domains available **for that category** — picking a
  video-only style on an image command is a usage error with the valid styles listed.
- `gflow tools run` passes **no** category, so its style resolution is "first name match wins"
  (preview mode).

---

## 5. The TOML schema

A tool is a single TOML file validated into a Pydantic `ToolSpec`
([`src/gflow_cli/tools/spec.py`](../src/gflow_cli/tools/spec.py)). All models are **frozen**
(immutable after load).

### Top-level (`ToolSpec`)

| Field | Type | Required | Meaning |
|---|---|---|---|
| `name` | `str` (`^[a-z0-9-]+$`) | ✅ | Slug; registry key, error token, future filename. |
| `title` | `str` | ✅ | Human-readable name (Flow card title). |
| `description` | `str` | ✅ | One-line description (Flow card text). |
| `category` | `"image" \| "video" \| "both"` | ✅ | Which generation commands the tool applies to. |
| `author` | `str` | — | Defaults to `"gflow"`. |
| `version` | `str` | ✅ | Hand-bumped on any behavior change (paired with `config_hash`, see [PROMPT_EXPANSION.md](PROMPT_EXPANSION.md)). |
| `requires_env` | `tuple[str, ...]` | — | Env vars the tool needs (e.g. `GFLOW_CLI_GEMINI_API_KEY`). |
| `options_schema` | `dict[str, str]` | — | Declared option keys → help text; validated, then frozen to a read-only mapping. |
| `config` | `ToolConfig` | ✅ | The behavior block. |

### `[config]` (`ToolConfig`)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `model` | `str` | `gemini-2.5-flash` | Default model for the prompt rewrite. |
| `system_template` | `str` | ✅ required | The instruction (e.g. the 5-component formula). **Must not** carry a trailing `User prompt:` marker — the runtime appends it exactly once. |
| `banned_keywords` | `tuple[str, ...]` | `()` | Whole-word, case-insensitive terms stripped from the output (belt-and-braces with the instruction). |
| `domains` | `tuple[DomainMode, ...]` | `()` | Style vocabulary libraries (see below). |
| `max_input_chars` | `int` | `4000` | Input prompt is truncated to this before the call. |
| `max_output_chars` | `int` | `3500` | Output is truncated to this. |

### `[[config.domains]]` (`DomainMode`) — styles

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | `str` | ✅ | Style name, used as `--style <name>` / `:style=<name>`. |
| `vocabulary` | `str` | ✅ | Guidance text injected into the instruction when this style is selected. |
| `category` | `"image" \| "video" \| "both"` | `both` | Restricts the style to image/video commands (enables same-named image/video styles). |

---

## 6. How a tool is applied (runtime contract)

[`src/gflow_cli/tools/runtime.py`](../src/gflow_cli/tools/runtime.py):

1. **`build_instruction(config, style, category)`** — starts from `config.system_template`,
   appends the selected domain's `vocabulary` (if any, gated by `category`), then appends the
   `"\n\nUser prompt: "` marker **exactly once**. An unknown style is ignored with a
   `tool_unknown_style` warning (never fatal).
2. **`apply_tool(spec, prompt, options, *, category, expander)`** — builds the instruction, calls
   the prompt rewriter (the Gemini-backed `PromptExpander`), and returns a frozen
   `ExpansionResult(original, expanded, was_expanded)`.
3. If the prompt was rewritten, **`strip_banned_keywords`** removes any banned terms (using the
   tool's own `banned_keywords` list) and logs `tool_banned_keywords_stripped`.

**Never fatal.** Missing API key, a rate limit, a network error, an empty or quote-only response —
any of these degrades gracefully to the original prompt (`was_expanded=False`). A tool can never
abort a generation. See [PROMPT_EXPANSION.md §6](PROMPT_EXPANSION.md#6-never-fatal-contract) for the
Creative Director specifics.

---

## 7. Adding a tool

### Built-in tools

1. Write `src/gflow_cli/tools/builtin/<name>.toml` following the schema in §5.
2. That's it — the loader auto-discovers every `*.toml` in the `builtin/` package via
   `importlib.resources` at startup
   ([`loader.py`](../src/gflow_cli/tools/loader.py)), validates it into a `ToolSpec`, and the
   registry ([`registry.py`](../src/gflow_cli/tools/registry.py)) exposes it under `spec.name`.
   Invalid TOML or a schema violation **fails loud** (`ConfigurationError`) the first time any
   `tools` / `--tool` path runs.
3. Bump the tool's `version` whenever you change behavior; the `config_hash` (a SHA-256 of the
   resolved `ToolConfig`) is recorded alongside it for tamper-evidence.

The registry is built once and memoized (`@lru_cache`), mirroring `config.get_settings`.

### "My Tools" — user-authored tools

You can add your own tools without touching the package: drop a TOML following the §5 schema into
**`<GFLOW_CLI_HOME>/tools/*.toml`** (`GFLOW_CLI_HOME` defaults per OS — see
[CONFIGURATION.md](CONFIGURATION.md)). At startup the registry loads the packaged built-ins first,
then layers your tools on top:

- A user tool with a **new** name is registered alongside the built-ins and appears in
  `gflow tools list`, `--tool`, and the MCP surface immediately.
- A user tool whose name **matches a built-in** overrides it (your customization wins) — the
  shadow is logged (`tool_user_override`) so it is never silent. This lets you, e.g., tweak
  `creative-director`'s `system_template` or styles by shipping your own `creative-director.toml`.
- A malformed user TOML **fails loud** (`ConfigurationError`), exactly like a malformed built-in —
  a typo in your tool won't be silently skipped.

There is no separate enable flag; presence of the file is the activation. (This is the "My Tools"
analogue of Flow's Tools menu.)

---

## 8. MCP exposure

Per AGENTS.md §61 (CLI ↔ MCP parity), the tools surface is mirrored on the MCP server
([`src/gflow_cli/mcp/`](../src/gflow_cli/mcp/)):

- **`gflow_list_tools`** — mirrors `gflow tools list`; returns `[{name, title, description,
  category}, …]`.
- **`tools` array parameter** on `gflow_generate_image` and `gflow_generate_video` — a list of
  `{"name": str, "options": dict}` objects, e.g.
  `[{"name": "creative-director", "options": {"style": "cinema"}}]`. The §61 parity test asserts
  the **container** `tools` param exists on both surfaces, so symmetry stays fixed as tools accrue
  (no per-tool flag sprawl).

The MCP `list[dict]` form is validated and adapted to the CLI `--tool name[:k=v]` form by
`tool_specs_from_invocations` ([`invocation.py`](../src/gflow_cli/tools/invocation.py)) — e.g.
`{"name": "creative-director", "options": {"style": "cinema"}}` → `"creative-director:style=cinema"`.
Malformed entries return a structured `{"status": "invalid_tools", ...}` error to the agent rather
than raising.

> **Note:** MCP generation tools are currently scaffolding (`status: pending`) — tool validation
> and adaptation work end-to-end, but the generation call itself is wired through the forthcoming
> daemon, not yet executed in-process. See [MCP.md](MCP.md).

### Relationship to the `expand_prompt` MCP *prompt*

The MCP server also registers an older `expand_prompt` **prompt template** (`mcp/prompts.py`),
now **deprecated** in favor of this tool. That is a distinct surface from the `creative-director`
**tool**: a prompt template returns text for the *client's* model to expand (no API call, no
banned-keyword stripping, no styles, no provenance), whereas the tool runs server-side and records
provenance. `expand_prompt` carries a `[DEPRECATED]` marker in its client-visible description and
is slated for removal in a future major release; see
[PROMPT_EXPANSION.md §9](PROMPT_EXPANSION.md#9-relationship-to-the-expand_prompt-mcp-prompt)
for the full comparison and migration guidance.

---

## 9. See also

- [PROMPT_EXPANSION.md](PROMPT_EXPANSION.md) — the Creative Director tool in depth.
- [CONFIGURATION.md](CONFIGURATION.md) — `GFLOW_CLI_GEMINI_API_KEY`, `GFLOW_CLI_GEMINI_MODEL`.
- [DATA_LAYER.md](DATA_LAYER.md) — how `expanded_prompt` and `metadata_json.tool` are recorded.
- [MCP.md](MCP.md) — the MCP server.
- [tool-abstraction evaluation](superpowers/research/2026-06-27-tool-abstraction-evaluation.md) —
  the deferred S3 dispatch-framework design.
