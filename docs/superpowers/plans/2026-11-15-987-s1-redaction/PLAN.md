# Slice 1: Redaction Hardening Implementation Plan

> **For agentic workers:** Run `/gflow:status` to find the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Port Slice 1 of PR #987 (secret redaction hardening and discovery head scrub-then-slice fix) onto current `develop`, test-first, keeping develop's #1229 form-field rules and crediting `@ziyedbe`.

**Architecture:** Update `src/gflow_cli/data/redaction.py` to add regex patterns for `lh3.../asb/` URLs, bounded linear lookbehind for bare `at` tokens, `0cAF...` reCAPTCHA Enterprise tokens, and 120+ character token runs; anchor `_SIGNED_QUERY_TOKEN_PATTERN` with `(?<!\S)`. Update `src/gflow_cli/api/transports/batchexecute.py::_discovery_head` to scrub the entire payload before slicing to 200 characters.

**Predict verdict:** GO (coordinator, offline security hardening, confidence 9.8/10 from assessment).

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| High | False positives on legitimate text (36-char UUIDs, media IDs, file paths, base64 under 120 chars) | Specific token alphabet, minimum length thresholds (100 for `0cAF`, 120 for token runs), bounded lookbehind anchors, and regression tests asserting near-miss survival. |
| High | Regex quadratic cost / ReDoS on large bodies (e.g. 160 KB body) | Pattern anchor `(?<!\S)` on `_SIGNED_QUERY_TOKEN_PATTERN`, lookbehind `(?<![A-Za-z0-9_-])` and bounded length `{20,64}` on the bare `at` pattern; timing regression test asserting < 1.0 s execution on 220 KB payloads. |
| Medium | Inadvertent conflict with develop's #1229 `(at|f\.sid)=` form-field redaction | Retain develop's #1229 rule verbatim; test bare `at` values in payloads/JSON alongside `at=` form fields. |
| Medium | Tokens straddling the discovery head boundary leaking fragments | `_discovery_head` scrubs the entire payload before slicing to 200 characters. |

---

## Scenarios

- **S1 (Secret Redaction):** Each new pattern redacts its secret:
  - `lh3.googleusercontent.com/asb/...` -> `<redacted:url>`
  - Bare `at` values (`[A-Za-z0-9_-]{20,64}(?::|%3A)\d{13}`) -> `<redacted:token>`
  - `0cAF[A-Za-z0-9_-]{100,}` -> `<redacted:token>`
  - `[A-Za-z0-9_-]{120,}` -> `<redacted:token>`
- **S2 (Near-Miss Survival):** Legitimate non-secret text survives untouched:
  - 36-char UUIDs (`1035317b-6c80-466f-b4f4-f568900fcd8d`)
  - RPC IDs (`rzMKMb`)
  - Timestamps (`[1792170598, 989086000]`)
  - File paths (`C:/x@y.co/path`, `/var/x@y.io/cache`)
  - Base64 strings under 120 characters
- **S3 (Discovery Head Window):** A token cut by the 200-character discovery head window in `batchexecute.py` leaves no fragment in `WireFormatError.discovery["payload_head"]`.
- **S4 (Linear Performance):** Scrubbing remains linear; 220 KB payload scrubs in well under 1 second.
- **S5 (Offline Scope):** No Flow surface touched; zero credits consumed; no browser required; zero e2e tests needed.
- **S6 (CLI & MCP Parity):** Internal data scrub only; no CLI commands, arguments, or MCP tools modified; parity gate remains green.

---

## File structure

### Modified files
```
src/gflow_cli/data/redaction.py
  Add lh3 asb URL pattern, bare at token pattern, 0cAF and 120+ token patterns, anchor _SIGNED_QUERY_TOKEN_PATTERN.
src/gflow_cli/api/transports/batchexecute.py
  Update _discovery_head to scrub the whole payload before slicing.
tests/data/test_redaction.py
  Add TestBatchexecuteSecrets test class with secret scrubbing, near-miss survival, and timing tests.
tests/api/transports/test_batchexecute.py
  Add test_a_token_cut_by_the_head_window_leaves_no_fragment.
tests/data/test_failure_recording.py
  Adapt test_redact_error_detail_truncates to use whitespace-separated prose so 120+ token pattern does not mask length truncation.
CHANGELOG.md
  Add entry under [Unreleased] crediting @ziyedbe (#987).
```

---

## Task 1 — Implementation Plan

**What:** Write task-by-task implementation plan for Slice 1.

**Files:**
- `docs/superpowers/plans/2026-11-15-987-s1-redaction/PLAN.md`

**Steps:**
- [x] Create implementation plan covering predict, risks, scenarios, file structure, and tasks.
- [x] Commit plan locally with contributor attribution trailer.

---

## Task 2 — Red Test Scaffold

**What:** Port contributor's tests, near-miss survival tests, timing benchmark, and discovery head boundary tests; verify failure reasons on unfixed code.

**Files:**
- `tests/data/test_redaction.py`
- `tests/api/transports/test_batchexecute.py`

