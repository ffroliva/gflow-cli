# Live Verification — v0.20.1

Feature under test:
- **Aspect ratio overrides in Agentic & Classic cohorts** ([#193](https://github.com/ffroliva/gflow-cli/issues/193)).
- **`GFLOW_CLI_PREFER_CLASSIC`** config option (`prefer_classic` setting).

## Live Verification Status

Live verification was **not run this cycle** (skipped honestly per the release protocol).

### Reason
The release prep was executed in an automated agent sandbox environment where live Google Flow credentials (`GFLOW_LIVE` and `GFLOW_CLI_E2E_PROFILE`) are not configured.

### Local Verification
To ensure no regressions, the following local verification checks were executed:
1. **Local Test Suite**: 1,765 local tests passed successfully (`pytest -m "not e2e and not live and not smoke"`).
2. **Type Checking**: `pyright src` completed with 0 errors and 0 warnings.
3. **Lint & Formatting**: `ruff check` and `ruff format --check` passed successfully with no violations.
4. **Repository Hygiene**: `check_repo_hygiene.py` and `check_doc_links.py` resolved all files and links with no violations.
