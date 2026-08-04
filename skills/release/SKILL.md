---
name: release
version: "1.0"
description: >
  Cut a new gflow-cli release — bump version, update CHANGELOG, tag, push, and back-merge.
---

# `/gflow:release` — Cut a new release

Follow this sequence verbatim. Every step matters.

> **Branch-protection note:** `main` blocks direct pushes. The release commit travels
> via a `chore/release-vX.Y.Z` branch PR. The signed tag is pushed independently
> (tag pushes bypass branch protection and trigger the CI release workflow immediately).
>
> **Source-branch note (read first):** `develop` is the integration branch — it carries
> ALL unreleased work and `main` usually lags it. The release branch is cut from
> **`develop`**, NOT `main`. The PR `chore/release-vX.Y.Z → main` then brings the full
> integration history onto `main`. Do not expect the work to already be on `main`.

## Inputs

Ask the user (if not already provided):

1. **Version** — the new version (e.g. `0.4.0`, `0.4.0a3`, `1.0.0rc1`). Use PEP 440 prerelease suffixes (`aN`, `bN`, `rcN`). If they don't know, run `/gflow:changelog` first and propose the next bump (PATCH for fixes only, MINOR for new features, MAJOR for breaks).
2. **Pre-release?** — prerelease versions stay marked as GitHub prereleases. Only the user can say when a release line is ready for the stable tag.

---

## Sequence

**1. Review what's queued.**

Run `/gflow:changelog` — confirm the `[Unreleased]` block is non-empty and accurate before proceeding.

**2. Verify (or triage) a clean working tree.**

```bash
git status --short
```

If empty, continue. If not, **triage before aborting** — do not blindly stop:

