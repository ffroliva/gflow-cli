# Explicit Omni 1.2 Flash

Predict: CAUTION. Independent correctness/security and parity reviewers identified
version ambiguity, legacy caps, chained-generation admission, account caps and
queue payload round trips. Reuse the existing client/composer/worker lifecycle.

## Acceptance

- [x] Explicit model identity/aliases; old Omni aliases/defaults unchanged.
- [x] Integer durations 3–15; reject strings/fractions/bools in the SDK DTO.
- [x] Legacy Omni and Veo keep their duration sets; unknown models do not inherit new limits.
- [x] Exact model menu match and slider index/display readback, no rounding/fallback.
- [x] Missing/disabled/changed/unbound slider fails before Send; free maximum 10 is enforced.
- [x] CLI t2v/i2v/r2v, MCP queue/codec, catalog and manifest preserve model/duration.
- [x] Chain-level and per-link Omni 1.2 remain refused before generation.
- [x] Live settings BDD covers all 13 durations; blocked wire captures cover 3 and 15.
- [x] Docs, published mirrors, live inventory and negative regression fixtures updated.
- [ ] Paid completed-generation verification: outside this task's authorization.

## Scenario matrix

| Trigger | Required outcome |
| --- | --- |
| Both Omni versions coexist | Explicit 1.2 picks only 1.2; old alias never picks 1.2 |
| UI slider max index 7, requested 15s | Refuse without key presses or Send |
| Model menu is closing | Wait for its hidden state before keyboard input |
| New model slider has not mounted | Wait for visible control, then verify unique identity |
| Index mapping/label differs | Refuse; do not infer or clamp |
| Missing model plus a new-only duration | Reject before queue/browser |
| MCP task is queued | Worker reconstructs exact model and integer duration |
| A later chain link selects 1.2 | Reject before an earlier link can bill |

See the dated spike for evidence and boundaries. No new transport, session,
retry, model routing or spend-approval mechanism is introduced.
