---
name: pypi-readme-staleness-fix
description: "To refresh PyPI's package-page README rendering, you MUST publish a new wheel; the page is locked at whichever README was bundled with the last uploaded tag. A docs-only patch release (e.g., v0.8.0 → v0.8.1) is the idiomatic mechanism."
---

PyPI renders the README from the wheel uploaded for that exact version. **The package page does not update when you edit `main`'s README** — it is frozen at the README that shipped with the last tagged release.

**Why:** A user fixing v0.7.0 references on `main` after shipping v0.8.0 leaves PyPI showing v0.7.0 content for as long as no new release ships. For a project where many users discover via PyPI, this is a real bug.

**How to apply:** Treat docs-on-PyPI as a release-worthy bug. The fix is a docs-only patch release:

1. Branch `hotfix/readme-vX.Y.(Z+1)-refresh` off `main`.
2. Update README + related docs.
3. Bump `pyproject.toml` version + `src/<pkg>/__init__.py` `__version__`.
4. Add a CHANGELOG `[X.Y.(Z+1)] — YYYY-MM-DD` entry under `### Documentation`.
5. Signed tag → trusted-publish → PyPI shows fresh README within ~2 minutes.
6. **Back-merge `main → develop`** per [[release-back-merge-gap-recovery]].

PEP 440 `0.X.Y.post1` is also valid (signals "no code, just metadata fix") but is less discoverable for end users. Default to patch bump unless the user explicitly prefers post-release semantics.

Don't wait for the next "real" code release to refresh PyPI — by then the misleading README has been live for weeks.

**Image / media assets:** PyPI does not resolve relative image paths in README — `![alt](docs/assets/foo.gif)` renders as a broken image. Use absolute `raw.githubusercontent.com` URLs targeting `main`:

```markdown
![alt text](https://raw.githubusercontent.com/ffroliva/gflow-cli/main/docs/assets/foo.gif)
```

The images will 404 on the PR preview (assets aren't on `main` yet) but render correctly once the release reaches `main`. Note this in the PR body so reviewers don't flag the temporary 404 as a bug.

Related: [[release-back-merge-gap-recovery]], [[readme-hybrid-router-pattern]], [[release-signing]].
