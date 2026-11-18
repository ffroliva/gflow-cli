# Telemetry: browser engine and served Flow host Implementation Plan

Status: **IN PROGRESS**
Tracking issue: **#1326**
Reference: [SCENARIO.md](SCENARIO.md)
Methodology: test first, smallest change, one decision in one place.

## Goal

The weekly report counts which browser engine ran and which Flow host was served, so a
defect limited to one engine (#1319, #1324) or one host can be sized. Both values are
closed enums counted once per browser-driven command or queued job, and the privacy notice
names them.

## Design

| Decision | Choice | Reason |
|---|---|---|
| Shape | Flat counter maps `engines` and `hosts`, like `models` | A user can switch engine, or be served another host, within a week |
| Values | `engines`: `playwright`, `patchright`, `other`; `hosts`: `labs`, `flow`, `unknown` | Closed enums; a raw hostname is never sent |
| Engine source | `log_engine_selected()` in `api/_engine.py`, the one point where a browser launches | The setting alone would count commands that never launch a browser |
| Host source | `flow_host_kind(page.url)` at the transport's routing points, stored, not counted | Routing can read the URL twice on a retry; storing makes the count per command |
| Flush | `telemetry.record()` (CLI command end, direct MCP tool return) and the worker after each job | One write per command or job, in the process that drove the browser |
| Endpoint | Two `SHAPES` entries with new closed key kinds `engine` and `host`; `schema` stays 1 | Additive and optional, so every existing client stays valid |
| Rollout | The endpoint deploys before any client sends the keys | The current endpoint answers 400 to unknown keys, and the client records a 400 as sent |
| Notice | `NOTICE_VERSION` 2 to 3; the consent file records the notice version | The notice must name what is sent; EU/EEA users consent to the new version |
| Crosses | None with country or between the two fields; `x:host_exit` deferred | Re-identification risk; see SCENARIO.md |

## File structure

### Modified files

- `gflow-telemetry-endpoint/src/index.ts`, `test/index.test.ts`: key kinds, shapes, tests.
- `src/gflow_cli/telemetry.py`: run facts (`note_engine`, `note_host`), flush in `record()`,
  report maps, notice v3 text, versioned consent.
- `src/gflow_cli/api/_engine.py`: note the engine at launch.
- `src/gflow_cli/api/transports/ui_automation_video.py`, `_ui_image.py`,
  `src/gflow_cli/api/_flow_media_edit.py`: note the served host where the route is read.
- `src/gflow_cli/worker/daemon.py`: flush after each job.
- `src/gflow_cli/telemetry_upload.py`: log a non-2xx answer at debug.
- `docs/TELEMETRY.md` (+ website mirror), `CHANGELOG.md`.
- Tests: `tests/test_telemetry*.py`, `tests/test_cli_telemetry.py`, `tests/e2e/test_telemetry_*_e2e.py`.

## Task 1 — Endpoint accepts the two maps (test first)

- [ ] Failing vitest cases: S2 (old report 204), S3 (`chromium`, a hostname: 400), new report stores `engines`/`hosts` totals.
- [ ] Add key kinds `engine` and `host` with closed sets; add `engines: ["engine"]`, `hosts: ["host"]` to `SHAPES`.
- [ ] `npm test`, `npm run typecheck`; deploy; live check that an old-shape report still returns 204.

## Task 2 — Client run facts and report maps (test first)

- [ ] Failing tests: S4, S5, S6, S9, S11 against a temporary `GFLOW_CLI_HOME`.
- [ ] `note_engine()` / `note_host()` store the latest value; `record()` writes one `engine` and one `host` counter row when an engine was noted, then clears both.
- [ ] `_add_scalar_counter` maps the kinds into `engines` and `hosts`; `build_report` sends them.

## Task 3 — Call sites (test first)

- [ ] `log_engine_selected` notes the engine; the routing points note `flow_host_kind(page.url)`.
- [ ] The worker flushes after each job (S7); the direct MCP path flushes through `record()` (S8).

## Task 4 — Notice v3 and versioned consent (test first)

- [ ] Failing tests: S10 (a v2 consent file disables uploads with the consent reason).
- [ ] `NOTICE_VERSION = 3`; both notice texts name the engine and the host Flow served; `set_consent` writes the version.

## Task 5 — Upload visibility and docs

- [ ] A non-2xx upload answer logs at debug (S1 visibility).
- [ ] `docs/TELEMETRY.md` field table and notice text; website mirror; CHANGELOG.

## Definition of done

- [ ] `/gflow:check` green; endpoint `npm test` green.
- [ ] `pytest -m e2e_telemetry` passes; a live upload from a new client returns 204 and D1 shows `engines` and `hosts` rows for the week.
- [ ] Two xhigh reviews addressed; PRs open in both repositories.

## Decision log

- 2026-11-18: counter maps chosen over report-level scalars (users switch within a week).
- 2026-11-18: `schema` kept at 1; a bump would make the current endpoint reject every report.
- 2026-11-18: `x:host_exit` deferred until report volume supports a per-host rate.
