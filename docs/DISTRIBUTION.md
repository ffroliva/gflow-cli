# Distribution channels

Where people can install or discover gflow-cli, what each channel needs, and where we stand.

This is an operational document, not a wish list. **Every row was verified live on its
`last verified` date** — a page fetched, a search run, a schema read. Where something could not be
checked, the row says `UNVERIFIED` and names what blocked it. Do not update a row from memory; if
you cannot re-check it, change the date and mark it stale instead.

**Status vocabulary:** `listed` (we are in it) · `todo` (eligible, not submitted) · `submitted`
(sent, awaiting review) · `not eligible` (cannot be listed as the project is packaged, or the
channel's own policy excludes it) · `passive` (auto-crawled — nothing to submit) · `closed` (the
channel no longer accepts anything).

> **Outreach is the maintainer's call.** Every submission below is drafted, never sent
> automatically. Anything that posts to a third party — a form, a PR to another repo, an email —
> waits for an explicit go-ahead. The 2026-09-14 round of submissions was made under an explicit
> one; the rows below record what was actually sent, and to where.

---

## At a glance

| Channel | Audience | Submit via | Status | Last verified |
|---|---|---|---|---|
| [PyPI](https://pypi.org/project/gflow-cli/) | Python users, every downstream scraper | `uv publish` (release) | listed | 2026-09-14 |
| [Official MCP Registry](https://registry.modelcontextprotocol.io) | Agent devs; feeds other registries | `mcp-publisher`, called by `release.yml` (#829; wiring repaired in #841) | **listed** · `0.77.0` active · automation **exercised and working** — v0.77.0 published itself unaided | 2026-09-17 |
| [Glama](https://glama.ai/mcp/servers/ffroliva/gflow-cli) | Broad MCP audience (87k servers) | Web form | **listed** · claimed · rated **A** · release **0.75.0** published · Auto-Release on | 2026-09-15 |
| [MCP Market](https://mcpmarket.com/server/gflow-cli) | Consumer discovery | — (crawled us) | **listed** | 2026-09-14 |
| [skills.sh](https://skills.sh/ffroliva/gflow-cli) | Cross-agent skill users | — (telemetry) | **listed** | 2026-09-14 |
| [punkpeye/awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) | The MCP list (95k★) | PR | **submitted** ([#14423](https://github.com/punkpeye/awesome-mcp-servers/pull/14423)) · Glama gate met, awaiting review | 2026-09-15 |
| [hesreallyhim/awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code) | Claude Code (54k★) | Issue | **submitted** ([#2844](https://github.com/hesreallyhim/awesome-claude-code/issues/2844)) | 2026-09-15 |
| [ComposioHQ/awesome-claude-skills](https://github.com/ComposioHQ/awesome-claude-skills) | Claude skills (75k★) | PR | **submitted** ([#1905](https://github.com/ComposioHQ/awesome-claude-skills/pull/1905)) · `ready-to-merge`, awaiting review | 2026-09-15 |
| [travisvn/awesome-claude-skills](https://github.com/travisvn/awesome-claude-skills) | Claude skills (15k★) | PR | **submitted** ([#1244](https://github.com/travisvn/awesome-claude-skills/pull/1244)) | 2026-09-15 |
| [mcpservers.org](https://mcpservers.org/servers/ffroliva/gflow-cli) | MCP discovery | Web form (no PRs) | **listed** · approved 2026-09-15 · badge in README | 2026-09-16 |
| [cursor.directory](https://cursor.directory/plugins/gflow-cli) | Cursor users | Web form | **listed** | 2026-09-15 |
| [appcypher/awesome-mcp-servers](https://github.com/appcypher/awesome-mcp-servers) | MCP (5.8k★) | — | **closed** (archived) | 2026-09-15 |
| Claude Code plugin marketplace (ours) | Claude Code users | — (self-hosted) | shipped | 2026-09-14 |
| [claude-community](https://github.com/anthropics/claude-plugins-community) | Claude Code + Cowork | Console web form | **not eligible** (policy §4.B) | 2026-09-15 |
| [cursor.com/marketplace](https://cursor.com/marketplace) | Cursor (curated) | Web form | todo | 2026-09-14 |
| GitHub MCP Registry / VS Code gallery | Copilot, VS Code | Email, after the MCP Registry | todo | 2026-09-14 |
| [Arnon-hs/open-source](https://github.com/Arnon-hs/open-source) | Agent-facing catalog | — (auto) | **listed, wrong licence** ([#7](https://github.com/Arnon-hs/open-source/issues/7)) | 2026-09-15 |
| [hasnocool/AI-CLI-Catalog](https://github.com/hasnocool/AI-CLI-Catalog) | AI CLI catalog | PR | listed | 2026-09-14 |
| [linny006/mcp-servers-live](https://github.com/linny006/mcp-servers-live) | Auto tracker | — (GitHub topic) | listed, fresh | 2026-09-14 |
| [PulseMCP](https://www.pulsemcp.com) | End users + devs | Closed; auto-ingests the MCP Registry | `closed` — rung 2 covers it | 2026-09-14 |
| [Smithery](https://smithery.ai) | Hosted MCP | — | **not eligible** | 2026-09-14 |
| [mcp.so](https://mcp.so) | SEO discovery | Web form, **$39 only** | not pursued | 2026-09-15 |
| Codex / ChatGPT Plugins Directory | ChatGPT + Codex | Portal, identity-verified | **not eligible** (MCP path); skills-only todo (low) | 2026-09-15 |
| printing-press-library | — | — | **not a listing** | 2026-09-14 |

---

## What is left

Ranked by reach per hour of work. The eight submissions are out; what is left is gated on one
thing, and it is not a distribution task.

> ✅ **Repaired in #841, and v0.77.0 proved it.** The registry now serves
> `io.github.ffroliva/gflow-cli` at **0.77.0, status `active`, published
> 2026-09-16T18:53:54Z** — from the release run itself, with no hand-publish.
>
> For every release through v0.76.0 the publish never fired. It hung off
> `release: published`, but `release.yml` creates the Release with the default
> `GITHUB_TOKEN`, and GitHub starts no workflow runs from `GITHUB_TOKEN`-created events.
> Measured on v0.76.0 — a real Release published, and the workflow had **zero runs, ever**
> ([#841](https://github.com/ffroliva/gflow-cli/issues/841)). v0.76.0 was published by hand;
> v0.77.0 was not. Verified against the live registry API on 2026-09-17.
>
> **It is now called, not triggered.** `release.yml` invokes `mcp-registry.yml` directly
> (`uses:` + `needs: build-and-publish`). No event, so nothing for the token rule to block —
> and no credential to rotate. A user PAT was the other way to fix it and was rejected on
> maintenance grounds: a 366-day expiry on a path that runs a few times a year is another
> scheduled silent-ish failure, which is the shape of the bug being fixed.
>
> `needs:` is also what enforces the ordering — `mcp-publisher` reads the `mcp-name:` token
> from the **published** PyPI README, so the wheel must be up first. And the call runs
> against the release **tag**, so the wrong-ref hazard below cannot arise on this path.
>
> **Pre-releases are excluded.** `release.yml` fires on every `v*.*.*` tag including
> `v1.2.3rc1`, and an `rc` must never become the registry's *active* listing — the one
> PulseMCP and GitHub's MCP gallery ingest. The call is skipped for them, and the workflow
> refuses a pre-release version outright if one ever reaches it by hand.
>
> **The manual path remains, and its ordering hazard is unchanged.** `gh workflow run
> mcp-registry.yml --ref <ref> -f version=X.Y.Z` publishes whatever `server.json` says **on
> that ref**, so dispatching `develop` before the release back-merge lands republishes the
> *previous* version. That happened on v0.76.0. The `version` input is now required and
> asserted against `server.json`, so the mismatch fails the run instead of publishing green.

1. **Publish to the Official MCP Registry** — the one that feeds the others (PulseMCP ingests it;
   GitHub's gallery is built on it). **v0.75.0 was the release that unblocked it**, and this document
   ships in it: PyPI metadata is frozen per release, so the rewritten summary, the three sidebar
   links and the ten classifiers take effect on that upload, and `mcp-publisher` can only verify
   ownership once the `mcp-name:` token is in the *published* README.

   **This is automated as of #841** — it was not before, and v0.76.0 went out by hand.
   `release.yml` calls `.github/workflows/mcp-registry.yml` as a reusable workflow, with
   `needs: build-and-publish` so it runs after the wheel is on PyPI, and it authenticates to
   the registry with GitHub Actions OIDC — **no token is stored for, or handed to, the
   registry, and none is needed to trigger it.** The call runs against the release tag, so the
   publish always carries the released `server.json`.
2. **Check Glama after the next release.** The Dockerfile is built and 0.75.0 is released, which
   met punkpeye's Glama gate. Auto-Release is meant to publish each GitHub release by itself, but
   it has never fired for us yet, and an unpinned build has already used a commit hours out of
   date (see below). After the next `/gflow:release`, open Glama → Admin → Releases and confirm
   the new build's `cli_version` matches the tag.
3. **Watch the open submissions.** Seven are awaiting a response — the six rows marked
   `submitted`, plus the Arnon-hs licence issue. cursor.directory is the one that already went
   live. Nothing to do but check back.
4. **skills.sh badge** — already listed with 21 installs, zero submission work, still not added.
5. **GitHub MCP Registry email** — after rung 2, and only if the auto-ingest question below
   resolves as "still needs the email".

Not pursued, with reasons in the sections below: the Anthropic directory (their policy excludes
us), Smithery and the ChatGPT directory (both need a hosted endpoint), mcp.so ($39), and
appcypher's list (archived).

---

## Channels in detail

### PyPI — `listed`

The package page is the single most-copied description we have. Three findings, all fixed in
**#816** and all taking effect on the **next upload** (PyPI metadata is frozen per release):

- The summary described only image-to-video and never mentioned MCP — half of what the package is.
- `Documentation`, `Repository` and `Changelog` sidebar links were missing (three free clicks).
  All three verified live at HTTP 200. `Funding` was already correct.
- Classifiers were thin. Added `Topic :: Scientific/Engineering :: Artificial Intelligence`,
  `Topic :: Multimedia :: Graphics`, `Topic :: Utilities`, `Typing :: Typed`,
  `Framework :: AsyncIO`, `Framework :: Pydantic :: 2`, `Operating System :: OS Independent`,
  `Intended Audience :: End Users/Desktop`, `Natural Language :: English`,
  `Programming Language :: Python :: 3 :: Only`. Every one was checked against the official
  895-entry trove list — an invented classifier fails the upload outright.

**Open, not done:** the README carries 36 relative links that are dead on PyPI. PyPI renders
`<a href="docs/MCP.md">` verbatim, so a reader lands on `pypi.org/project/gflow-cli/docs/MCP.md`.
Absolutising them fixes it, but `scripts/ci/check_doc_links.py` validates relative targets against
disk and would stop checking them — so the fix is absolutise **plus** teach the checker to map our
own `blob/main/` URLs back to paths. Tracked, not done here.

### Official MCP Registry — listed since v0.75.0, automated since #841

Schema `2025-12-11`, read live. `ServerDetail` requires `name`, `description`, `version`;
`description` is capped at **100 characters**; `name` must match `^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$`
(exactly one slash) and, under GitHub auth, start with `io.github.ffroliva/`.

`server.json` landed in **#816**, and `README.md` carries the ownership token the registry looks
for (`mcp-name: io.github.ffroliva/gflow-cli`, in an HTML comment so it does not render).
`tests/test_server_json.py` pins version lockstep, the description cap, the name pattern, the
token, and that the advertised command exists.

**The blocker that would have shipped a broken listing.** The registry builds `uvx <identifier>`
from the PyPI identifier and has no field for a differently-named executable. Measured:

```
$ uvx --isolated gflow-cli@0.74.0 --version
Use `uvx --from gflow-cli <EXECUTABLE-NAME>` instead.
```

`[project.scripts]` defined only `gflow` and `flow`. A `gflow-cli` console script was added in #816,
so `uvx gflow-cli mcp run` works — which is also what a user types first, the package being what they
just installed.

Publishing runs in CI: `.github/workflows/mcp-registry.yml`, on `release: published` (plus
`workflow_dispatch -f version=X.Y.Z` for a re-run). It authenticates with **GitHub Actions OIDC**,
so **no token is ever handed to the registry**, and the `mcp-publisher` download is pinned by
version *and* sha256 because that job holds `id-token: write`. **No personal access token is
involved anywhere in this path** — not for the registry, and not to start the workflow: it is
called by `release.yml` rather than triggered by an event (#841).

The equivalent by hand, if you ever need it locally:

```
mcp-publisher validate         # NOT `init` - see below
mcp-publisher login github     # device-code flow; namespace becomes io.github.ffroliva/*
mcp-publisher publish
```

`init` only writes a *template* `server.json`. This repo already has a real one, committed and
version-locked by `tests/test_server_json.py`, so `init` refuses it with `Error: server.json
already exists` **and exits 1** - which would abort the one-liner above at its first command.
`validate` is the right first step: it checks the existing file against the live registry and
leaves it untouched. Measured with `mcp-publisher 1.8.1` on 2026-09-15.

### Glama — `listed` 2026-09-14, `released` 2026-09-15

*"On the servers page, click Add MCP Server and fill in: the GitHub repository URL, a display name
and short description."* Automated licence, security and health checks; most submissions index
within minutes. A `glama.json` in the repo controls display name, description and category — worth
adding if the health check stalls on a browser-driving server. Confirmed not listed before
submitting: `glama.ai/mcp/servers/ffroliva/gflow-cli` → 404, and their search returned *"We don't
have an MCP server at ffroliva/gflow-cli"*.

Submitted through *Add Server → Open-Source Server* with the repo URL, name and description;
the form returned **"Your server has been submitted for review."** Approval is followed by an
email asking for a **Dockerfile**, and **only servers that pass its automated checks are indexed
for search**.

**Approved and listed the same day, then claimed.** The "Dockerfile" is not a file you paste —
Glama generates one from a **config form** on the server's admin page and clones the repo at the
default branch's head, so it never touches PyPI. Two builds failed with
`could not start the proxy Error: spawn gflow ENOENT`: `uv sync` installs console scripts into
`/app/.venv/bin`, which is never on `PATH`. With the absolute path the build passed in **31.2 s**,
answering `initialize` and `tools/list` with 15 tools, no browser and no credentials. The spec is
tracked as [`glama.json`](../glama.json) and guarded by `tests/test_glama_build_spec.py`; the
container's capabilities and limits are in [CONTAINER.md](CONTAINER.md).

Two numbers on that page are easy to confuse: the admin panel's percentage is internal profile
completeness (17% → 42% here), while the **public badge is a letter grade** — currently **A** —
and the badge is what `awesome-mcp-servers` asks for. Claiming required a **GitHub OAuth grant**
even though the account was created with Google; admin pages and build tests work unclaimed.

**That bar is lower than it looks, and the correction matters.** The first read of this was that a
container cannot exercise a logged-in Chrome profile, so the listing would exist but stay
unindexed. Glama's own bot says otherwise, on punkpeye PR #14423: *"we only need the server to
start and respond to introspection requests."* Tool introspection is static — `gflow mcp run`
answers `tools/list` with no browser, no profile and no Google session. The build above confirmed
it.

**Released 2026-09-15, and set to release automatically.** A passing build test is not yet
something Glama runs: its admin page asks you to create a **release** from a successful build.
`0.75.0` was released from build test `01a0a526-1013-7318-a171-937f48fb9a1a`, whose server logged
`cli_version 0.75.0`. The Releases page also has an **Auto-Release** switch ("builds and publishes
a new version on every GitHub release"). It is on, and the release uses the GitHub release notes
as its changelog, overwriting any text typed into the form.

Four observations, so nobody has to find them again:

- **There is no write API and no CLI.** Glama's [OpenAPI spec](https://glama.ai/api/mcp/openapi.json)
  has only read endpoints plus usage telemetry. Anything not covered by Auto-Release is done by hand
  in the admin pages.
- **Glama's copy of the repo can lag hours behind GitHub.** A build test at 12:50 UTC, with no pinned
  commit, checked out `cf1bb09` and reported `0.74.0`, although `develop` had moved on hours
  earlier. *Repository → Sync Server* brought Glama up to date. Glama's
  [methodology page](https://glama.ai/mcp/methodology) says syncs happen "within minutes of a
  push"; that is not what happened here.
- **The pinned-commit field only accepts commits Glama has synced.** Pinning the `v0.75.0` tag
  commit (`8689b8e`) failed with *"Commit not found"*, even after a sync, although it is on
  `develop`. The pin is left **empty**, so it cannot hold Auto-Release on one old commit, which
  keeps `glama.json`'s `"pinnedCommit": null` accurate.
- **Auto-Release has not fired yet.** The GitHub release `v0.75.0` was published at 08:24 UTC and
  Glama ran no build around then, possibly because the listing had no release until later that
  day. So nobody has seen whether it builds the tag or a synced branch head. `release.yml`
  publishes the GitHub release when the tag is pushed, before the release's step-15 merge into
  `develop`, so a branch-head build would report the previous version. **UNVERIFIED** until the
  next release.

### Glama is a dependency of the biggest awesome-list

Not obvious until the bot said so. **punkpeye/awesome-mcp-servers now requires a Glama listing.**
Their `check-submission` job passed, and then `github-actions` asked for two things: the server
listed on Glama and passing its checks, and a Glama score badge added to the PR line:

```
[![ffroliva/gflow-cli MCP server](https://glama.ai/mcp/servers/ffroliva/gflow-cli/badges/score.svg)](https://glama.ai/mcp/servers/ffroliva/gflow-cli)
```

So the chain is **Dockerfile → Glama indexed → badge → punkpeye merges**, and the ~95k★ list is
gated on the 87k-server registry rather than being independent of it. Do the Dockerfile once and
both rows move.

### MCP Market — `listed`, nobody submitted it

`https://mcpmarket.com/server/gflow-cli` → HTTP 200, titled *"Gflow CLI: Programmatic AI Video &
Image Generation"*, author `ffroliva`, category Developer Tools. The copy reads machine-generated,
so this was crawled. **Action: audit the copy for accuracy, not submit.** The page shows 112 stars
against 198 live, so its snapshot is stale.

### skills.sh — `listed`, and we did not know

`https://skills.sh/ffroliva/gflow-cli` — **16 skills, 21 installs**, `npx skills add
ffroliva/gflow-cli`. Operated by Vercel; indexed automatically from anonymous install telemetry,
so there is nothing to submit.

**It has the same curation problem the plugin channels had.** It indexes the repo's `skills/`
directory, so `release`, `check` and `pr-council-review` are listed as installable skills. The
plugin work curates the three manifest-driven channels; skills.sh reads the directory itself, so
curating it would mean moving files. Recorded, not fixed.

Free win: add the badge and the `npx skills add ffroliva/gflow-cli` line to the README.

### awesome lists — four submitted, one closed

- **punkpeye/awesome-mcp-servers** (~95k★) — PR
  [#14423](https://github.com/punkpeye/awesome-mcp-servers/pull/14423). Category
  `### 🎥 Multimedia Process`, alphabetical by `owner/repo`, between `editmamei/editmamei` and
  `FileToPDF/filetopdf-mcp`. Legend flags 🐍 Python, 🏠 local service, 🍎🪟🐧 platforms — **not** 🎖️,
  which means an official implementation. The title carries `🤖🤖🤖`: that is *their* documented
  opt-in marker for agent-authored PRs, used because an agent wrote it, not to claim a fast-track.
- **hesreallyhim/awesome-claude-code** (54k★) — issue
  [#2844](https://github.com/hesreallyhim/awesome-claude-code/issues/2844). They take an *issue*,
  not a PR. Gate is ≥100 stars or ≥14 days with active commits; we have 198. One resource per
  submission, and they offer no review contract — an unanswered issue is the expected outcome, not
  a problem to chase.
- **appcypher/awesome-mcp-servers** (5.8k★). **Archived — it cannot accept PRs at all.** This was
  listed as a live `todo` from earlier research; the fork-and-push succeeded and the PR creation
  returned 404. Read-only repositories fail late and confusingly, so the lesson is the general
  one: check `archived` before planning a contribution, not after.
- **mcpservers.org** (via wong2's list). **PRs are explicitly refused**; the web form at
  `mcpservers.org/submit` is the only door. **Approved 2026-09-15**, live at
  [`/servers/ffroliva/gflow-cli`](https://mcpservers.org/servers/ffroliva/gflow-cli); the
  approval mail supplies a README badge, which is now in the badge block. Their follow-up
  pitches a paid sponsorship for placement on the site and in `awesome-mcp-servers` — the
  free listing is what we took, and the badge does not depend on it.

Two Claude-skills lists were added after the first pass and submitted the same day:

- **ComposioHQ/awesome-claude-skills** (75k★) — PR [#1905](https://github.com/ComposioHQ/awesome-claude-skills/pull/1905),
  category *Creative & Media*.
- **travisvn/awesome-claude-skills** (15k★) — PR [#1244](https://github.com/travisvn/awesome-claude-skills/pull/1244),
  *Community → Individual Skills*.

Searched each for `gflow`, `ffroliva`, `veo`, `google flow` — none listed.
`TensorBlock/awesome-mcp-servers` was checked by README only and its README is small enough to
suggest category files exist, so treat its "not listed" as **UNVERIFIED**.

### Claude Code plugin marketplaces

Our own marketplace ships in this repo — see [MCP.md](MCP.md) for the install lines.

- **`claude-plugins-official`** — *"There is no application process"*. Not applicable.
- **`claude-community`** — **not eligible. We stopped at the consent checkbox and did not
  submit.** The mechanics all work: the Console form at `https://platform.claude.com/plugins/submit`
  accepts a solo maintainer (the claude.ai form needs a Team/Enterprise org), approved plugins are
  pinned to a commit SHA, the catalog syncs nightly, PRs against the repo are auto-closed, and
  `claude plugin validate . --strict` passes. The blocker is the policy the form makes you accept.

  Read live at
  [support.claude.com/en/articles/13145358](https://support.claude.com/en/articles/13145358-anthropic-software-directory-policy),
  § *4. Unsupported Use Cases*, clause **B** excludes *"Software that uses AI models to generate
  images, video, or audio content."* The carve-out immediately after it is for design-focused
  software, conditioned on the developer not offering standalone image generation as a primary
  service — which is exactly what we do offer. Clause **F** separately requires the developer to
  own or control any API endpoint the software connects to; gflow-cli drives Google's private Flow
  API, which we plainly do not control.

  Two independent disqualifications, one of them the product's whole purpose. Ticking a consent
  box that asserts compliance would be a false statement, so the box was left unticked. **Do not
  reopen this without a policy change** — re-read the article first and quote the clause that
  changed.
- **claudemarketplaces.com** — auto-crawls skills.sh, GitHub and MCP registries. `passive`.

### Cursor — `todo`

- **cursor.directory** (community, 5,429 plugins): **`listed` —
  [cursor.directory/plugins/gflow-cli](https://cursor.directory/plugins/gflow-cli)**, published
  2026-09-14, one component: the `gflow` MCP server running `gflow mcp run`.

  **Use the Manual tab, not the GitHub auto-scan.** The form offers both. The Auto (GitHub) path
  scanned the repo and proposed **18 components**, detecting every `skills/*/SKILL.md` in the tree
  — `changelog` was Component 1. That is the same curation failure the plugin manifests were
  written to prevent, arriving through a channel no manifest controls, because the scanner reads
  the directory and not `.claude-plugin/`. The Manual tab declares components by hand; only the
  MCP server was declared. This also settles the earlier **UNVERIFIED** note about needing a root
  `.mcp.json`: with the Manual tab the config is pasted into the form, so no root file is needed.
- **cursor.com/marketplace** (official, curated, 240 entries): needs a root `plugin.json` or
  `.cursor-plugin/plugin.json`, a logo committed to the repo, and Cursor-team review.
- Note: `cursor.com/directory` serves the generic homepage — it is not a directory.

### GitHub MCP Registry / VS Code gallery — `todo`

VS Code's MCP gallery **is** the GitHub MCP Registry (`chat.mcp.gallery.enabled`, backed by
`api.github.com/copilot/mcp_registry`). Two stages: publish to the official MCP Registry (self-
serve), then email `partnerships@github.com` to request inclusion. Curated, 252 servers, weighing
stability, security practices and ecosystem value. Not listed (all four search terms → 0).

**UNVERIFIED:** whether GitHub now auto-ingests from the official registry, making the email
unnecessary. Its 2025 launch post says servers *"will automatically appear"*; the current how-to
still says to email. The API needs a Copilot-scoped token to settle it.

### Catalogs already listing us

- **Arnon-hs/open-source** — `listed, stale`, **twice**
  ([mcp/](https://github.com/Arnon-hs/open-source/blob/main/mcp/ffroliva-gflow-cli.md),
  [content-creation/](https://github.com/Arnon-hs/open-source/blob/main/content-creation/ffroliva-gflow-cli.md)).
  Stars `111` → **198**; forks `32` → **54**; last push `2026-08-14`/`2026-09-05` → **2026-09-14**.
  Worse, the Russian summary says *«доступен под лицензией Python»* — "available under the Python
  licence". **We are MIT.** It looks like the `language` field was mapped into the licence
  sentence, which would affect every Python project in the catalog. Cards are machine-generated
  with no `CONTRIBUTING.md`, so a content PR would be overwritten; the route is an issue asking
  them to re-run their scout and fix the licence mapping — filed as
  [#7](https://github.com/Arnon-hs/open-source/issues/7). It is written as a catalog-wide bug
  report rather than a request about our card, because if the `language` field really is being
  mapped into the licence sentence then every Python project they list is mislabelled.
- **hasnocool/AI-CLI-Catalog** — `listed`, accurate. `catalog.json` and the README table agree,
  and every flag checks out (MCP yes, daemon yes, subscription auth yes, no API key, no local
  models). Only `"last_verified": "2026-08-09"` is stale. Optional one-line PR.
- **linny006/mcp-servers-live** — `listed, fresh`. Auto-indexes the GitHub topic `mcp-server`
  every 15 minutes; our page was one star behind live. Nothing to do — but it confirms the
  `mcp-server` topic is doing passive discovery work.

### Not eligible / not a listing

- **Smithery** — accepts only hosted JS uploads, an external public HTTPS endpoint, or a `.mcpb`
  stdio bundle. **PyPI is not a supported path.** A hosted endpoint is a non-starter for a server
  that drives a local Chrome profile and spends the user's credits.
- **PulseMCP** — submissions paused since 2026-09-03, and their own guidance is to publish to the
  Official MCP Registry, which they will ingest automatically. Doing #2 covers this for free.
- **mcp.so** — **$39, and there is no free tier.** The earlier **UNVERIFIED** note guessed a free
  reviewed queue might sit behind the sign-in gate. Checked signed in: it does not. Paid placement
  is a maintainer spending decision and nothing was spent.
- **Codex / ChatGPT Plugins Directory** — an MCP-backed submission requires a **public MCP server
  URL** for their scanner. Ours is local stdio. Only the "Skills only" path is open, and it needs
  verified developer identity plus 5 positive and 3 negative test cases. Low payoff.

**The hosted-endpoint wall is structural, and it will keep recurring.** Smithery, the ChatGPT
directory and several newer catalogs all want a public HTTPS MCP endpoint they can scan. gflow-cli
cannot have one: it drives a real Chrome profile holding the operator's Google session and spends
that account's Veo credits, so a shared hosted instance would be both a credential-sharing and a
billing problem. Treat "needs a hosted endpoint" as a permanent `not eligible`, not a backlog item
— and note the `gflow serve` HTTP transport does **not** change this. It is for *your own* daemon
on *your own* machine, not a public listing.
- **printing-press-library** — **we are not listed.** Our single mention is one line under
  *"Sources & Inspiration"* in the README of `flow-pp-cli`, a competing Go CLI that studied us.
  There is no entry to refresh and no submission process. Competitive intelligence, not a channel.

---

## Why there is no gflow "connector"

Anthropic calls it a Connector, OpenAI calls it an app; both mean the same thing — **a remote MCP
server at a public HTTPS URL that the platform connects to on the user's behalf.** It is the single
most-requested distribution shape, and it is worth writing down why gflow-cli does not have one, so
the question is answered rather than re-researched.

Three ways to build one, and what each costs:

1. **Host each user's Google session.** The server drives a logged-in Flow session out of a Chrome
   profile, so a hosted instance means holding users' Google credentials. Not a hosting problem, a
   liability one. **No.**
2. **Authenticate to Flow properly.** There is no public Flow API and no OAuth app model to
   register against — that absence is the entire reason this project reverse-engineers a private
   endpoint. **Not available to us.**
3. **A relay.** A thin hosted endpoint that tunnels to the user's own local daemon, which keeps the
   profile and the credits where they belong. **Technically real** — `gflow serve` plus an SSH
   tunnel or an ngrok-style reverse proxy already does it by hand today. What it adds is
   infrastructure we would run, an auth and abuse story for a public front door, and a second
   deployment to keep secure. That is a product decision with a running cost, not a packaging
   change.

Even a perfect option 3 would not open the channel people usually have in mind: the Anthropic
directory excludes us on content grounds (§4.B above) no matter how the server is reached, and the
ChatGPT directory wants a scannable endpoint *plus* verified developer identity and a test-case
suite. So a connector is a product bet on its own merits, not a distribution shortcut.

**Local stdio is the honest shape for this tool.** Every channel in the table above that accepts a
local-stdio MCP server has been approached; every one that refuses is refusing the architecture, not
the submission.

---

## Keeping this honest

- Re-verify before acting on any row older than ~30 days. These platforms change monthly: PulseMCP
  closed submissions, Smithery dropped to three release types, and `cursor.com/directory` stopped
  being a directory — all within the window this document covers.
- When a submission lands, update `status`, `listing URL` and `last verified` in the same change.
- Every `UNVERIFIED` above is a real gap, not a hedge. Closing one is a small, well-defined task.
- **An open submission is not a listing.** Six rows say `submitted`; that means a form was posted
  or a PR was opened, and nothing more. Do not let `submitted` age quietly into `listed` — when a
  row is checked, either find the live URL and change the status, or leave it `submitted` and move
  the date.
- **Check `archived` before planning a contribution.** appcypher's list was carried as a live
  `todo` through a full research pass and only revealed itself at `gh pr create` time, with a 404.
