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

**1. Repo hygiene + doc links** (read-only — CI runs both; a broken doc link fails CI)

```bash
PYTHONUTF8=1 uv run python scripts/ci/check_repo_hygiene.py
PYTHONUTF8=1 uv run python scripts/ci/check_doc_links.py
```

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
- **Step 4 verify result — `ruff check` + `ruff format --check` both clean (this is the CI gate; a non-zero here is a blocking finding, not a warning)**
- All pyright errors with `file:line` references
- Pytest summary line and coverage percentage
- Final verdict: all gates pass / which gates failed

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
