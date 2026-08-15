# Picker tile `alt` evidence (#529)

**Verdict:** Flow's media picker exposes a short Flow-authored caption in a
generated tile's `img[alt]`, not the generation prompt. Therefore a prompt-derived
`search_hints` tier cannot match that tile on the observed picker surface. Proposal 3
from #529 is refuted rather than deferred.

## Evidence

- Picker capture date: 2026-07-27
- Catalog comparison date: 2026-08-15
- Media ID: `995784c1-7948-44e5-acd9-7e47334cd602`
- Raw picker `alt`: `Brass key on marble surface`
- Catalog prompt: `The EXACT same weathered brass key from the reference image,
  unchanged in shape, patina and bit pattern, now resting alone on a plain white
  marble surface, soft daylight from above, minimal clean background.`
- Comparison: the strings differ; the picker value is a short caption.

The raw `debug_picker_dom_0341b04a.json` artifact came from the `kiln_ember` #393
verification output. Its tile HTML identifies the same media UUID in
`media.getMediaUrlRedirect?name=<uuid>` and carries the caption in `alt`. The prompt
was read from the local gflow catalog with `queries.get_asset_prompt()` for that UUID.

A second generated tile independently corroborates the reading:

- Media ID: `7529789a-dad0-4d9d-bc79-b171d862855e`
- Raw picker `alt`: `Brass key on wooden bench`
- Catalog prompt: `A single weathered brass key resting on a dark wooden workshop
  bench, warm lamplight from the left, deep shadows, shallow depth of field,
  photorealistic cinematic photography, film grain. Vertical composition.`

This agrees with the committed live ledger in
[`LIVE_VERIFICATION_v0.45.0.md`](../../LIVE_VERIFICATION_v0.45.0.md), which records
the same caption-not-prompt contract from both picker DOM and HAR evidence.

## Follow-up

Issue [#541](https://github.com/ffroliva/gflow-cli/issues/541) records that the
video path's prompt-derived `search_hints` tier also lacks a demonstrated match path,
and that #529 proposal 3 must not be implemented as designed.
