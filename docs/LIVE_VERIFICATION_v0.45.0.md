# Live verification — v0.45.0

> Evidence that v0.45.0's user-facing changes were exercised against **real**
> Google Flow, not just mocked tests. Profile `ffroliva`, 2026-07-27.
> See [E2E_TESTING.md](E2E_TESTING.md) for the marker/cost model.

Offline gates for the release: `ruff` clean, `pyright src` 0 errors, full
suite green (see §5).

---

## 1. `image i2i --ref <UUID>` reference binding (#393) — ✅ VERIFIED

The field report claimed a UUID `--ref` was **silently** dropped and the
generation "reported success" without the reference. Live investigation
disproved the silence and found the real defect.

### 1.1 What Flow actually does (measured, not assumed)

| Probe | Observation |
|---|---|
| `--ref <uuid>` of a recent in-project asset | Tile found in the picker's first viewport → `image_ref_selected_existing`, exit 0 |
| `--ref <uuid>` of an older in-project asset | Both UUID search tiers `found: false`; the **scroll fallback** located it → exit 0 |
| `--ref <uuid>` that cannot exist | Search tiers miss → scroll misses → **exit 9**, `TransportTimeoutError`. **Not silent.** |
| Picker DOM dump (`debug_picker_dom_*.json`) | Tiles carry `img src=…?name=<uuid>` **and** a short Flow-authored caption (`alt="Box tied with crimson ribbon"`) — **not** the generation prompt |
| HAR capture of `batchGenerateImages` | Response confirms the caption source: `workflows[0].metadata.displayName = "Calm woman natural light portrait"` |

Consequences for the fix: UUID-based tile matching is exact and stays; a
**prompt-derived search hint cannot work** on this surface (tried live, returned
zero tiles) and was therefore dropped from the change rather than shipped on the
strength of a plausible-sounding assumption.

### 1.2 Controlled A/B — identical input, fix toggled

Ref `61a0423e-…` (owned by the profile, cataloged with a local file, living in a
**different** project so its tile is unreachable from the target project's
picker):

| Build | Result |
|---|---|
| Pre-fix (fix stashed) | `existing_asset_not_found` → **exit 9**, `image_ref_upload_fallback` fired **0** times |
| Post-fix | `existing_asset_not_found` → **`image_ref_upload_fallback`** → **exit 0**, 774 KB `.jpg` written |

The 5 layers: file written (774 KB) · `.jpg` payload on disk · picker-miss and
upload-fallback events in the structlog stream · non-zero → zero exit-code flip
on identical input · the generated image is user-inspectable in the scratch
project.

### 1.3 Automated coverage

- `tests/cli/test_cli_image_uuid_ref_enrichment.py` — 8 unit tests (catalog hit,
  missing file, absent file record, unknown asset, catalog failure, mention
  display-name preserved, single catalog open for 10 refs, no open for zero refs).
- `tests/e2e/test_image_uuid_ref_e2e.py` — **2 live scenarios, both passing**:
  - `test_e2e_unresolvable_uuid_ref_fails_loud` — the anti-silence guard: an
    unbindable ref must abort non-zero and write no generated image (0 credits).
  - `test_e2e_cross_project_uuid_ref_falls_back_to_upload` — seed image in
    project A, referenced from a fresh project B, rescued by the upload fallback
    (2 Imagen credits).

---

## 2. `character create` binding + `--format-prompt` (#395, #383) — ✅ VERIFIED

Originally recorded here as *not verifiable this cycle*. That was wrong in an
important way: the cause was **ours**, not Flow's, and the spike below found it.

### 2.1 What the spike established

Drove Flow's own character flow in a CDP-attached real Chrome
(`gflow-agent-browser-spike`, `navigator.webdriver: false`) with full
request/response capture, then diffed it against gflow's traffic. The two
requests were byte-comparable except for one block:

```jsonc
"mediaGenerationContext": {
  "entityContext": { "entityId": "…", "characterSlot": { "imageReferenceIndex": 0 } }
}
```

With it, the response carries `parentEntityId` and the portrait binds; without
it, Flow files a plain project image. Contract documented in
[CHARACTER_RECON](CHARACTER_RECON.md#entity-binding-entitycontext-captured-live-2026-07-28).

### 2.2 Two defects, both ours

| # | Defect | Evidence |
|---|---|---|
| 1 | Overlay dismissal pressed **Escape** on Flow's own composer — `[role='dialog']`/`[role='alert']` matched the app itself — so the submit lost `entityContext` | Removing those two selectors made the identical command bind on the first try (`entity_patched`, real `thumbnail_media_id`); with them present it failed every run |
| 2 | The character route can **bounce** back to the project page (entity not yet queryable after `createEntity`); the project page also mounts a prompt box, so the readiness gate passed on the wrong surface and the prompt went into the **project** composer | `character_editor_ready` logged with the *project* URL; the new guard logs `character_route_bounced` → `character_route_settled` and the URL is now the character route |

A third symptom seen on 2026-07-27 — Flow's app crashing on that route — was
real but intermittent and is now reported as the typed retryable `FlowAppError`
(§3).

### 2.3 Proof

- `gflow character create --name "Overlay Hypothesis"` → `"status": "ok"`,
  `character_create.entity_patched`; read-back shows the character with its name
  **and** `thumbnail_media_id: fdaea0d2-…` — the first fully successful create
  since the breakage.
- `tests/e2e/test_character_create_e2e.py::test_character_create_binds_parent_entity`
  — **passes live** (this is the #395 contract: entity bound, same project,
  thumbnail attached).
- `tests/e2e/test_character_create_e2e.py::test_character_create_format_prompt_clicks_format_button`
  — **passes live**, asserting the `ui_automation.prompt_formatted` event fired
  (visible **and** enabled button clicked) and that neither skip-telemetry event
  appeared, so a no-op flag cannot pass as success. This closes the
  `--format-prompt` (#383) verification that was owed since 2026-07-26.

A harness bug was fixed on the way: `_character_env` inherited the isolated
`GFLOW_CLI_HOME`, so every subprocess test in that file exited 2 with
"No session" before reaching Flow.

## 3. Error-classification fixes — ✅ VERIFIED (observed live, unit-pinned)

| Change | Live basis |
|---|---|
| Flow app crash at the character-editor gate → typed retryable `FlowAppError` (exit 31) instead of a bare `RuntimeError` naming a selector | Incident `ui.json` `title.category: flow_app_crash` (§2.1) |
| Character binding failure message now states the image was filed as a plain project image | HAR + read-back showing unbound "Untitled Character" entities (§2.1) |

Unit coverage: `test_flow_app_crash_raises_typed_retryable_error`,
`test_non_crash_still_raises_selector_runtime_error`, plus tightened assertions
in `tests/api/test_client_generate_character.py` (the previous
`assert … or exc_info.value.detail` was vacuous — always truthy).

---

## 4. Not exercised this cycle

- **Veo/video paths** — no video generation was run; this release changes none.
- **Top banner & modal dismissal (#369)** — exercised incidentally (overlay
  detect/dismiss events fired during the image runs above) but not
  purpose-tested against a real "What's new" banner, which cannot be summoned on
  demand.
- **Self-documenting error remediation (#380)** — the remediation hint was
  observed on a real `TransportTimeoutError` payload during §1.1; the full
  matrix of exception classes is unit-covered only.
