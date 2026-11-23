# Spike: direct HTTP image transport — Gate 0 (WAF, latency, aspect and count slots)

**Date:** 2026-11-22 · **Profile:** `denon82` (Flow served `flow.google.com`)
· **Base:** `chore/spike-direct-image-with-browser-token` (`ee7fa2d7`)
· **Cost:** 21 images (daily image quota, zero credits); captures $0 (route-aborted)
· **Script:** [`scripts/dev/spike_direct_image_gate0.py`](../../../scripts/dev/spike_direct_image_gate0.py)
· **Plan:** [`docs/superpowers/plans/2026-11-22-direct-http-image-transport/PLAN.md`](../plans/2026-11-22-direct-http-image-transport/PLAN.md) — Gate 0
· **Raw evidence:** `scripts/dev/_spike_out/spike_direct_image_gate0_*.jsonl` (gitignored; tokens redacted to their length)

## Question

Before any production code: does Flow accept a run of consecutive direct `ogiZ0b` POSTs
built the way the transport would ship them, how does their latency compare with the UI
transport on the same profile, and which frame slots carry aspect ratio and image count?

## Method

One warm `FlowApiClient` on one project. Every direct POST has the shape that would ship:

- frame built from scratch (no captured template), `inner[4] = [batch_uuid]`;
- `at` / `f.sid` / `bl` from `flow_wire._shell_tokens` (the shell GET the credits read uses);
- user agent from `flow_wire._user_agent` (the profile's Chrome major version);
- a fresh `IMAGE_GENERATION` reCAPTCHA token minted on the project page for every POST;
- one POST per generation, 120 s budget, never retried.

The pre-registered readings are in the script's docstring.

## Results

### Finding 1 — the on-disk cookie store is unreadable while Chrome holds the profile

Run 1 stopped before any POST: `auth.cookies.read_cookies_for()` raised
`CookieStoreUnreadable` with the browser open. This is the documented behaviour of that
reader (`src/gflow_cli/auth/cookies.py:333` probes the jar because Chrome holds it
exclusively on Windows). The direct transport needs the browser open to mint tokens, so
it **cannot** use the on-disk reader. Run 2 took the cookies from the live browser
context (`page.context.cookies(["https://flow.google.com"])`, 26 cookies); the first
spike had already shown Flow accepts those.

### G0.1 — WAF escalation: none at N=10

| Measure | Value |
|---|---|
| Consecutive direct POSTs | 10 |
| HTTP 200 + image generated | **10 / 10** |
| RPC errors / refusals | 0 |
| Images per POST | 1 (768 x 1376, JPEG) |
| UI control run needed | no (no refusal occurred) |

No 429, 403 or `UNUSUAL_ACTIVITY` at N=10 back-to-back on one profile.

### G0.2 — latency, direct vs UI transport (same profile, prompt, model, project)

| Arm | n | min | median | mean | max |
|---|---|---|---|---|---|
| Direct: mint + POST to reply | 10 | 16.7 s | **18.6 s** | 18.4 s | 21.4 s |
| — mint only | 10 | 0.12 s | 0.13 s | 0.15 s | 0.22 s |
| — download (signed URL, separate) | 10 | 0.68 s | 0.87 s | 0.87 s | 1.14 s |
| UI transport, warm: `generate_image` call to records | 10 | 19.0 s | **20.7 s** | 20.9 s | 22.9 s |

The direct path is about **2.2 s (≈10 %) faster per warm image**. Server-side generation
dominates both arms. The arms ran back to back (direct first), not interleaved, so a
drift in Flow's own generation time between the two blocks is not excluded. The UI arm's
model is the same wire model: `NARWHAL` is sent as `BELUGA` on flow.google.com
(`_mc_common.py:535`).

### G0.3 / G0.4 — aspect and count captures

Four route-aborted captures ($0) of Flow's own `ogiZ0b` submit, driven through the real
UI transport on the same project (default, 16:9, count=2, default again):

| Capture | POSTs | Entries in `inner[1]` | `entry[3]` | `entry[4]` | Other slots |
|---|---|---|---|---|---|
| default (9:16) | 1 | 1 | random int | **2** | — |
| 16:9 | 1 | 1 | random int | **3** | identical to default |
| count = 2 | 1 | **2** | a different random int per entry | 2 | identical; one shared `inner[4]` batch UUID |
| default again | 1 | 1 | random int | 2 | identical to the first |

- **`entry[4]` is the aspect-ratio enum**, not the image count: portrait (9:16) is `2`,
  16:9 is `3`. The other three aspects (`SQUARE`, `4:3`, `3:4`) are not measured.
- **Count is the number of entries in `inner[1]`**, in ONE POST. Each entry carries its
  own `entry[3]`, so that slot is per image (a seed-like value), not a session counter.
- The UI's entries carry three extra slots, `[12]`/`[13]` (two random UUIDs) and
  `[14] = 4`. The 12-slot from-scratch entry is accepted without them (spike `16ab3d4b`
  and the 10 POSTs above).

A first capture run measured count = 1 for "count = 2" because it called
`generate_image`, which forces `count=1` (`_flow_generation.py:604`). The rerun above
uses `generate_images_batch`, the path that reaches Flow's x{N} selector.

## Readings

1. **The WAF does not escalate on 10 consecutive direct image POSTs** from one profile.
   Gate 0's ≥ 5 criterion passes.
2. **Latency is a modest gain, not the case for the transport.** The case is robustness:
   no selectors, no locale-sensitive DOM, no contenteditable prompt editor.
3. **Cookies come from the browser context, not the disk.** `read_cookies_for` cannot
   serve this transport on Windows while the minting browser is open.
4. **`FlowApiClient`'s own mint cannot serve this transport.** `_mint_recaptcha_token`
   calls `raise_if_migrated` first (`_flow_generation.py:424`) and defaults to the labs
   action `imageGeneration`. The transport keeps `uses_page_owned_image_recaptcha() ->
   True` and mints `IMAGE_GENERATION` on the project page itself.
5. **`flow_wire._execute_direct_rpc` cannot serve this transport.** Its 5 s total budget
   is shorter than one generation, and it maps a timeout to a fallback reason, which the
   plan forbids after send. The transport reuses `_shell_tokens` and `_user_agent` with
   its own 120 s POST.

6. **Phase 2 has its slots.** Aspect is `entry[4]` (two of five values measured); count
   is N entries in one POST, each with its own `entry[3]`. Count > 1 on the direct path
   is still unsent: whether Flow returns N images for N 12-slot entries is the first
   Phase 2 measurement.

### Phase 2 follow-up (same day, same profile and project)

| Probe | Method | Result |
|---|---|---|
| Count = 2 on the direct path | one POST, two 12-slot entries, own `entry[3]` each | HTTP 200, **2 images**, 23.9 s |
| 16:9 on the direct path | one POST, `entry[4] = 3` | HTTP 200, **1376 x 768**, 18.0 s |
| 1:1 / 4:3 / 3:4 | route-aborted captures of Flow's submit ($0) | `entry[4]` = **1 / 5 / 4** |

| In-project I2I (one `--ref`) | Flow's own submit, read by a passive request observer | `entry[2] = [[media_id, null, null, null, 1]]`; `entry[8]` starts with a chip `[null, [[media_id, display_name]]]`, then the text with **two** leading spaces |
| One `@Character` | the same | `entry[10] = [[entity_id]]`; `entry[8]` starts with a chip `[null, [null, null, [entity_id, name]]]`, then the text with **one** leading space |

The referenced submits needed a passive observer: the UI transport routes its own image
submit to guard the reference, and that route runs before the spike's abort, so those two
submits reached Flow (two images, zero credits). A bare `@Name` mention resolves to the
character's name only (`reference_entity_names`); the composer picks the id in the browser.

So the full `entry[4]` table is 1:1 = 1, 9:16 = 2, 16:9 = 3, 3:4 = 4, 4:3 = 5; 9:16 and
16:9 are proven by direct POSTs, the other three by capture only.

**Gate 0 verdict: PASS.** WAF ≥ 5 consecutive (10/10), latency recorded, aspect and
count slots identified.

## What was not measured

- Escalation beyond N=10, across several profiles, or over a long batch.
- Interleaved direct/UI timing, and cold-start timing (first generation of a process).
- Whether the on-disk cookies work on macOS or Linux with Chrome open (Windows only here).
- I2I references, character mentions, non-default aspect on the direct path (Phase 2).
