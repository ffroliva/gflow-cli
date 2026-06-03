# Governance benchmark — calibrating the materiality gate

> How we measure whether the advisory materiality gate
> ([`scripts/ci/check_materiality.py`](../scripts/ci/check_materiality.py)) is
> *worth its friction*, and how to act on the numbers. Companion to
> [AGENT_GUIDE § Governance & Enforcement](AGENT_GUIDE.md#governance--enforcement).

## Why benchmark a governance gate

The gate flags changes to "material" paths (auth, transports, the auth-token
client, the data layer, reCAPTCHA) and recommends a `predict` + council pass. That
recommendation is only worth making if the gate is **well-aimed**: it should fire on
the code that actually breaks, and *not* nag people for trivial edits. Those are two
measurable properties, and guessing at them is how governance theatre starts. The
[predict council](../skills/predict/SKILL.md) *estimated* 20–30% false-positive
friction; the benchmark replaced that guess with a measured **~1%**.

## The tool

[`scripts/dev/materiality_backtest.py`](../scripts/dev/materiality_backtest.py)
replays non-merge commits from real git history and scores the **exact gate that
ships** — it imports `is_material` from the classifier, so the benchmark and the gate
can never disagree.

```bash
# Full history, human-readable Markdown
uv run python scripts/dev/materiality_backtest.py

# Machine-readable JSON (regression checks / CI snapshotting)
uv run python scripts/dev/materiality_backtest.py --json

# Scope to a window or a range
uv run python scripts/dev/materiality_backtest.py --limit 100
uv run python scripts/dev/materiality_backtest.py --range v0.10.0..HEAD
```

## What it measures

| Axis | Question | Metric | Want |
|---|---|---|---|
| **1 — friction** | Of commits touching a material path, how many were *trivial* (comment / blank / whitespace / rename only)? | false-positive rate | **low** |
| **2 — coverage** | Of `fix:` / `hotfix` / `revert` commits, how many touched a material path? | coverage % | **high** |

**Axis 1** judges "substantive" from a whitespace-insensitive (`git show -w`) diff of
the material files only, counting any added/removed line that is non-blank and not a
`#` comment. It therefore catches reformatting, comment-only, blank-line and
pure-rename churn. It does **not** detect Python docstring-only edits, so the reported
rate is a **conservative lower bound**.

**Axis 2** is a proxy: it assumes a `fix`/`revert` commit landing in a path means that
path was bug-prone. Fixes landing *outside* material paths are listed as **candidate
coverage gaps** — surfaces that bite us but the gate currently ignores.

## Baseline

Re-run and compare against this when you change `MATERIAL_PATHS` or suspect drift.

| Metric | Value (as of 2026-06-03, 247 commits) |
|---|---|
| Material-path commits | 89 (36.0% of all commits) |
| **False-positive rate (Axis 1)** | **1.1%** (1 of 89) |
| Fix / revert commits | 57 |
| **Fix-coverage (Axis 2)** | **73.7%** (42 of 57) |

History of the gate's calibration:

- **Initial 4-path gate** (`auth/`, `api/transports/`, `data/`, `recaptcha`): FP 1.4%,
  coverage 61.4%.
- **After adding `api/client.py` + `_sapisidhash.py`** (auth-token plumbing the
  backtest surfaced as 3 historical fixes outside `auth/`): FP 1.1%, coverage 73.7%
  — strictly better on both axes.

## How to act on the numbers

- **Axis 1 climbs** (say > 10%) → the path-only gate is becoming too blunt. Either
  tighten `MATERIAL_PATHS` to more specific files, or upgrade the gate to classify on
  diff content (skip comment/rename-only changes). At ~1% today, this is **not**
  warranted — a deliberate "don't build the complex thing" call backed by data.
- **Axis 2 is low / a clear pattern sits in the gap list** → a bug-prone surface is
  unguarded. Add the specific file(s) to `MATERIAL_PATHS`. Keep it **precise** (name
  the file, e.g. `api/client.py`) rather than broad (`api/`) so routine code stays
  unflagged. Then update [`skills/pr-council-review/SKILL.md`](../skills/pr-council-review/SKILL.md)
  §1 in the same commit — the `test_material_list_sync_passes_on_real_skill` test
  fails the build if the constant and the prose drift apart.
- **Re-run after every change** and confirm both axes moved the right way before
  committing. That is the calibration loop.

### Known non-gaps (intentionally not flagged)

- The `scene` command surface shows occasional fixes but is a **feature** area, not a
  security/data-integrity one — left routine on purpose.

## Cadence & automation

- **On demand** — before adding/removing a material path, or when reviewing the gate.
- **Before a release** — a quick `uv run python scripts/dev/materiality_backtest.py`
  is part of governance hygiene.
- **Automated** — [`.github/workflows/governance-benchmark.yml`](../.github/workflows/governance-benchmark.yml)
  runs it monthly (and on `workflow_dispatch`) and writes the report to the job
  summary. It is **non-blocking and read-only** (no token, fork-safe) — a dashboard,
  not a gate.

## See also

- [AGENT_GUIDE § Governance & Enforcement](AGENT_GUIDE.md#governance--enforcement) — the gate itself, hard-vs-advisory table.
- [`skills/pr-council-review/SKILL.md`](../skills/pr-council-review/SKILL.md) §1 — canonical material-path priority weights.
- [DEVELOPMENT.md](DEVELOPMENT.md) — quality gates and PR protocol.
