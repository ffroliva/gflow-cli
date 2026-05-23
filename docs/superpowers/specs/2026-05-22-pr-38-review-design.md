# Design Spec: Review of PR #38 (Linux Auth Keyring Fix)

## Goal
Review and verify PR #38 (`feat: add --password-store=basic argument to Chromium launch options`) to ensure it correctly resolves authentication session sharing issues between headed and headless browsers, follows project conventions, and does not regress on Windows.

## Context
- **Issue:** On Linux, Chrome uses system keyrings (GNOME Keyring/KWallet) for cookie encryption. Headless Playwright instances often lack access to these keyrings, causing decryption failures and `no_session` errors.
- **Proposed Fix:** Use `--password-store=basic` to bypass the system keyring and use a portable, internal store.

## Steps

### 1. Worktree Isolation
- Create worktree: `worktrees/pr-38`.
- Branch: `fix-chrome-ubuntu` (from `kittinan`).
- Purpose: Prevent conflicts with active `feature/multi-image-prompt` work.

### 2. Code Review & Compliance
- **Compliance:** Verify DCO sign-off (`Signed-off-by`).
- **Conventions:** Ensure no `print()` or standard `logging` is used (use `structlog`).
- **Best Practices:** Check if the flag is applied to all relevant browser launch points (headed login and headless verification).
- **Location:** `src/gflow_cli/browser_manager.py`.

### 3. Verification Strategy
- **Hygiene:** Run `ruff check`, `ruff format --check`, and `pyright`.
- **Unit/Integration Tests:** Run `uv run pytest`.
- **E2E Validation:** 
    - Attempt a headless image generation run (`uv run pytest tests/e2e/test_image_batch_e2e.py`).
    - Observe if the `no_session` error persists or if the cookie store is correctly accessed.
- **Windows Impact:** Confirm that this flag doesn't break DPAPI-based encryption or profile management on Windows.

### 4. reCAPTCHA Analysis
- Investigate if the reported `no_session` error was indeed an auth issue or if reCAPTCHA was the primary blocker. Determine if this PR makes reCAPTCHA handling more robust by ensuring a valid session.

## Success Criteria
- [ ] PR branch checked out in worktree.
- [ ] Linting and Type checking pass.
- [ ] Full test suite passes.
- [ ] Headless E2E run successfully accesses session cookies.
- [ ] No regressions on Windows.
- [ ] Review comments provided to the author.
