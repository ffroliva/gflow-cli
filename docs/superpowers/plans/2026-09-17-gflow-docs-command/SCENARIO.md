# Scenario: `gflow docs` — documentation reachable from the CLI (#861)

**Feature.** A read-only command group that makes the 126 files in `docs/` reachable at
the moment of use: `gflow docs` (list topics), `gflow docs <topic>` (print one),
`gflow docs --search <term>` (find the page that answers a question).

**Why it exists** is a real failure, recorded in #861: a rule stating that the migrated
host offers r2v at 8 s only *existed in writing* and was never found, so a recipe was run
with `--duration 10`. gflow refused pre-submit and nothing was billed, but only because a
guard happened to exist.

---

## Two measurements taken before planning

Both would have made a plan written on assumption wrong.

### M1 — `exclude` does NOT apply to hatchling's `force-include`

`docs/` is not in the wheel today (`packages = ["src/gflow_cli"]`). The obvious one-line
fix is `force-include = { "docs" = "gflow_cli/_docs" }` with `exclude` for the parts that
should not ship. Built and inspected:

| | |
|---|---|
| `_docs` entries in the wheel | **275** |
| `docs/superpowers/**` leaked despite `exclude` | **145** |
| `docs/assets/**` leaked despite `exclude` | **3** |
| uncompressed docs bytes | **4 323 911** |
| wheel size | **3.6 MB** |

So that shape is out. Per-file control is required, and 126 hand-written `force-include`
entries would drift on the first new page. **A hatch build hook** (`hatch_build.py`,
writing `build_data["force_include"]` from `docs/*.md`) is the only form that ships
exactly the top-level pages — 1.3 MB, no `assets/`, no `superpowers/` — and cannot drift,
because the glob *is* the list.

### M2 — the rule that motivated the issue is documented, and still unfindable

`--duration` on migrated r2v **is** written down. It is in `docs/MCP.md`, inside a single
~4 000-character bullet describing the `gflow_generate_video` tool:

> `duration` there accepts only `8` and is pinned when omitted — Flow offers r2v at its
> base tier alone, and at 4 or 6 it silently drops the references and bills a
> text-to-video clip

This kills the simplest `--search`. A search that answers `MCP.md` is not an answer: the
reader is then facing a 4 KB paragraph in a 100 KB file, which is the state the issue is
complaining about. **Search must return the matching line with its position**, and must
rank the curated one-liners in `INDEX.md` § Topic shortcuts above raw full-text hits.

### M3 — ranking cannot rescue a one-word query, and two attempts proved it

Scenario #8 was first written as *"`--search duration` surfaces the rule"*. Built, then
measured three times against the real corpus (133 matching lines):

| ordering | what came back first |
|---|---|
| alphabetical (no ranking) | three lines of `AGENT_UI_RECON.md`; the rule past the cap |
| + heading bonus | eight `LIVE_VERIFICATION_*` release records |
| + "page is routed from `INDEX.md`" | four release records, rule 5th |

The third was the idea with the best story — reuse the repository's own statement of what
is reference material rather than invent one. It is false: **`INDEX.md` links 93 of the
126 pages**, every release record included, so it separates nothing. It was removed rather
than kept as a knob that does nothing, and `_score`'s docstring records why, so it is not
reinvented.

**Corrected after the council review.** The `LIVE_VERIFICATION_*` rows in row 2 above were
not simply "release records outranking reference pages" — several were `#` **shell comments
inside fenced code blocks**, scored as Markdown headings. `# 5. One cheapest stable T2V
generation, no explicit --duration` ranked 2nd of 136. The fence tracking that fixes it is
four lines, and with it a bare `--search duration` now opens with curated `INDEX.md` rows
rather than release archaeology. The separate curated-shortcut parser was deleted in the
same pass: its hits were measured as a strict *subset* of what the body scan already
returned (0 of 7 for `r2v duration`), so 28 lines of regex were only re-ordering rows search
already had — and stripping Markdown from those rows mangled the file name they pointed at
(`REFERENCE_STRATEGIES.md` → `REFERENCESTRATEGIES.md`, printed as the answer). It is now a
score bonus.

