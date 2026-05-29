# Live verification — v0.10.0

> Evidence plan for the v0.10.0 minor release. The release adds machine-readable
> `--json` output across every generation command plus a `gflow models` catalog,
> per-model reference-image caps for `i2i`/`r2v`, profile Google-account
> persistence (issue #92), external cloud storage via `GFLOW_CLI_STORAGE_URI`,
> and a `gflow data prune` maintenance command. The highest-risk paid surfaces are
> the per-model ref-cap tripwires (`i2i` / `r2v`) and the `--json` contract on the
> live `ui_automation` video path.

## Environment

- Date: 2026-05-29
- gflow-cli version: 0.10.0
- Python: 3.11+ (CI matrix: 3.11 / 3.12 / 3.13)
- Chrome: headed, real-Chrome strategy mandatory
- OS: Windows 11 primary dev; macOS / Linux on CI

## Pre-tag gates

Run before signing the tag (mechanical quality gates from `/gflow:check`):

```bash
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check --fix src tests
uv run ruff format src tests
uv run pyright src
.venv/Scripts/python.exe -m pytest -q   # no coverage locally — see note
```

> The unscoped `--cov=gflow_cli` coverage run OOMs / closes the local sandbox on
> this Windows setup, so the suite is run without coverage locally and the
> coverage XML is produced by CI.

Observed — 2026-05-29, `chore/release-v0.10.0` branch:

- repo hygiene: 323 tracked files checked, no violations
- ruff check: all checks passed
- ruff format: 146 files left unchanged
- pyright: 0 errors, 0 warnings, 0 informations
- pytest (no coverage): **1047 passed, 5 skipped, 50 deselected** in ~198s
- `/gflow:doc-review`: see fix-plan note below

### `/gflow:doc-review` fix-plan

_Council verdict: **YELLOW** (Auditor 1 Completeness YELLOW, Auditor 2 Cross-reference GREEN, Auditor 3 Drift YELLOW; no RED). All v0.10.0 feature claims verified against real code. 5 findings: 1 Tier 1 + 3 Tier 2 fixed in the release-prep commit, 2 Tier 3 deferred to backlog._

- **Tier 1 (fixed):** C1 — `Paid live smoke` used the invalid video alias `veo-3.1-fast`; corrected to `veo-fast` (the real `VideoModel` alias).
- **Tier 2 (fixed):** I1 — `docs/USAGE.md` now documents the `gflow models` command and the `--json` output contract; S1 — `docs/ARCHITECTURE.md` corrected to state the `experimental/` transports now exist as standalone modules under `api/transports/experimental/`; S2 — removed the dead `gflow_cli/providers/base.py` path from the system-overview diagram (Provider abstraction is planned, not yet a package).
- **Tier 3 (backlog):** `llms.txt` and `USER_GUIDE.md` Journey 14 predate `--json` (track for next docs pass); `Post-tag evidence` section below is intentionally a placeholder until publish.

Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._

## Paid live smoke (post-tag)

Before promoting the release, run one real-account smoke per high-risk surface:

```bash
# --json contract on the live video path
gflow video t2v "slow cinematic dolly across a quiet workshop" --aspect 16:9 \
  --model veo-fast --duration 4 --json --profile <profile>

# per-model i2i ref cap (attach at-cap refs; assert all consumed)
gflow image i2i .\ref1.png .\ref2.png .\ref3.png "compose these references" \
  --model nano-banana-2 --profile <profile>

# models catalog round-trips into the generation commands
gflow models --json
```

Pass criteria:

- `gflow video t2v --json` emits a single parseable JSON object on stdout with no
  log lines preceding it (`json.loads(stdout)` succeeds); a successful generation
  downloads one playable MP4.
- `gflow image i2i` consumes every reference up to the model cap (one
  `reference_attached` event per ref) and rejects over-cap input with exit 2
  before any network work.
- `gflow models --json` lists models whose aliases each round-trip back into the
  generation commands' `--model` choice.

## Post-tag evidence

_Filled after the tag is pushed and CI publishes._

- [ ] CI release workflow green (build + PyPI publish + GitHub Release)
- [ ] `pip install gflow-cli==0.10.0` resolves from PyPI
- [ ] `gflow --version` reports `0.10.0`
- [ ] one paid live smoke from the section above passes on a real account

## Conclusion

v0.10.0 is a feature minor release that hardens gflow-cli for machine-to-machine
use (worker schedulers driving the CLI via `--json` and `gflow models`) while
adding cloud storage and data-layer maintenance. No user-facing breaking changes.
