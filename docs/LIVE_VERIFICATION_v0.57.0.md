# Live verification — v0.57.0

> Hand-run against real Flow on Windows 11, profile `ffroliva` (chrome
> strategy), 2026-08-14. Every check in this release is **credit-free** — no Veo
> generation was submitted at any point. The #299 UI-mode evidence was captured
> on the feature branches at merge time (probes
> `tmp/live_video_bind_check.py`, `tmp/live_agent_mode_check.py`, untracked);
> the MCP surfaces were re-run on the release tree over the real JSON-RPC wire.

## Environment

| | |
|---|---|
| Branch | `chore/release-v0.57.0` (off `develop` @ `606dd7b`) |
| Local version | `0.57.0` editable (post-bump — confirmed by `cli_version: "0.57.0"` in every structlog line below) |
| Profile | `ffroliva` (chrome strategy) |
| Date | 2026-08-14 |
| OS | Windows 11 |
| Credits spent | **0** (no generation call on any path) |

## Pre-tag gates

- `/gflow:check` on `606dd7b`: hygiene (750 files), doc links (27), website PII
  (23), website mirror in sync (18), `ruff check`, `ruff format --check` (364),
  `uv lock --check` — all green; pyright at the accepted baseline (78, confined
  to `mcp/*` + `ui/app.py`).
- Full offline suite: **3095 passed / 22 skipped / 69 deselected** in 4m29s.
- Per-PR review gates already banked: `/code-review xhigh` on #525 (15 findings
  → 6 fixed, 8 deferred with triggers in the in-flight plan), council **GREEN**
  on #527.
- `/gflow:doc-review`: mechanical pass (§1–7) all PASS. Council verdict
  **YELLOW / YELLOW / YELLOW** across the 3 auditors — **zero Tier-1
  (release-blocking) findings**; all three independently confirmed that every
  v0.57.0-specific claim (exit codes 2/23/25/28, the 11→9 tool count, the 16 KB
  section cap, the 15 s reload bound, version agreement) checks out against the
  source. 9 Tier-2 fixes applied in the release-prep commit: `ui_mode` +
  `project_name`/`output`/`wait` added to both `docs/MCP.md` generate
  signatures (the docs had drifted 4 params behind
  `mcp/tools.py`); a no-spend MCP-client config block (Option A2); a
  `GFLOW_MCP_NO_SPEND` section in `docs/CONFIGURATION.md` + two `docs/INDEX.md`
  routing rows (the repo's own rule at `INDEX.md:151` requires both); `--no-spend`
  on the `gflow mcp run` reference in `docs/USAGE.md`; `--ui-mode` and the exit-2/28
  behavior in `llms.txt`; the Security/Scorecard theme in `docs/PROJECT_STATUS.md`;
  the pin example bumped to `==0.57.0`; and **a same-release factual error in this
  changelog** — the Security entry described the triage image as pinning
  `node:20-slim` when #522 had already moved it to `node:26-slim` (the stale
  `Dockerfile.triage` line-1 comment was corrected too). Tier-3 deferred to a
  follow-up issue (pre-existing, none introduced by this release): the
  `--reference-entity`-on-video fiction (three docs plus the `t2v` help text claim
  a flag that has shipped since v0.52.0 — and needs a maintainer decision on the
  I2V contract, which `api/video.py:339` rejects), the Windows profile-path
  author segment (`%LOCALAPPDATA%\ffroliva\gflow-cli`) wrong in 6 docs + 2
  docstrings, the `ARCHITECTURE.md` testing-topology table naming three
  nonexistent test dirs, its missing driver/mode-control layer, module-inventory
  drift, and the mirror's half-applied anonymization table. Council reports at
  `tmp/council/0{1,2,3}-*.md` (local-only).

## Matrix

| # | Feature | Variation | Result |
|---|---|---|---|
| 1 | #299 PR-A — video binds through the UI-mode policy | 3× real video-editor load, `--ui-mode auto` | ✅ 3/3 bound classic |
| 2 | #299 PR-A — explicit `--ui-mode agentic` rejected | `video t2v`, release tree | ✅ exit 2, pre-browser |
| 3 | #299 PR-B — `ensure_agent_mode` symmetry | real editor, classic→agent→classic | ✅ round-trip verified |
| 4 | #496 — `--no-spend` tool gating | `mcp run` vs `mcp run --no-spend` | ✅ 11 tools → 9, both generate tools absent |
| 5 | #497 — `gflow_auth_status` | real Flow session, both modes | ✅ `authenticated` |
| 6 | #498 — `gflow_list_projects` pagination | offset 0 / 2 / clamp | ✅ distinct pages, clamps hold |
| 7 | #501 — bounded known-issues resource | index + unknown slug | ✅ 7.5 KB index, 111 B unknown-slug reply |
| 8 | #498 — rate-limited problem-details envelope | — | ⚠️ not live-triggerable credit-free (see below) |

## 1–2. #299 PR-A — video joins the UI-mode policy

**Driver bind (3/3).** Three separate real video-editor loads on `ffroliva`
each bound the classic driver through `get_ui_driver` after editor mount and
overlay dismissal, with **no submission** (probe aborts before the generate
click, so the run is credit-free):

| Layer | Evidence |
|---|---|
| Wire path | `ui_driver.bound mode=classic` on all 3 loads |
| Project | `e00291af…` (real Flow project, reused across loads) |
| Timing | 1.2 – 1.7 s from mount to bind — no 30–40 s doomed-selector stall |
| Structlog | bind event carries the resolved mode, not a hardcoded constant |
| Cost | 0 credits — aborted pre-submit |

