# Live verification — v0.11.0

> Evidence plan for the v0.11.0 reliability release. The release repairs
> `gflow video i2v` — on v0.10.0 every i2v run silently produced text-to-video
> output that ignored the start/end frames (issue #125) — fixes create-project
> generation when Flow's "Agent" composer mode is active, and hardens
> image-model selection for non-English Flow UIs (issue #94). The highest-risk
> paid surface is the live `ui_automation` `gflow video i2v` path: it must route
> to the Veo i2v endpoint with the start/end frames attached and never fall back
> to text-to-video.

## Environment

- Date: 2026-05-31
- gflow-cli version: 0.11.0
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
uv run python -m pytest -q   # no coverage locally — see note
```

> The unscoped `--cov=gflow_cli` coverage run OOMs / closes the local sandbox on
> this Windows setup, so the suite is run without coverage locally and the
> coverage XML is produced by CI.

Observed — 2026-05-31, `chore/release-v0.11.0` branch (cut from `origin/develop`):

- repo hygiene: 334 tracked files checked, no violations
- ruff check: all checks passed
- ruff format: 146 files left unchanged
- pyright: 0 errors, 0 warnings, 0 informations
- pytest (no coverage): **1086 passed, 5 skipped, 51 deselected** in ~192s
- doc-link checker (`scripts/ci/check_doc_links.py`): all links resolved
- `/gflow:doc-review`: see fix-plan note below

### `/gflow:doc-review` fix-plan

_Mechanical pass (sections 1–7): PASS. §1 version refs — `pyproject.toml`,
`__init__.py`, `uv.lock` all bumped to 0.11.0; README "Project status",
`docs/PROJECT_STATUS.md` "Current release" + milestone rows, and `PLAN.md`
"Last revised" updated to v0.11.0. §3 — this evidence file created and the
`docs/INDEX.md` latest-release cue repointed to it. §4 doc-link check PASS.
§6 CHANGELOG footer PASS. The CHANGELOG gained a #94 entry (image-model
selector cascade) on top of the #125 and Agent-mode entries already present._

_Council audit (section 8): run as an inline single-pass cross-reference review
(README ↔ PROJECT_STATUS ↔ PLAN ↔ CHANGELOG ↔ source) rather than 3 parallel
subagents, due to degraded session tooling. No fictional claims found; the
release's doc surface is limited to version-currency + the #94/#125/Agent-mode
narratives, all verified against the merged source on `develop`._

## Paid live smoke (post-tag)

Before promoting the release, run one real-account smoke on the highest-risk
surface (the i2v path this release repairs):

```bash
# i2v must route to the Veo i2v endpoint with frames attached (no T2V fallback)
gflow video i2v ./start.png "slow push-in on the subject" \
  --model veo-lite --aspect 16:9 --duration 4 --json --profile <profile>
```

Pass criteria:

- The request hits the Veo image-to-video endpoint (not
  `batchAsyncGenerateVideoText`); the structured logs show the start frame
  attached and the model selected as `veo-lite`. A `WireFormatError` /
  `VideoModelSelectionError` / `ModelModeIncompatibilityError` must NOT be
  raised on a valid call.
- The downloaded MP4 visibly derives from the supplied start frame (not a
  pure text-to-video generation).
- `gflow video i2v --json` emits a single parseable JSON object on stdout with
  no log lines preceding it.

## Post-tag evidence

_Filled after the tag is pushed and CI publishes._

- [ ] CI release workflow green (build + PyPI publish + GitHub Release)
- [ ] `pip install gflow-cli==0.11.0` resolves from PyPI
- [ ] `gflow --version` reports `0.11.0`
- [ ] one paid live i2v smoke from the section above passes on a real account

## Conclusion

v0.11.0 is a reliability minor release. Its headline fix closes a paid-credit
correctness bug: `gflow video i2v` on v0.10.0 silently produced text-to-video
output that ignored the start/end frames. No user-facing breaking changes,
though `omni-flash` (and `--duration 10`) are no longer valid for `i2v` — they
remain valid for `t2v` and `r2v`.
