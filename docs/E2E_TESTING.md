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
Layer 3 — Smoke Tests          image gen only — ZERO credits, golden path (post-release)
Layer 4 — Full E2E Tests       real Veo credits (video only), all strategies (pre-release gate)
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

> **Image generation costs ZERO Flow credits.** Only video (Veo) spends credits.
> Images are limited by a **daily cap**, not billed — hitting it is a rate limit,
> not a charge. This table said "~1 Imagen credit" for years, which discouraged
> running tests that were always free; that stale claim is what left the #615
> `referenceEntity` guard unverified while it silently did nothing. Confirmed with
> the maintainer 2026-09-02.

| Marker | Credit cost | Typical wallclock | When to use |
|---|---|---|---|
| `e2e_auth` | 0 | < 30 s | auth/session/health-check — always safe to run |
| `e2e_image` | **0** (daily cap) | 30–120 s | text-to-image or image-to-image golden path |
| `e2e_batch` | **0** (daily cap) | 2–10 min | batch image generation |
| `e2e_video` | ~1 Veo | 1–10 min | text-to-video or image-to-video |
| `e2e_data` | (same as above) | +0 s | DB persistence check — combined with image/video |

`e2e_data` is always combined with `e2e_image` or `e2e_video` because
data-layer assertions depend on a real generation having run first.

### Marker inheritance diagram

```
e2e ─┬─ e2e_auth     (auth/session, health check — zero credits)
     ├─ e2e_image    (t2i, i2i — ZERO credits, daily cap only)
     │   └─ e2e_data (+ DB assertions)
     ├─ e2e_batch    (batch t2i — ZERO credits, daily cap only)
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
| `GFLOW_CLI_E2E_RUN_ENTITY_PROV` | `"0"` | Set to `"1"` to run the entity-provenance e2e (#402) — asserts a generation Flow actually accepted records its `entity_ids` / `entity_names` in `operations.metadata_json`. Opt-in because it drives a real browser generation (no credit cost — images are free). |
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

### Layer 3 — Smoke

> **Requires a real authenticated profile.** Smoke tests will skip automatically
> if `GFLOW_CLI_E2E_PROFILE` is not set or the named profile directory does not
> exist. They cannot be run in CI or any sandbox environment — a live Google
> Flow session on a workstation or server with a headed browser is required.

```bash
export GFLOW_CLI_E2E_PROFILE=<profile-name>

# All smoke tests (includes the golden-path image test — zero credits)
uv run pytest -m smoke -v

# Zero-credit only — account persistence check, no generation
uv run pytest -m smoke tests/smoke/test_profile_account_smoke.py -v
```

| Smoke test | Credits | Notes |
|---|---|---|
| `test_real_flow.py` | **0** (daily cap) | Full golden path; use `GFLOW_CLI_E2E_PROMPT` to override the prompt |
| `test_profile_account_smoke.py` | **0** | Auth verification only; backfills `.gflow_account` for pre-v0.10 profiles |

### Layer 4 — Cost-stratified e2e runs

```bash
export GFLOW_CLI_E2E_PROFILE=<profile-name>

# Zero-credit sanity check (auth, health, DB not-found)
uv run pytest -m "e2e_auth or (e2e and e2e_data and not e2e_image and not e2e_video)" -v

# Cheapest live generation: single image only
uv run pytest -m "e2e_image and not e2e_batch" -v

# Add batch (zero credits; consumes more of the daily image cap — see sample_batch.tsv for row count)
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

## Nightly canary (#502)

Between releases the live tiers are invisible, so Flow-side drift (cohort flaps,
selector renames, auth rot) surfaces ad-hoc during feature work instead of on a
schedule. The canary closes that gap.

It runs **locally, not in hosted CI** — the live tiers need a real authenticated
Chrome profile, and Google bot-detection plus reCAPTCHA make hosted auth
infeasible. Results are published to a single **rolling issue**; the canary never
opens new ones (issue spam trains red-blindness) and **gates nothing** (a gate on
a machine that might be off is self-DoS).

### Four states

