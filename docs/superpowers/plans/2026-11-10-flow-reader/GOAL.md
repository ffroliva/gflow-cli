# Goal: page-free reads wave (FlowReader)

**Milestone:** Wire resilience (#1229) · **Evidence base:** #1192 (PR #1205), #1217 (PR #1219),
#1221 (PR #1231), #1225 (PR #1232), #1218 (PR #1230) · **Bug in scope:** #1233

## Outcome

gflow reads a Flow balance, a clip's status and result, and a project's listing **without
starting Chrome** whenever the profile's cookies are accepted, and falls back to today's page
path whenever they are not, so no read command ever fails because the direct path failed.
Submits, uploads and anything that needs reCAPTCHA stay on the browser.

## Shape (decided, subject to predict)

- A new narrow port `FlowReader` (`credits()`, `listing(project_id)`, `record(workflow_id)`),
  separate from `FlowTransportStrategy`, which stays the generation port.
- Two adapters: `PageReader` (today's code moved behind the port, no behaviour change) and
  `WireReader` (httpx + profile cookies + `at` from a cookie GET of the shell, reusing
  `batchexecute.py` parsers and `CreditsInfo.from_nzlxg`).
- A fallback composer: wire first, page on any classified wire failure, with the reason logged.

## Exit criteria (each is measured, not asserted)

| # | Criterion | How it is proven |
|---|---|---|
| G1 | `gflow credits user` on `denon82`, no Chrome open: p50 ≤ 3 s over 5 runs (baseline ~40 s) | timed live run, pasted in the PR |
| G2 | credits, clip status/result and listing launch **zero** Chrome processes when the wire is healthy | e2e asserts the page factory was never called |
| G3 | every wire failure class falls back and the command still succeeds: signed-out shell (no `SNlM0e`), 401, 400, unparseable reply, cookie store unreadable/locked, network error | one offline test per class + one e2e that forces the fallback (cookie-free arm) |
| G4 | browser close no longer waits out its 30 s timeout (#1233) | Bug Lane: failing test first, then timed live run |
| G5 | a wire read sends no more requests than the page read it replaces (≤ 1 GET + 1 POST per read, `at` reused within a process, no retry on 4xx, poll interval ≥ the app's own 5 s) | request count asserted in the e2e |
| G6 | `gflow doctor` reports wire health (cookies accepted / signed-out shell / build label) and the CLI and MCP surfaces agree | doctor e2e + parity tests |
| G7 | an operator kill switch turns the wire path off | offline test |
| G8 | docs: ARCHITECTURE.md read-port section, KNOWN_ISSUES account-risk and fallback note, FLOW_WIRE_RPCS adapter column, `.env.template` | doc gates green |

## Process (every step is a gate)

`/gflow:predict` → `/gflow:scenario` → `/gflow:plan` (this directory) → worktree per ticket from
`develop` → TDD → `/gflow:check` (ten gates) → two fresh-agent xhigh reviews (`/code-review`,
`ponytail-review`) + one fix round → `/gflow:sonar` → `pytest -m e2e` on `denon82` → PR left
open. A predict STOP halts the wave and reports why.

## Out of scope

Caching `at` across processes (waits for S2 #1222), project create/rename over the wire (S7
#1227), any submit, any reCAPTCHA emulation, any new traffic pattern the app does not send.

## Known facts the plan must honour

- `get_chrome_cookie_snapshot` filters to `labs.google` cookies (`auth/cookies.py:26`); the
  wire needs the Google-domain cookies, so the read is new, not reused as-is.
- Its fallback for an unreadable store launches Playwright on the profile. The wire path must
  not use that fallback (it would start Chrome to avoid starting Chrome): unreadable store →
  page path, under the profile lease.
