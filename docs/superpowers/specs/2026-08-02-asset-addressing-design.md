# Asset addressing, storage portability, and error guidance — design spec

> **Status:** PROPOSED · **Date:** 2026-08-02 · **Council-reviewed:** 2026-08-02
> (internal 6-dimension council — D1 correctness/mode-coverage **RED**, D14 over-engineering **RED**,
> D3 security / D4 tests / D7 data-layer / D8 CLI-UX **YELLOW**; overall **RED** on the originally
> proposed shape, which this spec supersedes)
> **Origin:** [#411](https://github.com/ffroliva/gflow-cli/issues/411) asked for an `-o/--output`
> flag on the generation commands. Triage found the flag treats a symptom; this spec fixes the
> addressing model underneath it.
> **Companion plan:** [plans/2026-08-02-asset-addressing/PLAN.md](../plans/2026-08-02-asset-addressing/PLAN.md)
> **External-review caveat:** the council ran `small`-tier — `codex` and `agy` were unavailable in
> the review environment, so both RED verdicts rest on same-model-family reviewers. Re-run the
> external layer before executing Phase 2 or later.

---

## 1. Goal

Make a generated asset **addressable and verifiable** regardless of where its bytes live, so that:

```bash
# scripting: resolve any generation to its artifact, local or cloud
gflow image t2i "a serene mountain lake" --json | jq -r '.images[0].media_name'
gflow data media <media_id> --json      | jq -r '.locations[0].uri'

# portability: move an asset between backends without losing provenance
gflow data port <media_id> --to local
```

The **asset** is the entity. A **file is one location of that asset**. The path or URI is an
implementation detail behind the catalog, not the user's contract.

## 2. Problem

### 2.1 The catalog is write-mostly

`local_files` already models 1:N locations per asset
(`data/migrations/0001_initial.sql:79-90`, `UNIQUE(asset_id, path)`) with `storage_provider` and
`cloud_uri` added in `0002_add_cloud_storage.sql`. `gflow data media` already iterates
`asset.local_files` and prints `local_path_N` / `cloud_uri_N`. But it renders a Rich table **only** —
there is no `--json`, so the one command that answers "where is this asset?" cannot be scripted.
That, not filename unpredictability, is why #411's reporter reached for `-o`.

### 2.2 Keys are recovered by subtraction, lossily

`image_output_path` / `video_output_path` build an absolute local path; the storage key is then
recovered by string subtraction at three sites, with a silent lossy fallback:

```python
try:    key = out_path.relative_to(self.settings.output_dir).as_posix()
except ValueError:  key = out_path.name          # layout fidelity lost
```

`api/client.py:1583-1585` (`download_image`), `:1796-1798` (`concatenate_scene`),
`:1934-1936` (`upsample_image`). The comment at `client.py:1578` promises "cloud keys mirror the
local directory structure"; the fallback silently breaks that promise.

Cloud rows remain **recoverable** (`cloud_uri = storage_uri + key_actually_used`, so stripping the
prefix always recovers the key that was written). What is lost is layout fidelity. **Local** rows
are the fragile case: `record_upload_image` (`data/recorder.py:454`) and character files
(`:1259`) store `path.resolve()` for caller-supplied paths with no constraint to `output_dir`, so
`relative_to()` raising is an expected case, not an error.

### 2.3 The premise does not hold for five surfaces

| Surface | Why it has no `assets` row | Evidence |
|---|---|---|
| `image upscale` | Writes through the storage layer but **never instantiates `OperationRecorder`** | `cli_image.py:585-612` |
| `scene create --output` | Concat result is media-id-less ("ephemeral on Flow's side"), stored in a bespoke `scenes.output_path` column | `migrations/0004_add_scene_output_path.sql:1-5`, `recorder.py:693-697` |
| chain seed frames | Written as a bare local `Path`, tracked only in `chain_links.seed_frame_path` | `chain.py:257,379-385` |
| `movie run` | Writes a JSON handoff sidecar carrying raw path strings | `cli_movie.py:565,734-749` |
| `image upload` | References a pre-existing user file; writes no bytes | `cli_image.py:396-437` |

Any location model that does not reconcile `chain_links.seed_frame_path`, `scenes.output_path`, and
the movie sidecar **adds a fourth addressing scheme instead of unifying to one**.

### 2.4 Integrity is unavailable exactly where it matters

`sha256` is skipped for every cloud row — `recorder.py:456-457`, `:518-519`, `:845-846`
(`sha256=... if cloud_storage_info is None else None`). At all three sites the bytes are already
buffered in memory (`client.py:1557` and the concat/upscale equivalents), so hashing is free. Until
this is fixed, any port to or from cloud is a copy with **no integrity check by construction**.

### 2.5 Errors do not guide

The framework is sound — `GFlowError._default_remediation` / `remediation_hint`
(`errors.py:68-106`) renders identically in Rich (`_cli_helpers.py:299-314`) and `--json`
(`json_output.py:54-79`). Usage is not: `ConfigurationError` is raised **119 times and 5 pass a
`remediation_hint`**. The other 114 inherit a transport-specific default (`errors.py:339-342`),
so 53 manifest-validation sites and 32 batch-validation sites tell the user to check transport
registration. Separately, `storage.py:68-81` authors a correct install hint and then raises a raw
`ImportError`, which is not a `GFlowError` and is discarded by the catch-all handler.

## 3. Scope

**In scope.** Correct the write path so keys are canonical rather than recovered; give every
byte-producing surface a catalog row; compute integrity hashes for all backends; expose a
machine-readable read API; add an additive cloud↔local port; make errors on these surfaces
actionable and enforce that with tests.

**Non-goals.**

- **An `AssetStore` Protocol with per-backend strategy classes.** Rejected — see § 10.1.
- **Migration `0010` / `storage_key` / `gflow data reconcile`.** Deferred — see § 10.3.
- **`-o/--output` on the generation commands (#411's literal ask).** Deferred to a follow-up as
  sugar; § 4.4 fixes the underlying need. See § 10.4.
- **Reworking `UNIQUE(asset_id, path)` polymorphism.** Requires a table rebuild; out of scope
  (§ 9.2).
- **Azure Blob or any fourth backend.** No named user.

## 4. Contract

### 4.1 `Location` — a value object, not an interface

```python
@dataclass(frozen=True)
class Location:
    backend: str        # "local" | "gcs" | "s3"
    key: str            # canonical, backend-independent: "images/2026-08-02/<media>_1.jpg"
    uri: str            # concrete: "file:///…" | "gs://bucket/prefix/<key>" | "s3://…"
    sha256: str
    bytes: int
    media_type: str
```

`key` is the stable identity; `uri` is derived. `Location` is returned by the write path so callers
stop receiving a bare `Path`/`UPath` with no digest or media-type — the single real gap in today's
storage layer.

**Construction invariant:** `key` is sanitized at `Location.__post_init__` via the existing
`_sanitize_key` (`storage.py:55-65`). Sanitization is centralized here, not per-backend.

### 4.2 What is an asset, and what is not

Three artifact classes, explicitly:

| Class | Definition | Catalog home | Examples |
|---|---|---|---|
| **Generated asset** | gflow-cli produced the bytes and Flow issued a `media_id` | `assets` + N × `local_files` | t2i, i2i, batch, t2v, i2v, r2v, chain clips, character slots, upscale output |
| **Derived artifact** | gflow-cli produced the bytes; **no Flow `media_id` exists** | `assets` with `flow_media_id = NULL`, + `local_files` | scene concat output, chain seed frames |
| **Referenced file** | A pre-existing user file gflow-cli did not write | `local_files` row only, `provenance="referenced"` | `image upload` source |

The **derived artifact** class is the missing piece that makes the model total. It requires relaxing
`assets.flow_media_id` to nullable, with a synthetic stable id (`gflow:derived:<sha256[:16]>`) as
the addressing handle. Without it, scene concat and chain seed frames remain unaddressable.

`scenes.output_path` and `chain_links.seed_frame_path` become **derived, backward-compatible views**
of the corresponding `local_files` row — they are not removed in this change, but they stop being
the source of truth.

### 4.3 Key scheme

One scheme, applied uniformly, replacing the local/cloud divergence at
`ui_automation_video.py:1146-1157`:

```
images/<YYYY-MM-DD>/<media_name>_<index>.<ext>
videos/<YYYY-MM-DD>/<media_id>.mp4
characters/<YYYY-MM-DD>/character_<entity_id>_slot<slot>.<ext>
scenes/<YYYY-MM-DD>/<scene_id>.mp4
frames/<YYYY-MM-DD>/<chain_id>_link<index>_lastframe.jpg
```

Resolution is one function, replacing the three duplicated `relative_to` blocks:

```python
def resolve_location(*, key: str, output_dir: Path, storage_uri: str | None) -> AnyPath
```

**Behavioural change (breaking, must be changelogged):** local video output moves from flat
`<out_dir>/<media_id>.mp4` to `videos/<YYYY-MM-DD>/<media_id>.mp4`, matching what cloud already
does. Scripts globbing `--out-dir/*.mp4` will need updating. `--out-dir` continues to override the
root; the key is appended beneath it.

**Extension correction** stays byte-derived (`adjust_key_extension`, issue #96) — Flow's CDN
returns JPEG for `.png` targets. The corrected extension is reflected in `Location.key` before the
catalog row is written, so key and reality never diverge.

### 4.4 Read API

```
gflow data media <media_id> [--profile NAME] [--json]
```

`--json` emits:

```json
{
  "status": "ok",
  "media_id": "...", "project_id": "...", "kind": "video", "profile": "...",
  "locations": [
    {"backend": "gcs", "key": "videos/2026-08-02/abc.mp4",
     "uri": "gs://bucket/prefix/videos/2026-08-02/abc.mp4",
     "sha256": "…", "bytes": 5242880, "media_type": "video/mp4"}
  ]
}
```

Existing generation-command `--json` (already shipping `local_path`) gains `locations[]` alongside
it; `local_path` is retained for compatibility.

### 4.5 Port

```
gflow data port <media_id> --to {local|cloud} [--prune-source] [--dry-run]
```

**Additive by default.** Port produces a *second* `Location`; it is never destructive on its own.
`--prune-source` removes the old location only after the new one verifies.

Ordering is fixed and non-negotiable — bytes are the source of truth, the catalog is the
reconcilable side (`docs/DATA_LAYER.md:214`, "the catalog can be reconciled later… the file cannot
be un-billed"):

1. Write destination bytes
2. Hash the destination
3. Compare against the source hash — mismatch aborts, leaving both sides intact
4. Append the new `Location` row and commit
5. Only then, under `--prune-source`, delete the source object

On a post-copy catalog failure, **leave the new copy in place as an orphan**; never roll back bytes.
Idempotency comes from a `transfer_state` on the pending row (`pending` → `verified` → `committed`),
so a re-run after interruption is a no-op rather than a duplicate write.

Port never contacts Flow's API, so it is **not** subject to the live-verify gate.

### 4.6 Error contract

Every error on this surface satisfies: *the message names what went wrong, and the remediation names
the exact command or flag that fixes it.* An error that does not is a defect, not a style issue.

| Trigger | Class | Exit | Message | Remediation |
|---|---|---|---|---|
| Cloud extra not installed | `ConfigurationError` (was: raw `ImportError`) | 11 | `Cloud storage requires the matching extra.` | `Install it: pip install 'gflow-cli[gcs]'` (or `[s3]`) |
| `port` with missing credentials | `ConfigurationError` + raise-site hint | 11 | `No credentials found for <provider>.` | `Set GOOGLE_APPLICATION_CREDENTIALS (gs://) or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY (s3://), then retry.` |
| `port` object missing at backend | `DataIntegrityError` | 16 | `Catalog record for <id> points to <uri>, but the object no longer exists there.` | `Re-generate the asset, or remove the stale location with 'gflow data prune'.` |
| `port` hash mismatch | `DataIntegrityError` | 16 | `Copy verification failed: source <sha> != destination <sha>.` | `Re-run 'gflow data port'; both copies were left intact.` |
| `data media <id>` not found | `DataStoreError` + raise-site hint | 16 | `No local media record found: <id>.` | `Check the ID for typos, or run 'gflow data list media' to see valid IDs.` |
| `data media <id>` ambiguous | `DataStoreError` + raise-site hint | 16 | `Media '<id>' exists under multiple profiles: [...]` | `Pass --profile NAME to disambiguate.` |
| chain + cloud storage | `ConfigurationError` | 11 | `Chain seed-frame extraction cannot read from cloud storage.` | `Run 'gflow video chain' without GFLOW_CLI_STORAGE_URI, then 'gflow data port' the clips afterwards.` |

**No new exit codes.** `click.UsageError` → 2, `ConfigurationError` → 11,
`DataStoreError`/`DataIntegrityError` → 16 (`errors.py:825-844`, `:933-935`) cover every case.

**Root fix, not per-site patching:** split `ConfigurationError` into `ManifestValidationError` and
`PromptValidationError` with topically-correct defaults, so 114 raise sites become correct by
construction instead of each needing a bespoke hint.

**Enforcement** (two tests, because the existing one checks presence, not applicability):

1. Generalize `tests/test_self_documenting_errors.py:41-57` — it hardcodes 8 of 36 subclasses, so a
   new class can ship with an empty hint and nothing fails. Walk `__subclasses__()` instead.
2. Assert hints are **on-topic**: a manifest-validation error's hint must not mention "transport".
   This test fails today.

## 5. Security invariants

1. **No credentials in destination strings.** Reject embedded userinfo (`s3://KEY:SECRET@bucket/`)
   at the `GFLOW_CLI_STORAGE_URI` validator (`config.py:357-369`, currently prefix-check only).
   `safe_path_text` (`_cli_helpers.py:256-283`) strips CWD/home prefixes but has no credential
   scrubbing, and resolved paths are printed to console (`cli_image.py:975,1352`) and persisted to
   `local_files.path`/`cloud_uri`, neither of which passes through `redact_metadata`
   (`data/redaction.py:55-75`; `docs/SECURITY.md:120-122` does not list them).
2. **Cloud URIs are not filesystem paths** — do not route them through `safe_path_text`; they carry
   no local-machine information.
3. **MCP exposes no write destination.** Today zero MCP parameters reach a write path
   (`mcp/tools.py:624-769` — `profile` is resolved server-side, `project` is never a path). This
   spec keeps it that way. `tests/mcp/test_cli_parity.py` is command-level and does not force
   otherwise.
4. **Key sanitization is centralized** at `Location.__post_init__`, so no future backend can skip it.

## 6. Data layer

### 6.1 What changes now

- `assets.flow_media_id` becomes nullable, to admit the **derived artifact** class (§ 4.2).
- `local_files` gains `provenance` (`generated` | `derived` | `referenced`).
- `sha256` and `bytes` are populated for **all** backends.

### 6.2 What is explicitly deferred

`local_files.path` remains polymorphic (`repository.py:747-750` stuffs the cloud URI into `path` to
satisfy `NOT NULL` + `UNIQUE(asset_id, path)`). Making `path` legacy requires a **table rebuild** —
SQLite cannot alter a UNIQUE in place; the in-repo precedent is `0009_queue_claims.sql`'s
create-copy-drop-rename — and touches `repository.py:996-1034`, `cli_data.py:476-481`, and four raw
SQL blocks in `queries.py`. That is a separate change with its own plan.

### 6.3 Migration constraints (binding)

Migrations execute `.sql` **verbatim** with no Python hook (`store.py:47-71`), inside one
`BEGIN IMMEDIATE` (`:240-248`), and `apply_migrations()` runs on **every** `DataStore.open()` — i.e.
every `gflow` invocation. Therefore:

- No migration in this change may compute values from `Settings` or touch the filesystem.
- Any data backfill must be a resumable CLI command, never migration machinery — a failed backfill
  inside `apply_migrations()` raises `DataMigrationError`, which then blocks every future
  invocation.
- `DataMigrationError` is reserved for schema incompatibility. Row-level failures use
  `DataStoreError` / `DataIntegrityError`.

## 7. Testing & verification

| Piece | Offline | `containers` (Docker) | Live Flow |
|---|---|---|---|
| `Location`, key scheme, `resolve_location` | ✅ | | |
| Port algorithm (ordering, resumability, partial failure) | ✅ via fake dict-backed store | | |
| `data media --json` contract | ✅ | | |
| Error taxonomy + remediation tests | ✅ | | |
| sha256-on-cloud-write | ✅ (pure function of bytes) | | ⚠️ recording slice touches a generation path |
| s3fs / gcsfs adapter correctness | | ✅ MinIO + fake-gcs | |
| Catalog rows for upscale / character / scene | ✅ | | ⚠️ generation path |

**Two harness facts that constrain this plan:**

- `memory://` is allowed by `config.py:365` and documented as the test backend
  (`storage.py:15,34,110`) but has **zero references in `tests/`**. It is an unverified claim; do
  not build the port test strategy on it without first proving it round-trips.
- The only byte-level cloud harness (`tests/integration/test_storage_{s3,gcs}.py`, MinIO/fake-gcs
  via `testcontainers`) is marked `containers` and excluded by default addopts;
  `.github/workflows/ci.yml` has no Docker reference. Adapter tests are therefore **developer
  opt-in, not a merge gate**, until a CI job exists. This must be stated, not assumed.

Do not use `moto` — it is not a dependency and conflicts with the `s3fs`/`aiobotocore` pins.

## 8. Documentation impact

`docs/CONFIGURATION.md:400` currently states "file paths are not accepted (rename after the fact if
needed)" and `:58-59` documents the `--out` / `--out-dir` split. Both need rewriting.
`docs/EXTERNAL_STORAGE.md`, `docs/DATA_LAYER.md`, and `docs/SECURITY.md` § redaction scope all
change. `scripts/ci/check_doc_links.py` is a merge gate.

## 9. Rollout

Phase 0 is independently shippable and carries no new concepts — it is pure defect repair. Phases 1
and 2 each stand alone. Nothing in this spec requires a big-bang change.

## 10. Decisions and rejected alternatives

### 10.1 Rejected: `AssetStore` Protocol with per-backend strategies
`UPath` IS-A `Path`, and fsspec's gcsfs/s3fs already implement the `put/open/exists/url` surface the
Protocol would re-declare (`storage.py:32,68-81,113-145`). Three strategy classes wrapping a library
that already unifies them is precisely AGENTS.md's banned pattern — "no speculative abstractions
(interface/factory with one implementation)" — made worse by turning one implementation into three
thin wrappers. Blast radius would be 8 production modules plus 5 test files. **Keep UPath; add the
`Location` dataclass**, which is the only genuine gap.

### 10.2 Decided: close PLAN.md Phase 8 as superseded
Phase 8 (`PLAN.md:674-685`) specifies a `StorageBackend` Protocol, `GFLOW_CLI_STORAGE_BACKEND`, and
a `--storage-backend` flag. `gflow_cli.storage` shipped a different, working design — one validated
`GFLOW_CLI_STORAGE_URI` (`config.py:345-368`) with 8 production call sites. Reopening Phase 8 under
a new name would ship the over-engineering it was right-sized away from. **Caveat:** its integration
suites do not run in CI (§ 7), so record that gap when closing it. `PLAN.md:468` ("Unify Output
Resolution") is genuinely retired by § 4.3.

### 10.3 Deferred: migration `0010`, `storage_key`, and `gflow data reconcile`
The backfill cannot live in a migration (§ 6.3); `storage_key` as an additive column does not fix
the `UNIQUE` polymorphism (§ 6.2); the write-time sha256 fix (§ 2.4) beats reconcile-backfill, which
would cost a network read per object. Decisively: **reconcile cannot repair a catalog that
structurally omits upscale and scene-concat outputs** — fix the write path first (§ 4.2). Orphan
adoption additionally lowers the attacker bar from "controls Flow's API response" to "can write into
the output directory". Revisit when drift is **measured**, not assumed.

### 10.4 Deferred: `-o/--output` (#411 as literally filed)
The reporter's need is met by § 4.4 plus existing `--json`. When `-o` is eventually built it must
take `type=str`, never `click.Path` — verified empirically, `Path("s3://bucket/k.png")` collapses to
`s3:/bucket/k.png` on POSIX and `s3:\bucket\k.png` on Windows. It must reject multi-asset runs
(precedent: `cli_image.py:787-819`) and require `--force` to overwrite (precedent:
`cli_scene.py:100-102`).

## 11. Open questions

1. **Chain + cloud storage** (§ 4.6, Phase 0.5): fail fast with a clear error, or download-to-temp
   before PyAV extraction? Failing fast is smaller and honest; download-to-temp preserves the
   feature. Needs an owner decision.
2. **Local video layout change** (§ 4.3): accept the break with a CHANGELOG entry, or keep flat
   output behind a compatibility flag for one minor version?
3. **`containers` in CI** (§ 7): wire the job now, or ship with adapter tests documented as
   opt-in?