| State | Meaning | Action |
|---|---|---|
| `GREEN` | every selected $0 tier passed | none |
| `RED` | auth healthy, a $0 tier failed | **real drift or regression** — triage |
| `AUTH-EXPIRED` | session rot (expected) | `gflow auth login` |
| `DEFERRED` | nothing conclusive ran | fix the profile / config — says nothing about Flow |

Splitting the last two out of `RED` is the point: `RED` must always mean "code or
Flow drifted", never "please re-login" or "you had Chrome open". The classifier is
a pure function with all four states covered in
`tests/scripts/test_canary_classify.py`.

**Rot vs. drift is decided by a second probe, not by error names.** When a tier
fails for a reason other than a profile precondition, the canary re-runs
`gflow auth status`. Session still valid ⇒ the failure is
drift (`RED`); session now dead ⇒ genuine rot (`AUTH-EXPIRED`). The first live run
proved why this matters: an `AuthExpiredError` on `project.createProject`
*looked* exactly like session rot, but the session verified clean seconds later —
so it was a real divergence between two auth surfaces, and a name-matching
classifier would have buried it. The extra probe costs ~45s and only runs after a
failure.

> The route is `labs.google/fx/api/trpc/project.createProject` — the NextAuth
> **cookie** surface — not the aisandbox Bearer path. #561 originally recorded it
> as an `uploadImage` failure because the failing test is named for an upload;
> the traceback dies one line earlier, on the project-creation call. Corrected
> here so the next reader does not re-investigate the wrong host.

`DEFERRED` covers every run that **exercised nothing**: a held `ProfileLease`, a
`ProfileEngineDowngradeError` (profile written by a newer Chromium than the bundled
engine), a refused `--pull`, or a run where every selected test skipped. All fail
before reaching Flow, so none can evidence drift.

Two subtleties, both found by council review and both regression-tested:

- **A precondition is matched on the raised exception, never a substring.** Two
  `e2e_auth` tests mention `ProfileLockedError` in ordinary source — one in an
  assertion message, one in a comment — and pytest echoes the failing function's
  source into its traceback. Substring matching therefore published a genuine
  regression in those tests as `DEFERRED`, the one label that says "ignore me".
- **A green run that executed nothing is not green.** pytest exits 0 when every
  test skips, and every e2e test skips on a missing profile directory. `GREEN`
  requires `passed > 0`.

Because the issue carries a last-updated timestamp, a machine that was off is
*visibly stale* — unlike a lingering green commit status, which lies.

**A RED preserves its own evidence, logs included.** The run's JUnit is copied to
`tmp/canary-red-<stamp>.xml` (the live one is overwritten nightly), and pytest runs
with `-o junit_logging=all` so that copy carries the captured structlog output, not
just a traceback. This is not optional detail: the canary's failures reproduce only
unattended, so whatever the run does not record cannot be recovered afterwards. For
an auth 401 the deciding line is `client.context_cookie_state` — the #222
diagnostic, which reports whether the launched browser context actually loaded the
Flow session cookie. That single boolean separates "the browser never got the
cookie" from "the session was genuinely refused"; without it a 401 traceback is the
same shape either way. The copy stays **local** — it carries raw tracebacks and
profile paths, which the sanitization contract keeps off the public issue.

### Scope

`-m e2e_auth` only ($0, no reCAPTCHA); fast-follow adds `e2e_scene` ($0). Credit
tiers (`e2e_image` / `e2e_video` / `smoke`) stay **strictly manual** via
`/gflow:live-verify` — no unattended credit spend on a personal account.

### Run it

```bash
# dry run — executes for real, prints the payload, touches nothing on GitHub
uv run python scripts/canary/run_canary.py --profile <name> --dry-run
```

Generation markers (`e2e_image`, `e2e_video`, `e2e_batch`, `e2e_character`,
`smoke`) are **refused outright** — the canary never drives a real generation
unattended. (Only `e2e_video` spends credits; the image tiers are refused because
they drive a live browser and draw on the daily image cap, not because they bill.)
Run those manually via `/gflow:live-verify`.

### Schedule it

