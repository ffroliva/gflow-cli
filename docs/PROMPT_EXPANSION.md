# Prompt Expansion — the Creative Director Tool

`creative-director` is the first built-in [tool](TOOLS.md). It rewrites a terse prompt into a
single, vivid, self-contained prompt using Google's officially validated **five-component
formula**, optionally injecting domain-specific (cinema / portrait / product / …) vocabulary, and
deterministically strips low-signal "quality-booster" keywords. It calls the public **Gemini API**
server-side and is **never fatal** — any failure degrades to your original prompt.

> This is the tool. For the framework that hosts it (`gflow tools`, `--tool`, the TOML schema, MCP
> exposure), see [TOOLS.md](TOOLS.md).

---

## 1. Quick start

```console
# 1. Get a Gemini API key: https://aistudio.google.com/apikey
export GFLOW_CLI_GEMINI_API_KEY="..."

# 2. Preview an expansion (free of Flow credits)
gflow tools run creative-director "a cat on a couch" --style cinema

# 3. Use it on a real generation
gflow image t2i "a fox in the snow" --tool creative-director:style=cinema
gflow video t2v "a city at night"  -t creative-director:style=cinematic
```

Without `GFLOW_CLI_GEMINI_API_KEY` the tool silently leaves your prompt unchanged (see §6).

---

## 2. Domain model

| Concept | Where | Meaning |
|---|---|---|
| **Tool spec** | `tools/builtin/creative-director.toml` | Single source of truth — name, version, the formula instruction, banned keywords, and the style libraries. |
| **System instruction** | built at runtime | `system_template` + the selected style's vocabulary + a single `User prompt:` marker. |
| **Style / domain mode** | `[[config.domains]]` | A named vocabulary library (camera/lens/lighting refs) injected into the instruction. 9 image + 6 video. |
| **Expansion** | `ExpansionResult(original, expanded, was_expanded)` | The result; `expanded == original` when nothing changed. |
| **Provenance** | `AppliedTool(name, version, model, config_hash, params)` | Recorded in history when a tool rewrote a prompt. |

The tool itself lives in [`src/gflow_cli/tools/`](../src/gflow_cli/tools/): `spec.py` (schema),
`runtime.py` (`apply_tool`), `expander.py` (the Gemini-backed `PromptExpander`), `banned.py`
(keyword stripping), `invocation.py` (provenance + MCP adapter).

---

## 3. The five-component formula

Based on Google's validated prompt structure for Gemini image/video models. The tool instructs the
model to write **natural narrative paragraphs** — never comma-separated keyword lists — keeping the
user's original intent and named subjects intact:

1. **Subject** — who/what is the focus; specific physical characteristics, material, species, age,
   expression. (Never just "a person" or "a product".)
2. **Action** — what the subject is doing or its primary visual state; strong present-tense verbs,
   or a described pose/arrangement.
