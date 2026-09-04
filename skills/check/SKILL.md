---
name: check
version: "1.0"
description: >
  Auto-fix lint and formatting, then report types and tests. Run before every commit.
---

# `/gflow:check` — Quality gates

Run in order. Stop and report if a step fails after the fix pass.

## Steps

These steps mirror the CI `test` job in `.github/workflows/ci.yml` **in the same order**. Any command that CI runs as a *verify* (`--check`) is run here as a verify too — see step 4.

**1. Repo hygiene + doc links + website-docs PII guard** (read-only — CI runs all three; a broken doc link or a private identifier in the published `website/docs/` mirror fails CI)

```bash
PYTHONUTF8=1 uv run python scripts/ci/check_repo_hygiene.py
PYTHONUTF8=1 uv run python scripts/ci/check_doc_links.py
PYTHONUTF8=1 uv run python scripts/ci/check_website_docs_pii.py
PYTHONUTF8=1 uv run python scripts/ci/generate_website_docs.py --check
PYTHONUTF8=1 uv run python scripts/ci/check_council_memory.py
```

The last check fails on either half of the published-site contract:

- `DRIFT:` — canonical `docs/` changed but the `website/docs/` mirror was not
  regenerated. Fix with `uv run python scripts/ci/generate_website_docs.py` and
  stage `website/docs/`.
- `NAV-ORPHAN:` — a page is published but no `nav:` entry in
  `website/mkdocs.yml` points at it, so it is live but unreachable. Add the
  entry under the right nav section.

**1b. Surface blast radius — MCP↔CLI parity and the other mirrors** (read-only; MANDATORY whenever the change touches a CLI command, option, flag help text, exit code, or user-facing error string)

`tests/mcp/test_cli_parity.py` is a **command-level** gate only: it asserts every CLI *leaf*
has a mapped MCP tool or a stated exemption. It is green while a new `--option` goes
unmirrored, and green while an MCP docstring asserts a restriction the CLI no longer has.
That semantic drift is invisible to every automated gate in this file, so it is checked here
by hand — deliberately, because the alternative is shipping it.

```bash
PYTHONUTF8=1 uv run python -m pytest -q tests/mcp/test_cli_parity.py tests/mcp/test_server.py
```

Then enumerate the blast radius. For the CLI symbol(s) you changed (option name, model
alias, exit code, capability), grep the whole repo and confirm **every** hit is either
updated or still true:

```bash
# substitute the flag / alias / capability word you changed
grep -rn --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=tmp "<your-symbol>" \
    src/gflow_cli/mcp/ docs/ website/docs/ README.md KNOWN_ISSUES.md CHANGELOG.md skills/
```

Six axes, in blast-radius order. Tick each or state why it does not apply — "I did not touch
a CLI surface" is a valid answer for the whole step; silence is not.

**A. Execution paths — a request is built in THREE places, not one.**

| Hop | File | Fails as |
|---|---|---|
| CLI | `cli_video.py` / `cli_image.py` dataclass → request | option parsed but never passed |
| MCP direct | `mcp/tools.py` tool signature → payload dict | param accepted, dropped before the queue |
| MCP queued | `mcp/tools.py` payload keys → `worker/codec.py` decode → request | **silent no-op** — accepted, queued, never read |

