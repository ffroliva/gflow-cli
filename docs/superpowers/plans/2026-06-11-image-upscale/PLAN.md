# Image Upscale Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature image-upscale` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Add `gflow image upscale <mediaId> --scale 2k|4k` so a user can fetch Flow's
server-side 2K/4K upscaled variant of a platform-generated image and save it locally.

**Architecture:** Reuses the existing image-generation reCAPTCHA browser transport — same host
(`aisandbox-pa.googleapis.com`) and `recaptchaContext` shape as `batchGenerateImages`. New work is
glue: a route constant, a frozen request value object, a synchronous `FlowApiClient.upsample_image()`
that mints a reCAPTCHA token, POSTs, and decodes the inline `{encodedImage: base64}` (mirroring
`concatenate_scene`'s cap + `del` + magic-byte pattern), a new exit-coded error, and a Click
subcommand. No transport-Protocol change (it's a client-level op like `concatenate_scene`). No new
auth, no new persistent state, no async poll loop.

**Tracking issue:** [#171](https://github.com/ffroliva/gflow-cli/issues/171)

**Predict verdict:** GO — confidence 7.5/10. Recon: `docs/IMAGE_UPSCALE_RECON.md`. Scenario matrix
complete (20 scenarios; 12 Critical/High are must-cover).

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Critical | 403 ambiguity: tier-gate (4K/Pro) vs WAF heat vs expired token all return 403 | Disambiguate by response context; `UpscaleUnavailableError` (exit 22) for tier; **never auto-retry** a 4K 403 |
| Critical | ~5 MB `encodedImage` base64 leaks into logs / error context | Never log the field; decode then `del`; structlog carries size only |
| High | Pathological oversized base64 (4K PNG) → OOM | `MAX_UPSAMPLE_B64_LEN` (~50 MB) cap **before** decode; typed error |
| High | Response missing `encodedImage` / decodes to non-image | `WireFormatError` (redacted discovery payload); PNG/JPEG magic-byte check |
| High | Per-profile reCAPTCHA/WAF heat (a 4th gated op) | One upscale per invocation; no implicit batch; document the heat |
| High | mediaId injection / malformed input | UUID allowlist; reject before any request fires |
| Medium | Selector drift (download button / scale menu) on Flow UI update | Structural-first, locale-neutral selectors (`download`, `2K`/`4K`); re-capture via spike |

---

## File structure

### New files
```
src/gflow_cli/api/image_upscale.py        (or extend api/image.py)
  UpsampleImageRequest frozen dataclass + TargetResolution enum (from_cli "2k"/"4k")
tests/api/test_image_upscale.py
  Unit tests: request build, response parse (cap/magic-byte/missing-field), 403 disambiguation
tests/features/image_upscale.feature
  BDD scenarios from /gflow:scenario (happy 2K, 4K-on-Pro, oversized, malformed mediaId)
tests/features/test_image_upscale_steps.py
  Step defs mirroring runtime signatures
```

### Modified files
```
src/gflow_cli/api/routes.py
  + UPSAMPLE_IMAGE = f"{FLOW_API_BASE}/flow/upsampleImage"
src/gflow_cli/api/client.py
  + MAX_UPSAMPLE_B64_LEN; + async def upsample_image(media_id, target_resolution, out_path)
    (mint reCAPTCHA via _mint_recaptcha_token; POST; cap+decode+magic-byte; write via image_output_path)
src/gflow_cli/errors.py
  + UpscaleUnavailableError (exit 22) registered in EXIT_CODE_MAP; distinct from WAF/reCAPTCHA 403
src/gflow_cli/cli_image.py
  + `upscale` Click command: <mediaId> arg, --scale Choice(['2k','4k']), --out, --profile
src/gflow_cli/paths.py
  (reuse image_output_path; add <mediaId>_<scale> naming if needed)
docs/USAGE.md, README.md, docs/INDEX.md, CHANGELOG.md
  document the command + the platform-only / Ultra-only constraints
```

---

## Task 1 — Error type + exit code (test-first)

**What:** Add `UpscaleUnavailableError` (exit 22) for tier-gated/permission failures, distinct from WAF/reCAPTCHA 403.

**Files:**
- `tests/test_errors.py` — assert exit code 22, EXIT_CODE_MAP registration, ordering invariant
- `src/gflow_cli/errors.py` — new class + map entry

**Steps:**
- [ ] Red test: `UpscaleUnavailableError` maps to 22; not equal to WAF 403 code
- [ ] Add the class (RFC 9457 fields: type/title/status/detail/remediation_hint) + register in `EXIT_CODE_MAP`
- [ ] Verify `test_exit_code_map_ordering_invariant` still passes (inheritance trap — see memory)

**Tests created (red):**
- [ ] `test_upscale_unavailable_exit_code` — exit 22, distinct from WAF/reCAPTCHA

---

## Task 2 — Value objects (test-first)

**What:** `TargetResolution` enum (`2K`/`4K` ↔ wire `UPSAMPLE_IMAGE_RESOLUTION_*`, `from_cli`) and frozen `UpsampleImageRequest`.

**Files:**
- `tests/api/test_image_upscale.py` — enum mapping, `from_cli` rejects bad values, request immutability
- `src/gflow_cli/api/image.py` (or new `image_upscale.py`) — enum + dataclass + body builder

**Steps:**
- [ ] Red tests: `TargetResolution.from_cli("2k")` → enum; `"1k"`/`"8k"` rejected; request frozen
- [ ] Add enum + `UpsampleImageRequest(media_id, target_resolution, recaptcha_token="")`
- [ ] Body builder → `{mediaId, targetResolution, clientContext:{recaptchaContext:{token}}}`
- [ ] mediaId UUID-allowlist validation (reject malformed before request)

**Tests created (red):**
- [ ] `test_target_resolution_from_cli` / `test_target_resolution_rejects_1k_and_unknown`
- [ ] `test_upsample_request_body_shape`
- [ ] `test_media_id_uuid_validation`

---

## Task 3 — Client `upsample_image()` (test-first)

**What:** Synchronous client method: mint reCAPTCHA, POST `upsampleImage`, parse `{encodedImage}` with cap + magic-byte, write file. Route constant added.

**Files:**
- `tests/api/test_image_upscale.py` — mocked transport/HTTP: happy path, oversized cap, missing field, non-image bytes, 403→typed error, no-retry on 4K 403, `encodedImage` never logged
- `src/gflow_cli/api/routes.py` — `UPSAMPLE_IMAGE`
- `src/gflow_cli/api/client.py` — `MAX_UPSAMPLE_B64_LEN = 50 * 1024 * 1024`; `upsample_image(...)`

**Steps:**
- [ ] Red tests for: 200 happy (decode → PNG/JPEG bytes written); response > cap → typed error before decode; missing `encodedImage` → `WireFormatError` (redacted); decoded bytes fail magic-byte → typed error
- [ ] Red tests for 403 disambiguation: tier 403 → `UpscaleUnavailableError` (no retry); WAF 403 → existing `WafRejectionError`; expired-token 401 → existing refresh+single-retry
- [ ] Red test: structlog capture asserts `encodedImage` value never present in any event
- [ ] Add route + method; reuse `_mint_recaptcha_token("upscaleImage" or correct action)`; single Page checkout (no nested checkout — use `context.request` if aux call needed)
- [ ] Decode mirrors `concatenate_scene` (cap → `b64decode` → magic-byte → `del encoded`)
- [ ] Write via `paths.image_output_path` as `<mediaId>_<scale>.<ext>` (ext from magic byte)

**Tests created (red):**
- [ ] `test_upsample_happy_2k`, `test_upsample_oversized_rejected`, `test_upsample_missing_field`,
      `test_upsample_non_image_bytes`, `test_upsample_4k_pro_403_no_retry`,
      `test_upsample_waf_403_distinct`, `test_upsample_encoded_image_never_logged`

---

## Task 4 — CLI surface (test-first)

**What:** `gflow image upscale <mediaId> --scale 2k|4k [--out] [--profile]` with self-contained `--help`.

**Files:**
- `tests/test_cli_image.py` — Click invocation: arg/flag parsing, `--scale` Choice, exit codes, 4K/Pro hint, malformed mediaId usage error (no request), structlog `upscale_started/completed/unavailable`
- `src/gflow_cli/cli_image.py` — `upscale` command

**Steps:**
- [ ] Red tests: happy invocation calls `upsample_image` with mapped enum; `--scale 8k` rejected by Click; `--scale 1k` rejected with "1k is the original" hint; malformed mediaId → usage error, no HTTP
- [ ] Red test: 4K-on-Pro path surfaces exit 22 + Ultra-only remediation; no auto-retry
- [ ] Implement command; emit `upscale_started/completed/unavailable` (stable keys); `--help` states platform-only + Ultra-only constraints + "find mediaId via `gflow data list images`"
- [ ] Record the output asset via `OperationRecorder` honoring the redaction gate; wrap callback in try/except `DataStoreError`

**Tests created (red):**
- [ ] `test_upscale_cli_happy`, `test_upscale_cli_scale_choice`, `test_upscale_cli_1k_rejected`,
      `test_upscale_cli_bad_media_id`, `test_upscale_cli_4k_pro_exit22`

---

## Task 5 — BDD scaffold + green

**What:** Gherkin feature covering all Critical+High scenarios; step defs mirror runtime signatures.

**Files:**
- `tests/features/image_upscale.feature` — happy 2K, 4K-on-Pro, oversized, malformed mediaId
- `tests/features/test_image_upscale_steps.py` — typed step defs (mirror signatures — see memory)

**Steps:**
- [ ] Add the four `/gflow:scenario` BDD scenarios as a feature file
- [ ] Implement step defs against the real command; assert exit codes + redaction
- [ ] All BDD green

**Tests created:**
- [ ] BDD: 2K happy, 4K/Pro exit 22, oversized rejected, malformed mediaId usage error

---

## Task 6 — Docs + recon finalize

**What:** User-facing docs + cross-references; recon already written.

**Files:**
- `docs/USAGE.md` — `gflow image upscale` section (workflow: `data list images` → `upscale`)
- `README.md` command table; `docs/INDEX.md` route to `IMAGE_UPSCALE_RECON.md`
- `docs/KNOWN_ISSUES.md` — note 4K Ultra-gating + per-profile WAF heat from the extra gated op
- `CHANGELOG.md` `[Unreleased]` — `feat(image): upscale to 2K/4K (#171)`

**Steps:**
- [ ] Write usage + constraints (platform-only, Ultra-only, credit-free expectation)
- [ ] Index the recon doc; cross-link spikes
- [ ] CHANGELOG entry referencing #171

---

## Task 7 — Full gates + PR

**What:** Green `/gflow:check`; open PR to `develop`.

**Steps:**
- [ ] `/gflow:check` green (ruff + format + `pyright src` + pytest ≥ 80% on changed dirs)
- [ ] Live smoke (opt-in, supervised): one real 2K upscale on a known mediaId (credit-free) confirms wire still matches
- [ ] PR `feature/image-upscale` → `develop`, `Closes #171`

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` updated (`#171`)
- [ ] Docs updated (`USAGE.md`, `README.md`, `INDEX.md`, `KNOWN_ISSUES.md`)
- [ ] BDD feature covers all Critical + High scenarios from `/gflow:scenario`
- [ ] `encodedImage` proven absent from logs (test) — Critical mitigation verified
- [ ] 4K/Pro 403 → exit 22, no auto-retry — Critical mitigation verified
- [ ] No `# TODO` in diff without a tracked issue link
