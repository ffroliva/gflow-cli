# Manifest `ref` / `reference_entity` are dropped, all forms (#913) — 2026-10-01

**Question.** Does a manifest row's `ref` / `reference_entity` reach Flow, on the
surfaces that parse manifests?

**Setup.** `develop` @ 773f6ef9, Windows 11, profile `ci-probe`, host served
`flow.google.com`. Script: `scripts/dev/spike_batch_ref_dropped.py`. Signal:
`migrated.references_attached` (`migrated_composer.py:2073`). Cost: 4 images of quota,
0 credits.

## Observed

| Arm | Result |
|---|---|
| control: `image i2i --ref <local.jpg>` | `references_attached` fired once (then an unrelated 30 s API deadline, exit 9): the signal works |
| `image batch` (3 rows) | exit 1 before any row: `raise_if_migrated(at="image_batch_unported")`, `ui_automation.py:3370` |
| `run --config` (same 3 rows) | **exit 0, 3 images saved, `references_attached` 0** |
| offline capture of the request `run_one_image_prompt` builds | `refs=()`, `ref_paths=()`, `reference_entities=()` for `batch:0`, `./cat.png` (nonexistent, not rejected) and an entity UUID |

The spike's "submits" counter read 0 because it counted the video event name
(`migrated.submit_clicked`); that is a defect in the spike, not a finding. The saved images
show the rows ran.

## Reading

- **Wider than #913 as filed.** Every form is accepted and dropped, not only `batch:N`:
  `parse_batch_item_dict` takes any string (`image_batch.py:376-382`), and neither builder
  (`run_one_image_prompt` `:503`, `_to_request` `:882`) reads either field. A local path is
  not even checked for existence.
- **Reach.** On flow.google.com users reach the drop through `gflow run --config`. `image
  batch` is refused there before it runs, so it reaches the drop only on an account served
  labs (not observed here; labs answers 308 on every profile we hold). Both commands are
  MCP-exempt, so there is no MCP surface.
