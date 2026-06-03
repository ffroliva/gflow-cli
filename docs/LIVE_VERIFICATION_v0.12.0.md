# Live verification — v0.12.0

> Evidence record for the v0.12.0 feature release. v0.12.0 adds three new command
> surfaces: **`gflow character`** (reusable, project-scoped Flow Character entities —
> `create` / `list` / `show` / `voices`, #145), **`gflow scene`** (Add Clip / Scenes
> compose + credit-free server-side extended video via `runVideoFxConcatenation`), and
> **`gflow video chain`** (last-frame I2V chaining from a JSONL manifest). It also fixes
> create-project generation when Flow surfaces Agent mode as a docked chat panel.
>
> The highest-risk paid surfaces are the credited generation paths: `gflow character
> create` (two-step face + triptych body via Flow's own page JS, Option B passive
> capture) and each paid Veo link in `gflow video chain`. Both were live-verified on a
> real Pro account during feature development (see CHANGELOG [0.12.0] and the
> `gflow character` / `gflow video chain` docs).

## Environment

- Date: 2026-06-03
- gflow-cli version: 0.12.0
- Python: 3.11+ (CI matrix: 3.11 / 3.12 / 3.13)
- Chrome: headed, real-Chrome strategy mandatory
- OS: Windows 11 primary dev; macOS / Linux on CI

## Pre-tag gates

Run before signing the tag (mechanical quality gates from `/gflow:check`):

```bash
python scripts/ci/check_repo_hygiene.py
python -m ruff check --fix src tests
python -m ruff format src tests
python -m pyright src
python -m pytest -q   # no coverage locally — see note
```

> The unscoped `--cov=gflow_cli` coverage run OOMs / closes the local sandbox on
> this Windows setup, so the suite is run without coverage locally and the
> coverage XML is produced by CI.

Observed — 2026-06-03, `chore/release-v0.12.0` branch (cut from `origin/develop`):

- repo hygiene: 419 tracked files checked, no violations
- ruff check: all checks passed (no fixes needed)
- ruff format: 195 files left unchanged
- pyright: 0 errors, 0 warnings, 0 informations
- pytest (no coverage): **1376 passed, 5 skipped, 58 deselected** in ~197s
- doc-link checker (`scripts/ci/check_doc_links.py`): all links resolved across 23 files
- `uv lock --check` caught a stale lockfile on `develop` (the video-chain feature declared
  `av` / `numpy` in `pyproject.toml` but the lock was never regenerated); fixed here by
  `uv lock`, which added `av v17.0.1` + `numpy v2.4.6` and bumped the `gflow-cli` pin to 0.12.0
- `/gflow:doc-review`: see fix-plan note below

### `/gflow:doc-review` fix-plan

_Mechanical pass (sections 1–7): PASS after fixes. §1 version refs — `pyproject.toml`,
`__init__.py`, `uv.lock` bumped to 0.12.0; README "Project status",
`docs/PROJECT_STATUS.md` "Current release" + four milestone rows, and `PLAN.md`
"Last revised" updated to v0.12.0. §3 evidence file — this file created. §4 link
checker — all resolved. §2/§7 grep "dead links" were regex false positives (authoritative
§4 checker is clean), consistent with the `doc-review-grep-false-positives` memory._

_Council audit (section 8): **GREEN / YELLOW / YELLOW** across the three auditors.
Auditor 3 (Drift): **GREEN** — no fictional claims; all three new surfaces verified
really implemented (character create/list/show/voices, scene create/show, video chain),
29-voice catalog real, exit-code map matches, `experimental/` exists — safe for PyPI.
Auditors 1 (Completeness) and 2 (Cross-reference): **YELLOW**, both flagging the same
theme — the new commands were under-documented for discovery. All flagged gaps fixed in
the release-prep commit: `docs/USAGE.md` gained `gflow scene` + `gflow character` reference
sections; `docs/CHARACTER.md` "Proposed CLI surface" → "shipped v0.12.0"; `docs/INDEX.md`
topic-shortcut cues for scene/chain/character; and the agent-facing surfaces (`AGENTS.md`
module list + command surface + exit-code range 3→21, `llms.txt`, `skills/gflow-cli/SKILL.md`,
`docs/AGENT_GUIDE.md`) now describe character/scene/chain. Doc-link checker re-run clean
(23 files). Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._

## Post-tag evidence

_To be filled after the tag is pushed and the release workflow publishes:_

- [ ] CI `Release` workflow run — completed / success
- [ ] GitHub Release published (`gh release view v0.12.0`)
- [ ] PyPI shows `gflow-cli 0.12.0`
- [ ] `pip install gflow-cli==0.12.0` in a clean venv imports and reports the right version
- [ ] `main → develop` back-merge completed
