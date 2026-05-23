# AGENTS.md — gflow-cli

> Universal entry point for AI coding agents. Read this first; everything else routes from here.

Supported tools that auto-discover this file: Cursor, Codex, Aider, Gemini CLI, Jules, Devin, Windsurf, Zed, Warp, opencode, RooCode, Amp, Junie, Phoenix, GitHub Copilot, VS Code, Factory, Augment, Semgrep, Kilo Code, UiPath. Claude Code reads [CLAUDE.md](CLAUDE.md), which cross-references this file.

## Project at a glance

- Unofficial Python CLI for [Google Flow](https://labs.google/fx/tools/flow) — drives Veo (image-to-video, text-to-video) and Imagen (text-to-image) generations from the terminal by reverse-engineering Flow's private REST API at `aisandbox-pa.googleapis.com`.
- Python 3.11+ · `uv`-managed · `hatchling` builds · Playwright Chromium transport · `pyright` strict · `ruff` · `pytest`.
- Single-package modular monolith: `src/gflow_cli/{api,cli,cli_image,cli_video,_cli_helpers,auth,config,errors,observability,paths,profile_store}`.
- Requires a Google AI Ultra or Pro subscription with Flow access. All generations bill against the user's own Google account.

## Headed-browser dependency (architectural reality)

gflow-cli currently drives Flow via a **real Chrome session managed by Playwright** — `ui_automation` transport. Google's auth + reCAPTCHA stack rejects Playwright's bundled Chromium and most headless approaches. This is the project's defining trade-off:

- ✅ Works end-to-end against live Pro/Ultra accounts.
- ❌ Requires a saved Chrome profile, a display server for one-time login, and ~150 MB for Chromium.
- ❌ Cannot run on serverless / headless CI workers without prerecorded profile transplant.
- ❌ Per-account horizontal concurrency is capped by what one warm Page pool can drive.

If you can help unblock a pure HTTP transport (especially for video generation, where HTTP 401 + reCAPTCHA mints currently block us), please open an issue — see the README "Architecture & current limitations" section.

## Dev environment tips

- `uv sync` then `uv run playwright install chromium`. No global Python install needed.
- Copy `.env.template` to `.env.local`; never commit `.env.local`. It documents every env var.
- Output goes to `./tmp/` for scripts/tests or `$GFLOW_CLI_OUTPUT_DIR` for CLI outputs (defaults to `./out/`).
- One-time auth: `gflow auth login --browser chrome`. The `--browser chrome` flag is mandatory; the CLI fails fast on other strategies.
- Use `/gflow:plan` to see the active phase before starting work; `/gflow:known-issues` before touching auth or reCAPTCHA code paths.

## Testing instructions — The Impeccable Routine

Run these gates in order before every commit:

```powershell
$env:PYTHONUTF8=1
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run python -m pytest -q --cov=gflow_cli
```

Or invoke the wrapper: `/gflow:check`.

- Use `pytest -m "not live and not e2e"` locally; full suite OOMs on small dev machines. Scope to changed dirs; trust CI for the full sweep.
- TDD is non-negotiable. Coverage floor: 80% overall.
- Live tests (`@pytest.mark.live`) opt in via `GFLOW_LIVE=1`. E2E tests require `GFLOW_CLI_E2E_PROFILE`.

## Code style

- Type hints everywhere; `pyright` strict on `src/gflow_cli`.
- Structured logging only (`structlog`) — **never** raw `print()` or `import logging` in `src/`.
- Errors as RFC 9457 Problem Details with stable per-class exit codes (3–7, 14, 15).
- 100-char line length, `ruff` configured. Imports sorted by `ruff` (isort rules).

## PR instructions

- Branch naming: `feature/`, `bugfix/`, `hotfix/`, `chore/`, `docs/`, `test/`, `release/` — never `claude/` or unprefixed.
- `develop` is the integration branch; `main` is protected. Releases tag off `main` only.
- **Never add AI attribution to commit messages.** `Co-Authored-By:` trailers are fine if the user asks for them; auto-generated "🤖 Generated with Claude Code" footers are not.
- Run `/gflow:check` (or the Impeccable Routine) before every commit.
- All releases require a signed annotated tag (`git tag -s vX.Y.Z`); CI rejects unsigned tags.

## Where to look next

- **Architecture & target shape** → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Full docs index** → [docs/INDEX.md](docs/INDEX.md)
- **Known issues** (read before touching auth / reCAPTCHA) → [KNOWN_ISSUES.md](KNOWN_ISSUES.md)
- **Active phase & backlog** → [PLAN.md](PLAN.md) or run `/gflow:plan`
- **Release protocol** → [RELEASE.md](RELEASE.md)

## Claude Code-specific notes

[CLAUDE.md](CLAUDE.md) carries the auto-load instructions Claude Code reads natively. It cross-references this file for the universal rules; Claude-Code-specific session protocol (skills, slash commands, memory) stays in CLAUDE.md.
