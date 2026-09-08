# Scenario: Migrated-host image generation

## Coverage map

Active: D1/D2 (the image client currently mints reCAPTCHA before transport dispatch), D3
(Angular composer selectors), D4/D5 (native count and image-batch reuse of one Page), D6
(operation/media attribution), D7 (typed pre-submit failures), D8/D10 (headed Chrome and
local reference paths), D9 (unknown image `batchexecute` envelopes and signed URLs), D11
(model/aspect/count/reference caps), D12 (redacted structured evidence), and D13 (direct and
queued MCP twins). No schema migration is planned.

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D2/D9 | The page emits an image submit/result envelope not shaped like video | Critical | Parse the measured image shape or fail with redacted `WireFormatError`; never treat a poster as the result | Spike + unit + E2E |
| 2 | D3 | Account persisted Video mode before `image t2i` | Critical | Select Image by the locale-free mode ligature and read back `aria-checked` before submit | Unit + E2E |
| 3 | D3/D9 | A stale/incorrect selector would submit video | Critical | Outgoing submit assertion proves the image RPC/model shape before reporting success | Unit + E2E |
| 4 | D2 | Pooled page is on the migrated root grid, where reCAPTCHA script is absent | High | Navigate to the project editor before any mint; let the app issue its own image request without token replay | Integration + E2E |
| 5 | D9 | Signed result URL points outside the trusted Google allowlist | Critical | Refuse the download with `WireFormatError` and never follow redirects | Unit |
| 6 | D9 | Downloaded bytes are video/HTML rather than PNG/JPEG/WebP | Critical | Reject before persistence/attribution | Unit + E2E |
| 7 | D11 | Requested model/aspect/count differs from persisted editor state | High | Bind every requested setting and verify read-back; do not silently use Flow defaults | Unit + E2E |
| 8 | D3/D8 | `image i2i` uses a Unicode/space-containing local path | High | Upload through the migrated add menu, attach by observed media id/name, and assert references rode the submit | Integration + E2E |
| 9 | D11 | I2I uses UUID/name-only refs or character entities whose migrated attachment is unmeasured | High | Fail before submit with a precise exit-36 form unless the spike measures and tests it | Unit |
| 10 | D4/D5 | Native `-n 2..4` and multi-prompt image batch reuse the editor | High | Each prompt gets fresh listeners and exact count; no stale response attribution or double submission | Integration + E2E |
| 11 | D6/D12 | Successful result reaches recorder/download paths | High | Preserve media/workflow/project IDs, prompt redaction, and existing filename/type correction | Integration + E2E |
| 12 | D7 | Submit/status times out or Flow reports a failed terminal status | High | Raise the existing typed timeout/content-policy family with no false success | Unit |
| 13 | D1/D5 | Browser closes or profile lease is lost mid-generation | Medium | Existing lifecycle errors propagate and listeners detach | Unit |
| 14 | D13 | Direct MCP image generation uses migrated host | Critical | Same result and Problem Details semantics as CLI | MCP E2E |
| 15 | D13 | Queued MCP payload contains refs/model/aspect/count | Critical | `worker/codec.py` reconstructs the same request and no key is silently dropped | Unit + MCP E2E |

## Must-cover before merge

1. Live capture of the image mode, submit RPC, terminal result, signed image URL, and media bytes.
2. T2I and local-file I2I both assert image mode/settings and reference presence before success.
3. Unknown envelopes, untrusted URLs, wrong media bytes, and unsupported reference forms fail loud.
4. CLI and both MCP execution paths are exercised independently.
5. The existing labs-host image path remains green.

## Deferred

1. UUID/name-only image refs and character references may remain explicit exit-36 forms if the
   migrated picker does not expose a trustworthy binding contract during this slice.
2. Migrated-host new-project creation remains #639 scope outside this port; `--project` is required.

## Suggested BDD scenarios

```gherkin
Feature: Image generation on flow.google.com
  Scenario: Text-to-image uses the migrated Image mode
    Given an authenticated migrated profile and an existing Flow project
    When I run gflow image t2i with a supported model, aspect and count
    Then the page submits an image-generation request and saves exactly the requested images

  Scenario: Image-to-image binds a local reference before submit
    Given an authenticated migrated profile, an existing project and a local image
    When I run gflow image i2i with that image
    Then the outgoing image request contains the uploaded reference and the output is saved

  Scenario: An unmeasured migrated reference form is refused before billing
    Given an image request using a reference form the migrated picker cannot verify
    When generation is requested
    Then gflow exits with FlowHostMigratedError before clicking submit

  Scenario: MCP preserves the migrated image request
    Given the direct and queued gflow_generate_image surfaces
    When the same migrated-host request is submitted through each
    Then model, aspect, count and references reach the shared transport unchanged
```

## Known-issues cross-reference

This resolves the `image` portion of #639 / the first open `KNOWN_ISSUES.md` entry for
supported forms. It must not claim project creation, scenes, extend, instructions, tools, or
unmeasured reference forms are ported.
