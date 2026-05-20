---
description: Cut a new gflow-cli release — bump version, update CHANGELOG, tag, push.
---

# `/gflow:release` — Cut a new release

Follow this sequence verbatim. Every step matters.

## Inputs

Ask the user (if not already provided):
1. **Version** — the new version (e.g. `0.4.0`, `0.4.0a3`, `1.0.0rc1`). Use PEP 440 prerelease suffixes (`aN`, `bN`, `rcN`). If they don't know, run `/gflow:changelog` first and propose the next bump (PATCH for fixes only, MINOR for new features, MAJOR for breaks).
2. **Pre-release?** — prerelease versions should stay marked as GitHub prereleases. Only the user can say when a release line is ready for the stable tag.

## Sequence

**1. Review what's queued.**

Run `/gflow:changelog` — confirm the `[Unreleased]` block is non-empty and accurate before proceeding.

**2. Verify clean working tree.**

```bash
git status --short
```

Must be empty. If not, abort and tell the user to commit or stash first.

**3. Verify on `main`, up-to-date with `origin/main`.**

```bash
git rev-parse --abbrev-ref HEAD     # must be "main"
git fetch origin
git rev-list HEAD..origin/main      # must be empty
```

**4. Run quality gates.**

Run `/gflow:check` — all gates must pass. Abort if any fail.

**5. Bump version** in `pyproject.toml`:

```toml
[project]
version = "<NEW_VERSION>"
```

**6. Bump package version** in `src/gflow_cli/__init__.py`:

```python
__version__ = "<NEW_VERSION>"
```

**7. Update version assertion tests** if present:

```bash
rg -n "__version__|<OLD_VERSION>|version assertion" tests src pyproject.toml
```

**8. Migrate CHANGELOG.**

- Move all entries under `## [Unreleased]` to a new `## [<NEW_VERSION>] — YYYY-MM-DD` section.
- Leave `## [Unreleased]` empty.
- Update the link footer:
  ```
  [Unreleased]: https://github.com/ffroliva/gflow-cli/compare/v<NEW_VERSION>...HEAD
  [<NEW_VERSION>]: https://github.com/ffroliva/gflow-cli/releases/tag/v<NEW_VERSION>
  ```

**9. Review commands for staleness.**

Scan `.claude/commands/gflow/` — check if any command references a phase, file path, or behaviour that the release changes. Update in the same commit if so.

**10. Commit the release prep.**

```bash
git add pyproject.toml src/gflow_cli/__init__.py CHANGELOG.md tests
git commit -m "chore(release): v<NEW_VERSION>"
```

**11. Tag.** Use `-s` for a signed annotated tag so GitHub shows the **"Verified"** badge AND `.github/workflows/release.yml` passes the signed-tag gate (lightweight or unsigned tags are rejected).

```bash
git tag -s v<NEW_VERSION> -m "v<NEW_VERSION>"
```

Requires a GPG or SSH signing key registered in your GitHub account settings.
Run `git config --global user.signingkey` to confirm a key is set.
If signing is not available in the current environment, create the tag on
your local machine and push it: `git push origin v<NEW_VERSION>`.

**12. Push commit + tag.**

```bash
git push origin main
git push origin v<NEW_VERSION>
```

**13. Report.**

Tell the user:
- The pushed tag triggers `.github/workflows/release.yml`.
- Watch <https://github.com/ffroliva/gflow-cli/actions> for the release workflow.
- On success: PyPI publish + GitHub Release with auto-generated notes.
- On failure (most common: PyPI Trusted Publishing not yet configured): point to <https://pypi.org/manage/account/publishing/>.

## Critical reminders

- **NEVER** add `Co-Authored-By: Claude` (or any AI co-author) to the release commit.
- **NEVER** force-push a release tag once it's on GitHub. Ship a PATCH fix instead.
- **NEVER** `--no-verify` past hooks. Fix the underlying issue.
- If quality gates fail at step 4, **STOP**. Surface the failures to the user.

## See also

- [RELEASE.md](../../../RELEASE.md) — full release protocol, prerelease policy, and checklist
- [README § Releases](../../../README.md#releases) — release policy and cadence
- [PLAN § Phase 5](../../../PLAN.md#phase-5--public-alpha-release-on-pypi) — first-release exit criteria