- **The record says otherwise.** CHANGELOG (v0.52.0, #317) advertises the fields, and
  `LIVE_VERIFICATION_v0.52.0.md` row 5 records a PASS that no code at that tag could
  produce: `git grep item.ref v0.52.0 -- src` finds it only in the uncalled
  `resolve_batch_dependencies`. `tests/test_image_batch_references.py` tests only that
  uncalled function, which is why the suite stayed green.

## Not measured

The labs arm of `image batch`; whether Flow accepts a prior generation's media id as a
reference on flow.google.com (a media-UUID `--ref` is not ported there, exit 36). A local
file reference **is** ported on both hosts, and a batch downloads each row before the next,
so `batch:N` could be wired as the parent's downloaded file. **Superseded below:**
re-uploading duplicates the image in the project; the follow-up measures referencing the
generated image directly.

## Follow-up: reference row 0's generated image with no upload (owner's design)

Requirement (owner): an image the batch already generated is in the project; row N must
reference it by the handle Flow's reply returns, never by re-uploading the downloaded copy
(duplicates clutter the project). Two $0/2-image spikes, `ci-probe`, flow.google.com:

`scripts/dev/spike_mention_existing_media.py` (no submit, $0), on a project with two
same-prompt generations:

- The composer's `@` picker **does** offer generated images, under a caption Flow writes
  ("Matte grey ceramic sphere", "Ceramic sphere on white"), not the prompt. A prompt-text
  query matched nothing.
- The two same-prompt images got different captions.
- A media mention chip carries `data-reference-type="media"` and an **empty**
  `data-entity-id`; option markup carries no UUID. The DOM cannot prove which image bound.
- Mentioning an existing image made 0 requests (first pick) and 1 `as29s` (picker search);
  no upload.

`scripts/dev/spike_batch_ref_existing_media.py` (2 images), both rows through the real
`FlowApiClient.generate_image` path; row 1's `attach_references` swapped to "mention row 0
by its reply `display_name`, return row 0's media id":

| | Result |
|---|---|
| row 0 reply | `media_id` + `display_name` "Red apple on wooden table" (Flow's caption) |
| unfiltered `@` list right after row 0 | did **not** include it (same 6 older items) |
| row 1 (search by caption via `_mention_by_name`) | **completed**; production's `_image_body_problem` passed |
| row 1 `ogiZ0b` submit body | **carries row 0's media id** |
| rpcids during row 1 | 19, no `maseQ` (upload): nothing added to the project |

**Reading.** Buildable on this host today: the reply's `display_name` is the search key,
the reply's `media_id` is the identity, and the existing body check refuses the run if the
caption bound any other image (fail-safe, never silently wrong). Not measured: two images
with the same caption (the body check would refuse, not disambiguate); a row with
`count > 1` (which of its images `batch:N` means is a design decision); the labs host.

## Gate (PLAN Task 0): four claims the transport rests on

`scripts/dev/spike_ref_gate.py`, `ci-probe`, flow.google.com, 2026-10-01.

| Claim | Result |
|---|---|
| **#40 picker scope** | **Project-scoped.** In a new project, a caption that exists only in another project is not offered (0 options); control: the same search in the project holding it offers it. The picker dialog also shows a project selector set to the current project. |
| **#16 negative control** | **The body check discriminates.** Row 1 mentioned an unrelated image ("Teal origami crane on table") while declaring row 0's media id: refused, `WireFormatError` "submit body is missing … reference(s) 90879017…". |
| **#39 Enter on an empty picker** | **Does not submit** (no `ogiZ0b` within 8 s). `@` opens an asset-picker **dialog** with its own search box; Enter on "No assets found" does nothing and the dialog stays open over the composer, so the next composer click times out. Production's `_mention_by_name` retry clicks the composer after a miss without closing the dialog: a latent defect on exactly the lag path (#17). Fix: Escape before retrying. |
| **#17 search lag** | 3 searches for a just-generated image (spike row 1, negative row 1, chain row 1): all found on the first attempt (`mention_miss` 0). **Superseded by the live e2e (below):** the lag exists, per editor load. |

**#21 same caption, observed for real.** Flow's caption is sometimes the prompt verbatim
("a single red apple"). Two images with that caption existed in the project; the picker
listed the **older first** (timestamps 1790858420 vs …8466) despite its "Recent" label, the
chain mentioned the first option, and the body check refused. Binding by caption alone is
not reliable.

**The exact binder (measured, $0).** Each picker option's thumbnail is
`/asb/<token>`; each project-grid tile is `img[data-media-id=<uuid>]` with the **same**
token. Mapping option token → grid tile gives the option's media id:

| option | token | grid tile id |
|---|---|---|
| 0 | `ANqvLOZNWEQMo-…` | `90879017…` (older) |
| 1 | `ANqvLOaOnHmQZ6N0…` | `3f4272fd…` (newer) |

The project's asset list (`Zzl0ze`, fetched when the editor opens) carries the same
id ↔ token pairs (parsed structurally), so the DOM mapping is backed by the wire. Design:
search by the reply caption, then select the option whose token matches the parent's
grid tile; the `ogiZ0b` body check stays as the second line. Not measured: a project large
enough for the grid to virtualise tiles off-screen (gflow run projects hold ≤ 50 rows).

## Live e2e findings (2026-10-01, `tests/e2e/test_manifest_refs_bdd.py`)

Rows `0`, `1→0`, `2→0`, `3→1` through the real `gflow run --config`, `ci-probe`.

| Run | Row 1 (first child, right after its parent) | Cause, from the run's own log |
|---|---|---|
| 1 | failed: not in the grid | opened the editor 1 s after row 0 generated; the tile never appeared in 30 s of in-page polling; row 2 reloaded and found it |
| 2 | failed: not in the grid (after the in-page wait fix) | same; waiting in the page does not help |
| 3 | passed | reload-on-miss for the grid: one reload |
| 4 | failed: 0 picker options, 3 in-page searches | the picker search is also a per-load snapshot; row 2 found it after a reload |
| 5, 6 | passed (both) | reload-on-miss for grid and picker; one reload each; 6/6 references in place |

**Reading.** The grid and the `@` picker reflect what was indexed when the editor loaded.
A just-generated image appears after a reload, not after waiting. The transport reloads
the editor (and re-applies the settings, which a reload resets) until the reference is
mentionable, within a 90 s budget.


## Local files: uploads bind by identity too (PR C)

`scripts/dev/spike_upload_token_join.py` ($0), project holding two composer uploads
(`migrated-mcp-i2i-*.png`): both picker options map to grid tiles carrying a
`data-media-id` (`835690c8…`, `5afc9489…`), and each tile's id resolves back to the same
`/asb/` token (round trip true). So a local file uploaded once can be referenced in place
on later rows exactly like a generated image. Live: two rows naming one file → one upload
(`migrated.reference_uploaded`), two in-place attaches, the submit guard passed twice.
