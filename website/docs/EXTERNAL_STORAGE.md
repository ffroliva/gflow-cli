# External Storage

`gflow-cli` can write generated images and videos directly to Google Cloud
Storage, Amazon S3, or an S3-compatible bucket such as MinIO.

Set [`GFLOW_CLI_STORAGE_URI`](CONFIGURATION.md#gflow_cli_storage_uri) to enable
cloud writes. Leave it unset for normal local filesystem output.

## Storage Modes

| `GFLOW_CLI_STORAGE_URI` | Asset bytes are written to | Catalog record |
|---|---|---|
| unset | `$GFLOW_CLI_OUTPUT_DIR` or the command's local output override | `local_files.path` |
| `gs://bucket/prefix/` | Google Cloud Storage | `local_files.storage_provider="gcs"`, `local_files.cloud_uri` |
| `s3://bucket/prefix/` | S3 or S3-compatible storage | `local_files.storage_provider="s3"`, `local_files.cloud_uri` |

Current behavior is single-target, not dual-write. When cloud storage is active,
generated asset bytes are uploaded to the configured bucket instead of being
saved as local asset copies. The SQLite catalog remains local and records the
cloud URI for lookup through [`gflow data media`](USAGE.md#gflow-data-media).

## Install Extras

Cloud backends are optional dependencies. Install the matching extra in the
environment where `gflow` runs:

```bash
uv tool install "gflow-cli[s3]"
uv tool install "gflow-cli[gcs]"
```

If the tool is already installed, reinstall with the extra in your package
manager of choice, for example `uv tool install --force "gflow-cli[s3]"`.

## Object Layout

Without per-command local output overrides, cloud object keys mirror the default
local layout:

```text
<storage-prefix>/
├── images/<YYYY-MM-DD>/<media_id>_<index>.<ext>
└── videos/<YYYY-MM-DD>/<media_id>.mp4
```

`<ext>` is corrected from the actual bytes before upload. For example, if Flow
returns JPEG bytes for an image request originally planned as `.png`, the object
key is adjusted to `.jpg` before it is written.

Per-command local output flags are not bucket-prefix controls. For predictable
cloud keys, prefer setting `GFLOW_CLI_STORAGE_URI` to the desired bucket prefix
and leave the command output flags unset.

## S3 And MinIO

```bash
export GFLOW_CLI_STORAGE_URI=s3://my-bucket/gflow/
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1
gflow image t2i "blue product cube on a white sweep" -n 2
```

For local MinIO development:

```bash
export GFLOW_CLI_STORAGE_URI=s3://gflow-test/dev/
export AWS_ACCESS_KEY_ID=minioadmin
export AWS_SECRET_ACCESS_KEY=minioadmin
export AWS_ENDPOINT_URL=http://localhost:9000
export AWS_DEFAULT_REGION=us-east-1
```

The S3 backend uses standard AWS SDK environment variables. Omit
`AWS_ENDPOINT_URL` for real AWS S3.

## Google Cloud Storage

```bash
export GFLOW_CLI_STORAGE_URI=gs://my-gcs-bucket/gflow/
gcloud auth application-default login
gflow video t2v "slow cinematic push-in on a sunlit forest clearing"
```

For service accounts, point `GOOGLE_APPLICATION_CREDENTIALS` at the JSON
credential file before running `gflow`. For a local GCS emulator, set
`STORAGE_EMULATOR_HOST`.

## Verifying A Write

Use the generated media ID to inspect the local catalog:

```bash
gflow data media <media-id>
```

Cloud-backed assets show `cloud_uri_1`, `cloud_uri_2`, and so on:

```text
cloud_uri_1   s3://gflow-test/dev/images/2026-05-28/abc_1.jpg
```

Then verify the object with your provider tool:

```bash
aws s3 ls s3://gflow-test/dev/images/2026-05-28/
gsutil ls gs://my-gcs-bucket/gflow/videos/2026-05-28/
```

For MinIO, the web console can also preview generated images and download
videos from the configured bucket.

## Security Notes

- Generated assets may contain sensitive prompts, people, products, or client
  work. Configure bucket IAM, retention, public access, and encryption according
  to your own threat model.
- Do not commit real cloud credentials or `.env` files. Use the provider's
  normal credential chain or short-lived environment variables.
- `gflow-cli` does not store Flow signed CDN URLs, reCAPTCHA tokens,
  authorization headers, or cookies in the catalog. See
  [DATA_LAYER.md](DATA_LAYER.md#privacy-and-redaction) and
  [SECURITY.md](SECURITY.md#local-data-layer).
- The local SQLite database still stores profile names, Flow IDs, prompt hashes,
  and cloud URIs. Set `GFLOW_CLI_DB_PATH` to an encrypted or ephemeral location
  if that metadata is sensitive.

## Troubleshooting

| Symptom | Check |
|---|---|
| `Cloud storage requires universal_pathlib` | Reinstall with `gflow-cli[s3]` or `gflow-cli[gcs]`. |
| `GFLOW_CLI_STORAGE_URI scheme not supported` | Use `s3://...` or `gs://...`; local paths belong in `GFLOW_CLI_OUTPUT_DIR`. |
| S3 write fails against MinIO | Confirm `AWS_ENDPOINT_URL`, access key, secret key, bucket name, and region. |
| GCS write fails locally | Confirm `STORAGE_EMULATOR_HOST` for emulator runs or ADC/service-account credentials for real GCS. |
| `gflow data media` shows a local path | Cloud storage was not active for that run; check the shell environment used by the command. |

Related docs: [CONFIGURATION.md](CONFIGURATION.md#gflow_cli_storage_uri),
[DATA_LAYER.md](DATA_LAYER.md#cloud-backed-files), and
[SECURITY.md](SECURITY.md#cloud-storage).
