# Task 5 — Live Agentic Instructions Spike: Findings

**Feature:** API-Driven & Relational Agentic Instructions
**Profile:** `ffroliva` (agentic-enabled, authenticated)
**Date:** 2026-07-08
**Spike project (Phase A):** `6b714c4e-d94f-4109-91f4-7dfb06859618`

Hypotheses under test:
- **H1** — `PATCH agentInfo` round-trip works from a live profile (auth + wire format).
- **H2** — an `enabled: true` card actually influences the generated image.
- **H3** — an `enabled: false` card is ignored by the model.
- **H4** — `imageReferenceMediaIds` on a card anchors the model's visual style.

---

## Phase A — REST leg (FREE, no image credits) — ✅ RUN

Script: `scratch/spike_instructions_phase_a.py`. Created a project, probed the
`PATCH /v1/projects/{id}/agentInfo` route with three content-types, then ran the
production `patch_agent_info`, then attempted a plain `GET` read-back.

### Results

| Probe | Result |
|---|---|
| `create_project` on `profile_ffroliva` | ✅ HTTP 200 — **auth works** |
| PATCH agentInfo, `content-type: text/plain;charset=UTF-8` | ✅ **HTTP 200**, echoes updated agentInfo |
| PATCH agentInfo, `content-type: application/json` | ✅ **HTTP 200**, echoes updated agentInfo |
| PATCH agentInfo, `content-type: application/json+protobuf` | ❌ **HTTP 400** — "JSPB Fava message don't accept top-level braces" |
| Production `client.patch_agent_info(enabled=True, 2 cards)` | ✅ OK (uses `text/plain` via `_patch_json`) |
| Plain `GET /v1/projects/{id}/agentInfo` | ❌ **HTTP 404** — route/verb does not exist |

**H1: CONFIRMED.** The PATCH round-trip works. The card wire schema
`{id, title, description, enabled}` plus `projectBrief.enabled` is accepted and
**echoed back in the PATCH response body** — including our per-card `title`
values, so the server DOES persist distinct titles.

### 🐞 Bugs the spike caught (blockers for the feature)

1. **`agentic.py._reconcile_instructions` uses the wrong content-type.**
   It sends `content-type: application/json+protobuf` with a JSON-object body.
   Live server response: **HTTP 400** ("JSPB Fava message don't accept top-level
   braces"). Worse, the driver does **not check the response status** of
   `page.request.patch(...)`, so in production the instruction sync **fails
   silently** and no card is ever applied. Fix: use `text/plain;charset=UTF-8`
   (or `application/json`) — matching `client._patch_json`. Better: delete the
   duplicated inline PATCH and call `client.patch_agent_info` (single source of
   truth). See [[project_agentic_ui_driver]].

2. **`title` is hardcoded to `"Instruction title"`** in BOTH
   `client.patch_agent_info` and `agentic.py._reconcile_instructions`. The server
   preserves titles, so every card collapses to the same title — this breaks
   Task 7's case-insensitive **title-matching CRUD** (`enable`/`disable`/`rm`
   by title) before it is even built. `AgentInstruction` needs a `title` field
   (or derive+require one) threaded through both PATCH builders.

3. **No plain `GET` for agentInfo (404).** Task 7 plans `get_agent_info()`
   returning a `ProjectBrief`. The route `GET /v1/projects/{id}/agentInfo` does
   NOT exist. The read-back path is the **PATCH response echo** (already returns
   the full updated `agentInfo`) — or another route still to be discovered
   (candidate: it may ride inside `projectInitialData` / a tRPC call). Task 7
   must not assume a REST GET on this path.

### Corrections to the current implementation
- `client._patch_json` **discards** the response body (returns `{}`). Since the
  PATCH echoes the authoritative post-update `agentInfo`, `patch_agent_info`
  should **return the parsed brief** so callers (and `instructions list`) can
  confirm/read state without a separate GET.

---

## Phase B — Visual UI verification (Chrome MCP, live session) — ✅ RUN

Opened project `6b714c4e…` in the live authenticated agentic editor and clicked
the composer's Agent-Instructions button.

**Result: CONFIRMED.** The "Agent Instructions" sidebar rendered **both cards
set via the REST `patch_agent_info`**, with the correct toggle states:
- Card 1 — toggle **ON** — "Every image MUST be rendered as a flat 2D children's
  crayon drawing…"
- Card 2 — toggle **OFF** — "Every image MUST be a dark, gritty, photorealistic
  film-noir still…"

Both cards showed the title **"Instruction title"** in the UI — visual proof of
the hardcoded-title bug. Each card exposes a **"Reference"** (+) button (the H4
image-reference attach surface). Server→UI reflection works end-to-end via the
`text/plain` PATCH.

## Phase C — Generation hypotheses (2 credits spent) — ✅ RUN — ⚠️ SURPRISING

State at generation: crayon card **ENABLED**, film-noir card **DISABLED**.
Prompts were submitted through the live agent composer (Agent mode on).

| # | Prompt (style source) | Output |
|---|---|---|
| 1 (test) | "Generate one image: a cat sitting on a wooden chair next to a window" — **style only in the ENABLED card** | **Photorealistic** cat by a garden window. **No crayon.** |
| 2 (control) | same subject **+ "as a flat 2D children's crayon drawing on textured paper" in the PROMPT** | **Unmistakable crayon drawing.** |

Evidence: side-by-side saved to disk (control left = crayon, test right = photoreal).

### First-pass verdict (imperative directive) — MISLEADING, see Phase D

The first two generations used the **imperative** `"Generate one image: …"` form
(what `_compose_directive` emits). Result: the enabled crayon card was **ignored**
(photorealistic output), while the same style put *in the prompt* produced crayon.
This initially looked like "cards don't work" — but Phase D shows it's a
**phrasing** effect, not a card effect.

