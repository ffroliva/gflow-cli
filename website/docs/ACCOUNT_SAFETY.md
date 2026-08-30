# Account safety — what this tool does, what it doesn't, and what it can't promise

> **The short answer.** Automating a Google product that has no public API carries
> real account risk, and nothing on this page removes it. What gflow-cli does is
> avoid looking like a bot *by not behaving like one*, fail loudly instead of
> hammering a surface that pushed back, and tell you the truth when Google blocks
> it. What it does **not** do is hide from Google.
>
> This page exists because everything below was already true and scattered across
> five files, so a reasonable person could not find it before deciding to install
> ([#602](https://github.com/ffroliva/gflow-cli/issues/602)).

Read [DISCLAIMER.md](../DISCLAIMER.md) first — it is the legal statement. This
page is the operational one.

## Two different things people mean by "will I get banned?"

They have different answers, so separate them:

| | What it is | What it costs | How it shows up |
|---|---|---|---|
| **Rate limiting / quota** | Ordinary usage limits on your own account | Nothing — you wait | Exit **21**, "Daily or per-minute model quota reached" (the message names the model) |
| **A WAF / reCAPTCHA block** | Google's bot-scoring decided a *profile* looks automated | Nothing directly, but that profile is unusable until the score decays | Exit **10**, `WafRejectionError`, `PUBLIC_ERROR_UNUSUAL_ACTIVITY` |

Neither is an account ban. **We have never observed an account ban**, on any of
the test accounts, across the project's life. That is an honest observation and
**not** a guarantee — absence of evidence on a handful of accounts is weak
evidence of absence, and Google's terms are Google's to enforce.

The cost model matters too: **credits are only spent on Veo video.** Images and
composition operations bill against a per-model daily quota rather than credits,
so most failure modes cost you nothing but time — see the cost note in the
[README](../README.md#why-gflow-cli).

## What gflow-cli does to stay unremarkable

None of this is stealth. It is the difference between "software using a browser"
and "software that obviously is not a human".

**It drives real, headed Chrome.** The `ui_automation` transport requires headed
system Chrome (`channel=chrome`). Headless Chromium is rejected by reCAPTCHA
Enterprise with an immediate 403 regardless of anything else you do, so headless
is not an option the tool quietly takes — it is a supported setting only for
transports that never touch the Flow UI. A silent downgrade from real Chrome to
bundled Chromium is treated as a **fatal error**, not a fallback (#222).

**Every interaction delay is randomised.** Individual clicks, panel waits and
type actions are jittered by ±25% around their base value, specifically to break
the perfectly-uniform timing signature that deterministic Playwright automation
produces. This is always on and is not configurable.

**Submissions are paced.** Every multi-prompt path — `image batch`,
`image t2i` with `--prompts-file` / `--stdin` / multiple prompts, and `gflow run`
image batches — inserts a **random 0.5–1.5 s pause between submissions** by
default. This is the knob you can widen; see below. Note this is *between
prompts*, and is a different mechanism from the ±25% per-interaction jitter above.

**One project per run, not one per prompt.** Repeated `project.createProject`
calls between generations add to the bot-like signature, so multi-prompt runs
create a single shared project, and `--project` lets repeated single-prompt runs
reuse a standing one.

**Each account gets its own isolated browser profile** under `GFLOW_CLI_HOME`.
The tool never touches your everyday Chrome profile, and profiles do not share
cookies or WAF heat.

**It refuses rather than retries.** A run that cannot do what you asked aborts
*before* submitting — unreachable UI arm (exit 28), selector drift (exit 23),
unavailable model (exit 23), tier refusal (exit 22). This matters for account
safety, not just correctness: a tool that retries into a wall raises the profile's
score every time. The tier-403 path documents this explicitly — **it must not
auto-retry, because retrying only inflates per-profile heat.**

## What it deliberately does not do

- **No proxy or IP rotation.** Your requests come from your connection.
- **No fingerprint spoofing**, beyond the opt-in patched engine below. No canvas
  noise, no UA lying, no timezone games.
- **No headless "unlock".** The patchright option is explicitly *not* one.
- **No pretence that it is not automation.** It drives a browser; Google can see a
  browser being driven.
- **No ToS circumvention claim.** Automating Flow may violate Google's terms. That
  is your decision to make, on your account.

The reasoning is deliberate: evasion is an arms race with a party that has far
more information than we do, and a promise we could not keep. Modest, honest
pacing is defensible; a stealth feature that fails silently in six months is not.

## What you can tune

| Knob | When to reach for it |
|---|---|
| `--jitter 10-30` / [`GFLOW_CLI_JITTER_RANGE`](CONFIGURATION.md#gflow_cli_jitter_range) | After a 403, or before a large batch. Widens the between-submission pause. `--jitter 0` disables pacing entirely (don't) |
| `--project <id>` | Repeated single-prompt runs — avoids project churn |
| [`GFLOW_CLI_BROWSER_ENGINE=patchright`](CONFIGURATION.md#gflow_cli_browser_engine) | Opt-in patched Playwright that avoids the `Runtime.enable` CDP leak, for stronger reCAPTCHA-Enterprise evasion on the **headed** path. Install separately (`pip install patchright`). Not a headless unlock |
| `--profile <name>` | Spread work across profiles; a profile that has been driven hard is the "hottest" |

## Field data — what actually triggers a block

Real measurements from a real account ([#241](https://github.com/ffroliva/gflow-cli/issues/241), 2026-07-05):

- 3-prompt runs spread **30–60 min apart**: all passed.
- A burst of **~14 image submissions in ~10 minutes**: ended in a 403.
- After **~30 min cooldown**, individually paced calls (45–120 s apart) on the
  same profile and project: 4/4 passed.

Flow's WAF reacts to **cumulative cadence**, not to any single request. Full
guidance: [DEBUGGING § WAF cadence](DEBUGGING.md#waf-cadence).

## When Google does push back

You will see `WafRejectionError` (exit 10) and `PUBLIC_ERROR_UNUSUAL_ACTIVITY`.
It is **per profile, not per account** — the same code path on a different
profile of the same account has succeeded the same day.

1. **Stop.** Each rejected request can raise the score further.
2. **Cool down** 30–60 min, then probe with a single small generation before
   batching again.
3. **Use the account in real Chrome** in the meantime — genuine human interaction
   lowers the score.
4. **Switch profile** if you need to keep working.
5. **Widen the jitter** when you resume.

Full entry, including the structlog signature to confirm it:
[KNOWN_ISSUES § batchGenerateImages HTTP 403](../KNOWN_ISSUES.md).

## What we cannot tell you

- Whether Google will change its stance, its scoring, or its terms.
- Whether your account, workload or region behaves like our test accounts.
- Whether a WAF block ever escalates to an account action — **we have not seen
  it**, and we cannot promise it does not happen.

If account risk is unacceptable to you, the honest recommendation is not to use
this tool, or to use it on an account whose loss you could absorb. That
recommendation is not a formality.
