# Spike Tooling Reorg Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature spike-tooling-reorg` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** A spike tool a contributor on any OS can run to produce redacted evidence for a bug
report — backed by a tested library instead of 88 untested scripts and a Node dependency.

**Architecture:** Live capture goes into a new `src/gflow_cli/spike/` package (CDP attach, HAR
capture, DOM inventory). **`spike` because that is already the word here** — `/gflow:spike`,
`skills/spike/SKILL.md`, 48 `scripts/dev/spike_*.py` since July, and `docs/superpowers/spikes/`
for the findings. One word across command, protocol, scripts, findings and module beats a
taxonomically neater name that matches nothing else; a contributor grepping for "spike" should
land on all of it.

Named `spike/`, **not** `_spike/`. The underscore would claim it is private, and it is not:
[#707](https://github.com/ffroliva/gflow-cli/issues/707) builds a user-facing capture path on
exactly this code.

**`diagnostics.py` is deliberately left alone.** An earlier draft folded this into it, and that
was conflation rather than consolidation: `diagnostics` classifies and summarises what already
went wrong, post-hoc, for an incident bundle; a spike actively drives a browser to observe a
live surface. Different activity, different lifecycle. Promoting a shipped 1890-line module
with 4 importers to a package would have been risk taken for no benefit.

**What genuinely needed consolidating was redaction, and it still does.** There are three
implementations today — `redaction.py` (14 lines), `diagnostics.sanitize_url`, and
`extract_har_summary`'s header/URL redaction. Task 2 merges them into `redaction.py`, which both
`diagnostics` and `spike` then share. That was always the real drift risk; the module name never
was.

One-shot probes move to `scripts/dev/probes/` and gain a retention rule. The
Node/`agent-browser`/PowerShell harness is deleted script by script as each Python twin lands.

**Predict verdict:** pending — this is dev tooling, not a transport/auth/selector/schema
change, so the AGENTS.md routing table does not require `/gflow:predict`. Run it before
[#707](https://github.com/ffroliva/gflow-cli/issues/707), which does.

**Portability proven before planning** (2026-09-06,
`scripts/dev/spike_cdp_har_without_node.py`): Playwright can attach to a CDP Chrome running a
real gflow profile and see the human's page (`contexts=1, pages=1, sees_human_page=True`);
`record_har_path` works on a CDP-attached browser; and raw CDP `Network.*` events shape into a
HAR that the harness's own `extract_har_summary.summarize_har` consumes **unchanged**. The one
piece that could have killed the port is retired.

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| **HIGH** | Redaction regression leaks a Bearer token or cookie into an issue attachment | Consolidate to ONE implementation; property-style tests over a corpus of real header/URL shapes; Task 2 lands before anything that produces a shareable artefact |
| **HIGH** | A 59.5 MB HAR (measured) is attached to an issue, full of live response bodies | `record_har_content="omit"` is the default; the CDP-shaped path (95 KB for the same traffic) is what the contributor-facing entry point uses |
| MED | Moving 67 probes breaks a doc link or a `tests/scripts/` import | `check_doc_links.py` is a merge gate; move with `git mv`, run gates per task |
| MED | `spike` and `diagnostics` drift into two redaction implementations again | Task 2 makes `redaction.py` the single home BEFORE either uses it; no capture code lands first |
| MED | `spike` grows into a product surface by accident | No Click command, no MCP tool, until #707 decides deliberately |
| LOW | Ported script behaves differently from its `.ps1` twin | Delete each `.ps1` only after its twin is exercised against live Chrome once |

---

## File structure

### New files

```
src/gflow_cli/spike/cdp.py            launch/attach a real Chrome over CDP; profile resolution
src/gflow_cli/spike/har.py            capture -> HAR (body-free by default), shape CDP events
src/gflow_cli/spike/dom.py            ligature / ARIA role / custom-element inventory
tests/spike/test_cdp.py         attach/launch contract, no real browser
tests/spike/test_har.py         CDP-event -> HAR shaping; size discipline
tests/spike/test_har_redaction.py  header/URL/body redaction over a real-shaped corpus
scripts/dev/probes/README.md          what a probe is, and the retention rule
```

### Modified files

```
src/gflow_cli/diagnostics.py          sanitize_url delegates to redaction.py; otherwise
                                      UNCHANGED — its 4 importers are not touched
src/gflow_cli/redaction.py            EXTENDED to own header/URL/HAR-entry redaction
                                      (absorbs extract_har_summary's second implementation;
                                      diagnostics.sanitize_url delegates to it)
scripts/dev/har-spike/                shrinks per task; DELETED at Task 7
skills/spike/SKILL.md                 retention rule + new paths
CONTRIBUTING.md                       drop the Windows-only caveat once Task 6 lands
AGENTS.md                             two-modes table points at the new paths
.gitignore                            probes/ output + har-spike rules removed at Task 7
```

---

## Task 1 — Test scaffold for the spike library (red)

**What:** Red tests defining the contract before any code exists.

**Files:** `tests/spike/test_cdp.py`, `tests/spike/test_har.py`

**Steps:**
- [ ] `tests/spike/` package with a `conftest.py` fixture for a fake CDP session
- [ ] Assert `har.from_cdp_events()` emits `log.entries[].request/response` (the shape
      `summarize_har` reads) and nothing more
- [ ] Assert the capture default is **body-free** — a fixture with a 5 MB response body
      produces a HAR that does not contain it
- [ ] Assert `cdp.attach()` surfaces a typed error, not a raw Playwright exception, when no
      CDP endpoint answers

**Tests:**
- [ ] `uv run python -m pytest tests/diagnostics -q` — red, and red for the stated reason

---

## Task 2 — Consolidate redaction (the safety-critical task)

**What:** One redaction implementation. `src/gflow_cli/redaction.py` absorbs the header, URL
and HAR-entry redaction currently living in `scripts/dev/har-spike/extract_har_summary.py`.

**Files:** `src/gflow_cli/redaction.py`, `tests/spike/test_har_redaction.py`,
`scripts/dev/har-spike/extract_har_summary.py` (delegates)

**Steps:**
- [ ] Move `SENSITIVE_HEADERS`, `SENSITIVE_KEY_PARTS`, `_safe_url`, `_redact_header` into
      `redaction.py` as public functions
- [ ] `extract_har_summary.py` imports them — behaviour unchanged, one source of truth
- [ ] Keep `redact_sensitive_text` as-is; its one existing caller must not change

**Tests:**
- [ ] Corpus test: `authorization`, `cookie`, `set-cookie`, `x-goog-api-key`, `sapisid`,
      query params containing `key`/`token`/`sid` — each redacted, presence-vs-absence
      preserved (`<redacted:present>` vs `<redacted:empty>`)
- [ ] A URL with no sensitive parts round-trips unchanged (no over-redaction)
- [ ] `tests/scripts/test_extract_har_summary.py` still passes untouched — proves the move
      is behaviour-preserving

---

## Task 3 — `spike/cdp.py`: attach and launch

**What:** The Chrome-discovery and CDP-attach machinery, cross-platform.

**Files:** `src/gflow_cli/spike/cdp.py      `, `src/gflow_cli/diagnostics/__init__.py`

**Steps:**
- [ ] `launch_with_cdp(profile, port)` — `channel="chrome"` (no path hunting) +
      `--remote-debugging-port`
- [ ] `attach(port)` — `connect_over_cdp`, typed error on failure
- [ ] Profile resolution delegates to the existing profile store, never `LOCALAPPDATA`
- [ ] Docstring records the proven facts and cites the PoC spike

**Tests:**
- [ ] Task 1's tests go green
- [ ] No real browser in unit tests; live exercise is Task 6

---

## Task 4 — `spike/har.py`: capture without Node

**What:** Both capture routes, with the size discipline baked in.

**Files:** `src/gflow_cli/spike/har.py      `

**Steps:**
- [ ] `from_cdp_events(events)` — shape `Network.requestWillBeSent` /
      `responseReceived` into the HAR subset (proven compatible with `summarize_har`)
- [ ] `record_context_har(context, path, *, bodies=False)` — native route, bodies **off** by
      default; the docstring names the measurement: 59.5 MB with bodies vs 95 KB without
- [ ] Every capture passes through `redaction` before it is written

**Tests:**
- [ ] Task 1's shaping and size tests go green
- [ ] A shaped HAR fed to `summarize_har` produces entries (the compatibility contract)

---

## Task 5 — `spike/dom.py`: inventory helpers

**What:** The DOM-reading JS that tonight's probes each re-implemented.

**Files:** `src/gflow_cli/spike/dom.py      `

**Steps:**
- [ ] `inventory(page)` — ligatures + carrier tag, ARIA roles, custom elements, `href`s
- [ ] `candidates(page, selectors)` — visibility **and occluder** (`elementFromPoint`), the
      check that proved the character editor was reachable
- [ ] Docstring carries the anchor rule: structure over labels; custom elements are the best
      anchors; the carrier differs per host (`i.google-symbols` vs `mat-icon`)

**Tests:**
- [ ] Pure-function tests over captured DOM fixtures (no browser)

---

## Task 6 — Port `launch-flow-chrome.ps1`, delete it

**What:** First `.ps1` retired. Establishes the pattern for the rest.

**Files:** `scripts/dev/probes/launch_flow_chrome.py`, delete
`scripts/dev/har-spike/launch-flow-chrome.ps1`

**Steps:**
- [ ] Thin CLI over `spike.cdp.launch_with_cdp`
- [ ] Exercise once against live Chrome; paste the result in the PR
- [ ] Delete the `.ps1` in the same commit — two implementations is the drift we are removing

**Tests:**
- [ ] Live exercise recorded in the PR (this is a Flow-adjacent surface; a run, not a claim)

---

## Task 7 — Retire the rest of `har-spike/`, move probes

**What:** Finish the port; give probes a home and a lifecycle.

**Files:** `scripts/dev/probes/` (67 moved), `scripts/dev/har-spike/` (deleted), `.gitignore`

**Steps:**
- [ ] Port or drop each remaining `.ps1`; drop rather than port anything with no caller
- [ ] `git mv` the 67 `spike_*` / `capture_*` scripts into `scripts/dev/probes/`
- [ ] Prune the 7 named after closed issues **only if** their finding exists in
      `docs/superpowers/spikes/`; otherwise write the finding first
- [ ] `scripts/dev/probes/README.md` — what a probe is, cost header, retention rule
- [ ] `.gitignore`: remove the `har-spike/` rules, keep `_spike_out/` and `*.har`

**Tests:**
- [ ] `check_doc_links.py`, `check_repo_hygiene.py` green (14 doc references point at these
      paths — this is where a move breaks)
- [ ] `tests/scripts/` still imports what it imports

---

## Task 8 — Docs, skill, and the retention rule

**What:** Make the new shape the documented one.

**Files:** `skills/spike/SKILL.md`, `CONTRIBUTING.md`, `AGENTS.md`

**Steps:**
- [ ] Retention rule into the skill: **a probe merges with its `docs/superpowers/spikes/`
      finding, or it does not merge.** Findings are permanent; probes are prunable once
      written up
- [ ] Drop the Windows-only caveat from CONTRIBUTING; the harness is Python now
- [ ] AGENTS.md two-modes table points at `spike/` and `probes/`
- [ ] Add a one-line cost + question header convention for probes

**Tests:**
- [ ] `check_doc_links.py`, documentation-gate tests green

---

## Task 9 — Full gates and CHANGELOG

**Steps:**
- [ ] `/gflow:check` fully green (all six gates, ruff, pyright strict on the new package,
      full pytest)
- [ ] CHANGELOG entry under `[Unreleased]`
- [ ] Confirm no Click command and no MCP tool was added — the user-facing decision belongs
      to #707. `spike` adds ~40 KB to the wheel and no user-visible surface

**Tests:**
- [ ] `uv run python -m pytest -q --cov=gflow_cli` — coverage floor held

---

## Explicitly out of scope

- **`gflow capture` / any user-facing command** — that is
  [#707](https://github.com/ffroliva/gflow-cli/issues/707), and it gets `/gflow:predict`
  first. This plan deliberately ships **no** new CLI surface, which is also why it needs no
  MCP mirror task: there is nothing user-facing to mirror.
- Rewriting existing probes to use `spike/` — they keep working as they are; convert on
  next touch.
- Changing what incident bundles contain — overlaps #707's first design question.
