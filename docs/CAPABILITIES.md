# Capabilities

`gflow capabilities` declares what this gflow can drive, so an agent or a pipeline can
plan instead of learning by trying and failing ([#1197](https://github.com/ffroliva/gflow-cli/issues/1197)).
gflow drives Flow through a browser and Flow's private API, so some operations that
API-based generation services offer have no equivalent here, and some depend on the
Flow frontend gflow drives.

```bash
gflow capabilities          # table
gflow capabilities --json   # the document below
```

MCP clients read the same JSON document from the resource **`capabilities://self`**,
except on a **no-spend server** (`gflow mcp run --no-spend`, `GFLOW_MCP_NO_SPEND=1`):
it registers no generate tool, so `generate.image`, `generate.video` and
`submit.no_wait` are `no` there, with a note saying why. Both surfaces serialize one
function (`gflow_cli.cli_capabilities.build_capabilities`) through one serializer
(`gflow_cli.json_output.dumps`). A unit test compares the two, and an e2e test runs the
real `gflow capabilities --json` in a subprocess and compares the parsed document with
the resource's.

## Document

The default setting (`GFLOW_CLI_FLOW_HOST=auto`). A test keeps this block equal to the
code's output.

```json
{
  "schema": 1,
  "adapter": "flow-browser",
  "host": "auto",
  "capabilities": {
    "generate.image": "partial",
    "generate.video": "partial",
    "submit.no_wait": "partial",
    "status": "partial",
    "wait": "partial",
    "download": "partial",
    "upload": "partial",
    "abort.local": "partial",
    "cancel.server": "no",
    "idempotent_submit": "no",
    "reconcile": "partial",
    "credits.balance": "partial"
  },
  "notes": { "generate.image": "...", "...": "..." }
}
```

| Field | Meaning |
|---|---|
| `schema` | Document version. Bumped only on a breaking shape change. |
| `adapter` | How gflow reaches Flow. Always `flow-browser` today: a real browser session plus Flow's private API. |
| `host` | The `GFLOW_CLI_FLOW_HOST` **setting** (`auto`, `flow.google.com` or `labs.google`), not the host Flow serves a profile. Under `auto` the host is chosen per request from what Flow serves, and the document does not check which (see [No call to Flow](#no-call-to-flow)). |
| `capabilities` | One value per key: `yes`, `no`, `partial` or `unknown`. |
| `notes` | One sentence or two per key. A `partial` always has one, saying what is missing. |

## Keys

| Key | Meaning | Today |
|---|---|---|
| `generate.image` | Text-to-image and image-to-image generation (`image t2i`/`i2i`/`batch`, `run`, `character create`; MCP `gflow_generate_image`). | `unknown` with `labs.google` (gflow's labs driver takes every mode, but whether Flow serves this account labs is not checked offline; exit 36 if it serves flow.google.com). Otherwise `partial`: on flow.google.com gflow drives t2i and i2i from local files and from images the catalog records in this `--project` ([#913](https://github.com/ffroliva/gflow-cli/issues/913), [#995](https://github.com/ffroliva/gflow-cli/issues/995)); Agent instructions, `image4`, `image batch`, other Flow media UUIDs, mixed refs and a character id without a name exit 36. The agent-only composer ([#799](https://github.com/ffroliva/gflow-cli/issues/799)) takes t2i only. |
| `generate.video` | Video generation (`video t2v`/`i2v`/`r2v`/`chain`/`extend`, `movie run`; MCP `gflow_generate_video`). | `unknown` with `labs.google` (as above). Otherwise `partial`: on flow.google.com gflow drives t2v, i2v from a local frame, r2v from local refs or a named character, and a single-segment extend; a frame or reference by UUID or `@Name`, characters on t2v/i2v, and extend chains, `--scene` and `--seed` exit 36. The agent-only composer takes t2v only. |
| `submit.no_wait` | Submit and return an id without waiting for the result. | `partial`: MCP `wait=false` only, polled with `tasks/get` (needs a client with the SEP-2663 Tasks extension); no CLI `--no-wait` yet ([#1194](https://github.com/ffroliva/gflow-cli/issues/1194)). `no` on a no-spend server. |
| `status` | Ask how a submitted generation is doing, by id. | `partial`: MCP `tasks/get` only (SEP-2663 client); no CLI `status <id>` yet (#1194). |
| `wait` | Block until a submitted generation finishes, by id. | `partial`: CLI generation commands block until done and MCP `tasks/get` carries a finished result; no CLI `wait <id>` yet (#1194). |
| `download` | Fetch a finished asset to a local file. | `partial`: generation commands download on completion; `data download` (MCP `gflow_download_media`) recovers a failed **video** download only, and refuses an image id (exit 11, [#877](https://github.com/ffroliva/gflow-cli/issues/877)). |
| `upload` | Upload a local file to Flow for reuse. | `partial`: `image upload` (CLI only) prints an asset id; the MCP generate tools upload local reference paths themselves; no top-level `upload` yet ([#1200](https://github.com/ffroliva/gflow-cli/issues/1200)). On flow.google.com an uploaded id is not taken as a video frame or reference (exit 36), and as an image reference only when the catalog records it in the same `--project`. |
| `abort.local` | Stop gflow's own wait or job. Does **not** stop Flow. | `partial`: Ctrl-C stops a CLI run and MCP `tasks/cancel` (SEP-2663 client) stops a `wait=false` task; a generation Flow already accepted may still be charged; no `cancel <id>` yet ([#1198](https://github.com/ffroliva/gflow-cli/issues/1198)). |
| `cancel.server` | Stop a generation Flow has already accepted, so it is not charged. | `no`: once Flow accepts a generation it runs and may still be charged. |
| `idempotent_submit` | Retry a submit safely: the same request id never starts a second generation. | `no`: a retried submit is a new generation and may be charged again. |
| `reconcile` | Bring the local catalog back in line with what Flow holds. | `partial`: `data sync --names` refreshes names and presence (CLI only); no `reconcile <id>` yet ([#1199](https://github.com/ffroliva/gflow-cli/issues/1199)). |
| `credits.balance` | Read the account's credit balance. | `partial`: the Veo video-credit balance only (`credits user`/`list`, MCP `gflow_get_credits`); image models use separate per-model daily quotas gflow does not read, and tier fields are null. |

The `notes` in the document are the authoritative wording; this table summarizes them.
On the flow.google.com rows, "gflow drives" is a statement about gflow's port, not about
the host: a request gflow cannot drive there is **not ported yet**.

## No call to Flow

The command makes no call to Flow and opens no browser. (Like every `gflow` command, the
process may still run its once-a-day update check and the weekly telemetry upload;
see [CONFIGURATION](CONFIGURATION.md#gflow_cli_update_check) and [TELEMETRY](TELEMETRY.md).)
Its only input is `GFLOW_CLI_FLOW_HOST`. Nothing on disk records which host Flow served
a profile, so the document is the same for every profile, and under `auto` it describes
both arms. There is no `--probe` yet; per-profile resolution is
[#1212](https://github.com/ffroliva/gflow-cli/issues/1212).

## Keeping it true

- `tests/mcp/test_capabilities.py` maps every CLI command to the key it exercises, or to
  `None` when it sits outside this vocabulary (sessions, the local catalog,
  configuration, docs, the servers, prompt tooling, credit-free composition such as
  `scene create`, and post-processing such as upscale). It fails when a command is added
  without an entry, so a new command forces the question.
- The same file fails when `status`, `wait`, `cancel`, `reconcile`, a top-level `upload`
  or a `--no-wait` option appears, so the notes above that say "not yet" cannot go stale
  silently when #1194, #1198, #1199 or #1200 land.