The third hop is the dangerous one: the payload key the tool writes and the key the codec
reads are matched by string, so a mismatch type-checks, lints, and passes tests while doing
nothing. This has shipped before — a dead `output` param the queue never read survived until
a pre-release audit (#495). Verify the key names literally line up end to end:

```bash
grep -n "<param>" src/gflow_cli/mcp/tools.py src/gflow_cli/worker/codec.py
```

**B. MCP surface truth** — `mcp/tools.py` tool signature **and** every behavioural claim in
its docstring; `docs/MCP.md` parameter prose. `tests/mcp/test_cli_parity.py`'s
`CLI_TO_MCP` / `_MCP_EXEMPT` only when adding a NEW leaf.

**C. Agent-facing surfaces** — read by Codex / Cursor / Aider, not just by us, and covered by
no test at all: `skills/gflow-cli/SKILL.md`, the `AGENTS.md` "Command surface" bullet,
`README.md`, `docs/INDEX.md`.

**D. Error and exit-code surface** — `errors.py` `_default_remediation` **and** the class
docstring, `EXIT_CODE_MAP`, and the exit-code table in `docs/USAGE.md`. When a raise site is
deleted, re-derive what the remaining sites are: a remediation string that still describes a
removed case actively misleads.

```bash
grep -rn "raise <ErrorClass>" src/gflow_cli/   # must match what the docs table claims
```

**E. Declarative / template surfaces** — defaults baked into files users copy:
`cli_movie.py`'s manifest template, `movie_manifest.py`, `chain_manifest.py`,
`.env.template` for any env var, and the capability-reporting commands `gflow models` and
`gflow doctor`.

**F. Docs + published mirror** — `docs/USAGE.md` command section, `website/docs/`
(regenerate; step 1 catches staleness but never wrongness), `KNOWN_ISSUES.md` claims about
the surface, `CHANGELOG.md` under `### Changed` whenever behaviour a script could branch on
moves.

**Why this step exists.** In #626 the CLI stopped rejecting `omni-flash --end-frame` while
`mcp/tools.py` and `docs/MCP.md` went on telling agents the combination was rejected. Lint,
types, the full 2065-test suite, and the parity gate were green throughout, because none of
them can see a docstring that lies. The AGENTS.md prose saying "keep them in sync" had been
there the whole time and did not fire — the project's own lesson, again: wire the rule, or it
is a wish.

**2. Auto-fix lint and formatting** (rewrites files in place)

```bash
uv run ruff check --fix src tests
uv run ruff format src tests
```

Report which files were modified. **If this rewrote anything, those files are part of the change — stage them.** CI does not run the auto-fix; it runs the `--check` verify in step 4 against the *committed* tree, so an uncommitted reformat is a red CI build.

**3. Repeat lint/format ONLY on the files you touched? No — always run repo-wide.** The whole-tree `src tests` scope in step 2/4 is deliberate: a latent format failure in a file your change merely imports (e.g. a BDD step module) will fail CI even if your own edits are clean. Never narrow the scope to "just my files."

**4. Verify — the EXACT CI gate (non-mutating; must be clean before you commit/push)**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
```

These are byte-for-byte what CI runs (`ci.yml` "Lint" + "Format check"). If `--check` exits non-zero here, step 2's rewrites are **uncommitted** — stage them and re-run. **Never push while this is red:** the test job dies at the format step *before* pytest, which also makes SonarCloud report `new_coverage=0%` (no coverage XML is produced). A green `/gflow:check` that skips this verify is how PR #269 shipped a format failure past an 8-agent council.

**4c. Duplication proxy for the SonarCloud gate** (report only — offline approximation)

SonarCloud's quality gate fails on `new_duplicated_lines_density > 3%`, and it only
runs in CI — a copy-pasted option stack or body block sails through ruff/pyright/pytest
and then reddens the branch analysis after merge (this killed the develop build in the
v0.48.0 cycle). Approximate the CPD locally, zero new dependencies:

```bash
uvx pylint --disable=all --enable=duplicate-code --min-similarity-lines=10 src/gflow_cli
```

Any `duplicate-code` finding **inside code you are adding or touching** is a fix-now
signal (extract a shared helper/decorator before pushing). Findings in untouched files
are pre-existing — Sonar's gate measures *new* code only, so report them but do not
block on them. This is a proxy, not the gate: SonarCloud in CI remains authoritative.

**5. Type check** (report only — cannot auto-fix)

```bash
uv run pyright src
```

**6. Tests + coverage** (report only)

```bash
uv run python -m pytest -q --cov=gflow_cli --cov-fail-under=80
```

## Output

- List files changed by the fix pass (empty = nothing needed fixing)
- **Step 1b blast radius — the mirror table, each row ticked or explicitly marked N/A. "I did not touch a CLI surface" is a valid answer; silence is not.**
- **Step 4 verify result — `ruff check` + `ruff format --check` both clean (this is the CI gate; a non-zero here is a blocking finding, not a warning)**
- All pyright errors with `file:line` references
- Pytest summary line and coverage percentage
- Final verdict: all gates pass / which gates failed

## Pipeline Continuation (Next Step Handoff)

Upon successful completion of all quality gates:
1. **Local Feature Branch:** Proactively announce: **"Quality gates passed. Next step: Phase 5 Branch Council Review (`/gflow:branch-review`)."**
2. **Open PR:** Proactively announce: **"Quality gates passed. Next step: Phase 5 PR Council Review (`/gflow:pr-council-review <N>`) or Phase 8 Live Verification (`/gflow:live-verify`)."**

## Notes

Ruff fix and format may rewrite multiple files. Always `git diff` before staging.
Pyright errors and test failures require manual intervention — do not attempt silent workarounds.
If the coverage run crashes the current MCP/sandbox session with `Connection closed`, re-run the
same marker-filtered suite in smaller chunks without coverage and rely on CI for the coverage XML.
Project pytest defaults already exclude `e2e` and `live`; those markers are explicit,
credit-spending gates and must be requested with a separate `-m e2e` / `-m live` command.

**Windows agent sessions:** `uv run pytest` is unreliable — invoke
`.venv/Scripts/python.exe -m pytest` directly, and prefix Python invocations with
`PYTHONUTF8=1` when output contains non-ASCII. The unscoped full-coverage sweep
(step 6) can OOM locally (exit 137 / SIGKILL): run the suites scoped to the dirs you
touched and trust CI for the full coverage gate — **a green full-suite CI run on the
same tree (e.g. the just-merged PR) satisfies step 6 for release purposes.**

The OOM allowance applies to step 6 (coverage) ONLY. Step 4 (`ruff check` + `ruff
format --check src tests`) is cheap, never OOMs, and must ALWAYS be run repo-wide
against the exact tree you are about to push — no scoping, no skipping.

Offline-green here is not done-done for a change touching a generation code path (t2i/i2i/
i2v/t2v/r2v) — see `/gflow:live-verify` Part 2 before claiming that kind of feature complete.
