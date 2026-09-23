# An account with no Flow access is identifiable only in the DOM, by component

- **Date:** 2026-09-15
- **Script:** [`scripts/dev/spike_flow_unavailable_signal.py`](../../../scripts/dev/spike_flow_unavailable_signal.py)
- **Arm:** one profile, a Google account created days earlier with no subscription of any
  kind. The account name is not recorded here; pass the profile on argv or via
  `GFLOW_SPIKE_PROFILE`.
- **Cost:** $0 — one navigation, response *metadata* only, plus a DOM shape read. No
  bodies to disk unredacted, no generation, nothing submitted.
- **Raw:** `scripts/dev/_spike_out/flow-unavailable-signal.json` (gitignored)
- **Refs:** [#756](https://github.com/ffroliva/gflow-cli/issues/756) (the landing-state
  family this joins), [#773](https://github.com/ffroliva/gflow-cli/issues/773)

## Why it was askable at all

Five gflow commands were run on the account first. Each answered differently, and none
of them said the true thing:

| command | oracle | exit | what it said |
|---|---|---|---|
| `image t2i` (no `--project`) | labs REST | 3 | *"Authentication expired -> Run `gflow auth login`"* |
| `auth login` | session API | 8 | *"Signed in to your Google account, but the Flow app sign-in wasn't completed."* |
| `auth status` | session API | 1 | *"Signed in to Google, but not to the Flow app."* |
| `credits user` | labs REST | 3 | pointed at [#795](https://github.com/ffroliva/gflow-cli/issues/795) |
| `project list` | local SQLite | 0 | `{"projects": [], "total": 0}` |

A sixth surface was only found later, by driving the editor directly
(`--project <id>` on `flow.google.com`), and it is the worst of the set: **exit 23,
`UiSelectorDriftError`**, *"Google may have updated their frontend … file a bug"* — after
a 30 s wait, on `https://flow.google.com/unavailable`, with an incident bundle containing
a screenshot of the account's own page for the user to attach to that issue. gflow blamed
its own selectors for a missing subscription and asked the user to report it.

Before this spike the fix was going to be a URL-path matcher on `/unavailable`, on the
strength of what the operator had seen in a browser address bar. That is a guess about a
signal, and the project's own rule is that a claim about a live Flow surface is measured,
not inferred — so the four candidate signals were written down *before* the run, each with
what it would license.

## Pre-registered readings

| # | If this is observed | Reading |
|---|---|---|
| R1 | a field on the wire naming entitlement/tier/plan | route on the response; no DOM dependency at all |
| R2 | nothing on the wire, but a stable URL path | route on the path; cheapest possible check |
| R3 | an HTTP 3xx to the unavailable location | route on the redirect, before any DOM exists |
| R4 | nothing distinguishing at all | record as unmeasured; do **not** ship a guess |

R1 was scanned for with a deliberately broad key list (`entitle`, `eligib`, `tier`,
`subscription`, `plan`, `access`, `allowed`, `permission`, `unavailable`, `quota`,
`credit`, …) rather than a guess at one field name, so that "no such field" would be a
finding and not a spelling mistake.

## What was observed

**The wire (21 responses, `responses[]` in the JSON).**

| | observed |
|---|---|
| `flow.google.com/` document | **HTTP 200** |
| redirect chain | **one entry**, the 200 itself |
| Flow's own `batchexecute` | **6 calls, every one 200** |
| any 3xx anywhere | **one** — an avatar on `lh3.google.com`, unrelated |
| resolved URL after the app booted | `https://flow.google.com/unavailable` |

**The DOM (`dom` in the JSON).**

```
aisandbox-root > main > flow-banner, router-outlet, flow-pinhole-unavailable-screen
```

`tags` is: `div, p, span, a, button, img, svg, circle, path, script, aisandbox-root, main,`
`flow-banner, router-outlet, flow-pinhole-unavailable-screen, video, h2, mat-icon, iframe`.
The anchors include `support.google.com/flow/answer/16353333` and `labs.google/flow/tv`.
`bodyTextLen` is 395 — this is a rendered page with real content, not an error boundary.

## Verdict on each reading

| # | Verdict | Why |
|---|---|---|
| R1 | **refuted** | The key sweep's only hit across the captured bodies was a marketing banner. No entitlement, tier or plan field exists on this wire. (The sweep printed to stdout; the JSON holds URL/status and the DOM shape.) |
| R2 | **refuted** | The path is not stable. The script measured `/unavailable`; the same operator's browser, on a different account in the same session, landed on `/u/8/unavailable` — Google's account-index segment, which gflow does not parse anywhere. A path matcher would have passed this spike's arm and missed the other. |
| R3 | **refuted** | 200, and the only 3xx in the whole capture is an avatar. The hop is performed client-side by Angular after the shell boots, which is the same mechanism the [2026-09-11 `/about` spike](2026-09-11-about-redirect-is-decided-client-side.md) found. |
| R4 | **refuted** | Something distinguishing does exist — just not where the first three looked. |

## What this licenses

Anchor on **`flow-pinhole-unavailable-screen`**, Flow's own component for this state.

- It survives all three failures above: no wire field needed, no path assumption, no 3xx.
- It is locale-invariant by construction (`AGENTS.md` § Locale-Invariance Discipline,
  Tier 1). The `<h2>` on that screen is prose and would have to be translated; a component
  tag is emitted by Flow's build.
- It is specific. `aisandbox-root` alone is present on every Flow page including healthy
  ones; the pinhole component is only on this one.

## What this does **not** license

- **Which eligibility requirement failed.** Flow requires an age-verified account, a
  supported region *and* a paid plan. Only "no plan" was true of this arm, so the error
  message cites [Google's own page](https://support.google.com/flow/answer/16353333)
  rather than asserting a cause.
- **Anything about `auth login` / `auth status`.** Those read
  `labs.google/fx/api/auth/session`, a different oracle that this spike did not vary. It
  answers 200 with an empty `user` for an abandoned sign-in *and* for this account, so
  `evaluate_session_response` cannot separate them — and by the time the login verdict
  runs, Chrome has closed and there is no page left to ask. Left alone deliberately.
- **A claim about migration or cohort.** The account was served `flow.google.com`. That
  says where Flow answered, not what any cohort can do (`AGENTS.md` § Host-Membership
  Discipline).

## Where it landed

`raise_if_known_landing()` in `src/gflow_cli/api/transports/_common.py` — the shared
chokepoint all three "we landed somewhere unexpected" raise sites already route through —
now probes for the component before it reads the URL, and raises
`FlowAccessUnavailableError` (exit 39, not retryable). The measured DOM is reproduced in
`tests/e2e/test_landing_state_diagnosis_bdd.py` so a regression fails offline, including
the `<h2>` and the support link: a selector keying on prose instead of the component would
still find prose to key on, and would still be wrong.

## Verified live, as an A/B

The same command on the same account, on live Flow, with the probe neutered and then
restored — because "it exits 39 now" alone does not show the fix caused it. The command,
written out rather than implied:

```
gflow image t2i "a red cube on a white table" --profile <unentitled>   --project 11111111-2222-3333-4444-555555555555 --json
# GFLOW_CLI_FLOW_HOST=flow.google.com
```

The project id is deliberately nonexistent: the account has no projects, and the guard
fires on the landing before any project lookup, so a real id would have changed nothing.

| arm | exit | class | message |
|---|---|---|---|
| probe neutered (control) | **23** | `UiSelectorDriftError` | *"the settings trigger (`.settings-trigger-button`) did not become visible within 30s on `https://flow.google.com/unavailable`"* → file a frontend bug, bundle written |
| probe restored | **39** | `FlowAccessUnavailableError` | *"This Google account cannot reach Flow"*, `ui_driver.known_landing kind=unavailable at=migrated.ensure_editor` |

Cost $0 in both arms: the run fails before any submit.

The control arm also produced a second finding worth keeping. It wrote a full incident
bundle (`browser.json`, `network.json`, `report.md`, `sensitive/screenshot.png`, `ui.json`)
and told the user to attach it to a GitHub issue — a screenshot of their own signed-in
Google page, for a state that is not a bug in gflow at all. Under the fix no bundle is
written, because `FlowAccessUnavailableError` is a direct `GFlowError` outside
`_capture_triggers()` and `should_capture()` therefore returns `False`. That was verified
by calling it, with `UiSelectorDriftError` as a positive control returning `True`.

## The route this does **not** cover

The same account, same command, **without** `--project`, never reaches the editor. It
takes the labs REST arm and fails at `labs.google/fx/api/trpc/project.createProject` with
**HTTP 401** → `AuthExpiredError`, exit 3, *"Run `gflow auth login`"*. Unchanged by this
work, and not fixable at this chokepoint: a 401 there is indistinguishable from a genuinely
expired session, which is the same wall `auth login` and `auth status` hit. Recorded here
rather than left for someone to rediscover.
