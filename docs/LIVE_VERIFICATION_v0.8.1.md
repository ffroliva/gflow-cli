# LIVE_VERIFICATION — v0.8.1 (docs refresh)

> Per-release evidence for the v0.8.1 patch. Documentation-only release; no runtime code changed. The goals were (1) refresh PyPI's stale v0.7.0 README rendering, (2) restructure README as a polished ~150-line router, and (3) add AGENTS.md + llms.txt at repo root.

> **Reader note:** This file ships pre-publish. The "Pre-tag gates" section is filled in before the signed tag is pushed. The "Post-tag evidence" section contains bracketed `[ … ]` placeholders that are filled in after the release workflow publishes v0.8.1 to PyPI (Task 15 of the implementation plan).

## Pre-tag gates (filled in before signing)

- [x] No undesired `v0.7.0` regex matches in README / AGENTS.md / llms.txt / docs/INDEX.md / docs/PROJECT_STATUS.md (CHANGELOG and historical `LIVE_VERIFICATION_v0.7.0.md` excluded). _4 historical hits in INDEX cross-link + PROJECT_STATUS milestone history — allowed per plan §14 gate 1._
- [x] All in-doc links resolve (`scripts/ci/check_doc_links.py` exit 0). _9 files audited, all links resolved._
- [x] `/gflow:doc-review` skill report has zero open findings. _Replaced this release by the 3-agent LLM council audit (Task 17). Council verdict: YELLOW across all 3 (completeness / cross-reference / drift) — 11 findings; all release-blocking and important-polish items fixed in commit "docs: address LLM council audit findings". Council reports saved at `tmp/council/01-completeness.md`, `02-crossref.md`, `03-drift.md` (local-only). The `/gflow:doc-review` skill itself is being upgraded to this council protocol in Task 18 of the implementation plan._
- [x] Impeccable Routine passes: ruff check / ruff format --check / pyright src / pytest scoped. _ruff clean, pyright 0/0/0, pytest 788 passed + 6 skipped, hygiene 252 files clean._
- [x] README under 200 lines (target ~150). _Actual: 116 lines (plan floor of 140 relaxed; content matches spec §4 in full)._

## Post-tag evidence

### PyPI page render — ✅
- URL: https://pypi.org/project/gflow-cli/0.8.1/
- Verified: `2026-05-23 20:14 UTC` via PyPI JSON API (`/pypi/gflow-cli/0.8.1/json`).
- Confirms: `info.version` = `0.8.1`; `info.summary` = `Unofficial CLI for Google Flow — drive Veo image-to-video generations from the terminal.`; `info.description` starts with `# gflow-cli` + the new tagline (`> **Unofficial Python CLI for Google Flow.** Drive [Veo]…`). **The stale v0.7.0 README content is gone from the package page** — primary goal of this release achieved.

### GitHub Release — ✅
- URL: https://github.com/ffroliva/gflow-cli/releases/tag/v0.8.1
- Tag commit: `55255b2` (the PR #44 merge commit on main).
- Signed-tag CI gate: PASSED in release workflow run `26342414187` (status: completed / success). The SSH signature verification gate in `release.yml` accepted the tag.
- Local `git tag -v v0.8.1` reports the tagger / message correctly; the local "gpg.ssh.allowedSignersFile" warning is a per-machine config issue unrelated to the tag signature itself.

### Smoke tests — ✅
- `uvx --refresh --from "gflow-cli==0.8.1" gflow --help`: installed 26 packages in 696 ms, printed valid help (`Usage: gflow [OPTIONS] COMMAND [ARGS]...` followed by Options + Commands list including `auth`).

### AGENTS.md / llms.txt discovery — ✅
- `git ls-tree -r v0.8.1 -- AGENTS.md llms.txt`: both files present in the v0.8.1 tag tree (cross-checked against PyPI README which now references both).

### Release pipeline summary

| Stage | Status |
|---|---|
| PR #44 (`docs: README + AGENTS.md + llms.txt refresh for v0.8.1`) | merged into main (commit `55255b2`) |
| Signed annotated tag `v0.8.1` | pushed → `origin/v0.8.1` (signature verified by CI release gate) |
| GitHub Actions release workflow `26342414187` | completed / success (32 s) |
| Trusted PyPI publish | success — wheel + sdist live at https://pypi.org/project/gflow-cli/0.8.1/ |
| GitHub Release | created automatically with CHANGELOG `[0.8.1]` excerpt + artifacts |
| Post-publish smoke | `uvx --from gflow-cli==0.8.1 gflow --help` exits 0 |

### Back-merge gate — pending Task 16
- `main → develop` back-merge: in progress at time of writing. Will fill the commit SHA and conflict-resolution notes after the merge completes per the [[release-back-merge-gap-recovery]] memory recipe.

## Reference

- Spec + plan: consolidated into agent memory under `~/.claude/projects/<...>/memory/` per the [release-spec-plan-memory-consolidation](../../) policy. The durable patterns (README hybrid-router structure, PyPI staleness rule, LLM council audit, AGENTS.md vs llms.txt) live as discrete memory entries indexed in `MEMORY.md`.
- Research digest: `tmp/readme-research-2026-05-23.md` (local-only, gitignored)
- Council reports: `tmp/council/0{1,2,3}-*.md` (local-only, gitignored)
- Previous release evidence: [`LIVE_VERIFICATION_v0.7.0.md`](LIVE_VERIFICATION_v0.7.0.md), [`LIVE_VERIFICATION_video_download.md`](LIVE_VERIFICATION_video_download.md)