3. **Location / Context** — environment, time of day, atmospheric conditions.
4. **Composition** — camera perspective, framing, spatial relationship (e.g. "intimate close-up
   from slightly below eye level, shallow depth of field").
5. **Style (lighting included)** — visual register, medium, and lighting **combined**; references
   real cameras, film stock, photographers, publications, or art movements. Lighting is a
   sub-element of Style, **not** a sixth component.

The instruction also carries "Critical Rules": name real cameras/brands, include micro-details,
use prestigious context anchors ("Vanity Fair editorial", "National Geographic cover"), use ALL
CAPS for hard constraints, "prominently displayed" for products, and **never** emit banned
keywords. The full text is the `system_template` in
[`creative-director.toml`](../src/gflow_cli/tools/builtin/creative-director.toml).

---

## 4. Domain modes (styles)

Select with `--style <name>` (`tools run`) or `:style=<name>` (`--tool`). Each style injects a
vocabulary library of concrete references into the instruction. Styles are **category-gated** —
image styles apply to image commands, video styles to video commands (see
[TOOLS.md §4](TOOLS.md#4-category-gating)). `product` and `abstract` exist in **both** categories
with different vocabularies.

### Image styles (9)

| Style | Flavor of vocabulary |
|---|---|
| `cinema` | Cine cameras (RED, ARRI Alexa 65), anamorphic lenses, Kodak Vision3 stocks, lighting setups, color grades. |
| `product` | Surfaces, softbox/tent lighting, hero angles, Apple/Aesop/B&O style refs. |
| `portrait` | Focal lengths (85/105/135mm), apertures, pose language, skin micro-texture. |
| `editorial` | Vogue/Harper's/Kinfolk refs, styling notes, locations, editorial poses. |
| `ui` | Flat vector / isometric / glassmorphism, exact hex palettes, retina sizing. |
| `logo` | Geometric construction, golden ratio, 2–3 colors, monochrome-safe. |
| `landscape` | Depth layers, atmospherics, blue/golden/magic hour, weather. |
| `infographic` | Modular layout, data-viz types, exact-text quoting, accessible palette. |
| `abstract` | Fractals/voronoi, fluid/ink textures, color harmonies, generative styles. |

### Video styles (6)

| Style | Flavor of vocabulary |
|---|---|
| `cinematic` | Camera motion, slow-motion/speed-ramp timing, transitions, film grades. |
| `documentary` | Observational/handheld, B-roll coverage, vérité aesthetic. |
| `animation` | Cel/3D/stop-motion, easing curves, flat/isometric visual language. |
| `social` | 9:16/1:1/16:9 formats, hook-in-2s pacing, UGC vs branded aesthetic. |
| `product` | 360 orbit/macro reveal, studio lighting, infinity cove, end-card pacing. |
| `abstract` | Particle/fluid sims, smoke/ink textures, gradient-shift color, VJ aesthetic. |

An **unknown** style is ignored with a `tool_unknown_style` warning — never an error. Full
vocabularies live in [`creative-director.toml`](../src/gflow_cli/tools/builtin/creative-director.toml);
`gflow tools show creative-director` lists the current set.

---

## 5. Banned-keyword policy

The tool defends against low-signal "quality boosters" in two layers:

1. **Prevention** — the instruction explicitly forbids them.
2. **Cleanup** — `strip_banned_keywords` deterministically removes any that slip through, on a
   **whole-word, case-insensitive** basis, and logs `tool_banned_keywords_stripped`.

The 13 banned terms:

```
4k, 8k, ultra HD, high resolution, masterpiece, highly detailed, ultra detailed,
trending on artstation, hyperrealistic, ultra realistic, photorealistic, best quality,
award winning
```

Matching is longest-first (so `ultra detailed` is stripped before `ultra`), word-boundaried, and
the resulting separators are tidied (no dangling `, ,` or double spaces). Resolution intent
belongs in generation parameters, not the prompt. Stripping runs only on a rewritten prompt — a
prompt the tool left unchanged is never altered.

---

## 6. Never-fatal contract

The Gemini-backed `PromptExpander` ([`expander.py`](../src/gflow_cli/tools/expander.py)) is
stdlib-only (`urllib`) and bounded. `expand()` returns the original prompt with
`was_expanded=False` — never raising — in every degraded case:

- `GFLOW_CLI_GEMINI_API_KEY` is missing.
- Any HTTP error, network failure, timeout, or malformed JSON.
- An empty, whitespace-only, or quote-only model response.

Retryable statuses (`429, 500, 502, 503, 504`) are retried with exponential backoff (default 3
attempts, base delay 1s, doubling); **`401` / `403` fail fast** (and still degrade to the
original). Each attempt has a 20s socket timeout, and the whole call is bounded by an **overall
~60s wall-clock budget** (`max_total_seconds`): retries stop once the budget is spent and each
attempt's timeout is clamped to the remaining budget, so sustained rate limiting can never stall a
batch for the full timeout×retries schedule. (Both bounds are fixed defaults, not env-tunable.)
Input is truncated to `max_input_chars` (4000) and output to `max_output_chars` (3500); a single
layer of wrapping quotes is stripped from the response. The net guarantee: **a tool can never
abort a generation** — the worst case is "your prompt went through unchanged."

---

## 7. Gemini wiring (I/O)

| Aspect | Value |
|---|---|
| Endpoint | `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` |
| Auth | API key in the `x-goog-api-key` header (never on the query string). |
| Default model | `gemini-2.5-flash` (set per tool via the TOML `config.model` key). |
| Request | `system_instruction` = built instruction; user `contents` = the (truncated) prompt; `temperature=0.7`, `maxOutputTokens=1024`. |
| Response | First candidate's text, cleaned (quote-stripped, truncated). |

---

## 8. Provenance — what history records

When a tool rewrites a prompt, the generation is recorded with full provenance
([`data/recorder.py`](../src/gflow_cli/data/recorder.py)):

- **`prompt`** column = the user's **original** prompt.
- **`expanded_prompt`** column = the **submitted** (rewritten) prompt.
- **`operations.metadata_json.tool`** = the applied tool descriptor.

This is **redaction-aware**, gated on `GFLOW_CLI_HISTORY_PROMPTS`
(see [CONFIGURATION.md](CONFIGURATION.md) and [DATA_LAYER.md](DATA_LAYER.md)):

| Mode | `expanded_prompt` | `metadata_json.tool` |
|---|---|---|
| `store` (default) | stored | `{name, version, model, params, config_hash}` |
| `redacted` | withheld | `{name, version, params_hash, config_hash}` |

In `redacted` mode the recorder builds the minimal descriptor **itself** — it does not dump the
full `tool` dict and lean on key-name redaction, because a free-text option (e.g.
`params.style`) would otherwise leak verbatim. `params_hash` is a SHA-256 of the sorted params;
`config_hash` is a SHA-256 of the resolved `ToolConfig`, pairing with the hand-bumped `version` for
tamper-evidence. The `metadata_json.tool` write rides the post-success recorder safety wrapper, so
a recording failure warns and returns exit 0 — never a non-zero exit after a paid generation.

---

## 9. Relationship to the `expand_prompt` MCP prompt

The MCP server also exposes an older, now-**deprecated** **prompt template** named `expand_prompt`
([`mcp/prompts.py`](../src/gflow_cli/mcp/prompts.py)). It predates the tools framework and overlaps
functionally with `creative-director`, but they are **different surfaces**:

| | `expand_prompt` (MCP prompt) | `creative-director` (tool) |
|---|---|---|
| Kind | MCP **prompt template** — returns text for the *client's* model to expand. | MCP **tool** / CLI `--tool` — runs server-side. |
| API call | None (no Gemini call; the client model does the work). | Calls Gemini via `PromptExpander`. |
| Input | 5 structured slots (`subject`, `action`, `setting`, `camera`, `lighting`). | A single free-text prompt + optional `style`. |
| Banned-keyword stripping | No. | Yes (13 terms). |
| Domain styles | No. | Yes (15). |
| Provenance | No. | Yes (`expanded_prompt` + `metadata_json.tool`). |
| Formula | Treats lighting as a 5th separate component. | Folds lighting into the Style component. |

**Guidance:** prefer the `creative-director` tool (`--tool` / the MCP `tools` array) for actual
generation — it is the maintained, provenance-recording, server-side path. `expand_prompt` is
**deprecated** as of the unreleased line: its client-visible description carries a `[DEPRECATED]`
marker pointing to the tool, and it is **slated for removal in a future major release**. It remains
functional for now because some MCP clients surface prompts as user-pickable templates (a distinct
UX from agent-invoked tools), but no new work should depend on it. See
[TOOLS.md §8](TOOLS.md#8-mcp-exposure).

---

## 10. Configuration

| Env var | Default | Purpose |
|---|---|---|
| `GFLOW_CLI_GEMINI_API_KEY` | — | Required to actually rewrite prompts. [Get a key](https://aistudio.google.com/apikey). |
| `GFLOW_CLI_HISTORY_PROMPTS` | `store` | `redacted` withholds the expanded prompt and minimizes the recorded tool descriptor (§8). |

See [CONFIGURATION.md](CONFIGURATION.md) for the full precedence chain.

---

## 11. Credit

The Creative Director patterns — the five-component formula, the banned-keyword policy, and the
domain-vocabulary libraries — are adapted from the **banana-claude** evaluation
(PR [#202](https://github.com/ffroliva/gflow-cli/pull/202)) of Google's published Gemini
prompt-engineering guidance.

---

## 12. See also

- [TOOLS.md](TOOLS.md) — the tools framework.
- [CONFIGURATION.md](CONFIGURATION.md) — env vars and precedence.
- [DATA_LAYER.md](DATA_LAYER.md) — `expanded_prompt`, `metadata_json`, redaction.
- [USAGE.md](USAGE.md) — command reference for `image` / `video`.
