# Live Verification — v0.51.0

**Date:** 2026-08-05
**Profile:** `denon82`
**Cost:** 1 Imagen credit (i2i) + 1 Veo attempt (i2v, failed server-side inside Flow)

## What this release contains, and what that means for verification

v0.51.0 is a **dependency, security and test-correctness** release. There is no
new user-facing generation feature, so the live gate here answers a different
question from a feature release: *does the product still drive Flow correctly
after 38 locked packages moved and the playwright driver was raised a minor?*

The one change that genuinely required live evidence — the `playwright` bound —
has its own full ledger:
**[`LIVE_VERIFICATION_playwright_1.61.md`](LIVE_VERIFICATION_playwright_1.61.md)**.
It is summarised below rather than duplicated.

---

## Layer 1 — Offline gates (on the release tree)

| gate | result |
|---|---|
| `pytest` (offline suite) | 2923 passed, 3 skipped |
| `ruff check` / `ruff format --check` | clean, 346 files |
| `pyright src` | 0 errors |
| `pip-audit` over `uv export --all-extras` | no known vulnerabilities |
| repo hygiene / doc links / website mirror | clean |
| CI | 14/14 green |

## Layer 2 — Zero-credit live gates

| gate | result |
|---|---|
| `-m e2e_auth` | **16 passed** — browser launch, persistent context, cookie state, session verification, transport health checks |
| `tests/e2e/test_daemon_e2e.py` | **1 passed** — MCP over Streamable HTTP against a live spawned daemon |
| `scripts/dev/live_verify_mcp_tasks.py` | **5/5 layers** — task enqueued, `tasks/get` → `working`, `tasks/cancel` → `cancelled`, SQLite row, structlog invariants |

The daemon test passing is itself new: it was structurally incapable of passing
before this release (see CHANGELOG), so the MCP daemon lifecycle has live
coverage for the first time since the transport migration.

## Layer 3 — Costed generation (playwright 1.61.0)

**Live i2i**, local reference attach — `1 passed in 96.71s`. Uploaded, attached,
generated, downloaded.

**Live i2v**, driven via the CLI. Structlog trace in order:
`video_submode_entered` → `aspect_set` → `output_count_set` →
**`image_uploaded target=Start status=200`** → **`frame_attached slot=Start`** →
`prompt_submitted` → **`generate_captured status=200`** with
`startImage=893a001d` parsed → `poll_terminal`.

Flow then returned `PUBLIC_ERROR_VIDEO_GENERATION_TIMED_OUT` — a **server-side**
capacity failure inside Flow, not driver behaviour.

**No finished mp4 is claimed for this release.** What is claimed, and what the
bound exists to protect, is the driver chain: the frame upload returns 200 and
every downstream step executes. The 2026-08-03 regression on 1.62.0 hung
*silently at exactly that upload step*, with no error and no timeout.

## Layer 4 — Defect found live, recorded not omitted

**`--duration` is currently broken for `video i2v` against live Flow.** Flow has
dropped the duration-tab UI; `UiSelectorDriftError` fires on `duration_tab` for
both 4s and 8s.

A/B-proven **pre-existing and unrelated to the playwright raise**: identical
failure on 1.59.0 (24.33s) and 1.61.0 (28.16s). The guard is behaving correctly
— it refuses rather than silently accepting Flow's default (#288) — but the
selector needs re-deriving. Tracked as
[#451](https://github.com/ffroliva/gflow-cli/issues/451). This is why the i2v
proof above was driven via the CLI without `--duration`.

## Layer 5 — User-confirmable artifacts

- `tmp/live-verify/mcp-tasks.md` — MCP Tasks 5-layer evidence note, regenerated
  on 1.61.0.
- `tmp/pytest/test_e2e_i2v_start_end_frame_a0/debug_no_duration_tab.png` —
  screenshot of the drifted editor, auto-captured by the guard.

---

## Not verified this cycle

- **A completed video generation.** Flow's own generation timed out server-side;
  retrying spends another Veo credit against a condition this release cannot
  influence.
- **`e2e_scene`** — skipped, requires `GFLOW_CLI_E2E_SCENE_WORKFLOW_ID`
  (a pre-existing workflow to compose into). Recorded as *not run*, not as pass.
- **`e2e_batch` / `e2e_character`** — not exercised; unchanged by this release.
