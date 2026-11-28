# Scenario: headless-by-default direct transport (#1371 → #1545–#1550)

**Feature.** reCAPTCHA is minted in a headless Chrome by default. Text-to-image, the measured
image-to-image shape, text-to-video and image-to-video (start frame) at measured keys, and 1080p
video upscale go over direct HTTP on flow.google.com. Every other surface stays on the UI
transport, launched headed. A submit Flow refuses as `UNUSUAL_ACTIVITY` (not billed) is retried
once with a token from a fresh headless browser (headed only under an explicit `headed` setting).
The browser is used only to mint the token. The armed page is reused for the lifetime of one process (one command, or a
long-lived `gflow serve` / MCP server / worker task); it is not kept between commands.

**Decisions this analysis assumes** (recorded before Phase 3):

- Warmth is per process. Decision D3 (no browser or profile lease held while idle between
  commands) is unchanged; no ADR amendment is needed.
- Direct HTTP with a headless mint is the default for the measured shapes, on every platform,
  macOS included. macOS is documented as unmeasured until a run exists; the single retry from a
  fresh browser is the mitigation.
- Headed and headless are one adapter parameterised by a domain value
  (`BrowserVisibility`), with the retry policy in the client surface, not in the token source and
  not at each call site.
- The retry after a refusal does not switch to headed: in the spike, page freshness, not
  visibility, separated refused from accepted tokens.

Inputs: [the spike](../../spikes/2026-11-27-direct-video-and-headless-mint.md), the
pre-implementation analysis (five personas and two further reviews, verdict CAUTION once the warm
page is limited to one process).

## Coverage map

| Dimension | Active | Why |
|---|---|---|
| D1 Auth & session | yes | cookies rotate on long processes; the relaunch must keep the profile lease |
| D2 WAF / reCAPTCHA | yes | the core of the feature: headless mint, refusal, retry, burst |
| D3 Selectors & locale | yes | the UI transport still runs every unmeasured surface and must stay headed unless proven |
| D4 Batch & resume | yes | checkpoint timing, no resend after a send, `gflow run` and `movie run` mixing paths |
| D5 Concurrency | yes | the generation lock spans mint and POST today; 8 in flight measured |
| D6 Data layer | yes | the recorder must log direct submits exactly as UI submits |
| D7 Errors & exit codes | yes | `WafRejectionError` must not tell agents to retry; indeterminate outcomes |
| D8 Cross-platform | yes | macOS headless is unmeasured; Linux without a display |
| D9 Transport | yes | new RPCs `YhhmEf`, `maseQ`+`eb1hJf`, `p0UkFb`, `jwpduf`, `as29s` |
| D10 Headless vs headed | yes | the setting changes meaning; a headed launch needs a display |
| D11 Input validation | yes | unmeasured keys, portrait i2v, end frames must route to the UI |
| D12 Observability | yes | the route taken and any retry must be visible in logs and JSON |
| D13 MCP parity | yes | the same routing runs direct and queued through the worker |

## Scenario table

