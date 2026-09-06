# Spike Tooling Reorg Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature spike-tooling-reorg` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** A spike tool a contributor on any OS can run to produce redacted evidence — backed by
a small tested library, with probes that stop accumulating.

**Architecture:** A new `src/gflow_cli/spike/` package holds the two capabilities that are
genuinely reused: CDP attach/launch (`cdp.py`) and HAR capture/shaping (`har.py`). Probes become
**ephemeral by default** — the *finding* is the durable artefact, not the script. The
Node/`agent-browser`/PowerShell harness is deleted as each Python twin lands.

`spike` because it is already the word everywhere a human looks: `/gflow:spike`,
`skills/spike/SKILL.md`, 48 `scripts/dev/spike_*.py`, `docs/superpowers/spikes/`. Named `spike/`,
not `_spike/` — [#707](https://github.com/ffroliva/gflow-cli/issues/707) builds a user-facing
path on this code, so it is not private.

**Predict verdict:** pending — dev tooling, not a transport/auth/selector/schema change, so the
AGENTS.md routing table does not require `/gflow:predict`. Run it before #707, which does.

**Portability proven before planning** (`scripts/dev/spike_cdp_har_without_node.py`, live on a
migrated account): `connect_over_cdp` attaches to a real gflow profile and sees the human's page
(`contexts=1, pages=1, sees_human_page=True`); `record_har_path` works on a CDP-attached browser;
and raw CDP `Network.*` events shape into a HAR that `extract_har_summary.summarize_har` consumes
**unchanged** (38 entries). The Node layer can go.

---

## Council review changed this plan substantially

Two reviewers ran before implementation. **Both falsified assumptions in the first draft**, and
the plan is smaller as a result. Recorded because the reasoning matters more than the conclusion.

**1. The DOM-JS duplication this plan was built around does not exist.** Measured: 40 embedded JS
blocks / 954 lines across 28 scripts; trigram-Jaccard over all 780 pairs found **only 2 above
0.35**. The single literal repeat is one line — the `LIG` constant, 6 occurrences.
`elementFromPoint` appears **once** in 40 blocks. That is a constant, not a module.
→ **`spike/dom.py` is cut.** Three modules become two.

**2. "Three redaction implementations" was a miscount — it is two**, and the proposed fix was
itself the conflation this plan forbids. `src/gflow_cli/redaction.py` is already a 14-line
re-export shim over `data/redaction.py`, which **already owns** `SENSITIVE_URL_KEYS` /
`SENSITIVE_QUERY_KEYS` (`data/redaction.py:11-12`) — extending the shim would add a third layer.
And `diagnostics.sanitize_url` (`diagnostics.py:209-225`) is not a redactor: it returns
`SanitizedUrl(host_category, route)` via `CommandHasher` for the observability schema. Making it
delegate would drag `CommandHasher` into a module 14 `src/` files import.
→ **`diagnostics` is not touched at all.** The HAR header constants land beside their siblings.

**3. The real duplication is entry boilerplate, and it is bigger.** 70 scripts run their own
`sys.path.insert` while importing `_spike_common`, which already bootstraps `src/` at
`_spike_common.py:33-37` — **50 are provably redundant** (~460 lines). Separately **726 lines**
of `ArgumentParser` across 87 scripts, with `--profile` spelled 65× in **10 distinct ways**.
→ **New Task 2:** one `spike_parser()` helper. **~600 lines deleted, zero new modules.**

**4. Probes are not re-run, so committing them buys little.** **52 of 67 have exactly one commit
ever**; 65 of 67 have a first→last span under 14 days. Only **14 findings exist for 67 probes
(21%)**, and **29 scripts hardcode an account name** (`ffroliva` ×15 as an argparse default,
`denon82` ×7, `ci-probe` ×9), so a stranger cannot re-run them anyway.
→ **The retention rule inverts** (Task 6), and `skills/spike/SKILL.md`'s claim that "a question
worth asking once gets asked again" is **false as written** and gets corrected.

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| **HIGH** | Redaction regression leaks a Bearer token into an issue attachment | Task 3 lands before any capture code; corpus tests; constants live with their siblings, not in a new layer |
| **HIGH** | A 59.5 MB HAR (measured) is attached to an issue, full of live bodies | body-free default; the CDP-shaped route (95 KB for the same traffic) is the contributor path |
| MED | Deleting probes loses something still wanted | Delete only single-commit probes whose logic is in `spike/` or whose finding exists; git keeps history either way |
| MED | `spike` grows into a product surface by accident | No Click command, no MCP tool, until #707 decides deliberately |
| LOW | A ported script behaves differently from its `.ps1` twin | Delete each `.ps1` only after its twin is exercised against live Chrome once |

---

## File structure

### New files

```
src/gflow_cli/spike/__init__.py   attach() / capture() — the two verbs
src/gflow_cli/spike/cdp.py        launch/attach real Chrome over CDP; the LIG constant
src/gflow_cli/spike/har.py        capture -> HAR (body-free default), shape CDP events
tests/spike/test_cdp.py           attach/launch contract, no real browser
tests/spike/test_har.py           CDP-event -> HAR shaping; size discipline
tests/spike/test_har_redaction.py header/URL redaction over a real-shaped corpus
```

### Modified files

```
src/gflow_cli/data/redaction.py   gains SENSITIVE_HEADERS / SENSITIVE_KEY_PARTS beside the
                                  SENSITIVE_URL_KEYS it already owns
scripts/dev/_spike_common.py      gains spike_parser(); ~600 lines of boilerplate deleted
                                  across the probe scripts
scripts/dev/har-spike/            shrinks per task; DELETED at Task 5
skills/spike/SKILL.md             the false "gets asked again" claim corrected; new rule
CONTRIBUTING.md                   Windows-only caveat dropped once Task 4 lands
.gitignore                        probe scratch gitignored by default
src/gflow_cli/diagnostics.py      NOT TOUCHED — recorded because an earlier draft did
```

---

## Task 1 — Test scaffold (red)

**Files:** `tests/spike/test_cdp.py`, `tests/spike/test_har.py`

**Steps:**
- [ ] `tests/spike/` with a fake-CDP-session fixture
- [ ] Assert `har.from_cdp_events()` emits exactly the `log.entries[].request/response` shape
      `summarize_har` reads
- [ ] Assert the default is **body-free**: a 5 MB response body does not appear in the HAR
- [ ] Assert `cdp.attach()` raises a typed error, not a raw Playwright exception

**Tests:**
- [ ] `uv run python -m pytest tests/spike -q` — red, for the stated reason

---

## Task 2 — `spike_parser()`: delete ~600 lines of boilerplate

**What:** The highest line-count win in the plan, and it adds no module.

**Files:** `scripts/dev/_spike_common.py`, probe scripts

**Steps:**
- [ ] `spike_parser(*, project=False, out=False)` returning a configured `ArgumentParser` — one
      spelling of `--profile` / `--project` / `--out` instead of ten
- [ ] **No account defaults.** `--profile` is required, killing the 29 hardcoded
      `ffroliva`/`denon82`/`ci-probe` values that make probes unrunnable for anyone else
- [ ] Delete the 50 redundant `sys.path.insert` preambles (`_spike_common.py:33-37` already
      bootstraps `src/`)
- [ ] Convert probes opportunistically — no big-bang rewrite

**Tests:**
- [ ] `tests/scripts/test_spike_parser.py` — `--profile` required, no account default
- [ ] A converted probe still answers `--help`

---

## Task 3 — Redaction constants, in the module that already owns them

**What:** The safety-critical task. **Not** a new layer.

**Files:** `src/gflow_cli/data/redaction.py`, `tests/spike/test_har_redaction.py`,
`scripts/dev/har-spike/extract_har_summary.py` (imports them)

**Steps:**
- [ ] `SENSITIVE_HEADERS` + `SENSITIVE_KEY_PARTS` (`extract_har_summary.py:11,20`) move beside
      `SENSITIVE_URL_KEYS` / `SENSITIVE_QUERY_KEYS` (`data/redaction.py:11-12`)
- [ ] `extract_har_summary.py` imports them — behaviour unchanged, one source of truth
- [ ] **`diagnostics.sanitize_url` is not touched** — it is route canonicalisation, not redaction

**Tests:**
- [ ] Corpus: `authorization`, `cookie`, `set-cookie`, `x-goog-api-key`, `sapisid`, and query
      params containing `key`/`token`/`sid` — each redacted, presence-vs-absence preserved
- [ ] A clean URL round-trips unchanged (no over-redaction)
- [ ] `tests/scripts/test_extract_har_summary.py` passes **untouched** — proves behaviour is preserved

---

## Task 4 — `spike/cdp.py` + `spike/har.py`, and the first `.ps1` dies

**Files:** `src/gflow_cli/spike/{__init__,cdp,har}.py`, delete
`scripts/dev/har-spike/launch-flow-chrome.ps1`

**Steps:**
- [ ] `launch_with_cdp(profile, port)` — `channel="chrome"`, no path hunting
- [ ] `attach(port)` — `connect_over_cdp`, typed error
- [ ] `from_cdp_events()` and `record_context_har(..., bodies=False)`; the docstring names the
      59.5 MB vs 95 KB measurement
- [ ] Every capture passes through `data.redaction` before it is written
- [ ] `LIG` lives here as a constant — its only real duplication (6 occurrences)
- [ ] Exercise against live Chrome once, paste the result in the PR, delete the `.ps1` in the
      same commit

**Tests:**
- [ ] Task 1 goes green
- [ ] A shaped HAR still feeds `summarize_har`

---

## Task 5 — Retire `har-spike/`

**Steps:**
- [ ] Delete `probe-agent-mode.ps1`, `start-background-capture.ps1`,
      `stop-background-capture.ps1` — **0 references repo-wide**, and the first already has a
      live Python twin
- [ ] Port or drop each remaining `.ps1`; drop anything with no caller
- [ ] Delete `character_create_spike.py` (344 lines, superseded by `_v2`; live references are
      comments only, `api/client.py:297`)
- [ ] `.gitignore`: drop the `har-spike/` rules

**Tests:**
- [ ] `check_doc_links.py`, `check_repo_hygiene.py` green (14 doc references point here)

---

## Task 6 — Invert the retention rule; stop the accumulation

**What:** The behavioural change that stops this recurring.

**Files:** `skills/spike/SKILL.md`, `.gitignore`, `CONTRIBUTING.md`, `AGENTS.md`

**Steps:**
- [ ] Correct the false claim. The skill currently says *"The spike script itself → committed. A
      question worth asking once gets asked again."* **52 of 67 probes have exactly one commit
      ever.** Replace with: **compose `gflow_cli.spike`; commit the finding, not the probe.**
- [ ] New rule: a probe is committed **only if** it takes arguments and hardcodes no account.
      Otherwise it is scratch.
- [ ] Gitignore probe scratch by default
- [ ] Delete single-commit probes whose logic now lives in `spike/` or whose finding exists;
      **write the finding first** where it does not. (An earlier draft assumed closed-issue
      probes could be pruned on sight — checked, and none of the 14 findings covers issues
      170/313/314/404, so that prune was a no-op.)
- [ ] Drop the Windows-only caveat from CONTRIBUTING
- [ ] `check_repo_hygiene.py` gains a check for hardcoded account names under `scripts/`

**Tests:**
- [ ] Doc gates + documentation-gate tests green
- [ ] The new hygiene check fails on a planted `default="ffroliva"`

---

## Task 7 — Full gates and CHANGELOG

**Steps:**
- [ ] `/gflow:check` fully green
- [ ] CHANGELOG entry under `[Unreleased]`
- [ ] Confirm no Click command and no MCP tool was added — that is #707's call

**Tests:**
- [ ] `uv run python -m pytest -q --cov=gflow_cli`, coverage floor held

---

## Explicitly out of scope

- **Any user-facing command** — [#707](https://github.com/ffroliva/gflow-cli/issues/707), which
  gets `/gflow:predict` first. This plan ships no user-facing surface, which is why it needs no
  MCP mirror task.
- **`python -m gflow_cli.spike` as a probe CLI** — a reviewer ranked this second and it is
  attractive (~30 lines, would remove most future probe files). Deliberately deferred: it is
  only worth designing once `cdp.py` / `har.py` exist and their real call shapes are known.
  Revisit after Task 4.
- **`diagnostics.py`** — see the council notes above.