What works is the query carrying a second word: `r2v duration` returns **5** hits with
`docs/MCP.md:87` first. So the acceptance test is #8 as amended, and #8b holds the command
to answering a vague question honestly — the count and a way to narrow — rather than
guessing.

---

## Coverage map

| Dimension | Active? | Why |
|---|---|---|
| D1 Auth & session | ❌ | No account, no session, no network. |
| D2 WAF / reCAPTCHA | ❌ | Nothing is fetched. |
| D3 Selector drift & locale | ❌ | No browser. Docs are English-only artefacts of this repo; there is no locale axis to get wrong. |
| D4 Batch & resume | ❌ | No ledger, nothing resumable. |
| D5 Concurrency & Page pool | ❌ | No pool, no async. |
| D6 Data layer | ❌ | Reads no DB and records nothing. A read-only command has no `OperationRecorder` callsite. |
| **D7 Error propagation & exit codes** | ✅ | A new failure mode ("no such topic") needs a class already in `EXIT_CODE_MAP`, not a new one. |
| **D8 Cross-platform paths & packaging** | ✅ | The whole feature is package-data resolution, plus the console-encoding trap that shipped as #846 one release ago. |
| D9 Transport edge cases | ❌ | No HTTP. |
| D10 Headless vs headed | ❌ | No browser. |
| **D11 Input validation** | ✅ | `<topic>` is user input used to select a file. That is a path-traversal surface. |
| **D12 Observability** | ✅ | Small: does a read-only command emit events at all, and is `--json` shaped like every other command's. |
| **D13 MCP parity** | ✅ | The issue's stated consumer is *an agent*. Whether this gets an MCP twin is a decision, and `gflow update` is the precedent for deliberately refusing one. |

