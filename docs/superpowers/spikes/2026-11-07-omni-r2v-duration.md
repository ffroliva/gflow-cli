# Omni ingredients duration — 2026-11-07

## Question and prior evidence

Does the 8-second migrated R2V guard measured with Veo 3.1 Lite on 2026-09-06
also apply to explicit Omni Flash? Source base: `0cc97c201d9566e26e00b48e4428160ce16cef2b`.

Pre-registered reading: an R2V key with the exact attached identity retained is
candidate evidence for a model-specific exception. A T2V key or missing identity
means keep the guard. No capture is inconclusive, not permission to remove it.

## Method and observations

Used the existing production-submit capture harness, through the application's
SDK-owned profile and an operation-owned tab. No new browser owner, uploads or
asset deletions. The attachment was an existing named character. The diagnostic
applied the normal settings at 8s then selected the requested duration directly,
bypassing only the SDK's duration refusal for measurement. Its fetch/XHR wrapper
blocked batchexecute before network transmission. The current fetch body is a
ReadableStream: decode it with Response.text(), not String(body). Initial captures
that read `[object ReadableStream]` were inconclusive and excluded.

| Requested seconds | Captured RPC | Model key | Exact character ID retained |
| --- | --- | --- | --- |
| 4 | MZZa6b | abra_r2v_4s_360p | yes |
| 6 | MZZa6b | abra_r2v_6s_360p | yes |
| 8 | MZZa6b | abra_r2v_8s_360p | yes |
| 10 | MZZa6b | abra_r2v_10s_360p | yes |

The UI readback kept Ingredients selected and the requested duration checked.
Raw captures contain project/account data and stay local; no tokens or identities
are included here. No video generation or credit spend occurred.

## Root transition and regression

A valid explicit Omni R2V duration reaches `apply_video_settings`, selects Omni,
and then hits the unconditional R2V != 8 refusal before attachment or Send.
That guard generalized Veo's measured limitation to every model. The correction
exempts only explicit Omni; unknown/sticky models and Veo keep the safe policy,
and omitted duration still pins 8. Reference identity checks are unchanged.

The new unit regression failed at 4/6/10 before the fix; 8 passed. The live
BDD settings regression also failed at 4 before the fix and passed all four
examples after it. It restores 8s and never types a prompt or sends anything.

## Verification boundary

This proves the settings binding and the character-bearing prepared wire body.
It does not prove completed video duration/quality, another account/cohort,
720p, or primary-image-plus-character binding. No paid generation is authorized
by this diagnostic. Veo's earlier finding is preserved, not remeasured.
