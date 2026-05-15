---
description: Surface open known issues. Call before touching auth, reCAPTCHA, or anything previously flagged.
---

# `/gflow:known-issues` — Open issues

Read KNOWN_ISSUES.md and return items that are still open or mitigated (not resolved).

## Steps

1. Read [KNOWN_ISSUES.md](../../../KNOWN_ISSUES.md)
2. Return only items with status **open** or **mitigated**
3. Flag any that are relevant to the current task context

## When to call

- Before touching `src/flow_cli/auth.py`
- Before touching `src/flow_cli/api/recaptcha.py`
- Before any work the user flags as "this felt flaky before"
- When a test or behaviour feels unexpectedly broken
