# OpenAI-compatible image endpoint — Implementation Plan (`#1365`, images half)

**Goal:** `gflow serve` answers `POST /v1/images/generations` in the OpenAI schema.

**Architecture:** one new module, `src/gflow_cli/mcp/openai_api.py`, holding a Starlette route
that validates the OpenAI request, calls `tools.gflow_generate_image(..., wait=True)`, and
turns the result into the OpenAI response (base64 of each file) or an OpenAI error body.
`build_app` appends the route, so the daemon token middleware already covers it.

**Scenario:** [SCENARIO.md](SCENARIO.md)

## Global constraints

- No new CLI leaf, option or MCP tool; no new setting.
- Same guardrails as the MCP tool: token, rate limiter, `GFLOW_MCP_NO_SPEND`.
- Loopback, no-token bind: reject a non-loopback `Host` (the SDK guards only `/mcp`).
- Images only; video is a follow-up on #1365.

## Tasks

- [x] **1. Request mapping (scenarios 5–9, 13).** `parse_request(body) -> ImageRequest | OpenAIError`
  in `openai_api.py`; nearest-aspect `size` mapping; unit tests in
  `tests/mcp/test_openai_images_api.py`.
- [x] **2. Route + result mapping (scenarios 3, 4, 10–12).** `images_generations(request)`;
  tool faked in tests; problem dict → OpenAI error with the problem's status.
- [x] **3. Wire into `build_app` (scenarios 1, 2).** Append the route for both transports; host
  check when no token; tests through `build_app` + `TestClient`.
- [x] **4. E2E (scenario 14).** `tests/e2e/test_openai_images_api_e2e.py`, `e2e` + `e2e_image`.
- [x] **5. Docs.** `docs/MCP.md` (HTTP daemon section), README comparison row, CHANGELOG
  `[Unreleased]`, `website/docs/` mirror regenerated.
- [x] **6. Gates.** The Impeccable Routine green.
