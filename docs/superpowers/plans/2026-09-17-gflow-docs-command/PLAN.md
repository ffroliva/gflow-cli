# `gflow docs` Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature gflow-docs-command` to find the
> next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** `gflow docs` makes the 126 pages in `docs/` reachable from the terminal — list
them, print one, or search for the line that answers a question — with no network, no
account, no credits, and **no checkout required**.

**Architecture:** One new pure module (`docs_catalog.py`) holds every decision — what a
topic is, how a name resolves, how search ranks — and does no I/O beyond reading package
data through `importlib.resources`. One thin Click module (`cli_docs.py`) renders it, in
the shape `cli_models.py` established. A hatchling build hook puts `docs/*.md` into the
wheel as `gflow_cli/_docs/`. Nothing existing changes except one import and one
`add_command` in `cli.py`.

**Predict verdict:** not required — `/gflow:predict` gates transport, auth, selector and
schema changes ([AGENTS.md](../../../../AGENTS.md) routing table). This feature touches
none of the four: it adds a read-only command that reads files shipped inside its own
package. Said out loud rather than skipped silently.

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| Critical | A topic name is used to build a path → arbitrary file read | Names are matched against the **enumerated** set of shipped pages. No user string ever reaches a path join. Scenarios #1/#2 |
| Critical | Ships only for people with a git checkout, i.e. not the user in #861 | Build hook + an integration test that builds a wheel, installs it into a scratch venv and runs the command there. Scenario #3 |
| Critical | `UnicodeEncodeError` on a cp1252 Windows console — the #846 shape, one release old | Every byte printed goes through one `_console_safe()` round-trip. Scenario #4 |
| High | A hand-maintained topic list drifts the first time a page is added | There is no list. The glob is the list, at build time and at read time. Scenario #11 |
| High | Search answers `MCP.md` and leaves the reader in a 100 KB file | Matches are returned as `file:line` **with the line**, and `INDEX.md`'s curated one-liners rank above raw body hits. Scenarios #8/#9 |
| Medium | `INDEX.md` § Topic shortcuts is parsed; its format could change | The parser is additive — if it yields nothing, full-text search still answers. Never load-bearing. Scenario #10 |

---

## File structure

### New files

```
hatch_build.py
  Build hook: force-include docs/*.md into the wheel as gflow_cli/_docs/.
src/gflow_cli/docs_catalog.py
  Pure catalog + resolution + search. No Click, no console, no network.
src/gflow_cli/cli_docs.py
  `gflow docs` / `docs <topic>` / `docs --search` — rendering only.
tests/cli/test_cli_docs.py
  Scenarios 1,2,4,6,7,9,10,12,16 — resolution, refusal, ranking, encoding, --json.
tests/cli/test_docs_catalog_real_docs.py
  Scenarios 8,11 — asserted against the REAL shipped pages, not a fixture.
tests/integration/test_docs_ships_in_wheel.py
  Scenario 3 — build a wheel, install it in a scratch venv, run `gflow docs` there.
docs/superpowers/plans/2026-09-17-gflow-docs-command/{SCENARIO,PLAN}.md
  This analysis.
```

### Modified files

```
pyproject.toml
  Register the build hook.
src/gflow_cli/cli.py
  One import, one add_command.
docs/USAGE.md
  A `gflow docs` section.
docs/INDEX.md
  One Topic-shortcut row, so the command is discoverable the way everything else is.
CHANGELOG.md
  [Unreleased] → Added.
```

---

## Task 1 — Ship the pages (test scaffold + build hook)

**What:** `docs/*.md` lands in the wheel as `gflow_cli/_docs/`, and nothing else does.

**Steps:**
- [ ] `hatch_build.py` with a `custom` build hook writing `build_data["force_include"]`
- [ ] Register `[tool.hatch.build.targets.wheel.hooks.custom]` in `pyproject.toml`
- [ ] `tests/integration/test_docs_ships_in_wheel.py` — build, install, run

**Tests created (red):**
- [ ] `test_the_wheel_carries_the_top_level_pages_and_nothing_else` — asserts
      `assets/` and `superpowers/` are absent (the measured failure of the naive
      `force-include` + `exclude`, SCENARIO § M1)
- [ ] `test_docs_command_works_from_an_installed_wheel_with_no_checkout` — scenario #3

## Task 2 — The catalog (pure)

**What:** `docs_catalog.py`: enumerate topics, resolve a name, search.

**Steps:**
- [ ] `topics()` — slug, title (first `# `), summary (first prose line), repo path
- [ ] `resolve(name)` — exact slug, file name, case-insensitive, unique prefix; returns
      `None` for everything else. **Never** builds a path from the input
- [ ] `search(term)` — curated `INDEX.md` shortcuts first, then `file:line` body hits
- [ ] `read(topic)` — the page's text

**Tests created (red):**
- [ ] traversal refused (`../`, `..\`, absolute, drive letter, NUL) — #1, #2
- [ ] prefix + `.md` + case forms all resolve — #16
- [ ] ambiguous prefix is refused, and names the candidates
- [ ] `search("duration")` returns the migrated-r2v-8s line, with its file and line — #8
- [ ] a curated INDEX hit outranks a body hit for the same term — #9
- [ ] an INDEX with no parseable shortcuts still searches — #10
- [ ] every `docs/*.md` appears in `topics()` — #11

## Task 3 — The command

**What:** `cli_docs.py` + registration.

**Steps:**
- [ ] `gflow docs` — table of topics; `gflow docs <topic>` — the page; `--search`
- [ ] `--json` on all three
- [ ] `_console_safe()` on every printed string — #4
- [ ] Unknown topic → `ConfigurationError` (exit 11, already in `EXIT_CODE_MAP`),
      message naming the nearest topics — #6
- [ ] `--search` with no matches → exit 0 — #7

**Tests created (red):**
- [ ] cp1252 stdout prints a page full of `—`/`→`/`✅` without raising — #4
- [ ] unknown topic exits 11 and suggests — #6
- [ ] empty search exits 0 — #7
- [ ] `--json` shapes — #12

## Task 4 — Decide the MCP twin, in writing

**What:** Scenario #14. The stated consumer is an agent, so "no twin" is a claim.

**Steps:**
- [ ] Decide and record the reasoning in the module docstring and CHANGELOG

## Task 5 — Docs + changelog

**Steps:**
- [ ] `docs/USAGE.md` section, `docs/INDEX.md` shortcut row, `CHANGELOG.md`

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest)
- [ ] `CHANGELOG.md` `[Unreleased]` updated
- [ ] `docs/USAGE.md` + `docs/INDEX.md` updated
- [ ] Every Critical + High scenario from `SCENARIO.md` has a test, **including** the
      installed-wheel one — offline-green in the repo is exactly the false positive
      this feature exists to correct
- [ ] No `# TODO` without a tracked issue link
- [ ] `/gflow:branch-review` run and findings applied or declined in writing
