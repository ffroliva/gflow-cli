# Plan — Direct HTTP Image Transport

**Status:** Gate 0, Phase 1, Phase 2 and Phase 3 implemented; Phase 4 deferred
**Branch:** `feature/direct-http-image-transport` (off `develop`)
**Spike evidence:** `chore/spike-direct-image-with-browser-token` — 5 commits proving
each component end-to-end. Findings doc:
[`docs/superpowers/spikes/2026-11-22-direct-image-with-browser-token.md`](../spikes/2026-11-22-direct-image-with-browser-token.md)
Gate 0 findings: [`docs/superpowers/spikes/2026-11-22-direct-image-gate0.md`](../../spikes/2026-11-22-direct-image-gate0.md).

## Goal

Replace the browser-driven image generation path with direct HTTP calls for
text-to-image (T2I), in-project image-to-image (I2I), and character-mention
generations. The browser stays only for reCAPTCHA token minting. Everything
else — cookies, page tokens, frame building, the generation POST, the download
— is direct HTTP.

**Why:** eliminates selector drift, locale sensitivity, and contenteditable
fragility for image generation. The UI transport clicks through a prompt editor,
waits for Angular renders, and parses a contenteditable document — the direct
path skips all of that. Latency improvement to be measured in Gate 0.

---

## Architecture

```
┌───────────────────────────────────────────────┐
│  CLI / MCP                                    │
│  generate_image(prompt, model, project, ...)  │
└──────────────┬────────────────────────────────┘
               │ FlowTransportStrategy.generate_images()
               ▼
┌──────────────────────────────────────┐
│  DirectHttpTransport                 │
│                                      │
│  0. can_serve(request)?              │  ← route unsupported shapes to UI
│  1. flow_wire session (reuse)        │  ← cookies + at/fsid/bl already in flow_wire.py
│  2. mint_recaptcha_token()           │  ← page.evaluate() — ONLY browser step
│  3. build_ogiz0b_frame(prompt, ...)  │  ← pure function, no captured state
│  4. httpx.post(batchexecute)         │  ← reuse flow_wire's httpx + headers
│  5. image_records(response)          │  ← existing parser in batchexecute.py
│  6. download signed URLs             │  ← httpx, no cookies needed
└──────────────────────────────────────┘
```

### Key reuse: `flow_wire.py` already exists

