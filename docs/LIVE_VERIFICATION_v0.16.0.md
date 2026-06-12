# Live verification — v0.16.0

> Evidence record for the v0.16.0 release. v0.16.0 ships two features: locale-free
> resource-picker include selectors (issue #170, PR #173) and `gflow image upscale`
> (issue #171, PR #172). Backfilled 2026-06-12 to close the live-verification
> documentation gap that opened after v0.13.0 (see docs/INDEX.md).

## Summary

- **Verified by:** ffroliva (Claude Code)
- **Dates:** #171 upscale — 2026-06-11 (feature branch); #170 selectors — 2026-06-12 (released PyPI artifact)
- **gflow-cli version:** 0.16.0
- **Status:** 🟢 Green — both features live-verified credit-free; no Veo credits spent

All verification below is **credit-free**: image generation, character-entity attach,
and image upscale are non-Veo operations (see the credits-are-videos-only note in
`KNOWN_ISSUES.md`). No video generation was exercised for this release.

## 1. Quality gates

- [x] **Lint/format** — `ruff check` + `ruff format --check` clean (`src` + `tests`).
- [x] **Type check** — `pyright src`: 0 errors.
- [x] **Unit + BDD tests** — full suite green in CI across Python 3.11 / 3.12 / 3.13 (PR #173, PR #172).
- [x] **Repo hygiene + doc links** — `check_repo_hygiene.py` + `check_doc_links.py` green.

## 2. Issue #170 — locale-free picker include selectors (PR #173)

The pt-BR-hardcoded resource-picker include selectors broke entity attach on every
non-Portuguese account. PR #173 replaced them with sequentially-probed tier cascades
(locale-invariant ligature anchor first, then localized-text fallback) and added an
image-side submit backstop that fails loudly (`WireFormatError`, exit 7) if a staged
entity never reaches the wire.

### E2E evidence (live Flow, credit-free)

| Layer | Criterion | Outcome |
|---|---|---|
| Selector tier | `include_selector_tier=icon` logged (locale-free anchor matched) | **PASS** |
| Attach event | `ui_automation.character_entity_attached` fired | **PASS** |
| Submit backstop | `ui_automation.image_entities_attached` — entity rode the wire | **PASS** |
| Output artifact | t2i produced a JPEG, **768×1376**, character identity visibly preserved | **PASS** |
| Version | all events emitted at `cli_version 0.16.0` | **PASS** |

- **Pre-merge:** live credit-free `gflow image t2i --reference-entity` on **promo-denon82**
  (pt-BR profile). Recon harness: `scripts/dev/spike_issue170_picker_locale_recon.py`.
- **Released-artifact re-verification (2026-06-12):** installed `gflow-cli==0.16.0` from
  PyPI (global `uv tool`) and re-ran the same flow on promo-denon82 — all four layers
  above green at `cli_version 0.16.0`. **5-layer ledger PASS.**
- The backstop earned its keep immediately: it converted Flow's new full-page library-UI
  A/B (issue #174) from a silent text-only "success" into a typed exit-7 failure on its
  first live encounter.

## 3. Issue #171 — `gflow image upscale` (PR #172)

`gflow image upscale <mediaId> --scale 2k|4k` upscales a platform-generated image via
Flow's `POST /v1/flow/upsampleImage` (reCAPTCHA-gated, inline base64 response). 4K is
Ultra-gated and fails fast with `UpscaleUnavailableError` (exit 22) on lower tiers.

### E2E evidence (live Flow, credit-free)

| Layer | Criterion | Outcome |
|---|---|---|
| Wire | `POST /v1/flow/upsampleImage` accepted (full 5-field `clientContext`, action `IMAGE_GENERATION`) | **PASS** |
| File written | local JPEG written, **3,799,245 bytes** | **PASS** |
| Magic bytes | header `FF D8 FF` (valid JPEG) | **PASS** |
| Byte fidelity | written bytes matched the decoded wire payload | **PASS** |
| Tier gate | 4K returns exit 22 (`UpscaleUnavailableError`) on non-Ultra, not a generic 403 | **PASS** |

- Verified 2026-06-11 on the `feature/image-upscale` branch (= the code released as
  v0.16.0): `gflow image upscale <mediaId> --scale 2k --project <pid>` end-to-end.
- Wire protocol documented in [`docs/IMAGE_UPSCALE_RECON.md`](IMAGE_UPSCALE_RECON.md).

## 4. Documentation

- `CHANGELOG.md` — v0.16.0 entry covers both features.
- `KNOWN_ISSUES.md` — 4K-Ultra limit documented; library-UI A/B (#174) tracked.
- `docs/IMAGE_UPSCALE_RECON.md` — reverse-engineered upscale wire.
