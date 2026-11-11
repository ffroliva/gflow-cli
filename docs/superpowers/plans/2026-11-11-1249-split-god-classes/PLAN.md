# #1249 wave 1 - size ratchet and FlowApiClient split - Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature 1249-split-god-classes` to find the
> next unchecked task. One task per commit. `/gflow:check` before every commit.

**Goal:** Stop god classes from growing (a ratchet gate) and split `FlowApiClient` into
single-responsibility classes, with no user-visible change: same CLI, MCP tools, exit codes,
JSON shapes, log events and Flow traffic.

**Architecture:** `scripts/ci/check_size.py` measures file/class length (AST) and runs ruff
`C901`/`PLR0915`/`PLR0904`; offenders sit in `scripts/ci/size_baseline.json`, which can only
shrink. `FlowApiClient` becomes a facade over a linear mixin chain, and moved method bodies
are byte-identical.

**Predict verdict:** CAUTION 8/10 ([PREDICT.md](PREDICT.md)). Scenarios: [SCENARIO.md](SCENARIO.md).
Assessment: [ASSESSMENT.md](ASSESSMENT.md).

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Critical | Instance patches of private helpers stop reaching moved code | mixins, not composition (S1) |
| High | Module-level patch targets go stale | retarget strings; list each edit (S3, S4) |
| High | Gate blocks unrelated work | loose limits + ratchet baseline |

## Target shape (PR 3)

```
src/gflow_cli/api/
  client.py            FlowApiClient(CharacterSurface, _AccountBootstrap): lifecycle body
                       (__aenter__/__aexit__, launch, context, page pool, transport setup,
                       close, health_check) + re-exports of every name imported from it
  _flow_session.py     _FlowSession: __init__, recorder hooks, incidents, page checkout,
                       token, headers, _post/_patch/_get_json, retry, _write_target
                       + shared module helpers (errors, redaction, trpc)
  _flow_bootstrap.py   _AccountBootstrap(_FlowSession): account chooser, locale
  _flow_workspace.py   WorkspaceSurface(_FlowSession): projects, agent info, uploads,
                       workflows, scenes, listing, concat
  _flow_generation.py  GenerationSurface(WorkspaceSurface): generate_*, drivers, mint,
                       poll, downloads, get_credits
  _flow_media_edit.py  MediaEditSurface(GenerationSurface): upsample_*, extend_video*,
                       capability_listing, served_route
  _flow_characters.py  CharacterSurface(MediaEditSurface): entity CRUD, character image
```

Exact method-to-module assignment is computed by `scripts/dev/split_flow_client.py` (the
lever) from the AST and is listed in the PR.

---

## PR 1 - size gate + baseline (`chore/1249-size-gate`)

### Task 1.1 - plan docs
- [x] ASSESSMENT.md, PREDICT.md, SCENARIO.md, PLAN.md

### Task 1.2 - gate tests first (red), then the gate
- [x] `tests/scripts/test_check_size.py`: new oversized file fails; new oversized class fails;
      new ruff C901/PLR0915/PLR0904 offender fails; baselined offender may stay or shrink, not
      grow; `ratcheted` lowers/drops, never adds; repo passes its own baseline
- [x] see the ruff tests fail (no ruff support yet)
- [x] `scripts/ci/check_size.py`: ruff JSON metrics keyed `CODE:<rel>::<qualname>`
- [x] thresholds set once in `pyproject.toml` (mccabe 10, pylint statements 50, public 20)
- [x] baseline seeded with today's offenders

### Task 1.3 - wiring
- [x] CI step in `.github/workflows/ci.yml`
- [x] pre-commit hook (`language: system`)
- [x] `skills/check/SKILL.md` + AGENTS.md Impeccable Routine list
- [x] `docs/ARCHITECTURE.md` rule line

E2E: not applicable. PR 1 touches no Flow surface (scripts, CI, docs only).

## PR 2 - characterization tests (`test/1249-client-characterization`)

### Task 2.1 - pin the facade
- [x] `tests/api/test_client_facade_contract.py`:
  - the public method set of `FlowApiClient` with `inspect.signature` text and sync/async kind
  - the private helpers that tests patch exist on the class with the same signatures
  - every name imported from `gflow_cli.api.client` across src/tests/scripts is importable
  - delegation results: a facade call with a stubbed `_post_json`/transport returns the same
    observable result for one method per surface (project create, image generate, poll,
    upsample, character list)
- [x] green on develop before any move

E2E: not applicable (tests only).

## PR 3 - FlowApiClient split (`refactor/1249-split-flow-client`)

### Task 3.1 - metrics before
- [x] radon cc + coverage for `client.py` -> CRAP per function (baseline table)
- [x] pylint duplicate-code on `src/gflow_cli/api`

