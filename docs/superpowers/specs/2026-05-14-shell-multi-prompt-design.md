# Shell Multi-Prompt `t2i` Design Spec

**Status:** Draft v2 (council minor edits applied)
**Target version:** v0.6.0a1
**Date:** 2026-05-14

> **For agentic workers:** This is the SPEC, not the implementation plan. After
> approval and council review, write
> `docs/superpowers/plans/2026-05-14-shell-multi-prompt/PLAN.md` plus the
> sibling orchestration document. Do not start implementation from this file.
>
> **Revision log:**
> - v1: brainstorm-approved design.
> - v2: applied code/security/Gemini council minor edits: `image4` alias
>   preservation, parser source metadata, prompt-file size/file validation,
>   terminal-safe prompt previews, project/summary title parameterization,
>   output-dir regression coverage, and max fan-out documentation.

## 1. Goal

Add shell-friendly multi-prompt input to `gflow image t2i` while preserving the
existing single-prompt command behavior exactly.

## 2. Locked constraints from kickoff

These are copied from the kickoff prompt and are not open for re-design:

- **Non-breaking.** Existing `gflow image t2i "single prompt text"` callers MUST
  work identically. No daemon mode. No schema changes to `gflow run --config`.
- **Per-prompt overrides are OUT of scope** - they belong to
  `gflow run --config` (which already has them). Shell-multi-prompt is a
  *batch shortcut* with global `--aspect` / `--model` / `-n` shared across all
  prompts. The existing `--seed` flag remains supported for legacy
  single-prompt mode only; C7 locks the multi-prompt seed decision.
- **Branch convention:** `feature/*` (NOT `feat/*` - different from
  conventional-commit prefix). Operator's locked rule.
- **TDD throughout:** RED -> GREEN -> REFACTOR. Tests written before
  implementation. 80%+ coverage on new code.
- **Docs in the same commit as code.** Every commit that affects user-facing
  behavior MUST update USAGE.md / README / CHANGELOG. Operator's locked rule.
- **No upstream-PRs to dependencies.** Self-contained to gflow-cli.
- **No daemon mode.**
- **Do not touch `gflow run --config` JSON schema.**

## 3. Current behavior

`gflow image t2i` currently accepts exactly one positional prompt:

```text
gflow image t2i PROMPT [--model ...] [--aspect ...] [-n 1..4] [--seed N] [--out DIR]
```

It creates one `FlowApiClient` session, creates one Flow project, generates
one to four variations of that prompt, downloads them, prints a `gflow-cli t2i`
summary table, and exits. Output names are based on Flow's media name:
`<media_name>_<n>.png`.

`gflow run --config` already provides sequential batch behavior for different
prompts: one `FlowApiClient` session, one Flow project, prompt items walked in
order, per-prompt outcomes, `--continue-on-error` default, `--fail-fast`
optional, and final exit code equal to the max per-prompt exit code when
continuing.

## 4. New input surfaces

The feature adds three multi-prompt input surfaces to `gflow image t2i`.

### 4.1 Positional variadic prompts

```text
gflow image t2i "p1" "p2" "p3" --aspect 9:16
```

The command accepts one or more positional `PROMPT` values. Exactly one
positional prompt is the legacy single-prompt path. Two or more positional
prompts enter shell multi-prompt mode.

### 4.2 `--prompts-file <path>`

```text
gflow image t2i --prompts-file prompts.txt --aspect 9:16
```

`--prompts-file` reads UTF-8 text from a local file. The format is:

- one prompt per non-empty line
- leading and trailing whitespace is trimmed
- blank and whitespace-only lines are skipped
- whole-line comments whose first non-whitespace character is `#` are skipped
- inline `#` is literal prompt text, not a comment delimiter

The path must point to an existing readable regular file. The implementation
must reject oversized files before reading the full content into memory.
v0.6 locks the prompt-file byte cap at 512 KiB, which is comfortably above
50 prompts * 2000 characters plus comments while still protecting users from
accidentally passing a large log or binary file.

This deliberately does not support multi-line prompt blocks, JSONL, per-prompt
overrides, or seed columns.

