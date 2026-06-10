# scripts/diag/ — Diagnostic Investigation Scripts

These scripts require a live authenticated Chrome profile to run. They are investigation tools — not CI gates and not developer workflow tools — that help answer "what does this system actually do?" questions by observing a real browser session. Each script produces human-readable or structured output and does not modify production state.

## Scripts

| Script | Purpose | Prerequisite | Run |
|---|---|---|---|
| `capture_flow_traffic.py` | Capture Flow's outgoing API requests via `page.route` | `gflow auth login` | `uv run python scripts/diag/capture_flow_traffic.py --profile NAME` |
| `recaptcha_mint.py` | What `site_key` + token does gflow-cli's `TokenMinter` produce? | `gflow auth login` (headed) | `uv run python scripts/diag/recaptcha_mint.py --profile NAME` |
| `memory_profile.py` | Chrome process-tree RSS at key milestones (issue #155) | `gflow auth login` + `pip install psutil` | `uv run python scripts/diag/memory_profile.py --profile NAME` |

## What belongs here

1. Requires a live authenticated session to run
2. Produces human-readable or structured output for investigation
3. Does not write to production data stores
4. Answers a specific "what does this system actually do?" question

## What does NOT belong here

- **CI gates** → `scripts/ci/` (run on every commit, no auth needed)
- **Developer workflow tools** → `scripts/dev/` (`active_plan.py`, `skillopt/`, etc.)
- **Live smoke tests** → `scripts/` root (`smoke_*.py` — full end-to-end generation runs)

## Platform notes

- `memory_profile.py` requires `psutil`: `uv add --dev psutil` (not a hard dep — detected at runtime)
- All scripts: `uv run python scripts/diag/<name>.py` (no global Python install needed)
