# Live verification — v0.78.0

**Date:** 2026-09-17 · **Host:** Windows 11 Pro 10.0.26200 · **Flow host served:** `flow.google.com`
**Cost:** $0 — no Veo credits spent. Image runs used Flow's daily image quota; every video
submit was intercepted with `route.abort()` before it left the browser.

Step 4b of [`/gflow:release`](../skills/release/SKILL.md). Every user-facing change in this
release is exercised against the real thing, or its blocker is named. Nothing is listed as
"unverified" that could have been verified — see AGENTS.md § The Iron Law.

Profiles: `ci-probe` (served `flow.google.com`, app mounts, labs session token present) and
`ffroliva` (aspect enumeration). Issue: [#864](https://github.com/ffroliva/gflow-cli/issues/864),
closing [#561](https://github.com/ffroliva/gflow-cli/issues/561).

---

## 1. Why this release exists — labs' project route is retired

`scripts/dev/spike_create_project_401.py`, 3 attempts per profile, 4 profiles, through
`FlowApiClient.create_project` (the production path):

| profile | labs session token | host served | `createProject` |
|---|---|---|---|
| `ci-probe` | yes | flow.google.com `/` | **404** ×3 |
| `denon82` | yes | flow.google.com `/about` | **404** ×3 |
| `flavio.oliva` | no | flow.google.com `/` | **401** ×3 |
| `promo-denon82` | no | flow.google.com `/about` | **401** ×3 |

0 successes in 12. The 404 body is Google's own words:

```
Flow RPCs have been deprecated and disabled. Flow has migrated to https://flow.google.com.
```

The 401 is the same retired route reached by a session with no labs API token. gflow reported
it as `AuthExpiredError` — *"Run `gflow auth login`"* — which no login could fix.

## 2. `gflow project create` + `gflow project rename` — VERIFIED

`tests/e2e/test_project_create_e2e.py::test_cli_project_create_and_rename_land_on_flow`,
driving the real commands as a subprocess.

| Layer | Evidence |
|---|---|
| Artifact | a new project on flow.google.com, id `57c099ee-…`-shaped UUID from `project create --json` |
| Field value | `project rename` → Flow's header `flow-editable-text` input read back **the new title** after a fresh navigation |
| Wire | create = rpc `jHPbke` → `[project_id, [title]]`; rename = rpc `o8DA4` → `[title]` |
| Structlog | `project.labs_route_refused status=404` → `migrated.project_created project_id=<uuid>` |
| User-confirmable | the projects appear in Flow's grid, titled `gflow-e2e-864-*` |

## 3. `gflow image t2i` with no `--project`, at `--aspect 3:4` — VERIFIED

One run covers both halves of the release: creation, and the newly ported 3:4 radio.

| Layer | Evidence |
|---|---|
| File count | 1 PNG written by the CLI |
| Magic bytes / size | > 10 000 bytes on disk, opened from `--json` `images[].local_path` |
| Dimensions | width/height within 0.02 of 3/4 — a real 3:4 image, not a silently substituted aspect |
| Structlog | `migrated.project_created` → `migrated.image_settings_applied` |
| Wire | `ogiZ0b` submit owned by the page, completed record parsed |

## 4. MCP `gflow_generate_image` with no `project` — VERIFIED (separate surface)

The MCP twin runs different code (tool → queue payload → `worker/codec.py` → daemon), so it
is verified on its own: `status: "completed"`, files on disk > 10 KB, project created on
flow.google.com in the same run.

**One failure, re-tested rather than explained away.** The first run created the project and
then failed parsing the `ogiZ0b` reply (813 bytes, no completed record). It did not reproduce:
an A/B on the same profile — existing project 9:16, fresh project 9:16, existing project 1:1
twice — all succeeded, and the same e2e passed on unchanged code. Recorded as an external
flake, not a known issue.

## 5. Video with no project — VERIFIED credit-free, both surfaces

`FlowApiClient.generate_video` (the path every CLI video command takes) and the MCP
`gflow_generate_video` twin, each with the submit intercepted by `route.abort()`:

| Layer | Evidence |
|---|---|
| Structlog | `migrated.project_created` before any submit |
| Wire | a batchexecute submit rpc was attempted and aborted before leaving Chrome |
| Field value | MCP run titled its project from `project_name` — `gflow-e2e-864-mcp-video-*` in Flow |
| User-confirmable | Flow's own page shows *"Failed — You have not been charged for this generation"* for the aborted submit |

A billed end-to-end clip was **not** run: the contract under test is which project the run
lands in and that the submit is reached, both readable before Flow acts. Stated rather than
implied.

## 6. Aspect radios — MEASURED

`scripts/dev/spike_migrated_aspect_radios.py --profile ffroliva`, settings pane only:

- **Image:** `crop_16_9` 16:9 · `crop_landscape` 4:3 · `crop_square` 1:1 · **`crop_portrait` 3:4** · `crop_9_16` 9:16
- **Video:** `crop_16_9` 16:9 · `crop_9_16` 9:16 (plus 360p/720p, 4/6/8/10 s, x1–x4)

The 2026-09-08 enumeration had four image radios, which is why 3:4 was refused with exit 36.
All five are driven now. Video offers exactly the two aspects gflow already supported.

## 7. MCP profile contention — VERIFIED offline, not live

Measured through the real `gflow mcp run` entry point: with the variable unset, the loaded
setting was `0.0` while the environment said `180` — the defect this release fixes. After the
fix the loaded setting is `180`, and a value set in the environment or a `.env` still wins.
Serialisation of two concurrent calls on one profile is covered by
`tests/mcp/test_mcp_profile_contention.py`.

**Not live-verified:** driving two MCP clients against one profile simultaneously needs two
real clients and a second profile holder; the behaviour is contention timing, not a Flow
surface. Named here rather than left blank.

## 8. `video i2v --initial-frame <local file>` on the migrated host — VERIFIED

The defect this release fixes ([#860](https://github.com/ffroliva/gflow-cli/issues/860))
made every i2v run on a `flow.google.com` account exit 32 *before* submit: the Frames
picker renders a `mat-icon` ligature next to the file name, a locator reads both nodes as
one string, and the anchored match could never hold. Verified by running the real command
against live Flow, not by replaying the fix's own e2e.

Project `cg-s025-bracket-sage` (`339f65ee-…`), profile `ffroliva`, model `omni-flash`,
`--duration 10`, one clip per scene of a real story.

| Layer | Evidence |
|---|---|
| File count | **ten mp4 files** written by the CLI, one per scene, none orphaned |
| Magic bytes / shape | every clip `h264 + 720x1280`, read back with `ffprobe` |
| Field value | duration **10.005 s** — `--duration 10` was honoured, not silently dropped |
| Structlog | `migrated.frame_uploaded` → **`migrated.frame_bound`** → `migrated.submit_observed` → `migrated.download`, **ten/ten runs**. `frame_bound` is the event that could not fire before this release |
| User-confirmable | the clips play, and each one animates the approved still it was given |

**The negative is the point.** The identical command on v0.77.1 exits 32 with *"the frame
picker lists no asset named 's1-cbbc098b.jpg'"* while its own diagnostic lists
`'images1-cbbc098b.jpg'`. That is what blocked the story this release unblocks.

## 9. Flow's promo modal — NOT OBSERVED this cycle, stated rather than implied

[#859](https://github.com/ffroliva/gflow-cli/issues/859) moves the dismissal ahead of the
first click. The dismissal code path ran on **every** run in section 8, but **no promo
modal appeared on any of them**, so no dismissal was observed and nothing here proves the
ordering fix. The modal is served on a freshly loaded editor at Flow's discretion; it
cannot be summoned on demand.

It is covered instead by the $0 route-intercepted e2e shipped with the fix
(`tests/features/blocking_dialog_dismissal.feature`), which mounts the modal 250 ms after
load and places the trigger beneath the backdrop, so the *backdrop* is the occluder — the
arrangement in which a mocked dialog passes against the bug. Recorded as unobserved rather
than folded into section 8's green, which is what the ledger is for.

## 10. `gflow docs` — VERIFIED offline, which is the whole contract

[#861](https://github.com/ffroliva/gflow-cli/issues/861) is a read-only, no-network,
no-account command, so "live" here means *from the built artefact, with no checkout*.

| Layer | Evidence |
|---|---|
| Artifact | `gflow docs` lists **127 topics** from the pages shipped inside the wheel |
| Field value | `gflow docs --search "end-frame interpolation"` → 3 matches, each as `docs/FILE.md:LINE` **with the line**, not a file name |
| Field value | `gflow docs configuration` prints the page as raw Markdown (it pipes) |
| Structlog | an unknown topic raises `ConfigurationError` (exit 11) carrying a **docs** remediation hint, not the transport-registry default |
| User-confirmable | `gflow docs usge` → *"no documentation topic named 'usge' — did you mean usage?"* |
| Wheel | `tests/integration/test_docs_ships_in_wheel.py` builds a wheel, installs it into a scratch venv with no checkout, and runs the command there |

## 11. Not verified, with reasons

- **An account served `labs.google`.** **Blocker: no account.** All four profiles here are
  served flow.google.com (`labs.google/fx/tools/flow` answers HTTP 308 on every one), so no
  profile exists that could exercise the labs arm. It is unchanged and keeps its own error
  path. Anyone holding such an account can settle it — see [#639](https://github.com/ffroliva/gflow-cli/issues/639).
- **An `/about`-served account (`denon82`).** **Blocker: the account has no Flow access** —
  its app never mounts, which is [#756](https://github.com/ffroliva/gflow-cli/issues/756), not
  something this release can drive. Project creation there is expected to fail with the
  existing landing diagnosis.
- **A non-`en` locale.** **Blocker: no account** in a non-`en` locale is available here. The
  anchors are structural by construction (custom element + `mat-icon` ligature, no display
  text), which is what the locale-invariance rule in AGENTS.md requires; tracked with the rest
  of the migrated-host matrix in [#639](https://github.com/ffroliva/gflow-cli/issues/639).