### 4.3 `--stdin`

```text
cat prompts.txt | gflow image t2i --stdin
```

`--stdin` reads prompt text from standard input using the same plain-text
line parser as `--prompts-file`.

If `--stdin` is set and no input is piped, the command will naturally wait for
stdin, matching normal shell behavior. The implementation should not add
platform-specific pipe detection in v0.6.

## 5. Brainstorm decisions

### C1. File format

`--prompts-file` and `--stdin` use one prompt per non-empty line, with whole-line
`#` comments skipped. This matches the shell-shortcut goal and the existing
video TSV convention of allowing comment lines. Multi-line prompts stay out of
scope for this shortcut.

### C2. Mutual exclusion

Prompt sources are mutually exclusive:

- positional prompt(s)
- `--prompts-file`
- `--stdin`

Passing more than one source is a Click usage error before profile resolution,
browser launch, project creation, or any Flow API work. This avoids accidental
duplicate generations and credit spend.

### C3. Output naming

Single-prompt mode remains unchanged:

```text
<media_name>_<n>.png
```

Multi-prompt shell mode uses the same stem pattern as `gflow run`:

```text
prompt_<prompt-index>_<variation-index>.png
```

Indexes are zero-based to match the existing `gflow run` output convention
(`prompt_0_0.png`, `prompt_1_0.png`). This is intentionally different from the
legacy single-prompt `t2i` path so users can correlate files with prompt order.

### C4. Empty and invalid prompts

For file/stdin sources, blank lines and whitespace-only lines are skipped. After
parsing, the final prompt list must contain between 1 and 50 prompts.

Each retained prompt must satisfy the existing batch prompt validation bounds
where practical: text length 1 to 2000 characters. Validation errors should
identify the prompt source and index/line where possible without leaking
absolute internal paths unnecessarily.

For `--prompts-file`, normal user-facing validation errors should identify the
source as `--prompts-file <basename>` plus a line number when the error is tied
to prompt content. They should not print resolved absolute paths. Full paths may
be logged only under the project's existing debug/error logging policy.

### C5. Upper bound

Shell multi-prompt mode caps at 50 prompts, matching `gflow run --config`.

When the cap is exceeded, the command raises a clear usage/configuration error
before browser/API work. The message should mention that larger or more
structured batch workflows belong in `gflow run --config`, even though v0.6's
JSON schema has the same prompt count cap today. The intent is to steer users
toward the richer batch surface, not to promise a larger cap.

Because global `-n/--count` remains available, one shell multi-prompt invocation
can request up to 50 prompts * 4 variations = 200 images. User-facing help/docs
must make this fan-out visible so users understand the credit-spend multiplier.

### C6. Exit behavior

Multi-prompt shell mode inherits `gflow run` error semantics:

- `--continue-on-error` is the default.
- `--fail-fast` stops after the first failed prompt and marks remaining prompts
  as skipped in the summary.
- With `--continue-on-error`, the process exit code is the max per-prompt exit
  code.

The flags may be visible on `gflow image t2i` help globally. In single-prompt
mode they do not alter behavior.

### C7. Seed behavior

Single-prompt `t2i` keeps the current seed behavior exactly: `--seed` is valid
only when `-n 1`.

Multi-prompt shell mode rejects `--seed` with a clear usage error. This avoids
inventing awkward CLI pairing semantics for multiple prompts and multiple
seeds, and it respects the locked constraint that v0.6 must not change the
`gflow run --config` JSON schema.

Follow-up backlog item: add optional per-prompt `seed` support to
`gflow run --config`, paired with each prompt item. Sparse seeded batches should
be represented by omitting `seed` or using `null` in JSON, not by separate seed
arrays or `--skipseed`-style CLI flags.

### C8. Metadata and future operations history

v0.6 does not add a database. It should preserve structured metadata in code by
building multi-prompt shell requests as batch prompt items and outcomes rather
than as ad hoc strings.

Future database-backed operations history should be able to persist, for each
generated image:

- command source (`image t2i`)
- input source (`positional`, `prompts_file`, or `stdin`)
- prompt index
- variation index
- prompt text
- model
- aspect ratio
- seed when supported by the generating surface
- status
- output path