### Task 3.2 - the lever
- [x] the lever (kept out of the repo, one-shot; described in the PR): moves named methods (with leading comments and
      decorators) and module helpers by AST span, byte-identical; do one class by hand, then
      make the tool reproduce it

### Task 3.3..3.8 - one move per commit, suite green between each
- [x] `_flow_session.py`  - [x] `_flow_bootstrap.py`  - [x] `_flow_workspace.py`
- [x] `_flow_generation.py`  - [x] `_flow_media_edit.py`  - [x] `_flow_characters.py`
- [x] each commit lists its patch-target edits (S3/S4)

### Task 3.9 - metrics after + ratchet
- [x] `check_size.py --update-baseline`: `client.py` < 1,500 lines; FlowApiClient drops out
- [x] CRAP per moved function unchanged or better, new functions < 10
- [x] duplicate-code: no new blocks
- [x] wiring mutation run (mutmut does not run on Windows): each chain base, facade base and re-export removed in turn

### Task 3.10 - e2e on denon82
- [ ] `agentctl join` + `lease browser-session`
- [ ] `pytest -m "e2e_auth or e2e_image"` and `tests/e2e/test_credits_mcp_e2e.py`, results pasted

## G7 - wave 2 plan
- [x] `docs/superpowers/plans/2026-11-11-1249-wave2-god-classes/PLAN.md` (not implemented)

---

## Decision log

| Date | Decision | Why | Rejected |
|---|---|---|---|
| 2026-11-11 | Keep the WIP limits (file 2,000 / class 1,000) rather than the 400-line prose rule | A 400 limit freezes ~45 files and blocks unrelated work, which the issue rules out. The ratchet stops growth, and limits tighten one step at a time | 400/600 now |
| 2026-11-11 | Public-method count comes from ruff PLR0904 (default 20), not the WIP's AST counter | One measurement, one place; the goal names PLR0904 | AST `class-public` at 30 |
| 2026-11-11 | ruff thresholds pinned in `pyproject.toml`; the gate parses `(N > M)` from ruff JSON | Threshold lives in one place; ruff 0.17.4 concise output prints rule names, not codes | hard-coded limits in the script |
| 2026-11-11 | Drop the WIP's tracked `.claude/settings.json` PostToolUse/Stop hooks | Not asked for (G1 = check + CI); they would run on every Stop for every contributor, and they un-ignore a file people customise | keep hooks |
| 2026-11-11 | Pre-commit hook is `language: system` via `uv run --no-sync` | a `language: python` hook gets an isolated venv without ruff | `language: python` |
| 2026-11-11 | Mixins (linear chain), not composition | tests patch private helpers on the instance; composition needs body rewrites and breaks those patches (S1) | `self._generation = GenerationApi(self)` |
| 2026-11-11 | Linear chain in dependency order instead of type-only stubs | generation really calls `create_project`; stubs duplicate signatures and drift | TYPE_CHECKING Protocol stubs |
| 2026-11-11 | Five surfaces, not two (media edits and characters split out) | the 1,000-line class limit: generation alone is ~1,050 lines | two surfaces over the limit |
| 2026-11-11 | Base classes are `FlowSession` / `AccountBootstrap`, not `_FlowSession` | pyright strict flags an underscore class never used in its own module (`reportUnusedClass`); the modules are private already | `# pyright: ignore` on each |
| 2026-11-11 | client.py imports private re-exports one per statement with a line-level `reportPrivateUsage` ignore; new modules carry a file-level `reportPrivateUsage=false` | client.py's own code stays under the check; the new modules share helpers across files by design | file-level in client.py |
| 2026-11-11 | The gate re-keys a moved entry (same rule + name, value not higher) | without it a pure move reads as a new offender and the ratchet blocks the refactor it exists for | hand-editing the baseline |
| 2026-11-11 | CI runs the gate with `--base-ref`, failing a hand edit that adds or raises an entry | both reviews of PR 1: "shrink-only" was only true for `--update-baseline` | CODEOWNERS only |
| 2026-11-11 | Shrinking an offender without updating the baseline still passes | G1 requires that shrinking passes; `--update-baseline` locks it in | fail on a stale baseline |
| 2026-11-11 | `src/gflow_cli/api/_flow_` added to `check_materiality.py` | the token/mint code it flagged as `client.py` moved; dropping it would weaken the gate silently | leave the list |
| 2026-11-11 | `WafRejectionError` re-exported from client.py | `scripts/dev/spike_patchright.py` reads it off the module; the golden master caught the loss at step 5 | edit the script |
| 2026-11-11 | `get_credits` is not in the golden master | it drives the page UI (`page.on`), which a fake page cannot answer; `tests/e2e/test_credits_mcp_e2e.py` covers it live | a page-event fake |
| 2026-11-11 | ub-mode installed into `.claude-riskcontrol/skills/ub-mode` | owner request; it was only in `~/.claude` | - |
