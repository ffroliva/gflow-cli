---
description: Cut a new gflow-cli release — bump version, update CHANGELOG, tag, push.
---

# `/release` — Cut a new gflow-cli release

You are about to release a new version of `gflow-cli`. Follow this sequence verbatim — every step matters.

## Inputs

Ask the user (if not already provided):
1. **Version** — the new version (e.g. `0.4.0`, `0.4.0a3`, `1.0.0rc1`). Use PEP 440 prerelease suffixes (`aN`, `bN`, `rcN`) for Python package releases. If they don't know, look at the latest entry in `CHANGELOG.md` `[Unreleased]` and propose the next bump (PATCH for fixes only, MINOR for new features, MAJOR for breaks).
2. **Pre-release?** — prerelease versions such as `0.4.0a3` should stay marked as GitHub prereleases. Only the user can say when a release line is ready for the stable tag, such as `0.4.0`.

## Sequence

1. **Verify clean working tree.**
   ```bash
   git status --short
   ```
   Must be empty. If not, abort and tell the user to commit/stash first.

2. **Verify on `main`, up-to-date with `origin/main`.**
   ```bash
   git rev-parse --abbrev-ref HEAD     # must be "main"
   git fetch origin
   git rev-list HEAD..origin/main      # must be empty
   ```

3. **Run quality gates.** Reject if any fail.
   ```bash
   uv run ruff check src tests
   uv run ruff format --check src tests
   uv run pyright src
   uv run pytest -q --cov=gflow_cli --cov-fail-under=80
   ```

4. **Bump version** in `pyproject.toml`:
   ```toml
   [project]
   version = "<NEW_VERSION>"
   ```

5. **Bump package version** in `src/gflow_cli/__init__.py`:
   ```python
   __version__ = "<NEW_VERSION>"
   ```

6. **Update version assertion tests** if present:
   ```bash
   rg -n "__version__|<OLD_VERSION>|version assertion" tests src pyproject.toml
   ```

7. **Update `CHANGELOG.md`:**
   - Move all entries under `## [Unreleased]` to a new `## [<NEW_VERSION>] — YYYY-MM-DD` section.
   - Leave `## [Unreleased]` empty.
   - Update the link footer:
     ```
     [Unreleased]: https://github.com/ffroliva/gflow-cli/compare/v<NEW_VERSION>...HEAD
     [<NEW_VERSION>]: https://github.com/ffroliva/gflow-cli/releases/tag/v<NEW_VERSION>
     ```

8. **Commit the release prep.**
   ```bash
   git add pyproject.toml src/gflow_cli/__init__.py CHANGELOG.md tests
   git commit -m "chore(release): v<NEW_VERSION>"
   ```

9. **Tag.** PEP 440 prerelease tags include `aN` / `bN` / `rcN`:
   ```bash
   git tag -a v<NEW_VERSION> -m "v<NEW_VERSION>"
   ```

10. **Push commit + tag.**
   ```bash
   git push origin main
   git push origin v<NEW_VERSION>
   ```

11. **Report.** Tell the user:
   - The pushed tag triggers `.github/workflows/release.yml`.
   - Watch <https://github.com/ffroliva/gflow-cli/actions> for the release workflow.
   - On success: PyPI publish + GitHub Release with auto-generated notes.
   - On failure (most common: PyPI Trusted Publishing not yet configured), point them to <https://pypi.org/manage/account/publishing/>.

## Critical reminders

- **NEVER** add `Co-Authored-By: Claude` (or any AI co-author) to the release commit.
- **NEVER** force-push a release tag once it's been created on GitHub. If a release ships broken, bump to the next PATCH and ship a fix; never rewrite tag history.
- **NEVER** `--no-verify` your way past hooks. If a hook complains, fix the underlying issue.
- If quality gates fail at step 3, **STOP**. Do not proceed. Surface the failures to the user.

## See also

- [RELEASE.md](../../RELEASE.md) — full release protocol, prerelease policy, and checklist
- [README § Releases](../../README.md#releases) — short release policy & cadence
- [PLAN § Phase 5](../../PLAN.md#phase-5--public-alpha-release-on-pypi) — first-release exit criteria
