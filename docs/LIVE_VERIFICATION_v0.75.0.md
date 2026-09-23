# Live verification — v0.75.0

**Date:** 2026-09-15 · **Profile:** `ffroliva` (served `flow.google.com`) ·
**Cost:** **zero** — no Veo credits, no image generation, no Flow submit. Every arm below is
either a local process or a read.

v0.75.0 is a distribution-and-hardening release. That shape matters for what verification can
mean here: most of what changed is reached *before* Flow, or is packaging that only becomes
observable once the artefact is on PyPI. Each row below says which, and an item that could not be
run names what stopped it.

| # | Change | Surface | Verified live? |
|---|---|---|---|
| 1 | `gflow serve` verifies the daemon token on every request | local HTTP daemon, **both transports** | ✅ A/B, released-vs-merged |
| 2 | `video chain` fails before the first paid clip when the extra is missing | CLI, real wheel | ✅ $0, bare install |
| 3 | Remediation naming an extra survives Rich rendering | CLI console | ✅ $0 + neutered control |
| 4 | `gflow-cli` console script (`uvx gflow-cli …`) | packaging | ✅ real wheel, real venv |
| 5 | Claude Code plugin installs and curates to two skills | `claude plugin` | ✅ clean `CLAUDE_CONFIG_DIR` |
| 6 | PyPI metadata: summary, sidebar links, classifiers | PyPI page | ✅ **verified on the published page** — see §6 |
| 7 | `server.json` / MCP Registry listing | registry | ⚠️ **unblocked — token verified live, submission pending a tool install** — see §7 |
| 8 | "unofficial" dropped as a label | docs, README, site | n/a — touches no runtime surface |

---

## 1. `gflow serve` requires the token on every request

The A/B is against **released 0.74.0 from PyPI** and the **merged** `develop` tree, driven by the
same probe — not by reading the diff.

```
BEFORE  released 0.74.0   no token 200/200 (13 tools)  wrong 200/200  right 200/200
AFTER   merged develop    no token 401/401 (0 tools)   wrong 401/401  right 200/200
                          WWW-Authenticate: Bearer realm="gflow"
```

Both arms bind loopback with a throwaway `GFLOW_CLI_HOME` and `--no-spend`, and every call is a
read (`initialize`, `tools/list`). The BEFORE arm is the point: the token was *set* in both arms,
so this measures whether it is checked, not whether it is present.

**The deprecated SSE transport was driven separately, not assumed from shared code.** Its
handshake differs — `GET /sse` opens the event stream and the server answers with the POST
endpoint — so "both transports" is a claim about two different request paths:

| `GET /sse` | status | `WWW-Authenticate` |
|---|---|---|
| no token | **401** | `Bearer realm="gflow"` |
| wrong token | **401** | `Bearer realm="gflow"` |
| right token | **200** | — (stream held open, which is correct SSE behaviour) |

Also re-confirmed, because the fix forwards the bind host and getting that wrong would silently
disable protection that works today: on a loopback bind a bad `Host` still gets **421** and a bad
`Origin` still gets **403**.

Every arm binds **loopback**. A non-loopback bind is deliberately not exercised: standing an
authenticated daemon up on a live network to watch it return 401 is not a test worth the exposure,
and the bind host is not a variable the auth middleware reads — it is the outermost layer and runs
before transport security either way.

> **A correction that belongs in the record.** An earlier draft of the accompanying advisory
> claimed DNS-rebinding protection was disabled. It is not — the SDK auto-enables it for loopback
> hosts. That came from reading a fallback comment without checking its caller, and it was caught
> by measuring rather than by re-reading.

## 2. `video chain` refuses before anything is billed

Run against a **real wheel in a real venv installed without extras** — the exact state #813
reported, not a simulated one. The arm first proves it is in that state:

```
av present: False   PIL present: False
```

| Invocation | Exit | Console says |
|---|---|---|
| `video chain one.jsonl --dry-run` | **20** | `needs the optional [chain] dependencies: No module named 'av'` |
| `video chain one.jsonl` | **20** | same — no cost prompt reached |
| `video chain does-not-exist.jsonl --dry-run` | **20** | same — **not** a file-not-found |

The third row is the decisive one. A nonexistent manifest still exits 20 on the dependency error,
which is what proves the guard runs **before the manifest is read** rather than merely before the
generation. Exit 20 is `FrameExtractionError`; before this release the same state produced a
generic exit `1` *"Unexpected error… file a bug"*, and only after link 0 had been generated and
billed.

## 3. Rich no longer eats the extra out of the advice

