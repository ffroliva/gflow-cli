# SkillOpt harness for gflow-cli

Measures how accurately an LLM agent answers gflow-cli usage questions when
guided by a skill document. Provides the rollout → score step of the
[SkillOpt](https://github.com/microsoft/SkillOpt) loop so you can measure
baseline accuracy, manually edit the skill, and confirm the score improves
before running the full automated SkillOpt optimizer.

## Quick start

```bash
# Dry-run: inspect prompts and scoring specs. No key, no network.
uv run python scripts/dev/skillopt/harness.py --dry-run

# Live run. Configuration is the project's own GFLOW_CLI_LLM_* settings — the
# same ones the prompt tools use (docs/CONFIGURATION.md, .env.template).
GFLOW_CLI_LLM_API_KEY=... uv run python scripts/dev/skillopt/harness.py

# Any OpenAI-compatible endpoint: OpenRouter, LiteLLM, freellmapi, a corporate
# gateway, or a local Ollama/LM Studio. No provider flag — the URL is the choice.
GFLOW_CLI_LLM_API_KEY=sk-or-... \
GFLOW_CLI_LLM_BASE_URL=https://openrouter.ai/api/v1 \
GFLOW_CLI_LLM_MODEL=openai/gpt-4o-mini \
  uv run python scripts/dev/skillopt/harness.py

# Filter to a surface area
uv run python scripts/dev/skillopt/harness.py --tags auth,error-recovery --dry-run

# Compare a candidate skill edit against the same tasks
uv run python scripts/dev/skillopt/harness.py --skill /tmp/SKILL_candidate.md

# A skill outside this directory brings its own suite — pass BOTH, or you score
# the new doc against the gflow-cli tasks and the numbers mean nothing.
uv run python scripts/dev/skillopt/harness.py \
  --skill skills/video-production/SKILL.md \
  --tasks skills/video-production/tasks.json
```

`uv run` is required: the harness imports `gflow_cli` for its LLM settings and
HTTP transport, so a bare `python` outside the project venv cannot import it —
including `--dry-run`.

## Task dataset (`tasks.json`)

20 scored scenarios covering the full CLI surface:

| Tag | Count | What it tests |
|---|---|---|
| `auth` | 4 | login, status, multi-profile, expired-session recovery |
| `video` | 5 | T2V, I2V, aspect, output path, batch manifest |
| `image` | 6 | T2I, I2I, fan-out, seed, UUID reuse, model selection |
| `error-recovery` | 3 | reCAPTCHA headless, auth expiry, Playwright missing |
| `python-api` | 1 | Library async usage pattern |
| `parallel` | 1 | Multi-profile parallel generation (anti-pattern guard) |

Each task item:
```json
{
  "id": "auth-001",
  "question": "...",
  "context": "...",
  "expected": {
    "must_include": ["gflow auth login"],
    "must_not_include": ["gflow login"],
    "partial_credit": ["playwright install chromium"]
  },
  "tags": ["auth", "first-run"],
  "notes": "Why this test case matters."
}
```

## Scoring

| Field | Effect |
|---|---|
| `must_include` | Each missing item deducts `1/N` from the base score |
| `must_not_include` | Each hit deducts `0.30` |
| `partial_credit` | Each hit adds `0.10` (max `+0.30` total) |
| Final score | `max(0.0, min(1.0, base − penalties + bonus))` |

A task **passes** at score ≥ 0.80.

## The SkillOpt improvement loop (manual)

SkillOpt's automated loop (rollout → reflect → edit → validate) can be
run against these tasks once a mock transport exists. Until then, the
manual equivalent is:

1. **Baseline** — run the harness, note which tasks fail and why.
2. **Reflect** — read the failure reasons; identify patterns (wrong flag,
   missing prerequisite, wrong subcommand).
3. **Edit** — update `skills/gflow-cli/SKILL.md`:
   - Add the missing information to the relevant section.
   - Add the mistake to the `## Known agent failure modes` table.
   - Bump `skillopt_epoch` in the frontmatter.
4. **Validate** — re-run the harness with `--skill` pointing at the edited file.
   Only accept the edit if the total score improves.
5. **Commit** — commit the improved skill doc.

## Running the full SkillOpt optimizer

To run the real SkillOpt optimizer against these tasks (automated edits):

```bash
# Install SkillOpt
git clone https://github.com/microsoft/SkillOpt
cd SkillOpt
pip install -e .

# The gflow-cli task format is compatible with SkillOpt's custom benchmark
# interface. A config and adapter are needed (future work — see PLAN.md).
```

The blocker for automated SkillOpt training is that every rollout currently
touches Playwright + reCAPTCHA + the live Google API. Once
`GFLOW_CLI_PROVIDER=mock` is available (tracked in PLAN.md), the harness
can be wired directly into the SkillOpt `train.py` script.

## Adding new tasks

Edit `tasks.json`. Keep each task focused on a single skill gap. Check
the `notes` field documents *why* agents fail this task — that context
guides future optimizer runs.
