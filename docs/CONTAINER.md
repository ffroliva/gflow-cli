# Running gflow-cli in a container

**The Glama image** of gflow-cli is built automatically and cannot generate anything — it
installs no browser, by design. This page says what that image is for and what it provably does.

> **Corrected 2026-09-15.** This page previously generalised that into *"a container cannot run
> gflow"*. That was wrong, and it was wrong in an instructive way: the claim was true of the
> **health-check image** and was presented as a fact about containers. A measured spike refutes
> the strong form — in a container with real Chrome, gflow runs, Chrome launches headed under
> Xvfb, and `navigator.webdriver` is `None`. See
> [the spike](superpowers/spikes/2026-09-15-container-transport-viability.md) and
> [`docker/`](../docker/README.md) for the protocol and exactly how far it is verified.

## What the image is for

[Glama](https://glama.ai/mcp/servers/ffroliva/gflow-cli) runs automated safety and quality checks
on the MCP servers it lists, and only servers that pass those checks appear in its search
results. To run them it builds a container from a spec and asks the server to introspect itself.

That is the image's entire purpose: **a health check**. It is not a way to run gflow.

## The build spec lives in this repository

[`glama.json`](../glama.json) is the tracked copy of the spec Glama builds from:

```json
{
  "baseImage": "debian:trixie-slim",
  "buildSteps": ["uv sync"],
  "cmdArguments": ["mcp-proxy", "--", "/app/.venv/bin/gflow", "mcp", "run"],
  "placeholderArguments": {}
}
```

Glama generates the actual Dockerfile from these fields. It clones this repository at the
default-branch head **it last synced**, runs the build steps, then starts `cmdArguments`. That
synced head can be hours old, so press *Repository → Sync Server* before testing a new commit.
The build is published through Glama's **Auto-Release**; see
[DISTRIBUTION.md § Glama](DISTRIBUTION.md#glama--listed-2026-09-14-released-2026-09-15). `mcp-proxy` is the
bridge: Glama pings over HTTP, gflow speaks stdio.

**The spec is guarded by tests.** [`tests/test_glama_build_spec.py`](../tests/test_glama_build_spec.py)
asserts that the spawned executable is a console script this project defines, that it is an
absolute path into the uv virtualenv, that a build step creates that virtualenv, that the proxy
fronts it, and that the subcommand matches every other distribution artifact.

> **Why those tests exist.** The spec used to live only in a web form, so nothing in this
> repository described it and nothing could check it. Two builds failed with
> `could not start the proxy Error: spawn gflow ENOENT`. The image built fine — `uv sync`
> installs console scripts into `/app/.venv/bin`, which is never added to `PATH`, so a `CMD`
> naming a bare `gflow` missed and the container exited before answering a single request.
> A failing container is still listed but excluded from search results, which in turn blocks the
> awesome-list entries that gate on a Glama score.

Editing `glama.json` does **not** change what Glama builds — its copy lives in the server's admin
page and has to be changed there too. The tests keep the repository honest about what we told it;
they cannot tell you the image still boots. After changing either, re-run the build from the
Glama admin page and read the result.

## What it provably does

From build test `01a0a526-1013-7318-a171-937f48fb9a1a` at commit `73a7ad5`, which Glama released
as `0.75.0` (the first passing build, `01a0a3c1-…`, gave the same result on 0.74.0):

```
mcp.server.starting   cli_version 0.75.0
initialize    →  serverInfo {name: gflow-cli}, protocolVersion 2025-11-25
tools/list    →  15 tools
prompts/list  →  2 prompts
resources/list → 3 resources
```

No browser, no credentials, no Google session. The same 15 tools appear when the server is run
locally against a throwaway `GFLOW_CLI_HOME`, which is the point: **enumerating the surface needs
nothing.**

## What THIS image cannot do, and why

Every tool in that list that generates media will fail in **the Glama check image**.

gflow drives Flow through a **real, headed Chrome session signed in to your Google account** —
see [ARCHITECTURE.md](ARCHITECTURE.md). Google's auth and reCAPTCHA stack rejects browsers that
advertise automation and most headless approaches. The check image does not even install
Chromium, because it does not need it to answer `tools/list`.

So the container is in a genuinely odd state, and it is worth being blunt about it: it passes
every check Glama can run, and it would fail the first thing a user asked of it. Introspection
and capability are different questions, and only the first one is containerisable today.

Making it real needs: **real Google Chrome** in the image (Chromium does not satisfy
`channel="chrome"`, which resolves only to `/opt/google/chrome/chrome`), a logged-in profile in a
mounted volume, a display server, and Google accepting the session. **The first three are now
measured as working** — see the spike. The fourth is still unmeasured, because it needs an
interactive sign-in nobody has run yet; a clean fingerprint is a necessary condition, not a
sufficient one.

## Why there is no Docker Hub image

Publishing the check image to Docker Hub would ship something that looks installable and is not.
Someone would `docker run` it, watch `tools/list` succeed, call a generate tool and get a failure
that has nothing to do with their setup. A green badge on a broken install is worse than no
listing.

If a working containerised transport ever lands — the pure-HTTP transport tracked in
[ARCHITECTURE.md](ARCHITECTURE.md) would be the unlock — this decision should be revisited. It is
a deliberate "not yet", not a permanent no.

## Why there is no Docker MCP Catalog entry

Docker's [MCP Registry](https://github.com/docker/mcp-registry) accepts community submissions, and
gflow-cli clears its licence bar (MIT; they exclude GPL). Two things block it:

1. **Reviewers need working credentials.** Docker's `CONTRIBUTING.md` requires every submission to
   share test credentials so their team can verify the server. gflow authenticates against a
   personal Google account — supplying that means handing over the account and its Veo credit
   balance. There is no throwaway Flow account to provision instead.
2. **The container cannot do the job**, per the section above. Docker Desktop's MCP Toolkit runs
   catalog servers in containers and expects them to work.

Both are properties of the headed-browser architecture rather than of the submission process, so
neither is fixed by trying harder at the paperwork.

## Running the check yourself

The Glama admin page shows the generated Dockerfile and the two commands to reproduce it:

```bash
docker build -t mcp-server .
docker run -it --rm -e MCP_PROXY_DEBUG=true mcp-server
```

A successful run starts the proxy, logs `mcp.server.starting`, and answers `initialize` and
`tools/list`. A `spawn ... ENOENT` means the `cmdArguments` path no longer matches where the build
step puts the console script — the failure
[`tests/test_glama_build_spec.py`](../tests/test_glama_build_spec.py) now catches offline.

## See also

- [MARKETPLACES.md](MARKETPLACES.md) — every channel and what each delivers
- [DISTRIBUTION.md](DISTRIBUTION.md) — submission status per channel
- [MCP.md](MCP.md) — the MCP server itself, and per-client setup
- [ARCHITECTURE.md](ARCHITECTURE.md) — the headed-browser dependency this page keeps running into
