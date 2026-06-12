# Scenario: `gflow image upscale <mediaId> --scale 2k|4k`

> `/gflow:scenario` output for issue #171. Produced pre-implementation, then
> revised against the **live-verified** wire (see `docs/IMAGE_UPSCALE_RECON.md`).
> Feeds the test matrix in `tests/api/test_client_upscale.py`,
> `tests/cli/test_cli_image_upscale.py`, and `tests/features/image_upscale.feature`.

## Coverage map

**Active dimensions:**
- **D1 Auth & session lifecycle** — Bearer/session staleness observed live
  (`ACCESS_TOKEN_REFRESH_NEEDED`); the existing refresh-on-401 path applies.
- **D2 WAF / reCAPTCHA** — the gating mechanism. Action `IMAGE_GENERATION` is
  mandatory; per-profile heat is the dominant operational risk.
- **D3 Selector drift** — download button + 1K/2K/4K menu (locale-neutral).
- **D6 Data layer** — the catalog resolves the owning `projectId` from `mediaId`.
- **D7 Error propagation & exit codes** — 403 disambiguation, base64 redaction.
- **D8 Cross-platform paths** — `<mediaId>_<scale>.<ext>` output, Windows.
- **D9 Transport edge cases** — `encodedImage` parse/cap/magic-byte.
- **D11 Input validation** — mediaId/projectId UUID, `--scale`, 4K/Pro, uploads.
- **D12 Observability** — `image.upscale_{started,completed}` events, RFC 9457.

**Skipped:**
- **D4 Batch manifest** — v1 is single-image; no batch runner.
- **D5 Concurrency / Page pool** — one synchronous op, single Page checkout, no
  fan-out (no `asyncio.gather`, no nested checkout).
- **D10 Headless/CI** — the browser + reCAPTCHA requirement is identical to the
  existing image-generation path; inherited, not new.

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test |
|---|---|---|---|---|---|
| 1 | D2 | reCAPTCHA action mismatch (e.g. wrong/guessed action) | **Critical** | Token scores low → 403. Action MUST be `IMAGE_GENERATION`. *(Root-caused live; locked by the action constant.)* | Live + recon spike |
| 2 | D9 | Incomplete `clientContext` (missing projectId/sessionId/tool/tier) | **Critical** | Server 403s even with a valid token. Full 5-field clientContext is sent. *(Root-caused live.)* | Unit (`test_body_shape`) |
| 3 | D2/D7 | 4K requested on a non-Ultra account → server 403 | **Critical** | `UpscaleUnavailableError` (exit 22), "Ultra-only" hint; **no auto-retry** | Unit + CLI + BDD |
| 4 | D7 | 403 disambiguation: 4K-tier vs WAF vs expired token | **Critical** | 4K-403 → exit 22; 2K-403 → `WafRejectionError` (10); 401 → refresh+retry | Unit (`test_upsample_4k_403…`, `…2k_403_stays_waf`) |
| 5 | D7/D12 | The ~4 MB `encodedImage` base64 leaks into logs | **Critical** | Never logged; event carries `bytes` int only | Unit (`…never_logged`, `capture_logs`) |
| 6 | D9 | Oversized response (pathological 4K base64 > cap) | **High** | Reject before decode at `MAX_UPSAMPLE_B64_LEN` (50 MB); no write | Unit (`…oversized…`) |
| 7 | D9 | Response missing `encodedImage` / unexpected shape | **High** | `WireFormatError` + discovery payload, not a crash | Unit (`…missing…`) |
| 8 | D9 | `encodedImage` decodes but isn't PNG/JPEG | **High** | Magic-byte check → `WireFormatError`; no garbage written | Unit (`…non_image…`, `…undecodable…`) |
| 9 | D11/D6 | `projectId` unresolvable (not in catalog, no `--project`) | **High** | Fail fast exit 2 + "pass --project" hint; no browser/mint | CLI + BDD (`…unresolvable…`) |
| 10 | D11 | Malformed `mediaId` / `--project` (non-UUID) | **High** | Rejected before any work (exit 2) | Unit + CLI + BDD (`…bad_media_id…`) |
| 11 | D6 | mediaId owned by a different account/profile | **High** | Catalog lookup is profile-scoped (ownership proof); server is final arbiter | CLI (catalog resolve path) |
| 12 | D1 | Bearer/session stale at call time (`ACCESS_TOKEN_REFRESH_NEEDED`) | **High** | Existing refresh-on-401 mints fresh token + single retry | Live-observed |
| 13 | D11 | `--scale 1k` (the original, no upscale) | **Medium** | Rejected with "1k is the original" hint (exit 2) | Unit + CLI |
| 14 | D11 | `--scale` outside {2k,4k} | **Medium** | Rejected before I/O (exit 2) | Unit + CLI |
| 15 | D3 | Flow renames the download ligature / restructures the menu | **Medium** | Structural, locale-neutral selectors (`download`, `2K`/`4K`); re-capture via spike | Spike (manual) |
| 16 | D8 | Output filename on Windows; large binary write; PYTHONUTF8 unset | **Medium** | `write_bytes` (encoding-agnostic); platformdirs; `<mediaId>_<scale>.<ext>` | Unit (extension) |
| 17 | D12 | New structlog events stable + documented | **Medium** | `image.upscale_started/completed`; RFC 9457 on error | Covered by event asserts |
| 18 | D6 | `--project` disagrees with catalog's project for the mediaId | **Low** | Warn (non-fatal); use `--project` as given | CLI (resolve path) |

## Must-cover before merge (Critical + High) — all covered

1. **reCAPTCHA action = `IMAGE_GENERATION` + full clientContext** (#1, #2) — root-caused
   by live smoke; locked by the action constant and `build_upsample_image_body`.
2. **403 disambiguation, no auto-retry on 4K** (#3, #4) — `UpscaleUnavailableError` (22).
3. **base64 never logged + capped + magic-byte validated** (#5, #6, #7, #8).
4. **projectId resolution + fail-fast + UUID validation** (#9, #10, #11).
5. **Session refresh** (#12) — reuses the existing 401 path.

## Deferred (Medium + Low — follow-ups, not blockers)

- Record the upscaled output in the data catalog (D6) — future follow-up.
- `gflow image t2i --upscale` convenience flow (one reCAPTCHA mint) — future follow-up.
- Selector-drift re-capture is manual via `scripts/dev/spike_image_upscale_drive.py`.

## BDD scenarios (`tests/features/image_upscale.feature`)

Implemented: 2K happy (explicit project), catalog-resolved project, 4K→exit 22,
unresolvable project→exit 2, malformed mediaId→exit 2.

## Known-issues cross-reference

- **reCAPTCHA Enterprise 403 / per-profile WAF heat** (KNOWN_ISSUES) — #1, #12 map
  here. Upscale reuses the gated transport and adds a 4th gated op; mitigated by
  one-at-a-time + no-auto-retry. Not resolved (inherent to the platform).
- **4K requires Ultra** — now a documented Open limitation in KNOWN_ISSUES (#171).
- **Locale leak / icon ligatures** — #15; selectors stay language-neutral.
