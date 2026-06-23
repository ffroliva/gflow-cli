# Scenario: Model Context Protocol (MCP) Server

## Coverage map
We evaluate the MCP Server wrapper against relevant failure dimensions.

| Dimension | Status | Notes |
|---|---|---|
| **D1 — Auth** | **Active** | Handles missing/expired Playwright browser profiles gracefully during remote tool execution. |
| **D2 — WAF** | Skipped | Same risk profile as standard CLI runs. |
| **D3 — Selector Cascade** | Skipped | Standard browser automation risk, not unique to MCP. |
| **D4 — Batch manifest** | Skipped | MCP calls are single-execution tool requests. |
| **D5 — Concurrency** | **Active** | Handles parallel tool calls from the AI client safely (avoiding browser context collisions). |
| **D6 — Data layer** | **Active** | Fast read paths to SQLite database for listing projects and characters. |
| **D7 — Error propagation** | **Active** | Catches exceptions internally and returns them as text payloads to avoid crashing the JSON-RPC daemon. |
| **D8 — Cross-platform** | **Active** | Returns absolute `file://` URIs formatted correctly for Windows, macOS, and Linux. |
| **D9 — Transport** | **Active** | **CRITICAL**: Ensures absolutely no data is printed to `stdout` by the package or its dependencies, as it would corrupt the JSON-RPC stream. |
| **D10 — Headless/Headed** | Skipped | Standard runner configurations. |
| **D11 — Input validation** | **Active** | Validates tool inputs (aspect ratios, formats, bounds) before launching browser. |
| **D12 — Observability** | **Active** | All logs must be written strictly to `stderr` with clear tracing correlation. |

---

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D9 Transport| A package or dependency writes to `stdout` (e.g. print statements) | Critical | JSON-RPC stream is corrupted. We must redirect all stdout writes to stderr internally inside the server loop. | Unit |
| 2 | D7 Error | Tool call raises an exception (e.g. `FlowApiError`) | High | Catch error, format as standard text error response, return code 200 JSON-RPC (do not exit server). | Unit |
| 3 | D1 Auth | Active browser context has no cookies or has expired | High | Return text response: "Authentication required. Run 'gflow auth login' in your local terminal." | Integration |
| 4 | D8 Paths | Returning generated image path to AI client | High | Convert local path to a structured absolute file URI (e.g. `file:///C:/path/to/image.png`). | Unit |
| 5 | D5 Concurrency| AI client issues two parallel generation commands | High | Queue requests using asyncio.Lock, verify lock is released, and acquire file-based locks on the context directory to avoid profile crashes. | Integration |
| 6 | D6 Data | Tool queries local SQLite catalog | Low | Read directly using fast SQL select queries; resolve within < 50ms without launching browser. | Unit |
| 7 | D11 Input | Invalid aspect ratio passed via tool arguments | Low | Validate input in Python, return error message immediately without launching browser context. | Unit |
| 8 | D9 Transport| Client queries list of exposed prompts | Low | Return list of prompts including "expand_prompt" and "create_character". | Unit |
| 9 | D9 Transport| Client reads resource URI "gflow://docs/mcp-guide" | Low | Return custom MCP-targeted agent guidance to use registered tools. | Unit |
| 10| D9 Transport| Client reads resource URI "gflow://db/schema" | Low | Return SQLite database table definitions as text. | Unit |
| 11| D8 Cross-plat| Windows user issues prompt with non-ASCII characters | High | UTF-8 stdio reconfiguration prevents pipe crashes. | Unit |
| 12| D2 WAF/reCAP | Prompt injection attempts >3 generations in a minute | High | Local sliding-window rate limit triggers and returns rate-limit error response. | Unit |

---

## Must-cover before merge (Critical + High)
1. **D9 Transport**: Verify that all standard logging and printing inside `gflow-cli` is redirected to `stderr` when running the MCP daemon, ensuring the JSON-RPC stream on `stdout` is never corrupted.
2. **D1 Auth**: Ensure that missing/expired browser sessions return a clean instructional error instead of hanging or prompting interactive terminal input (which causes agent timeouts).

## Suggested BDD scenarios

```gherkin
Feature: MCP Server
  Scenario: Querying tool metadata
    When the MCP server receives a schema list request
    Then it should return the JSON-RPC description of all available tools
    And the tool list must contain "gflow_generate_image" and "gflow_generate_video"

  Scenario: Safe handling of execution errors
    Given the local browser profile has no active session
    When the MCP server executes "gflow_generate_image"
    Then the tool response should contain the text "Authentication required"
    And the JSON-RPC connection should remain open

  Scenario: Querying prompt templates and reading resources
    When the MCP server receives a prompt list request
    Then it should return description of "expand_prompt" and "create_character"
    When the MCP server receives a resource read request for "gflow://docs/mcp-guide"
    Then the response should contain the content of the MCP-targeted agent guide
```
