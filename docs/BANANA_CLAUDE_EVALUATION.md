# Evaluation: banana-claude

> **Source repository**: [AgriciDaniel/banana-claude](https://github.com/AgriciDaniel/banana-claude)
> **Evaluated**: 2026-06-24
> **Purpose**: Competitive/inspirational analysis — what patterns can gflow-cli adopt?

---

## 1. What it is

banana-claude is a Claude Code skill that wraps **Google Gemini image generation** (Nano Banana / Gemini 3.1 Flash). Rather than acting as a thin API wrapper, it positions Claude as a **Creative Director**: interpreting user intent, selecting a creative domain mode, and constructing structured, production-quality prompts before ever calling the Gemini API.

Version: 1.4.1 · License: MIT · Language: Python (94%), Shell (6%)

---

## 2. User-facing commands

| Command | Purpose |
|---|---|
| `/banana generate` | Full creative-director pipeline — interprets intent → constructs prompt → generates |
| `/banana edit` | Intelligent image editing with instruction rephrasing |
| `/banana chat` | Multi-turn visual sessions maintaining style/character consistency |
| `/banana inspire` | Browse 2,500+ curated prompt database |
| `/banana batch` | Generate variations of a concept |
| `/banana preset` | Save and recall brand/style presets |

All commands are handled by a single `SKILL.md` entry point that dispatches based on the subcommand. This is the same pattern gflow-cli uses.

---

## 3. Repository structure

```
banana-claude/
├── .claude-plugin/
│   ├── plugin.json          ← plugin identity (name, version, tags)
│   └── marketplace.json     ← distribution metadata for Claude plugin marketplace
├── agents/
│   └── brief-constructor.md ← sub-agent: constructs the final API prompt
└── skills/banana/
    ├── SKILL.md             ← main orchestration layer (7-step pipeline)
    └── references/          ← knowledge files the skill reads on demand
        ├── prompt-engineering.md
        ├── gemini-models.md
        ├── mcp-tools.md
        ├── cost-tracking.md
        ├── presets.md
        └── post-processing.md
    └── scripts/             ← Python fallback scripts (stdlib-only, no deps)
        ├── generate.py
        ├── edit.py
        ├── batch.py
        ├── cost_tracker.py
        ├── presets.py
        ├── setup_mcp.py
        └── validate_setup.py
```

---

## 4. Architecture patterns

### 4.1 Reference-document decomposition

`SKILL.md` is an **orchestration layer only** — it contains no factual knowledge about APIs, models, or prompt rules. All domain knowledge lives in dedicated reference files the skill reads when needed:

- `gemini-models.md` — model IDs, pricing tiers, resolution caps, rate limits
- `prompt-engineering.md` — the 5-component formula, banned keywords, domain templates
- `mcp-tools.md` — MCP tool signatures, known parameter quirks, error codes
- `cost-tracking.md` — per-resolution pricing table
- `presets.md` — preset schema and examples
- `post-processing.md` — ImageMagick pipeline options

**Why this matters**: when Google updates a model or pricing, exactly one file changes. The skill's orchestration logic is untouched. This is a more disciplined version of what gflow-cli does inline in `SKILL.md`.

### 4.2 Dedicated sub-agent for a bounded sub-task

```
agents/brief-constructor.md
```

This sub-agent has a single responsibility: take a user request + domain selection and return a production-ready API prompt string. It applies the 5-component formula, enforces banned keywords, and outputs only the prompt text — no explanation, no preamble.

The main skill spawns it, receives the finished prompt, then calls the MCP tool. This keeps the main skill free of prompt-crafting logic and makes the constructor independently testable and improvable.

### 4.3 MCP primary + Python fallback

Happy path: `@ycse/nanobanana-mcp` MCP server handles all API calls.

Fallback: if MCP is unavailable, `scripts/generate.py` (Python stdlib only, zero external dependencies) handles the REST call directly. The skill documents both paths and automatically falls back.

This mirrors gflow-cli's approach but makes the fallback explicit and documented within the skill itself.

### 4.4 Plugin marketplace manifest

```json
// .claude-plugin/plugin.json
{
  "name": "banana-claude",
  "version": "1.4.1",
  "description": "AI image generation Creative Director powered by Google Gemini Nano Banana models.",
  "keywords": ["image-generation", "ai-art", "gemini", "creative-director", "prompt-engineering", "mcp"]
}
```

This `.claude-plugin/` structure is the distribution format for the Claude plugin marketplace. gflow-cli does not currently have this, which limits its discoverability within the ecosystem.

### 4.5 Enforced 7-step pipeline in SKILL.md

Every command, regardless of subcommand, is routed through the same ordered pipeline:

1. Analyze user intent and extract constraints
2. Check for matching saved presets
3. Select domain mode (Cinema / Product / Portrait / Editorial / UI-Web / Logo / Landscape / Infographic / Abstract)
4. Invoke `brief-constructor` sub-agent to build the structured prompt
5. Select aspect ratio and resolution
6. Execute MCP tool (`gemini_generate_image` or equivalent)
7. Verify output; if blocked, apply safety-filter rephrasing strategy

The pipeline is enforced by instruction text in SKILL.md — not by code. Claude is the state machine.

---

## 5. Prompt engineering as a first-class design decision

The most distinctive aspect of banana-claude is that **prompt engineering is systematized and version-controlled**, not ad-hoc.

### 5-Component Formula

```
Subject → Action → Location/Context → Composition → Style
```

Written as narrative prose, not keyword lists. Example transformation:

> User request: "a cat in space"
>
> Generated prompt: "A tabby cat in her mid-life floating weightlessly inside the cupola module of the International Space Station, paws gently pressed against the curved glass viewport, gazing at the curvature of Earth below. Medium shot from slightly below eye level with shallow depth of field isolating the cat against the distant blue planet. Shot on a Canon EOS R5 with 85mm f/1.4 lens, warm color grading reminiscent of National Geographic photography, soft fill light from earthglow."

### Banned keyword list

Never use: `4k`, `8k`, `masterpiece`, `highly detailed`, `hyperrealistic`, `photorealistic`, `best quality`, `award winning`.

Instead use prestigious anchors: `"Pulitzer Prize-winning cover photograph"`, `"National Geographic cover story"`.

This is API-specific knowledge (these tokens degrade Gemini output quality) encoded as an enforced rule.

### Domain modes

Nine creative specializations, each with its own vocabulary, lens preferences, and lighting conventions:

| Domain | Camera style | Reference aesthetic |
|---|---|---|
| Cinema | RED V-Raptor, Cooke S7/i lenses | Dramatic storytelling |
| Product | Polished surfaces, softbox diffusion | E-commerce photography |
| Portrait | 85–135mm focal lengths | Character study |
| Editorial | Vogue Italia references | Fashion/lifestyle |
| UI/Web | Flat vector, isometric 3D | Icons and illustrations |
| Logo | Geometric primitives, golden ratio | Branding |
| Landscape | Depth layering, atmospheric effects | Environmental |
| Infographic | Modular layouts | Data visualization |
| Abstract | Fractals, fluid dynamics | Generative art |

### Safety-filter rephrasing

When Gemini's `IMAGE_SAFETY` filter blocks a prompt, the skill has a documented strategy: rephrase through abstraction, artistic framing, metaphor, or context shifting. This is baked into the error-handling section of SKILL.md, not left to the user to figure out.

---

## 6. Technical specifications

| Parameter | Value |
|---|---|
| Default model | `gemini-3.1-flash-image-preview` (Nano Banana 2) |
| Fallback model | `gemini-2.5-flash-image` (Nano Banana) |
| Max resolution | 4096×4096 (4K) |
| Aspect ratios | 14 including extreme (1:4, 4:1, 1:8, 8:1) |
| Images per call | 1 (Gemini hard limit) |
| Output format | PNG with SynthID watermark |
| Auth | `GOOGLE_AI_API_KEY` env var |
| Rate limit handling | Exponential backoff on HTTP 429 |
| Error codes handled | `IMAGE_SAFETY`, `FAILED_PRECONDITION` (billing), HTTP 400/429 |
| Free tier | ~5–15 requests/minute |
| Pricing (Nano Banana 2) | 1K: $0.067 · 2K: $0.134 · 4K: $0.268 per image |

---

## 7. What gflow-cli could adopt

### HIGH VALUE — directly applicable

**A. Reference-document architecture**

gflow-cli's `skills/gflow-cli/SKILL.md` currently embeds facts about the API, auth flows, and reCAPTCHA behavior inline. These should be extracted into `skills/gflow-cli/references/`:

```
skills/gflow-cli/references/
  api-endpoints.md      ← reverse-engineered route inventory
  auth-lifecycle.md     ← cookie refresh, reCAPTCHA triggers, known failures
  recaptcha-notes.md    ← workarounds and detection patterns
  model-specs.md        ← Veo model IDs, video params, limits
  error-codes.md        ← known HTTP errors + resolution strategies
```

This makes SKILL.md durable even as the Flow API evolves.

**B. Sub-agent for video brief construction**

Veo prompts have their own craft (motion language, temporal descriptions, camera movement vocabulary). A dedicated `agents/video-brief-constructor.md` sub-agent — mirroring banana-claude's `brief-constructor.md` — would improve generation quality and allow independent iteration on prompt strategy.

**C. Systematized safety-filter rephrasing**

gflow-cli hits content filters. Rather than leaving rephrasing to the user, encode known strategies (abstraction, indirect framing, context anchors) in SKILL.md's error-handling section.

**D. Domain modes for video generation**

Apply the same 9-domain concept to video:

| Domain | Use case |
|---|---|
| Cinematic | Dramatic narrative footage |
| Documentary | Realistic, observational |
| Product | E-commerce / showcase |
| Animation | Stylized / non-photorealistic |
| Abstract | Generative / motion graphics |
| Social | Vertical format, high-energy |

### MEDIUM VALUE — worth considering

**E. Plugin marketplace manifest**

Adding `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` would make gflow-cli distributable via the Claude plugin ecosystem without code changes.

**F. Cost-tracking reference**

banana-claude tracks per-image costs. gflow-cli could maintain a `references/cost-tracking.md` with Veo video generation cost estimates (per-second pricing, quota limits) to help users make informed decisions.

**G. Validate-setup script**

`scripts/validate_setup.py` checks prerequisites before first use. gflow-cli has `gflow doctor` but a lightweight validate script invoked by the skill on first run would catch misconfiguration earlier.

### LOWER VALUE — gflow-cli already does this better

- **Auth complexity**: Gemini uses a simple API key; gflow-cli's cookie/reCAPTCHA auth is fundamentally harder. The `/gflow:known-issues` skill is a more sophisticated answer to this problem than anything banana-claude needs.
- **Session/batch persistence**: gflow-cli has richer state management for long-running video jobs than banana-claude's single-image-per-call Gemini model requires.
- **Release pipeline**: banana-claude has no versioning automation. gflow-cli's `gflow:release` skill, CHANGELOG automation, and semantic versioning are significantly more mature.
- **Plan/predict/scenario skills**: banana-claude has no equivalent of gflow-cli's pre-implementation adversarial analysis toolchain.

---

## 8. Gaps observed in banana-claude

| Gap | Notes |
|---|---|
| No structured error taxonomy | Error handling is described in prose, not a reference table |
| No test coverage | No test files found in the repository |
| No version pinning for MCP package | `@ycse/nanobanana-mcp` version is not locked |
| Single image per call | Gemini limitation, not a design flaw — but batch.py must loop, which risks rate-limit cascades |
| No transparency watermark handling | SynthID watermarks are mentioned but not handled in output post-processing |
| Preset persistence unspecified | How presets survive across Claude Code sessions is not documented |

---

## 9. Summary verdict

banana-claude is a well-designed skill with a genuinely useful architectural innovation: **the reference-document decomposition pattern** that keeps SKILL.md as a pure orchestrator. The systematized prompt engineering (5-component formula, domain modes, banned keywords, safety-filter strategies) is the most transferable concept — it shows how to encode API-specific and domain-specific knowledge in a maintainable, version-controllable way.

The two highest-leverage adaptations for gflow-cli are:

1. **Extract inline facts from SKILL.md into `references/` files** — improves maintainability and makes the skill resilient to API drift.
2. **Add a `video-brief-constructor` sub-agent** — improves Veo prompt quality using the same delegation pattern banana-claude uses for Gemini prompt construction.

---

*This document was produced by automated analysis of the public banana-claude repository on 2026-06-24.*
