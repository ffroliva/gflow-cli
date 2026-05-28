# gflow Public Event Surface — Design

**Status:** Draft — council-reviewed (2026-05-27), Tier 1 fixes applied
**Date:** 2026-05-27
**Owner:** Flavio Oliva
**Repo:** `gflow-cli`
**Parallel work:** `gflow-cli-remotion/docs/specs/2026-05-27-promo-pipeline-design.md` (motivating consumer)

---

## 1. Goals & Non-Goals

### Goals

1. Define a **stable, public, structured event API** on gflow's stdout that any external consumer (monitoring, replay, data layer, plugins, the promo orchestrator) can bind to.
2. Replace today's mix of `error_raised` + scattered `ui_automation.*` dotted events with a **tier-organized model** that distinguishes public API from implementation detail.
3. Enable real external integrations without requiring code changes in gflow per consumer.
4. Resolve known internal fragility: the `on-started-callback-recorder-safety` footgun (uncaught callback exceptions aborting paid Flow runs) becomes a "subscribe to events" pattern instead of callback injection.

### Non-Goals (v1)

- No internal refactor to a pub/sub event bus. Components keep calling each other directly. This spec is about the **observability surface only**.
- No new transport. Events still flow through `structlog` to stdout as JSON lines.
- No removal of existing `ui_automation.*` events; they remain useful for internal debugging.
- No SaaS / cloud event-streaming infrastructure.
- No plugin SDK in v1 — consumers parse stdout JSON.

### Motivating consumers (today and near-term)

| Consumer | What it'd bind to |
|---|---|
| `gflow-cli-remotion` promo orchestrator | Tier 1 command boundaries + Tier 2 artifact-completion events |
| gflow's own data layer (existing) | Tier 2 — could replace fragile `VideoStartedCallback` injection |
| Hypothetical webhook plugin | Tier 1 + 2 — POST each event to a configured URL |
| Hypothetical Datadog / Prometheus exporter | Tier 1 counters + Tier 2 durations |
| Hypothetical replay / audit tooling | Tier 1 + 2 → reconstruct what any past run did |
| Debugging the gflow library itself | Tier 3 (implementation-detail dotted events) |

---

## 2. Three-Tier Model

| Tier | Stability | Examples | Audience |
|---|---|---|---|
| **1: Command lifecycle** | SemVer-stable public API | `command.invoked`, `command.completed`, `command.failed` | Any external consumer; orchestrator phase boundaries |
| **2: Resource lifecycle** | SemVer-stable public API | `image.generation.{started,completed,failed}`, `video.generation.{started,progress,completed,failed}`, `batch.{started,item.{started,completed,failed},completed}`, `auth.session.{refreshed,expired}`, `auth.login.completed` | Data layer, promo, replay, webhooks |
| **3: Implementation detail** | NO stability guarantee; may rename freely | `ui_automation.*`, `image_batch.*` (current dotted events) | Internal debugging only |

**Tier 1 + Tier 2 = "Public Event Surface."** Everything else is implementation detail and consumers MUST NOT bind to it.

Tier-3 events ARE documented as such in the source — a one-line `# Tier 3: implementation detail; do not bind in external consumers.` near each emission site — so any internal contributor adding a new event knows the consequence.

---

## 3. Event Envelope

Every Tier 1 / 2 event has these fields:

```json
{
  "ts": "2026-05-27T14:32:11.123Z",
  "event": "image.generation.completed",
  "level": "info",
  "schema_version": 1,
  "run_id": "01HZ7K8M3PVQTYK7A4N9XJ5MW2",
  "data": { /* typed per-event payload */ }
}
```

| Field | Type | Notes |
|---|---|---|
| `ts` | ISO 8601 UTC string | Wall-clock. Already emitted by structlog's default processor. |
| `event` | dotted string | The event name. Tier 1/2 are namespaced under `command.`, `image.`, `video.`, `batch.`, `auth.`. |
| `level` | `info` \| `warn` \| `error` | Aligned with Python logging. `*.completed` is `info`; `*.failed` is `error`. |
| `schema_version` | int | This envelope contract version. Starts at 1. Bumped on breaking changes. **Tier 3 events do NOT carry this field — absence of `schema_version` means implementation-detail; consumers MUST ignore those events.** |
| `run_id` | ULID (Crockford base32) | One per CLI invocation. ALL events in that invocation share it. Replaces the existing `correlation_id` contextvar (see §8.0). |
| `data` | object | Typed per-event payload. Schemas in `tests/fixtures/events/<event>.schema.json`. |

