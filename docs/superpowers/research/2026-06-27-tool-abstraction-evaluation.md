# Tool-Abstraction Evaluation — "Tools" concept for gflow-cli

> **Status:** Reference (decision record). Captured 2026-06-27 during the design of the
> Tools framework (S2). Preserves the multi-angle ("owl") analysis so the deferred
> **S3 dispatch framework** can be picked up later without re-deriving the trade-offs.

## 1. Why this exists

Google Flow's web UI has a **Tools** menu (`labs.google/fx/tools/flow/project/<id>/tools`):
an "Explore Tools" gallery with **Discover** and **My Tools** tabs. Each tool is a named,
icon'd, single-purpose capability under **Image/Video** categories that reshapes how you
drive generation — e.g. *Simple Sketch* (drawing → stylized image), *Scene Explorer*
(location → scene visuals), *Mockup* (image → comped into environments). Users can save
their own ("My Tools").

The owner wants gflow-cli to grow an analogous **"tool" concept** from a CLI perspective —
*expandable, versatile, flexible, composable* — using the just-shipped prompt-expansion
feature (the banana-claude "Creative Director") as the **first** tool. Porting Flow's
actual tool catalogue is explicitly out of scope (too much work).

### Source systems studied
- **Google Flow Tools** (the product) — the *concept*: discoverable, named, described tools.
- **banana-claude** (`github.com/AgriciDaniel/banana-claude`, local clone) — a Claude skill
  that turns Claude into a "Creative Director" for Gemini: orchestrator (`SKILL.md`) +
  `references/` (5-component prompt formula, banned-keyword list, 9 image domain modes) +
  a `brief-constructor` sub-agent. Evaluated in PR #202 (`docs/BANANA_CLAUDE_EVALUATION.md`).
- **gflow-cli** (our CLI) — Click command groups, transports, an MCP server, a SQLite catalog.

## 2. What a "tool" actually is (cross-angle agreement)

A **tool is a named, single-purpose transform layered on base generation** — a prompt/
workflow preset, **not** a new transport or verb. Flow's tools, banana-claude's domain
modes, and our `--expand` are the *same shape*. This matches the **flag/parameter** model,
not a command-per-tool model.

The seam already exists: `expand_prompt() → (prompt_to_send, original)` over a never-fatal,
provider-agnostic `PromptExpander.expand() → ExpansionResult`. The design principle is to
**generalize that seam**, not invent a parallel pipeline.

## 3. The five lenses

### Lens A — Extensibility & registry
- Minimal `Tool` interface with a **`ToolContext` → `ToolOutcome` envelope** (carry
  `prompt`, `params`, `inputs` even if v1 only mutates `prompt`) so the first non-prompt
  tool (e.g. Mockup, needing image inputs) doesn't force a re-architecture.
- Registry = in-process `dict` + `@register` decorator; **reject** entry-point/plugin
  discovery now (packaging + security surface for zero benefit).
- Adding tool #2 = 2 files. Generalize the flag to `--tool NAME` (no per-tool command groups).
- **Recommendation:** MEDIUM-minimal (~1 day). Biggest risk: binding the contract to
  prompt-string-in/string-out.

### Lens B — Composability & pipeline
- **No DAG.** gflow already has two substrates: stdout-JSON handle-passing (pure tools,
  pipeable) and in-process `chain.py` (held sessions). `upscale` already composes off a
  `media_id`.
- Split tools into **pure** (no browser/credits → standalone `gflow tools run`, pipeable)
  vs **generative** (compose via `media_id` handles across fresh sessions).
- Biggest risk: the `--json` envelope is not yet a stable, versioned, single-document handle
  contract. Optional hardening: `{status, command, schema_version, handles:{media_id, local_path}}`.

### Lens C — Agent/MCP ergonomics + CLI discoverability
- A registry + **one generic `--tool`/`tools=[]` param** is what keeps the AGENTS.md §61
  CLI↔MCP symmetry surface from exploding as tools accrue. Do **not** spawn one MCP tool per
  gflow-tool.
