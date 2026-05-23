# LIVE_VERIFICATION — v0.8.1 (docs refresh)

> Per-release evidence for the v0.8.1 patch. Documentation-only release; no runtime code changed. The goals were (1) refresh PyPI's stale v0.7.0 README rendering, (2) restructure README as a polished ~150-line router, and (3) add AGENTS.md + llms.txt at repo root.

## Pre-tag gates (filled in before signing)

- [ ] No undesired `v0.7.0` regex matches in README / AGENTS.md / llms.txt / docs/INDEX.md / docs/PROJECT_STATUS.md (CHANGELOG and historical `LIVE_VERIFICATION_v0.7.0.md` excluded).
- [ ] All in-doc links resolve (`scripts/ci/check_doc_links.py` exit 0).
- [ ] `/gflow:doc-review` skill report has zero open findings.
- [ ] Impeccable Routine passes: ruff check / ruff format --check / pyright src / pytest scoped.
- [ ] README under 200 lines (target ~150).

## Post-tag evidence (filled in after publish)

### PyPI page render
- URL: https://pypi.org/project/gflow-cli/0.8.1/
- Render check timestamp: [YYYY-MM-DD HH:MM UTC]
- Confirms: README header reads "gflow-cli", v0.8.1 in metadata, no v0.7.0 references in body, AGENTS.md cross-link visible.

### GitHub Release
- URL: https://github.com/ffroliva/gflow-cli/releases/tag/v0.8.1
- Signed tag verified: [yes / no — paste `git tag -v v0.8.1` output excerpt]
- Release notes excerpt matches CHANGELOG `[0.8.1]` section: [yes / no]

### Smoke tests
- `uvx --from "gflow-cli==0.8.1" gflow --help` — exit 0, help text references v0.8.1: [paste excerpt]
- `pip install gflow-cli==0.8.1 && pip show gflow-cli` — Version field reads 0.8.1: [paste excerpt]
- `gflow auth status` against a warm profile: still functional (no regression from a docs-only release).

### AGENTS.md / llms.txt discovery
- `gh api repos/ffroliva/gflow-cli/contents/AGENTS.md` — returns 200 with content: [yes / no]
- `gh api repos/ffroliva/gflow-cli/contents/llms.txt` — returns 200 with content: [yes / no]

### Back-merge gate
- `main → develop` back-merge completed at commit: [SHA]
- Conflicts resolved (pyproject.toml / __init__.py / CHANGELOG.md per the release-back-merge-gap-recovery memory): [yes / no — paste conflict files list]
- `develop` HEAD includes the v0.8.1 changes: [yes / no — paste `git log --oneline -5 develop`]

## Reference

- Spec: [`docs/superpowers/specs/2026-05-23-readme-v0.8.1-refresh-design.md`](superpowers/specs/2026-05-23-readme-v0.8.1-refresh-design.md)
- Plan: [`docs/superpowers/plans/2026-05-23-readme-v0.8.1-refresh.md`](superpowers/plans/2026-05-23-readme-v0.8.1-refresh.md)
- Research digest: `tmp/readme-research-2026-05-23.md` (local-only, not committed)
- Previous release evidence: [`LIVE_VERIFICATION_v0.7.0.md`](LIVE_VERIFICATION_v0.7.0.md), [`LIVE_VERIFICATION_video_download.md`](LIVE_VERIFICATION_video_download.md)
