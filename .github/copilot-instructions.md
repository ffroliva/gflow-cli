# Copilot Code Review Instructions

Review this repository as a Python CLI that automates browser-authenticated
Google Flow workflows. Treat auth, browser automation, CI, release, and secret
handling changes as high-risk even when the diff is small.

For pull request reviews:

- Check that PRs target `develop`, unless they are release PRs.
- Flag unclear contributor provenance, missing DCO sign-off, placeholder author
  emails, and any copied private data.
- Look for leaked tokens, cookies, signed URLs, local profile paths, and captured
  Google/Flow request data.
- For auth/browser changes, check that the implementation preserves profile
  isolation, does not weaken local path boundaries, and avoids remote debugging
  unless explicitly documented.
- For CI changes, check forked-PR secret behavior and avoid recommending
  `pull_request_target` for jobs that checkout or execute contributor code.
- For behavior changes, expect focused tests and docs/changelog updates.
- Keep findings concrete: reference files/lines, explain the user-visible risk,
  and separate blocking issues from cosmetic suggestions.

Do not approve pull requests. Copilot review is advisory; maintainer approval is
still required.
