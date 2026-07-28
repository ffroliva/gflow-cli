# Live Verification — v0.46.0

**Date:** 2026-07-28
**Feature under test:** provider-agnostic prompt-tools transport ([#387](https://github.com/ffroliva/gflow-cli/issues/387), PR [#400](https://github.com/ffroliva/gflow-cli/pull/400))
**Verifier:** repo owner, local Windows 11 workstation
**Credit cost:** zero — the prompt tools call an LLM endpoint, never Flow's generation API.

## Why this release needs live verification

The whole point of #387 is that gflow now talks to an **external service it does not
control**, chosen at runtime by the user. Offline tests inject a fake transport — which
is precisely the seam under test — so they cannot prove the wire format is right. Two
defects in this release were invisible to a fully green offline suite and were caught
only here (see §4).

---

## 1. Endpoint capability spike

`scripts/diag/spike_387_openai_compat.py`, stdlib `urllib` (deliberately not the `openai`
SDK — an SDK would paper over the header/param quirks this exists to discover).

| Probe | freellmapi (non-Google) | Google compat endpoint |
|---|---|---|
| text `chat/completions` | PASS 0.7 s | PASS 0.5 s |
| `temperature` + `max_tokens` | PASS 0.5 s | PASS 0.5 s |
| **multimodal `image_url` data URI (semantic)** | **PASS** — answered `'Red'` | **PASS** — answered `'Red'` |
| bad key ⇒ 4xx | 401 | 400 |

Probe 3 is deliberately **semantic**, not status-based: it generates a solid-red 64×64
JPEG and asks the model for the colour. A gateway that silently drops the image part
still returns HTTP 200 with plausible prose, so a status check would prove nothing.

**Consequences settled rather than assumed:** multimodal works on both, so the native
Gemini `inlineData` path was deleted outright with no dual-path fallback;
`max_completion_tokens` is not needed; and 0.5–1.2 s latency against a 60 s budget means
no configurable timeout knob was added.

---

## 2. Five-layer ledger — real CLI, real endpoints

Exercised through `gflow tools run creative-director … --json`, i.e. the production code
path, not a test harness.

| # | Layer | Evidence |
|---|---|---|
| 1 | Command exits 0 | `gflow tools run creative-director "cat in space" --json` → exit 0 |
| 2 | Response shape | `{"name","original","expanded","was_expanded"}`, `was_expanded: true` |
| 3 | Content actually rewritten | 12-char input → 1096-char output; `original_len=12 expanded_len=1096` in the structlog event |
| 4 | structlog invariants | `prompt_expander_endpoint host=127.0.0.1:3001` (audit trail), then `prompt_expanded model=gemini-2.5-flash` |
| 5 | User-confirmable artifact | Full five-component rewrite returned — named camera body, nebula setting, lighting, composition |

### Configuration matrix

| Configuration | Expected | Observed |
|---|---|---|
| gateway + key + model | rewritten | `was_expanded: true`, host `127.0.0.1:3001` |
| gateway + key, **no model** (gateway chooses) | rewritten | `was_expanded: true` |
| **key only** (default Google endpoint) | rewritten | `was_expanded: true`, host `generativelanguage.googleapis.com` |
| removed `GFLOW_CLI_GEMINI_API_KEY` only | loud warning + no-op | stderr warning emitted; `prompt_expander_not_configured`; `was_expanded: false` |
| nothing configured | silent no-op, **no network call** | `prompt_expander_not_configured`; `was_expanded: false`; no endpoint log |

The fourth row is the one that matters for a breaking change: because the prompt tools
never fail a run, an unmigrated user would otherwise see no error at all — just silently
un-rewritten prompts on full-price generations.

---

## 3. Integration suite

`tests/integration/test_expander_gateway.py`, `containers` marker, skips cleanly when no
endpoint is reachable so CI and other contributors are unaffected.

| Target | Result |
|---|---|
| Google compat endpoint | **6 / 6 passed** |
| freellmapi gateway | 4 passed, **2 skipped on a proven HTTP 429** |

Rate limiting is classified, not swallowed: the expander never raises, so a failed
expansion carries no reason. On failure the helper makes one raw probe and skips **only**
when a 429 is proven — a genuine transport break still fails the suite. The 429 was the
gateway's free-tier quota (`"All models exhausted. Add more API keys or wait for rate
limits to reset."`), reached after repeated runs; the retry loop backed off three times
and fell back cleanly, which is the designed behaviour.

---

## 4. Defects found *here* that the offline suite missed

Both were found with 2849 offline tests passing.

1. **Google's compat endpoint has no server-side default model.** Omitting `model`
   returns `400 "model is not specified"`, which the never-raise contract turns into a
   silent no-op — the default configuration was broken while every unit test passed.
   Omitting the model is correct only for a *user-chosen* gateway; the default endpoint
   now ships a matching default. Pinned by a regression test.
2. **The first redirect guard did not work.** `urllib.request.build_opener()` installs
   its default handler set regardless of what you pass it, so omitting
   `HTTPRedirectHandler` left redirects enabled — meaning `Authorization` would still
   have been replayed cross-host. The handler had to be *replaced*. Caught because the
   test asserts the behaviour (`redirect_request` returns `None`) rather than the
   handler list.

A third was found while running the release gates: `tests/e2e/test_tools_e2e.py` gated on
the removed `GFLOW_CLI_GEMINI_API_KEY`, so it would have skipped forever while appearing
healthy.

---

## 5. Not verified this cycle

- **Keyless gateway against a real server.** Header omission is proven by unit test
  (`test_authorization_header_omitted_when_keyless`), but no keyless endpoint was
  available — freellmapi requires its unified key. The code path is identical apart from
  the absent header.
- **`--tool` on a real generation command** (`image t2i --tool …`). The tool layer is
  verified end to end above; the generation wiring is unchanged by this release and is
  covered by `tests/e2e/test_tools_e2e.py`, which is gated on an authenticated profile.

---

## Gate summary

| Gate | Result |
|---|---|
| Offline suite | 2849 passed, 5 skipped |
| Coverage | 91% (floor 80%) |
| ruff · ruff-format · pyright | clean · clean · 0 errors |
| repo hygiene · doc links · website PII · mirror+nav | all pass |
| CI on PR #400 | 13/13 pass |
| SonarCloud quality gate | **GREEN** — zero new issues |
| `/ponytail-review` xhigh | 3 cuts applied |
| `security-review` | no HIGH/MEDIUM findings |
| `/code-review high` | **not run** — user-triggered only, cannot be launched by the agent |
