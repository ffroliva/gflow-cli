# Issue #125 fix plan — `gflow video i2v` silently routes to T2V with `omni-flash`

**Branch:** `bugfix/issue-125-i2v-t2v` (worktree at `.claude/worktrees/bugfix+issue-125-i2v-t2v/`)
**Issue:** https://github.com/ffroliva/gflow-cli/issues/125
**Status:** Plan — awaiting 5-persona council review before implementation.

---

## 1. Root cause (confirmed at zero credit)

`omni-flash` does NOT support i2v interpolation (start+end frames). Flow's frontend silently drops the bound mediaIds at submit time and routes the request to `batchAsyncGenerateVideoText` with `image_inputs: null`. The user is charged 1 credit for a pure text-to-video generation that ignores the start/end frames entirely.

This is **NOT** the originally hypothesised selector / UI-path bug. The "Add to Prompt" click in `_upload_via_open_dialog` IS correct on both EN and pt-BR locales (probe v1). The frame slots transition correctly (`FRAME_SLOTS_STRUCT.count()` drops 2 → 1 → 0, probe v3). The submit-button selector finds the right "Criar" button (probe v3). The bug is at Flow's model-vs-request-shape compatibility check, which is invisible from the DOM.

## 2. Evidence

### Zero-credit evidence

