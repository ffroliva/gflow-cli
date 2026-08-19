# DSPy — strategic evaluation for `gflow-cli`

> **Status:** Evaluation / recon. No code change proposed for the current milestone.
> **Verdict:** **Do not adopt as a runtime dependency. Revisit as a dev-only, offline optimizer once a prompt-quality metric exists.**
> **Date:** 2026-08-19 · **Scope:** `src/gflow_cli/tools/`, `scripts/autopilot/`, `src/gflow_cli/mcp/`

This document answers two questions: *what is DSPy actually for*, and *is it strategically
relevant to this project*. It is written for a reader who has not used the framework.

---

## 1. What DSPy is

DSPy (Stanford NLP) is a Python framework whose slogan is **"programming, not prompting."**
The pitch: stop hand-writing prompt strings, and instead declare *what* you want in terms of
typed inputs and outputs, then let an **optimizer** search for the prompt text that maximises a
score you define.

Three abstractions carry the whole framework:

| Abstraction | What it is | Analogue in this repo |
|---|---|---|
| **Signature** | A declarative I/O spec — `"terse_prompt -> vivid_prompt"`, or a typed class. You do *not* write the prompt; DSPy renders one from the signature. | The `system_template` string in a tool TOML |
| **Module** | A strategy that executes a signature — `Predict`, `ChainOfThought`, `ReAct`. Composable, and they carry state. | `PromptExpander.expand()` |
| **Optimizer** | The point of the framework. Takes a program, a **training set**, and a **metric**, and searches instruction wordings and few-shot example selections to maximise the metric. | *nothing — no equivalent exists* |

The optimizer menu in 3.x is `BootstrapFewShot`, `BootstrapFewShotWithRandomSearch`,
`COPRO`, `MIPROv2` (Bayesian search over instructions **and** demos, jointly), `SIMBA`,
`GEPA` (reflective — the LM critiques its own trajectories and proposes repairs), and
`BootstrapFinetune`. Verified present in `dspy==3.3.0`.

### The load-bearing sentence

**DSPy's value is the optimizer, and the optimizer requires a programmatic metric plus a
dataset to score against.** A metric is a plain Python function
`metric(example, prediction) -> float | bool`.

Strip the optimizer away and what remains is a somewhat opinionated templating and parsing
layer — one that would be *competing with*, not adding to, the code this project already has.
This is the single fact that decides the question for `gflow-cli`, so everything below is
really about whether a metric can exist here.

---

## 2. The LLM surfaces in this repo

I audited every place `gflow-cli` puts text in front of a language model.

### 2.1 `src/gflow_cli/tools/` — prompt rewriting **(the only real candidate)**

Three built-in tools, each a TOML file with a hand-written `system_template`:

| Tool | Template size | Notes |
|---|---|---|
| `creative-director` | 71 lines / 3 644 chars | Google's 5-component formula + **15 domain styles** (9 image, 6 video) |
| `reverse-engineer` | 20 lines / 1 341 chars | Deconstructs a scene; has a multimodal path (video frames → prompt) |
| `storyboard` | 12 lines / 1 192 chars | Multi-panel scaffolding |

Execution path: `runtime.apply_tool()` → `build_instruction()` (template + optional domain
vocabulary) → `PromptExpander.expand()` → `strip_banned_keywords()`.

Users can also author their **own** tools as TOML files in a user directory (`loader.py`).
That extension model matters below.

### 2.2 `scripts/autopilot/pr_triage_gate.py` — deterministic, and must stay that way

`should_review()` is **pure Python rules**, no LLM. It is backed by `eval/pr_triage_eval.py`,
which pins expected verdicts and demands **100 %** match. It also carries prompt-injection
detection (regexes for `"you must set the verdict to…"`, `"override verdict/status/rules"`)
because PR titles and bodies are attacker-controlled.

This is the one surface here with a real metric — and it is deliberately *not* an LLM. Putting
a language model where a deterministic, injection-hardened gate stands today would be a
security regression, not an upgrade. **Out of scope for DSPy on purpose.**

### 2.3 `scripts/autopilot/` council review — not a prompt DSPy could own

The `council-claude` engine shells out to the **Claude Code CLI** inside a sandboxed
container. There is no in-process prompt for DSPy to optimize; the "prompt" is a skill
definition consumed by another agent process.

### 2.4 `src/gflow_cli/mcp/prompts.py` — no LLM call

`expand_prompt` is a deprecated MCP *template*: it returns an instruction string for the
calling client's own model. It makes no API call. Nothing to optimize.

