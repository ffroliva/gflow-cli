# Selector Registry + Drift Probe — Design

**Status:** Draft, council round 1 applied (5 dimensions, all YELLOW)
**Date:** 2026-08-21
**Related:** #502 (nightly canary, shipped), #404, #493, #174, #299, #313, #171

---

## 1. Problem

gflow drives Flow by locating DOM elements. Those locators — **selectors** — are the
project's #1 breakage class: Google changes the page without notice and gflow stops
working. Every incident below is a selector that moved:

| Incident | What Google did |
|---|---|
| #404 | renamed video count tabs `1x` → `x1` |
| #493 | expanded sidebar removed the classic composer |
| #174 | library UI A/B flip |
| #313 | agent settings panel became sticky |

**No inventory.** Selectors are scattered across four modules, some inline, some
module-private ~2,900 lines into a 3,557-line file:

```
mode_control.py:43,49,61,79        factory.py:52,58,60
agentic.py:62,210                  (inline literals)
ui_automation.py:97…460, 2924, 2952, 2961, 2964
ui_automation_video.py:90,103,144
```

Nobody — human or agent — can answer *"what parts of Flow's page does gflow depend on?"*
The nightly canary (#502) can therefore only report *"a test failed"*, never *"this
selector moved"*.

**Context lives in file paths, not data.** `SIDEBAR_CLOSE_SELECTOR` and
`SIDEBAR_CLOSE_FALLBACK_SELECTOR` are related only by naming convention.
`UiSelectorDriftError` carries "the probe label" as free text. None of it is queryable.

---

## 2. Evidence

Measurements taken 2026-08-21. Independently replicated during council review; the
replication is recorded here because two claims changed as a result.

### 2.1 Offline selector checking works

Playwright's `set_content()` plus its locator engines (`:has()`, `:text-is()`) resolve
gflow's real selectors against static HTML — no network, auth, or credits.

### 2.2 One cookie is sufficient — *at mint time*

Six-arm matrix, positive control in-run, one variable at a time — **plus the negative
control the first write-up lacked**. Reproduce with `scripts/probe/spike_auth_matrix.py`.

| Arm | Browser | Cookie | project | composer | icons | buttons |
|---|---|---|---|---|---|---|
| CONTROL | real Chrome + full profile | profile (63) | real | 1 | 17 | — |
| D | real Chrome, fresh ctx | labs.google (7) | real | 1 | 17 | — |
| E | real Chrome, fresh ctx | all 63 | real | 1 | 17 | — |
| F | bundled chromium | all 63 | real | 1 | 17 | — |
| G | bundled chromium | labs.google (7) | real | 1 | 17 | — |
| **H** | **bundled chromium** | **session-token only** | real | **1** | 17 | — |
| **N1** | bundled chromium | **none** | real | **0** | 9 | 67 |
| **N3** | bundled chromium | **value replaced with `xxx…`** | real | **0** | 9 | 67 |
| N2 | bundled chromium | valid | **bogus** | 0 | **0** | **3** |

`__Secure-next-auth.session-token` (1088 chars, scoped to `labs.google`) is sufficient
alone. **N1/N3 are what make this falsifiable**: without the cookie, or with its value
corrupted, the app does not render. The cookie's *value* does the work, not its presence.

The discriminator is **`composer ≥ 1` and `icons ≫ 0`**, not the literal cell values —
those drift run to run (icons 17→18, jar 63→65).

This is decisive because the blocker assumed for CI auth — `__Secure-1PSIDTS` rotating
every ~10 minutes — is a **Google** cookie. Flow does not authenticate on it.

**Scope limit — the token under test was ~9 minutes old.** Its sibling
`__Secure-next-auth.pkce.code_verifier` / `.state` cookies show a full OAuth bounce
minutes before measurement. The matrix therefore establishes sufficiency **at mint
time**, never for an aged token — which is precisely the CI condition. R1 must include
a day-7 re-run before this is relied on.

### 2.3 The confound that voided three earlier runs

Three runs reused a hard-coded project id. A stale/deleted project renders an error shell
— **~441KB, 3 buttons, 0 icons** — indistinguishable from an auth wall by every metric
measured. Those runs produced the false conclusions *"CI-without-auth is dead"* and
*"DOM auth and API auth are separate gates."* Both retracted.

Replicated independently (arm N2: 431KB / 3 buttons / 0 icons, single variable). **Cause
established, not merely plausible.**

> **Binding consequence:** any probe navigating to a fixed project id can produce a
> convincing false negative. Every DOM probe MUST carry a positive control in the same
> run and MUST create-or-verify its project. §2.2 shows the same rule applies in the
> other direction: an all-HIT matrix without a falsifying arm proves nothing either.

### 2.4 Locale is account-driven

Passing `locale="en"` to `project_editor_url()` still served `/fx/pt/` in Portuguese.
The *evidence* for account-attribution is the authed/unauthed contrast at identical
`Accept-Language` and IP: **authed → `pt`, unauthed → `en`, cookie the only variable**
(N0/N2 vs N1/N3).

**Corollary worth its own ticket:** `client.py:416`'s `locale="en-US"` and
`ui_automation.py:77`'s comment that *"`?hl=en` locks locale"* are both **ineffective**.
Selectors MUST be locale-invariant (Material Symbols ligatures, never display text).

### 2.5 Viewport is selector-affecting

`ui_automation.py:117-124` pins `_VIEWPORT = {1920, 1080}` and states that smaller
*"would cross the responsive breakpoint and **drift the selectors**"*. `FlowApiClient`
uses 1280×720 and Playwright's default context is 1280×720 — **both below the
breakpoint**. Any probe not pinning 1920×1080 reports false drift.

### 2.6 Headless is not bot-blocked

Confirmed on the **authenticated full app** (arm N0), from one residential IP. The
earlier unauthenticated observation was of a marketing page and does not support the
claim. Says nothing about datacenter IPs — see R1.

### 2.7 The session token does not rotate on use

`last_update_utc == creation_utc` to the microsecond across a 30-day-old token. A CI
secret is therefore **stable until it hard-dies at day 30** — preferable to a silently
rotating credential, but it fails abruptly rather than degrading.

---

## 3. Design

### 3.1 Data model

```python
@dataclass(frozen=True)
class Surface:
    key: str                     # "editor"
    url_template: str
    viewport: tuple[int, int]    # (1920, 1080) — below the breakpoint drifts selectors (§2.5)

@dataclass(frozen=True)
class Selector:
    key: str                     # "editor.composer.input" — stable, public, in errors
    surface: str                 # → Surface.key
    candidates: tuple[str, ...]  # ORDERED: [0] preferred, then cohort variants / fallbacks
    mode: UiMode | None          # None = every mode of that surface
    expect_unique: bool          # True = >1 match is AMBIGUOUS, not HIT (§3.4)
    note: str                    # why fallbacks exist; incident refs
```

**Dropped after council review** (each verified to have zero consumers):

| Field | Why cut |
|---|---|
| `min_plan` / `Plan` enum | No plan-gated selector is in the initial registry. Add the day one is — 4 lines then. |
| `features` | Nothing reads it; `surface` + `note` already carry the context. |
| `required` | All entries defaulted `True`, and its `False` branch returned the same grade as the mode branch. Two encodings, one outcome. |
| `Surface.modes` | Never read — `for_surface()` filters on `Selector.mode`. Same category as `features`, cut in the same review that added it. |

**Keys are a public promise** — they appear in canary reports and any future override.

### 3.2 Capture / check split, and the mode sidecar

```
CAPTURE  navigate (viewport-pinned) → page.content() + observed context → HTML + JSON
CHECK    (html, entries, observed) → HIT | FALLBACK | AMBIGUOUS | MISS | EXPECTED_ABSENT
```

**`observed_mode` is a REQUIRED argument to the grader.** Without it,
`editor.crop_control` (`mode=CLASSIC`) grades MISS = drift on every agentic capture — the
exact error this design exists to prevent. Making it required enforces that at the type
level, which is cheaper and stricter than a sentinel grade for "no context".

**Mode detection must reuse production's detector**, never a reimplementation:
`factory._any_present(page, _CLASSIC_CROP_SELECTORS)` over **all six** ratio variants.
Checking only `candidates[0]` (`crop_16_9`) is a silent-pass hole: a classic editor on a
9:16 project reads as AGENTIC, so `crop_control` grades EXPECTED_ABSENT and real drift
hides permanently. That is the same one-of-six error §2.4 documents.

**Correction to an earlier claim.** This does *not* mean snapshot and live checks "cannot
disagree". `set_content()` drops external CSS/JS and re-executes inline scripts, and
`page.content()` omits shadow roots. Both layers assert **structural presence only**;
neither asserts visible/enabled today.

**Invariant:** reach steps MUST be non-mutating and credit-free.

### 3.3 Where each layer runs

| Layer | Trigger | Credential | Detects |
|---|---|---|---|
| **CI probe** | `schedule` + `workflow_dispatch` | `GFLOW_CI_SESSION_TOKEN` + project id | Google changed the page |
| **Nightly canary** | 03:00 local | maintainer profile | drift on the maintainer's cohort and IP |

**Snapshot fixtures are cut.** With CI probing live, a frozen snapshot only ever detects
*our* regressions, while costing a second capture path, a PII-scrubbing pipeline, and a
committed authenticated DOM in a public repo. The AST guardrail (deferred, §4) covers the
same regression class far more cheaply.

### 3.4 Grading

- **HIT** — `candidates[0]` resolved (uniquely, when `expect_unique`).
- **FALLBACK HELD** — `[0]` missed, a later candidate resolved. Warning, not failure; #493's actual outcome.
- **AMBIGUOUS** — resolved, but matched **>1** element on an `expect_unique` selector.
  Drivers call `.first`, so a second match means gflow clicks the wrong thing while a
  count-based check reports success. `SIDEBAR_CLOSE_FALLBACK_SELECTOR` is deliberately
  unscoped and is the standing candidate for this.
- **MISS** — nothing resolved → drift → exit 23.
- **EXPECTED_ABSENT** — the observed `mode` says it should not be here.

### 3.5 Error contract — BLOCKED, pending a scope decision

Adding `selector_key` to `UiSelectorDriftError` and shipping
`GFLOW_CLI_SELECTOR_OVERRIDE` both require the **drivers** to read from the registry.
Today all 7 `UiSelectorDriftError(` raise sites are in `ui_automation.py` (2) and
`ui_automation_video.py` (5), and nothing in those modules consumes the registry — so
`selector_key` would stay `None` in production and an override would change what is
*checked*, never what is *clicked*.

That makes §6's non-goal *"not replacing the driver code"* incompatible with this section.
Three independent findings say cut: no user has requested an override
(`gh issue list --state all --search "selector override"` → none), it does not work as
specified, and making it work means inverting imports across two 3,800-line modules.

**Deferred, not deleted** — the scope call belongs to the maintainer.

---

## 4. Migration

1. **Registry + grader** — only selectors present on a **freshly-loaded** editor:
   composer input, composer submit, agent toggle, crop control.

   **#404's count tabs are NOT registerable yet, and that is worth stating plainly.**
   They live inside the generation-settings panel, which must be clicked open
   (`_open_gen_settings_panel`; `_is_settings_panel_open` exists because it is normally
   closed). Registering them would grade MISS on every clean capture. The incident this
   design leans on hardest therefore needs `Reach` — which is the strongest argument for
   prioritising `Reach` in the follow-up, not for registering a selector that reds nightly.
2. **Capture** — viewport-pinned, creates its own project, writes the mode sidecar.
3. **CI probe workflow** — `schedule` + `workflow_dispatch` only.

**Deferred to a follow-up plan:** the AST guardrail (it flags **9 real offenders** today —
`ui_automation_video.py:91-96,162`, `agentic.py:62`, `diagnostics.py:1738` — so it reds on
arrival while its migration is still deferred), inverting the import direction, additional
surfaces, and §3.5 if the maintainer keeps it.

---

## 5. Risks

| # | Risk | Resolution |
|---|---|---|
| R1 | **Datacenter IP** — all measurements from a residential IP. | Unresolvable locally. `workflow_dispatch` once and read the result. Must also include a **day-7 token re-run** (§2.2 scope limit). |
| R2 | **Token hard-dies at day 30** (§2.7 — no rotation, so no warning). | Nightly canary reports remaining life. It MUST pin the profile path — two `profile_denon82` trees exist on the maintainer's machine and the orphaned one reports "expired 38 days ago". |
| R3 | **Project validity** (§2.3). | Probe creates-or-verifies per run. |
| R4 | **State-gated selectors.** `SIDEBAR_CLOSE` needs an expanded sidebar; **count tabs need the generation-settings panel opened**. A URL-only surface grades both MISS. | Phase 1 registers only selectors present on a freshly-loaded editor. Both wait for `Reach` — including #404's family (§4). |
| R5 | **CI has no browser.** `ci.yml:165` runs plain pytest; no workflow installs Playwright. | Probe workflow installs chromium itself. No default-suite test may require a real browser. |
| R6 | **Secrets in a public repo.** | Dedicated throwaway account, never the maintainer's. Reuse `src/gflow_cli/data/redaction.py` (already covers `Bearer`, `SAPISIDHASH`, `__Secure-next-auth.session-token`, signed queries) for any published output — do not write a fourth redactor. |
| R7 | **Same-repo branch PRs would receive the secret** while checking out attacker-editable probe code. Fork PRs are safe (GitHub withholds secrets). | Never add `pull_request`; never `pull_request_target`. |

---

## 6. Non-goals

- **Not** replacing the driver code *in this phase*. §3.5 is deferred precisely because it
  would require that; the two cannot both hold.
- **Not** a Page Object Model rewrite — POM hides selectors inside classes; enumerability
  is the point.
- **Not** the master token — §2.2 makes it unnecessary. See `[[master-token-tier0-deferred]]`.
- **Not** generation testing. The probe never submits, never spends credits.
- **Not** committed DOM snapshots (§3.3).
- **Not** splitting `ui_automation*.py`. Desirable, separately scoped.