## Phase D — Conversational vs imperative phrasing (1 credit) — ✅ RUN — 🎯 ROOT CAUSE

Same project, crayon card still **ENABLED**, film-noir **DISABLED**. Submitted a
**conversational** request with NO style words and NO "generate" directive:

> "Make me a picture of a cat sitting on a wooden chair next to a window."

**Result: the agent rewrote the image-tool prompt to**
> "a cat sitting on a wooden chair next to a window, **flat 2D children's crayon
> drawing on textured paper**"

…and produced a **crayon drawing**. The card's text was injected verbatim by the
agent even though the user never typed it. The composer showed an agent reasoning
step ("Defining the Core Elements") before the tool call.

### The mechanism (definitive)

Instruction cards steer generation **only through the agent's reasoning path**:

| Prompt form (what the composer receives) | Enabled card applied? | Output |
|---|---|---|
| **Imperative** — `"Generate one image: {prompt}"` | ❌ No — literal passthrough to the image tool | photorealistic |
| `"Generate one image: {prompt}, as crayon…"` (style in prompt) | n/a (control) | crayon |
| **Conversational** — `"Make me a picture of {subject}."` | ✅ Yes — agent rewrites the tool prompt and folds the enabled card in | crayon |

### Verdict on hypotheses

- **H2 — enabled card influences output: ✅ CONFIRMED, conditionally.** It works,
  but **only when the request goes through the agent's reasoning path** (natural
  conversational phrasing). An imperative `Generate …:` directive bypasses it.
- **H3 — disabled card ignored: ✅ SUPPORTED.** In the conversational run only the
  **enabled** crayon card was injected; the disabled film-noir card was not (no
  noir styling, no noir text in the rewritten prompt).
- **H4 — reference images anchor style: not tested**, but now plausible via the
  same reasoning path (each card exposes a "Reference" button; docs emphasise
  "drop a reference image into the panel"). Probe later with an uploaded asset.

### 🔴 Headline conclusion (feature-level) — the load-bearing implementation defect

The feature IS viable — cards genuinely steer output — **but our production
agentic transport cannot trigger it as written.** `AgenticFlowUiDriver._compose_directive`
emits `"Generate {n} image(s)[ in {aspect} aspect ratio]: {prompt}"`, i.e. the
**imperative form that the agent passes through literally**. With that phrasing,
instruction cards have **no effect on gflow-cli generations**, regardless of how
correctly we PATCH them.

**Required design change (new, from this spike):** the agentic transport must
submit prompts in a form that engages the agent's reasoning/prompt-rewriting
(conversational phrasing, e.g. drop the "Generate N images:" scaffold and let the
count/aspect be expressed conversationally or via the settings popover), OR we
accept that instruction cards only matter for interactive/movie use and never for
the CLI's direct t2i/i2i. This must be decided before Task 6/7.

Corroborating docs (support.google.com/flow + third-party guides): the brief is
described as shaping "the Agent's behavior" / a "living brief" the agent
"references every session" — consistent with a **reasoning-time injection**, not a
model-level style lock. Google's own help does NOT document generation mechanics
(vague: "improve consistency in the Agent's behavior"). See findings §Docs below.

## Phase E — Live e2e through the real gflow transport (3 credits) — ✅ RUN — 🎯 SECOND ROOT CAUSE

Wrote and ran `tests/e2e/test_live_agentic_instructions.py` (marked `e2e`/`e2e_image`,
`GFLOW_CLI_FORCE_AGENT_UI=1`, profile `ffroliva`). It drives the ACTUAL transport:
create project → agentic bind → `_reconcile_instructions` PATCH → conversational
submit → scrape. First runs produced **photorealistic** output even though the
agentic driver bound and the reconcile PATCH succeeded — the conversational-phrasing
fix alone was NOT enough.

**Second root cause (the e2e caught what Chrome exploration masked):** the driver's
reconcile PATCHed only `project_brief.cards`, never the brief-level MASTER switch
`project_brief.enabled`. On a fresh project that flag defaults **off**, so the agent
ignores every card regardless of per-card `enabled` or phrasing. My manual Chrome
Phase D accidentally had it set (Phase A's `patch_agent_info(enabled=True)`), which
hid the requirement. (A page `reload` after PATCH was tried and **ruled out** — it
did not help; reverted.)

**Fix:** `_reconcile_instructions` now PATCHes
`updateMask=project_brief.enabled,project_brief.cards` with `enabled: true`.
Re-ran the e2e → **unmistakable crayon drawing** from a style-neutral prompt.
Both conditions are required and now both hold:
1. conversational `_compose_directive` (engages reasoning), and
2. `projectBrief.enabled = true` (master switch on).

### 🚦 Task 5 gate — now GREEN with a redesign requirement

All hypotheses have observed answers. Cards work; the primary implementation path
does not trigger them. Recommended sequence:
1. **Fix the 3 REST bugs** (content-type, per-card title, return the PATCH echo).
2. **Re-design the agentic prompt submission** to use the reasoning path (this is
   the difference between the feature working and being dead on the CLI).
3. Add a live e2e that asserts a conversational agentic generation adopts an
   enabled card's style (the crayon test, automated).
4. Then proceed to Task 6 docs-first with an accurate mechanism description.

### Docs research (support.google.com/flow + guides)
- Official help (answer 17093911): "To improve consistency in the Agent's
  behavior across your entire project, add instructions for it." — no generation
  mechanics documented; confirms it's an Agent-behavior brief.
- Third-party guides frame it as a persistent "system-level brief the Agent
  references every session," used with reference images and conversational
  requests ("make five versions of this scene…"). Consistent with reasoning-time
  injection observed in Phase D.
