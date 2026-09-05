## Summary

<!-- What changed and why? Link issues with "Fixes #123" when applicable. -->

## Lifecycle

<!-- The pipeline every PR is reviewed against: CONTRIBUTING.md § "The development lifecycle"
     and AGENTS.md § "Standard Workflow Sequence". Tick what applies; strike what does not and say why. -->

- [ ] Issue triaged with `issue-assessment` (verdict + which surfaces reproduce it: CLI / MCP / both)
- [ ] `predict` verdict recorded (transport / auth / selector / schema changes only)
- [ ] `scenario` + `plan` written under `docs/superpowers/plans/<date>-<slug>/` and linked here
- [ ] `check` green, including the step 1b CLI↔MCP mirror sweep
- [ ] MCP twin added, or a reasoned exemption in `tests/mcp/test_cli_parity.py`
- [ ] **E2E evidence provided** — name the `tests/e2e/` test you ran and paste its result, or point at the new test this PR adds. If you could not run it, say why here:
- [ ] Live-verified against real Flow where a generation path changed; what could NOT be verified is stated below
- [ ] Council review run (`pr-council-review` / `branch-review`) and must-fix items addressed

## Validation

<!-- List the focused commands you ran, plus any checks you could not run. -->

- [ ] Focused tests added or updated for behavior changes
- [ ] Documentation updated or explicitly marked not applicable
- [ ] `uv run python scripts/ci/check_doc_links.py`
- [ ] `uv run ruff check src tests`
- [ ] `uv run pyright src`
- [ ] Relevant pytest command:

## Contribution Checklist

- [ ] This PR targets `develop`, unless it is a release or emergency fix
- [ ] My commits use my real Git identity or GitHub noreply email
- [ ] External contribution commits include `Signed-off-by:` (`git commit -s`)
- [ ] I did not include secrets, cookies, account tokens, signed URLs, or private captured data
- [ ] I reviewed any AI-assisted changes before submitting