The implementation should not make that future schema harder by discarding
prompt indexes or flattening all outcomes into unstructured console-only text.

## 6. Architecture

### 6.1 Command path split

`gflow image t2i` should have two explicit paths:

1. **Legacy single-prompt path.** Exactly one positional prompt, no
   `--prompts-file`, no `--stdin`. This path keeps the existing
   `GenerateImageRequest` -> `_run_t2i` behavior, output names, summary table,
   and seed semantics.
2. **Shell multi-prompt path.** Two or more positional prompts, or
   `--prompts-file`, or `--stdin`. This path parses/validates prompt text,
   converts prompts into batch items, and runs them through shared batch
   machinery.

The single-prompt path is not a special case of the new batch path. Keeping it
separate is the strongest way to preserve v0.5.0a1 behavior.

### 6.2 Shared batch machinery

The implementation should reuse or extract from `gflow_cli.cli_run` rather than
duplicating session/project/prompt-loop orchestration. Acceptable shapes:

- Extract reusable helpers/dataclasses from `cli_run.py` into a small shared
  module, for example `gflow_cli.batch_image`, then have both `cli_run.py` and
  `cli_image.py` call that module.
- Or make targeted `cli_run.py` helpers public enough within the package for
  `cli_image.py` to reuse without import cycles.

The preferred implementation is a shared module because `cli_run.py` is a Click
entrypoint and already mixes config parsing, command wiring, execution, and
summary rendering. A small shared module gives the shell shortcut a clean API:

```python
BatchPromptItem(...)
run_image_batch(..., project_title="gflow-cli t2i")
render_image_batch_summary(..., title="gflow-cli t2i")
resolve_batch_exit_code(...)
```

Exact names are implementation details for the plan. The key requirement is
that one `FlowApiClient` session and one Flow project wrap the entire
multi-prompt loop.

The shared execution layer must operate on already-validated prompt items. It
must not require JSON-specific `BatchConfig` parsing or force the `gflow run`
default output directory on `t2i`.

Shell multi-prompt mode must preserve the existing `gflow image t2i` model alias
surface: `nano2`, `nano-pro`, and `image4`. `gflow run --config` currently uses
`imagen4` in its JSON schema; extracting shared code must not make
`gflow image t2i --model image4` invalid.

### 6.3 Prompt-source parser

Add a focused parser for line-based prompt sources. It should be testable
without Click or Playwright:

```python
ParsedPrompt(text: str, source_label: str, line_number: int, prompt_index: int)
parse_prompt_lines(text: str, *, source_label: str) -> tuple[ParsedPrompt, ...]
```

Behavior:

- split on universal newlines
- gracefully handle a UTF-8 BOM at the beginning of a file/stdin stream
- strip leading/trailing whitespace
- skip empty results
- skip lines whose first non-whitespace character is `#`
- preserve inline `#` as literal prompt text
- return retained prompts in source order with original source line numbers and
  retained prompt indexes

This parser should be shared by `--prompts-file` and `--stdin`.

### 6.4 Output directory behavior

For multi-prompt shell mode:

- If `--out DIR` is provided, write files flat into that directory using
  `prompt_<prompt-index>_<variation-index>.png`.
- If `--out` is omitted, write under
  `$GFLOW_CLI_OUTPUT_DIR/images/<YYYY-MM-DD>/` using the same date partition as
  legacy `t2i`, but with the multi-prompt filename stems
  `prompt_<prompt-index>_<variation-index>.png`.

For single-prompt mode, keep the existing behavior:

- `--out DIR` writes `<DIR>/<media_name>_<n>.png`.
- `--out` omitted writes under `$GFLOW_CLI_OUTPUT_DIR/images/<YYYY-MM-DD>/`
  through `image_output_path()`.

### 6.5 Error classes

Input-source and validation failures should fail before browser/API work and
exit as usage/configuration errors. Click usage errors are acceptable for
mutually exclusive source combinations, invalid seed-in-batch combinations, and
empty parsed prompt lists.

