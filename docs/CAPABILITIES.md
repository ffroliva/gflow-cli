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

MCP clients read the same document from the resource **`capabilities://self`**. Both
surfaces serialize one function (`gflow_cli.cli_capabilities.current_capabilities`), and
a test holds them to the same bytes.

## Document

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
    "download": "yes",
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
| `host` | The `GFLOW_CLI_FLOW_HOST` setting: `auto`, `flow.google.com` or `labs.google`. Under `auto` the host is chosen per request from what Flow serves; the document does not check which (see [Offline](#offline)). |
| `capabilities` | One value per key: `yes`, `no`, `partial` or `unknown`. |
| `notes` | One sentence per key. A `partial` always has one, saying what is missing. |

## Keys

| Key | Meaning | Today |
|---|---|---|
| `generate.image` | Text-to-image and image-to-image generation (`image t2i`/`i2i`/`batch`, `run`; MCP `gflow_generate_image`). | `unknown` with `labs.google` (gflow's labs driver takes every mode, but whether Flow serves this account labs is not checked offline; exit 36 if it serves flow.google.com); otherwise `partial`: on flow.google.com gflow drives t2i and i2i from local files, and the agent-only composer ([#799](https://github.com/ffroliva/gflow-cli/issues/799)) takes t2i only. |
| `generate.video` | Video generation (`video t2v`/`i2v`/`r2v`/`chain`/`extend`, `movie run`, `scene create`; MCP `gflow_generate_video`). | `unknown` with `labs.google` (as above); otherwise `partial`: on flow.google.com gflow drives t2v, i2v from a local frame, r2v from local refs or a named character and a single-segment extend; the agent-only composer takes t2v only. |
| `submit.no_wait` | Submit and return an id without waiting for the result. | `partial`: MCP `wait=false` only; no CLI `--no-wait` yet ([#1194](https://github.com/ffroliva/gflow-cli/issues/1194)). |
| `status` | Ask how a submitted generation is doing, by id. | `partial`: MCP `tasks/get` only; no CLI `status <id>` yet (#1194). |
| `wait` | Block until a submitted generation finishes, by id. | `partial`: CLI generation commands block until done and MCP `tasks/get` carries a finished result; no CLI `wait <id>` yet (#1194). |
| `download` | Fetch a finished asset to a local file. | `yes`: generation commands download on completion; `data download` (MCP `gflow_download_media`) recovers a failed download. |
| `upload` | Upload a local file to Flow for reuse. | `partial`: `image upload` (CLI only) prints an asset id; the MCP generate tools upload local reference paths themselves; no top-level `upload` yet ([#1200](https://github.com/ffroliva/gflow-cli/issues/1200)). |
| `abort.local` | Stop gflow's own wait or job. Does **not** stop Flow. | `partial`: Ctrl-C stops a CLI run and MCP `tasks/cancel` stops a `wait=false` task; no `cancel <id>` yet ([#1198](https://github.com/ffroliva/gflow-cli/issues/1198)). |
| `cancel.server` | Stop a generation Flow has already accepted, so it is not charged. | `no`: once Flow accepts a generation it runs and is charged. |
| `idempotent_submit` | Retry a submit safely: the same request id never starts a second generation. | `no`: a retried submit is a new generation and is charged again. |
| `reconcile` | Bring the local catalog back in line with what Flow holds. | `partial`: `data sync --names` refreshes names and presence (CLI only); no `reconcile <id>` yet ([#1199](https://github.com/ffroliva/gflow-cli/issues/1199)). |
| `credits.balance` | Read the account's credit balance. | `partial`: the Veo video-credit balance only (`credits user`/`list`, MCP `gflow_get_credits`); image models use separate per-model daily quotas gflow does not read, and tier fields are null. |

The `notes` in the document are the authoritative wording; this table summarizes them.
On the flow.google.com rows, "gflow drives" is a statement about gflow's port, not about
the host: a request gflow cannot drive there is **not ported yet**.

## Offline

The command makes no network call and opens no browser. Its only input is
`GFLOW_CLI_FLOW_HOST`. Nothing on disk records which host Flow served a profile, so the
document is the same for every profile, and under `auto` it describes both arms. There
is no `--probe` yet.

## Every command has an entry

`COMMAND_CAPABILITY` in `src/gflow_cli/cli_capabilities.py` maps every CLI command to
the key it exercises, or to `None` when it sits outside this vocabulary (sessions,
the local catalog, configuration, docs, the servers). `tests/mcp/test_capabilities.py`
fails when a command is added without an entry, so a new command forces the decision.
