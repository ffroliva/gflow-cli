# Community Feedback Uplift Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature community-feedback-uplift` to find the next
> unchecked task. Implement one task at a time, one workstream per PR. Run `/gflow:check` before
> every commit.

**Goal:** Fix what community projects built on gflow-cli ran into, and adopt what they had to build
around it — a clean install that works, sticky defaults, one-step agent setup and spend safety —
without changing any existing default, exit code, command or MCP contract.

**Architecture:** An umbrella plan of independent workstreams, each its own PR to `develop`. WS1 and
WS10 are fully tasked here. WS6, WS3, WS4 and WS2 get detailed tasks written into this file after
their `/gflow:scenario` pass, and WS2 only after its zero-credit experiment. New behaviour is opt-in
and resolved in one place shared by CLI and MCP; existing defaults (ADR 7: video 9:16) and exit
codes stay.

**Evidence:** community projects that build on gflow-cli (three documented install and usage
workarounds), alternative tools that advertise one-command setup and credit ceilings, repository
traffic and PyPI downloads. Details are kept out of this public file.

**Predict verdict:** STOP as first drafted → **CAUTION, confidence 6/10** after the revisions below
(5-persona review, 2026-09-14, against `develop` 7a44dff6). Hard STOPs removed: WS2 redesigned and
gated on an experiment; the partner pitch deferred behind prerequisites; WS9 dropped.

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| High | A missing optional dependency fails **after** a paid clip (true for `av` today) | Both `av` and `PIL` import eagerly in `media.py`, and the import at the first statement of `_run_chain` raises `FrameExtractionError` (exit 20) — strictly before the manifest read, `--dry-run`, the confirm prompt and any client (WS1) |
| High | Onboarding improvements lead new users into a login that fails for accounts served `flow.google.com` (#791, fix in #793) | WS4 ships only after the #791 fix is released |
| High | Spend cap built on a balance that cannot be read on the host our profiles are served (#795), or on reads that launch Chrome and collide with the profile lease | WS2 uses Flow's declared per-model unit cost (the `video extend` pre-flight pattern), gated on a zero-credit experiment; fails closed when a cap is set and the price is unknown |
| High | A sticky project routes paid generations or uploads into the wrong project, including via a `.env` in a cloned directory | Explicit flag always wins; effective value **and its source file path** are shown pre-submit, in `--json`, MCP results and `gflow doctor`; validated locally before any browser launch |
| Medium | MCP `tools/list` schema defaults change (image aspect `1:1` in MCP vs `9:16` in CLI) | Keep schema defaults; resolve a setting only when the parameter is omitted **and** the setting is set; record the existing drift |
| Medium | `--dry-run` loses its "instant, no browser, cannot spend" contract | No balance or price reads in `--dry-run`; print `balance unknown` as `video extend` does |
| Medium | Hand-editing Claude Code's live config races the running app | Register through `claude mcp add --scope user`; print the command when `claude` is not on PATH |
| Low | Queue payload keys accepted but never read (codec keeps unknown keys) | Daemon-enforcement tests, not codec round-trip tests alone |

---

## Non-breaking contract (tick on every PR)

- [ ] No existing default changes. Video aspect stays 9:16; no project is implied unless configured.
- [ ] No exit code changes meaning; prefer existing typed errors. A new code (39) only if the
      workstream's scenario shows agents cannot branch on an existing `type`.
- [ ] No CLI option removed or renamed; new options optional.
- [ ] MCP tools only gain optional parameters; existing `tools/list` defaults and payload keys unchanged.
- [ ] No SQLite migration unless a scenario proves `metadata_json` cannot carry the data.
- [ ] `--json` outputs only gain fields — covered by field-level tests.
- [ ] `--dry-run` stays browser-free and spend-free.
- [ ] **Locale-invariant both ways.** Any new DOM selector anchors on structure (ARIA role, `href`,
      icon ligature, hierarchy) and never on a translated text label; and any number read back from
      the UI is parsed locale-independently. Both halves, every time — a `int(re.search(r"\d+", t))`
      reads `1.234` as `1`, so on the pt-/ru-locale profiles this project already drives,
      `--max-credits 100` would wave through a 1234-credit submit. This row exists because the
      spike stated the requirement as "a structural anchor **and** a locale-independent number
      parse" and the first draft of WS2 carried only the anchor.
- [ ] The pre-existing suite passes with no edits to existing assertions (or the PR explains why an
      assertion was wrong).

## Workstreams and gates

| Order | WS | What | Gates before code |
|---|---|---|---|
| 0 | **WS0** | `gflow serve` request auth + DNS-rebinding protection | Private track — handled outside this file until disclosed |
| 1 | **WS1** | Fail `video chain` early with the right error + Pillow in `[chain]` + install smoke in `resolve-drift` | Issue → Bug Lane (cause proven) |
| 2 | **WS10** | Plugin marketplace + distribution catalog (maintainer priority) | `/gflow:scenario` for the plugin; live install check |
| 3 | — | Land #793 (#791 migrated login) and progress #795 (balance on migrated host) | Existing tracks — prerequisites, not duplicated here |
| 4 | **WS6** | Papercuts, folded into onboarding issue #601 | `/gflow:scenario` (error text is a mirror axis) |
| 5 | **WS3** | Opt-in sticky defaults via Settings | `/gflow:scenario` |
| 6 | **WS4** | Agent setup, docs-first | #791 fix released; `/gflow:scenario` |
| 7 | **WS2** | Spend cap on declared unit cost | Experiment done 2026-09-14 (price readable pre-submit) → `/gflow:scenario` |
| 8 | **WS8** | Partner pitch (draft, private) | #791 released, t2v spike over `gflow serve` |
| — | **WS5** | Ledger | Folded: `gflow data` already records operations and `unit_cost` |
| — | **WS7** | #639 migrated-host parity | Own track |

Rows are in execution order. **WS9 (maintainer ecosystem-review automation) was dropped, not
deferred** — it had no owner and its trigger was "once the review has been run by hand a third
time", which is scaffolding for a use case nobody has yet. Re-add it when a third hand-run
actually happens.

---

## WS1 — `video chain` fails early, with the right error, on a clean install

**Proven cause:** `cli_video.py:942` (`from gflow_cli import chain as chain_mod`) is the **first
statement of `_run_chain`** — before the manifest parse (`:952`), `reject_unusable_links` (`:960`),
the `--dry-run` short-circuit, the confirm prompt and any client. It pulls `chain.py:53` →
`media.py:26` `from PIL import Image`. Pillow is declared only in `[dependency-groups] dev:174`,
**not** in `[project.dependencies]` and **not** in the `chain` extra (`av>=12` only) that
`media.py`'s own docstring points users to. So a clean `pip install "gflow-cli[chain]"` still fails
`gflow video chain` with a raw `ModuleNotFoundError: No module named 'PIL'`, exit 1, no remediation.

The sibling dependency `av` is imported lazily inside `_decode_frame` (`media.py:80`), so a missing
`av` fails only **after link 0 is generated and paid for**. `media` has exactly one importer
(`chain.py:53`) and `chain` exactly one (`cli_video.py:942`) — verified by grep over `src/` — so
nothing else is on this import path. `video chain` has no MCP twin
(`tests/mcp/test_cli_parity.py:132`, "chain pipeline — not yet ported"). CI's `resolve-drift` job
installs the package without the dev group but only smoke-imports the MCP surface.

**Decision — three small changes, no new machinery:**

1. `pillow>=12.3.0` joins `av>=12` in the `chain` extra.
2. `import av` moves to **module level** in `media.py`, beside the existing `from PIL import Image`,
   so both optional deps fail at the same point — the one `_run_chain` already reaches first.
3. The existing import block at `cli_video.py:942` is wrapped in `try/except ImportError` and
   re-raised as `FrameExtractionError` (exit 20, existing) whose remediation names
   `gflow-cli[chain]`, `av` and `pillow`.

Net effect: **both** missing extras now fail at `cli_video.py:942`, strictly earlier than any spend,
any prompt and any manifest read, with a typed error and remediation.

**Rejected — a lazy `PIL` import plus an `importlib.util.find_spec("av"|"PIL")` pre-flight.** It
makes the import late and then adds a second mechanism to restore the earliness it just gave away,
carrying a hardcoded package tuple that can drift from the extra it mirrors. It is roughly +20 src
lines against +7 for the above, and needs a `test_media_imports_without_pillow` that exists only to
serve the lazy import. Also rejected: Pillow as a hard runtime dependency (only the optional chain
feature needs it); a new CI job and an AST import scan (`resolve-drift` covers the class of bug more
cheaply).

### Task 1.1 — Red tests

**Files:** the chain CLI test module (`tests/test_cli_video_chain.py` or wherever `_run_chain` is
already covered — find it, do not create a parallel module).

> **Test-design trap — read before writing.** The import under test is **function-local** and
> Python caches modules, so `sys.modules["PIL"] = None` on its own passes **with or without the
> fix**: by the time the test runs, `gflow_cli.media` is already imported and the statement is a
> cache hit. Each test must **evict** `gflow_cli.media` and `gflow_cli.chain` from `sys.modules`
> *and* block the dependency, then invoke the CLI. Confirm each test is red for the right reason by
> watching it fail before the fix — a test that would pass without the fix is the worst outcome
> here, and a sibling review caught exactly this shape in the first draft of this plan.

**Steps:**
- [ ] `gflow video chain <manifest> --dry-run` with `av` unimportable → exit 20, `FrameExtractionError`,
      remediation names `gflow-cli[chain]`, `av` and `pillow`; no client created, no browser launched.
- [ ] Same with `PIL` unimportable.
- [ ] Same on the confirm path (no `--dry-run`, no `--yes`): exits **before** the prompt is shown.
- [ ] The guard fires before the manifest is read — `gflow video chain does-not-exist.jsonl --dry-run`
      with the extra missing exits **20**, not 1. (It is the first statement of `_run_chain`, so this
      is a property of the fix, and it is what makes Task 1.4's release check meaningful.)
- [ ] `--json` carries `retryable: false` and the stable `type` for `FrameExtractionError`.
- [ ] **No one-link exemption.** "`video chain` requires `[chain]`" is one rule; do not branch the
      guard on `len(links) > 1`. A single link is what `gflow video t2v` already covers.

**Tests created (red):**
- [ ] `test_chain_rejects_missing_av_before_spend`
- [ ] `test_chain_rejects_missing_pillow_before_spend`
- [ ] `test_chain_rejects_missing_extra_before_the_confirm_prompt`
- [ ] `test_chain_rejects_missing_extra_before_reading_the_manifest`
- [ ] `test_frame_extraction_remediation_names_chain_extra_and_both_packages`

### Task 1.2 — Fix

**Files:** `pyproject.toml` (`chain = ["av>=12", "pillow>=12.3.0"]`), `uv.lock`,
`src/gflow_cli/media.py` (move `import av` from `_decode_frame` to module level),
`src/gflow_cli/cli_video.py` (`try/except ImportError` around the existing import block at `:942`),
`src/gflow_cli/errors.py` (`FrameExtractionError` remediation)

**Steps:**
- [ ] Implement; Task 1.1 green.
- [ ] `/gflow:check` green, including step 1b (remediation text is a mirror axis; confirm no MCP tool
      states otherwise).

### Task 1.3 — Install smoke in `resolve-drift`

**Files:** `.github/workflows/ci.yml` (`resolve-drift` job)

**Steps:**
- [ ] After `uv pip install --upgrade .[chain]`: assert `python -c "import gflow_cli.media, av, PIL"`.
      This one line is the whole gate — it is what fails today, and it cannot pass while a package
      the `chain` code path imports is missing from the `chain` extra.
- [ ] Run `gflow video chain --help` (cheap; proves the command still wires up).
- [ ] **Dropped:** a `pkgutil.walk_packages` scan of every module. The Decision paragraph rejects an
      AST import scan as too costly, and a runtime walk is the same idea with an exclusion list
      (`__main__` already) to maintain. The `.[chain]` import line above catches this bug class.
- [ ] Keep hardening: actions pinned by SHA, `persist-credentials: false`, no `${{ github.* }}` inside
      `run:`, no shared uv cache restore (`tests/scripts/test_workflow_hardening.py` green).
- [ ] Demonstrate once that the step fails with Pillow removed from the extra, then restore.

### Task 1.4 — Docs, changelog, release

**Files:** `CHANGELOG.md` (`Fixed`), `docs/USAGE.md` (chain install note names Pillow),
`KNOWN_ISSUES.md` (entry for users on ≤0.73.2: install `gflow-cli[chain]` plus `pillow`)

**Steps:**
- [ ] Changelog quotes the symptom (`No module named 'PIL'`) and the paid-clip `av` case now caught early.
- [ ] Patch release via `/gflow:release`; afterwards verify on PyPI:
      `uvx --isolated --from "gflow-cli[chain]==<new>" python -c "import gflow_cli.media, av, PIL"` and
      `uvx --isolated --from gflow-cli==<new> gflow video chain x.jsonl --dry-run` → exit 20.
      The second command works **only because of this fix**: the guard sits at the first statement of
      `_run_chain`, ahead of the manifest parse, so a non-existent `x.jsonl` never gets read. Against
      0.74.0 the same command exits **1** with a raw `FileNotFoundError` — run it before the release
      as the A/B control.

**E2E / Iron Law:** no Flow surface; the pre-flight runs before any client. The re-runnable checks are
the offline tests and the `resolve-drift` step.

---

## WS6 — Papercuts (fold into #601)

Outline — detailed tasks after `/gflow:scenario`:
- [ ] `ProfileLockedError`: improve the `remediation_hint` at the real raise sites
      (`profile_lease.py`, `client.py`), not `_default_remediation` (unused by them). MCP picks it up
      through `_gflow_error_dict`; the canary keys on the class name, so wording is safe.
- [ ] `cookie_decryption_failed_falling_back_to_playwright` (`auth/cookies.py:196`, INFO): move to DEBUG
      with `exc_type` (class name only); the failure that matters already raises.
- [ ] `docs/USAGE.md`: a first-run path for `video chain`, `scene`, `movie`.
- [ ] Present `gflow data` as the generation ledger (operations and `unit_cost` are already recorded).
- [ ] Say `ffroliva/gflow-cli` where the bare name is ambiguous (README, catalogs, posts).

## WS3 — Opt-in sticky defaults

Outline — detailed tasks after `/gflow:scenario`:
- [ ] **First, reproduce and fix the existing gap:** `GFLOW_CLI_PROJECT_NAME` set in `.env` does not reach
      Click's `envvar=` (reproduced: `get_settings()` reads `.env`, `os.environ` does not), while
      `.env.template` tells users to put it there. Red test, then resolve it through `Settings`.
- [ ] Settings `GFLOW_CLI_PROJECT_ID`, `GFLOW_CLI_IMAGE_ASPECT`, `GFLOW_CLI_VIDEO_ASPECT` in `config.py`
      (unset → today's behaviour).
- [ ] One resolver returning value **and source** (flag / env / `.env` path / built-in), in a non-CLI
      module following `profile_store.resolve_profile`; project ids validated with the **existing
      `_cli_helpers._validate_project_id` / `_FLOW_ID_RE`** — not `routes._PROJECT_ID_RE`, which is
      private to `api/`. `_cli_helpers.py:90-94` already carries that mirror with a comment saying it
      was relocated (T4b) precisely so the call-site modules cannot drift; a third reference would
      reintroduce the drift T4b closed. A stale id fails against the local catalog before any browser
      launch.
- [ ] CLI: option defaults become `None` with help text stating the fallback (no misleading
      `show_default`); the five `default="9:16"` literals in `cli_video.py` route through the resolver.
- [ ] MCP: keep `tools/list` defaults; when a parameter is omitted, resolve at queue time and write the
      concrete value into the payload (never resolve in the codec). Document that a client-launched
      server reads `$GFLOW_CLI_HOME/.env` or the client's `env` block, not a project `.env`.
- [ ] Security: show the resolving file path; decide in scenario whether `GFLOW_CLI_PROJECT_ID` from a
      CWD `.env` requires confirmation or is ignored unless also in `$GFLOW_CLI_HOME/.env`.
- [ ] Discoverability: `project_source` in `--json`, MCP `params`, and `gflow doctor` `info` findings
      (exit 33 semantics unchanged). **`aspect_source` is not shipped** — an aspect cannot misroute
      spend and the effective value is already visible in the output, so the source belongs in one
      `structlog` event, not in two public output contracts × N commands × the MCP mirror × docs.
- [ ] Precedence tests include #792 (`--project` required on migrated accounts) and #799 (agent-only
      composer exit 25 with `--project`).
- [ ] E2E: zero-credit `e2e_image` run with `GFLOW_CLI_PROJECT_ID` lands in that project.

## WS4 — Agent setup, docs-first (after the #791 fix is released)

Outline — detailed tasks after `/gflow:scenario`:
- [ ] README and `docs/MCP.md`: `claude mcp add --scope user gflow -- gflow mcp run`, plus the
      `--no-spend` variant, with an explicit note that the server can spend credits.
- [ ] `gflow mcp setup --target claude-code`: `shutil.which("claude")` then `claude mcp add --scope user`
      with a timeout; otherwise print the exact command. Never edit Claude Code's config file directly;
      respect `CLAUDE_CONFIG_DIR`. Offer the `--no-spend` registration.
- [~] Observation (2026-09-14, installed plugins on the maintainer machine) — **not yet a recorded
      spike**: Claude Code loads skills a plugin ships under `skills/<name>/SKILL.md` without a
      manifest field; a repo becomes installable through `.claude-plugin/marketplace.json`
      (`"source": "./"` or a subfolder); plugins may declare `mcpServers`. WS10 records this properly
      under `docs/superpowers/spikes/`, against a clean Claude Code config, before relying on it —
      an `[x]` with no artifact is what `skills/spike/SKILL.md` exists to prevent.
      **Catch:** this repo's `skills/` also holds maintainer-only skills (`release`, `check`,
      `pr-council-review`, …), so pointing a plugin at the repo root would ship them to users.
      **This is not hypothetical — it is already live on the sibling channel:**
      `.codex-plugin/plugin.json` declares `"skills": "./skills/"` and therefore ships all 18,
      `release` and `pr-council-review` included, to every Codex user today. WS10 curates **both**
      channels with one mechanism; fixing only the Claude side would leave the same bug shipping.
- [ ] Skills: a dedicated plugin folder (e.g. `plugins/gflow/`) containing only `gflow-cli` and
      `video-production`, relative doc links rewritten to absolute URLs, plus
      `.claude-plugin/marketplace.json` at the repo root pointing at it. No `gflow skill install`
      command and no wheel bundling. Delivered by WS10.
- [ ] No `--target codex` (already `codex plugin add gflow@gflow-cli`); `cursor` only if verified.
- [ ] MCP parity: `mcp setup` remains a recorded CLI-only exemption.

## WS2 — Spend cap on declared unit cost

- [x] **Experiment (zero credits) — done 2026-09-14**, see
      `docs/superpowers/spikes/2026-09-14-credits-on-migrated-host.md`. On `flow.google.com`:
      the **declared price is readable before submit** from the composer settings pane
      ("Generating will use N credits", follows mode/model/count; 0 for an image model, 12 for
      Omni 1.1 Flash in the 2026-09-05 spike). **No price was found on the wire** and **no balance
      was found anywhere**; the labs credits route returns 401. So: a cap on declared price is
      buildable now; a balance comparison stays blocked on #795.
- [ ] Before scenario: re-measure the video cost line per video model and for x2–x4 (free:
      select, read, revert — nothing submitted), and find a structural anchor for the line **and**
      a locale-independent parse for its number.
- [ ] **That re-measure must use the repaired instrument.** The council found the spike script wrote
      its capture *after* teardown and swallowed body-read failures into the same shape as "nothing
      on the wire". Both are fixed in this PR, but the 2026-09-14 numbers came from the version
      before the fix, so §2/§3 of the spike are provisional. Re-running is the A/B control
      and it costs $0.
- [ ] Outline for `/gflow:scenario`:
  - `--max-credits` / `GFLOW_CLI_MAX_CREDITS` on `t2v`, `i2v`, `r2v`, `video chain`, `video extend`:
    before each submit, read the declared cost (settings pane on `flow.google.com`, which the
    composer already opens to set model and count; `creditMapping` on labs; the extend pre-flight
    as today) and refuse when `spent_so_far + declared_cost > cap`; accumulate the declared cost
    per started operation. No balance comparison on `flow.google.com` until #795 locates one.
  - Fail closed: cap set and declared cost unreadable → refuse before submit with remediation.
  - `--dry-run` stays browser-free: show the cap and `balance unknown`.
  - Chain: a cap hit mid-run surfaces as `ChainPartialError` (exit 21) with the cap as the reason, so
    `--resume-from` guidance survives; single-op refusal **reuses `InsufficientCreditsError` (37)** —
    a cap refusal branches on the RFC 9457 `type`, so no new exit code. (Exit 39 is dropped from
    this plan; re-open it only if a scenario shows agents cannot branch on the existing `type`.)
  - **Day one, cap only where a price read already exists**: the `video extend` pre-flight and the
    labs `creditMapping`. The migrated composer's cost line joins once it has a structural anchor —
    shipping the text-located selector is forbidden by the locale row in the contract above.
  - MCP: optional **per-call** `max_credits`; daemon-enforcement tests, not only codec round-trips.
    **No per-process cumulative budget on day one** — a budget held in process memory is a cap that a
    restart silently resets, which is a knob that lies. If cumulative is wanted later, sum the
    `unit_cost` that `gflow data` already records (WS5's own fold), never RAM.
  - No schema migration: `unit_cost` is already recorded in `metadata_json`.
  - Docs: `CONFIGURATION.md`, `USAGE.md`, `MCP.md`, `KNOWN_ISSUES.md` (cap is on Flow's declared
    price, not on balance accounting).
  - E2E (`e2e_video`, opt-in): a cap below one clip's declared cost refuses before any spend.

## WS10 — Installable everywhere: plugin marketplace + distribution catalog

**Goal:** anyone can install the gflow skill and MCP server in one step from the places agents and
developers already look, and the project is listed wherever that audience discovers tools.

**Gates:** `/gflow:scenario` for the plugin (what a user sees on install; spend consent for the MCP
server), then TDD. The catalog is docs, but every listing must be verified live, never assumed.

> **On shipping generated skill copies under `plugins/`.** AGENTS.md says vendor directories hold
> thin wrappers only and *"never put protocol content in a vendor directory"*. A Claude Code plugin
> cannot follow a pointer — the marketplace installs a directory, so the skills must physically live
> under the plugin root. The rule's purpose is to stop the two copies **drifting**, and the repo
> already solves that exact problem for `website/docs/` with `generate_website_docs.py --check`. So
> the copies are generated, never hand-edited, and a `--check` drift gate runs in CI: if they drift,
> CI is red. `skills/<name>/SKILL.md` stays the single source of truth. Do not hand-edit anything
> under `plugins/`.

- [ ] **Claude Code plugin + marketplace.** `plugins/gflow/.claude-plugin/plugin.json` (name,
      version kept in sync with `pyproject.toml`, description, homepage, license),
      `plugins/gflow/skills/{gflow-cli,video-production}/` **generated** from `skills/`,
      optional `mcpServers` entry running `gflow mcp run` (decide `--no-spend` default in scenario),
      and `/.claude-plugin/marketplace.json` at the repo root. Tests: manifest schema, version sync,
      skill copies in sync, no maintainer-only skill shipped, relative links rewritten.
- [ ] **Curate the Codex channel with the same generator.** `.codex-plugin/plugin.json` currently
      declares `"skills": "./skills/"`, shipping all 18 skills — `release`, `check`,
      `pr-council-review`, `sonar`, `doc-review` — to every Codex user. Point it at the same curated
      set. One mechanism, both channels; a test asserts no maintainer-only skill is reachable from
      either manifest.
- [ ] **One version source.** `pyproject.toml` is authoritative; `plugins/gflow/.claude-plugin/plugin.json`,
      `.claude-plugin/marketplace.json` and `.codex-plugin/plugin.json` are checked against it by
      `scripts/ci/check_repo_hygiene.py` (which already enforces version lockstep), so a release bump
      cannot leave a manifest behind.
- [ ] **Live install check** in a clean Claude Code config: `/plugin marketplace add ffroliva/gflow-cli`
      → `/plugin install gflow@gflow-cli` → skill listed → MCP server listed. Evidence in the PR.
- [ ] **Keep existing channels in sync:** `.codex-plugin/plugin.json` (Codex) and `server.json` /
      MCP registry metadata if present; one version source.
- [ ] **Distribution catalog** — `docs/DISTRIBUTION.md` (public, operational): for each channel the
      audience, submission method (PR / form / CLI / auto-crawl), requirements, status
      (listed / submitted / todo / not eligible), listing URL, owner and last-verified date. Channels to
      research and verify: official MCP Registry, GitHub MCP registry, Smithery, Glama, mcp.so,
      PulseMCP, MCP Market, awesome-mcp-servers lists, Anthropic/Claude plugin and skills
      marketplaces, community skills marketplaces, Codex plugin listings, Cursor directory, VS Code MCP
      gallery, PyPI classifiers/keywords, and the CLI catalogs already listing us (printing-press,
      AI-CLI-Catalog, open-source catalog — refresh stale data). Outreach steps (forms, PRs to other
      repos) are drafted, then submitted only with the maintainer's go-ahead.
- [ ] README + `docs/MCP.md`: install badges and one-line install per channel once live.

## WS8 — Partner pitch (private draft)

- [ ] Prerequisites: the #791 login fix is released, and WS0 has shipped.
- [ ] Spike: t2v end-to-end over `gflow serve` on loopback with the server token; record latency,
      one-job-per-account behaviour, timeout needs (≥600 s) and the tasks-extension behaviour.
- [ ] Draft the pitch privately (maintainer's repo), stating: the documented host and token setup, one
      account and one concurrent job per profile, multi-minute calls, headed browser on the user's
      machine, Google ToS risk. Maintainer decides whether to post.

---

## Definition of done (umbrella)

- [ ] WS0 released and disclosed (handled on its private track; listed here so it is not forgotten)
- [ ] WS1 released; `resolve-drift` install smoke green on `develop`
- [ ] WS10 merged: the plugin installs from a clean Claude Code config, the Codex channel ships the
      same curated set, the drift gate is green in CI, and `docs/DISTRIBUTION.md` carries a
      last-verified date per channel
- [ ] Each of WS6, WS3, WS4, WS2 merged with its scenario record, `/gflow:check` green, council GREEN,
      SonarCloud green, and the e2e evidence its surface requires
- [ ] Non-breaking contract ticked on every PR
- [ ] `CHANGELOG.md` `[Unreleased]` updated per PR
- [ ] Docs updated per workstream (`USAGE.md`, `CONFIGURATION.md`, `MCP.md`, `KNOWN_ISSUES.md`)
- [ ] BDD features cover every Critical and High scenario from each `/gflow:scenario`
- [ ] No `# TODO` in any diff without a tracked issue link