`run_id` ties events from the same invocation together. A consumer reconstructs "what did this run do" by filtering on `run_id`. ULID chosen over UUIDv4 because it sorts lexicographically by time, making event streams browsable. ULID leaks the invocation timestamp; this is acceptable because `ts` already carries it.

**Existing `correlation_id` collision.** `gflow_cli/cli.py:main()` already binds `correlation_id=str(uuid.uuid4())` via `bind_contextvars`. Phase 0 (§8.0) renames it to `run_id` and switches generation from UUIDv4 to ULID. Existing consumers of the old `error_raised` events lose `correlation_id`; for one minor release the envelope processor aliases the field (`run_id` AND `correlation_id` carry the same value), then `correlation_id` is dropped on the next minor.

---

## 4. Tier 1 Catalog — Command Lifecycle

| Event | Fires | Data payload |
|---|---|---|
| `command.invoked` | After argparse, before any work | `command` (e.g. "image t2i"), `args_redacted` (prompts hashed), `gflow_version`, `python_version`, `os` |
| `command.completed` | Successful CLI exit, code 0 | `command`, `exit_code` (always 0), `duration_ms` |
| `command.failed` | CLI exit, code != 0 | `command`, `exit_code` (int per `EXIT_CODE_MAP`), `exit_code_class` (e.g. "AuthError"), `error_message_redacted`, `duration_ms` |

**Invariants** (asserted in tests):
- `command.invoked` fires exactly once per invocation.
- Exactly one of `command.completed` OR `command.failed` follows (never both, never neither).
- All three share the same `run_id`.
- `command.failed.exit_code_class` aligns with the existing `EXIT_CODE_MAP` ordering (per `exit-code-map-ordering-invariant` memory).

---

## 5. Tier 2 Catalog — Resource Lifecycle

### 5.1 Image generation

| Event | Data |
|---|---|
| `image.generation.started` | `kind` ("t2i" \| "i2i"), `prompt_hash`, `aspect`, `model`, `transport` ("ui_automation" \| "rest") |
| `image.generation.completed` | `kind`, `artifact_path`, `format` ("png" \| "jpeg"), `width`, `height`, `file_size_bytes`, `duration_ms` |
| `image.generation.failed` | `kind`, `error_class`, `error_message_redacted`, `duration_ms` |

### 5.2 Video generation

| Event | Data |
|---|---|
| `video.generation.started` | `kind` ("t2v" \| "i2v" \| "r2v"), `prompt_hash`, `model` (e.g. "veo3"), `params` |
| `video.generation.progress` | `kind`, `state` ("queued" \| "rendering" \| "downloading"), `percent` (nullable), `eta_ms` (nullable) |
| `video.generation.completed` | `kind`, `artifact_path`, `format`, `width`, `height`, `video_duration_ms`, `file_size_bytes`, `duration_ms` |
| `video.generation.failed` | `kind`, `error_class`, `error_message_redacted`, `duration_ms` |

`video.generation.progress` cadence: emit on **state transition** + at most **every 10 seconds** during the same state. Goal: useful for progress UI without flooding the consumer.

### 5.3 Batch

| Event | Data |
|---|---|
| `batch.started` | `config_basename`, `count`, `kinds` (array of "t2i" / "t2v" etc) |
| `batch.item.started` | `index` (0-based), `total`, `kind`, `prompt_hash` |
| `batch.item.completed` | `index`, `total`, `kind`, `artifact_path_rel`, `duration_ms` |
| `batch.item.failed` | `index`, `total`, `kind`, `error_class`, `error_message_redacted`, `duration_ms` |
| `batch.item.skipped` | `index`, `total`, `kind`, `reason` (the existing image_batch supports skip) |
| `batch.completed` | `count`, `succeeded`, `failed`, `skipped`, `duration_ms` |

### 5.4 Auth

| Event | Data |
|---|---|
| `auth.login.started` | `profile_hash`, `browser_strategy` ("chrome") |
| `auth.login.completed` | `profile_hash`, `browser_strategy`, `session_expires_at_iso` |
| `auth.login.failed` | `profile_hash`, `browser_strategy`, `error_class`, `error_message_redacted` |
| `auth.session.refreshed` | `profile_hash`, `browser_strategy`, `session_expires_at_iso` |
| `auth.session.expired` | `profile_hash`, `browser_strategy` |