- CLI: `gflow tools list/show` (Discover) + repeatable `--tool name[:opts]` on verbs.
- MCP: a `tools` array param on `gflow_generate_image/_video` + one `gflow_list_tools`;
  enumerate valid names in the param description so agents self-educate.
- Biggest risk: per-tool params surfacing as top-level flags → §61 multiplies. Mitigation:
  **opaque `options` blob** validated in the registry, keeping the generate signature fixed.

### Lens D — Data, versioning, "My Tools"
- **No per-tool columns.** Write `metadata_json.tool = {name, version, model, params}`
  (redaction-gated — free-text params must pass the same store/redacted gate as prompts).
  Keep `expanded_prompt` (the reproducibility anchor).
- Versioning: a `TOOL_VERSION` constant bumped on template/model change + capture the model
  actually used (env-overridable). Reproducibility = stored output + inputs that produced it.
- "My Tools"/presets = **TOML files under the config dir**, not DB. Principle: **DB = what
  happened (immutable ledger); files = what to do (editable definitions).**
- Biggest risk: preset↔ledger drift. Mitigation: snapshot resolved preset values + hash into
  `metadata_json.tool` at record time.

### Lens E — YAGNI skeptic
- **Defer the registry.** A protocol with one implementer is premature; cost multiplies
  across CLI surface, MCP §61 parity, docs/INDEX, and migrations/tests.
- Ship the banana-claude value through the existing class + flag. Keep the clean seam (plain
  `api/` classes + thin call sites) so a registry is a ~20-line later extraction.
- Do **not** build: a `Tool` Protocol/ABC, a `gflow tools` dispatch group, an MCP tool
  catalogue, per-tool data tables, or any Flow-catalogue port.

## 4. The strategy spectrum

| | S1 "Feature, not framework" | **S2 "Thin discovery seam" (chosen)** | S3 "Registry + dispatch" |
|---|---|---|---|
| banana-claude value (banned filter, domains, reference decomposition, broaden surface, docs) | ✅ | ✅ | ✅ |
| `ToolSpec` metadata registry + `gflow tools list/show` + `gflow_list_tools` | ❌ | ✅ | ✅ |
| `--tool name[:opts]`/`tools=[]` as the fixed forward-compatible shape | ❌ | ✅ | ✅ |
| `Tool.apply(ToolContext)→ToolOutcome` dispatch framework | ❌ | ❌ | ✅ |
| Effort / surface to maintain | smallest | medium-small | largest |
| Risk | tool #2 pays full §61/flag sprawl | — | premature uniformity (guessed contract) |

## 5. Decision

**S2 — Thin discovery seam**, with a config-driven twist: tools are **TOML-defined**
(packaged builtins loaded + pydantic-validated), the My-Tools loader scan is a **dormant
seam**, and the surface is uniform `--tool/-t <name>` (no `-e/--expand` sugar — it was
never released, so it is cleanly refactored into the tool path). First tool: `creative-director`.

S2 threads the A-vs-E tension via Lens C: the registry's real job is not to justify one
tool but to keep **one fixed generic param** so the *second* tool doesn't explode the §61
surface — while deferring the `Tool.apply()` dispatch contract until two real tools reveal
its true shape.

## 6. The deferred S3 design (for the future)

When a second, non-prompt tool (e.g. Mockup) arrives, extract a dispatch framework:

```python
@dataclass(frozen=True)
class ToolContext:
    prompt: str
    params: Mapping[str, object]
    inputs: tuple[Path, ...] = ()

@dataclass(frozen=True)
class ToolOutcome:
    prompt: str
    param_overrides: Mapping[str, object] = field(default_factory=dict)
    was_applied: bool = False
    note: str = ""

class Tool(Protocol):
    spec: ToolSpec
    def apply(self, ctx: ToolContext) -> ToolOutcome: ...  # never raises
```

- Registry resolves `--tool name` → a `Tool` object; commands run `apply()` in sequence
  before the transport, threading `ToolOutcome.param_overrides` into the request.
- Activate the My-Tools loader scan (`~/.gflow/tools/*.toml`) with override/precedence.
- Optionally promote pure tools to standalone composition via the hardened `--json` envelope.

Build S3 informed by **two** real data points, not one guessed contract.
