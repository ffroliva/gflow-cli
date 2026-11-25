# Spike: `direct_http` multi-count (`count 2-4`) with a reference or character (#1455)

**Date:** 2026-11-25 · **Profile:** `<profile>` (served `flow.google.com`)
**Cost:** 11 image generations (daily image quota, zero Veo credits): 2 UI captures + 7 direct POST proofs + 1 setup `maseQ` upload ($0) + 1 `character create --image` ($0)

## Question

`direct_http` (#1442) previously capped requests carrying an in-project `--ref` or a named character (`--reference-entity` / `@Name`) at `count = 1`. Before widening `_references_measured` and `_characters_measured`, #1455 required verifying:

1. When Flow's UI submits `-n 2` with one in-project `--ref`, and `-n 2` with one `--reference-entity`, does each entry in `inner[1]` repeat `entry[2]`/`entry[10]` and the chip in `entry[8]`, or only the first entry?
2. Does a direct `ogiZ0b` POST built with that measured shape return N images?

## Observations

### 1. Live Flow UI captures (`GFLOW_CLI_TRANSPORT=ui_automation`, `count=2`, `model=NARWHAL -> BELUGA`)

| Run | POSTs | `len(inner[1])` | `entry[2]` / `entry[10]` across `entries[0..1]` | `entry[8]` chip across `entries[0..1]` | `entry[3]` (seed) | Returned |
|---|---|---|---|---|---|---|
| In-project `--ref`, `-n 2` | 1 | 2 | Repeated on both (`entry[2] = [[media_id, null, null, null, 1]]`) | Repeated on both (`[[[null, [[media_id, display_name]]], ["  <prompt>"]]]`) | Distinct random int per entry | 2 images |
| `--reference-entity`, `-n 2` | 1 | 2 | Repeated on both (`entry[10] = [[entity_id]]`) | Repeated on both (`[[[null, [null, null, [entity_id, name]]], [" <prompt>"]]]`) | Distinct random int per entry | 2 images |

Both UI submits shared one `inner[4]` batch UUID and matched what `frame_builder.build_ogiz0b_body()` already produces for `len(entry_seeds) > 1`.

### 2. Live direct `ogiZ0b` POST proofs (`run_direct_images`)

| Shape | Requested | Returned | Distinct media IDs | Dimensions | `transport` | Mint + POST |
|---|---|---|---|---|---|---|
| In-project `--ref`, `-n 2` | 2 | 2 | 2 | `768x1376` x 2 | `direct_http` | 19.4 s |
| In-project `--ref`, `-n 3` | 3 | 3 | 3 | `768x1376` x 3 | `direct_http` | 26.1 s |
| In-project `--ref`, `-n 4` | 4 | 4 | 4 | `768x1376` x 4 | `direct_http` | 21.8 s |
| `--reference-entity`, `-n 2` | 2 | 2 | 2 | `768x1376` x 2 | `direct_http` | 24.4 s |
| `--reference-entity`, `-n 3` | 3 | 3 | 3 | `768x1376` x 3 | `direct_http` | 25.9 s |
| `--reference-entity`, `-n 4` | 4 | 4 | 4 | `768x1376` x 4 | `direct_http` | 27.1 s |
| `@Name` mention (resolved via `read_project`), `-n 2` | 2 | 2 | 2 | `768x1376` x 2 | `direct_http` | 22.1 s |

## Conclusions & unmeasured gaps

- **Measured:** Flow's own multi-image submit repeats `entry[2]`, `entry[10]`, and the `entry[8]` chip identically across every entry in `inner[1]`, and direct `ogiZ0b` POSTs built by `build_ogiz0b_body()` return 2/2, 3/3, and 4/4 distinct `direct_http` images for both in-project `--ref` and `--reference-entity`, plus 2/2 for `@Name` mention resolution (which sends the exact same resolved entity frame).
- **Not measured:** multiple refs, multiple characters, or a ref + character combined (still routed to `ui_automation`); and subprocess CLI E2E without an HTTP proxy (this host requires `GFLOW_CLI_BROWSER_PROXY`, which routes `DirectHttpTransport` to `ui_automation` per `docs/DIRECT_TRANSPORT.md`).