---

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D11 | `gflow docs ../../../../etc/passwd` — or `..\..\` on Windows | **Critical** | Refused as an unknown topic. The topic name is matched against the *enumerated* set of shipped pages; it is never concatenated into a path. Nothing outside `_docs` is ever read | Unit |
| 2 | D11 | `gflow docs` with a topic containing a path separator, a NUL, or a drive letter (`C:\x`) | **Critical** | Same refusal, same code path — the guard is "is it in the enumerated set", so these need no special cases and must not acquire any | Unit |
| 3 | D8 | Installed from a wheel (`uv tool install`), no checkout anywhere on the machine | **Critical** | Every subcommand works. This is the *only* scenario that matters for the issue's stated user; a command that needs a git checkout solves nothing | Integration (build a wheel, install into a scratch venv, run it) |
| 4 | D8 | Windows console, `PYTHONUTF8` unset, cp1252 stdout, printing a page full of `—`, `→`, `✅` | **Critical** | No `UnicodeEncodeError`. This exact class shipped as #846 in the *previous* release — a decoration that only ever ran on a dev machine | Unit + Integration |
| 5 | D8 | The package is imported from a zipimport / frozen environment where `_docs` has no real filesystem path | High | Read through `importlib.resources`, never `Path(__file__).parent`. A `Traversable` that is not a real path must still read | Unit |
| 6 | D7 | `gflow docs no-such-page` | High | A typed error already in `EXIT_CODE_MAP` (**no new exception class**, no new exit code), whose message lists the nearest matching topics rather than only saying no | Unit |
| 7 | D7 | `gflow docs --search <term>` matching nothing | Medium | Exit 0 with "no matches", **not** an error. An empty result is a true answer to a valid question | Unit |
| 8 | M2 | `gflow docs --search "r2v duration"` on the real corpus | **Critical** | The migrated-r2v-8s rule comes back **first**, as a windowed line with `docs/MCP.md:87` next to it. Amended after measurement — see § M3; the bare word `duration` cannot be made to rank it | Unit (asserted against the real shipped docs) |
| 8b | M2 | `gflow docs --search duration` — 133 hits | High | Shows the cap, says how many were held back, and suggests narrowing. An honest count beats a confident wrong first result | Unit (real docs) |
| 9 | M2 | A term that appears in both a curated `INDEX.md` question and raw body text | High | The curated one-liner ranks first. It was written to be an answer; a body-text hit is only a location | Unit |
| 10 | D8 | `docs/INDEX.md` § Topic shortcuts changes shape (a line stops matching the `**"Q"** → [label](path)` form) | High | Degrade to full-text search, never crash and never silently return nothing. The parser must not be load-bearing for the command's basic function | Unit |
| 11 | D8 | A new page is added to `docs/` and nobody updates anything | High | It appears in `gflow docs` automatically. If the topic list is a hand-maintained manifest, it will drift — this scenario is the argument against one | Unit |
| 12 | D12 | `gflow docs --json` | Medium | The same envelope shape every other command emits, so an agent can parse it. Topic list as data, not as a rendered table | Unit |
| 13 | D12 | Structured-log events for a read-only command | Low | None needed. Emitting `error_raised` for "unknown topic" is fine; a success event for printing a file is noise | — (decision, not a test) |
| 14 | D13 | The MCP surface | High | **Decide explicitly.** An agent over MCP is the stated consumer, so "no twin" needs the `gflow update` style justification, in writing | — (decision) |
| 15 | D8 | Page count/size drifts upward over time (e.g. `docs/` doubles) | Low | The build hook globs, so it tracks automatically; wheel growth is the only cost and it is text | — |
| 16 | D11 | `gflow docs <topic>` given a unique *prefix* or the file name with `.md` | Medium | Accept both — `gflow docs usage`, `gflow docs USAGE.md`. An exact-only match makes the command feel broken for the most obvious input | Unit |

---

## Must-cover before merge (Critical + High)

1. **#1, #2 — path traversal.** Enumerate-then-match; never build a path from input.
2. **#3 — works from a wheel with no checkout.** Built, installed, and run in a scratch
   venv, because "it works in the repo" is exactly the failure this feature exists to fix.
3. **#4 — Windows cp1252 console.** #846 is one release old; this is the same shape.
4. **#8/#8b — `--search "r2v duration"` finds the migrated r2v rule, first.** The
   feature's reason for existing, asserted against the real docs rather than a fixture,
   plus the honest answer to the vague query (§ M3).
5. **#6, #9, #10, #11, #16** — refusal quality, ranking, parser resilience, no manifest.
6. **#5** — `importlib.resources`, not `__file__`.
7. **#14** — MCP decision written down either way.

## Deferred (Medium + Low)

1. **#13** — no success event. Revisit only if someone wants docs-usage telemetry.
2. **#15** — wheel growth. Measure at release; nothing to build.
3. Category grouping in the TOC (the mock-up in #861 shows `GETTING STARTED` /
   `GENERATION` headings). **Deliberately not built**: a hand-assigned category per page
   is a second manifest to drift, and scenario #11 is the argument against the first one.
   Alphabetical topics plus search covers the use case; revisit if 126 becomes 300.
4. A per-host capability matrix page (#861 calls it the highest-value page behind the
   command). That is a *documentation* task, not this command, and it is #639's to own.

---

## Suggested BDD scenarios

Deliberately **not** Gherkin-bound e2e. Every scenario above is decidable offline in our
own code — there is no browser, no Flow surface and no network anywhere in this feature —
so the Bug Lane's step 5 does not apply, and `tests/features/test_e2e_binding_guard.py`
would fail an `@e2e`-tagged feature with no live binder. The tests are ordinary
`tests/cli/test_docs*.py`, plus one integration test that builds and installs a wheel,
which is the only way to prove scenario #3.

Stated out loud because "write it as an e2e" is this repo's default answer, and here the
default is wrong: an e2e that mocks nothing but also touches nothing live is just a slow
unit test wearing a tag that makes hosted CI try to launch Chrome.
