# An Omni extend grows the source clip in place (#1184)

**Date:** 2026-11-08 · **Cost:** 12 credits for one extend, then a read-only listing ($0)
**Script:** `scripts/dev/spike_extend_1184_listing.py` · **Account:** the maintainer profile, served flow.google.com

## Question

After one paid `gflow video extend` with the Omni key `macaron_cookie_extend_4s` (see the
[#1180 spike](2026-11-08-extend-no-scene-create-omni-key.md)), the downloaded file was
byte-identical to a fresh download of the **source** media id. Did gflow fetch the wrong
clip, or did Flow change the source?

## Evidence

1. **Local copies.** The source clip `a69e…` downloaded on 2026-11-07 measured **7.93 s**
   (2.3 MB). After the extend, the same media id downloaded as **11.83 s** (5.7 MB), with a
   first frame identical to the source's. The extend's own output had the same hash.
2. **Project listing (`Zzl0ze`, read only).** It held **one** record for the clip: created
   2026-11-07 09:48 UTC and modified **2026-11-08 10:18 UTC**, the time of the extend. Every
   other one of the 28 records had a modified time equal to its created time, and none was
   created near the extend. No separate extension and no earlier version was listed.
   Positive control: the source id was present in the captured listing.
3. **Credits** went from 642 to 630 (12) for the one extend.

## Conclusion

On this account and day, the Omni extend grows the source clip **in place**, under its
own media id, by about 3.9 s, which matches the key's `_4s`. gflow downloaded the correct
media; the old contract (a separate extension clip with a new id, plus a new scene) does
not hold for this key.

## What changed in gflow

- The catalog notes the extend on the existing row (`metadata_json.extended_by`,
  `extend_unit_cost`, `extended_at`) and leaves its status and model alone, instead of
  failing on `UNIQUE(profile_name, flow_media_id)`. A started id the catalog does not hold
  is still inserted as before.
- `--json` reads the added/billed seconds from the key's `_<n>s` token; any other key keeps
  the measured Veo 7/8.
- The help, the plan line and USAGE describe the in-place result as a dated observation.
- The paid e2e accepts either contract: a separate 6.5–8.5 s clip, or the same id with a
  file longer than the extension alone.

## Verified on the fix tree

A second paid extend (12 credits, 630 → 618) through `gflow video extend --json` on the
fix branch: exit 0, `seconds_added`/`seconds_billed`/`request.duration` = 4, no
`extend_record_failed`, and the clip grew again in place, from 11.83 s to 15.87 s. The
existing catalog row kept `MEDIA_GENERATION_STATUS_SUCCESSFUL` / `veo_3_1_lite` and gained
`extended_by: macaron_cookie_extend_4s` with a timestamp (`extend_unit_cost` is null:
flow.google.com reports no price).

## Open

- Whether Flow keeps the pre-extend version anywhere (an undo or history) was not
  observed; the listing shows none.
- A page that orders a Veo extend key was not observed on this day.
