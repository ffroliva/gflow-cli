# Selector Registry + Drift Probe — Design

**Status:** Draft, pending council review
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

Two structural problems make each one worse than it needs to be.

**No inventory.** Selectors are scattered across four modules, some as inline literals,
some as module-private constants ~2,900 lines into a 3,557-line file:

```
mode_control.py:43,49,61,79        factory.py:52,58,60
agentic.py:62,210                  (inline literals)
ui_automation.py:97…460, 2924, 2952, 2961, 2964
ui_automation_video.py:90,103,144
```

Nobody — human or agent — can answer *"what parts of Flow's page does gflow depend on?"*
The nightly canary (#502) can therefore only report *"a test failed"*, never *"this
selector moved"*.

**No user recourse.** A selector is compiled in. When Google renames a control, every user
is broken until a release ships. `notebooklm-py`'s users patch a drifted value with an env
var and keep working; gflow's cannot.

**Context is encoded in file paths.** `SIDEBAR_CLOSE_SELECTOR` and
`SIDEBAR_CLOSE_FALLBACK_SELECTOR` are related only by naming convention.
`UiSelectorDriftError` carries "the probe label" as free text. None of it is queryable.

---

## 2. Evidence

Everything in §3 rests on measurements taken 2026-08-21, not assumptions. Two of these
overturned conclusions this design previously held.

### 2.1 Offline selector checking works

Playwright's `set_content()` plus its locator engines (`:has()`, `:text-is()`) resolve
gflow's real selectors against static HTML — no network, no auth, no credits. Cohort
variants correctly report MISS.

### 2.2 CI can capture the live DOM — one cookie is sufficient

Six-arm isolation matrix, positive control in the same run, one variable at a time:

| Arm | Browser | Cookies | composer | icons |
|---|---|---|---|---|
| CONTROL | real Chrome + full profile | profile (63) | 1 | 17 |
| D | real Chrome, fresh ctx | labs.google (7) | 1 | 17 |
| E | real Chrome, fresh ctx | all 63 | 1 | 17 |
| F | bundled chromium, fresh ctx | all 63 | 1 | 17 |
| G | bundled chromium, fresh ctx | labs.google (7) | 1 | 17 |
| **H** | **bundled chromium, fresh ctx** | **session-token only (1)** | **1** | **17** |

**All six identical.** `__Secure-next-auth.session-token` (1088 chars, **30-day** expiry,
scoped to `labs.google`) is sufficient on its own. No profile, no real Chrome, no
`.google.com` cookies, no master token, no OAuth bounce.

This is decisive because the blocker assumed for CI auth — `__Secure-1PSIDTS` rotating
every ~10 minutes — is a **Google** cookie. Flow does not authenticate on it.

### 2.3 The confound that voided three earlier runs

Three runs reused a hard-coded project id. A stale/deleted project renders an error shell
— **~441KB, 3 buttons, 0 icons** — which is indistinguishable from an auth wall by every
metric being measured. Those runs produced the false conclusions *"CI-without-auth is
dead"* and *"DOM auth and API auth are separate gates."* Both are retracted.

> **Binding consequence:** any probe navigating to a fixed project id can produce a
> convincing false negative. Every DOM probe MUST carry a positive control in the same
> run, and MUST create or verify its project rather than trust a stored id.

### 2.4 Other measurements

- **Headless is not bot-blocked** from a residential IP; unauthenticated it serves a 552KB
  marketing page with none of the app.
- **Locale is account-driven, not URL-driven.** Passing `locale="en"` to
  `project_editor_url()` still redirected to `/fx/pt/` and served Portuguese. Selectors
  MUST stay locale-invariant (Material Symbols ligatures, never display text).
- **`CROP_SELECTORS[0]` MISSes on the agentic arm.** It is the *classic-mode indicator*
  (`factory.py:116`), and only 1 of its 6 ratio variants was probed. Without `mode` and
  ordered `candidates`, that MISS is unattributable — the exact ambiguity this design exists
  to remove.

---

## 3. Design

### 3.1 Data model

```python
@dataclass(frozen=True)
class Surface:
    key: str                     # "editor", "editor.media_picker", "character_editor"
    reach: Reach                 # URL builder | (parent surface + non-mutating action)
    modes: tuple[UiMode, ...]    # which arms this surface can present

@dataclass(frozen=True)
class Selector:
    key: str                     # "editor.composer.input" — stable, public, in errors
    surface: str                 # → Surface.key
    mode: UiMode | None          # None = every mode of that surface
    min_plan: Plan | None        # None = every plan; gated controls are expected-absent below it
    candidates: tuple[str, ...]  # ORDERED: [0] preferred, then cohort variants / fallbacks
    features: tuple[str, ...]    # ("image", "video") — dependent commands
    required: bool               # True → drift is exit 23; False → degraded but survivable
    note: str                    # why fallbacks exist; incident refs
```

Every field is justified by §2 or by a listed incident. `min_plan` exists because #171
(`UpscaleUnavailableError`, exit 22) is plan-gated — `errors.py:508` says 4K upscale
"requires a Flow Ultra subscription". On a free CI account those controls are legitimately
absent, and that must not read as drift.

> **New type required.** `Plan` (Free / Pro / Ultra) does **not** exist in the codebase and
> must be introduced by this work. It is deliberately NOT named `Tier`: `api/video.py:35`
> already defines `Tier` as a *video quality* enum, and reusing the name would collide.

**Keys are a public promise.** They surface in exit-23 messages, canary reports, and the
env override. Renaming one is a breaking change — which is why the override ships last.

**Deliberately excluded:** timeouts, retry counts, screenshot-on-miss. Those are behaviour
and belong at the call site; including them would make this a config framework rather than
an inventory.

### 3.2 Capture / check split

```
CAPTURE   navigate to surface → page.content() → HTML     [browser + 1 cookie, $0]
CHECK     (html, entries) → HIT | FALLBACK | MISS         [pure; needs a browser only
                                                           for the selector engine]
```

The check is a pure function, so the *same code* grades a fresh capture and a committed
snapshot. The layers cannot disagree.

**Invariant:** reach steps MUST be non-mutating and credit-free. Opening a panel qualifies;
clicking Generate does not. This is what makes "$0" true by construction rather than by
hope, and it is reviewable.

### 3.3 Where each layer runs

| Layer | Trigger | Credential | Detects |
|---|---|---|---|
| **CI probe** | schedule + label (cadence open, see R1/§5) | `GFLOW_CI_SESSION_TOKEN` + project id | Google changed the page |
| **Nightly canary** | 03:00 local | maintainer profile | drift on the maintainer's cohort/tier/IP |
| **Snapshot tests** | every PR, offline | none | *we* broke a selector |

The nightly canary keeps a distinct role even with CI probing: a different account sits on
a different **A/B cohort** and a different **plan**, and runs from a residential IP.
Divergence between CI and nightly is *data*, not a bug — `mode`/`min_plan` make it attributable.

### 3.4 Grading

- **HIT** — `candidates[0]` resolved.
- **FALLBACK HELD** — `[0]` missed, a later candidate resolved. **Warning, not failure**;
  this is #493's actual outcome and the difference between a canary that gets read and one
  that gets ignored.
- **MISS (required)** — no candidate resolved → drift → `UiSelectorDriftError` (exit 23)
  carrying the key.
- **MISS (expected)** — `mode` or `min_plan` says it should be absent → informational.

### 3.5 Error contract and override

`UiSelectorDriftError` gains a structured `selector_key` alongside its free-text detail. The
key is publication-safe for a public issue (unlike a raw selector or a path).

Env override, **last phase only**:

```
GFLOW_CLI_SELECTOR_OVERRIDE='editor.count_tabs=<css>;editor.composer.input=<css>'
```

A user hit by a #404-class rename patches it and keeps working; the proper fix ships at its
own pace.

---

## 4. Migration

Incremental, no phase requiring the 3,800-line module split up front.

1. **Registry + surfaces for the drift-prone families only** — composer, mode switch, count
   tabs, sidebar close. These carry the incident history.
2. **Snapshot fixtures + offline check** — commit scrubbed DOM per surface; CI grades every PR.
3. **CI probe workflow** — live capture, grading, report.
4. **AST guardrail** — selector literals forbidden outside the registry (pattern:
   `test_rpc_method_ids_only_in_types.py`).
5. **Remaining selectors**, then the env override once keys have proven stable.

Steps 1–3 deliver value independently. The AST lint lands only after (5) is reachable,
otherwise it blocks its own migration.

---

## 5. Risks and open questions

| # | Risk | Resolution |
|---|---|---|
| R1 | **Datacenter IP.** Every measurement was from a residential IP; Google may treat a GitHub runner differently. | Unresolvable locally. Put the secret in, run once, look. Cheap and definitive. |
| R2 | **30-day secret rotation.** CI fails on day 31 with a confusing auth error. | Nightly canary reads cookie expiry locally and warns on the dashboard below 7 days. |
| R3 | **Project validity** (§2.3). | CI creates or verifies its project per run; never trusts a stored id blindly. |
| R4 | **Free-plan CI account** cannot render plan-gated controls. | `min_plan` field; expected-absent ≠ drift. Requires introducing a `Plan` enum. |
| R5 | **Snapshots rot** into always-passing. | They only ever detect *our* regressions; the live probe is the drift authority. Re-record on every confirmed drift. |
| R6 | **DOM snapshots carry PII** — project ids, asset URLs, possibly email. | Scrubbing required before commit, enforced by a guardrail test (pattern: `test_cassettes_clean.py`). |
| R7 | Static snapshots lose CSS/JS, so **visibility/enabled state is unverifiable offline**. | Snapshot layer asserts structural presence only; live probe additionally asserts visible/enabled. |

**Open question:** should CI probe on every PR, or on a schedule plus label? Every-PR gives
the fastest signal but hammers one account; the answer likely depends on R1.

---

## 6. Non-goals

- **Not** replacing the driver code. The registry owns data; clicking stays where it is.
- **Not** a Page Object Model rewrite. POM hides selectors inside classes; enumerability is
  the whole point here.
- **Not** the master token. §2.2 makes it unnecessary — see `[[master-token-tier0-deferred]]`.
- **Not** generation testing. The probe never submits, never spends credits.
- **Not** splitting `ui_automation*.py` in this work. Desirable, separately scoped.
