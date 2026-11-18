# A second extend continues the clip's newest workflow (#1346)

**Date:** 2026-11-18 · **Cost:** $0 (the page's `fZytfe` is aborted in the browser)
**Script:** `scripts/dev/spike_extend_1346_second_extend.py` · **Account:** the maintainer profile, served flow.google.com

## Question

After one in-place extend ([#1184](2026-11-08-omni-extend-grows-source-in-place.md)), the
next `gflow video extend` of the same clip is aborted by gflow's route guard: the page's
`fZytfe` slot 1 names a workflow other than the one gflow read for the media from `Zzl0ze`.
Which workflow does the page continue, and where does the listing say so?

## Method

The production `run_extend` click path runs with its route guard replaced by a recorder.
Every `batchexecute` request is logged by rpc id and the UUIDs it names (never the body,
which carries a page-minted token); any `fZytfe` is aborted. Every `Zzl0ze` reply is kept
and each UUID of the `fZytfe` request is looked up in it.

Positive control: one `fZytfe` was captured, and the listing held the media id.

## Evidence

On a clip extended twice before (one media id):

1. The listing's workflow records (payload slot 2) held **three** records with that media
   id at slot 2: the original generation (`veo_3_1_t2v_lite`), the first extend and the
   second extend (`macaron_cookie_extend_4s`, each naming its parent workflow), listed in
   the order first extend, original, second extend.
2. The media's own entry (payload slot 1: `[media, null, null, [title, created, null,
   null, CURRENT, ..., modified], project]`) named the **second extend's** workflow at
   slot 3, item 4.
3. The page's `fZytfe` slot 1 named that same workflow. No `rqZuUc` or `uwAyfb` was sent.
4. gflow's `source_record` took the first record with the media id, the first extend's
   workflow, so the guard refused the request.

Across all 12 media entries in that listing, the entry's slot 3, item 4 named the media's
only workflow record for the 9 clips never extended, and the newest record for the 3
extended clips; for 2 of those 3 the first-listed record was an older one.

## Conclusion

The workflow the page continues is the one the clip's media entry names as current. The
guard's expectation is that workflow, accepted only when it is a workflow record of the
same media id; any other workflow, including an older version of the same clip, is still
aborted before Flow sees it.

## Open

- `gflow data download`, and the download fallback after an extend whose reply carries no
  video URL, pick a clip's record by media id from the clip route's own `as29s` reply,
  not from this listing; whether that reply also lists one record per extend was not
  measured here ([#1352](https://github.com/ffroliva/gflow-cli/issues/1352)).
