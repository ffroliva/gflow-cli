---
name: unanchored-token-regex-is-quadratic
description: "A regex over a whitespace-free / token-alphabet run must be anchored at the run's start and length-bounded, or it is quadratic: three measured cases (#852 email scan; the batchexecute `at` scrub; the signed-query scrub fed whole replies) — and a long-input timing test belongs next to it"
metadata:
  type: project
---

**The shape:** a pattern like `X{20,}…`, `\S*…`, or `[A-Za-z0-9_-]+…` with no anchor and no
upper bound. On one long run that never completes a match, every start position inside the
run re-scans the rest of the run, so the cost is O(n²): 4x the time for 2x the input.

**Measured cases (all found by measuring, none by reading):**

| Where | 2x-input cost | Fix |
|---|---|---|
| #852 `find_emails` (auth verification) | 10k→0.48 s, 20k→2.0 s, 40k→12.1 s | bounded local-part window |
| `data/redaction.py` `at` XSRF scrub (2026-10-16) | 2k 36 ms, 4k 139 ms, 8k 610 ms | `(?<![A-Za-z0-9_-])` + `{20,64}` |
| `data/redaction.py` signed-query scrub (council D3, 2026-10-16) | 2.5k 0.10 s, 5k 0.47 s, 10k 1.88 s | `(?<!\S)` start anchor |

The third case had existed for months and was harmless while its only heavy caller sliced
input to 200 chars first; scrubbing BEFORE slicing (correct for secrets) is what fed it
whole replies. **Moving a scrub earlier in a pipeline changes its input size — re-time it.**

**How to apply:**
- Anchor at the start of a run (`(?<!\S)`, `(?<![<alphabet>])`) and bound the length when
  the token's shape is known (measure it first — the `at` value is 28 chars).
- Every scrub over persisted error details, transport snippets or worker payloads gets a
  timing test on 160 KB+ of the worst-case alphabet, bound ~1 s (see
  `tests/data/test_redaction.py::TestBatchexecuteSecrets`).
- Test with input the EARLIER patterns do not collapse: an all-`A` run is eaten by the
  token-run rule first and hides the slow pattern behind it; URL-like text (`/`, `.`, `?`)
  does not.
