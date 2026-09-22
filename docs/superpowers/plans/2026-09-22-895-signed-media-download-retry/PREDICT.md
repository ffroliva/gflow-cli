# Predict: bounded retry for the migrated-host signed-media GET (#895)

## Verdict: CAUTION
**Confidence:** 5/10

Average of the five persona scores is 7.0 (Architect 6, Security 7, Performance 7, CLI/MCP 8,
Devil's Advocate 7). Applying the skill's modifier — *"a simpler approach the proposal missed →
downgrade by 2"* — lands at **5/10**. No persona returned a STOP signal. The three items CLI/MCP
filed under "STOP conditions" are conditional-on-implementation ("if you do X, stop"), all
avoidable inside the current scope, and that persona's own signal is GO; they are carried below
as mitigations rather than blockers.

**CAUTION is about the proposal's *shape*, not its intent.** The bug is real, the fix is cheap,
and nobody argued against fixing it. What four of five personas independently rejected is the
mechanism I proposed.

## Summary

The proposal as written — a new shared helper with a custom message-matching predicate, a custom
sub-second backoff, and a new typed error — **reinvents three things that already exist**, and
its own justification cites a constant that does not mean what the issue says it means. The
council converged on a materially smaller change: try Playwright's **native `max_retries`**
first, reuse **`NetworkError`** (exit 6, already documented as *"Network failure persisted across
3 attempts"*), and fix **three** call sites rather than two.

## Persona findings

### Architect — CAUTION (6/10)

- **The proposal's premise is false.** `api/_retry.py:136-141` builds its predicate as
  `(NetworkError, RateLimitError, *retryable_engine_errors())`, and
  `api/_engine.py::retryable_engine_errors()` returns `playwright.async_api.Error` +
  `TimeoutError`. Its docstring (`_retry.py:120-129`) says so verbatim: *"so a TCP reset / DNS
  hiccup / connect timeout mid-attempt is retried rather than surfaced raw to callers."* There is
  no Playwright coupling to introduce. That is also why `grep -rn ECONNRESET src/` is empty —
  nothing needs to string-match it, and adding a substring match would be a *new and worse*
  coupling, the error-message analogue of the locale-dependent selectors AGENTS.md forbids.
- **`post_with_retry` is not POST-shaped; only its name is.** `_retry.py:146-167` returns a bare
  `AsyncRetrying.__aiter__()`; `client.py:2777` already wraps an arbitrary coroutine in it.
- **The labs arm already does this** (`client.py:2034-2040`). The migrated arm is the outlier, so
  the correct framing is *convergence on an existing pattern*, not a new abstraction.
- **Third call site:** `ui_automation_video.py:1255` (in `_download_video`, :1240) — same shape,
  same billed-clip blast radius, no retry. Patching two sites violates the root-cause rule.
- **`NetworkError` already exists** (`errors.py:357`, exit 6 at `:1415`) and forwards
  `remediation_hint` via `FlowApiError.__init__` (`:180-205`). A new class means a new exit code —
  a public contract change for no gain. The real defect is `_retry.py:142 reraise=True`, so
  translation to `NetworkError` belongs at the call site.
- **Duplication is the finding, not the retry.** `_fetch_mp4` (`migrated_composer.py:2775`) is
  ~90% duplicated by `migrated_recover._fetch_verified` (`:149-174`); they differ only in policy
  (composer warns on size mismatch at `:2806-2812`, recover hard-errors at `:226`, deliberately,
  documented at `:19-23`). The shareable unit is a primitive, not the whole method.
- **Cycle pressure is one edge, not two.** `ui_automation.py:44` imports `migrated_composer` at
  module top, so the deferred import at `:2780` is a real cycle break. But `migrated_recover` is
  imported only by `services/media_recovery.py:115` (itself deferred) and tests — the
  `# noqa: PLC0415 - import cycle` at `migrated_recover.py:151` is cargo-culted.
- **Do not add a `RetryPolicy` Protocol or a `RetryConfig` dataclass.** One implementation, one
  policy, no configurability anyone sets — it would fail the D14 lens on the way in.

### Security / reCAPTCHA — CAUTION (7/10)

- **`max_redirects=0` is safe under retry by construction.** With `max_redirects=0` Playwright
  *returns* the 3xx as an `APIResponse` — which is why `:2799` / `:166` test `resp.status >= 300`
  on a returned object. A 3xx cannot reach an exception-typed predicate. The risk is a *future
  edit* to a helper that owns both the GET and the status check. Make it structurally impossible:
  retry wraps only the GET, `max_redirects=0` is hard-coded (never a parameter), the `>= 300`
  check stays outside.
- **Playwright's built-in `max_retries` is the safer rung.** Documented in the pinned stub as
  retrying `ECONNRESET` only and *"Does not retry based on HTTP response codes"* — bounded,
  in-transport, logs nothing of ours, un-widenable to status codes.
- **Allowlist: no gap today.** Both sites validate (`migrated_composer.py:2788-2795`,
  `migrated_recover.py:155-162`) via `ui_automation.py:179-195`. But a shared helper taking
  `url: str` without its own assertion makes an unguarded GET reachable by a future caller.
- **HIGH — the exception message is the leak channel.** Playwright builds messages as
  `parse_error(error, format_call_log(...))` (`_connection.py:422-423`, `:672-677`), concatenating
  the server-side call log. `observability.py:146-155` logs `to_problem_details()` verbatim and
  `json_output.py:56-68` puts `detail` on stdout verbatim; redaction runs **only** on the DB path
  (`worker/daemon.py:473`). A typed `GFlowError` also bypasses the message-hashing at
  `observability.py:163-178`. So the exhaustion error's `detail` is precisely where a signed URL
  escapes unredacted.
- **MED — a per-attempt `log.warning(..., url=url)` would be a *new* leak.** Today the migrated
  download paths log no URL; the only `url=` kwargs are page routes (`:963`, `:997`, `:1088`).
  structlog kwargs are never redacted (`observability.py:88-98`).
- **Rich markup is already handled** — `_cli_helpers.py:305-312` escapes `detail` and
  `remediation_hint` (shipped #813/#817). No escaping work needed.
- **WAF on the CDN: unevidenced.** `flow-content.google` authorizes by query signature, not
  cookies or a reCAPTCHA mint. The only WAF finding in the tree is `batchGenerateImages` 403 on
  `aisandbox-pa` (`KNOWN_ISSUES.md:933-968`). Do not claim the CDN participates; do not claim it
  doesn't.
- **LOW — the expired-URL 403 already ships wrong advice today.** It lands on
  `migrated_composer.py:2800-2804` / `migrated_recover.py:167-171`, neither of which passes a
  hint, so `WireFormatError`'s default fires: *"retry with a simpler prompt text"*
  (`errors.py:372-376`) — on a path with no prompt and no payload. Same wrong-advice class #875
  just removed.

### Performance / Playwright — CAUTION (7/10)

- **Nothing above `_fetch_mp4` bounds the download.** `poll_timeout_s` (default 600,
  `client.py:2938`) becomes `deadline` at `migrated_composer.py:2468` and is consumed entirely
  inside `submit_and_observe`; `download` is called at `:2946` *after* it returns.
  `grep wait_for|asyncio.timeout` over `worker/` and `services/` returns nothing. **Whatever
  number this change picks is the only ceiling in the stack.**
- **No TTL to blow, but the lock is the blast radius.** The profile lease has no TTL
  (`profile_lease.py:1-36` — advisory lock held for the holder's lifetime). The Page pool is not
  involved: `_generate_video_locked` binds `self._page` (`ui_automation_video.py:3966`), never
  `_checkout_page` — **zero `QueueFull` risk**. What *is* held is `self._generate_lock`
  (`ui_automation.py:1002`, taken at `ui_automation_video.py:3498`), shared with
  `generate_images`. So a stuck download stalls **every** generation on that client: concurrency
  cost is N→0, not N→N-1.
- **A naive `post_with_retry` wrap gives ≈543 s worst case** (180+1+180+2+180, `_retry.py:39`,
  `:100-103`) — and because `_retry.py:140` also retries `TimeoutError`, a wedged socket burns all
  three full timeouts.
- **Native `max_retries` keeps it inside one budget.** Read from the shipped driver
  (`driver/package/lib/coreBundle.js`, `_sendRequestWithRetries`): 250 ms → 500 ms backoff via
  `progress.wait()`, charged against the *same* call's progress budget, retrying only
  `e.code === "ECONNRESET"`. Worst case stays ≈180 s. The sleep happens in the driver's Node
  process, not gflow's event loop. **Source-read, not measured** — an ablation against a killed
  socket should be a plan task.
- **Node reports `socket hang up` with `code: 'ECONNRESET'`** — so two of the reporter's three
  named conditions are natively covered. `ECONNABORTED` is not.
- **180 s for 3.79 MB is a hang detector, not a transfer budget** (a 21 KB/s floor; ~30 s at
  1 Mbit/s). Do not shrink it here — that is scope creep needing a measured throughput
  distribution.
- **Range resume: no.** The driver calls `_storeResponseBody(body)` inside its fetch handler, so
  `resp.body()` at `:2805` retrieves an already-buffered blob. The retry unit is the whole request
  by construction, and streaming would mean abandoning `page.request` and losing the
  BrowserContext cookies the signed URL depends on.
- **Note, out of scope:** `path.write_bytes(body)` at `:2841` is a synchronous 3.79 MB write on
  the loop.

### CLI UX / Cross-platform — GO (8/10)

- **`NetworkError`, exit 6 — reuse, no new code.** `docs/USAGE.md:1922` already documents it as
  *"Network failure persisted across 3 attempts"* → *"Check connectivity"*: the proposal is
  literally the behaviour the exit-code table already promises. It is in `RETRYABLE_ERRORS`
  (`errors.py:1432`) so `retryable: true` is automatic and honest, and it declares no `__init__`
  (`:357-360`) so it accepts both `remediation_hint=` and `retryable=`.
- **Reject `WireFormatError`:** it declares its own `__init__` (`:374-391`) that drops `retryable=`
  and raises `TypeError`; exit 7 would report `retryable: false` on a transient fault; and its
  default remediation re-attaches the "simplify your prompt" advice #875 just removed.
- **MCP blast radius — both tools flatten the failure *worse* than the CLI today.**
  `gflow_download_media` (`mcp/tools.py:1437`): a bare Playwright error falls to `_guarded`'s
  `except Exception` (`:211`) → `_masked_unexpected_dict` (`:175-190`), so the agent gets
  *"Unexpected Error; details were logged server-side"* with **no `remediation_hint` key at all**.
  `gflow_generate_video` (`:989`) → worker queue → `daemon.py:476-484` replaces the message with
  `detail: "sha256:<hash>"` and emits **no `remediation_hint` and no `retryable`**. Typing the
  error fixes both for free: `daemon.py:459` already calls `to_problem_details()`.
- **Docstrings that become false: none** — checked `tools.py:1440-1446`, `:990-1001`,
  `docs/MCP.md:103`. Stated explicitly per the #626 rule.
- **New MCP-only hazard:** once the error carries `retryable: true`, an agent will re-call
  `gflow_download_media`, opening a second browser under the per-profile lease →
  `ProfileLockedError`, exit 11. Add a clause to the tool description.
- **No new CLI option, no new MCP param** — `worker/codec.py` has no error-path code; mirror axis A
  is untouched. Say so in the PR rather than leaving it blank.
- **`media_id` is in scope at both raise sites** (`record.media_id` at `:2798`; an explicit kwarg
  at `migrated_recover.py:148`). Ship the **real id**, not a `<media_id>` placeholder. Keep it in
  the first ~80 chars — the daemon truncates `detail` at 500.
- **One structlog event**, `log.warning("migrated.download_retry", media_id=…, attempt=n, of=3,
  error_class=…)`. `warning` matches the sibling anomaly `migrated.download_size_mismatch`
  (`:2806`). **No `url=`.** No success event — `migrated.download` at `:2848` already records it.
- **No new `GFLOW_CLI_*` var.** None of the 41 in `.env.template` are retry tunables. Module
  constant.
- **Iron Law:** `tests/e2e/test_data_download_e2e.py` already exists at `e2e_data` (zero credits,
  `pytestmark` :31). Provable there at $0 with `page.route(..., lambda r: r.abort("connectionfailed"))`.
  The `gflow_generate_video` twin is a separate surface (daemon envelope, not `_guarded`) — assert
  the daemon payload shape offline rather than burning Veo credits.
- ⚠️ **One factual error in this persona's output:** it states *"there is no third caller."*
  Architect and Performance both independently located `ui_automation_video.py:1255`. Corrected in
  conflict resolution below.

### Devil's Advocate — CAUTION (7/10)

- **The "sub-second because #871" justification misreads the tree.**
  `migrated_composer.py:317 RESULT_URL_GRACE_S = 20.0` and `migrated_recover.py:54
  SIGNED_URL_WAIT_S = 45.0` are waits for the URL to *appear in a batchexecute frame*, not TTLs on
  the URL. #871's title is "missed the 20s grace" — never observed, not expired. **No measurement
  anywhere in the tree says a signed URL dies inside 7 s.**
- **Alternatives, ranked:** (a) message-only, relying on the shipped `gflow data download` — viable
  and honest at ~3 lines, but 2-in-6 runs needing a manual second command is a bad product when
  retry is free on the happy path; keep it, don't stop at it. (b) fall through to `poster_url` —
  **reject**, `:2806` already rejects a non-`ftyp` body so the loop is a URL-ordering tiebreaker,
  not a transport fallback, and wiring a reset into it would replace a truthful "read ECONNRESET"
  with a false "no signed URL returned an MP4". (c) retry `download()` at `:2842` — safe and
  genuinely 3 lines, but fixes only one site, and `migrated_recover.py:165` is *the path the new
  hint points at*. (d) `Range` resume — **kill it**, no partial handle exists.
- **`client.py` correctly does NOT wrap `generate_video` in `post_with_retry`** (only
  `_run_with_retry` at :1819 and `generate_images` at :2777) — retrying a whole video re-bills.
- **Merge-conflict risk is lower than feared, with one surprise.** All four in-flight PRs already
  report `CONFLICTING/DIRTY` and must rebase regardless. Against `migrated_composer.py:2776-2850`:
  **#873** hunks at 311/2098/2280-2424, no hit on `_fetch_mp4`; **#787** hunks at 150-2056, no hit;
  **#882** touches `client.py:2038`, different file and function. **#824 adds a fourth download
  call site** — `agent_only_composer.py::download_video` with `max_redirects=5`, contradicting the
  documented open-redirect posture that PR's own plan claims to keep. Worth raising on #824
  independently.
- **The self-inflicted-race hypothesis is disproven by ordering.** `submit_and_observe` fully
  returns — running its `finally` at `:2510` that removes both listeners — before `download` at
  `:2946`. `grep page.route` over the file returns zero hits. What remains **unevidenced**: Angular's
  own `<video>` element plausibly fetching the same 3.8 MB URL concurrently from the same context.
- **Rollback:** plain `git revert`, ~12 lines, two files, no config key, no flag, no persisted
  state. Explicitly **do not** feature-flag it. No double-bill is possible — billing happened at
  submit, not at the GET.

## High-confidence risks (flagged by 2+ personas)

1. **The proposal reinvents `api/_retry.py`** — Architect, Performance, Devil's Advocate (3/5).
   The existing predicate already matches `playwright.async_api.Error`, which is what an
   `ECONNRESET` surfaces as. A second retry policy is the YAGNI violation, not the two call sites.
2. **`playwright.TimeoutError` subclasses `Error`** — Architect, Performance, CLI/MCP (3/5). A
   type-based retry that does not exclude it turns one 180 s timeout into three, holding
   `_generate_lock` and stalling every concurrent generation on the client.
3. **Signed-URL leakage via `str(exc)` in `detail`, or `url=` in a log kwarg** — Security (HIGH),
   CLI/MCP (STOP condition). Neither the CLI stderr path nor the MCP envelope redacts `detail`.
4. **A third unretried call site, `ui_automation_video.py:1255`** — Architect, Performance (2/5).
   Fixing two of three leaves a sibling caller broken. A fourth arrives with #824.
5. **The expired-URL 403 ships "simplify your prompt" advice today** — Security, CLI/MCP (2/5).
   Same wrong-advice class #875 removed. Fixable in the same change, same string.
6. **The "#871 grace window ⇒ sub-second backoff" justification is unevidenced** — Devil's
   Advocate, Performance (2/5). Both constants are *observation* waits, not URL TTLs.

## Conflicts resolved

- **Retry predicate: message allowlist (CLI/MCP) vs exception type only (Security).** Both are
  right about their own risk. **Resolved: neither, as stated.** Playwright's native `max_retries`
  matches on `e.code === "ECONNRESET"` — a structured Node error code, not a rendered-message
  substring and not a Python type — which satisfies Security's "never a substring" and CLI/MCP's
  "don't triple a 180 s timeout" simultaneously, because the native loop shares one budget. *If*
  a Python-level wrap is still needed (only if `ECONNABORTED` is a measured observation), then
  type-based **excluding `TimeoutError` by type**. No message matching in either branch.
- **Does the retry need to wrap `await resp.body()`? (Architect finding 6).** **Resolved against
  Architect, on evidence.** Performance read the shipped driver: `_storeResponseBody(body)` runs
  inside the fetch handler, so `resp.body()` at `:2805` retrieves an already-buffered blob;
  Devil's Advocate concurred independently, and the reporter's traceback lands on `get`. With
  native `max_retries` there is no Python-level wrap to extend anyway.
- **Shrink the per-attempt timeout? (Architect) vs keep 180 s (Performance).** **Resolved for
  Performance** — *because* native `max_retries` removes the 9-minute worst case that motivated
  Architect's suggestion. Shrinking then buys nothing and risks failing slow-but-working links.
- **"No third caller" (CLI/MCP) vs `ui_automation_video.py:1255` (Architect, Performance).**
  **Resolved against CLI/MCP** — two personas located the line independently. Three sites today,
  four once #824 merges.
- **Move the host allowlist into the helper (Security) vs that is a separate PR (Architect).**
  **Moot under the native-`max_retries` shape** — there is no new helper, so the guard stays at
  each call site. The live question it leaves behind: does `ui_automation_video.py:1255` guard its
  host at all? Open item below.

## Required mitigations before EXECUTE

1. **Try `max_retries=2` on the native call first.** Only reach for a Python-level wrap if
   `ECONNABORTED` turns out to be a *measured* observation — the reporter listed it; their
   traceback shows `ECONNRESET`, and Node reports `socket hang up` with that same code. Ask them.
2. **If a Python wrap is needed, reuse `post_with_retry()`** — do not write a second policy — and
   **exclude `TimeoutError` by type** from that call's predicate.
3. **Never interpolate `str(playwright_exc)` into `detail`.** Carry `type(exc).__name__` and the
   attempt count. Never log `url=`.
4. **Use `NetworkError` (exit 6).** No new class, no new exit code. Not `WireFormatError`.
5. **Fix all three call sites** — `migrated_composer.py:2798`, `migrated_recover.py:165`,
   `ui_automation_video.py:1255` — or record why not. Check whether `:1255` validates its host.
6. **Extend `remediation_hint` to the existing `>= 300` branches** so an expired signed URL stops
   advising a prompt simplification.
7. **Strike the "#871 grace window" justification** from the issue. The outcome (sub-second
   backoff) survives; the reason was wrong — it is 250/500 ms because the driver says so.
8. **Add the MCP tool-description clause** — "already retries internally; a `retryable` failure
   means re-call after a pause, not immediately" — to avoid an agent stacking its own loop into
   `ProfileLockedError`.
9. **Regenerate `website/docs/`** — `USAGE.md`, `MCP.md`, `KNOWN_ISSUES.md` are all in the
   generated set; `generate_website_docs.py --check` goes red otherwise. This is the #626 failure.

## Open items needing evidence (not blockers)

- **Does native `max_retries` really share one timeout budget?** Source-read from
  `coreBundle.js`, not measured. A 60 s ablation against a killed socket settles it — carry as a
  plan task.
- **Is `ECONNABORTED` real?** Ask the reporter whether they observed it or listed it.
- **Is the reset self-inflicted?** The named race is disproven, but Angular's own `<video>`
  fetching the same URL concurrently is unevidenced. Devil's Advocate's $0 probe:
  `for i in $(seq 1 20); do gflow data download <id> --out-dir tmp/reset-probe && echo OK || echo RESET; done`.
  If resets reproduce near 2-in-6, retry is the right remedy and the same run measures whether
  attempt 2 succeeds — the design's core unproven claim. If 20/20 pass, the fault is in-run page
  state and retry papers over a race. Caveat: `data download` re-observes a fresh URL per run, so
  it isolates the GET but is not a perfect control for live-composer page state.
- **#824's `max_redirects=5`** contradicts the documented open-redirect posture — raise on that PR.

## Recommended next step

Run the $0 reset probe above **before** writing code — it is the cheapest thing that could
disprove the design, and it doubles as the measurement of whether attempt 2 actually succeeds.
Then `/gflow:scenario` on the corrected shape (native `max_retries`, `NetworkError`, three call
sites), and `/gflow:plan`.