```powershell
.\scripts\canary\register_task.ps1 -Profile <name> -Issue <n> `
    -RepoRoot C:\path\to\dedicated\clone
```

Point `-RepoRoot` at a **dedicated clone**, never your working tree: the runner's
`--pull` refuses a dirty checkout rather than resetting over uncommitted work, and
reports that refusal as `DEFERRED` rather than exiting silently. `--pull` also
re-syncs dependencies, so a lockfile bump on `develop` cannot masquerade as a
`RED` import error.

Publishing uses your already-authenticated local `gh` — **no new secrets or
tokens**. Published content is sanitized for a public repo: SHA, pass/fail
counts, duration, failure class, and failing test *names* only — never raw logs,
profile paths, prompts, or signed URLs.

## Selector drift probe (#563)

The canary answers *"does gflow still work on the maintainer's cohort?"*; the
probe answers *"which Flow DOM selector moved?"* — from hosted CI, with no
maintainer machine involved. It walks the structured selector inventory in
`gflow_cli.flow_selectors` (the incident families from #404/#493/#313) against
a live editor at the pinned 1920×1080 viewport: navigate and read only, **$0**,
never submits.

Runs via `.github/workflows/selector-probe.yml` on `schedule` (05:00 UTC daily)
plus `workflow_dispatch`. **Never add a `pull_request` trigger**: same-repo
branch PRs receive repository secrets while running attacker-editable probe
code (fork PRs are safe — GitHub withholds secrets — but the same-repo case is
not).

### Exit contract

| Exit | Meaning | Action |
|---|---|---|
| `0` | every registered selector resolved (`ok` / `FALLBACK[n]` / `n/a`) | none |
| `1` | **drift** — a selector graded MISS or AMBIGUOUS; the report names its key | re-derive that selector, ship a registry edit |
| `2` | **inconclusive** — expired token, dead project, known alternate UI state, or no recognizable editor arm | fix the credential/project; says nothing about Flow |

Keeping `1` and `2` apart is the whole design: an expired credential must never
be published as "Google changed the page". The deciding gate is a poll on
production's own arm indicators (the six `crop_*` variants ⇒ classic, the
agentic ligatures ⇒ agentic); a page showing neither within 25 s — a sign-in
wall renders neither — is inconclusive, not drift. Mode-scoped selectors absent
on the observed arm grade `n/a` (`EXPECTED_ABSENT`), never drift — the probe's
first two live runs landed on opposite arms (classic locally, agentic on the
runner) and both graded clean.

### Secrets and token care

Two repository secrets, from a **dedicated throwaway Google account**, never
the maintainer's: `GFLOW_CI_SESSION_TOKEN` (the
`__Secure-next-auth.session-token` cookie) and `GFLOW_CI_PROJECT_ID` (any
project that account owns — a deleted id renders an error shell the probe
reports as exit 2).

The token **re-issues on every profile open** (observed live 2026-08-21: three
values in 15 minutes; the first CI dispatch failed exit 2 on a superseded
snapshot). Harvest accordingly: open the throwaway profile once, read the
cookie, close the browser, `gh secret set GFLOW_CI_SESSION_TOKEN` from stdin —
and never reopen that profile without re-syncing the secret afterwards. The
value also hard-dies at day 30 with no warning. Either way an aged token shows
as a red exit-2 run, never as drift, and the nightly schedule itself is the
aging test.

---

## File map

```
tests/
├── conftest.py                           # install_log_capture; auto-marker hook
├── smoke/
│   ├── test_real_flow.py                 # [smoke] golden path, ZERO credits
│   └── test_profile_account_smoke.py     # [smoke] profile account persistence — 0 credits
└── e2e/
    ├── conftest.py                       # e2e_profile_dir, e2e_nosession_profile,
    │                                     #   e2e_env (shared fixtures)
    ├── test_auth_verification_e2e.py     # [e2e, e2e_auth]
    ├── test_transports_e2e.py            # [e2e, e2e_{auth,image,batch,video}]
    ├── test_image_batch_e2e.py           # [e2e, e2e_batch]
    ├── test_video_t2v_e2e.py             # [e2e, e2e_video]
    ├── test_incident_quality_e2e.py      # [e2e, e2e_auth] incident-bundle quality benchmark — 0 credits
    └── test_data_layer_e2e.py            # [e2e, e2e_{image,video,data}]
