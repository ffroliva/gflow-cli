# Where does flow.google.com keep the caption the `@` picker searches? (2026-11-12)

**Question.** #546 heals a stale catalog caption from the project listing. On an account
Flow serves flow.google.com, the labs listing `flow.projectInitialData` answers
`WireFormatError: Flow RPCs have been deprecated and disabled. Flow has migrated to
https://flow.google.com.` (direct call, denon82, 2026-11-12). The resolver therefore
raises, and a renamed image fails with exit 32 (`tests/e2e/test_refresh_on_miss_e2e.py`).
Which source on the new app carries the caption the picker finds an image by?

**Script.** [`scripts/dev/spike_migrated_listing_captions.py`](../../../scripts/dev/spike_migrated_listing_captions.py),
$0 (nothing submitted). Each candidate string is typed into the `@` picker and counted as
the caption only if the picker then offers **that image's tile**, matched by its
`/asb/<token>`, never by a label.

**Control.** A caption gflow recorded at generation time (`assets.metadata_json.display_name`)
is probed the same way. A negative reading counts only when the control is positive.

## Observed

| Run | Project / image | Control finds the tile | Result |
|---|---|---|---|
| 1 | 5-image project, image 3 min old | (no control) | no `Zzl0ze` field found it: **unmeasured** |
| 2 | 5-image project `06bf16d6…`, image `59109d8b…` | **yes** ("Red wooden block on table") | `Zzl0ze` fields (lengths 65, 23, 65): **none** finds the tile |
| 3 | same | yes | grid tile attributes: only `alt`, a **localized generated description** (Portuguese on this account), not the caption |
| 4 | same | yes | empty `@` query: **5 options, the target listed, its title equal to the caption** |
| 5 | 42-image project `7fa97443…`, its oldest image | no | `Zzl0ze` returned 21 image records; the tile is **not in the grid**, and the empty `@` query lists 15 options without it |

## Verdict

- **`Zzl0ze` does not carry the searchable caption** (run 2, with the control positive).
  Porting `fetch_project_listing` to `Zzl0ze` would not restore #546.
- **The page itself does.** With nothing typed, the `@` picker lists the project's
  assets with their caption as the option title (`_OPTION_TITLES_JS`), and the option is
  matched by token to the grid tile (run 4).
- **The reach is the first page** (runs 4 and 5): the grid and the empty-query picker show
  the recent assets only (21 and 15 seen). That is the same reach the in-place binder
  already has, because `await_existing_references` needs the grid tile, so a page-based
  heal adds no new limit. An image outside the first page fails the same way as today.

## What this means for the port

The resolver must read the caption **on the composer's own page**, inside
`MigratedComposer.reference_existing`. A second page checkout from the CLI's resolver
while the composer holds the page could deadlock when the pool has one page. The healed
name still has to reach the catalog (`sync.source == "refresh"`, honouring
`history_prompts`), which today happens inside the CLI resolver.

Not measured: whether the empty-query picker pages or scrolls further, and how the picker
orders options (recency or otherwise) beyond these two projects.
