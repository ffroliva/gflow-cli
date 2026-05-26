# Contributing to gflow-cli

Thanks for considering a contribution! The repo is public and PRs are welcome. Pre-1.0 means APIs may shift between minor versions; check [PLAN.md](PLAN.md) for the active phase before starting a non-trivial change.

## Development setup

```bash
git clone git@github.com:ffroliva/gflow-cli.git
cd gflow-cli
uv sync --extra dev
uv run playwright install chromium
```

## Test-driven development (mandatory)

`gflow-cli` is built test-first. Every change must include tests, and CI rejects PRs that lower coverage.

The cycle:

1. **Red** — Write the failing test that captures the new behaviour. Run `pytest` to confirm it fails for the *right* reason.
2. **Green** — Write the minimum production code to make the test pass. Don't add anything you don't need yet.
3. **Refactor** — Clean up the implementation, keep tests green.
4. **Commit** — Small, atomic commit. Conventional Commits style preferred:
   - `feat(provider): wire upload_image route`
   - `fix(cli): handle missing profile gracefully`
   - `test(flow): add live integration test for i2v`
   - `docs: clarify uvx install`
   - `chore(deps): bump httpx to 0.28`

### Test categories

```python
import pytest

@pytest.mark.unit              # Pure logic, no I/O. Default.
def test_parse_uuid_from_url(): ...

@pytest.mark.integration       # Mocked HTTP, real Provider plumbing.
async def test_upload_returns_asset(): ...

@pytest.mark.e2e               # Hits the real Flow API. Requires GFLOW_CLI_E2E_PROFILE env var.
@pytest.mark.e2e_image         # Cost sub-marker: spends ~1 Imagen credit.
async def test_full_t2i_roundtrip(): ...

@pytest.mark.e2e
@pytest.mark.e2e_video         # Cost sub-marker: spends ~1 Veo credit (most expensive).
async def test_full_i2v_roundtrip(): ...
```

CI runs `unit` + `integration` on every push. `e2e` tests require a live authenticated profile and are opt-in:

```bash
export GFLOW_CLI_E2E_PROFILE=<profile-name>   # name of a logged-in profile

# Zero-credit sanity check (auth + health)
uv run pytest -m e2e_auth -v

# Single image (1 Imagen credit)
uv run pytest -m "e2e_image and not e2e_batch" -v

# Full regression (all credits)
GFLOW_CLI_E2E_RUN_VIDEO=1 uv run pytest -m e2e -v
```

E2e tests spend real Veo/Imagen credits. Video tests default to opt-out — set
`GFLOW_CLI_E2E_RUN_VIDEO=1` to include them. Run the full suite on `develop`
before opening a release PR to `main`.

See [docs/E2E_TESTING.md](docs/E2E_TESTING.md) for the complete layer reference,
cost table, and run commands.

### Coverage targets

- **`src/gflow_cli/cli.py`, `src/gflow_cli/cli_image.py`, `src/gflow_cli/cli_video.py`**: 70%+ (CLI plumbing — some Click branches are hard to unit-test)
- **`src/gflow_cli/api/`**: 90%+ (the meat — every captured route has a contract test)
- **`src/gflow_cli/auth.py`, `config.py`, `paths.py`, `profile_store.py`**: 80%+
- **Overall**: 80%+

`uv run pytest --cov=gflow_cli --cov-fail-under=80` enforces the floor. Don't merge below it.

## Quality gates (run before commit)

```bash
uv run python scripts/ci/check_repo_hygiene.py  # artefact + path hygiene
uv run ruff check src tests          # lint
uv run ruff format src tests         # auto-format
uv run pyright src                   # type-check (strict on src/gflow_cli/)
uv run pytest -q --cov=gflow_cli      # tests + coverage
```

CI runs all five on every push. Install local pre-commit hooks (recommended):

```bash
pip install pre-commit && pre-commit install
```

The `.pre-commit-config.yaml` already ships ruff and the hygiene gate.

### Script output convention

All runtime output — smoke runs, debug dumps, generated images — **must** go
to `tmp/` (already gitignored). `test_assets/` is for committed test
*fixtures* only (static input files for unit tests). Never write to
`test_assets/smoke_*/` or `test_assets/debug_*/` from scripts; the hygiene
gate blocks this.

## Adding a new API route

1. **Capture the live request** — add a sanitised JSON sample under `samples/captured/` (numbered, e.g. `08_<route>.json`; scrub project IDs, asset UUIDs, bearer tokens, and reCAPTCHA tokens).
2. **Write the contract test first** — under `tests/api/`:
   ```python
   async def test_new_route_returns_expected_dto(mock_client):
       result = await mock_client.new_route(...)
       assert result.some_field
   ```
3. **Implement** in `src/gflow_cli/api/client.py` (and add helpers under `src/gflow_cli/api/` as needed) until green.
4. **Add a `live` test** that runs the real flow end-to-end (skipped in CI by default).
5. **Update `CHANGELOG.md`** under `[Unreleased] → Added`.
6. **Document** the route in the README's Architecture section if it's a new capability.

## Commit messages

Follow [Conventional Commits 1.0](https://www.conventionalcommits.org/):

```text
<type>(<scope>): <short summary>

<optional body explaining the why>

<optional footer for BREAKING CHANGE: or refs>
```

`type`: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`.

## Contribution provenance

External contributions must have clear provenance. By opening a pull request,
you agree that your contribution is submitted under this project's MIT license
and that you have the right to contribute it.

For external contributors, commits should include a Developer Certificate of
Origin sign-off:

```bash
git commit -s -m "fix(auth): handle rejected browser login"
```

This adds a `Signed-off-by:` trailer using your configured Git name and email.
If you already committed, use `git commit --amend -s` and force-push the branch.

Please use a real Git identity or a GitHub noreply email. Avoid placeholder or
machine-local author addresses such as `user@hostname.local`; maintainers may
ask you to amend those before merging.

AI-assisted contributions are welcome when reviewed by the contributor, but do
not submit copied proprietary code, private Google/Flow internals, account
tokens, cookies, signed URLs, or other secrets.

## Releasing (maintainer only)

See the [Releases section in README](README.md#releases).

## Code of conduct

Be excellent to each other. Bug reports are welcome, blame is not. Unresolvable disagreements are decided by the maintainer.
