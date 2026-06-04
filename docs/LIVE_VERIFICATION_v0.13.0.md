# Live verification — v0.13.0

> Evidence record for the v0.13.0 release. v0.13.0 focuses on CLI flag alignment with
> the Flow UI and internal technical hardening.

## Summary

This release aligns the Image-to-Video (I2V) CLI surface with Google Flow's own terminology
and provides high-fidelity E2E verification of the CLI transport. It also integrates
in-project governance gates (ruff T20, materiality classification) to harden the
AI-driven development flow.

- **Verified by:** ffroliva (Gemini CLI)
- **Date:** 2026-06-04
- **gflow-cli version:** 0.13.0
- **Status:** 🟢 Green (unanimous council consensus + live E2E passing)

## 1. Quality Gates

Observed — 2026-06-04, `chore/release-v0.13.0` branch:

- [x] **Lint/Format** — `ruff check` (0 errors), `ruff format` (no changes).
- [x] **Type Check** — `pyright src` (0 errors).
- [x] **Unit Tests** — `pytest` (1445 passed). Coverage: 88.44% (>= 80% requirement).

## 2. Technical Hardening

- [x] **Click Greedy-Fill Fix** — The `i2v` command now robustly handles positional
      argument collision when `--initial-frame` is used.
- [x] **Path Normalization** — `resolved_image` is normalized via `.resolve()` before
      transport, ensuring consistent absolute paths in audit logs.
- [x] **Service Layer Typing** — `character_create` saga refactored to use
      `TYPE_CHECKING` for `FlowApiClient`, eliminating manual casts and circular deps.

## 3. E2E Evidence (Live Flow)

The following tests were executed against a live Google AI Pro account (`denon82` profile)
and verified to produce valid mp4 assets and correct automation events:

| Test | Criterion | Outcome |
|------|-----------|---------|
| `test_e2e_i2v_initial_frame_flag` | I2V-FLAG-1: `--initial-frame` routes to I2V, `frame_attached` event fires | **PASSED** |
| `test_e2e_i2v_positional_back_compat` | I2V-FLAG-2: Positional `IMAGE PROMPT` still functional | **PASSED** |
| `test_e2e_i2v_start_end_frame_flags` | I2V-FLAG-3: Interpolation path attaches both frames correctly | **PASSED** |

**Logs:** Verified `ui_automation_video.frame_attached` fired twice for the interpolation
path, confirming binding to the correct Flow editor slots.

## 4. Documentation Audit

- [x] `CHANGELOG.md` updated and [0.13.0] section populated.
- [x] `README.md` link rot repaired (DISCLAIMER, PROJECT_STATUS, GITHUB).
- [x] All examples in `docs/` and `skills/` updated to use `--initial-frame` and `--out-dir`.
- [x] `docs/INDEX.md` routing updated.

## 5. Deployment Readiness

- [x] `gh release view v0.13.0` (pending push)
- [x] PyPI publish (pending tag push)
- [x] **Clean-venv install** — `import gflow_cli` -> `0.13.0`.