```

### Incident-bundle quality benchmark

`test_incident_quality_e2e.py` (marker `e2e_auth`, **0 credits**) is not a
generation test — it drives a real UI-state failure plus real two-process
profile contention and **grades the resulting incident bundles' diagnostic
quality**, asserting hard floors so a regression that hollows out the captured
evidence (empty journals, hosts all reduced to `other`, a null command, a
leaked identifier) fails CI even though the artifacts still exist. It reuses the
standalone scorer `scripts/dev/incident_bundle_quality.py` (also runnable on any
field bundle a user emails), which is unit-covered offline by
`tests/scripts/test_incident_bundle_quality.py`. See
[DEBUGGING § Assessing a bundle's quality](DEBUGGING.md#assessing-a-bundles-quality).

### Smoke test inventory

| File | Credits | What it verifies |
|------|---------|-----------------|
| `test_real_flow.py` | **0** | Golden path: open Flow, submit prompt, save PNG, check dimensions |
| `test_profile_account_smoke.py` | 0 | `.gflow_account` file present + valid email; `list_profiles()` surfaces `google_account`; `gflow auth list --json` includes the field |

> **Real environment required.** Both smoke tests require a profile that has been
> authenticated with `gflow auth login` against real Google Flow. They cannot run in
> a sandbox, CI, or any environment without a live Google session.
> Set `GFLOW_CLI_E2E_PROFILE=<name>` to the name of a logged-in profile before running.
> See [AUTHENTICATION.md § Session storage](AUTHENTICATION.md#session-storage) for
> where profile directories live on each OS.

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
the same `user-data-dir`, and the cross-process `ProfileLease` now enforces
that fail-fast: a second test (or a leftover daemon/task) touching an
already-leased profile is rejected immediately with `ProfileLockedError`
(exit code 11) instead of racing Chrome into a corrupted profile. That's a
clean failure, not a crash — but it still means two e2e runs against the same
profile can't proceed concurrently. Always run e2e single-threaded against a
given profile.

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

## Migrated host (`flow.google.com`) — `tests/e2e/test_migrated_host_e2e.py`

Google is moving accounts onto `flow.google.com` (#639). Under the default
`GFLOW_CLI_FLOW_HOST=auto` every `video t2v` with an existing project runs on the
new host, on moved and unmoved accounts alike, so these tests hold for any
logged-in profile. They need a project id on that host:

```bash
GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_PROJECT=<project-uuid> \
    uv run pytest -m e2e tests/e2e/test_migrated_host_e2e.py -v
```

| Test | Marker | Cost | Proves |
|---|---|---|---|
| `test_e2e_migrated_host_serves_this_account` | `e2e_auth` | 0 | a direct load of `flow.google.com/project/<id>` renders the migrated editor for this account |
| `test_e2e_kill_switch_keeps_exit_36_on_a_moved_account` | `e2e_auth` | 0 | `GFLOW_CLI_FLOW_HOST=labs.google` still yields the distinct exit 36 on a moved account (skips on an unmoved one) |
| `test_e2e_t2v_runs_on_flow_google_com_by_default` | `e2e_video` | 1 clip (12 credits measured) | `migrated.dispatch` → `submit_observed` → `result`, a real mp4 (`ftyp`), recorder-visible ids |

`GFLOW_CLI_E2E_FLOW_HOST` overrides the routing for the paid test (e.g.
`flow.google.com` to force it); `GFLOW_CLI_E2E_VIDEO_DURATION` applies as elsewhere.

## See also

- [CONTRIBUTING.md § Test categories](../CONTRIBUTING.md#test-categories)
- [DEVELOPMENT.md § E2e gate](DEVELOPMENT.md#e2e-gate-before-merging-develop--main)
- [docs/DATA_LAYER.md](DATA_LAYER.md)
- [docs/ARCHITECTURE.md § Testing topology](ARCHITECTURE.md#testing-topology)
