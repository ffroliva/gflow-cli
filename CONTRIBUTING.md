# Contributing to flow-cli

Thanks for considering a contribution! Pre-1.0 the repo is private and managed by [@ffroliva](https://github.com/ffroliva), but the workflow described here is what'll be opened up to the community at the v0.2 alpha milestone.

## Development setup

```bash
git clone git@github.com:ffroliva/flow-cli.git
cd flow-cli
uv sync --extra dev
uv run playwright install chromium
```

## Test-driven development (mandatory)

`flow-cli` is built test-first. Every change must include tests, and CI rejects PRs that lower coverage.

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

@pytest.mark.live              # Hits the real Flow API. Requires GFLOW_LIVE=1 env var.
@pytest.mark.skipif(not os.getenv("GFLOW_LIVE"), reason="live tests opt-in")
async def test_full_i2v_roundtrip(): ...
```

CI runs `unit` + `integration` on every push. `live` tests run only on the maintainer's machine, against the maintainer's own account, before tagging a release.

### Coverage targets

- **`src/flow_cli/cli.py`**: 70%+ (CLI plumbing — some Click branches are hard to unit-test)
- **`src/flow_cli/providers/`**: 90%+ (the meat — every captured route has a contract test)
- **`src/flow_cli/auth.py`, `models.py`**: 80%+
- **Overall**: 80%+

`uv run pytest --cov=flow_cli --cov-fail-under=80` enforces the floor. Don't merge below it.

## Quality gates (run before commit)

```bash
uv run ruff check src tests          # lint
uv run ruff format src tests         # auto-format
uv run pyright src                   # type-check (strict on src/flow_cli/)
uv run pytest -q --cov=flow_cli      # tests + coverage
```

CI runs all four on every push. Local pre-commit hook recommended:

```yaml
# .pre-commit-config.yaml — install with `pip install pre-commit && pre-commit install`
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
      - id: ruff-format
```

## Adding a new Provider route

1. **Capture the live request** — add to `samples/captured_requests.json` (sanitise project IDs, asset UUIDs).
2. **Write the contract test first** — `tests/providers/test_flow_<route>.py`:
   ```python
   async def test_upload_image_returns_asset(mock_flow_provider):
       asset = await mock_flow_provider.upload_image(Path("tests/fixtures/sample.png"))
       assert asset.uuid
       assert asset.kind == "image"
   ```
3. **Implement** in `src/flow_cli/providers/flow.py` until green.
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

## Releasing (maintainer only)

See the [Releases section in README](README.md#releases).

## Code of conduct

Be excellent to each other. Bug reports are welcome, blame is not. Unresolvable disagreements are decided by the maintainer.