**Explicit-agentic rejection**, re-run on the release tree:

```
$ gflow video t2v "release probe, never submitted" --ui-mode agentic
Error: --ui-mode agentic is not supported for video generation yet
       (no agentic video driver exists; refs #299). Use classic or auto.
$ echo $?
2
```

Exit **2** (Click usage error), raised before any browser launch or auth check —
deliberately *not* exit 28, whose "retry may land it" remediation would mislead
for a driver that does not exist. `--ui-mode [auto|classic|agentic]` is present
in `video t2v --help` with the classic-only note.

## 3. #299 PR-B — `ensure_agent_mode`

Real-editor round-trip on `ffroliva`, classic → agent → classic, verified by
`aria-pressed` rather than the `tune` ligature (the documented false-positive
source the deleted `_force_agent_mode` relied on):

```json
{"agent_acted": true, "after_agent": "agent", "after_media": "media"}
```

The switch was driven by a real click that fired the React handler (the
server-side preference persisted), not a forced DOM flip. Credit-free — no
generation followed the switch.

## 4. #496 — `gflow mcp run --no-spend`

Both modes driven over the **real stdio JSON-RPC wire** (a subprocess
`gflow mcp run`, the same transport Claude Desktop / Cursor speak), so this
covers registration *and* stream routing, not just an in-process registry read.

| `tools/list` | Default | `--no-spend` |
|---|---|---|
| Tool count | 11 | **9** |
| `gflow_generate_image` | present | **absent** |
| `gflow_generate_video` | present | **absent** |
| `gflow_auth_status` | present | present |
| `gflow_list_projects`, `gflow_list_tools`, 6× `gflow_instructions_*` | present | present |

The tools are genuinely *unregistered*, not refused at call time — a connected
agent cannot see them at all. Structlog: `mcp.no_spend_active`.

## 5. #497 — `gflow_auth_status`

Called with **zero arguments** over the wire, in both server modes:

```json
{"status": "authenticated", "profile": "ffroliva", "user_email": "ffroliva@gmail.com"}
```

| Layer | Evidence |
|---|---|
| Real network | `GET https://labs.google/fx/api/auth/session "HTTP/1.1 200 OK"` |
| Structlog | `mcp.tool.auth_status` with `profile=ffroliva`, `cli_version=0.57.0` |
| Fail-closed | same `verify_flow_profile` path as `gflow auth status` |
| Cost | 0 credits, non-interactive, no generation |

The failure envelope (expired session → `…/errors/auth-expired`, network fault →
`…/errors/verification-error` 503) is covered by
`tests/mcp/test_auth_status_tool.py`; it was not force-triggered live because
invalidating the working session mid-release would have cost the other checks.

## 6. #498 — honest pagination on `gflow_list_projects`

Real SQLite catalog, three calls over the wire:

| Call | `count` | `offset` | `has_more` | `next_offset` | project IDs returned |
|---|---|---|---|---|---|
| `limit=2, offset=0` | 2 | 0 | true | 2 | `8f36acba…`, `9a2a969f…` |
| `limit=2, offset=2` | 2 | 2 | true | 4 | `39897860…`, `6508c87b…` |
| `limit=0, offset=-5` | 1 | 0 | true | 1 | `8f36acba…` |

Page 2 returns **different projects** than page 1 — the offset is genuinely
honored, so catalogs larger than `limit` are now reachable through MCP (the old
hardcoded first page made them unreachable). The clamp holds: `limit=0` becomes
1 (no infinite `next_offset` loop) and `offset=-5` becomes 0 (no negative
`LIMIT -1` reaching SQLite). The removed `total` field — which used to report
the page size as the catalog total — is absent, as intended.

## 7. #501 — bounded `gflow://docs/known-issues`

| Layer | Evidence |
|---|---|
| Default read | **7,543 bytes** — an index of titles + status + slugs, against a ~70 KB KNOWN_ISSUES.md |
| Shape | `# Known issues — index` + `Full text of one issue: read gflow://docs/known-issues/<slug>` |
| Templated read | `gflow://docs/known-issues/{slug}` serves one issue, capped at 16 KB |
| Unknown slug | **111 bytes** — echoed slug is capped, so the last unbounded reflection path is closed |
| Static resources | `mcp-guide`, `known-issues`, `db/schema` — no unbounded read path remains |

## 8. #498 — rate-limited problem-details envelope (not live-verified, with reason)

The RFC 9457 envelope now returned by both generate tools when the shared token
bucket rejects a call (capacity 8, refill 1 per 20 s) **cannot be triggered
credit-free**: exhausting the bucket requires ≥9 accepted generate calls first,
and every accepted call enqueues a real Veo generation. Verifying it live would
cost credits for a pure error-path check.

Covered instead by `tests/mcp/test_rate_limit_envelope.py`, which asserts the
envelope is built from the canonical `RateLimitError` (type
`…/errors/rate-limit`, `retryable` and `message` present) for **both** tools —
the fix being that the image tool previously returned a plain error string and
the video tool's detail claimed a nonexistent "1 request per 30 seconds" policy.
Recorded here per the never-silently-omit rule.

## Post-tag evidence

To be appended after the tag push (release workflow result, PyPI publish, and
the #479 update notice firing organically for the first time — v0.57.0 is the
first release published *after* the notice shipped in 0.56.0).