**Steps:**
- [x] Add `TestBatchexecuteSecrets` to `tests/data/test_redaction.py` (excluding `TestScrubSecrets` which is deferred to Slice 3).
- [x] Adapt `test_the_at_xsrf_value_is_scrubbed_plain_and_url_encoded` to include bare `at` value verification since develop already has `(at|f\.sid)=` from #1229.
- [x] Add `test_a_token_cut_by_the_head_window_leaves_no_fragment` to `tests/api/transports/test_batchexecute.py`.
- [x] Run pytest on the new tests against the unfixed code and record failure reasons.
- [x] Commit red tests locally with contributor attribution trailer.

---

## Task 3 — Implementation & Green Gates

**What:** Implement the redaction patterns and `_discovery_head` fix; ensure all tests and quality gates pass.

**Files:**
- `src/gflow_cli/data/redaction.py`
- `src/gflow_cli/api/transports/batchexecute.py`
- `tests/data/test_failure_recording.py`

**Steps:**
- [x] Add `https?://lh3\.googleusercontent\.com/asb/\S+` to `_SECRET_TEXT_PATTERNS`.
- [x] Add `(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{20,64}(?::|%3A)\d{13}(?!\d)` to `_SECRET_TEXT_PATTERNS`.
- [x] Add `0cAF[A-Za-z0-9_-]{100,}` and `[A-Za-z0-9_-]{120,}` to `_SECRET_TEXT_PATTERNS`.
- [x] Anchor `_SIGNED_QUERY_TOKEN_PATTERN` with `(?<!\S)`.
- [x] Update `_discovery_head` in `batchexecute.py` to scrub before slicing to 200 chars.
- [x] Update `test_redact_error_detail_truncates` in `tests/data/test_failure_recording.py` to use spaced words (`"word " * 400`).
- [x] Run `scripts/ci/check_size.py`, `ruff check`, `ruff format --check`, `pyright src`, and pytest.
- [x] Commit implementation locally with contributor attribution trailer.

---

## Task 4 — Changelog Update

**What:** Document the redaction hardening in `CHANGELOG.md` under `[Unreleased]`.

**Files:**
- `CHANGELOG.md`

**Steps:**
- [x] Add a note under `[Unreleased]` -> `### Security` (or `### Fixed`) crediting `@ziyedbe (#987)`.
- [x] Verify formatting and doc gates.
- [x] Commit changelog locally with contributor attribution trailer.

---

## Definition of Done

- [x] All task steps checked off.
- [x] All new and existing tests pass (`pytest tests/data tests/api/transports/test_batchexecute.py`).
- [x] `uv run python scripts/ci/check_size.py` exits 0.
- [x] `uv run ruff check src tests` exits 0.
- [x] `uv run ruff format --check src tests` exits 0.
- [x] `uv run pyright src` exits 0.
- [x] `CHANGELOG.md` updated with `@ziyedbe (#987)`.
- [x] All commits end with `Co-authored-by: ziyedbe <ziyedbecha@hotmail.fr>` trailer.
- [x] Zero AI attribution in git history.

---

## Decision Log

- **2026-11-15 (Plan creation):** Scoped Slice 1 strictly to redaction hardening and `_discovery_head` scrub-then-slice fix. `scrub_secrets` and public `discovery_head` deferred to Slice 3 with `migrated_character.py`. `character_bundle.py` and `image_files.py` deferred to Slice 4.
- **2026-11-15 (Test adaptation):** Contributor test `TestScrubSecrets` dropped from Slice 1 because `scrub_secrets` is not in this slice. `test_the_at_xsrf_value_is_scrubbed_plain_and_url_encoded` adapted to test bare `at` tokens (e.g. in JSON/headers) because develop's #1229 rule already handles `at=` form fields.
- **2026-11-15 (Review fix round, two fresh xhigh reviews):** The generic `[A-Za-z0-9_-]{120,}` rule is **dropped**: it erased Flow media ids (162 chars), and, placed before the Bearer and address rules, it cut those secrets in two so their tails leaked. `0cAF` moves to the end of the list. The URL-credential scheme and the address domain and lookahead are bounded (both were quadratic: 8 s on 40 KB, 36 s on 80 KB). `_discovery_head` scrubs only the first 4 KB (two status observers build it for every unmatched frame). The `asb` rule stops at JSON delimiters and matches any `lh<n>` host. Tasks above that name the 120 rule describe the first pass, not the shipped code. Not changed: other call sites that slice DOM text before scrubbing (`agent_only_composer.py:553`, `:626`, `_mc_editor_gate.py:387`) carry page text, not token-dense replies; the `at` rule also matches `<20-64 token chars>:<13-digit ms>` ids, accepted as low risk.
- **2026-11-15 (CodeRabbit on #1302):** `scrub_secrets` moves INTO this slice. `_discovery_head` now runs the named secret rules before the generic `_TOKEN_RE` (which had cut a Bearer token in two), and it needs that scrub without `redact_error_detail`'s 500-char cap. After the 4 KB cut, the trailing token run is dropped, because once earlier long runs collapse to `<token>` it would slide into the 200-char head. Both cases got tests that were red first.
