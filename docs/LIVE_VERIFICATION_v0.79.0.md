# Live verification — v0.79.0

Evidence gathered 2026-09-18 against real Flow, on profile `ci-probe`
(`compiledgrowth…`), a profile Flow serves from **flow.google.com**. Project
`23c192d5-3aea-4be0-b85f-dbf369e647be`.

Cost: **zero credits.** Image generation draws on Flow's separate per-model daily
quota; no video was generated.

---

## 1. `model_name_type` is no longer echoed on the migrated host (#789)

The release's claim: on `flow.google.com`, gflow reports no model rather than
echoing the requested one back, because the `ogiZ0b` reply carries no model field.

**Layer 1 — e2e, both surfaces.**

```
GFLOW_CLI_E2E_PROFILE=ci-probe GFLOW_CLI_E2E_PROJECT=23c192d5-… \
  pytest -m e2e_image tests/e2e/test_migrated_host_e2e.py -v

tests/e2e/test_migrated_host_e2e.py::test_e2e_t2i_runs_on_a_moved_account PASSED
tests/e2e/test_migrated_host_e2e.py::test_e2e_mcp_i2i_runs_on_the_migrated_host PASSED
2 passed, 6 deselected in 86.46s
```

The CLI path and the MCP twin are separate surfaces and were exercised separately.

**Layer 2 — the field, in a populated envelope.** `gflow image t2i --json`:

```json
{
  "status": "ok", "command": "image t2i", "model": "NARWHAL", "count": 1,
  "images": [{
    "media_name": "4814faa7-caf6-4a3f-857e-3699d82b00fd",
    "workflow_id": "c3c25fe2-1402-4117-b187-5db52e7e74d8",
    "seed": 124935770,
    "model_name_type": null,
    "aspect_ratio": "IMAGE_ASPECT_RATIO_PORTRAIT",
    "dimensions": {"width": 768, "height": 1376},
    "is_signed_url": true
  }]
}
```

Every sibling field is populated, which is what makes the `null` a result rather
than an empty envelope. The top-level `"model": "NARWHAL"` is the **request echo**
and is unchanged — the user still sees what they asked for; what is gone is the
claim that Flow confirmed it.

> **Instrument note.** The first read of this envelope was flat (`d["model_name_type"]`)
> rather than into `images[]`, and returned `None` for *every* field including
> `media_name` and `local_path`. That was an instrument error and was discarded, not
> reported. A `null` is only evidence when its neighbours are not.

**Layer 3 — magic bytes and size.** `443,433` bytes at
`~/Downloads/gflow-cli/images/2026-09-18/4814faa7-…_1.jpg`, header
`ff d8 ff e0 00 10 4a 46 49 46` (JPEG/JFIF). `sha256
b53e2a6f7b65c51ac3fa29894e227741d80a354417cd6389dac911e9c7ec35b3`.

**Layer 4 — structlog.** `client.persistent_context_launch` (channel `chrome`),
`client.context_cookie_state` (`flow_session_cookie_present: true`,
`google_sapisid_present: true`, 57 cookies), `client.account_locale_resolved`
(`en`, `html_lang`).

**Layer 5 — user-confirmable.** The image is in the Flow project and openable in
the browser; `gflow data media 4814faa7-…` shows the catalog row with its
`local_path`.

---

## 2. `gflow data download` — verified for video, **fails for images** (#865, #877)

The command recovers a billed asset whose download failed. Verified for video in
the v0.78.0 cycle (two orphaned clips recovered byte-exact, $0).

**This cycle it was tested on an image, and it did not work.** The asset above was
deleted locally and recovery attempted:

```
gflow data download 4814faa7-caf6-4a3f-857e-3699d82b00fd --profile ci-probe
→ exit 7 after 45s
  "migrated host: no signed media URL for 4814faa7-… within 45s of opening its
   clip route. The media id may belong to another project, or the clip may have
   been moved to trash in Flow."
  -> Check request payload parameters or retry with a simpler prompt text.
```

All four claims are false: the image was twelve minutes old, in that project,
not trashed, with an intact catalog row — and a download command has no prompt.
`migrated.recover_navigate` fired and no `as29s` record followed, because that
record is a **video** mechanism and `_await_signed_record` had no kind check.

Filed as **#877** and **fixed in this release.** Re-run after the fix, same media
id, same profile:

```
gflow data download 4814faa7-caf6-4a3f-857e-3699d82b00fd --profile ci-probe
→ exit 11, immediately, no browser
  "Media '4814faa7-…' is image, and `gflow data download` recovers video only.
   The signed URL it needs comes from the record Flow emits when a clip's own
   route loads, and an image's route does not carry one."
  -> "Nothing to retry — this is a capability gap, not a fault. Open the image
      in Flow and save it from there, or pass a video media id."
```

> **The fix's own first version was wrong, and every test was green.** The detail
> was right but `ConfigurationError`'s class-default remediation took over —
> *"check that the transport name is registered via `make_transport()`"* — one
> misleading hint swapped for another. The live re-run above is what caught it;
> the tests only read the detail. They assert the remediation now.

The deleted file was restored from a pre-deletion copy; `sha256
b53e2a6f…` matches, nothing was lost.

---

## 3. `AisandboxAuthError` remediations (#803) — NOT live-verified. Blocker named.

The three corrected raise sites need Flow to answer an aisandbox route with a
non-JSON body or a token-less session. Attempting to reach one on this profile
never got there:

```
gflow character list --profile ci-probe --project 23c192d5-…
→ exit 7, WireFormatError, route projectInitialData, HTTP 404
  "Flow RPCs have been deprecated and disabled. Flow has migrated to
   https://flow.google.com."
```

The labs tRPC route refuses first, so `_fetch_access_token` is never called. No
profile available here reaches the condition, and it cannot be forced.

Covered offline by 4 new unit tests plus the existing `test_credits_http.py`
suite (78 passed). **The live leg is outstanding** and needs a profile in the state
that produces it — recorded here rather than omitted, and it is a blocker, not a
substitute for a run.

That attempt also surfaced a separate misleading remediation, filed as **#875**:
the 404 above tells the user to *"retry with a simpler prompt text"* for a command
that has no prompt.

---

## 4. Hermetic e2e in CI

Five `tests/e2e/` files are route-intercepted and need no account, profile,
network or credits. They were excluded from every CI run by `addopts`, so the
regression tests for #593, #773, #859 and #860 ran nowhere. Now wired into the
`test (3.13)` job.

Local run before wiring: **27 passed** (8 + 19) in ~4 min against real headless
Chromium.

---

## Not verified this cycle, with reasons

| Item | Why not |
|---|---|
| #803 live remediation | No profile reaches the condition (§3) |
| `data download` for video | Verified in the v0.78.0 cycle, not re-run here; §2 covers the image gap found this cycle |
| Any non-`en` locale | No non-`en` profile available |
| labs.google arm | No account here is served labs — 6/6 visits returned HTTP 308 to flow.google.com in the 2026-09-14 survey |
| Video generation | Deliberately not run; no change in this release touches the video submit path |
