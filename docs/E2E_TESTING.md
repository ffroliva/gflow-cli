# E2E Testing Strategy

`gflow-cli` tests the real Google Flow API by necessity — the project drives a
live browser session and there is no publicly-documented mock API. This document
explains the layered testing strategy, how to run each layer, what it costs, and
how to extend it.

---

## Layer model

Rather than a flat test pyramid, gflow-cli uses a **Cost–Risk Matrix** with five
layers. The dominant constraint is not developer time or CI compute but
**external credit burn + API fragility**.

```
Layer 0 — Static Analysis     ruff · pyright · detect-secrets
Layer 1 — Unit Tests           pure logic, no I/O
Layer 2 — Integration Tests    real Provider plumbing, mocked HTTP/browser
Layer 3 — Smoke Tests          1 Imagen credit, golden path only (post-release)
Layer 4 — Full E2E Tests       real credits, all strategies (pre-release gate)
```

**Only Layers 3 and 4 ever hit the real Flow API.**

### Principle: error paths must be free

Any test that covers an error response (HTTP 401, timeout, missing profile,
malformed payload) should live at Layer 1 or 2. Never spend a real credit to
assert that an error is returned. The canonical example: `C5` (transport timeout
budget) was moved from the e2e suite to `tests/api/transports/test_transport_timeout.py`
because it patches all I/O — no browser, no credits.

---

## Markers

Every test file in `tests/e2e/` carries `pytest.mark.e2e`. The root `conftest.py`
enforces this automatically via `pytest_collection_modifyitems` even if a test
author forgets the `pytestmark` declaration.

Tests in `tests/smoke/` carry `pytest.mark.smoke`.

### Cost sub-markers (Layer 4)

Each e2e test also carries **one or more cost sub-markers** so you can run
exactly the cost tier you can afford:

| Marker | Credit cost | Typical wallclock | When to use |
|---|---|---|---|
| `e2e_auth` | 0 | < 30 s | auth/session/health-check — always safe to run |
| `e2e_image` | ~1 Imagen | 30–120 s | text-to-image or image-to-image golden path |
| `e2e_batch` | N × Imagen | 2–10 min | batch image generation |
| `e2e_video` | ~1 Veo | 1–10 min | text-to-video or image-to-video |
| `e2e_data` | (same as above) | +0 s | DB persistence check — combined with image/video |

`e2e_data` is always combined with `e2e_image` or `e2e_video` because
data-layer assertions depend on a real generation having run first.

### Marker inheritance diagram

```
e2e ─┬─ e2e_auth     (auth/session, health check — zero credits)
     ├─ e2e_image    (t2i, i2i — 1 Imagen credit per test)
     │   └─ e2e_data (+ DB assertions)
     ├─ e2e_batch    (batch t2i — N Imagen credits)
     └─ e2e_video    (t2v, i2v — 1 Veo credit per test)
         └─ e2e_data (+ DB assertions)
```

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `GFLOW_CLI_E2E_PROFILE` | *(unset)* | Master gate: name of a logged-in Chromium profile. **Required for Layers 3–4.** |
| `GFLOW_CLI_E2E_RUN_VIDEO` | `"0"` | Set to `"1"` to include video generation in the data-layer e2e run. Video is opt-in because Veo credits are the most expensive. |
| `GFLOW_CLI_E2E_VIDEO_ASPECT` | `"landscape"` | Aspect ratio for video e2e: `landscape` or `portrait`. |
| `GFLOW_CLI_E2E_VIDEO_MODEL` | `"omni-flash"` | Veo model for i2v tests: `omni-flash` or `veo-fast`. |
| `GFLOW_CLI_E2E_VIDEO_DURATION` | `"4"` | Seconds of video to generate in i2v tests (minimum credit unit). |
| `GFLOW_CLI_E2E_BATCH_MANIFEST` | `test_assets/sample_batch.tsv` | TSV manifest for the batch image e2e. |
| `GFLOW_CLI_E2E_BATCH_JITTER` | `"1"` | Set to `"0"` to disable inter-request jitter in batch tests. |
| `GFLOW_CLI_E2E_PROMPT` | *(safe default)* | Prompt override for the smoke test. |

---

## Run commands

### Layer 0–2 (free — run on every commit)

```bash
# All fast tests; excludes e2e, smoke, and live
uv run pytest -m "not e2e and not smoke and not live" -q --cov=gflow_cli
```

### Layer 3 — Smoke (1 Imagen credit)

```bash
export GFLOW_CLI_E2E_PROFILE=<profile-name>
uv run pytest -m smoke -v
```

### Layer 4 — Cost-stratified e2e runs

```bash
export GFLOW_CLI_E2E_PROFILE=<profile-name>

# Zero-credit sanity check (auth, health, DB not-found)
uv run pytest -m "e2e_auth or (e2e and e2e_data and not e2e_image and not e2e_video)" -v

# Cheapest live generation: single image only
uv run pytest -m "e2e_image and not e2e_batch" -v

# Add batch (N Imagen credits — check sample_batch.tsv for row count)
uv run pytest -m "e2e_image" -v

# Add video (1 Veo credit per test)
GFLOW_CLI_E2E_RUN_VIDEO=1 uv run pytest -m "e2e_video" -v

# Full regression (everything)
GFLOW_CLI_E2E_RUN_VIDEO=1 uv run pytest -m e2e -v
```

### Pre-release gate (develop → main)