| # | Dim | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D2/D7 | Direct submit refused as `UNUSUAL_ACTIVITY` (HTTP 200, error frame, no record) | Critical | One retry with a token from a **fresh headless browser** (a new browser process; headed only under an explicit `headed` setting), same request id policy; credits unchanged; log `retried=true` | Unit (policy) + E2E live |
| 2 | D2/D7 | The retry is refused too | Critical | `WafRejectionError`, `charged="no"`, `retryable=False`, `next_action="none"`, remediation names `--browser-visibility headed` and the cooldown in `ACCOUNT_SAFETY.md`; no third send | Unit + BDD |
| 3 | D9/D4 | POST times out or the connection drops **after** the body was sent | Critical | Outcome `INDETERMINATE`: never resent; reconcile by reading the project; exit names "may have been charged" | Unit + Integration |
| 4 | D9 | 3xx / 401 / 403 after send | Critical | Not a refusal: indeterminate → reconcile. The old `_send_once` UI fallback after send is removed (it could submit twice) | Unit |
| 5 | D2 | `ConnectError` before any byte is sent | High | One reconnect allowed (nothing reached Flow); covered by an explicit test that asserts zero bytes sent | Unit |
| 6 | D10/D3 | A shape the direct path cannot serve (r2v, extend, edit, end frame, 4K upscale, character create) | Critical | Route decided **before launch** by a pure shape predicate; the UI path launches headed; it never runs on a headless page | Unit (predicate) + E2E |
| 7 | D10 | Direct path falls back to the UI after a failed mint (nothing sent) | Critical | Fallback relaunches headed for the UI path on a new page; a page whose token was refused is never reused (a refused page stayed refused 20 min in the spike) | Integration + E2E live |
| 8 | D1/D5 | The headed relaunch while another process waits on the same profile | Critical | `relaunch(visibility)` keeps the lease across close and launch; no `ProfileLockedError` in-process; the other process still sees the lock | Integration |
| 9 | D11 | Default `gflow video t2v` sends no model, so the key is the account's picker selection | High | Direct when the selection, read without a click, maps to a key in the measured allowlist; otherwise, or when the read fails, the UI path; the allowlist grows only with a live run | Unit + E2E live |
| 10 | D11 | i2v in portrait, i2v with an end frame (`nprQif`), ingredients (`MZZa6b`) | High | UI path (unmeasured), headed | Unit (predicate) |
| 11 | D11/D7 | `--max-credits` (or `GFLOW_CLI_MAX_CREDITS`) set on a direct video | Critical | Direct video has no wire quote, so a capped run routes to the UI path where the quote is enforced; never fails closed with exit 45 when a path exists, never submits uncapped | Unit + BDD |
| 12 | D4 | `submit_attempted` checkpoint written before the mint | High | Checkpoint written immediately before the POST and cleared on a refusal, so `gflow cancel` and recovery see the true state | Unit |
| 13 | D4 | `gflow cancel` while the direct POST is in flight | High | Reported `abandoned` (may still be charged), exit 22, as the UI path does | Integration |
| 14 | D5 | 8 images in flight on one page | High | Lock covers the mint only; at most 4 POSTs in flight per profile; a start-rate limit spaces submits; `GFLOW_CLI_CONCURRENCY` cannot raise the direct cap | Unit + E2E live |
| 15 | D2/D5 | Several refusals in one process | High | Superseded: the retry does not change visibility, so there is nothing to keep sticky; each request retries at most once from a fresh browser and a second refusal is final | Unit (row 2) |
| 16 | D2 | Armed page idle for 20 min, or the machine suspended for an hour | High | Re-arm (new page) when the wall-clock gap since the last mint exceeds the threshold, or after N mints; monotonic time is not used (it counts through sleep on Windows) | Unit (clock injected) + E2E live |
| 17 | D2 | One armed page serves submits to several projects | Medium | Allowed (measured 3/3); the page does not navigate per project | E2E live |
| 18 | D1 | Cookies rotate during a long `gflow serve` | High | Session headers are read from the live browser before each request, never cached; shell token (`at`) refreshed on 401 before a mint, not after a send | Integration |
| 19 | D1 | The profile's saved session is signed out | Medium | Same message and exit as today's UI path (`auth renew` remediation); no retry for an auth failure | BDD |
| 20 | D10/D8 | A headed launch (explicit `headed` setting, or a UI-bound shape) on Linux with no display, or in CI | High | Before launch, a clear `UiModeUnavailableError` (exit 28) naming the missing display; never misreported as `ProfileLockedError`. A headless launch, including the retry, needs no display | Unit + BDD |
| 21 | D8 | macOS headless (Keychain-protected cookies) | High | Unmeasured; headless is attempted; any cookie or mint failure retries once or reports clearly; docs say "unmeasured on macOS" | Doc + E2E when a Mac exists |
| 22 | D10 | The user sets the visibility explicitly | Medium | One setting `GFLOW_CLI_BROWSER_VISIBILITY=auto\|headless\|headed` (`--browser-visibility`); `headed` disables the headless mint; `GFLOW_CLI_HEADLESS` kept as a deprecated alias flagged by `gflow doctor` | Unit + BDD |
| 23 | D9 | A proxy is configured | Medium | Direct path skipped as today (`_proxy_configured`); UI path, headed | Unit |
| 24 | D9 | An account Flow serves on labs.google | Medium | Direct path is flow.google.com only; labs routes to the UI path | Unit |
| 25 | D9 | `jwpduf` returns status 4 with no media, or `as29s` has no download URL | High | Typed error after the existing poll budget; the clip is not resubmitted | Unit |
| 26 | D9 | `p0UkFb` upscale needs the workflow id and aspect, which the old upscale request lacks | High | Request carries both; resolved from the catalog or `as29s`; missing → UI path | Unit + E2E live |
| 27 | D9 | Downloaded mp4 host outside the allowlist | Medium | Refused by the existing SSRF guard | Unit |
| 28 | D6 | A direct submit is recorded | Medium | Same `OperationRecorder` events and fields as the UI path, plus `route` and `visibility`; prompt redaction respected | Unit |
| 29 | D7/D12 | Token or cookie text in an httpx exception | High | Redaction scrubs token prefixes beyond `0cAF` and the exception text before logging | Unit |
| 30 | D12 | Which path ran | Medium | `--json` output and the log carry `route: direct\|ui`, `visibility`, `retried: bool` | Unit |
| 31 | D13 | MCP `gflow_generate_video` with the same shapes | High | Same predicate, same retry; queued path round-trips any new key through `worker/codec.py` | Unit (parity) + E2E |
| 32 | D13 | Agent receives `WafRejectionError` | High | Problem Details with `retryable=false` and `next_action="none"`, so an agent does not retry into a storm | Unit |
| 33 | D13 | MCP `max_credits` | Critical | Same routing as row 11 | Unit |
| 34 | D4 | `movie run` / `video chain` with mixed shapes | Medium | Each clip routes independently; the warm page is reused across clips of one run | Integration |
| 35 | D3 | Headless UI automation (if the open spike shows it works for a surface) | Medium | That surface may move to headless only with its own measured evidence; until then headed | Spike |

