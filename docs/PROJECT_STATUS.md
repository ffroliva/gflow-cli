# Project Status

> Where gflow-cli is in its lifecycle, by release. Updated on every signed tag.

## Current release

**v0.69.0 — alpha.** **The migrated `flow.google.com` host gains image-to-video from a local
start frame, and gflow can finally tell you what your balance is.**

`gflow credits user` and `gflow credits list` report the current Veo balance and account tier
for one saved profile or all of them, and `gflow_get_credits` is the MCP twin. The primary
path is genuinely browser-free: saved cookies authenticate a `labs.google` session request,
that client is **closed** before a separate cookie-free, Bearer-only call to the credits
endpoint, so no cookie crosses an origin boundary. A browser is the fallback, not the route.
Multi-profile inspection keeps partial successes — a stale profile degrades to its own row
with a reason instead of failing the run — and `--json` is a stable automation contract. The
balance funds **Veo video only**; image generation draws on separate per-model daily quotas,
which the docs now say in three places
([#671](https://github.com/ffroliva/gflow-cli/pull/671), @ziyedbe).

`gflow video i2v --initial-frame <file> --project <id>` now runs on the migrated host
([#639](https://github.com/ffroliva/gflow-cli/issues/639), slice 1) — on a moved account, and
by default on an unmoved one. The port is UI-driven and observed rather than replayed: the
composer picks the Frames submode, uploads through the editor's own toolbar entry, reads the
media id off the app's `maseQ` reply, finds the upload in the picker by file name (the picker
exposes no media id in its DOM, and indexes a fresh upload late — hence up to three searches),
and inspects the submit *request* as it leaves. An unbound chip is refused before the click,
exit 23 at zero credits, because an empty Frames submit silently goes out as text-to-video.
Not ported in this slice, and named in the exit-36 detail: `--end-frame`, a frame given by
UUID or `@Name`, and r2v.

Also in this release: `--model veo-lite-lp` is drivable on the migrated host — the one tier
its model map omitted, matched by the `[Lower Priority]` tag rather than a label
([#669](https://github.com/ffroliva/gflow-cli/pull/669), @kittinan); moved accounts now exit
36 rather than a misleading `RecaptchaError` on `image t2i`/`i2i`/`upscale`/`extend`, because
the token was minted on the pool's bootstrap page before any transport guard could fire
([#673](https://github.com/ffroliva/gflow-cli/issues/673)); and three migrated model-picker
defects are fixed — a tier bound that the user did not ask for, a stacked overlay that broke
the next run after a `--model` switch, and a short-circuit that logged nothing.

The `video-production` skill ships at **SkillOpt epoch 1**: a scored rollout picked
`veo-quality` for a two-reference shot while correctly reciting that its reference cap is 0,
because the skill stated the prohibition twice and the substitute nowhere. It now routes —
and names `video chain` as the exception, since chains refuse `omni-flash`. The SkillOpt
harness moved onto the project's own `GFLOW_CLI_LLM_*` settings, dropping a parallel provider
configuration and both LLM SDK dependencies.

Verification: [LIVE_VERIFICATION_v0.69.0](LIVE_VERIFICATION_v0.69.0.md) — i2v proven on **two
moved accounts in two locales**, the decisive run being the CLI entrypoint on the
Portuguese-locale account (exit 0, 64 s, 632,755 B byte-exact with the status record, catalog
row present); credits re-tested on the maintainer machine that had originally caught it
launching a browser, now `status_code=200` with no browser and an `e2e_auth` test whose pass
*is* the browser-free proof. Recorded as NOT verified: 5 of the 19 skill-benchmark tasks
(blocked on a 20-request/day free-tier quota, not on the code), the MCP twin of the credits
path live, and the items the i2v ledger already lists.

<details><summary>v0.68.0 — gflow update, and the once-a-day banner</summary>

**gflow-cli keeps itself current: `gflow update` upgrades an install in
place, and every command shows a banner when a newer release is on PyPI.**

`gflow update [--check] [--json]` runs the package manager that installed gflow-cli, read
off the install itself rather than guessed from `PATH` — `uv-receipt.toml` → `uv tool
upgrade`, `pipx_metadata.json` → `pipx upgrade`, otherwise that venv's own `python -m pip`
(a `uv venv` with no `pip` module is refused before anything spawns). It asks PyPI first and
runs nothing when already current; if PyPI is unreachable the manager still runs. The
outcome is read back from the venv, not from the manager's exit code: on Windows the running
`gflow.exe` launcher holds its own file open, so `uv tool upgrade` installs the new wheel and
*then* exits 1 copying the launcher — measured — and that is reported as an upgrade with a
note, because the launcher only points at the venv's python and keeps working. After an
upgrade the venv's Playwright version is re-read and the `playwright install chromium` hint
appears only when it moved. Editable / local / VCS / source installs, a manager binary
missing from `PATH`, and a manager run that leaves the version unchanged are exit 11.
Deliberately no MCP twin: the manager would replace the venv under a live `gflow mcp run`
and write onto its stdout channel ([#668](https://github.com/ffroliva/gflow-cli/pull/668)).

The once-a-day notice ([#479](https://github.com/ffroliva/gflow-cli/issues/479)) is now a
bordered stderr panel on a terminal — new version, `gflow update`, release-notes link — and
stays one plain line when stderr is piped, so `2>&1 | jq` pipelines keep one line per event.
Same cache, same gates (`GFLOW_CLI_UPDATE_CHECK=0`, CI, non-index installs).

Also in this release: `CONTRIBUTING.md` routes contributors — and their coding agents —
through the same lifecycle `AGENTS.md` defines (phase → skill → artifact), the PR template
carries a matching *Lifecycle* checklist, and the contributor quality-gate list is identical
to `AGENTS.md`'s again. And a contributor fix on the migrated host
([#670](https://github.com/ffroliva/gflow-cli/issues/670), @ChandraLiuswanto): the composer
now waits up to 5 s for the `arrow_forward` submit button to enable after the prompt is
inserted — it flips roughly 100 ms after `insert_text` lands, and reading it synchronously
made every `video t2v` on a fully migrated account exit 23 before submitting.

Verification: [LIVE_VERIFICATION_v0.68.0](LIVE_VERIFICATION_v0.68.0.md) — a wheel lowered
to 0.60.1 installed through `uv tool` and through `pipx`, then `gflow update` run from each
install's own `gflow.exe`: **exit 0 both times, venv at PyPI 0.67.0 afterwards**, the uv run
carrying the stale-launcher note. Recorded as NOT verified: plain-venv `pip` on Windows,
macOS / Linux for any manager, a real interactive-terminal screenshot of the panel, and the
first genuine banner (0.68.0 is the first release after the change).

<details><summary>v0.67.0 — Flow's migrated flow.google.com host driven for text-to-video</summary>

**v0.67.0 — alpha.** **Flow's migrated `flow.google.com` host is driven for text-to-video,
and it is now the default host for every request it can serve.**

Google is moving accounts off `labs.google` one at a time
([#639](https://github.com/ffroliva/gflow-cli/issues/639)); both maintainer accounts moved
during this cycle. The migrated frontend is the same product on Angular Material over a
different wire (`batchexecute`, not `aisandbox-pa`): three $0 spikes and two real clips settled
the DOM and the protocol
([spike](superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md)), and the new
migrated composer drives it — option-group radios, model menu, `contenteditable` composer,
`arrow_forward` submit — then **observes** the app's own `YhhmEf` / `jwpduf` / `as29s` replies
and downloads the clip from its signed CDN URL. Routing (`GFLOW_CLI_FLOW_HOST`): under the
default `auto`, `flow.google.com` takes every `video t2v --project` on moved and unmoved
accounts alike; what it cannot serve yet (image, i2v/r2v, characters, scenes, extend,
instructions, tools, project creation) keeps the labs driver on an unmoved account and exits
36 on a moved one. `flow.google.com` forces it, `labs.google` is the kill switch.

Also in this release: exit 36 is **non-retryable** — the handoff is a server-assigned
per-account flag applied client-side, one-way, not the per-page-load flap the error text
claimed ([spike](superpowers/spikes/2026-09-04-migrated-host-handoff-mechanism.md)); and
`--duration` accepts 4/6/8 s on the Veo 3.1 models where the account's cohort renders the
control ([#650](https://github.com/ffroliva/gflow-cli/pull/650), @stgmt).

Verification: [LIVE_VERIFICATION_v0.67.0](LIVE_VERIFICATION_v0.67.0.md) — `gflow video t2v`
on the moved account (default route) **exit 0 in 49.9 s**, on the unmoved Portuguese-locale
account (forced route) **exit 0 in 50.5 s**, both mp4s byte-exact with the size Flow reported,
recorder rows present; `--model veo-fast --duration 6` aborts pre-submit with exit 11 at zero
credits because this cohort renders no duration row for Veo. The positive Veo 4/6/8 path is
recorded as NOT verified (cohort-external), and the labs-side guard can no longer be verified
here at all — there is no labs account left.

</details>

<details><summary>v0.66.3 — the `&lt;html lang&gt;` hydration race</summary>

**v0.66.3 — alpha.** **gflow was reading Flow's locale off the `en` shell it serves before the
app rewrites it, so every account whose URL could not answer came back English.**

Flow serves an `en` shell and sets the real `<html lang>` during hydration. The single early read
added by [#643](https://github.com/ffroliva/gflow-cli/issues/643) therefore returned `en` — for
exactly the accounts #643 existed to help, the ones whose URL carries no locale segment. Reported
by [@maipmacrothorax-75](https://github.com/maipmacrothorax-75) on a pt-BR account resolving `en`
while both pages served `<html lang="pt">`, with the mechanism offered explicitly as a guess.

The guess was right, and it **reproduces on the old host** — so this was never a migration bug.
Measured, two consecutive loads:

| t (ms) | `lang` | `readyState` |
|---|---|---|
| 887 | `en` | interactive |
| **1510** | `en` | **complete** |
| 2092 | `en` | complete |
| **2488** | **`pt`** | complete |

The useful part of that table is the negative result: **`readyState` reaches `complete` a full
second before the flip**, so the obvious "wait for complete, then read" would have shipped the
same bug with more code. DOM node count oscillates and does not discriminate either. Nothing
cheap predicts the flip, so the resolver now **observes** it — read, bounded-wait for a change,
re-read — and treats a timeout as an *answer* (the shell value was already correct) rather than a
failure, logged distinctly so the field can tell the two apart.

Verification: [LIVE_VERIFICATION_v0.66.3](LIVE_VERIFICATION_v0.66.3.md). Proven at zero credits:
the helper captures the flip live (`en` → `pt`, +638 ms) and a real bootstrap on an `en` account
resolves correctly at the cost of the 4 s bound. Those two are reported **separately**, because
collapsing a component measurement into a user-facing claim is the mistake v0.66.1 made and
v0.66.2 corrected.

</details>

<details><summary>v0.66.2 — the fast-fail that had never fired, and a locale cache answering the wrong question</summary>

**v0.66.2 — alpha.** **The fast-fail v0.66.1 announced had never once fired, and the locale
cache was answering a question nobody asked it.**

v0.66.1 claimed exit 36 arrived in 0 ms on Google's migrated `flow.google.com` frontend
([#639](https://github.com/ffroliva/gflow-cli/issues/639)). It did not. That number came from
calling `get_ui_driver` **in isolation on a page already sitting on the migrated origin** — a
state no real run ever reaches, because `project_editor_url` only ever builds a `labs.google`
URL and the hop to the new origin is a *post-`goto`* redirect that neither settle path waits
for. The guard read a pre-redirect URL and declined. The reporter measured the truth on three
consecutive runs: **57.0 / 57.1 / 58.3 s**, through the slow selector-probe path, with the bail
event absent from the timeline entirely.

**Check the host where the run already blocks.** A shared `raise_if_migrated()` now runs at
`get_ui_driver` entry, on every `detect_ui_mode` poll tick, in `_exit_agent_mode` once the media
panel is found absent, and inside `mode_control`'s 8 s composer poll — the last of which was
found by review after the first fix shipped, and covers both `--ui-mode agentic` and a redirect
landing mid-`ensure_media_mode`. None of these adds a wait: `page.url` is a cached property and
`flow_host_kind` is one parse plus a dict lookup. Three blanket `except Exception` handlers that
would have demoted the abort to a warning now re-raise it.

**Stop letting a `lang` attribute mean "this account redirects".** Two locale defects, both
shipped in v0.66.1. `NOT_REDIRECTED` was an *absorbing* state: it returned before the only site
of the `<html lang>` recovery, so the profiles #643 was written for could never use it. And the
recovered locale was then recorded as evidence of a redirect — which it is not, since every
account declares one — switching the URL settle back on permanently and costing the full 4 s
`URL_SETTLE_TIMEOUT_MS` on every bootstrap of any non-redirecting account. Measured on a real
profile: **7.41 s → 2.66 s**, locale still recovered
([#643](https://github.com/ffroliva/gflow-cli/issues/643)).

Two designs were rejected **by measurement**, not preference: a bounded settle (72/72
navigations across two accounts showed no post-`goto` URL change at all, so the bound would be
pure dead time), and caching "this profile is migrated" to navigate straight to
`flow.google.com` (it would have locked both maintainer accounts out of a working frontend the
day it shipped).

Drivable for text-to-video since v0.67.0; #639 stays open for the rest of the matrix.

Verification: [LIVE_VERIFICATION_v0.66.2](LIVE_VERIFICATION_v0.66.2.md). The old host is proven
live at zero credits (**exit 0**, real 768x1376 JPEG, twice). The **migrated** path — which no
maintainer account could reach, because the rollout flapped back mid-work — was verified by
[@maipmacrothorax-75](https://github.com/maipmacrothorax-75) on a permanently-migrated account:
**57 s → 4.08–4.26 s**, `ui_driver.migrated_host_bail` present with `at=mode_control` on all nine
runs, and the crop cascade and agent dismissal both gone. That measurement was posted to #639
before the tag was cut and was not read in time, so the shipped verification doc understated its
own result; the correction is recorded there rather than quietly applied.

**Found by that same verification and NOT fixed here:** on a pt-BR account the locale resolves
`en` while both pages serve `<html lang="pt">` — the probe appears to read before the app sets
the attribute. That is the population #643 was written for, so the `<html lang>` fallback can
latch a locale the account does not use. Tracked separately; it is not a migration bug.


</details>

<details><summary>v0.66.1 — a fast-fail that never fired (superseded by v0.66.2)</summary>

**v0.66.1 — alpha.** Intended to make the migrated-origin failure instant and to stop discarding
a learned locale. **Both fixes were real but neither reached the path users take**, and this
entry is kept to record that rather than to claim it.

The `0 ms` exit 36 reported here was measured on `get_ui_driver` in isolation, on an
already-migrated page; the CLI route was unchanged at ~57 s. The `<html lang>` locale recovery
was likewise exercised directly, while the client returned before it on any profile cached
`NOT_REDIRECTED`. v0.66.2 fixes both, and additionally a settle regression this release
introduced. See the correction block at the top of
[LIVE_VERIFICATION_v0.66.1](LIVE_VERIFICATION_v0.66.1.md).

What did hold: `_check_logged_in` accepting the migrated host, and the old-host no-regression
result (exit 0 with a real JPEG), which was the load-bearing evidence in that release.

</details>

<details><summary>v0.66.0 — naming Flow's flow.google.com migration</summary>

**v0.66.0 — alpha.** **Google started moving Flow to a new domain, and gflow blamed its own
selectors for it.**

On a migrated page load, `labs.google/fx/tools/flow/project/<id>` redirects to
`flow.google.com/project/<id>` and serves a rewritten frontend containing **zero `<i>`
elements**. Every gflow selector anchors on Material Symbols ligatures, so cohort detection
and every mode control missed at once and the run died `UiSelectorDriftError` — exit 23,
`retryable: false`. That error means "a selector rotted, file a bug", so it sent operators
hunting where nothing was broken. The selectors are correct for the host they were written
against; that host is being replaced under them
([#639](https://github.com/ffroliva/gflow-cli/issues/639), reported by
[@maipmacrothorax-75](https://github.com/maipmacrothorax-75) with measurements from both
sides of the rollout).

v0.66.0 read the rollout as **flapping per page load** — the same account landing on the old
host on one navigation and the migrated one on the next — and on that reading made
`FlowHostMigratedError` (exit 36) **retryable**. The reading was wrong: the handoff is a
server-assigned per-account flag that the labs.google app applies client-side with
`window.location.replace`, one-way and not transient (settled 2026-09-04 —
[spike](superpowers/spikes/2026-09-04-migrated-host-handoff-mechanism.md)), so exit 36 is
**non-retryable** since that fix. What v0.66.0 got right stands: gflow recognises the migrated
origin and raises a distinct error, and CLI and MCP both inherit it — they share the raise
site, and `is_retryable` is a single source of truth for the `--json`, MCP and worker
envelopes.

Tracing the same root cause turned up three things the report did not name. `_check_logged_in`
hard-required `labs.google` in the URL, so a valid authenticated session on the migrated host
was read as **logged-out** — and that gate was a *substring* match, satisfied by any foreign
URL merely carrying the string in a path or query. Separately, `UiSelectorDriftError` is an
incident-**capture** trigger, so swapping in a new class would have silently switched off
bundle capture for exactly the failure whose evidence is most wanted; a test now pins that
invariant across all four arms of the raise site rather than a list of names.

**This does not add support for the migrated frontend.** It converts a confusing hard failure
into a clearly named one. No retry helps — the flag is per account — and #639 stays open for
the driver work, with the anchor recon it needs already recorded.

Verification: [LIVE_VERIFICATION_v0.66.0](LIVE_VERIFICATION_v0.66.0.md) — proven against the
**real** migrated frontend at zero credits (`i_total: 0`, `flow_host_kind: "migrated"`,
`check_logged_in: true`, exit 36, `retryable: true` as designed then — `false` since the
2026-09-04 fix), with a credit-free `image t2i` on the
old host minutes earlier completing exit 0 to prove no regression.

</details>

<details><summary>v0.65.0 — the referenceEntity guard that had never fired</summary>

**v0.65.0 — alpha.** **A safety net that had never once fired, and two crashes that spent
your credits before failing.**

The `referenceEntity` guard strips character entities the caller did not request, so a
"poisoned" entity left in the Flow composer cannot smuggle itself into an unrelated
generation. It had never run on any released version
([#615](https://github.com/ffroliva/gflow-cli/issues/615), reported by
[@DioServis](https://github.com/DioServis)). The route glob could not match Flow's
namespaced endpoint — and the *video* guard was dead for the same reason, which the report
did not mention. It failed **open**, silently, because the guard logged only when it
stripped something: "never ran" and "ran, nothing to strip" were identical silence.

That silence is why it hid for months, and fixing it is what made the fix provable. The
guard now announces every intercepted request, so **absence of the event is evidence**
([#620](https://github.com/ffroliva/gflow-cli/issues/620)). The result is A/B-verified
live at zero credits: without the fix the guard never fired and the e2e failed; with it,
it fired and passed — same account, same prompt, one variable. That also settled the
question that had held the fix for two days (Flow delegates to a **dedicated Web Worker**,
so `context.route` suffices) and proved the request rewrite does not corrupt the body.

Separately, `gflow video chain` and `gflow movie run` historically died **mid-spend** on
`duration` × `model` ([#634](https://github.com/ffroliva/gflow-cli/issues/634)) — after
earlier links or scenes had already rendered and billed. Historically, manifests pairing
unsupported durations or models caused late crashes — including an example manifest
tested against obsolete assumptions ([#635](https://github.com/ffroliva/gflow-cli/issues/635)).
Both surfaces now share the canonical duration check: Veo accepts 4/6/8s, omni-flash accepts
4/6/8/10s, and invalid combinations are refused before the first submit (and `--dry-run`
refuses what the real run refuses). Single-clip i2v ([#630](https://github.com/ffroliva/gflow-cli/issues/630))
similarly exits 2 naming the model instead of exit 1 `"Unexpected error"`.

**Validation:** a `movie.toml` scene `duration` accepts 4/6/8 for Veo 3.1 and 4/6/8/10 for
omni-flash; invalid values are rejected during parsing (exit 11) before any spending occurs.

**Known limitation:** the guard covers browser-driven generation only. Direct-wire routes
issued through Playwright's `APIRequestContext` are not routable at all and bypass it
regardless of matcher — [#619](https://github.com/ffroliva/gflow-cli/issues/619).

Verification:
[LIVE_VERIFICATION_reference_entity_guard](LIVE_VERIFICATION_reference_entity_guard.md).

</details>

<details><summary>v0.64.0 — <code>i2v --end-frame</code> on Omni 1.1 Flash</summary>

**v0.64.0 — alpha.** **`gflow video i2v --model omni-flash --end-frame` — first+last
interpolation on Omni 1.1 Flash ([#626](https://github.com/ffroliva/gflow-cli/issues/626)).**
Google shipped first-and-last-frame generation for Omni 1.1 Flash, so the guard that
rejected that combination with exit 17 was enforcing a fact that had expired. omni-flash is
also the only model exposing `--duration`, so this is the one route to a 10-second first+last
interpolation.

The interesting part is what replaced the guard. gflow used to decide which models could
carry an end frame from a hand-maintained mirror of Google's support matrix — which went
stale silently, and could have gone stale in the permissive direction just as easily. That
table is **deleted**, not corrected. gflow now checks the route Flow *actually* used after
submit: a run carrying an end frame that comes back on `batchAsyncGenerateVideoStartImage` —
Flow dropping the frame and billing a clip that was never interpolated — fails with
`WireFormatError` rather than being reported as success. That catches a partial or staged
rollback on any account, at any time, with nobody re-reading a support page. It also closes a
narrower hole the old backstop had: it only fired when Flow dropped *every* frame to the T2V
route.

**Live-verified on two accounts at zero credits plus one paid render.** The route-abort probe
fired `batchAsyncGenerateVideoStartAndEndImage` with both images non-null on two distinct
Google accounts, ruling out a staged rollout. The decisive layer is semantic, not structural:
the paid 4s clip's **last frame is the supplied end image** and its first frame is the start
image — the only check that distinguishes "Flow used the end frame" from "Flow accepted and
ignored it", since exit code, HTTP status and file properties pass either way. Recorded as
*not* verified rather than omitted: `--duration 10` + end frame is submit-verified only, its
status poll having hit the pre-existing 401 of
[#561](https://github.com/ffroliva/gflow-cli/issues/561). See
[LIVE_VERIFICATION_v0.64.0.md](LIVE_VERIFICATION_v0.64.0.md).

**Breaking:** scripts branching on exit 17 for `omni-flash` + `--end-frame` now see success.
Exit 17 is unchanged for `gflow video chain --model omni-flash`, still rejected because
chain-scale seeded i2v remains unverified.

Also in this release, contributor-facing: **MCP↔CLI parity became a duty of every pipeline
phase.** This very change drifted — `mcp/tools.py` and `docs/MCP.md` went on telling agents
`omni_flash` was rejected for i2v-with-frames — through green lint, types, the full suite and
`test_cli_parity.py`, which is command-level and cannot see a docstring that lies. Each skill
now owns a slice (`scenario` D13, `plan` task 6, `pr-council-review` D15, `check` step 1b,
`doc-review` blocking on a *false* MCP claim); automating the mechanically checkable part is
tracked in [#628](https://github.com/ffroliva/gflow-cli/issues/628).


</details>

<details><summary>v0.63.0 — <code>gflow video extend</code>, past Flow's 8-second ceiling</summary>

**v0.63.0 — alpha.** **`gflow video extend` — continue a clip past Flow's 8-second ceiling.**
Veo caps a single generation at 8 seconds. `extend` chains server-side
continuations: each segment is seeded from the *previous segment's* media rather
than an extracted still, so the join is continuous rather than a cut. The run
lands as a Flow Scene, and `-o/--output` renders it to one file through the
existing credit-free server-side concat.

The model key is resolved from the account's live capability listing rather than
hardcoded (`extend_model_resolved` logs `candidate_count`, `service_tier`,
`unit_cost`), which *prevents* a tier-403 instead of classifying one after the
fact. A whole-run balance pre-flight aborts before the first submit rather than
at segment 6 holding a half-length video, submissions are paced by the shared
jitter resolver, and an interrupted run publishes its resume handle before the
first submit so Ctrl+C reports real credits spent and `--resume-from` appends
after the scene's true tail.

**Live-verified at 20 credits, and the run found a defect the offline suite
structurally could not:** an extend segment carries **7.000s** of content while
Flow advertises and bills 8, so server-side concat pads every internal seam with
a frozen frame and digital silence. That is filed in
[KNOWN_ISSUES.md](../KNOWN_ISSUES.md) with the three questions that must be
answered before any clamp, and it is why `--extend N` on `t2v`/`i2v` was
deliberately **not** shipped — a convenience wrapper whose default output is
defective is worse than no wrapper. See
[LIVE_VERIFICATION_v0.63.0.md](LIVE_VERIFICATION_v0.63.0.md).

Also in this release: `CLAUDE.md` now `@`-imports `AGENTS.md` so the project's
agent rules load rather than being politely requested, and `AGENTS.md` opens with
a Skill Routing table making the `/gflow:` lifecycle the default workflow.

</details>

> **Releases v0.60.0 – v0.62.1 are not expanded below.** This file drifted for
> five releases; rather than reconstruct their summaries after the fact, they are
> recorded accurately in [CHANGELOG.md](../CHANGELOG.md) and in their own
> `LIVE_VERIFICATION_v*.md` evidence files. The gap is named here rather than
> hidden, so the next release does not inherit a silent hole.

<details><summary>v0.58.0 — catalog-name picker contract (#529) + r2v named-reference fixes</summary>

**v0.58.0 — alpha.** **Catalog UUIDs resolve through Flow display names (#529) + r2v named references un-broken after Flow's picker redesign.** The picker
contract is now `catalog UUID → workflows[].metadata.displayName → picker name
search → exact UUID-in-thumbnail tile`: the UI response collector preserves the
sibling workflow name so generated-image catalog rows retain the search key
(store-mode history only), image `--ref <uuid>` and I2V frame UUIDs are enriched
with it, and UUID/UUID-stem/prompt-hint/grid-scroll fallbacks are gone from that
path — a missing name falls through to the SHA-verified local-file upload or a
typed error, never an unfiltered click. Live-verifying the r2v surface caught
two silent UI drifts and fixed them: the picker dialog exposes no accessible
tree (tiles are now text-matched with an anchored, locale-badge-tolerant
regex), and clicking a result now attaches directly (include button demoted to
legacy fallback). Four committed e2e tests pin all of it against real Flow,
including a real r2v video generated from a catalog name. See
[LIVE_VERIFICATION_v0.58.0.md](LIVE_VERIFICATION_v0.58.0.md).

</details>

<details><summary>v0.57.1 — two long-open UI bugs root-caused from live evidence (#493, #451/#288)</summary>

**v0.57.1 — alpha.** **Two long-open UI bugs root-caused from live evidence (#493, #451/#288).**
#493 was reported as an unrecognized "third editor variant"; it is not. Flow's
**expanded chat sidebar** removes the classic composer entirely — no `crop_*`
settings trigger *and* no Agent pill — which is one state producing both reported
symptoms, and why the failure surfaced as `UiSelectorDriftError` (exit 23) rather
than the retryable agentic error: with no agentic indicator on screen, the cohort
detector matched nothing. Recovery hinged on a close button scoped to the
sidebar's `edit_square` affordance, so a cohort lacking that ligature could never
recover; `ensure_media_mode` now falls back to an unscoped close from the
demonstrably stuck state, A/B-proven live. #451/#288 were never selector drift
either: Flow's settings popover is **model-conditional by cohort**. While the historical
negative matrix (denon82, 2026-08-14) found no duration row for Veo, a subsequent live
capture (presentation-reels-google, labs.google, 2026-09-04, PR #650) confirmed that
cohorts exposing duration controls offer 4/6/8s for Veo 3.1 and 4/6/8/10s for `omni-flash`.
Both matrices remain valid for their respective cohorts. The CLI now validates the
canonical model cap at the edge with exit 2 before any browser work.
Also: `--reference-entity` no longer advertised on `video i2v` (its DTO always
rejected it), a typed `ReferenceNotFoundError` (exit 32) replaces a bare
Playwright timeout. (`gflow models` reported `max_duration` 0 for the Veo models
under that rule; since #650 it reports 8, because the cohorts that render the row
genuinely accept it.) The v0.64.0 note below described a duration users cannot
set. Everything verified at **zero credits**. See
[LIVE_VERIFICATION_v0.57.1.md](LIVE_VERIFICATION_v0.57.1.md).

</details>

<details><summary>v0.57.0 — video joins the UI-mode policy + MCP truthfulness wave</summary>

**v0.57.0 — alpha.** **Video joins the UI-mode policy (#299) + an MCP truthfulness wave (#496–#501).** `gflow video t2v`/`i2v` and MCP `gflow_generate_video` now take `--ui-mode`/`ui_mode` and bind their driver through `get_ui_driver` after editor mount instead of a hardcoded classic bind; video has only a classic driver, so `auto` ≡ `classic`, an env-sourced `agentic` degrades with a warning, and an explicit `--ui-mode agentic` is refused with exit 2 before any browser work. When Flow serves the agentic editor and classic cannot be recovered, video now fails fast pre-submit (`UiModeUnavailableError`, exit 28, zero credits) instead of burning 30–40 s of doomed selector timeouts. The agentic direction was hardened to match the classic one: `mode_control.ensure_agent_mode` replaces `_force_agent_mode` (deleted) with real-click-first + `aria-pressed` verification, no `tune`-ligature check, unknown editor variants no-op with a warning, and the sanctioned reload carries an explicit 15 s timeout. On the MCP side: `gflow mcp run --no-spend` (#496) unregisters both generate tools so an agent cannot even see them; `gflow_auth_status` (#497) gives agents a credit-free pre-flight session probe; `gflow_list_projects` paginates honestly with `offset`/`has_more`/`next_offset` (#498); the stub `gflow_list_characters` — which always answered "no characters", an active lie — is gone (#499); and `gflow://docs/known-issues` is bounded to a small index plus a per-issue templated read (#501) instead of injecting ~70 KB on every read. On the supply-chain side the release finishes the Scorecard hardening tail: every workflow now runs with a least-privilege token and every `uses:` action is pinned to a full commit SHA, the remaining in-workflow package installs are hash- or digest-pinned (`website/requirements.txt`, `Dockerfile.triage`, `pip-audit`), CI enforces a test-count floor so "a green build that ran nothing" cannot pass, `check_repo_hygiene.py` fails on version disagreement between `pyproject.toml`/`__init__.py`/`plugin.json`, and a self-run OpenSSF Scorecard workflow publishes the score as a README/website badge with a [SECURITY.md](SECURITY.md) section explaining it. Everything in this release was verified at **zero credits**. See [LIVE_VERIFICATION_v0.57.0.md](LIVE_VERIFICATION_v0.57.0.md).

</details>

**Develop (unreleased, post-v0.59.0):** nothing queued yet — see `CHANGELOG.md` `[Unreleased]` for the authoritative list. `develop` is the staging branch for the next release; this line is not a substitute for the changelog.

<details><summary>v0.56.0 — ops-hardening batch + honest mode-switch evidence (#477/#478/#479, #493)</summary>

**v0.56.0 — alpha.** **Ops-hardening batch (#477/#478/#479) + honest mode-switch evidence (#493).** A Chromium major-version downgrade guard refuses to open a persisted profile with an older bundled engine than last wrote it (`ProfileEngineDowngradeError`, exit 11 — pre-auth, pre-credits) instead of letting Chromium's downgrade cleanup shred the session store; `GFLOW_CLI_LEASE_WAIT_SECONDS=N` adds an opt-in bounded wait on same-profile lease contention (default keeps the historical fail-fast); a once-a-day cache-served PyPI check prints a one-line stderr notice when a newer gflow-cli exists; and the exit-23 mode-switch fall-through now states that no known Flow cohort matched (the third, unrecognized editor layout from #493) while the drift remediation names the artifacts runs actually produce (`diag_mode_switch_miss.json`, the referenced screenshot, the incident bundle) instead of a phantom "debug screenshot from this message". Guard, lease wait, and the mode-switch path were live-verified; #479's notice is deferred-with-reason to a post-release check (it needs a published newer version on PyPI). See [LIVE_VERIFICATION_v0.56.0.md](LIVE_VERIFICATION_v0.56.0.md).

</details>

<details><summary>v0.55.0 — Tier-1 hardening batch + docs truth sweep (#471–#476)</summary>

**v0.55.0 — alpha.** **Tier-1 hardening batch (#471–#476) + docs truth sweep.** `gflow auth status` now **proves** the Flow session (cookie-jar probe of the live session endpoint, no browser, no credits) and exits 0/1 with remediation hints; `gflow mcp setup` is implemented (claude-desktop / cursor / vscode, non-destructive merge with a pristine one-time backup); incident bundles stage a pre-filled `report.md` bug-report template built from allowlisted manifest fields only; Windows profile dirs get a real restrict-to-current-user DACL at login plus a marker-gated upgrade sweep at browser launch; all 11 MCP tools route through one error funnel that masks raw exception text from clients; `llm_api_key`/`daemon_token` are `SecretStr` so a Settings dump cannot leak them. The false "requires a Google AI Ultra or Pro subscription" claim was removed everywhere — **any Google account with Flow access works**; only feature gates (4K upscale) are tier-bound. See [LIVE_VERIFICATION_v0.55.0.md](LIVE_VERIFICATION_v0.55.0.md).

</details>

<details><summary>v0.54.0 — login close-guidance + supply-chain guards (#465, #470)</summary>

**v0.54.0 — alpha.** Clearer close-the-browser guidance in `gflow auth login` (#470); overlay/watermark detection hardened to pure structural selectors; Dependabot now ignores playwright minors and all patchright bumps after PR #465 proved a `pyproject.toml` bound does not gate the `uv` ecosystem (it rewrites it); timing-jitter entropy moved to `secrets.SystemRandom` (SonarCloud S2245). First mainline release after the orphaned v0.53.x tags. See [LIVE_VERIFICATION_v0.54.0.md](LIVE_VERIFICATION_v0.54.0.md).

</details>

<details><summary>v0.53.0 / v0.53.1 — interaction humanization + release-modal detection (#315, #403)</summary>

**v0.53.0 — alpha.** Driver interaction delay humanization (`_jitter_ms`, #315) and locale-invariant Flow release-overlay detection for the watermark-toggle modal (#403). **v0.53.1** is a version-bump re-release recording headed live-verification evidence. Both tags were published without reaching mainline at the time; their content and honest CHANGELOG sections were reconciled during the v0.54.0 cut.

</details>

<details><summary>v0.52.0 — intra-batch references + entity provenance parity (#317, #402, #451)</summary>

**v0.52.0 — alpha.** Intra-batch reference support for `gflow image batch` (`ref="batch:0"` DAG ordering, #317); `--reference-entity`/`--reference-entity-name` parity on the video commands with provenance recording (#402); video duration selector cascade fix (#451). See [LIVE_VERIFICATION_v0.52.0.md](LIVE_VERIFICATION_v0.52.0.md).

</details>

<details><summary>v0.51.0 — dependency & audit hygiene release</summary>

**v0.51.0 — alpha.** `pip-audit` now covers every optional extra; the full Dependabot backlog cleared in one lock update; the `playwright` bound raised `>=1.59.0,<1.60.0` → `>=1.61.0,<1.62.0` after live A/B evidence (1.62.0 stays excluded — it hangs i2v at frame upload). See [LIVE_VERIFICATION_v0.51.0.md](LIVE_VERIFICATION_v0.51.0.md).

</details>

<details><summary>v0.49.0 / v0.50.0 — omni-flash I2V + MCP Tasks extension (#125, #409)</summary>

**v0.49.0 — alpha.** `omni-flash` start-frame I2V (#125), playwright upper-bound pin, submission-stage watchdog, count-tab fail-closed logic (#404). **v0.50.0 — alpha.** MCP 2026-07-28 Tasks extension (SEP-2663, #409) with non-blocking task handles, `-o`/`--output` path hardening (#414/#415), PR-triage alert resilience (#428). See [LIVE_VERIFICATION_v0.49.0.md](LIVE_VERIFICATION_v0.49.0.md) / [LIVE_VERIFICATION_v0.50.0.md](LIVE_VERIFICATION_v0.50.0.md).

</details>

<details><summary>v0.48.0 — predictable output paths (#411)</summary>

**v0.48.0 — alpha.** **Predictable output paths (#411).** Adds an explicit `-o` / `--output` flag to `image t2i`/`i2i` and `video t2v`/`i2v`: the asset lands at the exact **local** path you name (parents auto-created, `-o` beats `--out`/`--out-dir`), with deterministic `_1`, `_2`, … suffixes for multi-count image runs. Cloud (`s3://`/`gs://`) targets, `r2v`/`chain`, video multi-count suffixes, and an MCP-tool `output` param are tracked follow-ups (#414, #415 — the MCP param was cut pre-release when the audit showed the worker queue never reads it). See [LIVE_VERIFICATION_v0.48.0.md](LIVE_VERIFICATION_v0.48.0.md).

</details>

<details><summary>v0.47.0 — MCP SDK 2.0.0 migration + entity provenance (#402, #407, #408)</summary>

**v0.47.0 — alpha.** **MCP SDK 2.0.0 migration + dual-era protocol + entity provenance (#402, #407, #408).** Bounds `mcp` dependency to `>=2.0.0,<3`, migrates server to `MCPServer`, supports both 2026-07-28 and legacy protocol eras via SDK, defaults `gflow serve` to Streamable HTTP at `/mcp`, adds `resolve-drift` CI job, and records `entity_ids` / `entity_names` in `operations.metadata_json` on generation operations. See [LIVE_VERIFICATION_v0.47.0.md](LIVE_VERIFICATION_v0.47.0.md).

</details>


<details><summary>v0.46.0 — prompt tools on any OpenAI-compatible endpoint (#387)</summary>

**v0.46.0 — alpha.** **BREAKING — the prompt tools drive any OpenAI-compatible endpoint (#387).** `--tool creative-director` / `reverse-engineer` / `storyboard` now speak OpenAI Chat Completions instead of being hardwired to Google's native Gemini API, so OpenAI, gateways/proxies (OpenRouter, LiteLLM), local runtimes (Ollama, LM Studio), and Google's own compatibility endpoint all work. New config: `GFLOW_CLI_LLM_BASE_URL`, `GFLOW_CLI_LLM_API_KEY` (optional — omitted when unset so keyless local gateways work), `GFLOW_CLI_LLM_MODEL`. The removed `GFLOW_CLI_GEMINI_API_KEY` / `GFLOW_CLI_GEMINI_MODEL` trigger a loud one-time notice, never a silent no-op. The user-supplied endpoint is treated as a trust boundary: redirects declined, `https` (or loopback `http`) enforced, error bodies redacted. `reverse-engineer` degrades to the original input instead of expanding a file path as a prompt. See [LIVE_VERIFICATION_v0.46.0.md](LIVE_VERIFICATION_v0.46.0.md).

</details>

<details><summary>v0.45.0 — reference + character binding fixes (#393/#395)</summary>

**v0.45.0 — alpha.** **Reference + character binding fixes (#393/#395).** `gflow image i2i --ref <UUID>` now attaches the catalog's recorded file when Flow's per-project media picker cannot reach the tile, instead of failing the run — the fail-loud contract (never generate without a requested reference) is unchanged and pinned by a live e2e. `gflow character create` binds portraits to the character again: overlay dismissal was pressing Escape on Flow's own composer (`[role='dialog']`/`[role='alert']` matched the app itself), and the character route could bounce to the project page, sending the prompt to the **project** composer — both produced generations with no `entityContext`, which Flow filed as plain project images. Also: a Flow web-app crash on the character route is now the typed retryable `FlowAppError` (exit 31), character binding failures say what actually happened, and `--format-prompt` (#383) is live-verified. 2810 tests pass. See [LIVE_VERIFICATION_v0.45.0.md](LIVE_VERIFICATION_v0.45.0.md).

</details>

<details><summary>v0.44.0 — dual-side project naming &amp; management (#381)</summary>

**v0.44.0 — alpha.** **Dual-side project naming & management (#381).** Feature set includes `gflow project` subcommand family (`list`, `show`, `rename`, `create`), `--project-name` / `--project-title` options across `gflow image` (`t2i`, `i2i`) and `gflow video` (`t2v`, `i2v`, `r2v`) generation commands, prompt slugging for scratch projects, dual-side title sync updating Google Flow's tRPC server/UI and local SQLite catalog in lockstep, HTTP 429 adaptive backoff / `RateLimitError` recovery (#384), character body prompt composer fixes (#378), and a repo-local Codex plugin (`$gflow:*` skills). Verified via full test suite and live Playwright Chromium E2E transport test. See [LIVE_VERIFICATION_v0.44.0.md](LIVE_VERIFICATION_v0.44.0.md).

</details>


<details><summary>v0.42.0 — content-safety classification + Antigravity coding agent (#359/#360/#361)</summary>

**v0.42.0 — alpha.** **Content-safety classification + Antigravity migration (#359/#360/#361).** Content-safety `400` responses from Flow are now classified as `ContentPolicyError` (exit 5) instead of the misleading `WireFormatError`, so callers can branch deterministically on a policy rejection. Corrected a false `flow_operation_id` invariant (`veo-lite` can emit a `remote_started` checkpoint with `operation_id` unset; `media_id` is the canonical handle used by polling, download, and lookup). Replaced the retired Gemini CLI with Antigravity (`agy`) as the supported Google coding agent across skills and docs — Antigravity auto-discovers `AGENTS.md`, so the dedicated `GEMINI.md` hub is removed and `agy` is the pinned `high`-tier `llm-council` external reviewer. See [LIVE_VERIFICATION_v0.42.0.md](LIVE_VERIFICATION_v0.42.0.md).

</details>

<details><summary>v0.41.0 — production-readiness hardening (#357)</summary>

**v0.41.0 — alpha.** **Production-readiness hardening** ([#357]): queue safety (versioned payloads, atomic claims, checkpointed execution phases), cross-process profile lease (`ProfileLockedError` exit 11), cancellation-safe browser teardown, driver honesty (typed `SupportsSendPrompt` injection, frozen `TransportSetup`), mention-index fail-closed (`MentionIndexUnavailableError` exit 29), and external-CDP lifecycle removal. Removed nonfunctional manifest-driven video batch command (never worked end-to-end; loop `gflow video t2v`/`i2v` from the shell instead). Also: `/gflow:live-verify` skill for per-feature live-verification enforcement. 2513 tests pass; live-verified against real Flow (stale-session fail-fast, free image gen, paid veo-lite T2V). See [LIVE_VERIFICATION_v0.40.0-production-readiness.md](LIVE_VERIFICATION_v0.40.0-production-readiness.md).

</details>

<details><summary>v0.40.0 — prompt @-mention resolution for asset tagging (#344)</summary>

**v0.40.0 — alpha.** **Prompt `@`-mention resolution for asset tagging (#344).** `@Name` in a t2i/i2i/video prompt resolves to a staged, taggable character entity via `services/mentions.py`'s `resolve_and_apply`, shared by the `image`/`video` CLI paths, the async worker, and MCP tools. Media-asset (non-character) `@`-mentions also work, but on the **image path only** — video-path media mentions are Phase 3. A bare character with no reference images is rejected early with a clear error instead of failing deep in the UI attach. De-tagged prompts are persisted to the catalog. See [REFERENCE_STRATEGIES](REFERENCE_STRATEGIES.md) for `@`-mention vs `--reference-entity` vs `--ref`. Verification: [LIVE_VERIFICATION_v0.40.0](LIVE_VERIFICATION_v0.40.0.md) (`gflow character create` + `@Zoro` t2i passed live end-to-end against unmodified `develop`, ~2 Imagen credits; also records a same-cycle investigation dead end where a stale local WIP branch was mistaken for `develop`'s real state).

</details>

<details><summary>v0.39.0 — failed-generation persistence + gflow data list errors</summary>

**v0.39.0 — alpha.** **Failed generations are now persisted to the local catalog (#341).** Every paid-generation path (`video t2v/i2v/r2v`, `video chain`, `image t2i/i2i`, multi-prompt t2i, `gflow run`, `movie run`, and the async worker) now records a terminal `status="failed"` operation row — with a stable `error_type` derived from the exception's RFC 9457 `problem_type` and a redacted `error_detail` — before the error propagates, so WAF-403 block onset, duration, and recovery windows are measurable instead of reconstructed from memory. The new `gflow data list errors` subcommand browses failed operations newest-first, and videos whose poll returns `succeeded=false` are now recorded as `failed` (previously mis-recorded as `succeeded`). Bounded retention + export deferred to #345. Verification: [LIVE_VERIFICATION_v0.39.0](LIVE_VERIFICATION_v0.39.0.md) (a real `image t2i` HTTP-400 wire failure produced the first `failed` row in the production catalog, $0).

</details>

<details><summary>v0.38.1 — agentic-pin recovery (opt-in reload after a real toggle-off)</summary>

**v0.38.1 — alpha.** **Agentic-pin recovery (#338).** When a real, unforced Agent-toggle click lands but the classic media panel never mounts in place (the 2026-07-17 both-accounts pin), `ensure_media_mode` can now reload the page once — opt-in and sanctioned only from the pre-bind classic path — to re-roll the server's per-load cohort and mount the persisted `isAgentModeToggled=false` preference; a composer-render race that made the toggle unreachable was also fixed. Verification: [LIVE_VERIFICATION_v0.38.1](LIVE_VERIFICATION_v0.38.1.md) (classic recovered on denon82 after ~2h of active server pin).

</details>

<details><summary>v0.38.0 — robust agentic↔classic mode control + i2i ref dedup</summary>

**v0.38.0 — alpha.** **Robust agentic↔classic mode control + i2i reference dedup (#332, #314).** `--ui-mode` is now driven by a state-aware mode controller reading Flow's Agent toggle `aria-pressed` state (locale-invariant), ending spurious "forced agentic — not recoverable" aborts caused by an icon heuristic that matched both modes; `--ui-mode classic` reliably reaches the classic editor. Repeated local `--ref` images in `gflow image i2i` are now attached by selecting the already-uploaded library tile via exact-filename picker search (with a virtualised-grid scroll fallback, #335) instead of re-uploading duplicates. Also ships the PR-triage autopilot implementation (#238/#333, host deployment staged separately) and a behavior-preserving cognitive-complexity refactor (#331). Verification: [LIVE_VERIFICATION_v0.38.0](LIVE_VERIFICATION_v0.38.0.md) (agentic→classic recovery observed in a real run; dedup contract proven with an upload-then-dedup run pair; veo-fast t2v successful).

</details>

<details><summary>v0.37.0 — viewport 1920×1080 + agentic count enforcement</summary>

**v0.37.0 — alpha.** **Viewport 1920×1080 + agentic count enforcement + FIPS-safe SAPISIDHASH (#313, #315, #329).** UI-automation and auth-login viewports harmonized to 1920×1080 to blend with the most common desktop resolution; the agentic cohort's requested image count (`-n`) is reliably enforced via the Agent settings panel; the protocol-mandated SHA-1 in SAPISIDHASH is marked `usedforsecurity=False` so it works under FIPS-mode Python. Also bumps `mcp` to 1.28.1 (CVE-2026-59950). Verification: [LIVE_VERIFICATION_v0.37.0](LIVE_VERIFICATION_v0.37.0.md).

</details>

<details><summary>v0.36.0 — diagnostic tooling + reference-entity-smuggling fix</summary>

**v0.36.0 — alpha.** **Diagnostic tooling (`GFLOW_CLI_HAR_PATH` + `GFLOW_CLI_DEBUG_TRACEBACK`) + reference-entity-smuggling fix (#312/#316).** Two opt-in, env-var-only debug knobs: `GFLOW_CLI_HAR_PATH` captures full Playwright network traffic to a HAR file (0600-hardened on POSIX); `GFLOW_CLI_DEBUG_TRACEBACK` prints the real exception + traceback for unhandled errors (console and `--json`) instead of the generic placeholder, while structured telemetry stays hashed unconditionally either way. Also fixes a poisoned character entity leaking `referenceEntities` into unrelated `image i2i` calls in the same project workspace. Also ships the `llm-council` skill (`/gflow:llm-council`), adding external CLI reviewers (`codex`/`gemini`, opt-in `agy`) alongside the internal council for high-stakes reviews. Verification: [LIVE_VERIFICATION_v0.36.0](LIVE_VERIFICATION_v0.36.0.md) (live HAR capture against real Flow traffic; typed-error scoping confirmed live; unhandled path covered by 6 new tests, 30 total across both files).

</details>

<details><summary>v0.35.0 — multimodal reverse-engineering + storyboard tool</summary>

**v0.35.0 — alpha.** **Multimodal reverse-engineering + Storyboard tool + Dynamic Token Budgeting (#305-follow-up).** Adds `gflow tools run storyboard` to generate sequential visual prompts from single ideas, and integrates `gflow tools run reverse-engineer` with `claude-video`'s `watch.py` script for frame extraction and multimodal deconstruction of video/URL references using Gemini. Token budgets now scale dynamically with character limits. Verification: [LIVE_VERIFICATION_v0.35.0](LIVE_VERIFICATION_v0.35.0.md) (proven live storyboard expansion + frame extraction).

</details>

<details><summary>v0.34.0 — bidirectional UI cohort switching</summary>

**v0.34.0 — alpha.** **Bidirectional UI cohort switching (#299).** Introduces `--ui-mode` / `GFLOW_CLI_UI_MODE` to force classic or agentic Flow UI cohort layouts on the fly, with verification and an exit-28 fail-fast when a required layout cannot be reached. Verification: [LIVE_VERIFICATION_v0.34.0](LIVE_VERIFICATION_v0.34.0.md).

</details>

<details><summary>v0.33.0 — anti-bot jitter and video i2v project name overrides</summary>

**v0.33.0 — alpha.** **Anti-bot jitter and video i2v project name overrides (#241, #287).** Configurable anti-bot jitter range via `--jitter` / `GFLOW_CLI_JITTER_RANGE`, lower default batch jitter (0.5–1.5 s), and `--project-name` overrides for resolving in-project assets on localized or virtualized project dropdowns. Verification: [LIVE_VERIFICATION_v0.33.0](LIVE_VERIFICATION_v0.33.0.md).

</details>

<details><summary>v0.32.1 — browser teardown hardening</summary>

**v0.32.1 — alpha.** **Browser teardown hardening and profile lock translation (#293, #283).** Fixed Chrome process leaks on aborted context teardowns, translated launch failures to `ProfileLockedError` (exit 11), and fixed picker grid off-by-one scroll bounds. Verification: [LIVE_VERIFICATION_v0.32.1](LIVE_VERIFICATION_v0.32.1.md).

</details>

<details><summary>v0.32.0 — in-project asset i2v frame selection</summary>

**v0.32.0 — alpha.** **In-project asset i2v frame selection by UUID (#287, #288).** Select existing assets for video initial/end frames by UUID in place without re-uploading, and add fail-fast for duration settings control presence. Verification: [LIVE_VERIFICATION_v0.32.0](LIVE_VERIFICATION_v0.32.0.md).

</details>

<details><summary>v0.31.0 — wrong-media attribution defenses</summary>

**v0.31.0 — alpha.** **Wrong-media attribution defenses and multi-ref picker scrolling (#281, #282).** Added pre-download verification guards against ambiguous agentic-cohort image downloads (`MediaAttributionError` exit 26), and added viewport-scrolling fallback to resolve multiple sequential `--ref` selections in the virtualized picker grid. Verification: [LIVE_VERIFICATION_v0.31.0](LIVE_VERIFICATION_v0.31.0.md).

</details>

<details><summary>v0.30.0 — agentic-cohort image path support</summary>

**v0.30.0 — alpha.** **Agentic-cohort image path support and MCP video parameters (#258).** Supported native 768x1376 still generations in the agentic cohort, added character-creation integrity guards, and mapped model/duration/count video parameters on the MCP server. Verification: [LIVE_VERIFICATION_v0.30.0](LIVE_VERIFICATION_v0.30.0.md).

</details>

<details><summary>v0.29.0 — persistent gflow instructions CRUD</summary>

**v0.29.0 — alpha.** Persistent `gflow instructions` CRUD — see [LIVE_VERIFICATION_v0.29.0.md](LIVE_VERIFICATION_v0.29.0.md) and the [CHANGELOG](../CHANGELOG.md) entry.

</details>

<details><summary>v0.28.0 — agent instructions (-i) steer agentic generation</summary>

**v0.28.0 — alpha.** **Agent instructions (`-i` / `--instruction`) now actually steer agentic
image generation (PR #263).** Instruction cards sync to the project's Agent brief via
`PATCH …/agentInfo` and the agent folds every enabled card into generation. Root causes fixed:
conversational (not imperative) composer directive + the `project_brief.enabled` master switch.
Verification: [LIVE_VERIFICATION_v0.28.0](LIVE_VERIFICATION_v0.28.0.md) (crayon e2e GREEN).

</details>

<details><summary>v0.27.1 — v0.27.0 follow-up fixes + documentation sync</summary>

**v0.27.1 — alpha.** **v0.27.0 release follow-up fixes and documentation sync (#239).** Patch release wiring package version dynamically to `build_handoff()` and `FastMCP` server, escaping brackets in Rich console planning output, updating MCP agent guide, and adding `gflow movie` usage documentation. Verification: [LIVE_VERIFICATION_v0.27.1](LIVE_VERIFICATION_v0.27.1.md) (credit-free baseline verification).

</details>

<details><summary>v0.27.0 — Global [style] block with named variants + prompt-aware resume for gflow movie</summary>

**v0.27.0 — alpha.** **Global `[style]` block with named variants + prompt-aware resume
for `gflow movie` (#239).** A `movie.toml` can now express a visual style system once —
`prefix`/`suffix` on `[style]` plus `[style.variants.*]` sub-tables — and select it
per-scene via `style_variant` / `style_suffix` (deterministic composition, `none`
reserved as the opt-out keyword). The handoff manifest records `style_applied`
(variant/prefix/suffix/scene_suffix) per clip. Resume is now prompt-aware: completed
scenes persist a `style_hash`; a scene whose composed prompt changed is regenerated
instead of silently skipped, and dry-run marks it `re-run (style changed)`. Carries
forward v0.26.0 (i2i select-in-place by UUID). Verification:
[LIVE_VERIFICATION_v0.27.0](LIVE_VERIFICATION_v0.27.0.md) (credit-free CLI ledger).

</details>

<details><summary>v0.26.0 — image i2i references a generated image by UUID (select in place)</summary>

**v0.26.0 — alpha.** **Reference a generated image in `image i2i` by its Flow UUID.**
A `reference_images` entry that is a media UUID is attached by **selecting the
already-existing asset in Flow's reference picker** (located by UUID in the thumbnail
URL, surfaced by display-name search when hidden) — no duplicate upload; local upload
remains the fallback. Generated images also record their Flow `display_name` (credited
@C1ph3r404). Verification: [LIVE_VERIFICATION_v0.26.0](LIVE_VERIFICATION_v0.26.0.md)
(live e2e GREEN).

</details>

<details><summary>v0.25.0 — remote-UUID i2v + silent-failure guards</summary>

**v0.25.0 — alpha.** **`video i2v` from a generated image's UUID proven live (#237)** —
the picker-search attach was reworked to a local-upload path, producing a real 8s
interpolation from a catalogued UUID. Home-`.env` config matrix (#240) verified live.
Two silent failures made loud: video-as-image download rejection and rejected-upload
fail-fast. Verification: [LIVE_VERIFICATION_v0.25.0](LIVE_VERIFICATION_v0.25.0.md).

</details>

<details><summary>v0.24.0 — `--project` parity across CLI + MCP</summary>

**v0.24.0 — alpha.** **`--project` parity across CLI + MCP.** The video commands
(`video t2v`/`i2v`/`r2v`) gain `--project <id>` to generate into an existing Flow project
instead of a scratch one (#233/#234), matching `image t2i`/`i2i`; and the MCP
`gflow_generate_image` / `gflow_generate_video` tools gain a matching `project` parameter
(#235), so agent callers get the same capability. Both surface an already-wired worker
capability (`payload["project_id"]`) and validate the id identically. Carries forward
v0.23.0 (MCP generation live + macOS 401 fix). Verification:
[LIVE_VERIFICATION_v0.24.0](LIVE_VERIFICATION_v0.24.0.md).

</details>

<details><summary>v0.23.0 — MCP generation live + macOS 401 fixed</summary>

**v0.23.0 — alpha.** **MCP generation goes live + macOS 401 fixed.** The MCP server's
`gflow_generate_image` / `gflow_generate_video` tools — previously non-functional stubs —
are now wired end-to-end to the FlowWorker queue (background worker owns download +
history recording), the `tools` prompt-expansion parameter is actually applied, and i2v/r2v
require their frame/reference inputs at the tool boundary. The long-standing macOS
generation `401` (#222) is resolved (#230, @gunalak): Flow cookies are read from the full
jar by domain instead of a path-`/` filter that dropped the `/fx`-scoped session token, and
the headed context is seeded from a pre-launch snapshot when macOS can't decrypt the store.
Carries forward v0.22.0 (Tools framework) + v0.21.0 (MCP server). Verification:
[LIVE_VERIFICATION_v0.23.0](LIVE_VERIFICATION_v0.23.0.md) (MCP wiring proven live; #222
reporter-verified e2e on macOS).

</details>

<details><summary>v0.22.0 — Tools framework ("Creative Director")</summary>

**v0.22.0 — alpha.** **Tools framework ("Creative Director").** A TOML-defined prompt-tool system: `creative-director` rewrites a terse prompt into a vivid one via Google's five-component formula (public Gemini API, never-fatal), with 15 category-gated domain styles and deterministic banned-keyword stripping. Invoke it via the new `gflow tools list/show/run` group or the uniform `-t`/`--tool` option on every generation command (`image t2i`/`i2i`/`batch`, `video t2v`/`i2v`/`r2v`/`chain`), replacing the never-released `-e/--expand`. History records the original prompt, the submitted `expanded_prompt`, and `metadata_json.tool` provenance (redaction-honoring). **"My Tools"**: user-authored TOMLs in `<GFLOW_CLI_HOME>/tools/*.toml` load automatically. MCP parity via `gflow_list_tools` + a `tools` array param; the legacy `expand_prompt` MCP prompt is deprecated. The Gemini expander gained an overall wall-clock budget. Carries forward v0.21.0 (MCP server over stdio + HTTP/SSE). Verification: [LIVE_VERIFICATION_v0.22.0](LIVE_VERIFICATION_v0.22.0.md) (CI/automated complete; live owner-run pending).

</details>

</details>

## Milestone history

| Milestone | Status |
|---|---|
| Read-only credit balance in the CLI and MCP (`gflow credits user` / `list`, `gflow_get_credits`) over a browser-free HTTP path; image-to-video from a local start frame on the migrated `flow.google.com` host (#639 slice 1) | ✅ done (v0.69.0) |
| `gflow update` self-update through the installing manager (uv tool / pipx / pip), venv-verified outcome; the update notice as a stderr banner; CONTRIBUTING routes contributors and agents through the AGENTS.md lifecycle | ✅ done (v0.68.0) |
| Flow's migrated `flow.google.com` host driven for text-to-video; the default host for what it can serve (`GFLOW_CLI_FLOW_HOST`) | ✅ done (v0.67.0) |
| Migrated-origin runs fail fast and keep their learned locale (v0.66.1's fast-fail never fired in the field; corrected in v0.66.2) | ✅ done (v0.66.2) |
| Flow `flow.google.com` migration named as its own failure class (exit 36; retryable in v0.66.0, non-retryable since 2026-09-04) | ✅ done (v0.66.0) |
| Repo scaffold, CI, license, README, disclaimer | ✅ done |
| Auth login flow (one-time browser capture) | ✅ done |
| Video: `t2v` / `i2v` / `batch` (Veo 3.1) | ✅ done (v0.2.0a1) |
| Image generation (T2I/I2I, 1–4 per call, 5 ratios, 3 models) | ✅ done (v0.3.0a1) |
| End-to-end smoke test against live Flow | ✅ done |
| First public alpha release on PyPI | ✅ done (v0.2.0a1) |
| Batch concurrency / per-worker Page pool (`GFLOW_CLI_CONCURRENCY=N`) | ✅ done (v0.4.0a2) |
| Typed errors (RFC 9457 Problem Details) + per-class exit codes 3–7 | ✅ done (v0.4.0a2) |
| Retry / backoff + reCAPTCHA re-mint inside the retry loop | ✅ done (v0.4.0a2) |
| Structured logs (`structlog`, JSON on pipe) | ✅ done (v0.4.0a2) |
| Pluggable image transport + `ui_automation` default strategy | ✅ done (v0.5.0a1) |
| `gflow run --config <file>` sequential JSON batches | ✅ done (v0.5.0a1) |
| `examples/` directory with runnable single-image + batch scripts | ✅ done (v0.5.0a1) |
| Shell multi-prompt `gflow image t2i` (`PROMPT...`, `--prompts-file`, `--stdin`) | ✅ done (v0.6.0a1) |
| Downstream-worker ergonomics (`out_dir`, `health_check()`, optional `project_id`, `BrowserSessionClosedError`) | ✅ done (v0.7.0) |
| Signed-tag release verification + first stable (`v0.7.0`) | ✅ done (v0.7.0) |
| `gflow video t2v` restored on `ui_automation` with first-class video download | ✅ done (v0.7.0 unreleased → v0.8.0) |
| Image/video mode-switch symmetry + live verify on ffroliva (PR #40) | ✅ done (v0.8.0) |
| README + AGENTS.md + llms.txt refresh, docs governance | ✅ done (v0.8.1) |
| `gflow video t2v` model picker (5 Veo models) + `--duration` / `--count` | ✅ done (v0.9.1) |
| `gflow video i2v` (start + optional end frame) on `ui_automation` | ✅ done (v0.9.1) |
| `gflow video r2v` (reference-to-video, model-aware ref cap omni≤7 / veo≤3) | ✅ done (v0.9.1) |
| `gflow image t2i/i2i --model` actually selects the model (was a no-op) | ✅ done (v0.9.0) |
| Local SQLite catalog (data layer) recording every project / image / video / operation | ✅ done (v0.9.0) |
| `gflow data list {projects,images,videos,profiles}` read CLI over the catalog | ✅ done (v0.9.0) |
| `ROADMAP.md` published (themed milestones through v1.0) | ✅ done (v0.9.0) |
| Locale-agnostic media-dialog upload selectors (fixes non-English Chrome profiles) | ✅ done (v0.9.0) |
| Wheel-build fix (removed redundant `force-include` causing duplicate ZIP entries) | ✅ done (v0.9.0 hotfix, PR #74) |
| `--json` machine-readable output across `image t2i/i2i`, `video t2v/i2v/r2v`, `auth list` + `gflow models` catalog | ✅ done (v0.10.0) |
| Per-model reference-image caps for `i2i` / `r2v` (Veo 3.1 Quality rejects R2V) | ✅ done (v0.10.0) |
| Google-account identity persisted per profile + auto-rename of first-run `default` (issue #92) | ✅ done (v0.10.0) |
| External cloud storage (S3 / MinIO / GCS) via `GFLOW_CLI_STORAGE_URI` | ✅ done (v0.10.0) |
| `gflow data prune` + aggregated asset listing (`--all-copies`) + cross-profile count fixes (#111, #113) | ✅ done (v0.10.0) |
| Layered cost-stratified e2e test strategy (`e2e_auth`/`e2e_image`/`e2e_video`/`e2e_batch`/`e2e_data`/`smoke`) | ✅ done (v0.10.0) |
| `gflow video i2v` routes to the Veo i2v endpoint (no silent T2V fallback) + `veo-lite` default (issue #125) | ✅ done (v0.11.0) |
| Create-project generation works under Flow's "Agent" composer mode | ✅ done (v0.11.0) |
| Image-model selection hardened for non-English Flow UIs (selector cascade, #94) | ✅ done (v0.11.0) |
| `gflow character rm` — free character deletion (#150) | ✅ done (v0.13.0) |
| Align I2V CLI flags with Flow UI Labels (`--initial-frame`) (#122) | ✅ done (v0.13.0) |
| In-project governance (ruff T20, materiality Classifier) | ✅ done (v0.13.0) |
| `gflow movie` — multi-scene, character-consistent video from a TOML manifest (entity reuse, resumable, handoff manifest) | ✅ done (v0.14.0) |
| `gflow image t2i/i2i` — reference locked CHARACTER entities (`--reference-entity`) + `--project` for character-consistent stills | ✅ done (v0.15.0) |
| `gflow character` — reusable Flow Character entities (`create`/`list`/`show`/`voices`), persist-before-spend saga (#145) | ✅ done (v0.12.0) |
| `gflow scene` — Add Clip / Scenes compose + credit-free server-side extended video (`runVideoFxConcatenation`) | ✅ done (v0.12.0) |
| `gflow video chain` — last-frame I2V chaining from a JSONL manifest (`--dry-run`/`--max-links`/`--resume-from`) | ✅ done (v0.12.0) |
| `gflow video extend` — chained server-side Veo continuations past the 8s ceiling (tier-resolved model, whole-run balance pre-flight, resumable) | ✅ done (v0.63.0) |
| `i2v --model omni-flash --end-frame` — first+last interpolation on Omni 1.1 Flash; static capability table replaced by a post-submit route check that fails a dropped end frame (#626) | ✅ done (v0.64.0) |
| Create-project generation works under Flow's Agent docked chat panel | ✅ done (v0.12.0) |
| Video status poll raises `AuthExpiredError` (exit 3) on mid-workflow 401 (#156) + Docker `/dev/shm` hardening | ✅ done (v0.15.1) |
| Locale-free resource-picker include selectors — entity attach works on every account language (#170) | ✅ done (v0.16.0) |
| `gflow image upscale <mediaId> --scale 2k\|4k` — credit-free download-menu upscale, 4K Ultra-gated (#171) | ✅ done (v0.16.0) |
| Cookie-store session verification fast path (`verify_flow_profile`, PR #168) + Playwright fallback | ✅ done (v0.17.0) |
| Entity-attach exit-7 remediation hint + `entity_attach_context` drift telemetry (#174 interim) | ✅ done (v0.17.0) |
| Agentic-UI exit-23 `UiSelectorDriftError` + `out_dir` wiring (#183) | ✅ done (v0.18.0) |
| Patchright opt-in browser engine (`GFLOW_CLI_BROWSER_ENGINE=patchright`) | ✅ done (v0.19.0) |
| Aspect-ratio overrides under Agentic & Classic cohorts + `GFLOW_CLI_PREFER_CLASSIC` (#193) | ✅ done (v0.20.0 / v0.20.1) |
| MCP server (`gflow mcp run` stdio + `gflow serve` HTTP/SSE) + daemon/queue scaffolding | ✅ done (v0.21.0) |
| Tools framework: `gflow tools` group + `--tool` + `creative-director` + "My Tools" + MCP parity | ✅ done (v0.22.0) |
| MCP generation wired to FlowWorker (tool→queue→download→record) + `tools` applied + i2v/r2v boundary validation | ✅ done (v0.23.0) |
| macOS generation 401 fixed — `/fx` cookie-path read + headed-context seed (#222/#230) | ✅ done (v0.23.0) |
| `--project <id>` on `video t2v/i2v/r2v` + MCP `project` parameter (#233/#234/#235) | ✅ done (v0.24.0) |
| `video i2v` from a generated image's UUID (#237) + home-`.env` matrix (#240) + silent-failure guards | ✅ done (v0.25.0) |
| `image i2i` references a generated image by UUID — select in place, no duplicate upload + `display_name` capture | ✅ done (v0.26.0) |
| `movie.toml` `[style]` block with named variants + prompt-aware resume (`style_hash`) (#239) | ✅ done (v0.27.0) |
| Agent instructions (`-i`/`--instruction`) steer agentic generation — conversational directive + brief master switch (PR #263) | ✅ done (v0.28.0) |
| Persistent `gflow instructions` CRUD + `movie.toml` instructions brief-sync + `gflow_instructions_*` MCP tools + CI-enforced MCP↔CLI parity (#192) | ✅ done (v0.29.0) |
| Diagnostic tooling (`GFLOW_CLI_HAR_PATH` + `GFLOW_CLI_DEBUG_TRACEBACK`) + reference-entity-smuggling fix (#312/#316) | ✅ done (v0.36.0) |
| Viewports 1920×1080 + agentic count enforcement + FIPS-safe SAPISIDHASH (#313/#315/#329) | ✅ done (v0.37.0) |
| Robust `aria-pressed` agentic↔classic mode control (#332) + i2i ref dedup via picker filename search (#314) | ✅ done (v0.38.0) |
| Agentic-pin recovery: opt-in reload after a real Agent-toggle click (#338) | ✅ done (v0.38.1) |
| Failed generations persisted to the local catalog + `gflow data list errors` (#341) | ✅ done (v0.39.0) |
| Prompt `@`-mention resolution for asset tagging (#344) | ✅ done (v0.40.0) |
| Production-readiness hardening: queue safety, profile lease, cancellation-safe teardown (#357) | ✅ done (v0.41.0) |
| Content-safety `ContentPolicyError` classification + Antigravity coding agent (#359/#360/#361) | ✅ done (v0.42.0) |
| Private incident diagnostics (`GFLOW_CLI_INCIDENT_CAPTURE`) | ✅ done (v0.43.0) |
| Dual-side project naming & management (`gflow project` family, `--project-name`/`--project-title`) (#381) | ✅ done (v0.44.0) |
| `--ref` catalog-backed upload fallback + character-entity binding fixes (#393/#395) | ✅ done (v0.45.0) |
| Prompt tools on any OpenAI-compatible endpoint (`GFLOW_CLI_LLM_*`) (#387) | ✅ done (v0.46.0) |
| Classic count-setter: digit-keyed tab selection + typed drift error (#404) | ✅ done (v0.46.1) |
| Predictable output paths: `-o`/`--output` on `t2i`/`i2i`/`t2v`/`i2v` (#411; MCP + cloud follow-ups #414/#415) | ✅ done (v0.48.0) |
| Manifest-driven video batch runner on `ui_automation` | ❌ removed — never worked end-to-end, shipped as a nonfunctional stub; see v0.41.0 changelog. For multi-clip video, loop `gflow video t2v`/`i2v` from the shell; `gflow image batch` remains supported for images. |
| Persistence layer (stay-mounted batch sessions across project boundaries) | ⏳ Phase B |
| Provider abstraction for official Veo 3.1 API | ⏳ planned |
| Signed-tag CI verification automation (no manual signing in CI yet) | ⏳ planned |

## What's new in each release

For per-release deltas see [CHANGELOG.md](../CHANGELOG.md). Per-release evidence files (live verification, screenshots, smoke logs) live under `docs/LIVE_VERIFICATION_*.md`.

## Lifecycle policy

- **Alpha (`0.x.y`)** — current. APIs may change between minor versions; breaking changes are noted in the changelog.
- **`1.0.0`** — stable surface. Breaking changes require MAJOR bump + migration notes.
- **Patch releases** — bug fixes, doc refreshes (like v0.8.1), and other backward-compatible changes.

See [RELEASE.md](../RELEASE.md) for the full release protocol and the prerelease vs full-release policy.
