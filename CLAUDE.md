# CLAUDE.md

> **Project memory hub for AI coding agents.** Claude Code reads this natively. Other agents (Cursor, Codex, Gemini CLI, Aider) should read this as a top-level reference.

## What this project is

`gflow-cli` is an unofficial Python CLI that drives [Google Flow](https://labs.google/fx/tools/flow) (Veo image-to-video, Imagen text-to-image) from the terminal by reverse-engineering Flow's private REST API at `aisandbox-pa.googleapis.com`. Built for Google AI Ultra/Pro subscribers who want to spend their Flow credits via batch automation rather than the web UI.

**Same pattern as `edge-tts`:** a community SDK over a private cloud API.

## On every session start

1. Read **[docs/INDEX.md](docs/INDEX.md)** — routing layer for all project docs.
2. Read **[PLAN.md](PLAN.md)** — current phase, next concrete tasks, ADRs.
3. Read **[KNOWN_ISSUES.md](KNOWN_ISSUES.md)** — open issues to avoid re-discovering.
4. Check **[CHANGELOG.md](CHANGELOG.md) `[Unreleased]`** — what's recently shipped.

## Active phase

**Phase 4 — Hardening DONE (v0.4.0a2).** Per-worker Page pool, tenacity
retry/backoff with reCAPTCHA re-mint, RFC 9457 Problem Details error
taxonomy with per-class exit codes 3–7, structlog observability with
`error_raised` / `error_unhandled` events, and 12 pytest-bdd scenarios all
shipped. Documentation polish landed in v0.4.0a2. Next phase TBD — likely
Phase 5 public alpha on PyPI followed by Phase 6 operations history
(see `PLAN.md`).

## Architecture (skim)

> Note: the layered diagram below describes the **target** architecture
> (deferred per [PLAN.md ADR #2](PLAN.md#5-decision-log-adrs-in-miniature)).
> The current package layout is the simpler
> `src/gflow_cli/{api/, cli.py, cli_image.py, cli_video.py, _cli_helpers.py,
> auth.py, config.py, errors.py, observability.py, paths.py,
> profile_store.py}`. See
> [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full target shape.

```text
interfaces/   →  application/   →  domain/   ←  infrastructure/
   CLI            commands /          pure         FlowProvider,
                  queries /         business        Playwright,
                  handlers /          logic         filesystem
                  ports
```

Dependency rule: **`domain/` depends on nothing**. `application/` depends on `domain/` + ports (Protocols). `infrastructure/` implements the ports. `interfaces/` wires it together.

## CRITICAL rules — invariant, never violate

- **Never commit secrets.** `.gitignore` excludes `auth/`, `profile_*/`, `*.cookies.json`, `storage_state.json`, `secrets.json`, `.env`, `.env.local`, `.env.*.local`. If you see one of these staged, abort and tell the user.
- **Never add `Co-Authored-By: Claude` (or any AI co-author) to commit messages.** Author attribution is the human user's only.
- **Sessions belong outside the repo.** Default location is `$LOCALAPPDATA/gflow-cli/profile_*` (Windows) or `~/.local/share/gflow-cli/profile_*` (POSIX) via [`platformdirs`](https://github.com/platformdirs/platformdirs). Never store sessions in `/tmp`, `%TEMP%`, or anywhere the OS auto-reaps.
- **No raw `print()` and no `import logging` in `src/`** — use `structlog` for structured events, or Rich `console.print(...)` for user-facing output.
- **Domain layer is pure** — `src/gflow_cli/domain/*` must not import `application/`, `infrastructure/`, or `interfaces/`. No I/O, no frameworks.
- **Frozen dataclasses for value objects.** Aggregates may have controlled mutation methods, but VOs (AspectRatio, Prompt, JobId, ...) are immutable.
- **Async all the way down.** Handlers and providers are `async def`. CLI is the only place that calls `asyncio.run(...)`.
- **TDD is non-negotiable.** Red → Green → Refactor → Commit. Coverage floor: **80% overall**, **90% on `domain/` and `application/`**. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Coding conventions

- **Python 3.11+**, strict typing (`pyright --strict` on `src/gflow_cli/`), `from __future__ import annotations` at the top of every module.
- **`@dataclass(frozen=True)`** for value objects and DTOs. `Protocol` for ports.
- **`pathlib.Path`** everywhere — never raw strings for filesystem paths.
- **Click + Rich** for the CLI, **Playwright `page.request`** as the HTTP transport (auto-attaches Google session cookies), **`tenacity`** for retry/backoff, **`structlog`** for structured logs, **`pytest + pytest-bdd`** for tests.
- **Conventional Commits** for messages (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`, etc.). See examples in `git log`.
- **Short files, single responsibility.** ~200-400 lines typical, 800 max. Split into `package/<topic>.py` if growing.

## How to verify your work

Run all five BEFORE asking to commit:

```bash
uv run python scripts/ci/check_repo_hygiene.py  # hygiene gate (run first)
uv run ruff check src tests          # lint
uv run ruff format --check src tests # formatting
uv run pyright src                   # types
uv run pytest -q --cov=gflow_cli      # tests + coverage
```

CI runs the same five on every push (see `.github/workflows/ci.yml`).

## Output path rule — MANDATORY

**All script and test runtime output goes to `tmp/`.** Never to `test_assets/`
or the repo root. `test_assets/` is for committed test *fixtures* (static
input files checked into git). The CI hygiene gate (`check_repo_hygiene.py`)
actively blocks commits that violate this rule.

## Where to look

| I need to… | Read this |
|---|---|
| Understand the architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Add a CLI command | [docs/USAGE.md](docs/USAGE.md) + existing patterns in `src/gflow_cli/cli.py` |
| Add an API route | [PLAN.md § 4 Phase status](PLAN.md#4-phase-status) + existing client in `src/gflow_cli/api/client.py` + capture data under `samples/captured/` |
| Touch auth | [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md) |
| Add a config knob | [docs/CONFIGURATION.md](docs/CONFIGURATION.md) + `.env.template` + (later) `src/gflow_cli/shared/config.py` |
| Write a test | [CONTRIBUTING.md § TDD](CONTRIBUTING.md#test-driven-development-mandatory) + existing patterns in `tests/` |
| Cut a release | [README § Releases](README.md#releases) — bump version in `pyproject.toml`, update CHANGELOG, `git tag vX.Y.Z`, push |
| Track a known issue | [KNOWN_ISSUES.md](KNOWN_ISSUES.md) |
| Understand security posture | [docs/SECURITY.md](docs/SECURITY.md) |

## Common tasks

### Run the CLI from source

```bash
uv sync
uv run gflow --help
uv run gflow auth                              # bare command — list profiles or login
uv run gflow auth login --profile experiments  # named profile
```

### Add a new test (TDD)

1. Write the failing test in `tests/<area>/test_<thing>.py`.
2. `uv run pytest tests/<area>/test_<thing>.py` — verify it fails for the right reason.
3. Implement the minimum production code to pass.
4. Refactor.
5. `uv run pytest -q --cov=gflow_cli` — verify nothing else broke and coverage didn't regress.

### Update a doc

1. Edit the file under `docs/`.
2. Add a row to `docs/INDEX.md` if it's a new file.
3. Update `CHANGELOG.md` `[Unreleased]` only if the change is user-visible.

## Things NOT to do

- Don't add a feature without a PLAN entry (or update PLAN if scope shifts).
- Don't `pip install` — always `uv add` so the lockfile stays in sync.
- Don't put new files in the repo root unless they're truly top-level (LICENSE, README, PLAN, CHANGELOG, etc.). Code goes in `src/`, tests in `tests/`, docs in `docs/`.
- Don't introduce a new dependency without listing the rationale in PLAN's Decision Log.
- Don't `--no-verify` your way past a hook. Fix the hook's complaint instead.
- Don't generate placeholder content (lorem ipsum, "todo: implement"). Either skip the file or write the real thing.

## Reasoning style for an agent

- **Verify before claiming done.** Run the quality gates and read the output. "Tests pass" without evidence is not acceptable.
- **Small, atomic commits.** Each commit message captures one logical change.
- **Question scope creep.** If the user asks for X and you're tempted to also fix Y, ask first.
- **Plan before implementing** for anything > 1 file or > 50 LoC. Write the plan into PLAN.md or a comment, then execute.
- **Show your work.** When you change something non-obvious, leave a one-line comment with the WHY (not the WHAT — the diff already shows the what).
- **Fix bugs autonomously.** Given a bug report, reproduce it, trace the root cause, fix it, and verify — no hand-holding needed. Point at logs/errors/failing tests, then resolve them.
- **Learn from corrections.** After any user correction, append the pattern to `tasks/lessons.md` (create if absent) as a rule that prevents the same mistake. Review it at session start for relevant context.

## See also

- [README.md](README.md) — public-facing project overview
- [docs/INDEX.md](docs/INDEX.md) — full doc routing
- [PLAN.md](PLAN.md) — implementation roadmap with phases & ADRs
- [.claude/README.md](.claude/README.md) — what's in `.claude/` and how to extend it

# context-mode — MANDATORY routing rules

You have context-mode MCP tools available. These rules are NOT optional — they protect your context window from flooding. A single unrouted command can dump 56 KB into context and waste the entire session.

## BLOCKED commands — do NOT attempt these

### curl / wget — BLOCKED
Any Bash command containing `curl` or `wget` is intercepted and replaced with an error message. Do NOT retry.
Instead use:
- `ctx_fetch_and_index(url, source)` to fetch and index web pages
- `ctx_execute(language: "javascript", code: "const r = await fetch(...)")` to run HTTP calls in sandbox

### Inline HTTP — BLOCKED
Any Bash command containing `fetch('http`, `requests.get(`, `requests.post(`, `http.get(`, or `http.request(` is intercepted and replaced with an error message. Do NOT retry with Bash.
Instead use:
- `ctx_execute(language, code)` to run HTTP calls in sandbox — only stdout enters context

### WebFetch — BLOCKED
WebFetch calls are denied entirely. The URL is extracted and you are told to use `ctx_fetch_and_index` instead.
Instead use:
- `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` to query the indexed content

## REDIRECTED tools — use sandbox equivalents

### Bash (>20 lines output)
Bash is ONLY for: `git`, `mkdir`, `rm`, `mv`, `cd`, `ls`, `npm install`, `pip install`, and other short-output commands.
For everything else, use:
- `ctx_batch_execute(commands, queries)` — run multiple commands + search in ONE call
- `ctx_execute(language: "shell", code: "...")` — run in sandbox, only stdout enters context

### Read (for analysis)
If you are reading a file to **Edit** it → Read is correct (Edit needs content in context).
If you are reading to **analyze, explore, or summarize** → use `ctx_execute_file(path, language, code)` instead. Only your printed summary enters context. The raw file content stays in the sandbox.

### Grep (large results)
Grep results can flood context. Use `ctx_execute(language: "shell", code: "grep ...")` to run searches in sandbox. Only your printed summary enters context.

## Tool selection hierarchy

1. **GATHER**: `ctx_batch_execute(commands, queries)` — Primary tool. Runs all commands, auto-indexes output, returns search results. ONE call replaces 30+ individual calls.
2. **FOLLOW-UP**: `ctx_search(queries: ["q1", "q2", ...])` — Query indexed content. Pass ALL questions as array in ONE call.
3. **PROCESSING**: `ctx_execute(language, code)` | `ctx_execute_file(path, language, code)` — Sandbox execution. Only stdout enters context.
4. **WEB**: `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` — Fetch, chunk, index, query. Raw HTML never enters context.
5. **INDEX**: `ctx_index(content, source)` — Store content in FTS5 knowledge base for later search.

## Subagent routing

When spawning subagents (Agent/Task tool), the routing block is automatically injected into their prompt. Bash-type subagents are upgraded to general-purpose so they have access to MCP tools. You do NOT need to manually instruct subagents about context-mode.

## Output constraints

- Keep responses under 500 words.
- Write artifacts (code, configs, PRDs) to FILES — never return them as inline text. Return only: file path + 1-line description.
- When indexing content, use descriptive source labels so others can `ctx_search(source: "label")` later.

## ctx commands

| Command | Action |
|---------|--------|
| `ctx stats` | Call the `ctx_stats` MCP tool and display the full output verbatim |
| `ctx doctor` | Call the `ctx_doctor` MCP tool, run the returned shell command, display as checklist |
| `ctx upgrade` | Call the `ctx_upgrade` MCP tool, run the returned shell command, display as checklist |
