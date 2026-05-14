# Code Review: Shell Multi-Prompt `t2i` Spec

## Verdict

MINOR-EDITS

## Findings

### 1. Preserve existing `t2i` model aliases when reusing batch validation

- **Spec:** `docs/superpowers/specs/2026-05-14-shell-multi-prompt-design.md:21`, `:231`, `:251`
- **Existing code/docs:** `src/gflow_cli/cli_image.py:182`, `src/gflow_cli/cli_run.py:45`, `docs/USAGE.md:94`, `docs/USAGE.md:311`

The spec correctly requires non-breaking `gflow image t2i` behavior and reuse/extraction of `cli_run` batch machinery. The main compatibility edge is the Imagen alias: `t2i` exposes `image4`, while `gflow run` config validation currently allows `imagen4`. `Model.from_cli()` accepts both aliases, but a direct reuse of `BatchConfig` / `BatchPromptItem` validation could reject an existing `t2i --model image4` invocation in multi-prompt mode.

This is not a blocker, but the spec should explicitly require shell multi-prompt mode to preserve the current `gflow image t2i` CLI model choices, either by normalizing `image4` before constructing shared batch items or by widening shared validation without changing the `gflow run --config` JSON schema.

### 2. Parser shape loses line metadata needed for source/index validation messages

- **Spec:** `docs/superpowers/specs/2026-05-14-shell-multi-prompt-design.md:145`, `:260`, `:264`

C4 asks validation errors to identify source and index/line where possible, but the proposed parser returns only `tuple[str, ...]`. Once blank/comment lines are skipped, later length validation can identify the retained prompt index but not the original file/stdin line number. That weakens diagnostics for `--prompts-file`, especially with comments and blank lines.

Suggested edit: either make the parser return small records such as `ParsedPrompt(text, source_label, line_number, prompt_index)`, or explicitly state that the parser performs text length validation before discarding line metadata and raises errors with source/line details.

### 3. Acceptance criteria should lock single-prompt behavior with new batch flags present

- **Spec:** `docs/superpowers/specs/2026-05-14-shell-multi-prompt-design.md:170`, `:306`

C6 says `--continue-on-error` / `--fail-fast` may appear on `gflow image t2i` help and do not alter single-prompt behavior. AC1 covers legacy output naming, seed behavior, and summary rendering, but it does not explicitly test that these new flags are inert for exactly one positional prompt.

Suggested edit: extend AC1 or add a new AC requiring `gflow image t2i "one prompt" --fail-fast` and `--continue-on-error` to route through the legacy single-prompt path with unchanged output naming and summary behavior.

## Open Questions / Assumptions

- Assumption: shell multi-prompt mode should use the existing `t2i` model alias surface (`nano2`, `nano-pro`, `image4`) rather than the JSON config alias surface (`nano2`, `nano-pro`, `imagen4`).
- Assumption: extracting `BatchPromptItem`, `_PromptOutcome`, `_run_batch`, `_run_one_prompt`, `_render_summary`, and `_resolve_exit_code` into a shared module is preferable to importing private helpers from `cli_run.py`; this matches the spec's preferred architecture.
- Open question: should the multi-prompt summary title remain `gflow run`, or should the shared renderer accept a title so `image t2i` can report a command-specific table while preserving outcome semantics?

## Suggested Edits

1. Add an explicit model-alias preservation requirement under Section 6.2 or Acceptance Criteria.
2. Adjust the parser contract to retain source/line metadata or validate prompt length before returning bare strings.
3. Add an acceptance criterion for single-prompt invocations that include `--continue-on-error` or `--fail-fast`.

The spec is otherwise architecturally sound: it keeps the legacy single-prompt path separate, reuses the existing one-session/one-project batch loop, avoids per-prompt override scope creep, preserves the `gflow run --config` schema boundary, and has testable acceptance criteria.