`api/flow_wire.py` (#1229, #1363) already does direct batchexecute against
`flow.google.com` for credits reads and media mutations. It has:
- Cookie loading via `auth.cookies.read_cookies_for()`
- Page token scraping (`at`/`SNlM0e`, `fsid`/`FdrFJe`, `bl`/`cfb2h`)
- httpx POST to `/_/AiSandboxAngularFrontend/data/batchexecute`
- `parse_frames()` and `rpc_errors()` from `batchexecute.py`
- Fallback reason enum, structlog events, proxy detection, PSIDTS freshness

**The direct image transport extends this infrastructure to the `ogiZ0b` RPC.**
No new cookie reader, no new token fetcher, no new httpx client. The only new
code is the frame builder (pure function) and the transport class that
orchestrates the existing pieces plus reCAPTCHA minting from the browser page.

Inherited constraints from `flow_wire.py`:
- Skipped while any proxy is configured (`getproxies()`)
- macOS default-off unless `GFLOW_CLI_WIRE_READS=1`
- PSIDTS freshness: works ~1.5–2.5 h after a browser visit
- One account's failure never affects another

### `can_serve()` gate — route before sending

Before building a frame, check whether the request is in the proven surface:

| Feature | can_serve? | Phase |
|---|---|---|
| Plain T2I, default aspect, count=1 | Yes (proven) | 1 |
| In-project I2I refs | Yes (format known) | 2 |
| Character mentions | Yes (format measured) | 2 |
| Non-default aspect ratio | **No until slot captured** | 2 (after T2.0) |
| Count > 1 | **No until semantics captured** | 2 (after T2.0b) |
| Local-file I2I (needs maseQ upload) | **No** | 4 (deferred) |

A request outside the proven surface is routed to `ui_automation` **before any
POST**. This is not a fallback — it is the primary routing decision. The direct
path never sends a frame it cannot fully build.

### Fallback policy

Fallback to UI only on:
- **Pre-send:** `can_serve()` returns false (the common case while features are phased in)
- **Definitive rejection:** HTTP 403 (reCAPTCHA challenge) from the POST

**No fallback on:** timeout, connection reset, HTTP 5xx. These are errors — log
them, raise, let the caller's retry/error path handle it. A timeout in
particular may mean the frame was billed.

**Never retry a sent ogiZ0b** — it may have been billed. On any post-send failure,
return the error.

---

## Proven in spike

| Component | Evidence | Finding |
|---|---|---|
| On-disk cookie reading | SQLite parse → httpx POST succeeded | `d24f1952` |
| Programmatic frame (template swap) | Prompt + token swap → generation | `634a991c` |
| From-scratch frame builder | 12-slot minimum viable, `inner[4]` = `[uuid]` not `uuid` | `ee7fa2d7` |
| Slot optionality | Slots 12-14 optional | `16ab3d4b` |
| Predict verdict | GO (7.4/10) | — |

## Open unknowns

| Unknown | Cost to settle | When |
|---|---|---|
| Aspect ratio slot position | 1 capture ($0, non-default aspect) | Gate 0 |
| Count > 1 semantics (entry[4]) | 1 capture ($0, count=2) | Gate 0 |
| WAF escalation at N=10 consecutive | 10 images (~$0, Imagen) | Gate 0 |
| Direct-vs-UI latency comparison | Same N=10 run, logged | Gate 0 |
| `maseQ` direct upload for local-file I2I | Research + 1 test | Deferred |

---

## Phased sequence

### Gate 0 — WAF spike + settle unknowns (design validation)

This gate can kill the design. Run it first, before any production code.

- [ ] **G0.1 — WAF escalation spike.** Using the spike branch scripts, run N=10
  consecutive T2I generations via direct httpx POST against one profile. Log each
  response status code, `mint_ms`, `post_ms`, `total_ms`. If Flow escalates
  (429, challenge, block), document the threshold. **STOP if N<5 triggers a block.**

- [ ] **G0.2 — Latency comparison.** In the same run, compare the N=10 direct times
  against N=10 UI-transport times for the same prompt. This is the performance
  case the user asked for. Record the numbers.

- [ ] **G0.3 — Aspect ratio capture.** One generation ($0) with `--aspect 16:9`.
  Diff the ogiZ0b frame against the default-aspect capture. Identify the slot.

- [ ] **G0.4 — Count capture.** One generation ($0) with count=2. Confirm whether
  entry[4] controls count. Note whether count=2 is one POST returning 2 images
  or two separate POSTs.

- [ ] **G0.5 — Write finding doc.** Consolidate all spike + Gate 0 results into
  `docs/superpowers/spikes/2026-11-22-direct-image-with-browser-token.md`.

**Gate 0 exit criteria:** WAF allows ≥5 consecutive, latency numbers recorded,
aspect and count slots identified or documented as unknowable.

### Phase 1 — Core direct transport (T2I only)

The smallest slice that works end-to-end: plain text-to-image with default
aspect and count=1.

- [ ] **T1.1 — Frame builder.** `src/gflow_cli/api/transports/frame_builder.py`:
  `build_ogiz0b_frame(prompt, model, project_id, recaptcha_token) -> str` — pure
  function, builds the batchexecute body from scratch (12-slot minimum). No
  captured templates, no file I/O. No optional parameters until their slots are
  proven in Gate 0. Returns the full `f.req` form-encoded body ready for httpx.

- [ ] **T1.2 — `can_serve()`.** A module-level function in `direct_http.py`:
  `direct_can_serve(request: GenerateImageRequest) -> bool`. Returns `True` only
  for plain T2I with default aspect and count≤1. Extended in Phase 2.

- [ ] **T1.3 — DirectHttpTransport class.**
  `src/gflow_cli/api/transports/direct_http.py`: implements
  `FlowTransportStrategy.generate_images()`. Steps:
  1. `direct_can_serve(request)` — if false, raise `DirectTransportUnavailableError`
     (the caller in `FlowApiClient` catches and routes to `ui_automation`)
  2. Acquire a `WireSession` from `flow_wire` (cookies + tokens, reusing the
     existing infrastructure; add `ogiZ0b` to `_ALLOWED_RPCIDS` or factor out
     the session acquisition)
  3. `page.evaluate("grecaptcha.enterprise.execute(site_key, {action})")` — the
     browser `Page` comes from the existing `FlowApiClient` context
  4. `build_ogiz0b_frame(prompt, model, project_id, recaptcha_token)`
  5. httpx POST to `_BATCHEXECUTE` (reuse `flow_wire`'s headers and timeout pattern)
  6. `image_records(response)` → `list[GeneratedImage]`
  7. Download signed URLs via httpx

  The transport does NOT handle video, scene, or any non-image operation. Those
  stay on `ui_automation` unconditionally.

- [ ] **T1.4 — Transport registration.** Add `"direct_http"` to `_registry()` in
  `transports/__init__.py`. Add to `EXPERIMENTAL_TRANSPORTS`. Do NOT add to
  `STANDALONE_ONLY_TRANSPORTS` — it needs the FlowApiClient's browser for
  reCAPTCHA minting.

- [ ] **T1.5 — Tests (offline).**
  - Frame builder: assert slot structure, project_id placement, `inner[4]` = `[uuid]`
  - `can_serve`: true for plain T2I, false for refs/mentions/non-default aspect
  - Transport orchestration: mocked wire session + mocked `page.evaluate` + mocked httpx
  - BDD: "Given a valid profile, when I generate a T2I image with `--transport
    direct_http`, then the image is generated via direct HTTP and saved to disk"

- [ ] **T1.6 — E2E test.** `tests/e2e/test_direct_image_e2e.py`, marker `e2e_image`
  ($0 tier). One T2I generation with `--transport direct_http`. Verify the asset
  record's `metadata_json` contains `"transport": "direct_http"`.

- [ ] **T1.7 — MCP parity.** The MCP `gflow_generate_image` tool calls
  `generate_images()` on whatever transport is active. But the queued MCP path
  goes through `FlowWorker`, which is different code from the CLI's direct call.
  Add one MCP-path test that `direct_http` works through the worker. Verify the
  returned schema matches the CLI path.

### Phase 2 — Full image surface (I2I, mentions, aspect, count)

Wire the features whose slots are now known from Gate 0.

- [ ] **T2.1 — In-project I2I refs.** Wire `refs` parameter in the frame builder:
  `entry[2] = [[media_id, null, null, null, 1], ...]` (format known from
  research). Update `can_serve()` to accept requests with `reference_images`.
  The `GenerateImageRequest.reference_images` already carries media IDs.

- [ ] **T2.2 — Character mentions.** Wire `mentions` parameter: prompt doc changes
  in `entry[8]` + entity IDs in `entry[10]`. Update `can_serve()`. The
  `NameResolver` already resolves `@Character` to entity IDs.

- [ ] **T2.3 — Aspect ratio.** Wire the slot discovered in G0.3. Map from the CLI's
  `--aspect` values to the wire format. Update `can_serve()`.

- [ ] **T2.4 — Count.** Wire the slot discovered in G0.4. Update `can_serve()`.

- [ ] **T2.5 — Tests.** Unit tests for each new frame builder path. BDD scenarios:
  I2I with in-project ref, character mention, non-default aspect, count=2.
  One e2e test per new path (all $0 tier).

### Phase 3 — Observability and hardening

- [ ] **T3.1 — Telemetry.** Add `note_transport(kind: str)` to `telemetry.py`
  following the `note_engine` / `note_host` pattern. Emit `("transport",
  "direct")` or `("transport", "ui")` per generation.

- [ ] **T3.2 — Structlog events.** Three events:
  `direct.frame_built` (prompt hash, model, slot count),
  `direct.image_post` (elapsed_ms, status_code),
  `direct.fallback_to_ui` (reason). Via existing `structlog.get_logger()`.

- [ ] **T3.3 — DataStore metadata.** Add `"transport": "direct_http"` to the
  `metadata_json` dict in `data/_repo_assets.py::upsert_asset()`. No schema
  migration — it is a JSON column. One line change.

- [ ] **T3.4 — Performance fields.** Log `mint_ms`, `post_ms`, `total_ms` as
  structlog fields on `direct.image_post`. These feed the comparison report
  from Gate 0.

- [ ] **T3.5 — Fallback wiring.** On HTTP 403 from the direct POST: log
  `direct.fallback_to_ui`, raise `DirectTransportUnavailableError` so the
  `FlowApiClient` routes to `ui_automation` for that generation. On timeout
  or 5xx: raise the existing `NetworkError` — no fallback, the frame may have
  been billed.

- [ ] **T3.6 — Tests.** Fallback path test (mocked 403 → verify UI transport
  invoked). Timeout test (mocked timeout → verify error, no fallback).
  Performance field assertions.

### Phase 4 — Local-file I2I via maseQ (deferred, follow-up issue)

Local-file I2I (`--initial-frame local.png`) currently uploads via the browser.
The `maseQ` RPC is a separate upload with its own reCAPTCHA token. `can_serve()`
routes these to UI until this phase ships.

- [ ] **T4.1 — Spike: maseQ direct upload.**
- [ ] **T4.2 — Upload module.** `upload_image_direct(file, cookies, tokens) -> media_id`.
- [ ] **T4.3 — Wire into DirectHttpTransport + update `can_serve()`.**
- [ ] **T4.4 — Tests.**

---

## Concurrency, batch, and parallel execution

Research findings from the codebase analysis:

| Concern | Current state | Direct HTTP impact |
|---|---|---|
| **`gflow run` (batch)** | Sequential per prompt, calls `generate_images()` in a loop | Direct HTTP plugs in at the transport level — no loop changes. Each iteration mints its own reCAPTCHA token |
| **`gflow image batch`** | Sequential, same loop | Same — transport-level swap, no structural change |
| **`"ref": "batch:N"`** | In-batch cross-reference resolves to a media_id before the next generation | Works with direct HTTP — the returned `GeneratedImage` carries the media_id |
| **Local-file rows in batch** | Rows with a local `--initial-frame` need `maseQ` upload | Routes to UI via `can_serve()` until Phase 4. The batch loop already handles per-row transport decisions |
| **ProfileLease** | Per-profile, held by `FlowApiClient` | Unchanged — the browser stays open for reCAPTCHA minting, so the lease is still needed |
| **MCP per-profile lock** | `asyncio.Lock` per profile in the MCP server | Unchanged — serializes requests to the same profile |
| **Two processes, same profile** | ProfileLease prevents concurrent access | Unchanged |
| **Token minting** | `page.evaluate()` serializes naturally on one Page | One token per generation, sequential — no pooling |
| **Token expiry during long batches** | — | Tokens valid ~2 min; sequential minting per-call is fine |

**Decision:** no structural changes to concurrency or batch. The direct transport
is a drop-in replacement at the `generate_images()` interface. The existing
serialization (ProfileLease, MCP lock, sequential batch loop) all work as-is.

---

## New / touched files

```
src/gflow_cli/api/transports/
├── frame_builder.py   # NEW — ogiZ0b frame constructor (pure function)
├── direct_http.py     # NEW — DirectHttpTransport + can_serve()
└── __init__.py        # EDIT — register direct_http in _registry()

src/gflow_cli/api/flow_wire.py     # EDIT — factor out session acquisition or add ogiZ0b to allowed RPCs
src/gflow_cli/telemetry.py         # EDIT — add note_transport()
src/gflow_cli/data/_repo_assets.py # EDIT — transport key in metadata_json (1 line)

tests/
├── transports/
│   ├── test_frame_builder.py
│   └── test_direct_http.py
└── e2e/
    └── test_direct_image_e2e.py   # e2e_image tier ($0)

docs/
├── superpowers/spikes/2026-11-22-direct-image-with-browser-token.md  # Gate 0 findings
└── DIRECT_TRANSPORT.md            # user-facing doc
```

No new cookie reader — reuse `auth.cookies.read_cookies_for()`.
No new token fetcher — reuse `flow_wire`'s page token scraping.
No new runtime deps — `httpx` and `sqlite3` (stdlib) are already present.

---

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| WAF escalation on consecutive direct POSTs | Medium | **Gate 0** — measured before any production code |
| Cookie/PSIDTS expiry mid-batch | Low | Per-generation check; `DirectTransportUnavailableError` → route to UI |
| ogiZ0b frame format changes server-side | Medium | Frame builder is a pure function, easy to update; `image_records()` catches malformed responses |
| reCAPTCHA enterprise blocks `page.evaluate` | Low | Same mechanism the UI transport uses — if it breaks, both break |
| Double-send on billed op | High impact | Never retry a sent ogiZ0b; on timeout, raise error |
| Unsupported feature silently sent with defaults | High impact | `can_serve()` gate — route before POST, never after |
| `ImageGenerationRecord` vs `GeneratedImage` type mismatch | Medium | The direct path returns `GeneratedImage` (the DTO); verify the conversion matches what `ui_automation` returns |

---

## Definition of Done

- [ ] Gate 0 passed: WAF allows ≥5 consecutive, latency numbers recorded, aspect and count slots known.
- [ ] `gflow image t2i --transport direct_http "a cat"` generates via direct HTTP, saves to disk.
- [ ] In-project I2I (`--ref <media_id>`) works via direct HTTP.
- [ ] Character mentions (`"a cat @Character"`) work via direct HTTP.
- [ ] `can_serve()` routes unsupported shapes (local-file I2I, unknown slots) to UI before POST.
- [ ] MCP `gflow_generate_image` with transport=direct_http works through the FlowWorker.
- [ ] `gflow run` (batch) works with direct_http — including `"ref": "batch:N"` cross-references.
- [ ] Telemetry records `transport: direct` / `transport: ui` in the weekly report.
- [ ] Fallback to UI on HTTP 403 is logged and works; timeout/5xx raises, no fallback.
- [ ] All quality gates: ruff, ruff format, pyright (0), pytest (≥80%), `/gflow:check`.
- [ ] E2E: `pytest -m e2e_image` passes with direct_http as the transport.
- [ ] Performance comparison: direct vs ui_automation over ≥10 runs, numbers in finding doc.
- [ ] `docs/DIRECT_TRANSPORT.md` written, indexed in `docs/INDEX.md`.
- [ ] Phase 4 (local-file I2I via maseQ) tracked as a follow-up issue.

---

## Decisions

1. **Gate 0 first.** WAF escalation is predict risk #1 and can kill the design.
   Measure it before writing production code.

2. **Reuse `flow_wire.py`.** No new cookie reader, no new token fetcher. The direct
   image transport is a new RPC (`ogiZ0b`) on the existing wire infrastructure.

3. **`can_serve()` gate.** Unsupported features route to UI before any POST.
   The direct path never sends a frame it cannot fully build.

4. **Experimental first.** Start behind `EXPERIMENTAL_TRANSPORTS` flag. Promote to
   default after Gate 0 + 50 successful generations across profiles.

5. **No local-file upload in v1.** `maseQ` is a separate RPC with its own
   complexity. In-project I2I covers the common case; local-file deferred to
   Phase 4 / follow-up issue.

6. **Fallback is pre-send or definitive only.** A timeout or 5xx after the POST
   is an error, not a fallback trigger — the frame may have been billed.

7. **One reCAPTCHA token per generation.** No token pooling or pre-minting.
   Sequential minting from the same page is reliable and simple.

8. **Images only.** The direct transport implements `generate_images()` only.
   Video, scene, and all other operations stay on `ui_automation` unconditionally.
   `make_transport` picks one transport for the client, so the DirectHttpTransport
   must delegate non-image operations to the UI transport (or the routing must
   happen at the `FlowApiClient` level per-operation).

9. **Hexagonal / DDD / Strategy pattern — no god classes.**
   - The transport layer is a **port** (`FlowTransportStrategy` protocol) with
     multiple **adapters** (`ui_automation`, `direct_http`, the experimentals).
     Each adapter owns its own I/O; the domain (`GenerateImageRequest` →
     `GeneratedImage`) stays transport-agnostic.
   - The `can_serve()` gate is a **strategy selector**, not a monolith branch.
     Each transport declares what it can serve; the routing layer picks the
     right adapter per request. This is CQRS-adjacent: the read path (cookies,
     tokens) is separated from the write path (frame build, POST).
   - The frame builder is a **pure domain function** — no I/O, no state, no
     browser dependency. It takes domain types in and returns wire bytes out.
   - `flow_wire.py` is infrastructure — it stays behind the port boundary.
     The direct transport calls it; it never calls the transport.
   - No class accumulates unrelated responsibilities. If `DirectHttpTransport`
     starts growing (upload, polling, download), split by responsibility along
     the domain boundary, not by size.
   - TDD with `/ub-mode` is the execution discipline. Failing test first, then
     the smallest implementation that passes. If we hit errors:
     `superpowers:systematic-debugging` — root cause before any fix attempt.

---

## Execution record (2026-11-23)

| Item | Outcome |
|---|---|
| Gate 0 | Passed: 10/10 consecutive direct POSTs; direct median 18.6 s vs UI 20.7 s; `entry[4]` is the aspect enum (1:1=1, 9:16=2, 16:9=3, 3:4=4, 4:3=5); count is N entries in one POST |
| Cookie source | The on-disk store is unreadable while Chrome holds the profile (`auth/cookies.py`), so the transport reads the live browser context; `flow_wire` supplies the shell tokens and user agent |
| Mint | `FlowApiClient`'s mint refuses the migrated host and uses the labs action, so the transport mints `IMAGE_GENERATION` itself and retries while reCAPTCHA loads |
| Model | `NARWHAL` is sent as `BELUGA` (#958); other models route to the UI transport |
| T2.1 / T2.2 | One in-project ref, or one character id with its name; both also ride as a chip in `entry[8]` |
| T3.3 | Recorded by the image recorder from `GeneratedImage.transport`, not by `upsert_asset` (which never sees the transport) |
| Live e2e | `tests/e2e/test_direct_image_e2e.py` (t2i, 16:9 x2, model routing, in-project I2I, character) and `test_direct_image_worker_e2e.py` (queued MCP path) pass on a `flow.google.com` profile |

A bare `@Name` mention is served: the id is read from the project's own reply.
Count 1-4 is served (N entries returned N images live). Not served directly yet: several refs or characters, local-file refs (Phase 4), batch runs (`image_batch`
calls the UI transport's batch method). The weekly telemetry field (T3.1) is not in this change: it went out for every
image user and needed a notice-version bump that stops EU/EEA uploads until re-consent,
so it ships together with the endpoint change. T3.3 (per-image metadata) is in.
