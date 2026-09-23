# Spike — can gflow-cli run in a container? (2026-09-15)

**Question.** Is a container a viable host for gflow-cli, with authentication held in an external
volume? And specifically: does Google's anti-automation stack reject a container-run browser?

**Cost.** $0. No generation was attempted in any arm. No Flow submit, no Veo credits.

**Verdict.** The strong claim *"a container cannot generate"* is **refuted** for every layer that
can be measured without an interactive Google sign-in. gflow runs, real Chrome launches headed,
and the browser presents no automation markers. What remains unmeasured is the one-time login and
what Flow does afterwards — and that is stated as unmeasured, not inferred.

---

## Why this spike exists — a claim that was written before it was tested

On 2026-09-15 the sentence *"the container **cannot generate**, because generation needs a
signed-in Chrome session"* was written into `docs/CONTAINER.md`, into issue #822, and into a
marketing draft. It came from architecture, not from a run.

Its evidence base was the **Glama health-check image**, which deliberately installs no browser at
all. "It cannot generate" was a true statement about *that image*, generalised into a fact about
containers. That is the exact failure `skills/spike/SKILL.md` exists to prevent:

> A selector that does not match is evidence about the selector. It is never evidence about the
> feature.

An image built without a browser is evidence about the image.

## Three of the spike's own failures, recorded because they look like findings

All three were the harness, not the system. Each would have read as "containers do not work":

| Symptom | Actual cause |
|---|---|
| `xvfb-run: error: xauth command not found` | `xvfb` installed without `xauth`. Dockerfile bug. |
| Headed Chrome exits 1 immediately | The probe used `--entrypoint python`, bypassing the entrypoint, so Chrome launched with **no `DISPLAY`**. |
| `navigator.webdriver = True` | Measured from a **raw Playwright** launch, which gflow never performs. gflow strips that flag itself. |

The third is the instructive one: it looked like a real anti-automation finding and would have
confirmed the prior belief.

## Arm 1 — plumbing (image: Chromium + Xvfb)

```
gflow --version                     ->  gflow, version 0.75.0
headed Chromium under Xvfb          ->  launches, version 149.0.7827.55
navigate https://example.com        ->  title 'Example Domain'
navigator.webdriver (raw Playwright)->  True          <- about the probe, not gflow
```

**Plumbing works.** A container is a viable host.

## Arm 2 — real Chrome, and gflow's own detector

Chromium is not sufficient: `browser_manager.is_playwright_chrome_channel_available()` accepts
exactly one Linux path for `channel="chrome"` — `/opt/google/chrome/chrome` — and generation
requires a real-Chrome profile. Installing `google-chrome-stable` puts the binary there.

```
/opt/google/chrome/chrome                    293 MB, "Read channel stable"
is_playwright_chrome_channel_available()  =  True
is_chrome_available()                     =  True
resolved_chrome_binary()                  =  /usr/bin/google-chrome
```

**gflow's own gate passes inside the container** — not merely Playwright's.

## Arm 3 — the fingerprint, with gflow's actual launch shape

Replicating what gflow does rather than what a default Playwright script does:
`launch_persistent_context(channel="chrome", headless=False)`, plus
`--disable-blink-features=AutomationControlled` (`client.py:518`) and the init script that
overwrites `navigator.webdriver` (`client.py:759`).

```
REAL CHROME launched   : 153.0.8010.36
navigator.webdriver    : None
userAgent              : Mozilla/5.0 (X11; Linux x86_64) … Chrome/153.0.0.0 Safari/…
headless UA marker?    : False
page title             : 'Example Domain'
```

`navigator.webdriver` is **None**, not `True`. Per the `auth-autoclose-shipped-g12-is-webdriver`
finding, Google's G12 gate keys on `navigator.webdriver` rather than on Playwright itself, so the
signal that gate reads is absent here. The UA carries no `HeadlessChrome` marker.

**Xvfb is not a headless workaround.** It is a real X server with no attached monitor; Chrome runs
genuinely headed against it. That is categorically different from Chrome's `--headless` mode,
which is what Google's stack rejects.

## What is still UNMEASURED

Stated as unmeasured rather than inferred from the arms above:

- **The one-time interactive Google sign-in inside a container.** Needs a real display attached.
  The compose file provides a `login` service bound to the host X socket for exactly this; it has
  not been run.
- **Whether Flow accepts the session afterwards, and whether a generation succeeds.** Cannot be
  concluded from arms 1–3. A clean fingerprint is a necessary condition, not a sufficient one.
- **Whether a Windows-created Chrome profile is portable into Linux.** Expected to fail on DPAPI
  cookie decryption — Chrome seals its cookie store with a key bound to the Windows user. That
  would be a *portability* limit, not anti-automation, and the fix is to create the profile inside
  the container. Untested.

## The protocol this implies

The container is disposable; the **volume** is not. Everything identifying — the Chrome profile,
`GFLOW_CLI_HOME`, the SQLite catalog — lives in a named volume, so the image can be rebuilt or
upgraded without signing in again.

1. **Build** an image with `google-chrome-stable` (not Chromium) at `/opt/google/chrome/chrome`,
   plus `xvfb` **and `xauth`**.
2. **Log in once**, with a real display attached, into the volume. This is the only arm a headless
   CI box cannot run.
3. **Run everything else** headed under Xvfb against that volume.
4. **Expose `gflow serve`** on loopback only, and set `GFLOW_CLI_DAEMON_TOKEN` — since v0.75.0 it
   is verified on every request.

Artifacts: [`docker/Dockerfile`](../../../docker/Dockerfile) and
[`docker/docker-compose.yml`](../../../docker/docker-compose.yml).

## What this changes

`docs/CONTAINER.md` said a container cannot run gflow. That is now corrected to what is actually
known: the plumbing works and the browser is clean; the login and the generation are untested.
Issue #822's blocker 2 is narrowed accordingly — **blocker 1, Docker's requirement to share
working test credentials, is unaffected and remains the harder problem** for the Docker MCP
Catalog specifically.
