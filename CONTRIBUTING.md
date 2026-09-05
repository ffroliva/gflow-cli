# Contributing to gflow-cli

Thanks for considering a contribution! The repo is public and PRs are welcome. Pre-1.0 means APIs may shift between minor versions; check [PLAN.md](PLAN.md) for the active phase before starting a non-trivial change.

## The development lifecycle — how a PR gets in without back-and-forth

This project is developed through a fixed pipeline, and every PR is reviewed against it.
The pipeline is written down once, in [AGENTS.md](AGENTS.md) — the **Skill Routing**
table (which skill to load for which situation) and the **Standard Workflow Sequence**
(the ten phases, their artifacts, and the gates between them). The skills themselves are
plain Markdown under [`skills/<name>/SKILL.md`](skills/): agent-agnostic by design, so
Claude Code (`/gflow:<name>`), Codex (`$gflow:<name>`), Cursor, Aider, Antigravity, or a
human reading the file all follow the same protocol.

**If you use a coding agent, point it at [AGENTS.md](AGENTS.md) first.** Most tools
auto-discover it; if yours does not, paste the Skill Routing table into its context. A PR
whose agent skipped the pipeline is the single biggest source of review churn here.

What a contributor PR is expected to have gone through, phase by phase:

| Phase | Skill (`skills/<name>/SKILL.md`) | What it leaves behind for the reviewer |
|---|---|---|
| Touching a GitHub issue | [`issue-assessment`](skills/issue-assessment/SKILL.md) | A verdict (CONFIRMED / LIKELY / NEEDS-INFO …) and which surfaces reproduce it — CLI, MCP, or both |
| Transport / auth / selector / schema change | [`predict`](skills/predict/SKILL.md) | A GO / CAUTION / STOP verdict **before** code exists; STOP means open an issue instead |
| Any feature | [`scenario`](skills/scenario/SKILL.md) → [`plan`](skills/plan/SKILL.md) | `docs/superpowers/plans/<date>-<slug>/SCENARIO.md` + `PLAN.md` — the edge-case table and the task list; link them from the PR (they live on the branch and in the PR's history; at release the durable facts are folded into `docs/superpowers/memory/` and the plan dir is removed) |
| Every commit | [`check`](skills/check/SKILL.md) | The Impeccable Routine green (it is the exact CI gate), plus the **step 1b** CLI↔MCP mirror sweep no CI gate can see |
| Anything on a generation path | [`live-verify`](skills/live-verify/SKILL.md) | Evidence against real Flow, not just green offline tests — this project drives a black box; say plainly what you could **not** verify |
| Opening the PR | [`pr-council-review`](skills/pr-council-review/SKILL.md) / [`branch-review`](.claude/commands/gflow/branch-review.md) | The multi-dimension council (correctness, quality, security, tests, memory, YAGNI, parity …); maintainers run it on every PR, so running it yourself first removes a round trip |
| Red SonarCloud check | [`sonar`](skills/sonar/SKILL.md) | Zero new issues — the gate measures *new* code |
| Auth or reCAPTCHA | [`known-issues`](skills/known-issues/SKILL.md) | Known-broken surfaces have documented workarounds; rediscovering them costs days |

Three rules the pipeline enforces that catch first-time contributors most often:

1. **Ship each capability twice, or say why not.** Every CLI command has an MCP twin or a
   reasoned exemption in `tests/mcp/test_cli_parity.py` — and options, payload keys and tool
   docstrings must stay mirrored (AGENTS.md § *MCP & CLI Schema Symmetry*).
2. **Locale-invariant selectors only.** No `has-text(...)` on Flow's DOM, ever
   (AGENTS.md § *Locale-Invariance Discipline*).
3. **Verified beats claimed.** If a fix cannot be verified on the affected surface in your
   environment, the PR says so and uses `Refs #N`, not a green checkbox.

The PR template's *Lifecycle* checklist mirrors this table. Tick what applies, strike what
does not, and say why — a reviewer can then start from your evidence instead of re-deriving it.

## Development setup

```bash
git clone git@github.com:ffroliva/gflow-cli.git
cd gflow-cli
uv sync --extra dev
uv run playwright install chromium
```

### Codex project skills

Codex CLI and the Codex desktop app can install the repository's canonical `skills/`
workflows as the `gflow` plugin. From the repository root, run:

```bash
codex plugin marketplace add .
codex plugin add gflow@gflow-cli
```

Start a new Codex session after installation, then invoke a workflow with `$gflow:<skill>`
(for example, `$gflow:status`, `$gflow:check`, or `$gflow:pr-council-review`). Claude Code
continues to use the equivalent `/gflow:*` commands. The Codex IDE extension does not
currently load plugins; include the relevant `skills/<name>/SKILL.md` directly when using
that surface.

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
@pytest.mark.e2e_image         # Cost sub-marker: zero credits; draws on the daily image cap.
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

# Single image (zero credits; daily image cap)
uv run pytest -m "e2e_image and not e2e_batch" -v

# Full regression (all credits)
GFLOW_CLI_E2E_RUN_VIDEO=1 uv run pytest -m e2e -v
```

E2e video tests spend real Veo credits (image tests are free, daily-capped). Video tests default to opt-out — set
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

Prefer the skill — [`skills/check/SKILL.md`](skills/check/SKILL.md) (`/gflow:check` in
Claude Code, `$gflow:check` in Codex) — because it also carries the step 1b CLI↔MCP mirror
sweep that no command below can check. The mechanical subset it runs, in order:

```bash
uv run python scripts/ci/check_repo_hygiene.py        # artefact + path hygiene
uv run python scripts/ci/check_doc_links.py           # internal Markdown links
uv run python scripts/ci/check_website_docs_pii.py    # no private identifiers in website/docs
uv run python scripts/ci/generate_website_docs.py --check  # website mirror in sync
uv run python scripts/ci/check_council_memory.py      # council memory slugs resolve both ways
uv run ruff check src tests          # lint
uv run ruff format --check src tests # formatting
uv run pyright src                   # type-check (strict on src/gflow_cli/)
uv run pytest -q --cov=gflow_cli      # tests + coverage
```

CI runs all of them on every push (this list is kept identical to AGENTS.md's; a shorter
copy once shipped a stale website mirror to a green local run and a red CI). Documentation is part of the merge gate: update
the relevant docs for behavior, workflow, configuration, architecture, or
operator-facing changes. If no documentation change is needed, state that in
the PR validation checklist.

Install local pre-commit hooks (recommended):

```bash
pip install pre-commit && pre-commit install
```

The `.pre-commit-config.yaml` already ships ruff and the hygiene gate.

### Review lenses (over-engineering matters)

`gflow-cli` values **YAGNI / least-code** (see [AGENTS.md § Code style](AGENTS.md)):
the smallest change that works, no speculative abstractions, no dead code. This is a
first-class review dimension — **D14 over-engineering** in
[`pr-council-review`](skills/pr-council-review/SKILL.md), which always runs and carries
its own portable rubric.

Optionally, the `ponytail` Claude Code plugin (from the plugin marketplace) encodes
exactly this review taste — `/ponytail-review` audits a diff for over-engineering. It's a
**recommended, not required** accelerant: the D14 rubric applies with or without it, so no
contributor is blocked for lacking the plugin.

### SonarCloud quality gate

On top of the six gates, CI runs a **SonarCloud** analysis whose quality gate must be
**green** before a PR is merge-ready — the target is **zero new issues** on changed code
(new bugs, vulnerabilities, and code smells = 0; coverage of new lines ≥ 80%; security
hotspots reviewed = 100%). The local `--cov-fail-under=80` gate already pre-empts the
coverage condition, so if your tests are green locally you have usually cleared the part
of the gate you can run yourself.

SonarCloud is **server-side and maintainer-run**: it needs a repo secret (`SONAR_TOKEN`)
that, for security, GitHub does **not** share with pull requests from forks. So **on a
fork PR the SonarCloud check is skipped** — you cannot run it, and that is expected. A
maintainer checks the gate before merging (and, for fork PRs, may re-run the branch
internally to produce an analysis). Don't worry if you see the SonarCloud check absent or
skipped on your PR; focus on keeping the six local gates green and your diff free of new
smells. Full policy: [`docs/GITHUB.md`](docs/GITHUB.md) § SonarCloud Quality Gate.

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
