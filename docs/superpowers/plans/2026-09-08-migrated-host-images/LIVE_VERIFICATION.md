# Live verification — migrated-host images (#639)

Date: 2026-09-08 · Profile: `arjhibe` · Host: `flow.google.com` · Project: existing
project (UUID intentionally omitted from this ledger).

## Watched runs

| Surface | Command/test | Result | Evidence |
|---|---|---|---|
| CLI | `gflow image t2i ... --model nano-pro --aspect 16:9 --project <id> --json` | PASS, exit 0 | `ogiZ0b` response decoded to one signed JPEG; 1376×768; local file written |
| CLI | `gflow image i2i ... --ref <local JPEG> --model nano-pro --aspect 16:9 --project <id> --json` | PASS, exit 0 | `maseQ` upload id appeared in the outgoing `ogiZ0b` request; one signed JPEG returned |
| CLI + MCP | `pytest -m e2e_image tests/e2e/test_migrated_host_e2e.py` | PASS, 2 passed in 112.48 s | Direct `FlowApiClient` T2I and queued `gflow_generate_image` local-file I2I both completed |

The live capture and test logs were scanned for cookies, reCAPTCHA tokens and signed
query strings before being retained. Unsupported UUID/entity/instruction/Imagen-4 image
forms remain pre-submit refusals; this ledger does not claim those paths work.
