---
name: release-signing
description: "gflow-cli release tags are signed with SSH (not GPG). Local git is configured globally; CI verifies via grep for `BEGIN SSH SIGNATURE`."
---

Release tags MUST be signed annotated tags or CI rejects them (PR #30, `.github/workflows/release.yml` step "Verify tag is signed" greps the tag object for `BEGIN (PGP|SSH) SIGNATURE`).

The local maintainer environment is configured for SSH signing as of 2026-05-20:

```bash
git config --global user.signingkey ~/.ssh/id_rsa.pub
git config --global gpg.format ssh
git config --global tag.gpgsign true
git config --global commit.gpgsign false   # only sign tags, not every commit
```

The SSH public key at `~/.ssh/id_rsa.pub` ("hp-elitebook-flavio" in GitHub) must additionally be registered as a *Signing Key* (not just an Auth key) at https://github.com/settings/keys for the GitHub UI to render the "Verified" badge. The CI gate does not depend on this — only the local signature header matters.

**Local `git tag -v` verification (added 2026-05-29, v0.10.0):** signing a tag with `-s` succeeds, but `git tag -v vX.Y.Z` fails with `gpg.ssh.allowedSignersFile needs to be configured` until you create an allowed-signers file. This is a *verification-only* gap — the tag is still validly signed and CI still passes (CI only greps for the header). To make local verify work:

```bash
# one line: <principals> <keytype> <keydata>  (principals = your signer email(s))
# file at C:/Users/<you>/.ssh/allowed_signers contains:
#   <maintainer-email>,<maintainer-email> ssh-rsa AAAAB3Nza...==
git config --global gpg.ssh.allowedSignersFile "C:/Users/<you>/.ssh/allowed_signers"
git tag -v vX.Y.Z   # → Good "git" signature for <maintainer-email> with RSA key SHA256:...
```

The principal MUST match the tagger email (`git cat-file -p <tag> | grep tagger`), which is `<maintainer-email>`.

**No CI auto-signing yet** — see [[phase-b-followups]] item E for the proposed sigstore `gitsign` keyless-signing follow-up. Until that lands, every release must be tagged on a machine with the above configuration.

**Procedure used for v0.7.0:**
1. After PR develop → main merges, checkout main locally and pull.
2. Bump pyproject + `__init__.py` + uv.lock.
3. Commit `chore(release): vX.Y.Z` locally.
4. `git tag -s vX.Y.Z -m "vX.Y.Z"`.
5. Push the tag: `git push origin vX.Y.Z` (the tag push DOES work even when direct push to main is blocked by branch protection; the tag carries the commit).
6. Open a PR `chore/release-vX.Y.Z → main` to land the bump commit on the branch (the tag already references it).

See also: [[branch-workflow]].