Verified on the **Rich-rendered console lines**, separated from the structlog JSON — `--json` was
never affected by this bug, so matching the JSON line would have been a false pass:

```
Last-frame extraction failed: `gflow video chain` needs the optional [chain]
dependencies: No module named 'av'
-> … Ensure the gflow-cli[chain] extra and both packages it carries (av, pillow)
   are installed: pip install 'gflow-cli[chain]'
```

**Control arm — the fix neutered, same string, same Rich version:**

```
UNESCAPED -> pip install 'gflow-cli'
ESCAPED   -> pip install 'gflow-cli[chain]'
```

The unescaped render reproduces the reported bug exactly: advice to reinstall what the user
already has. Without this control, the escaped output alone would only show that the text is
*currently* intact, never that the escape is what keeps it so.

## 4. The package-named console script exists

```
Scripts/  ->  flow.exe, gflow-cli.exe, gflow.exe
$ gflow-cli --version
gflow-cli, version 0.74.0        (the wheel under test, built pre-bump)
```

This is the entry point `uvx gflow-cli mcp run` resolves, and the MCP Registry builds exactly that
command from the PyPI identifier with no field for a differing executable name. Before this
release uv answered *"Use `uvx --from gflow-cli <EXECUTABLE-NAME>` instead"* — a registry listing
would have been broken on arrival.

Wheel sanity at the same time: builds clean, **138 entries, zero ZIP duplicates.**

## 5. The plugin installs, and installs only two skills

`claude plugin install` into a clean `CLAUDE_CONFIG_DIR`, checking what actually landed on disk
rather than what the manifest claims. Only `gflow-cli` and `video-production` are present; no
maintainer-only skill directory appears.

> **Found by running, not by the gate.** An earlier cut left *empty stray directories* in the
> payload, so `generate_plugin_skills.py --check` reported "in sync" while `skills/release/` sat
> in the installed plugin — the gate counted files and a directory has none. A second defect in
> the same generator walked the filesystem rather than git, so an untracked local file could have
> shipped to users; CI checks out clean and would never have seen it. Both are fixed and both
> were invisible to a green gate.

## 6. PyPI metadata — verified on the published page

The rewritten summary, the `Documentation` / `Repository` / `Changelog` sidebar links and the ten
added classifiers are **frozen per release**: PyPI renders the metadata of the last uploaded
artefact, so there was no way to observe them until this release was published. The three URLs were
each fetched live and returned HTTP 200, and every classifier was checked against the official
895-entry trove list (an invented one fails the upload outright), but the rendered page itself was
**unverified until publish** — a property of the platform, not a skipped run.

**Closed after the upload** (read back from the PyPI JSON API for 0.75.0):

```
version:     0.75.0
summary:     CLI and MCP server for Google Flow — drive Veo text-to-video, …
classifiers: 18
project_urls: Documentation · Repository · Changelog · Homepage · Funding
```

## 7. MCP Registry — unblocked; the ownership token is verified live

`mcp-publisher` verifies ownership by reading the `mcp-name:` token out of the **published** PyPI
README. That token was on `develop` and shipped here, but at the time of writing the published
artefact was 0.74.0, which predates it — a named external blocker with a defined removal step.

**The blocker is gone.** Read back from the published 0.75.0 description:

```
published README:  <!-- mcp-name: io.github.ffroliva/gflow-cli -->
server.json name:  io.github.ffroliva/gflow-cli          MATCH: True
```

What remains is not a blocker on this release but a tooling step: `mcp-publisher` is not installed
here, and its `login github` is an interactive device-code flow. Note also that `mcp-publisher
init` would **overwrite** the existing `server.json` — use `mcp-publisher validate` instead.
`tests/test_server_json.py` pins the version lockstep, the 100-character description cap, the name
pattern, the token's presence and that the advertised command exists.

---

## Not verified

- **A successful multi-link `video chain` run.** It spends Veo credits (`e2e_video`, gated behind
  `GFLOW_CLI_E2E_RUN_VIDEO=1`). What changed in this release is the *refusal* path, which is
  verified above against a real wheel; the generation path is unchanged code. Tracked as the
  outstanding e2e debt on **#813**.
- **`gflow credits` against a working balance.** The account returns **401** on the credits
  endpoint — pre-existing **#795**, not introduced here, and unrelated to anything in this
  release. Confirmed still present this cycle (`auth status` green, Flow session verified as the
  maintainer account; `credits user` exit 3, `detail: credits endpoint returned 401`). Its
  remediation text renders correctly, which is what §3 changed.
