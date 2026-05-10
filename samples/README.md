# `samples/captured/` — sanitised reference traffic from a real Flow session

These JSON files are the **ground truth** for what `gflow-cli` sends to and receives from `aisandbox-pa.googleapis.com` and `labs.google/fx/api`. Each one was captured during a live discovery run, then sanitised: UUIDs → `<UUID>`, bearer tokens → `<REDACTED_BEARER>`, image bytes → `<BASE64_IMAGE_BYTES_LEN_N>`, emails → `<EMAIL>`.

Use them when:
- Implementing or extending a route in `src/gflow_cli/api/`
- Writing contract tests in `tests/api/`
- Debugging a "this used to work" regression after a Flow update

## Files

| File | Route | Notes |
|---|---|---|
| `01_upload_image.json` | `POST /v1/flow/uploadImage` | Multipart-style body: `{clientContext, imageBytes(base64)}`. Response includes `media.name` (asset UUID) + `workflow.metadata.displayName` (filename echo). |
| `02_batchAsyncGenerateVideoText.json` | `POST /v1/video:batchAsyncGenerateVideoText` | T2V example. Body includes `mediaGenerationContext.batchId`, `clientContext` (with `recaptchaContext.token` — required, see below), `requests[]` with `aspectRatio`/`textInput.structuredPrompt`/`videoModelKey`/`seed`. |
| `03_batchCheckAsyncVideoGenerationStatus.json` | `POST /v1/video:batchCheckAsyncVideoGenerationStatus` | Body is just `{media: [{name, projectId}]}`. Response carries `mediaStatus.mediaGenerationStatus` and a `video.operation.name` once started. |
| `04_archive_workflow.json` | `PATCH /v1/flowWorkflows/{id}` | Soft-delete via `metadata.archived = true`. Used by `clear_flow_library`. |
| `05_createProject.json` | `POST labs.google/fx/api/trpc/project.createProject` | tRPC route. Different host. Body: `{json: {projectTitle, toolName: "PINHOLE"}}`. Response wrapped in `result.data.json.result.projectId`. |
| `06_batchGenerateImages.json` | `POST /v1/projects/{projectId}/flowMedia:batchGenerateImages` | T2I (no seed images). **Synchronous** — response includes `media[].image.generatedImage.fifeUrl` (signed Google CDN URL, short-lived). `projectId` lives in the URL path, not the body. Per-request fields: `imageModelName: "NARWHAL"` (the UI's "Nano Banana 2"), `imageAspectRatio` (symbolic), `structuredPrompt`, `seed`, `imageInputs: []`. Top-level body adds `useNewMedia: true`. |
| `07_batchGenerateImages_seeded.json` | same | I2I variant — exercises seed image refs, aspect `LANDSCAPE_FOUR_THREE`, and parallel-call quantity. `imageInputs[]` is populated with `{imageInputType: "IMAGE_INPUT_TYPE_REFERENCE", name: "<media-uuid>"}` where `name` came from `01_upload_image.json`. **Multi-image quantity (x2/x3/x4) is N parallel POSTs**, not a batched request — same `batchId` shared, different `seed` and fresh reCAPTCHA token per call. |

## Critical observations

**reCAPTCHA token requirement (route 02):**
The `clientContext.recaptchaContext.token` field is a fresh reCAPTCHA Enterprise token (~3000 chars, starts `0cAFcWe…`). It is:
- Minted client-side by `grecaptcha.execute(siteKey, {action: ...})`
- Single-use
- Expires in ~2 minutes

This is why `gflow-cli` keeps a Playwright persistent context open even for "API-driven" calls — we need to mint a fresh token via `page.evaluate("grecaptcha.execute(...)")` per generation request. The architecture is hybrid: Playwright for auth + reCAPTCHA token mint, REST (`page.request`) for everything else.

**Model selection (route 02):**
`videoModelKey` encodes the full variant: `veo_3_1_t2v_fast_portrait` = Veo 3.1 / text-to-video / fast tier / portrait orientation. I2V uses the same shape: `veo_3_1_i2v_fast_portrait`. We map our CLI flags (`--model fast|quality`, `--aspect 9:16|16:9`) to the right key.

**Aspect ratios are symbolic (route 02):**
`VIDEO_ASPECT_RATIO_PORTRAIT` not `"9:16"`. We translate at the client edge.

**Polling protocol (route 03):**
The status response includes `video.operation.name` once the gen kicks off — store it; same value can be used to fetch the rendered mp4 via `getMediaUrlRedirect?name=<name>`.

**Headers (all aisandbox-pa routes):**
- `authorization: Bearer <SAPISIDHASH>` — auto-attached by Playwright when same-origin cookies are present.
- `content-type: text/plain;charset=UTF-8` — yes, even though the body is JSON. Don't change to `application/json` or the server 400s.

## Sanitisation policy

If you re-run the discovery script (in the Compiled Growth monorepo) and want to refresh these samples, **always run them through the sanitiser first**. Specifically scrub:

- `imageBytes` (base64) — replace with `<BASE64_IMAGE_BYTES_LEN_N>`
- `authorization` headers — replace token with `<REDACTED_BEARER>`
- All UUIDs — replace with `<UUID>` (we don't want to leak project IDs that could correlate to a Google account)
- reCAPTCHA tokens — keep them as a reference (they're already expired by the time you commit)
- emails — replace with `<EMAIL>`

The script that does this lives in the Compiled Growth monorepo at `python/workers/google-flow-worker/_extract_samples.py` (or run the inline Python that produced these — see the relevant gflow-cli commit message for the snippet).

## License

Same as the parent repo: MIT. The data here is wire-format documentation, not Google's intellectual property — it describes how a public Flow web client talks to a public Google service.
