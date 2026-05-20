---
description: Cut a new gflow-cli release — bump version, update CHANGELOG, tag, push, and back-merge.
---

# `/gflow:release` — Cut a new release

Follow this sequence verbatim. Every step matters.

> **Branch-protection note:** `main` blocks direct pushes. The release commit travels
> via a `chore/release-vX.Y.Z` branch PR. The signed tag is pushed independently
> (tag pushes bypass branch protection and trigger the CI release workflow immediately).

## Inputs

Ask the user (if not already provided):

1. **Version** — the new version (e.g. `0.4.0`, `0.4.0a3`, `1.0.0rc1`). Use PEP 440 prerelease suffixes (`aN`, `bN`, `rcN`). If they don't know, run `/gflow:changelog` first and propose the next bump (PATCH for fixes only, MINOR for new features, MAJOR for breaks).
2. **Pre-release?** — prerelease versions stay marked as GitHub prereleases. Only the user can say when a release line is ready for the stable tag.

---

## Sequence

**1. Review what's queued.**

Run `/gflow:changelog` — confirm the `[Unreleased]` block is non-empty and accurate before proceeding.

**2. Verify clean working tree.**

```bash
git status --short
```

Must be empty. If not, abort and tell the user to commit or stash first.

**3. Verify `main` is up-to-date.**

```bash
git rev-parse --abbrev-ref HEAD     # must be "main"
git fetch origin
git rev-list HEAD..origin/main      # must be empty
```

If not on `main`, switch: `git checkout main && git pull origin main`.
If `main` is behind, pull first.

**4. Run quality gates.**

Run `/gflow:check` — all gates must pass. Abort if any fail.

**5. Create a release branch.**

```bash
git checkout -b chore/release-v<NEW_VERSION>
```

All release prep commits live here; this branch gets PR'd into `main`.

**6. Bump version** in `pyproject.toml`:

```toml
[project]
version = "<NEW_VERSION>"
```

**7. Bump package version** in `src/gflow_cli/__init__.py`:

```python
__version__ = "<NEW_VERSION>"
```

**8. Update version assertion tests** if present:

```bash
rg -n "__version__|<OLD_VERSION>|version assertion" tests src pyproject.toml
```

**9. Migrate CHANGELOG.**

- Move all entries under `## [Unreleased]` to a new `## [<NEW_VERSION>] — YYYY-MM-DD` section.
- Leave `## [Unreleased]` empty.
- Update the link footer:
  ```
  [Unreleased]: https://github.com/ffroliva/gflow-cli/compare/v<NEW_VERSION>...HEAD
  [<NEW_VERSION>]: https://github.com/ffroliva/gflow-cli/releases/tag/v<NEW_VERSION>
  ```

**10. Run the documentation review gate.**

Run `/gflow:doc-review` — audit all version refs, INDEX completeness, evidence files, skill files, CHANGELOG footer, and memory files. Fix every **FAIL** before continuing. Fold all discovered fixes into the release prep commit.

**11. Commit the release prep.**

```bash
git add pyproject.toml src/gflow_cli/__init__.py CHANGELOG.md
git add docs/ .claude/commands/gflow/        # include any doc-review fixes
git commit -m "chore(release): v<NEW_VERSION>"
```

**12. Tag the release commit.** Use `-s` for a signed annotated tag so GitHub shows **"Verified"** AND `.github/workflows/release.yml` passes the signed-tag gate (unsigned or lightweight tags are rejected by CI).

```bash
git tag -s v<NEW_VERSION> -m "v<NEW_VERSION>"
```

Signing requirements:
- **SSH signing (preferred):** `git config --global gpg.format ssh` + `user.signingkey` pointing at your public key.
- **GPG:** any registered GPG key works.
- Run `git config --global user.signingkey` to confirm a key is configured.

**13. Push the tag first** (bypasses branch protection; triggers the CI release workflow immediately):

```bash
git push origin v<NEW_VERSION>
```

CI will start building the release. Watch <https://github.com/ffroliva/gflow-cli/actions>.

**14. Push the release branch and open the PR.**

```bash
git push origin chore/release-v<NEW_VERSION>
```

Open PR `chore/release-v<NEW_VERSION> → main` with title `chore(release): v<NEW_VERSION>`. Merge it once CI is green. (The release workflow already ran from the tag push in step 13 — the PR is to keep `main` up-to-date with the bump commit.)

**15. Back-merge `main` into `develop`.**

After the release PR is merged, bring the bump commit back to `develop` so branches stay aligned:

```bash
git checkout develop
git pull origin develop
git fetch origin main
git merge origin/main --no-ff -m "chore: back-merge main (v<NEW_VERSION>) into develop"
git push origin develop
```

If there are conflicts (rare — only if `develop` has commits that touched the same lines as the bump), resolve them, keeping `develop`'s unreleased work and `main`'s version bump.

**16. Report.**

Tell the user:
- Tag push triggered `.github/workflows/release.yml`.
- Watch <https://github.com/ffroliva/gflow-cli/actions> for the release workflow.
- On success: PyPI publish + GitHub Release with auto-generated notes.
- On failure (most common: PyPI Trusted Publishing not yet configured): point to <https://pypi.org/manage/account/publishing/>.
- `develop` is now synced with `main` (back-merge done in step 15).
- Next development cycle starts on `develop` — open `## [Unreleased]` in CHANGELOG is ready.

---

## Critical reminders

- **NEVER** add `Co-Authored-By: Claude` (or any AI co-author) to the release commit.
- **NEVER** force-push a release tag once it's on GitHub. Ship a PATCH fix instead.
- **NEVER** `--no-verify` past hooks. Fix the underlying issue.
- **NEVER** push directly to `main` — branch protection will reject it. Always use a PR.
- If quality gates fail at step 4, **STOP**. Surface the failures to the user.
- If doc-review fails at step 10, **STOP**. Fix before committing.

---

## See also

- [RELEASE.md](../../../RELEASE.md) — full release protocol, prerelease policy, and checklist
- [README § Releases](../../../README.md#releases) — release policy and cadence
- [PLAN § Phase 5](../../../PLAN.md#phase-5--public-alpha-release-on-pypi) — first-release exit criteria
