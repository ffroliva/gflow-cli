# Release Protocol

This project publishes Python distributions to PyPI and GitHub Releases from
Git tags. The release workflow is `.github/workflows/release.yml`.

## Current Policy

- `0.x` versions are alpha-quality releases: useful for real testing, but APIs
  may change before `1.0.0`.
- PEP 440 prerelease tags use the form `vX.Y.ZaN`, `vX.Y.ZbN`, or `vX.Y.ZrcN`.
  Example: `v0.4.0a2`.
- Stable tags use the form `vX.Y.Z`. Example: `v0.4.0`.
- Never rewrite a pushed release tag. If a release is wrong, ship the next
  version with a fix.

## What The Workflow Does

When a tag matching `v*.*.*` is pushed, GitHub Actions:

1. Verifies the tag matches `pyproject.toml` exactly after stripping the
   leading `v`.
2. Builds the wheel and source distribution with `uv build`.
3. Publishes to PyPI through Trusted Publishing.
4. Creates a GitHub Release and attaches the built artifacts.
5. Marks tags containing PEP 440 alpha, beta, or release-candidate markers as
   GitHub prereleases.

## Prerelease Versus Full Release

Use a prerelease while validating behavior with end-to-end tests:

```bash
0.4.0a1
0.4.0a2
0.4.0rc1
```

Use a full release only when the same release line is ready for general use:

```bash
0.4.0
```

PyPI treats `0.4.0a2` as a prerelease version. Users can install it explicitly:

```bash
uvx --from "gflow-cli==0.4.0a2" gflow --help
python -m pip install --pre gflow-cli
```

## Release Checklist

1. Confirm the working tree is clean:
   ```bash
   git status --short
   ```
2. Confirm `main` is current:
   ```bash
   git fetch origin
   git branch --show-current
   git rev-list HEAD..origin/main
   ```
3. Run quality gates:
   ```bash
   uv run python scripts/ci/check_repo_hygiene.py
   uv run ruff check src tests
   uv run ruff format --check src tests
   uv run pyright src
   uv run python -m pytest -q --cov=gflow_cli
   ```
4. **Live-verify the release's user-facing features and record the evidence.**
   **Required gate — do not skip.** Exercise each new/changed user-facing feature
   against live Flow (credit-free wherever possible — image gen, entity attach,
   upscale, and scene/timeline ops cost no Veo credits) and write the result to
   `docs/LIVE_VERIFICATION_vX.Y.Z.md` using the 5-layer ledger (file count + magic
   bytes + dimensions/shape + structlog invariants + user-confirmable artifact).
   Then add it to the "what was live-verified" entry in `docs/INDEX.md`. Every
   release v0.7.0→v0.13.0 had this doc; it lapsed for v0.14.0–v0.15.1 — that gap is
   the reason this is now an explicit step. If a feature genuinely cannot be live-
   verified this cycle, say so in the doc with the reason; never silently omit it.
5. **Consolidate shipped planning artifacts.** Extract any durable patterns into
   auto-memory, then remove the now-shipped `docs/superpowers/` plan / spec /
   verification files (keep only in-flight work). Stale review docs and session
   markers do not belong in the repo root. (Enforced by `check_repo_hygiene.py` —
   see the root-doc allowlist.)
6. Update the version in:
   - `pyproject.toml`
   - `src/gflow_cli/__init__.py`
   - any tests that assert the package version
7. Move user-visible changes from `CHANGELOG.md` `[Unreleased]` into a dated
   release section.
8. Commit the release prep:
   ```bash
   git add pyproject.toml src/gflow_cli/__init__.py CHANGELOG.md tests docs
   git commit -m "chore(release): vX.Y.Z"
   ```
9. Tag and push. **Must be a signed annotated tag** (`-s`) — `.github/workflows/release.yml` rejects unsigned or lightweight tags. Requires a GPG or SSH signing key registered with your GitHub account.
   ```bash
   git tag -s vX.Y.Z -m "vX.Y.Z"
   git push origin main
   git push origin vX.Y.Z
   ```
10. Watch the release workflow:
    <https://github.com/ffroliva/gflow-cli/actions/workflows/release.yml>
11. Confirm the new version appears on:
    - <https://pypi.org/project/gflow-cli/>
    - <https://github.com/ffroliva/gflow-cli/releases>

## Known Historical Quirk

Older alpha tags (`v0.2.0a1`, `v0.3.0a1`) were created as normal GitHub
Releases because the workflow only detected hyphenated prerelease names. The
workflow now recognizes PEP 440 alpha, beta, and release-candidate tags.

## Local Test-Memory Quirk

`@pytest.mark.e2e` and `@pytest.mark.live` tests are opt-in, may open real
browser sessions, and may spend Flow credits. The default pytest configuration
excludes both markers, so the release smoke gate mirrors CI:

```bash
uv run python -m pytest -q --cov=gflow_cli
```

When running through an MCP/context sandbox, coverage instrumentation can exceed
the sidecar memory ceiling and close the connection. In that case, run the same
marker-filtered suite in smaller path chunks without coverage for local evidence
and let GitHub Actions produce the authoritative coverage XML.