**No event ever contains an OAuth token, refresh token, cookie value, or session blob.** This is invariant — enforced by a per-event "forbidden field names" allowlist in tests.

---

## 6. Redaction Policy

### 6.1 Field rules

| Field type | In events |
|---|---|
| Prompts | `prompt_hash` = HMAC-SHA-256 with a per-install salt (persisted in `<user-state-dir>/event_salt`, 32 random bytes, generated on first use). NEVER plaintext. **Raw SHA-256 is insufficient because low-entropy prompts ("logo for Acme Inc") are rainbow-attackable.** |
| File paths (absolute) | TWO fields: `artifact_path` (absolute) AND `artifact_path_rel` (relative to `out_dir`). Default `--safe-stdout` mode emits only `*_rel`. See §6.4. |
| Account identifiers (`email`, `google_account_id`) | Never serialized. |
| Auth tokens, cookies, session blobs | Never serialized. |
| Error messages | `error_message_redacted` — pass through `redact()` (§6.2). |
| Args / params | `args_redacted` — per-arg policy (§6.3). |

### 6.2 `redact()` rules (centralized helper)

Phase 0 introduces `gflow_cli/observability/redact.py:redact()` by **lifting and generalizing** the existing `redact_metadata` from `gflow_cli/data/redaction.py` (the data-layer redactor). The lifted helper applies these substitutions to any input string:

| Pattern | Regex | Replacement |
|---|---|---|
| Email | `[\w.+-]+@[\w-]+\.\w+` | `<email>` |
| Google OAuth refresh token | `1//[\w-]+` | `<refresh-token>` |
| Google OAuth access token | `ya29\.[\w.-]+` | `<access-token>` |
| JWT (3-segment base64) | `eyJ[\w-]+\.[\w-]+\.[\w-]+` | `<jwt>` |
| Fernet token | `gAAAAAB[\w-]+` | `<fernet-token>` |
| SAPISID hash | `sapisidhash=[\w-]+` | `sapisidhash=<redacted>` |
| Windows user path | `[A-Z]:\\Users\\[^\\]+\\` | `<user-home>\` |
| POSIX user path | `/(Users|home)/[^/]+/` | `/<user-home>/` |
| Long base64-shaped blob (≥64 chars) | `[A-Za-z0-9+/=]{64,}` | `<base64-blob>` |

Golden-file tests in `tests/fixtures/redaction/` lock these rules.

### 6.3 `args_redacted` per-arg policy

| Arg | Policy |
|---|---|
| `--prompt <text>` | HMAC-hash → `prompt_hash` field |
| `--prompt-file <path>` | Hash the FILE CONTENTS → `prompt_hash`; record `prompt_file_basename` only |
| `--profile <name>` | HMAC-hash → `profile_hash` (profile name is an account proxy) |
| `--config <path>` | Basename only → `config_basename` |
| `--out <path>` | Basename only → `out_basename` |
| `--ref <path>` | Basename only → `ref_basename` |
| `--aspect`, `--model`, `--seed`, `--duration`, `--count` etc. | Pass-through |
| Any flag matching `*token*`, `*secret*`, `*password*`, `*key*` | Refuse and abort (this is a CLI policy violation, log to stderr) |

Per-event JSON Schemas set `additionalProperties: false` at the top level of `data` so new args don't silently leak.

### 6.4 `--safe-stdout` mode (default ON)

Operator can pass `--unsafe-stdout` (or set `GFLOW_CLI_UNSAFE_STDOUT=1`) to disable redaction of absolute paths. Default behavior emits only `*_rel` path fields and the redacted error-message variant.

### 6.5 Tier 3 events ALSO obey redaction

The forbidden-field invariant test (§9) and `redact()` apply to Tier 3 events too. Stability is orthogonal to safety. Tier 3 events just have no API stability guarantee — they still cannot leak secrets.

---

## 7. Stability & Versioning Policy

**Treat the public event surface as a public API and apply SemVer to it.**

| Change | Bump? | Notes |
|---|---|---|
| New optional field in existing event's `data` | No bump | Consumers ignore unknown fields. |
| **New required field** | **Bump** | Strict consumers break otherwise. |
| New event | No bump | Consumers ignore events they don't recognize. |
| Rename field | Bump | Plus CHANGELOG entry + deprecation period. |
| Remove field | Bump | Plus CHANGELOG entry + deprecation period. |
| Change field type | Bump | Plus CHANGELOG entry. |
| Rename event | Bump | Plus deprecation: both names emitted for ≥1 minor release. |
| **Remove event entirely** | **Bump** | Plus deprecate-then-remove over ≥2 minor releases. |
| **Semantic change (same name + shape, different meaning)** | **Bump** | E.g., `duration_ms` switching from wall-clock to monotonic. CHANGELOG mandatory. |
| Tier 3 events (any change) | No bump | No stability guarantee. |

**Deprecation policy**: bumped events emit **both** old and new shape for at least one minor `gflow-cli` version (≥4 weeks), then old shape is dropped on the next minor release.

**CI enforcement**: a **field-surface snapshot test** (§9) freezes the exact key-set per Tier 1/2 event. CI fails on any diff unless the snapshot is regenerated in the same PR. Reviewers see the surface change in the diff.

Schema version applies to the **envelope**, not individual payloads. Payload changes follow the rules above.

**Cross-repo versioning is independent.** `gflow-cli-remotion`'s `RunManifest.schemaVersion` and gflow-cli's envelope `schema_version` are separate version namespaces. A consumer adding more events doesn't necessarily bump either.

---

## 8. Implementation Approach

### Phase 0 — Prerequisites (PR `chore/event-surface-prerequisites`)

These exist as preconditions before any event work:

1. **Add `jsonschema>=4` to `[dependency-groups].dev`** in `pyproject.toml`. (Council finding: spec assumed it was a dev-dep; it is not.)
2. **Add `python-ulid>=2` to runtime dependencies.** Small, well-maintained.
3. **Lift `redact()` helper** from `gflow_cli/data/redaction.py:redact_metadata` (the existing data-layer redactor) into a new `gflow_cli/observability/redact.py`. The data layer keeps its current import path (delegates to the new helper). Apply the rule set from §6.2 + golden-file tests in `tests/fixtures/redaction/`.
4. **Rename `correlation_id` → `run_id`** in `gflow_cli/cli.py:main()`'s `bind_contextvars` call, switch generation from UUIDv4 to ULID. Add envelope processor to alias `run_id` AND `correlation_id` for one minor release (deprecation per §7).
5. **Add `<user-state-dir>/event_salt` generator** — first-use auto-creates 32 random bytes; HMAC-prompt-hashing uses it.

### Phase 1 — Foundation (PR `feature/event-surface-foundation`)

1. **Envelope processor**: structlog processor that injects `schema_version: 1` into every event whose name matches `^(command|image|video|batch|auth)\.`. **The processor RUNS on every event — it just only SETS `schema_version` when the regex matches.** Tier 3 events pass through the processor with no envelope field; absence of `schema_version` signals "Tier 3, do not bind."
2. **`command.{invoked,completed,failed}` emission**: at CLI entry (`cli.main()`) and CLI exit (existing exit-code wrapper). `command.failed.exit_code_class` aligns with the existing `EXIT_CODE_MAP` — cross-link to `test_exit_code_map_ordering_invariant` rather than duplicating.
3. **Argparse rejection path**: `command.failed` MUST fire from an `atexit`/exception-hook wrapper even when argparse rejects args before `command.invoked` would normally emit. This is the only way replay tooling reconstructs rejected invocations.
4. **Docs**: new `docs/EVENT_SURFACE.md` with the catalog, envelope contract, versioning policy, redaction policy.
5. **JSON Schema fixtures**: `tests/fixtures/events/command-invoked.schema.json` etc. (one per Tier 1/2 event), validated by tests. Each schema sets `additionalProperties: false` at top level.

### Phase 2 — Resource lifecycle (PR `feature/event-surface-resources`)

Sprinkle Tier 2 emissions at existing call sites. Each emission is ~3 lines:

```python
log.info(
    "image.generation.completed",
    kind="t2i",
    artifact_path=str(out_path),
    format=fmt,
    width=w, height=h,
    file_size_bytes=size,
    duration_ms=elapsed_ms,
)
```

- `image.generation.completed` after `client.download_image()` (where `image-t2i-jpeg-with-png-extension` fix lives — extension sniff already runs there).
- `video.generation.completed` after `client.download_video()`.
- `batch.*` in the batch orchestrator (next to existing `image_batch.row_completed`).
- `auth.*` in the auth module.

### Phase 3 — Data layer migration (separate PR, opportunistic)

**Important correctness constraint:** `VideoStartedCallback` fires BEFORE polling so the recorder can insert a STARTED row before paid Flow work commits. Per `exit-code-16-data-store` memory, pre-Flow data-store failures MUST abort the run. A pure event subscriber CANNOT block the producer, so it CANNOT replace this pre-flight insert path.

The migration is therefore **additive**, not a replacement:

- **Keep the blocking callback** for the pre-flight STARTED-row insert (preserves the exit-code-16 contract).
- **Add Tier 2 event subscribers** for COMPLETED-row and FAILED-row updates — these don't need to block the producer; if the subscriber errors, the producer's exit status is unaffected and the operator gets a warning that a row didn't persist (gflow-cli already returns 0 with a warning on post-success failures, per the same memory).
- Net effect: callback-as-pre-flight stays; subscribers handle post-flight. The `on-started-callback-recorder-safety` footgun (uncaught exception in callback aborting paid run) is mitigated by wrapping the callback in try/except per that memory, NOT by removing the callback.

Result: the data layer eventually subscribes to most Tier 2 events for "free persistence" of future event types, while the integrity-critical pre-flight insert keeps its blocking entry point.

### Phase 4 — Documentation & catalog freeze

- `docs/EVENT_SURFACE.md` fully populated.
- `docs/INDEX.md` adds an entry for it.
- `docs/AGENT_GUIDE.md` references it for "what does gflow emit?"
- After 1 minor version of soak time with no breaking changes, `schema_version=1` is declared stable and the public surface guarantee enters force.

---

## 9. Testing Strategy

| Layer | Test |
|---|---|
| Per-event unit | Synthetic call site fires event → captured by `structlog.testing.LogCapture` (`structlog-cache-logger-off-for-tests` memory applies — `cache_logger_on_first_use=False`) → JSON-schema validation against fixture committed in `tests/fixtures/events/<event>.schema.json`. |
| **Forbidden-field invariant (recursive)** | Walk every event payload as a tree (`walk_dict()`, all nested levels). Two checks per leaf: (a) **key path** matches `r'(?i)(token\|secret\|password\|cookie\|csrf\|xsrf\|session\|sid\|id_token\|access_token\|refresh_token\|bearer\|authorization\|sapisid\|oauth\|jwt\|api[_-]?key\|private[_-]?key\|client[_-]?secret\|email\|google_account_id\|prompt(?!_hash))'`; (b) **value shape** matches `r'eyJ[\w-]+\.'` (JWT) OR `r'ya29\.'` OR `r'^1//'` (Google refresh token) OR `r'gAAAAAB'` (Fernet) OR `r'[A-Za-z0-9+/=]{64,}'` (long base64). Applies to Tier 1+2 AND Tier 3. |
| Run-id invariant (positive + negative) | (+) All events in one invocation share `run_id`. (-) Two sequential `cli.main()` calls in the same pytest session have DISTINCT `run_id`s — must call `structlog.contextvars.clear_contextvars()` between. |
| Lifecycle invariant | At end-of-stream, for every `*.started` per `run_id`, a matching `*.completed` OR `*.failed` exists. Tests opt-in to `expect_inflight=True` for partial-run scenarios. SIGINT-mid-run test asserts an exit-handler emits `command.failed` before process death. |
| Command invariant | Exactly one `command.invoked` per invocation; exactly one of `command.completed` / `command.failed`. Argparse-rejection path tested: invalid args still emit `command.failed`. |
| **Field-surface snapshot (CI-enforced SemVer)** | Per Tier 1/2 event, serialize the sorted key-set of `data` to `tests/fixtures/events/_surface_snapshot.json`. Any diff fails CI unless the snapshot is regenerated in the same PR — the snapshot diff IS the SemVer change request. |
| Schema version invariant | All Tier 1/2 events carry `schema_version: 1`. Tier 3 events do NOT carry `schema_version`. |
| **Progress-event rate limit** | Stub a slow stdout, fire 100 `video.generation.progress` events; assert ≤1 every 5 seconds; assert `*.completed` / `*.failed` are NEVER dropped. |
| Backward compat | `tests/fixtures/events/consumer_sample.py` (a 30-line consumer that counts batch successes from a JSON stream) + `tests/fixtures/events/golden_stream.jsonl` (one canonical sample). CI runs `consumer_sample.py < golden_stream.jsonl`; exit 0 + expected counter required. PR that breaks this must update the golden stream AND bump `schema_version`. |
| End-to-end | One `--dry-run` capture per Phase 1+2 PR, committed as `tests/fixtures/events/e2e_capture.jsonl`. The full Tier 1+2 cascade for that surface is asserted. Marked `e2e_data` per `e2e-cost-stratification-pattern` memory (zero credits). |

**`structlog` is already a dependency** (version `>=24.0.0` per `pyproject.toml`). `jsonschema` and `python-ulid` are added in Phase 0 (§8.0). Tests apply `bdd-stubs-mirror-runtime-signatures` — every event addition ships matching stub updates for `tests/features/_fake_t2i` / `_fake_video` / `_fake_batch` families.

---

## 10. Cross-Repo Consumer: gflow-cli-remotion (parallel)

The promo orchestrator's path-(b) implementation (its own spec) consumes the **existing Tier 3 dotted events** today (`ui_automation.entered_editor`, `image_batch.row_completed`). When Phase 1+2 of this event-surface land, the orchestrator migrates to Tier 1+2 events — same data, stable API, no recording invalidation. The cross-repo schema fixture (the `RunManifest` shape on the Remotion side) bumps from `schema_version: 1` to `schema_version: 2` at that point.

This decouples the two specs: this one can ship at gflow's pace; the promo orchestrator works today against Tier 3, upgrades later.

---

## 11. Security & Privacy

- **No secrets in events.** Allowlist-based: forbidden-field invariant test enforces it.
- **Prompts are hashed**, never plaintext in events. Aligned with `data-layer-overview` memory's existing redaction policy.
- **`run_id` is non-secret** but persistent within one invocation. Consumers that persist `run_id` can correlate across logs; this is the intent.
- **Stdout is the channel.** Operators who pipe gflow to a public log aggregator are responsible for downstream redaction. We don't ship a "safe for upload" alternative channel in v1.
- **Schema fixtures are public docs.** The JSON Schema files commit-in-repo are themselves part of the public API.

---

## 12. Open Questions / Risks

1. **ULID dependency.** Python's stdlib doesn't have ULID. Choices: (a) add `python-ulid` (~50 KB), (b) use UUIDv7 (Python 3.11+ doesn't include uuid7 in stdlib yet), (c) generate manually (20 lines). Recommendation: `python-ulid` — small, well-maintained, no in-repo cryptography risk.
2. **`video.generation.progress` cadence and backpressure.** Veo runs can be 3-5 min. Cadence policy: state-transition events fire immediately; same-state progress events are rate-limited to **at most 1 per 5 seconds** by a token-bucket in the emitter (deterministic, not based on stdout buffer fullness — Python can't introspect the OS pipe buffer). `*.completed` and `*.failed` are NEVER rate-limited.
3. **Existing tests bind to Tier 3 events.** Per `bdd-stubs-mirror-runtime-signatures` memory, tests stub event emissions. Adding new Tier 1/2 events means stub updates. Migration plan: every PR in Phase 1+2 ships its own stub updates.
4. **Backward compat of CHANGELOG.** Today the CHANGELOG has user-facing entries only. The event surface introduces a "Public API: Events" section. Existing users won't notice; net add.
5. **`auth.*` events on a multi-account machine.** A `profile` field is fine; an `email` field is not. The spec excludes `email`. But operators on shared machines may want to correlate events to a Google account — they'll have to maintain their own profile→email mapping out-of-band. By design.

---

## 13. Out of Scope for v1

- Internal pub/sub event bus refactor.
- Event filtering / sampling configuration (operators pipe through `jq`).
- Event persistence within gflow (the data layer persists artifacts, not events; events go to stdout only).
- Cloud streaming / event hubs.
- Plugin SDK — consumers write 30 lines of Python or Node to parse stdout JSON.
- Internationalization of event names (English-only).
- Binary event encoding (Protobuf / Avro).

---

## 14. Migration Plan

| Step | PR | Effort | Risk |
|---|---|---|---|
| 1 | Add ULID + `run_id` contextvar; add envelope processor; emit `command.*` events; write `docs/EVENT_SURFACE.md` scaffold; commit JSON Schema fixtures for `command.*`. | `feature/event-surface-foundation` | ~2 days | Low — additive. |
| 2 | Emit `image.generation.*` + add per-event JSON Schemas + tests. | `feature/event-surface-image` | ~1 day | Low. |
| 3 | Emit `video.generation.*` + progress event cadence + tests. | `feature/event-surface-video` | ~1-2 days | Medium — progress backpressure needs e2e validation. |
| 4 | Emit `batch.*` + tests. | `feature/event-surface-batch` | ~1 day | Low. |
| 5 | Emit `auth.*` + tests. | `feature/event-surface-auth` | ~1 day | Low. |
| 6 | Data-layer migration: replace `VideoStartedCallback` with event subscriber. | `feature/data-layer-event-driven` | ~2 days | Medium — touching paid-run path. Existing live-verification gate per `verification-ledger-5-layer`. |
| 7 | Stability declaration: tag `schema_version: 1` as stable in CHANGELOG + lock fixtures. | `feature/event-surface-stable` | ~30 min | None. |

Total: ~8-9 days of work spread across 7 PRs. Each PR is self-contained and ships to `develop` independently. None block the promo orchestrator's path-(b) implementation.

---

## Appendix A — Relationship to existing memory entries

**Directly honored:**
- `real-browser-auth-mandatory` — `auth.*.browser_strategy` literal is "chrome"; invariant documented in §5.4.
- `verification-ledger-5-layer` — Phase 3 (data-layer migration) is gated by the existing 5-layer ledger.
- `data-layer-overview` — Phase 3 augments (not replaces) the data layer's callback path.
- `on-started-callback-recorder-safety` — Phase 3 keeps the blocking callback for the pre-flight INSERT path; subscribers handle post-flight only (§8 Phase 3).
- `exit-code-map-ordering-invariant` — `command.failed.exit_code_class` cross-links the existing `test_exit_code_map_ordering_invariant`.
- `exit-code-16-data-store` — preserved by Phase 3's additive (not replacement) migration.
- `structlog-cache-logger-off-for-tests` — applies to all event-emission tests (`cache_logger_on_first_use=False`).
- `bdd-stubs-mirror-runtime-signatures` — every Phase 2 PR ships matching stub updates for `tests/features/_fake_*` families (named in §9).
- `image-t2i-jpeg-with-png-extension` — `image.generation.completed` fires AFTER the extension sniff/rename in `client.download_image()`.
- `gflow-strategy-local-first` — event surface is local-only; no cloud event hubs (§13 out of scope).
- `e2e-cost-stratification-pattern` — Phase 1+2 e2e captures are marked `e2e_data` (zero-credit). Phase 6 (live verification) is `e2e_image` / `e2e_video` per the marker registry.
- `pre-pr-verification-discipline` — every PR in §14's migration table follows: council + scoped pytest + live verify before `gh pr create`.
- `pr-must-verify-on-affected-surface` — Phase 2 PRs add events per surface (image, video, batch, auth); each PR live-verifies on the surface it touches, not a single golden surface.
- `data-layer-test-pollution-trap` — data-layer subscriber fixtures (Phase 3) MUST set `GFLOW_CLI_DB_PATH` to tmp before `get_settings()` caches.
- `video-result-wraps-status-trap` — `video.generation.completed` payload extracts from `VideoResult.status`, not `VideoStatus` directly.
- `branch-naming-convention` — migration PRs use `feature/event-surface-*` prefix (table §14).

**Post-ship consolidation:** per `release-spec-plan-memory-consolidation`, after Phase 7 (stability declaration) this spec is replaced by a `docs/EVENT_SURFACE.md` user-facing doc + a small memory entry pointing to it. This spec file is deleted from the repo per project policy.

---

## Appendix B — What this is NOT

This is not "event sourcing." gflow doesn't reconstruct state from an event log. Events are observability; state lives in the SQLite catalog (`gflow_cli.data`) and on disk.

This is not "make gflow a platform." gflow stays a focused local CLI per `gflow-strategy-local-first`. The event surface is a polite contract for consumers who choose to integrate; it doesn't change gflow's product identity.

This is not "rewrite for plugins." Plugins are out of scope. The event surface makes plugins *possible* later; it doesn't deliver them.
