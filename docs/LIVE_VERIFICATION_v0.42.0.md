# Live verification — v0.42.0

Per the release protocol's live-verification gate, this records the live-verification
status of v0.42.0's user-facing changes.

## Content-safety 400 → ContentPolicyError (#359)

**Status: not live-triggered — unit-covered.** Live-triggering this would require
deliberately submitting a content-policy-violating prompt to Flow, which is
inadvisable. The classification is covered by unit tests (`tests/test_errors_403.py`,
`tests/test_errors_classification.py`), which assert that a content-safety `400`
maps to `ContentPolicyError` (exit code 5) rather than `WireFormatError`.

## Production-readiness live-gaps (#361)

**Status: live-confirmed 2026-07-21** (profile `ffroliva`, 1 Veo credit). A real
`veo-lite` t2v through the worker path persisted a real `remote_started` checkpoint
(observer→checkpoint→DB chain confirmed), and crash-recovery classifies such a task
`indeterminate` without resubmitting. Mid-launch cancellation released the ProfileLease
and left no browser process behind (credit-free). Full evidence:
[`docs/LIVE_VERIFICATION_v0.40.0-production-readiness.md`](LIVE_VERIFICATION_v0.40.0-production-readiness.md)
§ "2026-07-21 live follow-up". Durable live-gated tests:
`tests/e2e/test_crash_recovery_e2e.py`, `tests/e2e/test_cancellation_e2e.py`.

## Documentation (#360, #362)

**Status: n/a — no runtime feature.** Docs-only changes (Antigravity agent references;
onboarding page; data anonymization; security contact → GitHub private vulnerability
reporting). Verified by the documentation gates (`check_doc_links.py`,
`test_documentation_gate.py`).
