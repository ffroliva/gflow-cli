# Per-Generation Credit Cap (`--max-credits`) Implementation Plan

> **For agentic workers:** REQUIRED: Use `skills/plan/SKILL.md` (or subagent-driven-development / executing-plans) to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

- **Issue:** [#1355](https://github.com/lanesket/gflow-cli/issues/1355) — `feat(credits): hard per-generation credit cap read from Flow's own quote (--max-credits)`
- **Phase 0 spike:** [`docs/superpowers/spikes/2026-11-20-credit-quote-before-submit.md`](../../spikes/2026-11-20-credit-quote-before-submit.md)
- **Scenario matrix & Gherkin:** [`SCENARIO.md`](SCENARIO.md)
- **Date:** 2026-11-20

---

## Pre-Implementation Predict Review (`/gflow:predict`)

**Proposal:** Add `--max-credits N` (CLI), `GFLOW_CLI_MAX_CREDITS=N` (env default), and `max_credits: int | None = None` (MCP `gflow_generate_video`) to enforce a hard per-command credit ceiling read from Flow's own pre-submit quote across every video generation route, failing closed with `CreditCapExceededError` (exit 45 in both legacy and `GFLOW_CLI_EXIT_CODES=2` modes, `charged="no"`, `next_action="fix_input"`, Problem Details extensions `quoted` and `max_credits`) before submit when the quote exceeds the ceiling or cannot be read.

**Verdict:** **CAUTION** — confidence **7.8 / 10**

Proceed with implementation subject to the five design mitigations below (non-single-chokepoint coverage for `video extend`, agent-only gate confirmation forcing, per-command cumulative deduction for `chain`/`movie`, fail-closed enforcement on unquoted routes, and size-gate extraction for `mcp/tools.py` and `_mc_settings_pane.py`).

### Five-Persona Analysis

#### 1. Architect (Boundary, Coupling & Chokepoint Analysis)
- **Correction to Phase 1 assessment's "single chokepoint" claim:** `FlowApiClient.generate_video` (`src/gflow_cli/api/_flow_generation.py`) is the chokepoint for `video t2v`, `video i2v`, `video r2v`, `video generate`, `video chain`, `movie run`, and MCP `gflow_generate_video`. However, **`gflow video extend` does not call `FlowApiClient.generate_video`**. It dispatches along two separate paths:
  1. Labs direct-wire extend: `cli_video.py::_extend_session` -> `client.capability_listing` (`video_extend.resolve_extend_model`, which already reads `creditMapping[serviceTier].cost`) -> `client.extend_video` (`src/gflow_cli/api/_flow_media_edit.py`).
  2. Migrated UI extend on `flow.google.com`: `cli_video.py::_extend_migrated_session` -> `client.extend_video_migrated` -> `migrated_extend.py::run_extend`.
- **Correction on Labs UI `t2v`/`i2v`/`r2v`:** `ui_automation_video.py` (`route == "labs"`) drives `ClassicFlowUiDriver` (`drivers/classic.py`), does not call `projectInitialData`, has no measured DOM quote anchor, and allows `--model` to be omitted (leaving Flow's sticky UI model unknown to gflow). Therefore `route == "labs"` in `ui_automation_video.py` cannot compute a trustworthy quote and must fail closed when `max_credits is not None`.
- **Boundary placement:** Put pure quote parsing, cap comparison, and cumulative budget deduction in a dedicated module `src/gflow_cli/api/credit_cap.py`. Have each transport/route invoke `enforce_credit_cap(quoted, max_credits, ...)` at the exact pre-submit boundary, and store `quoted_credits: int | None = None` on `VideoResult` so multi-step orchestrators (`chain.py`, `cli_movie.py`) deduct the actual quoted spend per completed step.

#### 2. Security / reCAPTCHA Engineer
- **Zero-mint / zero-RPC refusal:** On the migrated classic composer (`_mc_settings_pane.py::apply_video_settings`), the quote element (`flow-credit-cost-label.settings-credit-cost`) lives inside `.cdk-overlay-pane` while the settings pane is already open—before frame upload (`_attach_start_frame` / `_attach_reference_frames`), before prompt fill, and before clicking `SUBMIT_BUTTON` (which is what mints the enterprise reCAPTCHA token and dispatches `batchGenerateVideos`). Checking the quote at the end of `apply_video_settings` guarantees zero reCAPTCHA token mints and zero generation RPCs on refusal.
- **Labs direct-wire extend:** Enforcing the cap in `_extend_session` after `resolve_extend_model` (and inside `client.extend_video` before `mint_token`) prevents wasting a reCAPTCHA token mint when `unit_cost * segments > max_credits`.

#### 3. Performance / Playwright Specialist
- **Zero extra navigation or overlay open:** `apply_video_settings` already opens `.cdk-overlay-pane` and waits for settings controls to settle. Reading `pane.locator(CREDIT_COST_LABEL).first.inner_text()` while the pane is open adds one DOM text read (~5–15 ms) and zero extra clicks.
- **When `max_credits is None`:** Read the quote best-effort (populating `VideoResult.quoted_credits` when present, swallowing missing-element errors without failing) so normal unconstrained runs never break if Flow hides the badge for zero-cost or experimental tiers. When `max_credits is not None`, a missing or non-numeric quote raises `CreditCapExceededError(quoted=None, max_credits=max_credits)`.
- **Size-gate discipline (`scripts/ci/check_size.py`):**
  - `src/gflow_cli/mcp/tools.py` has a baseline ceiling of **2691** lines and is currently **2690** lines (1 line of headroom), and `_build_video_payload` is at `C901 = 11`. Compact `_build_video_payload` by iterating over `(key, value)` tuples instead of eight `if ... is not None:` branches, reducing `mcp/tools.py` by ~10 lines and `_build_video_payload`'s C901 from 11 to 4.
  - `_mc_settings_pane.py::SettingsPaneMixin.apply_video_settings` (`C901 = 12`), `migrated_composer.py::run_video` (`C901 = 13`), `cli_video.py::_extend_session` (`C901 = 26`, `PLR0915 = 102`), and `cli_video.py::_run_chain` (`PLR0915 = 53`) are all ratcheted in `scripts/ci/size_baseline.json`. All new branching lives in `src/gflow_cli/api/credit_cap.py` or single helper calls so no ratcheted function's complexity or statement count increases.

#### 4. CLI and MCP UX / Cross-Platform Engineer
- **Per-command vs per-submit semantics (Decision & Justification):**
  - `--max-credits N` / `GFLOW_CLI_MAX_CREDITS=N` / MCP `max_credits=N` is a **per-command credit cap** (bounding total credits quoted across all submissions made by a single command invocation):
    1. On single-submit commands (`video t2v`, `i2v`, `r2v`, `generate`, MCP `gflow_generate_video`), Flow's settings-pane quote already incorporates `--count` (`x1..x4`), so per-command and per-submit are identical.
    2. On multi-step commands (`video chain`, `movie run`, and multi-segment labs `video extend --segments S`), an autonomous agent or operator passing `--max-credits 20` expects the command as a whole not to spend more than 20 credits. If `--max-credits 20` were enforced only per link, a 5-link chain at 16 credits/link would silently spend 80 credits—violating the core safety guarantee of a budget cap.
    3. Implementation of per-command enforcement is small and deterministic:
       - Labs `video extend --segments S` already computes `total_cost = unit * segments` pre-flight in `_extend_session`; comparing `total_cost <= max_credits` before segment 1 refuses the entire extend command at $0 (`charged="no"`).
       - `video chain` and `movie run` track `spent_credits = 0` across links/scenes using `result.quoted_credits`, passing `remaining = max_credits - spent_credits` on each step's `GenerateVideoRequest.max_credits`. If step 0 exceeds `max_credits`, exit 45 has `charged="no"`. If step `k > 0` in `chain` exceeds `remaining`, step `k` is not submitted and `chain.py`'s existing partial-completion handler sets `charged="yes", next_action="reconcile"` with `completed_media_ids` for steps `0..k-1`. In `movie run`, a `CreditCapExceededError` halts the run immediately even when `--continue-on-error` is set, after saving the handoff manifest for completed scenes.
- **Exit code 45 verification:**
  - `src/gflow_cli/errors.py::EXIT_CODE_MAP` uses codes `3..32` and `34..39` (with `33` reserved for `gflow doctor`).
  - `_CORE_V2_EXIT_CODES` (`GFLOW_CLI_EXIT_CODES=2`) remaps `4->40, 5->41, 6->42, 7->43, 8->3, 9->44`.
  - `docs/ERRORS.md` reserves `40..63` for specific error causes. **45** is unused in both mode 1 and mode 2, so `CreditCapExceededError: 45` in `EXIT_CODE_MAP` yields exit **45** in both modes without any `_CORE_V2_EXIT_CODES` override.
- **MCP & CLI Parity (`tests/mcp/test_cli_parity.py`):**
  - Adding `--max-credits` (`max_credits`) to `video t2v`, `video i2v`, `video r2v` (and `video generate` via `bridge_params`), `video chain`, `video extend`, and `movie run` requires adding `max_credits: int | None = None` to `gflow_generate_video` in `src/gflow_cli/mcp/tools.py` so `test_every_cli_option_reaches_its_mcp_tool` passes (`video chain`, `video extend`, and `movie run` are in `_MCP_EXEMPT`).
  - Image generation (`gflow image ...`, `gflow_generate_image`) costs 0 Veo credits (confirmed by the Phase 0 spike: Nano Banana 2.1 quotes `0 credits`) and draws on a separate daily image quota; `--max-credits` is scoped to billed video commands and `gflow_generate_video`.

#### 5. Devil's Advocate / Failure Mode Analyst
- **What if the agent-only composer (#799) has confirmation set to "Never"?**
  - On `agent_only_composer.py`, the credit quote only appears in `LIVE_GATE` (`flow-permission-message p.title`) when Flow's confirmation gate is active. If `GFLOW_CLI_AGENT_CONFIRM=never`, the gate is suppressed and clicking submit bills immediately.
  - **Mitigation:** When `request.max_credits is not None` on `agent_only_composer.py`:
    1. If `_confirm_setting() == "never"`, refuse pre-submit before `apply_defaults` or `SUBMIT` with `CreditCapExceededError(quoted=None, max_credits=request.max_credits, charged="no")`.
    2. Otherwise, pass `confirm="always"` to `apply_defaults` so the gate is forced on even if the account's sticky setting was `"Never"`, parse `quoted = gate_quoted_credits(title)` (`numbers[-1]`) in `_gate_within_count`, and if `quoted > run.max_credits` (or the title does not settle with a number), click `REJECT_ROW` (`_reject_gate`) and raise `CreditCapExceededError`.
- **What if `.cdk-overlay-pane` contains other numbers (`4s`, `8s`, `720p`, `x4`, `Omni 1.2`)?**
  - **Mitigation:** Never match digits on `.cdk-overlay-pane` as a whole. Anchor strictly on `flow-prompt-box-settings div.settings-content > flow-credit-cost-label.settings-credit-cost` (the custom element measured in the Phase 0 spike across `en` and `pt-BR`), and parse the single integer inside its text. If zero or more than one distinct integer token appears inside `flow-credit-cost-label`, treat the quote as unreadable (`None`) and fail closed when `max_credits is not None`.
- **What if `--max-credits 0` is passed?**
  - `--max-credits 0` is a valid "refuse anything that costs credits" guard (analogous to a zero-spend check against a live quote): if Flow quotes `4`, `4 > 0` raises `CreditCapExceededError` (exit 45); if Flow quotes `0`, `0 <= 0` succeeds. Negative values (`< 0`) are rejected at input validation (Click `click.IntRange(min=0)` -> exit 2; Pydantic `Field(default=None, ge=0)` / `GenerateVideoRequest` `ValueError`).

### Per-Route Enforcement Table

| Surface / Route | Code Path | Quote Source | Enforcement Point (Before Submit) | Behavior When Quote Unreadable |
|---|---|---|---|---|
| `flow.google.com` classic composer (`video t2v`, `i2v`, `r2v`, `generate`, `chain`, `movie run`, MCP `gflow_generate_video`) | `src/gflow_cli/api/transports/_mc_settings_pane.py::SettingsPaneMixin.apply_video_settings` | `.cdk-overlay-pane flow-prompt-box-settings div.settings-content > flow-credit-cost-label.settings-credit-cost` integer token | End of `apply_video_settings`, after model/aspect/count/duration are selected and **before** `_close_pane`, frame upload, prompt entry, or `SUBMIT_BUTTON` click | Fail closed if `max_credits is not None`: raise `CreditCapExceededError(quoted=None, max_credits=N, charged="no")` |
| `flow.google.com` agent-only composer (#799, `video t2v`, MCP `gflow_generate_video`) | `src/gflow_cli/api/transports/agent_only_composer.py::run_agent_video` / `_gate_within_count` | `flow-permission-message p.title` (`LIVE_GATE` `GATE_TITLE`) trailing integer (`_NUMBER_RE.findall(title)[-1]`) | Pre-submit refusal if `GFLOW_CLI_AGENT_CONFIRM=never`; otherwise force `confirm="always"`, inspect `LIVE_GATE` title before `APPROVE_BUTTON`, and click `REJECT_ROW` on cap breach | Fail closed: click `REJECT_ROW` and raise `CreditCapExceededError(quoted=None, max_credits=N, charged="no" if rejected else "maybe")` |
| Labs direct-wire `video extend` (`aisandbox-pa.googleapis.com`) | `src/gflow_cli/cli_video.py::_extend_session` and `src/gflow_cli/api/_flow_media_edit.py::MediaEditSurface.extend_video` | `projectInitialData` (`capability_listing`) `creditMapping[serviceTier].cost * segments` via `resolve_extend_model` | Pre-flight in `_extend_session` before scene creation, and in `client.extend_video` before reCAPTCHA `mint_token` / `batchAsyncGenerateVideoExtendVideo` POST | Fail closed if `unit_cost is None` and `max_credits is not None`: raise `CreditCapExceededError(quoted=None, max_credits=N, charged="no")` |
| `flow.google.com` UI `video extend` | `src/gflow_cli/api/transports/migrated_extend.py::run_extend` (and `cli_video.py::_extend_migrated_session`) | None (Flow's extend action menu displays no credit quote before submission) | Top of `run_extend` / `_extend_migrated_session` before opening the item menu or clicking extend | Always fail closed when `max_credits is not None`: raise `CreditCapExceededError(quoted=None, max_credits=N, charged="no")` |
| `labs.google` legacy UI video composer (`video t2v`, `i2v`, `r2v`) | `src/gflow_cli/api/transports/ui_automation_video.py::generate_video` (`route == "labs"`) | None (`ClassicFlowUiDriver` does not call `projectInitialData` and has no measured DOM quote anchor) | Before `_apply_video_settings` / `_submit_and_poll` on the `route == "labs"` branch | Always fail closed when `max_credits is not None`: raise `CreditCapExceededError(quoted=None, max_credits=N, charged="no")` |

### Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `mcp/tools.py` exceeds its 2691-line ceiling or `_build_video_payload` exceeds C901=11 | High (currently 2690 lines, C901=11) | CI `check_size.py` fails | Refactor `_build_video_payload` to loop over `(key, value)` pairs while adding `max_credits` (Task 6) |
| Ratcheted functions (`apply_video_settings`, `run_video`, `_extend_session`, `_run_chain`) gain a branch | High | CI `check_size.py` fails | Place all branching inside `src/gflow_cli/api/credit_cap.py` and invoke via unconditional 1-line helper calls (Tasks 3–5) |
| Flow changes or omits `flow-credit-cost-label` on a future frontend rollout | Medium | Unbounded spend if fallback were used | Fail closed (`quoted=None`, exit 45, `charged="no"`) whenever `max_credits is not None`; never guess from a local table |
| Multi-step `movie run --continue-on-error` continues attempting later scenes after a cap breach | Medium | Later smaller scene could spend remaining credits after the operator's movie budget failed | Treat `CreditCapExceededError` in `cli_movie.py` as a hard budget halt that saves handoff state and exits 45 immediately (Task 5) |

---

## Architecture & File Map

- **Create:**
  - `src/gflow_cli/api/credit_cap.py` — pure quote parser (`parse_credit_quote`), enforcement helper (`enforce_credit_cap`), and cumulative per-command budget tracker (`CreditBudget`).
  - `tests/api/test_credit_cap.py` — unit tests for quote parsing, fail-closed enforcement, `CreditBudget`, `GenerateVideoRequest.max_credits`, and `Settings.max_credits`.
  - `tests/features/credit_cap.feature` + `tests/features/test_credit_cap_steps.py` — offline BDD scenarios.
  - `tests/test_cli_video_credit_cap.py` + `tests/test_cli_movie_credit_cap.py` — CLI option precedence, cumulative `chain`/`movie` deduction, `extend` pre-flight refusal, and `--dry-run` output tests.
  - `tests/features/credit_cap_refusal.feature` + `tests/e2e/test_credit_cap_refusal_bdd.py` — `@e2e @e2e_auth` ($0 CLI + MCP refusal with route-abort backstop and balance check) and `@e2e @e2e_video` (compliant paid run).
- **Modify:**
  - `src/gflow_cli/errors.py` — add `CreditCapExceededError` (`quoted: int | None`, `max_credits: int`), add `quoted` and `max_credits` fields to `ProblemDetails` and `to_problem_details()`, map `CreditCapExceededError: 45` in `EXIT_CODE_MAP`.
  - `src/gflow_cli/config.py` + `.env.template` — add `max_credits: int | None = Field(default=None, ge=0)` (`GFLOW_CLI_MAX_CREDITS`).
  - `src/gflow_cli/api/video.py` — add `max_credits: int | None = None` (validated `>= 0` when set) to `GenerateVideoRequest` and `quoted_credits: int | None = None` to `VideoResult`.
  - `src/gflow_cli/flow_selectors/registry.py` — add `CREDIT_COST_LABEL` to `MigratedComposerSelectors`.
  - `src/gflow_cli/api/transports/_mc_settings_pane.py` & `src/gflow_cli/api/transports/migrated_composer.py` — read and enforce the settings-pane quote at the end of `apply_video_settings` and return `quoted_credits` into `VideoResult`.
  - `src/gflow_cli/api/transports/agent_only_composer.py` — enforce `max_credits` via confirmation gate (`gate_quoted_credits`), rejecting via `REJECT_ROW` on cap excess or unreadable quote, and failing closed before submit when `GFLOW_CLI_AGENT_CONFIRM=never`.
  - `src/gflow_cli/api/transports/ui_automation_video.py` & `src/gflow_cli/api/transports/migrated_extend.py` — fail closed on `route == "labs"` UI video composer and on `flow.google.com` UI `video extend` when `max_credits is not None`.
  - `src/gflow_cli/api/_flow_media_edit.py` — add `max_credits: int | None = None` to `extend_video` and `extend_video_migrated`, enforcing `unit_cost <= max_credits` before reCAPTCHA mint.
  - `src/gflow_cli/cli_video.py`, `src/gflow_cli/chain.py`, `src/gflow_cli/cli_movie.py` — add `--max-credits` (`click.IntRange(min=0)`), resolve CLI flag over `settings.max_credits`, thread cumulative `CreditBudget` through `chain` and `movie run`, enforce `total_cost <= max_credits` in `_extend_session`, and display `max_credits` in `--dry-run`.
  - `src/gflow_cli/mcp/tools.py`, `src/gflow_cli/worker/codec.py`, `src/gflow_cli/worker/daemon.py` — add `max_credits: int | None = None` to `gflow_generate_video`, compact `_build_video_payload`, round-trip `max_credits` in `build_video_request`, and fall back to `get_settings().max_credits` in the worker when unset.
  - `src/gflow_cli/cli_capabilities.py`, `docs/CAPABILITIES.md`, `docs/USAGE.md`, `docs/CONFIGURATION.md`, `docs/MCP.md`, `docs/ERRORS.md`, `CHANGELOG.md`, `README.md`, `skills/gflow-cli/SKILL.md` (+ generated mirrors via `generate_plugin_skills.py` and `generate_website_docs.py`).

---

## Ordered Tasks (Test-First)

### Task 1: Unit Test Scaffold for `CreditCapExceededError`, Quote Parsing, Config & Budget Tracker (Red)

**Files:**
- Create: `tests/api/test_credit_cap.py`
- Modify: `tests/test_errors_classification.py`
- Modify: `tests/test_problem_details_extensions.py`

- [x] **Step 1: Write failing unit tests in `tests/api/test_credit_cap.py`**

Cover:
1. `parse_credit_quote("Generating will use 4 credits") == 4`, `parse_credit_quote("A geração vai usar 12 créditos") == 12`, `parse_credit_quote("Generating will use 0 credits") == 0`.
2. `parse_credit_quote("") is None`, `parse_credit_quote("Generating video...") is None`, `parse_credit_quote("4s 12 credits") is None` (rejects ambiguous multi-number text).
3. `enforce_credit_cap(quoted=4, max_credits=4)` returns `4`; `enforce_credit_cap(quoted=4, max_credits=3)` raises `CreditCapExceededError` with `err.quoted == 4`, `err.max_credits == 3`, `err.charged == "no"`, `err.next_action == "fix_input"`.
4. `enforce_credit_cap(quoted=None, max_credits=10, route="flow.google.com settings pane")` raises `CreditCapExceededError` with `err.quoted is None`, `err.max_credits == 10`, `err.charged == "no"` (fail-closed).
5. `enforce_credit_cap(quoted=None, max_credits=None)` returns `None` without raising.
6. `enforce_credit_cap(quoted=4, max_credits=0)` raises `CreditCapExceededError(quoted=4, max_credits=0)`; `enforce_credit_cap(quoted=0, max_credits=0)` returns `0`.
7. `CreditBudget(max_credits=6)`: `remaining` starts at `6`; `record_spend(4)` leaves `remaining == 2`; next step receives `max_credits=2`. When `quoted_credits is None` while `max_credits is not None`, `record_spend(None)` raises `CreditCapExceededError`.
8. `GenerateVideoRequest(prompt="test", max_credits=-1)` raises `ValueError("max_credits must be >= 0")`.
9. `Settings(max_credits=10)` and `GFLOW_CLI_MAX_CREDITS=10` load `10`; `GFLOW_CLI_MAX_CREDITS=-1` raises `ValidationError`.

- [x] **Step 2: Add failing error-code and Problem Details extension tests in `tests/test_errors_classification.py` and `tests/test_problem_details_extensions.py`**

Assert:
- `get_exit_code(CreditCapExceededError(quoted=8, max_credits=4))` returns `45` both when `GFLOW_CLI_EXIT_CODES` is unset/1 and when `GFLOW_CLI_EXIT_CODES=2`.
- `CreditCapExceededError(quoted=8, max_credits=4).to_problem_details().model_dump(exclude_none=True)` contains:
  - `"type": "https://gflow-cli.dev/errors/credit-cap-exceeded"`
  - `"status": 422`
  - `"exit_code": 45`
  - `"charged": "no"`
  - `"next_action": "fix_input"`
  - `"quoted": 8`
  - `"max_credits": 4`
- When `quoted=None`, `to_problem_details()` serializes `"max_credits": 4` and `"quoted": None` (or omits `None` when `exclude_none=True` while preserving `err.quoted is None` on the exception and `"quoted": None` in JSON error output).

- [x] **Step 3: Run pytest on the new tests and confirm they fail (Red)**

Run: `uv run python -m pytest tests/api/test_credit_cap.py tests/test_errors_classification.py tests/test_problem_details_extensions.py -q`
Expected: `ImportError` / `AttributeError` for `gflow_cli.api.credit_cap` and `CreditCapExceededError`.

---

### Task 2: Offline BDD Feature & Step Definitions Scaffold (Red)

**Files:**
- Create: `tests/features/credit_cap.feature`
- Create: `tests/features/test_credit_cap_steps.py`

- [x] **Step 1: Create `tests/features/credit_cap.feature` with the 5 offline scenarios from [`SCENARIO.md`](SCENARIO.md)**

Include:
- `Scenario Outline: Settings-pane credit quote parser extracts the integer across locales`
- `Scenario: Settings-pane credit quote enforces fail-closed when the anchor is missing or non-numeric`
- `Scenario: CreditCapExceededError preserves exit code 45 in both legacy and v2 exit-code modes`
- `Scenario: Multi-link video chain deducts each link's quote from the per-command budget`
- `Scenario: MCP video payload codec round-trips max_credits to GenerateVideoRequest`

- [x] **Step 2: Create `tests/features/test_credit_cap_steps.py` binding `credit_cap.feature`**

- [x] **Step 3: Run pytest on `tests/features/test_credit_cap_steps.py` and confirm it fails (Red)**

Run: `uv run python -m pytest tests/features/test_credit_cap_steps.py -q`

---

### Task 3: Error Class, Config Setting, Request/Result DTOs & `credit_cap.py` Core (Green for Tasks 1–2)

**Files:**
- Create: `src/gflow_cli/api/credit_cap.py`
- Modify: `src/gflow_cli/errors.py`
- Modify: `src/gflow_cli/config.py`
- Modify: `.env.template`
- Modify: `src/gflow_cli/api/video.py`

- [x] **Step 1: Add `CreditCapExceededError` and `ProblemDetails` extensions in `src/gflow_cli/errors.py`**

1. On `ProblemDetails` (`src/gflow_cli/errors.py:92`), add:
   ```python
   quoted: int | None = None
   max_credits: int | None = None
   ```
2. On `GflowError` (`src/gflow_cli/errors.py:130`), add class defaults:
   ```python
   quoted: int | None = None
   max_credits: int | None = None
   ```
   and pass `quoted=self.quoted, max_credits=self.max_credits` inside `GflowError.to_problem_details()`.
3. Define `CreditCapExceededError(GflowError)` after `InsufficientCreditsError` (`src/gflow_cli/errors.py:1703`):
   ```python
   class CreditCapExceededError(GflowError):
       """Flow's pre-submit credit quote exceeds the caller's --max-credits cap (or no quote is available)."""

       error_type = "credit-cap-exceeded"
       title = "Credit Cap Exceeded"
       status = 422
       default_next_action: NextAction = "fix_input"

       def __init__(
           self,
           detail: str | None = None,
           *,
           quoted: int | None = None,
           max_credits: int,
           route: str | None = None,
           instance: str | None = None,
           charged: ChargeState = "no",
           next_action: NextAction | None = None,
           completed_media_ids: list[str] | None = None,
       ) -> None:
           if detail is None:
               if quoted is None:
                   where = f" on {route}" if route else ""
                   detail = (
                       f"Cannot verify --max-credits {max_credits}{where}: "
                       "Flow did not expose a pre-submit credit quote. Refusing before submit."
                   )
               else:
                   detail = (
                       f"Flow quoted {quoted} credits for this generation, which exceeds "
                       f"--max-credits {max_credits}. Refused before submit; no credits were charged."
                   )
           super().__init__(
               detail,
               instance=instance,
               charged=charged,
               next_action=next_action,
               completed_media_ids=completed_media_ids,
           )
           self.quoted = quoted
           self.max_credits = max_credits
   ```
4. Register `CreditCapExceededError: 45` in `EXIT_CODE_MAP` (`src/gflow_cli/errors.py:1817`). Export `CreditCapExceededError` in `__all__` if present.

- [x] **Step 2: Add `max_credits` setting to `src/gflow_cli/config.py` and `.env.template`**

In `Settings` (`src/gflow_cli/config.py`):
```python
max_credits: int | None = Field(
    default=None,
    ge=0,
    description="Hard per-command credit cap enforced against Flow's pre-submit quote",
)
```
Document `# GFLOW_CLI_MAX_CREDITS=` in `.env.template`.

- [x] **Step 3: Add `max_credits` to `GenerateVideoRequest` and `quoted_credits` to `VideoResult` in `src/gflow_cli/api/video.py`**

In `GenerateVideoRequest`:
```python
max_credits: int | None = None
```
In `GenerateVideoRequest.__post_init__`:
```python
if self.max_credits is not None and (
    isinstance(self.max_credits, bool) or self.max_credits < 0
):
    raise ValueError("max_credits must be a non-negative integer")
```
In `VideoResult`:
```python
quoted_credits: int | None = None
```

- [x] **Step 4: Create `src/gflow_cli/api/credit_cap.py`**

Implement:
- `parse_credit_quote(text: str | None) -> int | None`: extracts digits via `re.findall(r"\d+", text)`; returns `int(matches[0])` only when `len(matches) == 1`, otherwise `None`.
- `enforce_credit_cap(quoted: int | None, max_credits: int | None, *, route: str = "Flow composer") -> int | None`: if `max_credits is None`, returns `quoted`; if `quoted is None` or `quoted > max_credits`, logs `credit_cap.refused` and raises `CreditCapExceededError(quoted=quoted, max_credits=max_credits, route=route, charged="no")`; otherwise logs `credit_cap.passed` and returns `quoted`.
- `resolve_max_credits(explicit: int | None, configured: int | None) -> int | None`: returns `explicit if explicit is not None else configured`.
- `CreditBudget`: small stateful helper for multi-step commands (`chain`, `movie run`) tracking `initial_cap: int | None` and `spent: int = 0`, providing `remaining() -> int | None` and `record_step(quoted: int | None, *, route: str) -> None`.

- [x] **Step 5: Run unit tests from Task 1 and confirms they pass**

Run: `uv run python -m pytest tests/api/test_credit_cap.py tests/test_errors_classification.py tests/test_problem_details_extensions.py -q`

---

### Task 4: Transport & API Enforcement Across All Routes

**Files:**
- Modify: `src/gflow_cli/flow_selectors/registry.py`
- Modify: `src/gflow_cli/api/transports/_mc_settings_pane.py`
- Modify: `src/gflow_cli/api/transports/migrated_composer.py`
- Modify: `src/gflow_cli/api/transports/agent_only_composer.py`
- Modify: `src/gflow_cli/api/transports/ui_automation_video.py`
- Modify: `src/gflow_cli/api/transports/migrated_extend.py`
- Modify: `src/gflow_cli/api/_flow_media_edit.py`
- Modify: `tests/api/test_credit_cap.py`

- [x] **Step 1: Add transport enforcement unit tests in `tests/api/test_credit_cap.py` (Red)**

Add fake-page / fake-locator tests covering:
1. Migrated classic composer (`SettingsPaneMixin.apply_video_settings` / `read_and_enforce_pane_quote`):
   - Reads `.cdk-overlay-pane flow-prompt-box-settings div.settings-content > flow-credit-cost-label.settings-credit-cost` across `en` (`"Generating will use 4 credits"`) and `pt-BR` (`"A geração vai usar 4 créditos"`).
   - Raises `CreditCapExceededError(quoted=4, max_credits=3)` before `_close_pane` when `max_credits=3`.
   - Raises `CreditCapExceededError(quoted=None, max_credits=3)` when the element is absent and `max_credits=3`.
   - Returns `4` (stored on `VideoResult.quoted_credits`) when `max_credits=4` or `max_credits=None`.
2. Agent-only composer (`agent_only_composer.py`):
   - `gate_quoted_credits("...kick off this 1 video generation, costing 10 credits?") == 10` and `gate_quoted_credits("Сгенерировать 1 видео за бонусы (10)?") == 10`.
   - When `max_credits=5` and `GFLOW_CLI_AGENT_CONFIRM=never`, refuses pre-submit with `CreditCapExceededError(quoted=None, max_credits=5, charged="no")`.
   - When `max_credits=5` and gate quotes `10`, clicks `REJECT_ROW` and raises `CreditCapExceededError(quoted=10, max_credits=5, charged="no")`.
3. Unquoted routes:
   - `migrated_extend.run_extend` with `max_credits=10` refuses immediately before clicking the item menu (`CreditCapExceededError(quoted=None, max_credits=10, charged="no")`).
   - `ui_automation_video.py` `route == "labs"` with `request.max_credits=10` refuses before `_submit_and_poll` (`CreditCapExceededError(quoted=None, max_credits=10, charged="no")`).
4. Labs direct-wire `MediaEditSurface.extend_video`:
   - When `max_credits=5` and `resolve_extend_model` returns `unit_cost=10`, raises `CreditCapExceededError(quoted=10, max_credits=5, charged="no")` before `mint_token`.

- [x] **Step 2: Register `CREDIT_COST_LABEL` in `src/gflow_cli/flow_selectors/registry.py`**

In `MigratedComposerSelectors`:
```python
CREDIT_COST_LABEL: str = (
    "flow-prompt-box-settings div.settings-content"
    " > flow-credit-cost-label.settings-credit-cost"
)
```

- [x] **Step 3: Enforce and return quote in `src/gflow_cli/api/transports/_mc_settings_pane.py` and `migrated_composer.py`**

In `_mc_settings_pane.py`:
- Add helper `_read_pane_credit_quote(self, pane: Any, max_credits: int | None) -> int | None` (delegating to `credit_cap.parse_credit_quote` and `credit_cap.enforce_credit_cap`) and call it at the end of `apply_video_settings` right before `await self._close_pane(page, pane)`.
- Have `apply_video_settings` take `max_credits: int | None = None` and return `int | None` (the parsed quote), adding zero `if` branches to `apply_video_settings` (`C901` stays 12).
- In `migrated_composer.py::run_video`, pass `max_credits=request.max_credits` into `self.apply_video_settings(...)`, capture `quoted_credits`, and attach `quoted_credits=quoted_credits` on the returned `VideoResult` (`C901` stays 13).

- [x] **Step 4: Enforce quote on `src/gflow_cli/api/transports/agent_only_composer.py`**

- Add `gate_quoted_credits(title: str) -> int | None` (returns `int(numbers[-1])` when `len(_NUMBER_RE.findall(title)) >= 2`, else `None`).
- Add `max_credits: int | None = None` to `_AgentRun`.
- In `run_agent_video`, call a helper `_prepare_agent_confirm_mode(request.max_credits)` that checks `_confirm_setting()`: if `request.max_credits is not None` and mode is `"never"`, raises `CreditCapExceededError(quoted=None, max_credits=request.max_credits, route="flow.google.com agent-only composer with GFLOW_CLI_AGENT_CONFIRM=never")`; if `request.max_credits is not None`, forces `confirm="always"`.
- In `_gate_within_count`, after confirming count matches, call `_enforce_agent_gate_credit_cap(page, title, run)`: when `run.max_credits is not None`, parse `quoted = gate_quoted_credits(title)` and if `quoted is None or quoted > run.max_credits`, call `rejected = await _reject_gate(page)` and raise `CreditCapExceededError(quoted=quoted, max_credits=run.max_credits, charged="no" if rejected else "maybe", next_action="fix_input" if rejected else "reconcile")`. Attach `quoted_credits` to `VideoResult`.

- [x] **Step 5: Fail closed on unquoted routes and enforce on `MediaEditSurface.extend_video`**

- In `src/gflow_cli/api/transports/ui_automation_video.py`: on the `route == "labs"` branch (before `_apply_video_settings`), call `enforce_credit_cap(None, request.max_credits, route="labs.google UI video composer")`.
- In `src/gflow_cli/api/transports/migrated_extend.py::run_extend` and `src/gflow_cli/api/_flow_media_edit.py::extend_video_migrated`: accept `max_credits: int | None = None` and call `enforce_credit_cap(None, max_credits, route="flow.google.com video extend")` before opening the menu.
- In `src/gflow_cli/api/_flow_media_edit.py::extend_video`: accept `max_credits: int | None = None`, and after `resolve_extend_model`, call `enforce_credit_cap(resolved.unit_cost, max_credits, route="video extend")` before minting reCAPTCHA.

- [ ] **Step 6: Run unit and offline BDD tests (Green)**

Run: `uv run python -m pytest tests/api/test_credit_cap.py tests/features/test_credit_cap_steps.py -q`

---

### Task 5: CLI Surface (`video t2v/i2v/r2v/generate/chain/extend`, `movie run`)

**Files:**
- Modify: `src/gflow_cli/cli_video.py`
- Modify: `src/gflow_cli/chain.py`
- Modify: `src/gflow_cli/cli_movie.py`
- Create: `tests/test_cli_video_credit_cap.py`
- Create: `tests/test_cli_movie_credit_cap.py`

- [x] **Step 1: Write failing CLI tests in `tests/test_cli_video_credit_cap.py` and `tests/test_cli_movie_credit_cap.py` (Red)**

Cover:
1. `--max-credits 4` on `gflow video t2v`, `i2v`, `r2v`, and `generate` populates `GenerateVideoRequest.max_credits == 4`.
2. `GFLOW_CLI_MAX_CREDITS=6` populates `max_credits == 6` when `--max-credits` is omitted; `--max-credits 10` overrides `GFLOW_CLI_MAX_CREDITS=6`.
3. `--max-credits -1` fails at Click validation with exit code 2.
4. `gflow video extend --segments 2 --max-credits 15` against a 10-credit/segment model refuses in `_extend_session` before creating a scene or calling `client.extend_video`, exiting with code **45** (`charged: "no"`, `quoted: 20`, `max_credits: 15`).
5. `gflow video extend --max-credits 15` on a migrated profile (`_extend_migrated_session`) fails closed before submit with exit code **45** (`quoted: None`, `max_credits: 15`).
6. `gflow video chain --max-credits 6` with two 4-credit links completes link 0 (`quoted_credits=4`), passes `max_credits=2` to link 1, catches link 1's `CreditCapExceededError`, and exits **45** with `charged: "yes"`, `next_action: "reconcile"`, `completed_media_ids: [link0_id]`.
7. `gflow movie run --max-credits 6 --continue-on-error` with two 4-credit scenes completes scene 1, refuses scene 2 with `CreditCapExceededError`, writes handoff state for scene 1, and exits **45** immediately without attempting a third scene.
8. `--dry-run --max-credits 12` on `video chain`, `video extend`, and `movie run` includes `"max_credits": 12` in JSON/table dry-run output and exits 0 without opening a browser.

- [x] **Step 2: Wire `--max-credits` across `src/gflow_cli/cli_video.py` and `src/gflow_cli/chain.py`**

- Add `--max-credits` (`type=click.IntRange(min=0), default=None, help="Refuse before submit if Flow's credit quote exceeds this cap (default: GFLOW_CLI_MAX_CREDITS)."`) to `_shared_video_options` (automatically inherited by `t2v`, `i2v`, `r2v`, `chain`, and bridged to `video generate` via `cli_gen.py::bridge_params`) and to `extend`.
- In `_run_video_generation`, resolve `effective_max_credits = resolve_max_credits(max_credits, settings.max_credits)` and pass `max_credits=effective_max_credits` to `GenerateVideoRequest`.
- In `chain.py::run_chain` / `_execute_chain_links`, accept `max_credits: int | None = None`, instantiate `budget = CreditBudget(max_credits)`, set each link's `request = dataclasses.replace(request, max_credits=budget.remaining())`, and after `client.generate_video(request)` call `budget.record_step(result.quoted_credits, route="video chain")`. Note: `chain.py:264` already catches `GflowError` after `completed_ids` is non-empty and sets `exc.charged = "yes"`, `exc.next_action = "reconcile"`, `exc.completed_media_ids = list(completed_ids)`!
- In `cli_video.py::_extend_session` (which is at `C901 = 26, PLR0915 = 102`), extract cost resolution + `enforce_credit_cap(total_cost, effective_max_credits, route="video extend")` into a module-level helper `_resolve_and_check_extend_cost(...)` so `_extend_session`'s complexity and statement count decrease rather than increase. Also pass `max_credits=effective_max_credits` to `_extend_migrated_session`.

- [x] **Step 3: Wire `--max-credits` in `src/gflow_cli/cli_movie.py`**

- Add `--max-credits` (`type=click.IntRange(min=0), default=None`) to `movie run`.
- Resolve `effective_max_credits = resolve_max_credits(max_credits, settings.max_credits)`.
- Track `budget = CreditBudget(effective_max_credits)` across scenes in `_run_movie`, passing `max_credits=budget.remaining()` into `_execute_scene` -> `GenerateVideoRequest` and calling `budget.record_step(result.quoted_credits, route="movie run")`.
- If `_execute_scene` raises `CreditCapExceededError`, record the failed scene, write handoff/state artifacts if any scene completed, and re-raise immediately even when `continue_on_error` is True.

- [x] **Step 4: Run CLI tests (Green)**

Run: `uv run python -m pytest tests/test_cli_video_credit_cap.py tests/test_cli_movie_credit_cap.py -q`

---

### Task 6: MCP Surface Mirror (`gflow_generate_video`, Worker Codec & Daemon)

**Files:**
- Modify: `src/gflow_cli/mcp/tools.py`
- Modify: `src/gflow_cli/worker/codec.py`
- Modify: `src/gflow_cli/worker/daemon.py`
- Modify: `tests/mcp/test_server.py`

- [ ] **Step 1: Add MCP & worker codec tests in `tests/mcp/test_server.py` and `tests/features/test_credit_cap_steps.py` (Red)**

Assert:
1. `gflow_generate_video` tool schema exposes `max_credits` (`integer | null`).
2. `_build_video_payload(..., max_credits=12)` includes `"max_credits": 12` when set and omits it when `None`; `_build_video_payload(..., max_credits=-1)` raises `ConfigurationError` (or `ValueError`).
3. `worker/codec.py::build_video_request({"prompt": "cat", "max_credits": 12}, ...)` produces `GenerateVideoRequest(..., max_credits=12)`.
4. `worker/daemon.py` applies `resolve_max_credits(request.max_credits, settings.max_credits)` before calling `client.generate_video(request)`.
5. `tests/mcp/test_cli_parity.py` (`test_every_cli_leaf_has_an_mcp_decision` and `test_every_cli_option_reaches_its_mcp_tool`) passes.

- [ ] **Step 2: Update `src/gflow_cli/mcp/tools.py` while shrinking line count and C901**

- Compact `_build_video_payload` (`src/gflow_cli/mcp/tools.py:165-198`) by replacing the eight `if x is not None: payload[...] = x` blocks with a `for key, val in (...):` loop that includes `("max_credits", max_credits)`. This drops `_build_video_payload`'s C901 from 11 to 4 and saves ~10 lines in `mcp/tools.py` (keeping it well under the 2691-line ceiling).
- Add `max_credits: int | None = None` to `gflow_generate_video` (`src/gflow_cli/mcp/tools.py:1149`) and pass `max_credits=max_credits` to `_build_video_payload`. Update the docstring to document `max_credits`.

- [ ] **Step 3: Update `src/gflow_cli/worker/codec.py` and `src/gflow_cli/worker/daemon.py`**

- In `src/gflow_cli/worker/codec.py::build_video_request`: extract `raw_max_credits = payload.get("max_credits")`, validate non-bool `int >= 0` when present (raising `QueueSchemaError` on invalid type/value), and pass `max_credits=raw_max_credits` to `GenerateVideoRequest`.
- In `src/gflow_cli/worker/daemon.py`: when `request.max_credits is None` and `settings.max_credits is not None`, replace `request` with `dataclasses.replace(request, max_credits=settings.max_credits)` before calling `client.generate_video`.

- [ ] **Step 4: Run MCP parity, server, BDD, and size checks (Green)**

Run:
```bash
uv run python -m pytest tests/mcp/test_cli_parity.py tests/mcp/test_server.py tests/features/test_credit_cap_steps.py -q
uv run python scripts/ci/check_size.py
```

---

### Task 7: E2E BDD Feature & Binding Guard (`@e2e @e2e_auth` & `@e2e_video`)

**Files:**
- Create: `tests/features/credit_cap_refusal.feature`
- Create: `tests/e2e/test_credit_cap_refusal_bdd.py`

- [ ] **Step 1: Create `tests/features/credit_cap_refusal.feature` and `tests/e2e/test_credit_cap_refusal_bdd.py`**

Follow [`docs/E2E_TESTING.md`](../../../E2E_TESTING.md) § BDD-bound e2e and the pattern in `tests/e2e/test_upscale_bdd.py`:
- Bind all three scenarios in `tests/features/credit_cap_refusal.feature`:
  1. `Migrated composer refuses before submit when Flow's quote exceeds --max-credits` (`@e2e @e2e_auth`, $0): records balance before, attaches a Playwright route-abort backstop on `**/v1/video:batchAsyncGenerate*` and `batchexecute` generation submits, runs a 4s x1 `omni-flash` generation with `max_credits=3`, asserts `CreditCapExceededError` (exit 45, `charged == "no"`, `quoted == 4`, `max_credits == 3`), asserts the backstop was never tripped, and verifies balance after is unchanged.
  2. `MCP gflow_generate_video refuses before submit when Flow's quote exceeds max_credits` (`@e2e @e2e_auth`, $0): invokes the MCP `gflow_generate_video` path with `max_credits=3`, asserts RFC 9457 error envelope with `exit_code == 45`, `charged == "no"`, `quoted == 4`, `max_credits == 3`, and unchanged balance.
  3. `Migrated composer submits and completes when Flow's quote equals --max-credits` (`@e2e @e2e_video`, opt-in via `GFLOW_CLI_E2E_RUN_VIDEO=1`, 4 credits): runs 4s x1 `omni-flash` with `max_credits=4` and asserts exit 0 and a downloaded MP4 file.

- [ ] **Step 2: Run the offline E2E binding guard test**

Run: `uv run python -m pytest tests/features/test_e2e_binding_guard.py -q`
Expected: PASS (every `@e2e`-tagged feature is bound only under `tests/e2e/`, and every untagged feature is bound under `tests/features/`).

---

### Task 8: Documentation, Capabilities, Skill Mirrors & Full Quality Gate (`/gflow:check`)

**Files:**
- Modify: `src/gflow_cli/cli_capabilities.py`
- Modify: `docs/CAPABILITIES.md`
- Modify: `docs/USAGE.md`
- Modify: `docs/CONFIGURATION.md`
- Modify: `docs/MCP.md`
- Modify: `docs/ERRORS.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `skills/gflow-cli/SKILL.md`
- Regenerate: `website/docs/*` and `.claude-plugin/*` via CI scripts

- [ ] **Step 1: Update `src/gflow_cli/cli_capabilities.py` and `docs/CAPABILITIES.md`**

Update the `generate.video` capability note in `src/gflow_cli/cli_capabilities.py` (and `docs/CAPABILITIES.md` if applicable) to mention that pre-submit credit caps (`--max-credits`, `GFLOW_CLI_MAX_CREDITS`, MCP `max_credits`) are enforced from Flow's own pre-submit quote on `flow.google.com` video generation and labs direct-wire `video extend`, and fail closed on routes without a pre-submit quote.

- [ ] **Step 2: Update `docs/ERRORS.md`, `docs/USAGE.md`, `docs/CONFIGURATION.md`, `docs/MCP.md`, `CHANGELOG.md`, `README.md`, and `skills/gflow-cli/SKILL.md`**

1. `docs/ERRORS.md`:
   - Add `https://gflow-cli.dev/errors/credit-cap-exceeded` | `422` | `45` | `no` (or `yes` after partial chain completion) | `fix_input` (or `reconcile` after partial chain completion) | Flow's pre-submit credit quote exceeds `--max-credits` / `max_credits`, or the active route does not expose a readable pre-submit quote when a cap is set.
   - Document the `quoted` (`int | null`) and `max_credits` (`int`) extension members on Problem Details.
2. `docs/USAGE.md` & `docs/CONFIGURATION.md`:
   - Document `--max-credits N` on `video t2v`, `i2v`, `r2v`, `generate`, `chain`, `extend`, and `movie run`, per-command cumulative semantics on `chain`/`movie`/`extend`, fail-closed behavior on unquoted routes, and `GFLOW_CLI_MAX_CREDITS`.
3. `docs/MCP.md`:
   - Document `max_credits` on `gflow_generate_video` and the exit-45 `credit-cap-exceeded` error envelope.
4. `README.md`:
   - Update the comparison table row `"Per-call credit cap"` (`README.md:107`) for **gflow-cli** from `Planned` to `**Yes** (\`--max-credits\`, read from Flow's own quote)`.
5. `CHANGELOG.md`:
   - Under `## [Unreleased]` -> `### Added`, add the entry for `--max-credits`, `GFLOW_CLI_MAX_CREDITS`, MCP `max_credits` on `gflow_generate_video`, and `CreditCapExceededError` (exit 45) (#1355).
6. `skills/gflow-cli/SKILL.md`:
   - Document `--max-credits` / `GFLOW_CLI_MAX_CREDITS` / MCP `max_credits` and exit 45 (`CreditCapExceededError`).
7. Sync generated mirrors:
   ```bash
   uv run python scripts/ci/generate_plugin_skills.py
   uv run python scripts/ci/generate_website_docs.py
   ```

- [ ] **Step 3: Run the complete `/gflow:check` verification suite**

Run:
```bash
export PYTHONUTF8=1
uv run python scripts/ci/check_repo_hygiene.py
uv run python scripts/ci/check_size.py
uv run python scripts/ci/check_doc_links.py
uv run python scripts/ci/check_website_docs_pii.py
uv run python scripts/ci/generate_website_docs.py --check
uv run python scripts/ci/check_council_memory.py
uv run python scripts/ci/generate_plugin_skills.py --check
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run python -m pytest -q -m "not live and not e2e and not smoke"
```