| Probe | Sequence | Model | Captured URL | startImage | endImage |
|---|---|---|---|---|---|
| v4 prod-order | attaches → prompt → submit | (inherits Flow's last = omni-flash) | `batchAsyncGenerateVideoText` ❌ | `null` | `null` |
| v4 reorder | prompt → attaches → submit | omni-flash | `batchAsyncGenerateVideoText` ❌ | `null` | `null` |
| **v5 omni-flash + start+end** | attaches → prompt → submit | omni-flash (explicit) | `batchAsyncGenerateVideoText` ❌ | `null` | `null` |
| **v5 omni-flash + start-only** | Start only → prompt → submit | omni-flash (explicit) | `batchAsyncGenerateVideoText` ❌ | `null` | `null` |
| **v5 veo-lite + start+end** | attaches → prompt → submit | **veo-lite** | **`batchAsyncGenerateVideoStartAndEndImage` ✓** | **`4e551690...` ✓** | **`700c5522...` ✓** |

**Conclusion: omni-flash does NOT support i2v in ANY form** — neither start-only nor start+end. The strict rejection in §3 is verified, not just conservative.

Output dirs:
- `C:\Users\ffrol\gflow-output\i2v-intercept-prodorder-20260530-160535\evidence.json` (omni-flash, broken)
- `C:\Users\ffrol\gflow-output\i2v-intercept-veolite-20260530-165432\evidence.json` (veo-lite, correct)

### Human-session HAR evidence

`C:\Users\ffrol\Downloads\labs.google14.har` — manual i2v session on the same account, same UI flow as gflow. The captured generate body shows:

```json
{
  "videoModelKey": "veo_3_1_interpolation_lite",  // ← Flow auto-promotes veo_3_1_lite → veo_3_1_interpolation_lite when start+end frames are present
  "startImage": { "mediaId": "3c65f7ed-..." },
  "endImage":   { "mediaId": "7e70fd03-..." }
}
```

### Paid run evidence

The original gflow paid retry (`C:\Users\ffrol\gflow-output\i2v-prompt-retry-20260530-133705\_err2.log`) used `--model omni-flash` (gflow's default), produced an 8s "wake-up in bedroom" stickman video that has zero visual continuity with `01-t2i.jpg` (stickman at desk) or `02-i2i.jpg` (arms-raised triumph at sunrise). Per the structured-log `generate_captured` event, the request URL was `batchAsyncGenerateVideoText` with all-null image_inputs — pure T2V from the prompt alone.

**Retroactive impact:** every i2v paid run on `gflow v0.10.0` with the default model (or `--model omni-flash`) has been a silent T2V. The wf-004 promo "drift" was not Veo style-shift — it was a pure T2V.

## 3. Scope

### In scope (this PR) — council-revised

- Add model-vs-mode compatibility helper `VideoModel.supports_i2v_interpolation()` returning False for `OMNI_FLASH`, True for the four `VEO_3_1_*` variants. Add `I2V_DEFAULT_MODEL = VideoModel.VEO_3_1_LITE` module constant in `api/video.py` (domain).
- **Typed error class** `ModelModeIncompatibilityError` (new, in `gflow_cli.errors`) wired into `EXIT_CODE_MAP` with **exit code 17** (distinct from Click's exit 2 "usage error" and generic exit 1). Per CLI UX council finding: "semantic model/mode incompatibility known to the domain deserves a typed error."
- `gflow video i2v` CLI: drop `omni-flash` from the i2v command's `--model` Click `Choice` tuple entirely (cleanest UX per CLI UX council). The `--help` output then lists only valid models; users see the constraint at help time. Note: t2v / r2v keep `omni-flash` in their Choice.
- `gflow video i2v` CLI: when `--model` omitted → resolve to `I2V_DEFAULT_MODEL` (`veo-lite`). When a deserialized config still names `omni-flash` (e.g. from a stale `--config` JSON), the Click callback raises `ModelModeIncompatibilityError`.
- **Defense-in-depth transport guard** in `_generate_video_locked`: place **immediately after `_select_video_model` (~L1170)** and BEFORE any `_attach_frame` call (Architect + Performance both flagged this as the right placement — fails before any DOM mutation, no cleanup needed, no `page.reload()`). Guard checks `mode is Mode.I2V and (start_image is not None or end_image is not None) and not model.supports_i2v_interpolation()` → raises `ModelModeIncompatibilityError`. Catches direct `FlowApiClient` callers (gflow-cli-remotion) who bypass Click.
- **Structlog event** `ui_automation_video.model_mode_rejected` emitted by the transport guard before raising — fields `{model, mode, has_start_image, has_end_image, issue_ref: "#125"}`. Required for e2e assertion per CLI UX council + [[verification-ledger-5-layer]].
- `--model` Click `help=` text on i2v + i2v command `examples=` corrected.
- Unit tests for the typed error, Choice-drop, default resolution, transport guard, and structlog assertion — see §6.
- CHANGELOG entries under both `### Fixed` and `### Changed` (the default-model shift is a Changed behaviour worth surfacing separately).
- Update issue #125 with the actual root cause and link the PR.
- Ship two diagnostic probes (`capture_i2v_post_bind_state.py`, `capture_i2v_intercept_submit.py`). **DROP probe v1 (`capture_i2v_upload_dialog_dom.py`)** — it proved a refuted hypothesis; ship-as-doc was post-hoc justification per Devil's Advocate.

### Out of scope (deferred — with evidence-based plan)

- **r2v + omni-flash compatibility** — Devil's Advocate flagged for in-scope inclusion. Probing r2v requires extending the probe with `--mode references` (different sub-mode, different attach helper `_attach_references`, different endpoint family `batchAsyncGenerateVideoReferenceImages`). Material rewrite. **Decision: file follow-up issue with a one-line probe extension as the first task. Defer scope expansion until evidence in hand.** The plan's transport guard is structured so adding r2v rejection later is a one-line change (`mode is Mode.R2V and not model.supports_r2v(...)`).
- **omni-flash + start-only i2v** — RESOLVED by probe v5: also routes T2V. Already covered by the in-scope reject-omni-flash-for-any-i2v rule. No longer "unverified strictness."
- **Full model+mode compatibility matrix** — broader spec; separate work.
- **Selector-cascade hardening for `_upload_via_open_dialog`** (originally Stage A from the predict review) — no longer needed; probe v1 confirmed the selector works on pt-BR. Drop this scope. (Probe itself dropped from ship list per above.)

## 4. Files touched — council-revised

| File | Purpose | Change |
|---|---|---|
| `src/gflow_cli/api/video.py` | Domain | Add `VideoModel.supports_i2v_interpolation()` classmethod. Add `I2V_DEFAULT_MODEL = VideoModel.VEO_3_1_LITE`. Per Architect: "domain invariant, not adapter knowledge — analogous to `Aspect.wire()`." |
| `src/gflow_cli/errors.py` | Typed error | Add `ModelModeIncompatibilityError(GFlowError)` with `exit_code=17`. Per CLI UX council. |
| `src/gflow_cli/cli_video.py` | CLI surface | Drop `omni-flash` from i2v `--model` Click `Choice` (cleanest discoverability per CLI UX). Add a Click callback resolving omitted `--model` to `I2V_DEFAULT_MODEL`. Raise `ModelModeIncompatibilityError` if a deserialized config still names `omni-flash` for i2v. Update i2v `--model help=` and command examples. |
| `src/gflow_cli/api/transports/ui_automation_video.py` | Transport guard | In `_generate_video_locked`, **immediately after `_select_video_model` (~L1170), BEFORE `_attach_frame`**: emit `ui_automation_video.model_mode_rejected` event and raise `ModelModeIncompatibilityError` if `mode is Mode.I2V and (start_image is not None or end_image is not None) and not model.supports_i2v_interpolation()`. Per Architect + Performance: earlier placement = no DOM mutation, no cleanup needed. **No `page.reload()`** — drop the R6 mitigation entirely. |
| `tests/api/test_video.py` (or sibling) | Unit | `test_supports_i2v_interpolation_matrix` parametrized over all 5 VideoModel variants. `test_i2v_default_model_is_veo_lite`. |
| `tests/errors/test_exit_codes.py` (or sibling) | Unit | `test_model_mode_incompatibility_error_exit_code_17` + ordering-invariant. |
| `tests/cli/test_cli_video.py` (or sibling) | Unit | i2v --help no longer lists omni-flash; omitted --model resolves to veo-lite; explicit omni-flash via stale config raises exit 17. |
| `tests/api/transports/test_ui_automation_video.py` (or sibling) | Unit | `test_transport_rejects_i2v_with_omni_flash` — stubbed request DTO, assert raise + `model_mode_rejected` event captured. |
| `CHANGELOG.md` | Release note | New `### Fixed` entry (i2v silent T2V regression) + new `### Changed` entry (i2v default model shift to veo-lite). |
| `scripts/dev/capture_i2v_post_bind_state.py` | Dev tooling | NEW — slot-state regression probe. |
| `scripts/dev/capture_i2v_intercept_submit.py` | Dev tooling | NEW — definitive zero-credit reproducer (the smoking-gun probe). |
| ~~`scripts/dev/capture_i2v_upload_dialog_dom.py`~~ | ~~Dev tooling~~ | **DROPPED from ship list** — proved a refuted hypothesis; not maintaining as documentation. |

## 5. Implementation steps (in order — each a focused commit) — council-revised

1. **Add `ModelModeIncompatibilityError`** to `gflow_cli.errors` + wire into `EXIT_CODE_MAP` with `exit_code=17`. Unit test for ordering-invariant + the new mapping.
2. **Add `VideoModel.supports_i2v_interpolation()` + `I2V_DEFAULT_MODEL`** in `api/video.py` + unit tests.
3. **Drop omni-flash from i2v's `--model` Click `Choice`; add resolution callback** in `cli_video.py`. Add `ModelModeIncompatibilityError` raise for the stale-config edge case. Unit tests for both paths.
4. **Transport guard** at the correct placement (just after `_select_video_model`) in `_generate_video_locked` + structlog event + unit test stubbing the request DTO. Verify no `page.reload()` is added.
5. **Update `--model help=` text + i2v command examples** in `cli_video.py`.
6. **CHANGELOG**: `### Fixed` + `### Changed` entries.
7. **Add the two dev probes** to `scripts/dev/`: `capture_i2v_post_bind_state.py` + `capture_i2v_intercept_submit.py`. Do NOT add `capture_i2v_upload_dialog_dom.py`.
8. **Update issue #125** with the corrected root cause and link to the PR (in §7 verification, after merge).

## 6. Test plan — council-revised

### Unit tests

- `test_omni_flash_does_not_support_i2v_interpolation`: `VideoModel.OMNI_FLASH.supports_i2v_interpolation() is False`
- `test_veo_3_1_models_support_i2v_interpolation`: parametrize across the four VEO_3_1_* variants, all return True
- `test_i2v_default_model_constant`: `I2V_DEFAULT_MODEL is VideoModel.VEO_3_1_LITE`
- `test_model_mode_incompatibility_error_exit_code_is_17`: `ModelModeIncompatibilityError().exit_code == 17` and the class is registered in `EXIT_CODE_MAP`
- `test_exit_code_map_ordering_invariant_still_holds` after the new entry (per [[exit-code-map-ordering-invariant-test-pitfall]])
- `test_i2v_click_choice_excludes_omni_flash`: introspect the i2v command's `--model` Click `Choice` — `"omni-flash" not in choices`. (Compare to t2v Choice which DOES include it.)
- `test_i2v_cli_resolves_default_to_veo_lite`: `gflow video i2v start.jpg "p" --end-image end.jpg` (no --model) → resolved model in the request DTO is `VideoModel.VEO_3_1_LITE`
- `test_i2v_cli_rejects_stale_config_with_omni_flash`: a deserialized config JSON pointing at omni-flash for i2v → exits **17** (not 2) with stderr containing "#125" and "veo-lite"
- `test_transport_raises_on_i2v_with_omni_flash_and_end_image`: invoke `_generate_video_locked` with a stubbed request `(model=OMNI_FLASH, mode=I2V, start_image=Path, end_image=Path)` → raises `ModelModeIncompatibilityError`, structlog captures `model_mode_rejected` event with fields `{model: "omni_flash", mode: "I2V", has_start_image: True, has_end_image: True, issue_ref: "#125"}`. **No DOM mutation must have occurred** — assert no `_attach_frame` call fired (via mock).
- `test_transport_raises_on_i2v_with_omni_flash_and_start_only`: same shape but `end_image=None`, `start_image=Path` — still raises (omni-flash rejected for any i2v ref, per probe evidence).
- `test_transport_does_NOT_raise_on_t2v_with_omni_flash`: pure t2v + omni-flash is a valid combination — guard must NOT fire.

### Integration tests

- BDD scenario: "user runs i2v with default model" → command resolves to veo-lite, no Flow interaction. Assert `structlog` shows `model_mode_rejected` is NOT emitted.
- BDD scenario: "in-process API caller (e.g. flow_workflow.py shape) passes `model=OMNI_FLASH, mode=I2V, start_image=Path` to `FlowApiClient.generate_video`" → raises `ModelModeIncompatibilityError`, exit code 17. **This is the safety net for gflow-cli-remotion-style consumers.**
- Audit existing i2v integration tests: any fixture using omni-flash for i2v must be updated to a Veo 3.1 model.

### Live e2e (1 paid credit)

- Re-run probe v5 (`capture_i2v_intercept_submit.py --model veo-lite ...`) zero-credit sanity check — captured URL must still be `StartAndEndImage` with bound mediaIds.
- Then a live `gflow video i2v 01-t2i.jpg "<tightened stickman motion prompt>" --end-image 02-i2i.jpg --model veo-lite --aspect 9:16 --profile promo-denon82` → expect:
  - structured log `ui_automation_video.generate_captured` event with `url=batchAsyncGenerateVideoStartAndEndImage` and `image_inputs.startImage` + `image_inputs.endImage` non-null
  - **assert structured log does NOT contain `ui_automation_video.model_mode_rejected`** (positive verification that the new guard didn't false-fire on a valid combination)
  - output mp4 visually: 2D ink line-art stickman starting at desk (matches 01-t2i.jpg), interpolating to arms-raised at sunrise (matches 02-i2i.jpg)
  - 5-layer verification ledger per memory [[verification-ledger-5-layer]]: file count, magic bytes (mp4 signature), Pillow dims (720×1280), structlog invariants (above), user gallery confirmation.

## 7. Verification ladder (definition of done) — council-revised

- [ ] `ruff format --check` + `ruff check` clean on `src/gflow_cli/{cli_video,errors,api/video,api/transports/ui_automation_video}.py` and the two dev probe scripts shipping.
- [ ] `pyright` clean on the same scoped paths.
- [ ] All new unit tests pass (see §6).
- [ ] Scoped pytest of `tests/cli/` + `tests/api/` + `tests/api/transports/` + `tests/errors/` passes (full sweep deferred to CI per [[full-test-suite-ooms]]).
- [ ] BDD/integration tests pass.
- [ ] **Exit-code-map ordering-invariant test passes** (per [[exit-code-map-ordering-invariant-test-pitfall]]) — adding code 17 must not invert any prior subclass-relation invariant.
- [ ] Probe v5 re-run with `--model veo-lite` still produces `StartAndEndImage` URL with bound mediaIds (zero credit).
- [ ] Probe v5 re-run with `--model omni-flash --start-only` still produces T2V (zero credit — proves the rejection is still warranted).
- [ ] 1 paid live e2e i2v run completes; structured log shows `generate_captured.url=batchAsyncGenerateVideoStartAndEndImage` with non-null mediaIds AND `model_mode_rejected` event is NOT emitted.
- [ ] User watches the output mp4 and verbally confirms it's a 2D ink line-art stickman desk → sunrise scene matching the refs.
- [ ] Issue #125 comment thread updated with the actual root cause + closed by PR merge.
- [ ] CHANGELOG entries present under `[Unreleased]` — both `### Fixed` and `### Changed`.

## 8. Risks + mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| R1: Changing default model silently surprises users who already had a working incantation | LOW — prior incantation was broken (T2V output); making it work correctly is a strict improvement | None needed beyond clear CHANGELOG note |
| R2: veo-lite has different cost/quality than omni-flash | LOW — both are tier-one lite models; veo-3.1-lite cost is documented in Flow's UI per `MEDIA_GENERATION_PAYGATE_TIER: PAYGATE_TIER_ONE` | Document in --model help text |
| R3: omni-flash + start-only i2v might actually work (unverified) | MEDIUM — if it does work, our strict rejection blocks a valid combination | Conservative reject in this PR; reverse with probe evidence in a follow-up if proven |
| R4: r2v + omni-flash has the same silent-drop pathology | MEDIUM — separate UI surface, separate ticket needed | Out of scope; file follow-up issue with link from #125 |
| R5: Hidden in-process API callers (scripts using `FlowApiClient` directly like `gflow-cli-remotion/flow_workflow.py`) bypass the CLI validation | MEDIUM | The transport guard in `_generate_video_locked` is the safety net for these callers |
| ~~R6: pre-submit guard raises but the page is left in a dirty state for the pool~~ | ~~LOW~~ | **DROPPED per Performance council review.** Guard now placed at L1170 (immediately after `_select_video_model`, BEFORE any `_attach_frame`) — fires before any DOM-state mutation, no Page cleanup needed, no `page.reload()`. Architect concurred ("earlier-fail is cleaner than dirty-state cleanup"). |

## 9. Rollback

- Revert the PR as a single commit-range. No DB migrations, no settings changes, no state mutations.
- The transport guard fails-closed: if a user with cached venv hits the guard on a stale config, they get a clear error pointing at #125 with remediation. No silent regression.

## 10. CHANGELOG entries — council-revised (two sections)

```markdown
### Fixed

- `gflow video i2v` silently routed every call to the T2V endpoint with
  `image_inputs: null` when the model was `omni-flash` (Flow's last-used
  default in most sessions). Flow's frontend rejects omni-flash + frame
  refs and falls back to text-only generation, but the rejection is
  invisible to the client — every i2v paid run on v0.10.0 burned a
  credit for a pure text-to-video generation that ignored the supplied
  start/end frames. Issue #125. Fix: omni-flash dropped from the i2v
  `--model` Click `Choice` entirely; a defense-in-depth transport guard
  in `_generate_video_locked` raises `ModelModeIncompatibilityError`
  (exit code 17) for direct `FlowApiClient` callers that bypass the CLI.

### Changed

- `gflow video i2v` default model is now `veo-lite` (was: inherit
  Flow's last-used, which was typically `omni-flash` — see Fixed). The
  veo-3.1 family is the only model line that supports i2v
  interpolation; omni-flash is now correctly rejected for any i2v
  invocation (start-only or start+end). Explicit `--model omni-flash`
  for `gflow video t2v` and `gflow video r2v` remains valid.
```

## 11. Migration / backward compat

- No breaking config or env-var changes.
- v0.10.0 users running `gflow video i2v ... --end-image ...` without explicit `--model` previously got silent T2V output; they now get correct interpolation (silent improvement, no surprise).
- v0.10.0 users running `gflow video i2v ... --end-image ... --model omni-flash` previously got silent T2V; they now get a clear error with `gflow video i2v ... --end-image ... --model veo-lite` as the remediation.
- BREAKING for: nobody. The prior behavior was a silent bug.

---

## Appendix: probe scripts

All zero-credit. Only two ship in this PR per Devil's Advocate review (probe v1 dropped — proved a refuted hypothesis, no future-locale-regression value).

| Probe | What it proved | Ships? |
|---|---|---|
| `capture_i2v_upload_dialog_dom.py` | "Incluir no comando" pt-BR button matches the production `add_btn` selector — selector cascade is correct (REFUTED original hypothesis). | **NO** — proved a refuted hypothesis; not maintaining as documentation. |
| `capture_i2v_post_bind_state.py` | `FRAME_SLOTS_STRUCT.count()` transitions 2 → 1 → 0 after binds; submit button is unambiguous (single arrow_forward "Criar" button); slots stay bound after prompt typing. | **YES** — useful future probe for slot-state regressions. |
| `capture_i2v_intercept_submit.py` | Intercepts the submit XHR via `page.route(..., abort)` and inspects the request body. omni-flash → T2V with null images (both start+end and start-only); veo-lite → StartAndEndImage with populated mediaIds. **This is the definitive reproducer.** | **YES** — definitive regression probe for future model/mode work. |
