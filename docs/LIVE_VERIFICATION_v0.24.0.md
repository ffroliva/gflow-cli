# Live Verification — v0.24.0

Release date: 2026-07-01. This release completes `--project` parity: `video
t2v/i2v/r2v` gain `--project` (#233/#234) and the MCP `gflow_generate_image` /
`gflow_generate_video` tools gain a `project` parameter (#235) — both let a caller
target an existing Flow project instead of a scratch one.

## Scope

| Change | Surface | Verification |
|---|---|---|
| **#234** — `--project` on `video t2v/i2v/r2v` (CLI) | `cli_video.py`, `_cli_helpers.py` | ✅ Automated — behavior tests assert the id threads to `generate_video(project_id=...)` for all three modes; default (no flag) → scratch project; bad id rejected at the CLI boundary. Full council review GREEN + Sonar 100% new-coverage. |
| **#235** — `project` param on MCP generate tools | `mcp/tools.py` | ✅ Automated — payload-threading tests assert `project` reaches `payload["project_id"]` (image + video), omitted → no `project_id`, bad id rejected before the worker. Reuses the CLI `_FLOW_ID_RE` validator. Council GREEN + Sonar pass. |

## Nature of the change (why automated coverage is sufficient here)

Neither change adds new generation plumbing: the worker/daemon already consumed
`payload["project_id"]` (daemon.py:97 image, :168 video) and `FlowApiClient.generate_video`
already accepted `project_id`. Both PRs only **surface + validate** the existing
capability at the CLI and MCP boundaries, and thread it into the already-wired payload.
The end-to-end "generate into an existing project" path (browser navigation to
`project_editor_url(locale, project_id)` → `entering_existing_project`) is the same code
prior releases exercised live for `image t2i --project`.

## Live generation status

A live credit-free run of `--project` against real Flow was **not** performed this cycle
because the available `denon82` profile's Flow session is server-side expired (HTTP 401 —
see [LIVE_VERIFICATION_v0.23.0](LIVE_VERIFICATION_v0.23.0.md)); exercising it requires an
interactive `gflow auth login`. The project-targeting code path is shared with the
already-live-verified `image t2i --project` path, and is fully covered by the automated
behavior tests above.

## Automated coverage

- Full suite green on both PRs' CI (test matrix 3.11/3.12/3.13) at the merged tree.
- `pyright src`: 0 errors. `ruff check` / `ruff format --check`: clean.
- SonarCloud gate green on both PRs (#234 new-coverage 100%; #235 pass).
