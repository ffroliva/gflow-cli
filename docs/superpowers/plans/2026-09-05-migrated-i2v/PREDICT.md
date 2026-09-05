# Predict: port i2v (`--initial-frame` / `--end-frame`) to the migrated flow.google.com host

Run 2026-09-05 after v0.67.0. Persona reports: `tmp/predict/01..05-*.md` (local-only).

## Verdict: CAUTION
**Confidence:** 7/10

## Summary
The port is transport-only and fits the existing adapter (`run_video` already takes the i2v DTO;
CLI, MCP, codec, chain need no key changes). Every persona lands on the same blocker: how a frame
gets INTO a migrated project has never been observed — no Frames-submode DOM, no upload RPC, no
i2v `YhhmEf` key, no evidence the picker exposes a media UUID. One $0 probe answers all of it and
decides which of two first slices (in-project UUID vs local upload) is cheaper. Two open external
PRs (#672, #669) rewrite the same file and should land first.

## Persona findings

### Architect — CAUTION (7/10)
Clean adapter growth: `migrated_can_serve` (`migrated_composer.py:86-95`), the `run_video` guard
(`:540-546`), one new `attach_frames` step between `apply_video_settings` and `send_prompt`, a
submode `_select`. Labs binding (`_attach_frame` `ui_automation_video.py:1841`,
`_resolve_frame_slot` `:1965`, `_attach_frame_by_media_id` `:2039`) is labs DOM + aisandbox wire —
reusable intent, not mechanism; no shared "FrameBinder". Credit-safety gap: no migrated twin of
`_assert_i2v_route` (`:3650-3720`) — must assert the captured `YhhmEf` body carries an i2v model key,
not `abra_t2v_8s`. Six places claim "t2v only" and turn false on merge.

### Security / reCAPTCHA — CAUTION (7/10)
HARD RULE: never replay the upload RPC ourselves (`page.request.post`, `f.req`/`at=` builders,
`context.cookies()` on flow.google.com, in-page `fetch`, reading `g-recaptcha-response`) — STOP if
the design drifts there. Required shape: `expect_file_chooser` → `set_files` → observe the app's own
reply, as labs does (`ui_automation_video.py:2152,2170`). Keep the UUID gate before selector
interpolation (`api/video.py:208-218`); display names only via `filter(has_text=re.compile)`. Log
`image_uploaded target=/status=`, never filenames; failure screenshots through the incident bundle,
not ad-hoc PNGs. Download trust path unchanged. Do not borrow any labs helper that drags in
`_mint_recaptcha_token`.

### Performance / Playwright — CAUTION (7/10)
The credit-burning race is upload-after-submit (Angular enables `arrow_forward` on prompt text while
the upload is in flight) — #672's enable-poll does not cover it, and labs' upload wait is soft
(`:2179-2183`, do not port). Pre-click three-way proof: upload reply observed, structural thumbnail
in the composer, `page.on("request")` backstop on the `YhhmEf` body. The migrated route never
dismisses the post-handoff changelog modal (`_dismiss_blocking_overlays` is labs-only,
`ui_automation_video.py:3969-3975`) — today a raw `TimeoutError` at `migrated_composer.py:170`.
Add a 90 s stage watchdog over attach→type→click. Resolve any picker overlay by anchor, never
`.first/.last` (#665). Constants: `FRAME_PICKER_OPEN_S=8`, `FRAME_UPLOAD_S=60` hard,
`FRAME_COMMIT_HIDDEN_S=15`, `FRAME_THUMB_VISIBLE_S=5`, `ATTACH_STAGE_S=90`. Concurrency unchanged
(video serialized under `_generate_lock`).

### CLI UX / MCP — GO (8/10)
Transport-only: CLI `cli_video.py:1244-1256`, MCP `tools.py:693-709`, codec `codec.py:248-267`
already carry i2v end to end; the only gates are `migrated_can_serve` and the `run_video` guard.
Zero new exit codes (2 / 32 / 27 / 7 / 23 / 11 / 36 cover every new failure), `retryable` unchanged.
~25 sentences in 15 files become false (`docs/MCP.md:86`, `docs/USAGE.md:558-564,1708`,
`CONFIGURATION.md:364,368`, `README.md:127`, `llms.txt:3`, `KNOWN_ISSUES.md:17,19,56`,
`errors.py:681-682,700`, `config.py:551-552`, `.env.template:185-188`, `_common.py:96-99`,
`skills/gflow-cli/SKILL.md:274`, `INDEX.md:151`, `ARCHITECTURE.md:439`, `PROJECT_STATUS.md:150`).
Unsupported frame kind → `migrated_can_serve` False (unmoved keeps labs) / exit 36 with a naming
detail on moved accounts, never 2 or 11. Chain link ≥1 is local-path i2v and routes through the
same dispatch — the port fixes chains on moved accounts for free; check `project_id` binding
(`chain.py:255`).

### Devil's Advocate — CAUTION (6/10)
No ADR or KNOWN_ISSUES entry blocks it. Counter-arguments: (1) UUID-only i2v (#287 shape) may serve
the reporter with zero upload code IF the migrated picker lists project assets; (2) migrated t2i may
be the smaller delta (mode radio in the same pane, same `YhhmEf`/`jwpduf`/`as29s`) and covers both
#673 reporters; (3) one probe answers both ports; (4) #672, #669, #673 (+#644) should land first —
same file, same overlay bugs; (5) upload-by-HTTP is a trap (#644, rotating `__Secure-1PSIDTS`).
Labs lesson to carry: `set_input_files` on the hidden input adds to the library but does not bind
the slot (`ui_automation_video.py:295-299`).

## High-confidence risks (2+ personas)
1. Upload/attach mechanism on the migrated host is unobserved (all five).
2. Credit-burning submit with an unbound or half-uploaded frame (Architect, Performance, Security).
3. Doc/remediation sentences asserting "t2v only" become false on merge (Architect, CLI-UX).
4. Same-file conflicts with #672 / #669; changelog modal untreated on the migrated route
   (Performance, Devil's Advocate).

## Conflicts resolved
- **UUID-first (Architect, DA) vs local-upload-first (Performance, CLI-UX):** neither is chosen by
  preference. The probe measures both; slice 1 is whichever binds with fewer unknowns, slice 2 is
  the other. The plan carries both as alternatives with a decision task.
- **i2v vs t2i order (DA):** i2v stays the committed target (public commitment on #639, reporter's
  batch need). The probe captures the image-mode keys in the same $0 session so a t2i slice can
  follow at low cost; it is not foreclosed.
- **Hybrid upload-by-HTTP (raised and rejected by Security + DA):** not on the table.

## Required mitigations before EXECUTE
1. **$0 recon spike** (extend `scripts/dev/spike_migrated_submit_capture.py`, aborted submit, no
   billing): Frames-submode radios and slot DOM; the add-media affordance and whether a
   `filechooser` fires; the upload RPC (rpcid/host, response media id, timing); whether the project
   library exposes a known `gflow data media` UUID structurally; the i2v `YhhmEf` model key on an
   aborted submit with a bound frame; image-mode keys; the changelog modal DOM.
2. **Credit safety:** hard upload wait + structural thumbnail + `YhhmEf` body assertion before/at
   click (`WireFormatError` on a t2v key), `ATTACH_STAGE_S=90` watchdog.
3. **Token boundary:** file chooser only; any self-made upload request is a STOP.
4. **Overlays:** anchor-resolved picker/pane; changelog modal dismissal on `ensure_editor` (or
   land it via #673 first).
5. **Dispatch predicate** gates on the exact frame kind slice 1 supports; the other kind exits 36
   with a naming detail.
6. **Doc sweep** of the ~25 "t2v only" sentences, run check § 1b with `text-to-video` as the symbol.
7. **Sequence:** rebase after #672 / #669 / #673 land (or open the branch and rebase before review).

## Recommended next step
Run the $0 recon on the moved `ffroliva` profile against project `300f5260-…` (probe image, aborted
submit), write `docs/superpowers/spikes/2026-09-05-migrated-frames-attach.md`, then `/gflow:scenario`
with the slice the probe selects.
