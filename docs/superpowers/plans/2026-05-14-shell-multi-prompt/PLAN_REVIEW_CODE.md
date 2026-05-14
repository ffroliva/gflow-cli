# Code Architecture Review: shell multi-prompt `t2i`

## Verdict

MINOR-EDITS

The proposed `image_batch.py` extraction is architecturally sound: it keeps the legacy single-prompt `t2i` path separate, gives `gflow run --config` and shell multi-prompt `t2i` a shared one-session/one-project runner, and can preserve the `image4`/`imagen4` alias split because `Model.from_cli()` accepts both aliases while `cli_run.py` can keep JSON schema validation local.

The plan should be edited before implementation because several snippets will fail Click/test semantics or miss required preflight validation.

## Findings

### High: prompt-source validation errors are raised as `ConfigurationError` but not converted at the Click boundary

Spec C2/C7 require selected cases to be clear Click usage errors before profile/API work, and acceptance criteria pin pre-profile failures for multi-source and seed cases. The plan's parser helpers raise `ConfigurationError` from `image_batch.py` (`PLAN.md:891-903`, `PLAN.md:914-930`), but the `t2i` multi-prompt branch calls `read_prompt_file()` / `parse_prompt_lines()` directly before `run_with_handlers` (`PLAN.md:1161-1184`). In current `cli_image.py`, `run_with_handlers` only wraps the legacy async call after profile resolution (`src/gflow_cli/cli_image.py:274-293`), and Click will not automatically turn `ConfigurationError` into exit 2.

This also contradicts the plan's own tests: `test_t2i_rejects_empty_stdin_before_profile_resolution()` expects `result.exit_code == 2` with `catch_exceptions=False`, but `parse_prompt_lines()` would raise uncaught `ConfigurationError`.

Suggested edit: keep `image_batch.py` Click-free, but add a `try/except ConfigurationError as exc: raise click.UsageError(str(exc)) from exc` around all prompt-source parsing/conversion in `cli_image.t2i`, or explicitly change the expected exit code and wrap with the existing GFlow handler. The former better matches spec language for usage errors.

### High: positional multi-prompt mode is not validated for 1..50 prompts or 1..2000 characters

Spec C5 caps shell multi-prompt mode at 50 before browser/API work (`spec:168-176`), and acceptance criterion 10 requires rejecting more than 50 parsed prompts (`spec:374-375`). The plan tests a 51-positional-prompt BDD scenario (`PLAN.md:357-360`, `PLAN.md:458-462`), but `prompt_items_from_texts()` simply enumerates all positional prompts without `_validate_prompt_count()` or per-prompt length checks (`PLAN.md:612-631`). That path is used for positional multi-prompt input (`PLAN.md:1177-1184`).

Suggested edit: make `prompt_items_from_texts()` validate total prompt count and each prompt length before constructing `BatchPromptItem`s, with source/index in the error. Add/keep a direct CLI test for 51 positional prompts asserting `_resolve_profile` is not called.

### Medium: async runner patch in the proposed CLI wiring test is wrong

`run_image_batch()` is async (`PLAN.md:639-648`) and the `t2i` implementation calls it via `asyncio.run(...)` (`PLAN.md:1205-1215`). The test snippet patches it as a plain `MagicMock` returning `[]` (`PLAN.md:254-258`). `asyncio.run([])` will fail because a list is not a coroutine.

Suggested edit: use an async fake or `AsyncMock` returning `[]`, for example:

```python
async def _fake_run_batch(**kwargs):
    return []

monkeypatch.setattr("gflow_cli.cli_image.run_image_batch", _fake_run_batch)
```

### Medium: `image_batch.py` extraction changes `BatchPromptItem` shape and should preserve `gflow run` import compatibility deliberately

`cli_run.py` currently defines `BatchPromptItem` locally with no `index`, `source_label`, or `line_number` fields (`src/gflow_cli/cli_run.py:76-214`), while the plan moves the class to `image_batch.py` and adds required `index` (`PLAN.md:576-585`). The plan adapts `BatchConfig._parse_prompt()` correctly (`PLAN.md:778-792`), and existing tests import only `BatchConfig` from `cli_run.py`, but this is still a package-level API change for anyone importing `gflow_cli.cli_run.BatchPromptItem`.

Suggested edit: explicitly state whether this is acceptable private API movement. If compatibility is desired, re-export `BatchPromptItem` from `cli_run.py` after import so old internal imports keep working.

### Medium: summary rendering should keep `gflow run` behavior stable while fixing prompt escaping

The shared renderer parameterizes the title (`PLAN.md:717-747`), which satisfies the spec requirement that `image t2i` not render as `gflow run` (`spec:383-385`). However, replacing `cli_run.py`'s renderer means existing `gflow run` output will now escape/truncate prompts via `safe_prompt_preview()` and escape errors (`src/gflow_cli/cli_run.py:355-389`, `PLAN.md:724-740`). This is likely acceptable, but it is not called out as a deliberate behavior change.

Suggested edit: add one note under Task 3 that `gflow run` summary output may become terminal-safe but should retain the same columns, title, aggregate counts, skipped semantics, and exit code behavior. Keep current `tests/cli/test_cli_run.py` assertions for `"3/3 succeeded"`, `"1 skipped"`, and output files.

### Low: output path helper is sound, but add the planned regression test before relying on it

`resolve_t2i_batch_output_dir()` uses `image_output_path(output_root, job_id="prompt_0", index=0).parent` (`PLAN.md:754-758`), which correctly maps to `$GFLOW_CLI_OUTPUT_DIR/images/<YYYY-MM-DD>/` under the existing helper (`src/gflow_cli/paths.py:73-80`). This is a reasonable reuse. The key risk is accidentally falling back to `gflow run`'s timestamped `out/<UTC>` default (`src/gflow_cli/cli_run.py:231-238`).

Suggested edit: keep the explicit default-output-dir regression test named in the spec (`spec:378-380`) and assert the path includes `images/<today>` and not `out/<timestamp>`.

## Open Questions / Assumptions

- I assume `gflow_cli.cli_run.BatchPromptItem` is not a supported public API. If it is, re-export it from `cli_run.py`.
- I assume positional prompt text should use the same 1..2000 character bound as file/stdin prompts because the shell multi-prompt path converts it into the same batch item model.
- I assume using Click exit 2 for prompt-source parse/validation failures is intended, based on the plan's own CLI and BDD tests.

## Suggested Plan Edits

1. Add Click-boundary conversion for `ConfigurationError` raised by prompt parsing/validation in `cli_image.t2i`.
2. Validate positional multi-prompt count and prompt length inside `prompt_items_from_texts()`.
3. Fix async `run_image_batch` mocks in unit and BDD snippets.
4. Decide whether to re-export `BatchPromptItem` from `cli_run.py` for internal compatibility.
5. Add a Task 3 note that shared summary rendering must preserve `gflow run` columns/counts/exit behavior while making prompt previews safe.
