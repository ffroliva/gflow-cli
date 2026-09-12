# Live verification — v0.73.2

**Date:** 2026-09-12 · **Profile:** `ffroliva` (migrated host, `flow.google.com`) ·
**Cost:** **zero** — every arm fails before submit, so no image and no Veo credits were
spent.

v0.73.2 is a diagnostics and error-clarity patch. It does **not** fix migrated login
(#791) or the i2v dispatch failure reported in #792 — both remain open. What it fixes is
the layer underneath those: the evidence a failure produces, and the advice a failure
prints.

One consequence worth stating plainly, because it decides whether this release is worth
cutting at all: until now every failure on `flow.google.com` shipped an **unreadable**
incident bundle. The reporter of #792 filed a wrong root cause *because* of it. This
release is what makes the next report actionable.

---

## 1. The defect, measured before the fix (control arm)

The claim under test: the migrated composer parks its own page on `about:blank` inside a
bare `finally`, which runs on the **failure** path too — so `_capture_incident`
photographs a blank page instead of the page that failed.

Control: `develop` source, with only the new e2e test file checked out on top, driving a
real failure (a syntactically valid but nonexistent `--project` UUID lands on Flow's 404
and fails the `.settings-trigger-button` probe with exit 23).

```
tests/e2e/test_incident_quality_e2e.py::test_a_failed_generation_bundle_is_not_hollow
FAILED in 46.27s
```

The bundle it produced, verbatim from the assertion:

```json
{"ligatures": [], "ligature_count": 0,
 "signals": {"crop_present": false, "textboxes": 0},
 "url": {"host_category": "other", "route": "other"},
 "title": {"category": "other", "length": 0},
 "tag_counts": {"div": 0, "button": 0, "input": 0, "textarea": 0,
                "dialog": 0, "iframe": 0, "video": 0, "img": 0},
 "viewport": {"width": 1280, "height": 720}}
```

That is field-for-field the signature [#792](https://github.com/ffroliva/gflow-cli/issues/792)
reported from a different account, on a different OS, on a different command. The bug
reproduces.

## 2. The cure, measured with the same instrument

Same test, same profile, same command shape, on the release branch:

```
tests/e2e/test_incident_quality_e2e.py::test_a_failed_generation_bundle_is_not_hollow
1 passed in 37.80s
```

| Reading | Control (`develop`) | v0.73.2 |
|---|---|---|
| `tag_counts.div` | **0** | **25** |
| `title.length` | **0** | **81** |
| `url.host_category` | **`other`** | **`flow_app`** |
| `url.route` | `other` | `/404` |
| `ligature_count` | 0 | 1 (`arrow_back`) |
| `signals.textboxes` | 0 | 1 |
| `sensitive/screenshot.png` | **4,254 B** (blank frame) | **38.4 KB** (real page) |

An earlier manual A/B on the same host, taken before the design was revised, produced the
same two columns — so the result is not an artifact of the final implementation.

## 3. Five-layer ledger — #792

| Layer | Evidence |
|---|---|
| Artifact exists | Both arms wrote an incident bundle under `$GFLOW_CLI_HOME/incidents/2026-09-12/`; the CLI printed its path and a `report.md` |
| Magic bytes / size | `screenshot.png` 4,254 B (control) vs 38,4xx B (fixed) — a blank 1280×720 PNG compresses to ~4 KB, a rendered Flow page ~10× that |
| Shape | `ui.json` `tag_counts.div` 0 → 25, `title.length` 0 → 81, `host_category` `other` → `flow_app` |
| Log invariants | Exit **23** in both arms with the same `UiSelectorDriftError` on `.settings-trigger-button` at `https://flow.google.com/404?reason=project` — i.e. the arms differ only in what was *captured*, not in what failed |
| User-confirmable | The bundle directory is openable; the screenshot now shows the Flow 404 page rather than white |

The test is committed, so this is a re-runnable regression, not a one-off observation:
`pytest -m e2e tests/e2e/test_incident_quality_e2e.py` with `GFLOW_CLI_E2E_PROFILE` set.

## 4. NOT live-verified this cycle — and why

**#796 — `PROFILE_MARKER_MISSING`.** Offline only (unit + MCP-twin tests). Its live
trigger is a cookie-store **decryption failure**, which does not occur on Windows/DPAPI —
it is the macOS Keychain path tracked in #768. **Named external blocker: no macOS host
available.** This is recorded, not omitted, and the issue stays open with `Refs`.

A synthetic attempt to force the path is worth recording because it *failed informatively*:
a deliberately corrupted cookie store raises `sqlite3.DatabaseError`, which is not
normalized to `PermissionError`, so it bypasses the marker gate entirely and still prints
"check network connectivity". Same misdirection class, different trigger — untouched by
this release.

To close it, on macOS: fail a login once on a fresh profile, then
`GFLOW_CLI_LOG_LEVEL=DEBUG gflow auth status`, and confirm the outcome reads
`profile_marker_missing` rather than `verification_error`.

**#795 — the `credits` remediation string.** Offline only. The raise path is reachable
live on this account (labs answers `200 {}` for migrated accounts), but the change is a
message, and its correctness is a text assertion the unit test already makes. The *other*
half of #795 — `credits` being labs-only on migrated accounts — is **not fixed** and stays
open.

**The two pre-existing `[Unreleased]` entries** (#756's `/about` retry measurement and the
terminal-width test pin) were verified in their own PRs; the second touches no Flow
surface at all.

---

## 5. Doc-review gate (`/gflow:doc-review`)

Mechanical sections 0-7 **PASS**: repo hygiene (1073 files), doc links (29 files),
website PII (25 files), `generate_website_docs.py --check` (20 files, nav complete),
`check_release_artifacts.py`, council memory (49 files) — all green, re-run after every
fix below. CHANGELOG footer correct (`[Unreleased]` empty, `compare/v0.73.1...v0.73.2`).
`EXIT_CODE_MAP` is unchanged at 37 entries, range 3-38 — #796 adds a session *outcome*,
not an error class, and `gflow auth status` still exits 1.

Council: **GREEN** (cross-reference) / **YELLOW** (completeness) / **YELLOW** (drift).
No RED. Eight findings fixed in this release-prep commit:

| # | Finding | Fixed in |
|---|---|---|
| 1 | `docs/MCP.md` said the `/about` retry flag "was measured and could not be settled" — this release settles it 5/5 | `docs/MCP.md` |
| 2 | `docs/USAGE.md` exit-31 row said the retry "is not measured" and linked the superseded 2026-09-10 spike | `docs/USAGE.md` |
| 3 | `gflow_auth_status`'s new `profile_marker_missing` outcome (409, `retryable: false`) was undocumented | `docs/MCP.md` |
| 4 | The same outcome was undocumented on the **CLI** surface | `docs/AUTHENTICATION.md` |
| 5 | `gflow credits` being labs-only on migrated accounts (#795, open) appeared in no user doc | `docs/USAGE.md`, `docs/MCP.md`, `KNOWN_ISSUES.md` (x2) |
| 6 | CHANGELOG undercounted the corrected `/about` sites ("two comments" — it is four) | `CHANGELOG.md` |
| 7 | `docs/ARCHITECTURE.md`'s current-layout inventory had drifted again: no `flow_selectors/`, `_cli_helpers.py`, `exceptions.py`, `file_integrity.py`, `redaction.py` | `docs/ARCHITECTURE.md` |
| 8 | `docs/USER_GUIDE.md` 1.3 told first-time users bare `gflow auth login`, contradicting README/AGENTS.md | `docs/USER_GUIDE.md` |

Carried forward rather than fixed here, each with its reason:

- **`src/gflow_cli/auth/verification.py:87`** — the `FlowSessionStatus` docstring still says
  `detail` is "one of the **four** fixed strings"; `_DETAIL_BY_OUTCOME` has **five** since
  #796. Found independently by the drift auditor and the mechanical pass. Left to the
  implementer: this gate does not edit `src/`.
- **`KNOWN_ISSUES.md` has no entry for #791, #792's dispatch half, or #768** — all open and
  user-facing (titles confirmed via `gh issue view`). Not written here, because #791's
  premise ("verification never passes for migrated accounts") is contradicted by this
  repo's own [v0.72.0 ledger](LIVE_VERIFICATION_v0.72.0.md), which signed in six times on a
  migrated account; an entry asserting it is universal would be exactly the n=1
  generalisation the project forbids. Route through `/gflow:issue-assessment` first.

---

## Verdict

The release's headline change is verified live on the affected surface, with a control
arm, by a committed test. The two auth-message changes are verified offline with one
named external blocker recorded above. Nothing in this release is claimed as verified
that was not run.

---

## Gates

Run on `chore/release-v0.73.2` (cut from `develop@15c58af0`), 2026-09-12.

| Gate | Result |
|---|---|
| `check_repo_hygiene.py` | PASS |
| `check_doc_links.py` | PASS |
| `check_website_docs_pii.py` | PASS |
| `generate_website_docs.py --check` | PASS (mirror in sync) |
| `check_council_memory.py` | PASS |
| `check_release_artifacts.py` | PASS — it caught the missing `docs/INDEX.md` ledger reference before the tag |
| `ruff check src tests` | PASS |
| `ruff format --check src tests` | PASS (467 files) |
| `uv run pyright src` | PASS — **0 errors** |
| `pytest` (offline, full) | **4241 passed, 24 skipped** on `develop@15c58af0` |
| `pytest tests/auth tests/mcp tests/cli` | **884 passed** on the release branch |
| `uv lock --check` | PASS (lockfile carries the 0.73.2 bump) |
| `uv build` + ZIP-duplicate scan | PASS — wheel reports `__version__ = "0.73.2"`, 138 entries, no duplicates |
| `/gflow:doc-review` | **PASS** — 8 fixes applied, two of them false claims this release contradicts |

**One gate caveat worth recording.** `pyright` must be invoked as `uv run pyright src`
(what CI runs). Invoked as `.venv/Scripts/python.exe -m pyright src` on this machine it
reports **86 phantom errors** in `mcp/` and `ui/` — stable across re-runs, convincing
(`"MCPServer" is unknown import symbol`), and entirely an artifact of how that invocation
resolves the environment. The packages are correctly installed and the suite passes
against them. It nearly blocked this release.