```bash
export GFLOW_CLI_E2E_PROFILE=<profile-name>
GFLOW_CLI_E2E_RUN_VIDEO=1 uv run pytest -m e2e -v -p no:cov
```

See [DEVELOPMENT.md § E2e gate](DEVELOPMENT.md#e2e-gate-before-merging-develop--main).

---

## File map

```
tests/
├── conftest.py                       # install_log_capture; auto-marker hook
├── smoke/
│   └── test_real_flow.py             # [smoke] golden path, 1 Imagen credit
└── e2e/
    ├── conftest.py                   # e2e_profile_dir, e2e_nosession_profile,
    │                                 #   e2e_env (shared fixtures)
    ├── test_auth_verification_e2e.py # [e2e, e2e_auth]
    ├── test_transports_e2e.py        # [e2e, e2e_{auth,image,batch,video}]
    ├── test_image_batch_e2e.py       # [e2e, e2e_batch]
    ├── test_video_t2v_e2e.py         # [e2e, e2e_video]
    └── test_data_layer_e2e.py        # [e2e, e2e_{image,video,data}]
```

Tests that were previously misclassified as e2e:

| Test | Old location | New location | Why moved |
|---|---|---|---|
| `test_transport_raises_timeout_error_when_io_hangs` (C5) | `test_transports_e2e.py` | `tests/api/transports/test_transport_timeout.py` | Fully mocked — no browser, no credits |

---

## Shared fixtures

All fixtures live in `tests/e2e/conftest.py` unless noted.

| Fixture | Description |
|---|---|
| `e2e_profile_dir` | Resolves `GFLOW_CLI_E2E_PROFILE` → `Path`. Skips if unset or absent. Use in all tests that need a live authenticated session. |
| `e2e_nosession_profile` | Creates a fresh UUID-named empty profile dir inside gflow home. For testing the "no session" path without spending credits. Tears down in `finally` with Windows-lock delay. |
| `e2e_env` | Builds an isolated subprocess environment: temp SQLite DB + temp output dir + active profile. Use for tests that drive `gflow` via subprocess. |
| `install_log_capture` | *(in `tests/conftest.py`)* Installs a fresh `structlog.LogCapture` + `merge_contextvars`. Use instead of defining a local `log_capture` fixture. |

---

## Cost minimization patterns

1. **Single model, minimum parameters** — always use `--model narwhal`, `--count 1`
   for image tests; `--model omni-flash`, `--duration 4`, `--count 1` for video.
2. **Video is always opt-in** — `GFLOW_CLI_E2E_RUN_VIDEO` defaults to `"0"`. A
   developer running `pytest -m e2e` will not burn Veo credits by accident.
3. **Shared project for batch** — `--same-project` (always-on in the batch runner)
   shares one project across all rows. One project creation instead of N.
4. **HAR replay for error paths** — do not spend a live credit to assert that a
   4xx response is handled correctly. Use Playwright's HAR replay (see roadmap).
5. **`e2e_data` is free if generation succeeded** — the DB assertions in
   `test_data_layer_e2e.py` add zero additional credits on top of the generation.
6. **`e2e_auth` tests are always free** — run them before anything else to verify
   the profile is still valid before committing to credit-spending tests.

---

## Isolation

Every e2e test that writes output or touches a database uses an isolated
environment:

- **Output files** → `tmp_path` (pytest-managed, auto-cleaned)
- **SQLite database** → `tmp_path / "gflow.db"` via `e2e_env` fixture
- **Empty profile** → UUID-named dir inside gflow home via `e2e_nosession_profile`
- **Authenticated profile** → read-only borrow via `e2e_profile_dir`; tests must
  never write to the real profile directory

**No parallel execution with `-n`.** Chrome refuses two persistent contexts on
the same `user-data-dir`; running e2e tests in parallel risks OOM and profile
corruption. Always run e2e single-threaded.

---

## Roadmap: contract/replay layer (future)

The gap between Layer 2 (fully mocked) and Layer 4 (fully live) is wide.
A **Layer 3 contract layer** using Playwright HAR replay would close it:

1. **Record** real API responses once:
   ```python
   ctx = await browser.new_context(record_har={"path": "tests/contract/cassettes/t2i.har"})
   ```
2. **Commit** the HAR file. Sanitize: strip bearer tokens, signed URLs, reCAPTCHA
   tokens before committing (use the same redaction logic as `redact_metadata`).
3. **Replay** in CI without credentials:
   ```python
   ctx = await browser.new_context()
   await ctx.route_from_har("tests/contract/cassettes/t2i.har", not_found="fallback")
   ```

For the pure-HTTP transports (`BearerTransport`, `SapisidhashTransport`), use
`pytest-recording` (VCR.py) to record/replay HTTPX interactions.

**Benefits:** error-path tests move from "spend a credit" to "replay a 401
cassette". Transport contract drift is caught the next time cassettes are
regenerated. CI gains confidence that the API shape hasn't changed without
spending credits on every PR.

This work is tracked in the project backlog.

---

## See also

- [CONTRIBUTING.md § Test categories](../CONTRIBUTING.md#test-categories)
- [DEVELOPMENT.md § E2e gate](DEVELOPMENT.md#e2e-gate-before-merging-develop--main)
- [docs/DATA_LAYER.md](DATA_LAYER.md)
- [docs/ARCHITECTURE.md § Testing topology](ARCHITECTURE.md#testing-topology)