**Conclusion:** the addressable surface is §2.1 and only §2.1.

---

## 3. Why DSPy does not fit today

### 3.1 There is no metric — and no cheap way to get one

This is the blocking objection.

The `tools` layer emits *a prompt*, which is then sent to Veo/Imagen, which produces *a video
or image*, whose quality is a **subjective human visual judgement** — and every sample
**costs Flow credits**. So a naïve DSPy metric would mean: run the optimizer, and for each of
several hundred candidate prompts, burn credits on a generation and ask a human to rate it.
`MIPROv2` and `GEPA` are built around exactly that kind of repeated rollout. The economics
are prohibitive and the loop is not automatable in its obvious form.

Supporting evidence — the repo confirms no signal exists yet:

- **83 tests** across `tests/tools/` (`test_expander.py`, `test_runtime.py`, `test_banned.py`, …).
  Every one is mechanical: transport shape, truncation, retry/backoff budgets, quote and
  code-fence stripping, banned-keyword removal, provenance snapshots. **Not one asserts
  anything about whether an expanded prompt is *good*.**
- The SQLite catalog has **no rating, score, favourite, or outcome column** — grepped across
  `data/*.py` and all nine migrations. There is no label to train against.

Without a metric, adopting DSPy buys the *shape* of optimization with none of the substance.

### 3.2 The dependency cost is severe, and directly contradicts a deliberate design stance

Measured, not estimated — `uv pip install dspy` into a clean venv:

```
Resolved 57 packages · ~162 MB on disk
```

Largest contributors:

| Package | Size |
|---|---|
| `litellm` | **83 MB** |
| `openai` | 15 MB |
| `hf_xet` | 12 MB |
| `tokenizers` | 11 MB |
| `aiohttp` | 6.4 MB |
| **`dspy` itself** | **3.8 MB** |

DSPy is 3.8 MB of the 162 MB; the rest is its transitive closure. Thirteen dependencies are
**mandatory** (`litellm`, `openai`, `orjson`, `regex`, `requests`, `pydantic`, `diskcache`,
`json-repair`, `tenacity`, `anyio`, `cachetools`, `cloudpickle`, `gepa`, `tqdm`).

Set against that, the module docstring of `expander.py` states the opposing principle
explicitly:

> **No new dependencies** — uses `urllib.request` rather than the project's `httpx` so the
> expander stays a self-contained, synchronous pre-processing step with a trivially mockable
> seam (the injected `transport` callable).

And `pyproject.toml` shows this is a settled policy, not an accident — nearly every dependency
carries a comment justifying its version bounds (the `playwright>=1.61.0,<1.62.0` pin, the
`mcp>=2.0.0,<3` bound after 2.0.0 deleted `fastmcp`). This is a `pipx`-installable CLI that
already ships a browser driver. Adding 57 packages so that three TOML files can be worded
differently is not a trade this project's own stated priorities would make.

### 3.3 It fights three existing architectural contracts

**Never-fatal.** `expander.py`'s contract is that a bad key, a 429, a network blip, or a
malformed response all degrade to *the original prompt* with `was_expanded=False` — callers can
always use the result verbatim. The docstring even explains why `tenacity` was rejected for
the retry loop: tenacity re-raises after exhausting retries, and *"this client's contract is the
opposite — it must never raise."* DSPy raises, and manages its own retry/caching (`diskcache`)
and client lifecycle. That is the same mismatch, one layer larger.

**Gateway-agnostic by design.** The expander speaks OpenAI Chat Completions deliberately, so a
user points `GFLOW_CLI_LLM_BASE_URL` at OpenRouter, LiteLLM, Ollama, LM Studio, or Google's
compat endpoint and is done — provider keys stay with the gateway. `resolve_model()` is a
documented single source of truth for precedence, and the comments record a real past defect: a
hardcoded Google model name silently 400'd on non-Google gateways. DSPy would introduce a
*second* LM-configuration system (`dspy.LM` over `litellm`) layered on the one that already
works — a new place for exactly that bug to recur.

**Users author tools in TOML.** DSPy signatures are Python classes. Adopting DSPy inside the
runtime either breaks the "My Tools" extension model or forces a parallel code path for
built-ins vs. user tools. Neither is attractive.

---

## 4. Where DSPy *could* genuinely pay off

There is a real version of this idea, and it is worth writing down.

**Use DSPy offline as a build-time optimizer; ship plain text; take zero runtime dependency.**

