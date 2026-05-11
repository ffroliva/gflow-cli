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
   uv run ruff check src tests
   uv run ruff format --check src tests
   uv run pyright src
   uv run pytest -q
   ```
4. Update the version in:
   - `pyproject.toml`
   - `src/gflow_cli/__init__.py`
   - any tests that assert the package version
5. Move user-visible changes from `CHANGELOG.md` `[Unreleased]` into a dated
   release section.
6. Commit the release prep:
   ```bash
   git add pyproject.toml src/gflow_cli/__init__.py CHANGELOG.md tests
   git commit -m "chore(release): vX.Y.Z"
   ```
7. Tag and push:
   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin main
   git push origin vX.Y.Z
   ```
8. Watch the release workflow:
   <https://github.com/ffroliva/gflow-cli/actions/workflows/release.yml>
9. Confirm the new version appears on:
   - <https://pypi.org/project/gflow-cli/>
   - <https://github.com/ffroliva/gflow-cli/releases>

## Known Historical Quirk

Older alpha tags (`v0.2.0a1`, `v0.3.0a1`) were created as normal GitHub
Releases because the workflow only detected hyphenated prerelease names. The
workflow now recognizes PEP 440 alpha, beta, and release-candidate tags.
