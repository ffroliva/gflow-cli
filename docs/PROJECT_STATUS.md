# Project Status

> Where gflow-cli is in its lifecycle, by release. Updated on every signed tag.

## Current release

**v0.83.2 — alpha.** Three fixes on flow.google.com: non-English UIs, image upscale, and
`auth status` on hosts without Chrome.

**`--duration` works in a non-English Flow UI (#963).** The duration radio was matched on
its English label (`8s`), so a Vietnamese pane (`8 giây`) stopped every `--duration` run
at exit 11, before any credit was spent. The radio is now matched on its number, measured
identical across locales; AGENTS.md records this as the second text-selector exception.
Live-checking it under a Vietnamese UI also found a **notice-only cookie bar** (no reject
button) that blocked every run at exit 23; it is now acknowledged when it has no reject.

**`gflow image upscale` no longer fails with *Element is not attached to the DOM* (#957).**
The tile and download button are Playwright locators, re-resolved on every action.

**`gflow auth status` no longer reports a migrated account as signed out on hosts without
Google Chrome.** The probe opens the profile with the same channel generation uses.

**Not verified here:** a host without Google Chrome; video upscale, which shares the fixed
helper but whose live run hit a disabled 1080p option (#967); accounts served labs. Full ledger:
[LIVE_VERIFICATION_v0.83.2](LIVE_VERIFICATION_v0.83.2.md).

<details><summary>v0.83.1 — the default image model works on flow.google.com again</summary>

**v0.83.1 — alpha.** The default image model works on flow.google.com again.

**`nano2` runs Nano Banana 2.1 on flow.google.com (#958).** Flow replaced "Nano Banana 2"
with "Nano Banana 2.1" in that host's image menu, and the submit now carries `BELUGA`
where it carried `NARWHAL`. gflow's submit guard refused every `nano2` run (the default
model) with exit 7. `nano2` now accepts `BELUGA`; every other model still needs its own
token. Measured on two accounts: 2.1 replaced 2 rather than appearing beside it. Found and
first fixed by @omid-io.

**Not verified here:** accounts served labs. Full ledger:
[LIVE_VERIFICATION_v0.83.1](LIVE_VERIFICATION_v0.83.1.md).

</details>

<details><summary>v0.83.0 — video downloads on flow.google.com, upscaling ported</summary>

**v0.83.0 — alpha.** Video runs on flow.google.com download again, and upscaling is
ported there.

**Billed video runs no longer fail on flow.google.com (#948).** Since about 2026-10-05,
Flow sends `null` in the generation record's fourth slot, where it used to send `"CAE"`.
gflow found the record by that marker. As a result, video submits (reported on t2v, i2v and
r2v, reproduced on t2v) were accepted and billed, then exited 7 and never downloaded. A record is now matched by its
three ids and its details block. A run now selects its own record from a reply that lists
several. `gflow data download` reads only the clip route's signed `as29s` URL. If a record
is ever missing again, the error says the run may already be billed instead of suggesting
a retry.

**Upscaling on flow.google.com (#922).** `gflow image upscale` (2K) works there, a new
`gflow video upscale` exports 1080p, 720p or a 270p GIF, and both have MCP twins
(`gflow_upscale_image`, `gflow_upscale_video`). A 1080p export measured 0 credits in one
observation. The download menu items are anchored on their resolution token, measured
identical across locales. AGENTS.md records this as the one exception to the
text-selector rule.

**Not verified here:** 4K upscale (the button is disabled on the account used); accounts
served labs. Full ledger: [LIVE_VERIFICATION_v0.83.0](LIVE_VERIFICATION_v0.83.0.md).

</details>

<details><summary>v0.82.1 — a mint failure, a busy catalog and a missing workflow id stop misreporting</summary>

**v0.82.1 — alpha.** Three fixes for failures that misreported what happened.

**A reCAPTCHA mint failure is a typed error (#915).** It was a bare `RuntimeError`: exit 1
with no remediation, a hashed "Unknown Error" over MCP, and one failure ended a
multi-prompt run past `--continue-on-error`. It now has its own `type`
(`…/errors/recaptcha-mint`) and a `retryable` flag measured live: a mint that lost a race
with a navigation, or ran before Flow injected its script, is retryable; a mint on a
non-web page (`about:blank`) is not. Still exit 1 — branch on the `type`.

**A busy catalog no longer fails a successful generation (#900).** A write blocked past the
5 s lock timeout raised a raw sqlite error that missed every "recording failed after
success" handler, so `gflow video` exited 1 for a clip that existed and was paid for.
Catalog transactions now raise `DataStoreError`: the run succeeds with a warning.

**A video's Flow workflow id is recorded (#898).** It was `NULL` for every clip, so the MCP
task result's `flow_workflow_id` was always `null` for a video. A repeated start for a
known media id is now a no-op instead of a crash.

**Not verified here:** accounts served labs (labs answers 308 on the three profiles here
that hold a live Flow session). Full ledger:
[LIVE_VERIFICATION_v0.82.1](LIVE_VERIFICATION_v0.82.1.md).

</details>

<details><summary>v0.82.0 — a run config builds a series from one image</summary>

**v0.82.0 — alpha.** A `gflow run` config can build a series from one image, and every
generation it makes is now tracked.

**`"ref": "batch:N"` (#913).** A row can generate from an earlier row's image. The image is
already in the run's Flow project, so it is referenced where it is, by the handle Flow
returned: nothing is re-uploaded. On flow.google.com the image is chosen by identity (its
thumbnail token in the `@` picker equals its grid tile's), because captions repeat. The
editor is reloaded until a just-generated image is listed, because the grid and the picker
are per-load snapshots. A submit that does not carry the reference is aborted before Flow
acts. Rows keep file order; a failed parent skips its dependents with the reason.

**Local-file references (#913).** A row's `ref` can be an image file, resolved against the
config's folder and checked before the browser starts. It is uploaded once per run and
referenced in place by every row that names it.

**Tracking.** `gflow run` and multi-prompt `gflow image t2i` now record their successful
generations (they recorded failures only); a referencing row is recorded as image-to-image
with its parent as input.

**Fixed:** manifest references were parsed and silently ignored since v0.52.0, whose
verification record claimed otherwise (corrected); the docs no longer suggest clearing a
sign-in screen in the generation window (#925).

**Not verified here:** accounts served labs (labs answers 308 on the three profiles here that hold a live Flow session). Full
ledger: [LIVE_VERIFICATION_v0.82.0](LIVE_VERIFICATION_v0.82.0.md).

</details>

<details><summary>v0.81.0 — the generation browser opens off-screen</summary>

**v0.81.0 — alpha.** The generation browser stops covering your desktop, a denied profile
directory stops looking like another gflow holding it, and one dependency CVE locked out.

**The generation browser opens off-screen (#923).** Generation needs a real headed Chrome,
so every `image`/`video` run used to put a window over whatever you were doing. It now
opens at `-30000,-30000`, still fully headed, so Flow and reCAPTCHA see the same browser.
`GFLOW_CLI_BROWSER_WINDOW_POSITION` takes any `X,Y` to watch or debug a run; empty
restores Chrome's placement. (A consent or account-chooser screen is cleared through
`gflow auth login`, whose window is always visible; see #925.) Measured before adopting it: image and
video generation unchanged, no timer or animation throttling off-screen. The contributor's
`--no-focus-on-init` measured as a no-op on Windows and was dropped, so the docs say
plainly that Chrome can still take keyboard focus at launch. Thanks to @johngbl.

**A write-denied profile gets its own error (#919).** Chrome reports a profile it cannot
write with the same `ProcessSingleton` failure as one another process holds. gflow now
tells the two apart and raises `ProfileAccessError` (exit 11) with a permissions remedy
instead of a lock-contention one. Covered at the generation client's launch only. Thanks to
@L1meSn0w.

**Security:** `urllib3` 2.8.0 (CVE-2026-97687, CVE-2026-97688, CVE-2026-97689), transitive.

**Not verified here:** macOS and Linux for the off-screen window, and polls longer than
about five minutes. Full ledger: [LIVE_VERIFICATION_v0.81.0](LIVE_VERIFICATION_v0.81.0.md).

</details>

<details><summary>v0.80.0 — --resolution, nano2-lite, unported forms refused by name</summary>

**v0.80.0 — alpha.** Two new controls, four fixes, and two dependency CVEs locked out.

**`--resolution 360p|720p` and `--model nano2-lite` (#787).** Video models that render a
resolution row (Omni Flash) take an explicit resolution instead of Flow's default, on the
CLI and through `gflow_generate_video`. Nano Banana 2 Lite joins the image models. Both
were verified live without spending Veo credits: the resolution check drove the real
settings pane and stopped before the submit, on the CLI and the MCP twin.

**An unported image form is refused by name, on a fresh or a reused client (#891).** The
browser transport pre-minted a reCAPTCHA token it never read. After a successful image the
page is parked on `about:blank`, where that mint raised `RecaptchaError`. So
`gflow run --config` with a per-prompt `imagen4` after a working prompt crashed the whole
run with exit 1 and lost its results table. The dead mint is gone. The composer now
refuses the form with exit 36 and names it ("the IMAGEN_3_5 model is not ported yet"),
and the run completes. Measured before and after on both paths.

**A lost transfer is recorded as a generated clip (#896).** When the signed-media
connection drops on every retry after Flow reported the clip done, the catalog marked it
`pending` forever. It now records it as generated, with `error_type=media-download`, and
`gflow data list videos` shows each clip's `STATUS`.

**A refusal is reported as the refusal (#909), and `auth login` stops claiming success on
the `/about` identity re-check (#902).** flow.google.com states its refusal on the wire
(`PUBLIC_ERROR_UNUSUAL_ACTIVITY`), and gflow now reads it instead of timing out.

**Security:** `oauthlib` 4.0.0 (CVE-2026-49265) and `pyjwt` 2.15.1 (CVE-2026-102274), both
transitive.

**Not verified here:** the rendered resolution (it needs a billed clip; selection is
proven), the `/about` re-check arm (no profile is in that state), and accounts served
labs (labs answers 308 on every profile here). Full ledger:
[LIVE_VERIFICATION_v0.80.0](LIVE_VERIFICATION_v0.80.0.md).

</details>

<details><summary>v0.79.1 — a billed clip survives a dropped download</summary>

**v0.79.1 — alpha.** A finished, billed clip is no longer thrown away when its download
hits a transient connection reset — and when the transfer truly cannot complete, you are
told the clip exists and how to get it, instead of `Unexpected error`.

**The signed-media download survives a dropped connection (#895).** The transfer was issued
once, with no retry: a single `ECONNRESET` mid-stream failed the whole command *after* Veo
had produced the clip and charged for it — reported at 2 failures in 6 consecutive runs. It
now retries using Playwright's own `max_retries`, which matches on the driver's
`ECONNRESET` and never on an HTTP status, and whose backoff is charged to the same timeout
rather than multiplying it. That last property is what makes it safe here: three attempts
stay inside one ~180 s budget instead of stretching to nine minutes on a lock shared with
every other generation on the client. Measured with an A/B control, not read from the docs.
Fixed at **all three** download sites, not just the one the traceback named — including
`gflow data download`'s own transfer, so the recovery path cannot be stranded by the fault
it exists to rescue.

**A lost transfer now says the clip survived.** `playwright.async_api.Error` is not a gflow
error class, so it escaped the typed-error contract and rendered as *"Unexpected error …
exit code 1, retryable: False"* — of which the last two are wrong and the first is useless.
It is now `NetworkError` (exit 6, correctly retryable) naming the media id and the free
recovery command, so nobody re-generates a clip they already own. The Playwright message is
deliberately **not** forwarded into `detail`: it embeds the driver's call log including the
request URL, and `detail` reaches stderr and `--json` stdout without passing through
redaction.

**Both MCP doors get the same repair.** `gflow_download_media` returned *"Unexpected Error;
details were logged server-side"* with no remediation field at all, and the queued
`gflow_generate_video` path returned `detail: "sha256:<hash>"` with neither
`remediation_hint` nor `retryable`. Typing the error fixes both. The failed queue row also
keeps its `flow_media_id` now — without it an agent's only copy of the id was a UUID buried
in an English sentence suggesting a shell command an MCP client cannot run.

**An expired signed link stops blaming your prompt.** A late GET answers 4xx, and every one
of those branches fell through to `WireFormatError`'s class default — *"retry with a simpler
prompt text"* — on paths carrying no prompt and no payload. Same wrong-advice class removed
in #875.

**Not verified here:** the composer download's own call site needs a **billed** generation to
reach, and `gflow credits` returns 401 on this account (the #795 condition), so the balance
is unreadable and none was spent. The shared helper it calls *is* live-verified through
`gflow data download` at zero credits, and the retry itself is measured against a real TCP
RST with a control. The reporter's fault was **not** reproduced locally — 20/20 clean here
against their 2-in-6, so it looks environment-specific. Recorded as blockers, not passes.
Full ledger: [LIVE_VERIFICATION_v0.79.1](LIVE_VERIFICATION_v0.79.1.md).

</details>

<details><summary>v0.79.0 — recovering a billed clip whose download failed</summary>

**v0.79.0 — alpha.** Two error paths stop asserting things nothing measured, and a billed
asset that never downloaded can be fetched back for free.

**`gflow data download <media_id>` recovers a stranded generation (#865, #871).** A run that
finishes and bills, but whose signed URL is never observed, used to exit 7 with the credit
spent, the clip sitting in the Flow project, and `local_path: null` in the catalog — and the
only recovery on offer was to generate it again and pay twice. The command opens the clip's
own route, takes the signed URL Flow reports, verifies the bytes against the size Flow
records, and writes both the file and the `local_files` row. Costs nothing. Mirrored as
`gflow_download_media`. **Video only, and it says so:** the signed URL comes from the `as29s`
record a clip route emits, which an image's route does not carry. An image id is refused
immediately with exit 11 instead of opening a browser and timing out after 45 s on three
wrong guesses (#877).

**Auth failures on aisandbox routes stop blaming a cookie they never read (#803).** The
default remediation said *"SAPISID cookie missing, expired, or unreadable — re-run `gflow
auth login`"*. v0.74.0 corrected the two `gflow credits` sites; the other three inherited the
default, so `createScene`, `commitWorkflow`, `createEntity`, `projectInitialData`,
`upsampleImage` and every other route through the shared retry helper still said it. None of
them reads SAPISID — the credential is a Bearer token minted by labs' session endpoint, and
SAPISID is what made that endpoint answer at all, so by the time these raise it has
demonstrably just worked. The advice was also expensive: on a profile with no
browser-strategy marker a failed re-login rolls the marker back and starts the #791 loop.
Each site now names what was actually refused, and the 401 site names the route.

**The migrated host stops reporting a model it never observed (#789).** `model_name_type`
echoed the requested `--model` back on `flow.google.com`, where the `ogiZ0b` reply carries no
model field at all. `recorder.py` persists it as `AssetRecord.model`, so `gflow data` read
the echo back as though Flow had confirmed it — and with a hidden model picker (#788) the
model actually selected can differ from the one requested, which makes the echo wrong on the
one field that would reveal it. It is `null` on that host now. The request echo is unchanged:
the envelope still reports what you asked for.

**CI runs e2e tests for the first time.** Five `tests/e2e/` files are hermetic — every Flow
origin served by `page.route().fulfill()`, so no account, profile, network or credits, only
the Chromium the test job already installs. They are the regression tests for #593, #773,
#859 and #860, and because `addopts` excludes `-m e2e` and no job overrode it they ran
nowhere: a regression of any of those four would have gone green. 27 tests, ~4 min, behind a
count guard so an empty run cannot pass as a green one.

**Not verified here:** #803's corrected remediations are offline-tested only. Reaching them
live needs Flow to answer an aisandbox route with a non-JSON body or a token-less session,
and no profile available reaches that state — the labs tRPC route now refuses first with a
404 (which is its own misleading-error bug, #875). Recorded as a blocker, not a pass. Full
ledger: [LIVE_VERIFICATION_v0.79.0](LIVE_VERIFICATION_v0.79.0.md).

</details>

<details><summary>v0.78.0 — generating without a project on flow.google.com</summary>

**v0.78.0 — alpha.** Generating without a project works again on accounts Flow serves from
`flow.google.com`, and the error that used to appear there is gone.

**labs.google's project route is retired, and gflow was blaming the user's login (#864,
closes #561).** Every path that omits a project — `gflow image t2i`/`i2i`, `video
t2v`/`i2v`/`r2v`, `gflow project create`/`rename`, and the MCP twins — began by creating a
scratch project through `labs.google/fx/api/trpc/project.createProject`. Google has disabled
that route: it answers **404 "Flow RPCs have been deprecated and disabled. Flow has migrated
to https://flow.google.com."** for a session holding a labs token, and **401** for one
without. Measured 12/12 across four profiles. gflow rendered the 401 as *"Authentication
expired — run `gflow auth login`"*, advice no login could act on, and #561 sat open for a
month with a contributor's 401-then-200 measurement that no host-based theory could explain.

gflow now creates the project **on flow.google.com** when the labs route refuses — keyed on
that observed 401/404, never on which host an account is served, so the labs arm and
`GFLOW_CLI_FLOW_HOST=labs.google` are untouched. Creation drives the projects page's `add`
button and reads the new id and title out of Flow's own `jHPbke` reply; rename drives the
project header and confirms the `o8DA4` reply echoes the title back. `generate_video` now
creates the project up front exactly as `generate_image` already did, so every video route is
decided with one in hand, and MCP `gflow_generate_video` honours `project_name`, which it had
accepted and silently dropped.

**An MCP server waits out profile contention instead of failing it.** Two calls on one profile
inside one server now queue rather than the second dying with `ProfileLockedError`, and a
profile held by another process is waited out for up to 180 s. Both halves came from
reviewing a contributor PR that had the right goal and two defects: the default was applied
after `gflow`'s root command had already cached settings (measured: env `180`, setting
`0.0`), and same-process contention fails fast by design, so no wait value could have helped
the case it was written for.

**Video from a start frame works again on migrated accounts (#860).** The Frames picker
renders a `mat-icon` ligature beside the file name, and a locator reads both nodes as one
string -- so gflow's anchored match could never hold and every `video i2v --initial-frame`
run on a `flow.google.com` account ended in exit 32, *"the frame picker lists no asset
named ..."*, while the error's own diagnostic listed that asset with `image` glued to the
front. Matching is now containment on the display name, which #792 already made
run-unique. Flow's promo modal is also dismissed before the first click rather than only
on load (#859).

**`gflow video i2v --end-frame` runs on flow.google.com (#639).** Start+end interpolation
was the last i2v form the migrated composer refused. Both frames are uploaded and bound,
and the submit body is asserted before Flow acts on it -- a frame that failed to bind is
now a refusal instead of a wrong generation you paid for. The interpolation model key is
cohort-dependent and matched by shape.

**`gflow docs` puts the documentation in the terminal (#861).** 126 pages lived in `docs/`
and nothing in the CLI pointed at any of them. `gflow docs` lists the topics, `gflow docs
<topic>` prints one as Markdown, and `gflow docs --search <term>` answers with
`docs/FILE.md:LINE` **and the line**. The pages ship inside the wheel, so it works with no
checkout and no network -- which is the whole point, since the reader who filed it
installed from PyPI. The wheel grows 0.74 MB -> 1.32 MB.

**`--aspect 3:4` images run on flow.google.com.** Flow's image settings render a fifth aspect
radio that the 2026-09-08 enumeration did not have, so gflow had been refusing 3:4 with exit
36. Re-measured; all five image aspects are driven. Video offers 16:9 and 9:16 only, which was
already covered.

Verified in `docs/LIVE_VERIFICATION_v0.78.0.md`. The project/aspect/MCP work was proven at
**zero credits** -- five e2e tests drive both surfaces with every video submit intercepted
before it reached Flow. **#860 was not**, and could not be: the only way to show that a
start frame now binds is to let a real generation run, so ten clips were generated and
downloaded on a live account. #859's dismissal path ran on every one of those runs, but no
promo modal appeared, so it is recorded as unobserved rather than verified.

**v0.77.1 — alpha.** A patch release: four fixes, and one of them made the package
unusable on a clean Windows install.

**A clean Windows install could not run a single command (#846).** `configure_logging`
renders TEXT logs with `structlog.dev.ConsoleRenderer(colors=True)`, whose Windows
`_init_terminal` raises `SystemError` outright when `colorama` is missing — and `colorama`
was never declared. The call sits in the Click *group* callback, so it fired before any
subcommand body: every interactive command aborted with a traceback that named structlog
and never gflow, and the README's Windows quick-start failed as written. Nothing in the
runtime closure supplied it; the only `colorama` edge in `uv.lock` came from **pytest**, a
dev dependency, so every developer machine and every CI job had it transitively and no gate
could see the gap. Reported from outside with the root cause already found. Reproduced on a
clean Python 3.13 venv, fixed by declaring the dependency, and verified by reinstalling into
that same venv — on both the CLI and the MCP stdio server.

**The sign-in window now closes on a migrated account (#849).** v0.77.0 listed this under
"not fixed here". The auto-close detector watched the `labs.google` session endpoint, which
for a migrated account never mints a session, so the one oracle it had could not answer: the
window sat open for the full 600 s while the banner promised gflow would close it. Worse, the
timeout propagated *past* verification, so a sign-in that had completed perfectly was
discarded unread and returned exit 12 for a login that worked — the first thing a new user on
a migrated account meets. gflow now also stops waiting when the jar carries both halves of a
migrated session, and a timeout reads the disk before failing. The authentication decision
stays with `verify_flow_profile`; no cookie was promoted to a proof of login.

**`gflow update` stops reporting the version of a half-replaced install (#848).** It now
verifies that the upgraded package imports in a fresh interpreter, rather than trusting the
metadata of a broken one.

**The migrated-host address scan was quadratic (#852).** `findall` retried from every start
position over a 1.28 MB response whose character class covers the whole URL-safe base64
alphabet: 40 000 chars took 12.07 s, synchronously, inside an `async def` — so a stall blocked
the event loop and cancellation could not land. Anchoring on the literal `@` and reading the
local part backwards over the RFC 5321 64-character cap makes the work proportional to the
number of `@` in the document. Same output for every address shape, Workspace and custom
domains included; 40 000 chars now scan in under a millisecond.

**Not fixed here:** `gflow credits` on migrated accounts (#795), and the agent-only composer
driver (#799, #824 open).

<details>
<summary>v0.77.0 — accounts Google moved to <code>flow.google.com</code> can sign in again</summary>

**v0.77.0 — alpha.** **Accounts Google moved to `flow.google.com` can sign in again — including
Google Workspace accounts, which an earlier attempt would have excluded without saying so.**

**The migrated-host login lockout is fixed (#791).** Verification used exactly one oracle,
`labs.google/fx/api/auth/session`, which answers `200 {}` forever for a migrated account — so a
perfectly usable workspace reported *"Signed in to Google, but not to the Flow app"* and gflow
refused every command. Not a degraded feature: a total lockout. When labs declines **and** the
profile carries a `flow.google.com` app-session cookie, gflow now confirms against the host that
actually serves the app. The cookie only *gates* that check; the decision is where the request
lands, because a revoked session is redirected off `myaccount.google.com` and that is
server-attested in a way page content is not. v0.76.0 recorded this as withdrawn; the approach
that shipped is a different one, and it was live-verified before merge.

**Google Workspace accounts are covered, and that was nearly missed.** The contributed revision
gated authentication on an `@gmail.com`-only pattern, which silently declined every custom
domain — this issue would have stayed open for them while appearing fixed. `docs/AUTHENTICATION.md`
documents Workspace SSO as supported. Verified live on a Workspace account: authenticated, correct
address, 2/2.

**One failed login no longer degrades the profile (#796).** That issue was filed as *"not yet
reproduced end-to-end — the chain is read from code"*. It is reproduced now: a failed verification
rolled the Chrome marker back, flipping `channel_for_profile` from `chrome` to `None` and silently
demoting generation to bundled Chromium on a profile created with `--browser chrome`. The rollback
is gated on `not verified`, so a verification that succeeds never fires it. Measured before and
after on one profile with untouched cookies: exit 8 with the marker gone, then exit 0 with it
intact.

**The release protocol stops disagreeing with itself (#839).** Three lists of "the version sites"
disagreed, one of them inside the gate that catches the disagreement. There are seven sites across
three gates and no list knew them all. The release skill now carries the one canonical table and a
test **parses** it rather than restating it — a constant would have been the fourth copy. This
release is the first cut under that gate, and it passed on the first attempt.

**A measurement that argued against its own issue (#836).** `wait_until="networkidle"` was
proposed for removal on the theory it burned a 45 s ceiling. Measured: it fires every time, 0/5
hit the ceiling — but costs 3422 ms against 1022 ms. Removing it turned the #580/#584 navigation
ratchet red, because it is what absorbs Flow's locale redirect. The issue read two call sites with
different constraints as one contradiction. Closed as invalid, with the probe and the numbers in
the tree.

**Not fixed here:** the login window still does not close by itself on a migrated account — the
auto-close detector polls the same labs endpoint that never answers, so it spins to the login
timeout while the banner promises otherwise (recorded on #791). Also unfixed: `gflow credits` on
migrated accounts (#795), and the agent-only composer driver (#799, #824 open).

</details>

</details>

## Milestone history

| Milestone | Status |
|---|---|
| `--duration` binds in a non-English Flow UI (#963), a notice-only cookie bar is acknowledged, image upscale clicks through locators (#957), and `auth status` probes with the profile's own channel (#962) | ✅ done (v0.83.2) |
| The default image model (`nano2`) runs Nano Banana 2.1 on flow.google.com — the submit guard accepts its `BELUGA` token (#958) | ✅ done (v0.83.1) |
| Billed video runs on flow.google.com download again — the generation record is found without its `"CAE"` marker, picked by id from multi-record replies, recovered from `as29s` only (#948); image and video upscale ported to flow.google.com with MCP twins (#922) | ✅ done (v0.83.0) |
| A reCAPTCHA mint failure, a busy catalog and a missing video workflow id stop misreporting what happened (#915, #900, #898) | ✅ done (v0.82.1) |
| A run config row generates from an earlier row's image or a local file, referenced in place with no re-upload (#913); `gflow run` successes recorded with lineage; refs were silently dropped since v0.52.0 (fixed, record corrected) | ✅ done (v0.82.0) |
| The generation browser opens off-screen, placeable via `GFLOW_CLI_BROWSER_WINDOW_POSITION`, measured unchanged for image and video (#923); a write-denied profile raises `ProfileAccessError` instead of posing as lock contention (#919); `urllib3` CVE lock (#920) | ✅ done (v0.81.0) |
| An unported image form is refused by name instead of crashing a `gflow run` batch with a misleading `RecaptchaError` — the browser transport stops pre-minting a token it never read (#891); a lost transfer is recorded as a generated clip with a visible `STATUS` (#896); `--resolution` and `nano2-lite` (#787); flow.google.com refusals read from the wire (#909); `auth login` stops on the `/about` identity re-check (#902); `oauthlib`/`pyjwt` CVE locks | ✅ done (v0.80.0) |
| A finished, billed clip is no longer discarded when its download hits a transient connection reset, at all three download sites including the recovery command's own; and the failure that survives is typed rather than `Unexpected error` — naming the clip and the free way to fetch it, on the CLI and through both MCP doors (#895) | ✅ done (v0.79.1) |
| A billed generation whose download failed can be fetched back for free instead of paid for twice — `gflow data download`, video only (#865/#871, image gap #877); and two error paths stop asserting what nothing measured: aisandbox auth failures stop blaming a SAPISID cookie no such route reads (#803), and the migrated host stops echoing the requested model back as though Flow had confirmed it (#789). CI runs e2e tests for the first time — five hermetic, route-intercepted files that had been excluded from every run | ✅ done (v0.79.0) |
| A clean Windows install can run at all — `colorama` declared, so `ConsoleRenderer` stops aborting every interactive command before any subcommand body (#846); the sign-in window closes itself on a migrated account and a completed login stops being discarded unread as exit 12 (#849); `gflow update` detects a half-replaced install (#848); the migrated-host address scan goes linear (#852) | ✅ done (v0.77.1) |
| An account with no Flow access is told so instead of being shown a selector-drift error and asked to file a bug — exit 39, read from the rendered unavailable screen rather than a URL or a status code (#833); the containerised sign-in works on Windows and pins its own version (#830); an MCP agent's `project_name` is finally consumed, found by a new AST gate on the MCP→worker payload keys (#628); the Official MCP Registry publishes itself on release via OIDC (#829) | ✅ done (v0.76.0) |
| Every local file the migrated driver uploads is run-unique, so a re-run stops binding a stale look-alike, and the Frames picker is confirmed when it does not commit on the pick — covering `video i2v`, `video r2v` and `image i2i` alike (#792); `gflow credits` stops sending migrated accounts into a re-login loop at the raise site they actually hit (#795); an agent-only `flow.google.com` composer exits 25 `retryable: false` instead of 23 (#799) | ✅ done (v0.74.0) |
| Four error paths stop lying about what went wrong: a click that never lands reports the actionability condition that failed instead of a bare timeout (#776), a known Flow landing is named rather than blamed on the selector (#756), Google's auth URLs are stripped from error messages (#777), and the post-migration account chooser auto-selects instead of stalling (#763/#764) | ✅ done (v0.73.0) |
| Google's `glue` consent bar no longer blocks the migrated composer: it is cleared before the driver's first click, rejecting rather than accepting, and a bar that will not go is named as `div.glue-cookie-notification-bar` instead of `span` (#780) | ✅ done (v0.73.1) |
| Incident bundles from a migrated-host failure stop arriving blank — the composer's `about:blank` park ran before the capture, so every failure shipped `div = 0` and a white screenshot (#792); a missing browser-strategy marker and a token-less labs session stop being reported as network and SAPISID faults (#796, #795) | ✅ done (v0.73.2) |
| `gflow auth login` closes the sign-in browser itself, on a measured retraction — G12 blocks `navigator.webdriver`, not bundled Chromium (#767); `gflow image t2i`/local-file `i2i` driven on the migrated host (#692) | ✅ done (v0.72.0) |
| Two migrated-host error paths stop blaming the wrong thing: Flow's agent mode (three distinct outcomes, not one message) and its one-time upload-terms dialog (#749/#752, #719 shape A) | ✅ done (v0.71.1) |
| `gflow character create --voice` verified end to end for the first time; a credit shortfall reports exit 37; migrated-host incident bundles report their ligatures (the DOM dump had queried `i.google-symbols` only) | ✅ done (v0.71.0) |
| `gflow character create` driven on the migrated `flow.google.com` host; `--model` made deterministic by chip read-back; spike promoted to Phase 0 of the workflow | ✅ done (v0.70.0) |
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