The `system_template` strings in the tool TOMLs are hand-written prose that nobody has ever
measured. If a metric existed, an optimizer could rewrite them measurably better — and the
*output* of that optimization is just text, which gets committed into the TOML. DSPy would live
in `[project.optional-dependencies] dev`, or in a separate tooling venv, and never ship to users.

I verified this export path works mechanically:

```python
>>> p = dspy.Predict("terse_prompt -> vivid_prompt")
>>> p.dump_state().keys()
dict_keys(['traces', 'train', 'demos', 'signature', 'lm'])
>>> p.dump_state()["signature"]["instructions"]
'Given the fields `terse_prompt`, produce the fields `vivid_prompt`.'
```

`signature.instructions` is the optimized instruction text as plain JSON — copyable straight
into a TOML `system_template`.

One caveat: `dump_state()` also carries `demos` (selected few-shot examples), and the current
expander sends only a system instruction plus one user message — there is no few-shot slot.
So either constrain the optimizer to instructions only (`MIPROv2.compile(...,
max_bootstrapped_demos=0, max_labeled_demos=0)` — both knobs verified present) or fold the
chosen demos into the template prose. This is a solvable detail, not a blocker.

### The prerequisite nobody can skip

**The missing asset is not DSPy. It is the evaluation.** The staged path:

| Stage | Work | Unlocks |
|---|---|---|
| **0** | Capture a quality signal — a rating/outcome column on `operations`, or a `gflow data rate` surface. The catalog already persists `prompt`, `expanded_prompt` (migration `0008`), and `metadata_json.tool` provenance (`name`, `version`, `model`, `config_hash`, `params`) — the *features* of a training set are already there; only the **label** is missing. | A dataset |
| **1** | Build a **text-only** metric that scores the *expanded prompt* without generating anything: 5-component-formula coverage, banned-keyword compliance (already deterministic in `banned.py`), intent/named-subject preservation vs. the original, length discipline. An LLM-judge plus deterministic checks. | **This is the real unlock** — it makes optimization possible without spending a single Flow credit |
| **2** | *Only then*: DSPy as a dev-only optimizer over the templates; commit the resulting text. | Measurably better built-in tools |

Note that Stage 1 is independently valuable **whether or not DSPy is ever adopted**. A metric
turns the 83 mechanical tests into 83 mechanical tests *plus a quality regression gate* —
today, someone editing `creative-director.toml` has no way to know whether they made it worse.
And with a metric in hand, Stage 2 could equally be done with `COPRO`, with a hand-rolled
search loop, or by hand — DSPy would then be a convenience, chosen on merit, rather than a bet.

---

## 5. Verdict

**Not now, and not as a runtime dependency.**

| Question | Answer |
|---|---|
| Is DSPy relevant to this project? | *Conceptually yes* — §2.1 is a genuine, unmeasured prompt-engineering surface. |
| Should it be adopted as a runtime dependency? | **No.** 57 packages / 162 MB into a `pipx` CLI, contradicting an explicit and well-reasoned zero-dependency stance, and conflicting with the never-fatal and gateway-agnostic contracts. |
| Should it be adopted as a dev-only offline optimizer? | **Not yet** — it would have nothing to optimize against. Revisit after Stage 1. |
| What should happen instead? | Build the metric (Stage 0 → 1). It is the prerequisite for DSPy *and* valuable standalone. |

The honest summary: DSPy is a good answer to a question this project has not yet asked.
The project cannot currently tell a good expanded prompt from a bad one — and until it can,
no optimizer of any brand has anything to hill-climb.

---

## References

- [DSPy optimizers — official docs](https://github.com/stanfordnlp/dspy/blob/main/docs/docs/learn/optimization/optimizers.md)
- [MIPROv2 API reference](https://dspy.ai/api/optimizers/MIPROv2/)
- [What is DSPy? Stanford's compiled prompt framework in 2026](https://futureagi.com/blog/what-is-dspy-2026/)
- [DSPy optimizers explained: BootstrapFewShot, MIPROv2, COPRO, GEPA](https://futureagi.com/blog/dspy-optimizers-explained/)
- [MIPROv2 vs GEPA — automatic prompt optimization](https://particula.tech/blog/dspy-gepa-vs-miprov2-automatic-prompt-optimization)
- In-repo: [docs/TOOLS.md](TOOLS.md), [docs/PROMPT_EXPANSION.md](PROMPT_EXPANSION.md), [docs/DATA_LAYER.md](DATA_LAYER.md)

*Measurements taken 2026-08-19 against `dspy==3.3.0` (Python 3.11, `uv`).*
