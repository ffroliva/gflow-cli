---
name: pypi-rejected-filename-reusable
description: PyPI HTTP 400 wheel rejection does NOT consume the filename; you CAN retry with the same version number after fixing the build
---

When `twine upload` fails with HTTP 400 "Invalid distribution file" (e.g. duplicate ZIP entries, bad metadata, file size), **the version filename is not registered with PyPI** and can be retried after fixing the build.

Confirmed empirically on the v0.9.0 release (2026-05-25):

1. First attempt: pushed signed tag `v0.9.0` at commit `c8c2710`. Release workflow ran. `pypi-publish` step failed with HTTP 400 (duplicate ZIP entries from a hatchling `force-include` collision).
2. Deleted the `v0.9.0` tag locally + on origin.
3. Cut a hotfix branch removing the force-include blocks; merged to main as commit `11d3dc2`.
4. Cut a pre-tag doc-review fix branch; merged to main as `4aee7eb`.
5. Re-signed `v0.9.0` at `4aee7eb` and pushed. Release workflow re-ran. **PyPI accepted the upload — same version number, no `409 File Already Exists`.**

**This is different from a successful upload.** Once a wheel uploads cleanly to PyPI, the filename is locked forever — even `yank` does not free it. The 400-vs-success distinction matters:

| PyPI response | Filename state | Retry semantics |
|---|---|---|
| `400 Invalid distribution file` | Not consumed | Fix and retry with same version |
| `200 OK` (success) | **Permanently locked** | Must bump to next version |
| `409 File already exists` | Consumed | Must bump to next version |

**How to apply:** if a PyPI publish fails with 400, don't panic-cut a patch release. Diagnose the build, fix, force-rewrite the tag, push, and PyPI accepts it. Save the next version number for actual bug-fix work.

Caveat: this is empirically observed PyPI behavior, not a documented contract. If PyPI ever switches to "consume on receipt" semantics, this rule breaks. Sanity-check at the time by trying a `pip index versions <pkg>` after the rejection — if the version doesn't appear, the filename is free.

Linked: [[wheel-build-sanity-gate]], [[release-signing]], [[release-back-merge-gap-recovery]].