Severity: **Critical** (billed twice, data loss, unrecoverable) · **High** (feature broken, workaround
exists) · **Medium** (degraded, explicit error) · **Low** (cosmetic).

## Must-cover before merge (Critical + High)

1. Rows 1–4: the single retry policy, keyed on the refusal envelope, at most one retry,
   everything ambiguous after send is indeterminate and reconciled, never resent.
2. Rows 6–8: route chosen before launch; UI path always headed; every retry on a new page;
   relaunch keeps the lease.
3. Rows 9–11, 33: direct only for measured keys and shapes; credit-capped video routes to the UI.
4. Rows 12–13: checkpoint timing and cancel.
5. Rows 14–16: mint-only lock, cap 4, a log-only start-rate warning, wall-clock re-arm, a refused page discarded.
6. Rows 18, 20, 21: cookie freshness, no-display message, macOS documented.
7. Rows 25, 26, 29, 31, 32: poll failure, upscale request, redaction, MCP parity, agent-safe error.

## Deferred (log as issues)

1. A page kept warm between commands (needs an amendment to D3 and an idle-release rule).
2. Concurrency above 8 and several warm pages per profile.
3. End frames, ingredients, extend, portrait i2v, 4K upscale over direct HTTP.
4. A Mac and a Linux live run.

## Suggested BDD scenarios

Offline, bound from `tests/features/test_direct_routing_steps.py`:

```gherkin
Feature: Route and refusal retry for direct generation

  Scenario: A refused direct submit is retried once from a fresh headless browser
    Given the direct path serves the request
    And Flow refuses the first submit as unusual activity
    When the generation runs
    Then exactly two submits are sent
    And the second runs on a new headless browser process
    And no credits are reported as charged

  Scenario: A refusal on both pages stops without a third submit
    Given Flow refuses every submit as unusual activity
    When the generation runs
    Then exactly two submits are sent
    And the error is not retryable and suggests no next action

  Scenario: A timeout after send is never resent
    Given the submit times out after the body was sent
    When the generation runs
    Then exactly one submit is sent
    And the outcome is indeterminate and the project is reconciled

  Scenario: A credit-capped video takes the UI path
    Given a video request at a measured key with a credit cap
    When the route is chosen
    Then the UI path is chosen and launched headed

  Scenario: An unmeasured shape takes the UI path headed
    Given an image-to-video request with an end frame
    When the route is chosen
    Then the UI path is chosen and launched headed
```

Live, bound from `tests/e2e/test_headless_direct_bdd.py`:

```gherkin
@e2e @e2e_video
Feature: Headless direct generation against Flow

  Scenario Outline: A measured shape runs headless and direct
    Given profile "<profile>" with browser visibility "<visibility>"
    When I run "<command>"
    Then the JSON result reports route "direct" and visibility "<visibility>"
    And a media file is written

    Examples:
      | profile  | visibility | command                         |
      | ffroliva | headless   | gflow video t2v (measured key)  |
      | denon82  | headed     | gflow video t2v (measured key)  |
      | ffroliva | headless   | gflow video upscale --1080p     |

@e2e @e2e_image
Feature: Headless direct images

  Scenario: Four images in flight from one armed page
    Given profile "denon82" with browser visibility "headless"
    When I run a four-row image batch
    Then four images are written
    And one page was armed for the whole run
```

## Known-issues cross-reference

- reCAPTCHA / WAF entries in `KNOWN_ISSUES.md`: the headless claim there ("headless → immediate
  403") is contradicted by the spike (15/15 headless cold mints accepted on two accounts) and is
  rewritten in this change, along with `docs/ACCOUNT_SAFETY.md`, `skills/gflow-cli/SKILL.md`,
  `.env.template`, `docs/CONFIGURATION.md`, the README FAQ, the AGENTS.md "Headed-browser
  dependency" section, `docs/DIRECT_TRANSPORT.md`, `docs/MCP.md` and the remediation hints in
  `api/recaptcha.py`.
- The redeem-bonus modal that blocked a `create_project` click (spike, "A UI-path observation")
  affects the UI transport only and is out of scope here.
