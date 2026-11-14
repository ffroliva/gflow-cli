# Predict: split FlowApiClient behind a facade, plus a size ratchet (#1249 wave 1)

## Verdict: CAUTION (confidence 8/10)

## Summary

A pure move is low-risk for Flow traffic (no request body, header, selector or timing
changes), and the risk sits in the test seams instead. The tests patch private helpers on the
instance (`_mint_recaptcha_token` 15x, `_post_json` 10x, `_checkout_page`/`_checkin_page` 24x)
and module names on `gflow_cli.api.client` (`logger`, `routes`, `storage_path`,
`MAX_CONCAT_B64_LEN`, `_is_target_closed`). The split must keep every `self._x` call
resolving through the instance. That rules out composition and points to mixins.

## Persona findings

### Architect - CAUTION (8/10)
- Composition (`self._generation.generate_image`) needs every moved body rewritten from
  `self.` to `self._client.`, and private helpers patched by tests stop being seen. That
  breaks the "move, don't rewrite" rule.
- Mixins keep bodies byte-identical: `FlowApiClient` inherits surface classes, and attribute
  lookup on the instance still finds a patched `_post_json` first.
- A **linear** chain types cleanly under pyright strict without stub declarations:
  `_FlowSession` (`__init__` + primitives) <- `WorkspaceSurface` <- `GenerationSurface` <-
  `MediaEditSurface` <- `CharacterSurface`. `FlowApiClient` adds `_AccountBootstrap` and keeps
  the lifecycle in its own body. The chain order is the real dependency order (generation
  creates projects; character images download). That is the coupling the issue reports, and
  it is recorded for the provider port, not hidden.
- The new modules cannot import `client.py` at runtime (cycle). Helpers shared by several
  surfaces move into the base module, and `client.py` re-exports every name imported from it.

### Security / reCAPTCHA - GO (9/10)
- No token, cookie or header code changes; `_mint_recaptcha_token` moves verbatim.
- Redaction helpers (`_redact_*`) move with their users; their callers must keep calling
  them, which the existing redaction tests pin.

### Performance / Playwright - GO (9/10)
- MRO lookup over 6 classes costs nanoseconds per call. Import time: same code, more files.

### CLI and MCP UX - GO (9/10)
- No CLI option, MCP tool, payload key, exit code or JSON shape is touched.
- Log lines are unchanged: the structlog config has no `add_logger_name` processor, so a
  module's logger name never reaches output (`observability.py:101-121`).

### Devil's Advocate - CAUTION (7/10)
- "Mixins just move the god class into six files." Partly true: the instance still has every
  method. The gain is that each class has one responsibility, a file a reader can hold, and a
  gate that stops regrowth. A narrow port type comes with the provider port (wave 2+). That
  is when there is a second implementation to justify it (YAGNI).
- The 1,000-line class limit forces generation to split into generation + media edits;
  `CharacterSurface` splits out of workspace for the same reason. Accept it: these are real
  seams.

## High-confidence risks (2+ personas)

1. Tests that patch `gflow_cli.api.client.<name>` stop affecting moved code -> import-path
   edits, each listed in the PR (allowed by the goal).
2. A source-scan test (`tests/services/test_character_gen_no_direct_post.py`) reads
   `client.py` text to find `generate_character_image` -> its path must follow the method.

## Required mitigations before EXECUTE

- Characterization tests land first (PR 2): the public method set with signatures, the names
  importable from `gflow_cli.api.client`, and delegation results through the facade.
- Each patch-target edit is listed in the PR body; no test logic edits.

## Recommended next step

Phase 3: `/gflow:scenario` -> SCENARIO.md.