For per-prompt Flow failures, reuse existing `GFlowError` handling and
`gflow run` outcome semantics.

All new prompt-source parse and validation failures must happen before
`_resolve_profile`, output directory creation, browser launch, project creation,
or Flow API work. This includes invalid paths, non-regular files, oversized
files, invalid UTF-8, empty parsed prompt lists, prompt text over 2000
characters, too many prompts, mutually exclusive source combinations, and seed
in multi-prompt mode.

Generated prompt text must be passed to the Flow API unchanged. Prompt previews
in Rich tables, validation messages, and logs must be display-safe: escape Rich
markup and replace or visibly encode terminal control characters except normal
whitespace.

## 7. Acceptance criteria

Each item must be verifiable by an automated test.

1. Existing `gflow image t2i "one prompt"` behavior remains unchanged: it calls
   the legacy single-prompt path, preserves output naming, accepts `--seed` when
   `-n 1`, and uses existing summary rendering.
2. Single-prompt invocations that include the new batch flags, for example
   `gflow image t2i "one prompt" --fail-fast`, still route through the legacy
   single-prompt path with unchanged output naming and summary behavior.
3. `gflow image t2i "p1" "p2" "p3" --aspect 9:16 --model nano2` runs all three
   prompts in one `FlowApiClient` session and one Flow project.
4. Multi-prompt positional mode passes the same global `aspect`, `model`, and
   `count` to every prompt item.
5. Multi-prompt mode accepts the existing `gflow image t2i` model aliases,
   including `--model image4`, without changing the `gflow run --config` JSON
   schema.
6. `--prompts-file p.txt` with five lines containing three prompt lines, one
   blank line, and one whole-line `#` comment produces exactly three prompt
   items in source order, retaining original line metadata for diagnostics.
7. `--stdin` uses the same parser and execution path as `--prompts-file`.
8. Passing more than one prompt source, for example positional prompt(s) plus
   `--prompts-file`, fails with a clear usage error before profile resolution
   or browser/API work.
9. Passing `--seed` in multi-prompt mode fails with a clear usage error before
   profile resolution or browser/API work.
10. Multi-prompt mode rejects zero parsed prompts and more than 50 parsed prompts
    with clear errors.
11. Multi-prompt mode writes outputs using
    `prompt_<prompt-index>_<variation-index>.png`.
12. Multi-prompt mode without `--out` writes under the normal image date
    partition (`$GFLOW_CLI_OUTPUT_DIR/images/<YYYY-MM-DD>/`) and does not use
    the `gflow run` timestamped default output directory.
13. Multi-prompt mode supports `--continue-on-error` and `--fail-fast` with the
    same final exit-code semantics as `gflow run`.
14. The shared batch execution helper accepts command-specific presentation
    metadata such as project title and summary title, so `image t2i` does not
    render as `gflow run`.
15. The implementation preserves structured per-prompt outcomes containing at
    least prompt index, prompt text, status, output paths, and exit code.
16. `--prompts-file` rejects non-files, unreadable files, invalid UTF-8, and
    files larger than 512 KiB before browser/API work.
17. All prompt-source parse/validation failures occur before profile
    resolution, output directory creation, browser launch, project creation, or
    Flow API work.
18. Prompt text is sent to Flow unchanged, while prompt previews in terminal
    output/logs are display-safe against Rich markup and terminal control
    characters.
19. Docs in `docs/USAGE.md`, `README.md`, and `CHANGELOG.md` describe the new
    input surfaces, file format, error semantics, seed limitation, and output
    naming, and state that multi-prompt `t2i` can generate up to
    `prompt_count * -n` images (maximum 200).
20. Tests cover the required BDD scenarios from the kickoff prompt.

## 8. Out of scope

- Per-prompt overrides in shell multi-prompt mode.
- Any change to the `gflow run --config` JSON schema.
- Optional per-prompt `seed` in JSON config; tracked as follow-up.
- Multi-line prompt files.
- JSONL/YAML/TOML prompt files.
- Daemon mode, cross-command warmed sessions, or a persistent local session
  server.
