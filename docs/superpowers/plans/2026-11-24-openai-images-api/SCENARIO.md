# Scenario: OpenAI-compatible image endpoint on `gflow serve` (`#1365`)

Scope: `POST /v1/images/generations` served by `gflow serve` next to `/mcp`, so OpenAI SDK
code (and any HTTP client that speaks that schema) can generate Flow images by changing the
base URL. Images only; the video equivalent named in #1365 is a follow-up.

The route is an adapter over the existing MCP tool `gflow_generate_image` (`wait=True`), so
it inherits the tool's profile resolution, rate limiter, `_guarded` error funnel and queue
path. It adds no CLI leaf, option or MCP tool.

---

## Coverage map

| Dimension | Active | Reason |
|---|---|---|
| D1 Auth & session lifecycle | Yes | Same daemon token as `/mcp`: the route is mounted inside `build_app`, under `_BearerAuthMiddleware`. |
| D2 WAF / reCAPTCHA scoring | No | No new Flow call; the tool's transport does the generation exactly as over MCP. |
| D3 Selector drift & locale invariance | No | No DOM code. |
| D4 Batch manifest & resume | No | One request, one tool call. |
| D5 Concurrency & Page pool | Yes | Concurrent HTTP requests share the tool's token bucket and per-profile lease; an exhausted bucket answers 429. |
| D6 Data layer (SQLite / DataStore) | No | Reads only the file paths the tool returns. |
| D7 Error propagation & exit codes | Yes | RFC 9457 envelope → OpenAI `{"error": {...}}`, translated by one table (`openai_errors.py`) into OpenAI's status, `type` and `code`; gflow's facts ride along as extra keys. |
| D8 Cross-platform paths | No | Files are read from paths the catalog already returned. |
| D9 Transport edge cases | No | Unchanged transport. |
| D10 Headless vs headed environment | No | Unchanged. |
| D11 Input validation & boundary values | Yes | `prompt`, `n`, `size`, `model`, `response_format` validated before the tool runs. |
| D12 Observability & structured log contract | Yes | `openai_api.images.*` events for failures and completions; the request itself is logged once, by the tool. |
| D13 MCP surface parity | Yes | No new CLI leaf or option; the route is a third door onto the existing `gflow_generate_image` tool, and `GFLOW_MCP_NO_SPEND` removes it with the tool. |

---

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D1 | Token configured, request without `Authorization` | **Critical** | 401 from the existing middleware; the tool is never called | Unit |
| 2 | D1 | No token, loopback bind, request whose `Host` is not loopback (DNS rebinding) | **Critical** | 421; the tool is never called. The plain route checks it itself | Unit |
| 3 | D13 | `GFLOW_MCP_NO_SPEND=1` | **Critical** | 403 `no_spend`; the tool is never called (the tool is unregistered under no-spend, the route must not be a back door) | Unit |
| 4 | D11 | Golden path: `{"prompt": "...", "n": 2, "size": "1792x1024"}` | **High** | Tool called with `count=2`, `aspect="16:9"`, `model="nano2"`, `wait=True`; 200 `{"created": int, "data": [{"b64_json": ...}, {"b64_json": ...}]}` holding the files' bytes | Unit |
| 5 | D11 | `size` mapping | **High** | `WxH` maps to the nearest of Flow's five aspects by ratio (`1024x1024`→1:1, `1024x1792`→9:16, `1536x1024`→4:3 …); an aspect string (`"16:9"`) passes through; `"auto"` or absent → 1:1; anything else → 400 `param: size` | Unit |
| 6 | D11 | `response_format: "url"` | **High** | 400 `param: response_format`: gflow returns local files, and serving them is out of scope; `b64_json` (the default here) is accepted | Unit |
| 7 | D11 | `n` outside 1–4, or not an integer | **High** | 400 `param: n` | Unit |
| 8 | D11 | Unknown `model`, including OpenAI's own (`gpt-image-1`, `dall-e-3`) | **High** | 400 `param: model` listing Flow's image models; gflow aliases (`nano2`, `nano-pro`, …) accepted | Unit |
| 9 | D11 | Missing/empty `prompt`, body not a JSON object | **High** | 400 `param: prompt` / 400 invalid JSON | Unit |
| 10 | D7 | Tool returns `status: error` with a problem dict | **High** | Status, `type` and `code` from the mapping table in `docs/MCP.md`; `gflow_type` carries the problem `type` URI | Unit, one per table row |
| 11 | D5 | Tool returns `status: rate_limited` | **High** | 429 `rate_limit_error` with `retry-after` | Unit, SDK contract |
| 12 | D7 | Tool completes with no files | **Medium** | 502; never a 200 with an empty `data` list | Unit |
| 13 | D11 | Fields OpenAI clients send that Flow has no knob for (`quality`, `style`, `user`, `background`, `output_format`) | **Medium** | Ignored, request still served | Unit |
| 14 | E2E | Live generation over HTTP on a logged-in profile | **High** | `POST /v1/images/generations` through `build_app` returns one decodable PNG/JPEG; through the `openai` SDK, `n=2` returns two | Live E2E (`e2e`, `e2e_image`, $0) |
| 15 | D1 | No token, a web page POSTs `text/plain` or from a foreign `Origin` (CSRF) | **Critical** | 415 for any non-JSON Content-Type, 403 for a non-loopback Origin; the tool is never called | Unit, subprocess |
| 16 | D5 | A failure the official SDK would retry (5xx) after Flow may have billed | **Critical** | `x-should-retry: false`, so the SDK makes exactly one call | Unit, SDK contract |
| 17 | D1 | Flow answers 401/403 for the daemon's saved session | **High** | 502 `server_error` saying to run `gflow auth renew`, never a 401 that makes a client rotate a valid daemon token | Unit |
| 18 | D7 | The task is still pending or processing when the wait ends | **High** | 504 `generation_timeout` naming the task id | Unit |
| 19 | D11 | Explicit `null` fields, `stream: true`, a huge `size`, a deeply nested or oversized body | **High** | `null` reads as absent; `stream` 400; huge `size` and deep nesting 400; over 4 MiB 413 | Unit |
| 20 | D7 | The generated file cannot be read | **Medium** | 500 `output_unreadable`, `x-should-retry: false` | Unit |
| 21 | D1 | Wrong token, or a GET on the route | **Medium** | OpenAI-shaped 401 / 405 | Unit, SDK contract |

**Count:** 21 scenarios. Critical 5 · High 12 · Medium 4.

## Must-cover before merge

Scenarios 1–11 and 14–21.