- **Auto-injected boilerplate** (e.g. a context-mode routing block appended to
  `CLAUDE.md` by an MCP plugin's SessionStart hook): this is plugin-injected, not
  project content — `git restore` it. Confirm with the user if unsure.
- **Build/temp artifacts** (e.g. a stray `tmp*.tar.gz` sdist at repo root): delete them.
- **Genuine uncommitted work:** STOP and tell the user to commit or stash on the
  appropriate branch (never commit straight to `develop`).

The tree must be clean before you create the release branch.

**3. Verify `develop` is the release source and up-to-date.**

The release is cut from `develop`, NOT `main` (see Source-branch note above).
Confirm direction explicitly — a backwards divergence means a prior back-merge was skipped.

```bash
git fetch origin
git rev-parse --abbrev-ref HEAD                      # expect "develop"
git rev-list --count HEAD..origin/develop            # local behind origin — expect 0
git rev-list --count origin/main..origin/develop     # develop AHEAD of main — expect > 0 (the work to release)
git rev-list --count origin/develop..origin/main     # main AHEAD of develop — expect 0
```

If not on `develop`: `git checkout develop && git pull origin develop`.
If local is behind origin: `git pull origin develop`.
**If `main` is AHEAD of `develop` (last count > 0): STOP.** A prior release skipped its
`main → develop` back-merge — recover first (see the `release-back-merge-gap-recovery`
memory) or the release branch will hit conflicts on `pyproject.toml` / `__init__.py` / `CHANGELOG.md`.

**4. Run quality gates.**

Run `/gflow:check` — all gates must pass. Abort if any fail.

**4b. Live-verify the release's user-facing features (REQUIRED gate).**

For every new/changed user-facing feature in this release, exercise it against
live Flow (credit-free wherever possible — image gen, entity attach, upscale, and
scene/timeline ops cost no Veo credits) and write the evidence to
`docs/LIVE_VERIFICATION_v<NEW_VERSION>.md` using the 5-layer ledger (file count +
magic bytes + dimensions/shape + structlog invariants + a user-confirmable
artifact). Add it to the "what was live-verified" entry in `docs/INDEX.md`. This
doc shipped for every release v0.7.0→v0.13.0, then lapsed for v0.14.0–v0.15.1 —
which is why it is now an explicit gate. If a feature genuinely cannot be verified
this cycle, record that and the reason in the doc; never silently omit it. Stage
the doc into the release-prep commit (step 11).

**5. Create a release branch off `develop`.**

```bash
git checkout develop                          # ensure the base is develop, not main
git checkout -b chore/release-v<NEW_VERSION>
```

This branch now contains all of `develop` (⊇ `main`) plus your release prep. All
release prep commits live here; the PR into `main` (step 14) carries the full
integration history forward.

**6. Bump the shared release version** in `pyproject.toml` and
`.codex-plugin/plugin.json`:

```toml
[project]
version = "<NEW_VERSION>"
```

```json
{
  "version": "<NEW_VERSION>"
}
```

**7. Bump package version** in `src/gflow_cli/__init__.py`:

```python
__version__ = "<NEW_VERSION>"
```

**8. Update version assertion tests** if present:

```bash
rg -n "__version__|<OLD_VERSION>|version assertion" tests src pyproject.toml .codex-plugin/plugin.json
```

**9. Migrate CHANGELOG.**

- Move all entries under `## [Unreleased]` to a new `## [<NEW_VERSION>] — YYYY-MM-DD` section.
- Leave `## [Unreleased]` empty.
- Update the link footer. Match the repo's existing convention — every prior entry
  uses the `compare/vPREV...vNEW` form, so use that for the new version too (NOT the
  `releases/tag/` form), or `/gflow:doc-review` will flag the inconsistency:
  ```
  [Unreleased]: https://github.com/ffroliva/gflow-cli/compare/v<NEW_VERSION>...HEAD
  [<NEW_VERSION>]: https://github.com/ffroliva/gflow-cli/compare/v<PREV_VERSION>...v<NEW_VERSION>
  ```

**10. Run the documentation review gate.**

Run `/gflow:doc-review` — audit all version refs, INDEX completeness, evidence files, **the published `website/docs/` mirror (PII gate + content-drift check, §4b)**, **code↔docs parity via git log (§4c)**, skill files, CHANGELOG footer, and memory files. Fix every **FAIL** before continuing. Fold all discovered fixes into the release prep commit — **including any `website/docs/` re-sync** (the mirror is anonymized and hand-synced; a canonical doc change this release must be mirrored, and `CHANGELOG.md` must never appear under `website/docs/`).

Also **consolidate shipped planning artifacts** here: extract any durable patterns
into auto-memory, then remove the now-shipped `docs/superpowers/` plan / spec /
verification files (keep only in-flight work). `check_repo_hygiene.py` enforces the
root-doc allowlist, so a stray review doc or session marker left at the repo root
will fail the gate.

**11. Commit the release prep.**

```bash
git add pyproject.toml .codex-plugin/plugin.json src/gflow_cli/__init__.py uv.lock CHANGELOG.md
git add docs/ website/docs/ skills/ .claude/commands/gflow/   # include any doc-review + mirror fixes
# doc-review version-currency fixes often also touch ROOT docs — stage them too:
git add README.md PLAN.md KNOWN_ISSUES.md AGENTS.md llms.txt 2>/dev/null || true
git status --short                            # review EVERYTHING staged before committing
git commit -m "chore(release): v<NEW_VERSION>"
```

- **`uv.lock` changes** on every version bump (the editable package version is
  pinned in the lockfile) — it is easy to forget and must ship in this commit.
- **`.codex-plugin/plugin.json` tracks the package version** so marketplace installs
  receive a new cache path for every release.
- The release-prep commit must NOT carry a `Co-Authored-By` trailer (see reminders).

**12. Tag the release commit.** Use `-s` for a signed annotated tag so GitHub shows **"Verified"** AND `.github/workflows/release.yml` passes the signed-tag gate (unsigned or lightweight tags are rejected by CI).

```bash
git tag -s v<NEW_VERSION> -m "v<NEW_VERSION>"
```

Signing requirements:
- **SSH signing (preferred):** `git config --global gpg.format ssh` + `user.signingkey` pointing at your public key.
- **GPG:** any registered GPG key works.
- Run `git config --global user.signingkey` to confirm a key is configured.

Confirm the tag actually carries a signature (this is what CI checks):

```bash
git cat-file -p v<NEW_VERSION> | grep -c "BEGIN SSH SIGNATURE"   # expect 1 (or "BEGIN PGP SIGNATURE" for GPG)
```

> **Benign local-verify error:** `git tag -v v<NEW_VERSION>` may fail with
> `gpg.ssh.allowedSignersFile needs to be configured`. This is a *local
> verification-config* gap only — the tag IS validly signed and CI still passes
> (CI greps for the signature header, above). To make local verify work once and
> for all, create an allowed-signers file (`<your-email> ssh-rsa AAAA...`) and run
> `git config --global gpg.ssh.allowedSignersFile <path>`. See the `release-signing`
> memory for the exact recipe. Do NOT treat this error as a signing failure.

**13. Push the tag first** (bypasses branch protection; triggers the CI release workflow immediately):

> **⚠ POINT OF NO RETURN — confirm with the user before this push.** Pushing the
> tag immediately triggers `.github/workflows/release.yml` → **PyPI publish +
> public GitHub Release**. A pushed release tag must NOT be force-replaced (ship a
> PATCH instead). Get an explicit go-ahead, then push.

```bash
git push origin v<NEW_VERSION>
```

CI will start building the release. Watch <https://github.com/ffroliva/gflow-cli/actions>.
Wait for the `Release` run to report **completed / success** and confirm the GitHub
Release published before continuing (`gh release view v<NEW_VERSION>`).

**14. Push the release branch and open the PR.**

```bash
git push -u origin chore/release-v<NEW_VERSION>
gh pr create --base main --head chore/release-v<NEW_VERSION> \
  --title "chore(release): v<NEW_VERSION>"
```

Wait for PR CI to go green (`gh pr checks <N> --watch`). **The `SonarCloud analysis`
check must be green (gate passed) — not just the test matrix.** If it is red or you
want the verdict, run `/gflow:sonar <N>` and drive it to zero before merging. Then
merge with a **merge commit** — never squash:

```bash
gh pr merge <N> --merge --delete-branch
```

> **NEVER `--squash` this PR.** Because the branch was cut from `develop`, the PR
> carries the entire batch of unreleased integration commits. A squash collapses
> them into one opaque commit on `main` and destroys that history. `--merge`
> preserves it. (The release workflow already ran from the tag push in step 13 —
> this PR is to bring the bump commit + integration history onto `main`.)

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

### Pipeline Continuation (Next Step Handoff)

Upon completing a Release:
1. Proactively announce: **"Release v<NEW_VERSION> shipped to PyPI & GitHub Releases! Back-merge to develop complete. Next step: Phase 1 Triage (`/gflow:issue-assessment <N>`) for the next development cycle."**

---

## Critical reminders

- **ALWAYS** cut the release branch from `develop`, not `main` — `develop` carries the work.
- **NEVER** `--squash` the release PR into `main` — it destroys the integration history the branch carries. Use `--merge`.
- **NEVER** skip the `main → develop` back-merge (step 15) — skipping it guarantees conflicts at the next release.
- **NEVER** add `Co-Authored-By: Claude` (or any AI co-author) to the release commit.
- **NEVER** force-push a release tag once it's on GitHub. Ship a PATCH fix instead.
- **NEVER** `--no-verify` past hooks. Fix the underlying issue.
- **NEVER** push directly to `main` — branch protection will reject it. Always use a PR.
- A `git tag -v` `allowedSignersFile` error is **benign** (verify-only) — the tag is still signed and CI passes. Don't treat it as a failure.
- **CONFIRM with the user before the step 13 tag push** — it's the irreversible PyPI + public Release trigger.
- If quality gates fail at step 4, **STOP**. Surface the failures to the user.
- If doc-review fails at step 10, **STOP**. Fix before committing.

---

## See also

- [RELEASE.md](../../RELEASE.md) — full release protocol, prerelease policy, and checklist
- [README § Releases](../../README.md#releases) — release policy and cadence
- [PLAN § Phase 5](../../PLAN.md#phase-5--public-alpha-release-on-pypi) — first-release exit criteria