- Database-backed operation history.
- Cross-account scheduling or account pools.
- New dependencies.

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Single-prompt behavior regresses by being routed through batch code | Keep a separate legacy path and add explicit regression tests. |
| Users accidentally run duplicate prompt sources and spend credits | Prompt sources are mutually exclusive; fail before browser/API work. |
| Seed semantics become confusing for multiple prompts | Reject `--seed` in multi-prompt shell mode; defer per-prompt seed to JSON config follow-up. |
| Shared batch extraction disrupts `gflow run` | Use TDD around existing `tests/cli/test_cli_run.py`; extract narrowly and keep the JSON schema untouched. |
| Absolute local paths leak in errors | Prefer source labels and basenames in user-facing errors; council security review must inspect this. |
| Prompt metadata is lost before future DB work | Represent shell prompts as batch items/outcomes with stable indexes. |

## 10. Required tests

At minimum:

- BDD: user passes three positional prompts -> three PNGs written, all share
  the same aspect/model.
- BDD: user passes `--prompts-file p.txt` with five lines (three valid, one
  blank, one `#` comment) -> three PNGs written.
- BDD: user passes both positional prompt(s) and `--prompts-file` -> clear
  usage error.
- BDD: user pipes prompts via `--stdin` -> batch runs identically to
  `--prompts-file`.
- BDD: old single-prompt callers (`gflow image t2i "one prompt"`) work
  identically to v0.5.0a1.
- BDD: user hits the 50-prompt upper bound -> clear error.
- Unit: line parser skips blank/comment lines and preserves inline `#`.
- Unit: line parser strips a UTF-8 BOM at stream start and retains source line
  metadata for later validation errors.
- Unit: multi-source validation happens before profile resolution.
- Unit: `--seed` in multi-prompt mode is rejected before profile resolution.
- Unit: invalid prompt file path, non-regular file, invalid UTF-8, oversized
  file, zero prompts, prompt >2000 chars, and >50 prompts fail before profile
  resolution and before output directory creation.
- Unit: batch item conversion assigns `output_filename="prompt_<idx>"` and
  preserves prompt text/model/aspect/count.
- Unit: `--model image4` is accepted in shell multi-prompt mode.
- Unit: multi-prompt output without `--out` uses the normal image date
  partition rather than `gflow run`'s timestamped default.
- Unit: prompt previews escape Rich markup and terminal control characters
  while raw prompt text passed to the API is unchanged.
- Unit: `--continue-on-error` and `--fail-fast` are passed through to the shared
  batch runner.
- Unit: single-prompt `--fail-fast` and `--continue-on-error` are inert and keep
  the legacy path.
- Regression: existing `gflow run --config` tests still pass unchanged.

## 11. Council review requirements

After this spec is approved, dispatch:

1. **Claude code-reviewer** - architectural soundness, scope creep, locked
   decision consistency, and reuse of existing batch machinery.
2. **Claude security-reviewer** - untrusted prompt files, stdin handling,
   path/error hygiene, and prompt-content escaping.
3. If `gemini` CLI is installed locally, a third headless Gemini review for
   cross-model perspective.

Each review writes under
`docs/superpowers/plans/2026-05-14-shell-multi-prompt/` and returns one of:
`PROCEED-AS-IS`, `MINOR-EDITS`, or `MAJOR-REVISION`.

Address all `MAJOR-REVISION` findings before writing the implementation plan.

## 12. Self-review

- [x] Goal stated in one sentence.
- [x] All three input surfaces enumerated with flag/arg shapes.
- [x] All six kickoff design questions answered.
- [x] Seed behavior clarified beyond the kickoff's open questions.
- [x] Metadata/future database concern captured without adding DB scope.
- [x] Existing locked decisions referenced verbatim.
- [x] Acceptance criteria are numbered and test-verifiable.
- [x] Out-of-scope items are explicit.
- [x] No `TBD` / `TODO` placeholders.
- [x] No `gflow run --config` schema changes.
- [x] Council minor edits applied: model alias compatibility, parser metadata,
  prompt-file bounds, display safety, preflight validation, project-title
  parameterization, output-dir regression, and fan-out docs.

---

End of design spec v1.
