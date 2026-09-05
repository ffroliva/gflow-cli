# PR-Triage Autopilot Operations Runbook

This runbook guides the deployment, configuration, monitoring, and administration of the hourly PR-Triage Autopilot on the VPS host.

---

## 1. Environment & Credentials

The autopilot orchestrator (`scripts/autopilot/pr_triage_autopilot.py`) runs as an hourly cron job and requires the following environment variables:

| Variable | Scope | Purpose |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Autopilot orchestrator & sandbox | Claude **subscription** token from `claude setup-token`, valid **1 year**. This deployment has no `ANTHROPIC_API_KEY`. Passed into the container as an env var. **Not** `~/.claude/.credentials.json` — `setup-token` does not write that file. |
| `GH_COMMENT_TOKEN` | Autopilot orchestrator (host) | Fine-grained least-privilege GitHub PAT (scoped to `ffroliva/gflow-cli` with `pull_requests:write`) used to post verdicts and reviews to PRs. Stored encrypted in hermes-ops SOPS (`vps-prod.env.sops.yaml`) and rendered into `/opt/hermes/.env`. |
| `TELEGRAM_BOT_TOKEN` | Autopilot orchestrator (host) | Bot token to dispatch alert messages. |
| `TELEGRAM_USER_ID` | Autopilot orchestrator (host) | The chat ID to receive triage alert messages. |
| `PR_TRIAGE_ENGINE` | Autopilot orchestrator (host), optional | Review engine selector. Default `council-claude`; any other value exits at startup (`council-multi-cli` is reserved backlog). |
| `HERMES_OPS_DIR` | Autopilot orchestrator (host), optional | Location of the hermes-ops checkout hosting the Resend email notifier. Default `/opt/hermes-ops`. |
| `GH_SANDBOX_TOKEN` | Review **container** | Fine-grained **read-only** PAT for the repo under review. The container only reads the PR; the host posts the comment. If unset the orchestrator falls back and raises an alert rather than degrading silently. Stored in the SOPS secret store. |
| `GFLOW_TRIAGE_MEMORY_DIR` | Autopilot orchestrator (host), optional | Council memory directory mounted read-only for D5. Set it only to override the XDG default (`$XDG_DATA_HOME/gflow-cli/memory`, i.e. `~/.local/share/gflow-cli/memory`). **Never put a machine-specific path in the cron line** — see §2. |
| `RESEND_API_KEY` | Email notifier (hermes-ops) | Resend API key consumed by `$HERMES_OPS_DIR/scripts/notify/email_notify.py`. |
| `HERMES_NOTIFY_EMAIL_TO` | Email notifier (hermes-ops) | Recipient address for high-signal triage emails. |
| `HERMES_NOTIFY_EMAIL_FROM` | Email notifier (hermes-ops) | Verified sender address for the Resend account. |

**Email channel note:** on the VPS the three email vars live in `/opt/hermes/.env`, rendered from hermes-ops' SOPS store. When they are unset the email channel silently disables itself — the notifier logs "email disabled" and the autopilot run is unaffected (Telegram, ledger, and GitHub comments remain authoritative).

---

## 2. Directory Layout & Symlinks

Deploy the following layout on the VPS:
- `/opt/gflow-cli`: Dedicated git repository checkout representing the active branch.
- `~hermes/.local/share/gflow-cli/memory`: Council memory namespace, mounted read-only into the sandbox for dimension D5.

Create the memory directory once, as the user the cron runs under:

```bash
sudo -u hermes mkdir -p ~hermes/.local/share/gflow-cli/memory
```

An **empty** directory is a valid deployment — D5 then reports that no memory is available, and the other 13 dimensions run normally. Populate it only if you want the council to consult durable project memory.

> **The memory path is resolved by the orchestrator, never hardcoded.** Precedence is `--memory-dir` > `$GFLOW_TRIAGE_MEMORY_DIR` > `$XDG_DATA_HOME/gflow-cli/memory` (XDG default, falling back to `~/.local/share`). `XDG_DATA_HOME` is the correct slot per the XDG Base Directory Specification: council memory is durable knowledge that should be backed up, not regenerable cache or throwaway state.
>
> The orchestrator validates the resolved path on the host and refuses to start with an actionable error if it is missing, rather than failing later inside the container.

> **The memory namespace is NOT synced to this host, and that is currently the
> autopilot's biggest blind spot.** Claude Code keys memory by working-directory
> path, not repo identity, so `/opt/gflow-cli` starts empty — see
> `docs/superpowers/specs/2026-07-04-pr-triage-autopilot-design.md` (the one-way
> local-to-VPS sync it describes was never implemented). The orchestrator now logs
> a warning when the resolved directory holds no `*.md`, and D5 must report
> `UNAVAILABLE` rather than GREEN. On PR #650 it reported "no memory entry
> contradicts this PR" from an empty mount while the local store recorded that
> exact PR as REJECTED. Until a sync exists, read every autonomous D5 verdict as
> "not checked".

## 2b. What lands in the public PR comment

The container's stdout is **sliced** before posting: from the first `# PR #`
heading to the line before `SUMMARY_VERDICT:`. Everything else — the wrapper's
Docker/iptables progress, the agent's preamble, the machine marker — is dropped.
The wrapper logs operationally to **stderr**, which the orchestrator keeps for
`RuntimeError` context and the log file but never posts. Both halves exist
because a triage comment on PR #650 published the build steps, a raw Docker
network id, and the bridge subnet to an external contributor.

Adding a new operational `echo` to `run_sandboxed_review.sh`? Send it to `>&2`.
Anything on stdout outside the two markers is silently discarded from the
comment; anything between them is published verbatim.

---

## 3. Ephemeral Sandbox Firewall Hardening

The docker sandbox runs under `scripts/autopilot/run_sandboxed_review.sh`. If run with root/sudo privileges on the VPS host, it automatically invokes `iptables` rules restricting the container bridge network egress interface to:
- UDP/TCP port 53 (DNS resolution)
- TCP port 443 to `api.anthropic.com` resolved IPs
- TCP port 443 to `github.com` resolved IPs
- All other internet egress is blocked (`DROP`).

Ensure `iptables` is installed on the host VPS. If `iptables` permissions are withheld, the script falls back with a warning, but egress hardening will not be active.

---

## 4. Cron Configuration & Verification

### Setup Cron Tick
Configure a cron job checking every hour on the hour under the `hermes` system user:

```cron
0 * * * * set -a; . /opt/hermes/.env 2>/dev/null; set +a; cd /opt/gflow-cli && uv run python scripts/autopilot/pr_triage_autopilot.py --repo-dir /opt/gflow-cli >> /var/log/hermes/pr_triage.log 2>&1
```

Sourcing `/opt/hermes/.env` first (`set -a` exports everything it defines) is what delivers `GH_COMMENT_TOKEN`, `TELEGRAM_*`, and the `RESEND_API_KEY` / `HERMES_NOTIFY_EMAIL_*` vars to the process. Without it the email channel silently disables itself, and without `GH_COMMENT_TOKEN` the run exits 1.

> **`.env` must stay bash-sourceable.** This line sources the file, so any value containing spaces or shell metacharacters has to be quoted in the SOPS store. systemd's `EnvironmentFile` parser is more permissive than bash and will not warn you: an unquoted `HERMES_NOTIFY_EMAIL_FROM=Hermes Ops <noreply@...>` broke exactly this line on 2026-08-02 (`<` is a redirect), silently dropping every variable defined after it. Fixed in hermes-ops by quoting the value.

### Claude authentication (subscription token, no API key)

The council review runs `claude -p` inside the sandbox, authenticated by `CLAUDE_CODE_OAUTH_TOKEN` — the subscription token minted by `claude setup-token`, **valid 1 year**. It is stored in hermes-ops `secrets/vps-prod.env.sops.yaml`, rendered into `/opt/hermes/.env`, and reaches the process because the cron line sources that file. The orchestrator checks it is set before building the image, so a missing token fails in a second rather than as an opaque 401 minutes into a container run.

> **It is not `~/.claude/.credentials.json`.** `setup-token` does not write that file — verified on the ops VPS 2026-08-02, where it still held the expired 2026-07-16 interactive-login token after a successful mint. Reading the file would authenticate with a dead credential. The two are separate mechanisms.

**Rotation — the one real operational dependency.** The token is a static bearer value with **no refresh pair**: it cannot self-renew, and nothing in the API surface reports its age. The previous interactive token 401'd silently for 16 days. Rotate before the 1-year mark:

```bash
sudo -u hermes -H claude setup-token          # prints the token ONCE
# store it in hermes-ops secrets/vps-prod.env.sops.yaml as CLAUDE_CODE_OAUTH_TOKEN
sudo -u hermes -H bash -c 'set -a; . /opt/hermes/.env; set +a; claude -p "say OK"'   # verify
```

The daily `ev-ops-health` Telegram digest carries the expiry countdown; treat that warning as the rotation trigger.

**Accepted risk (operator, 2026-08-02):** the token grants inference on the whole subscription and is injected into a container that reviews untrusted external PRs. Its scope is `user:inference` only — narrower than the five scopes an interactive login carries — and the §3 egress firewall is the compensating control. Accepted because no API key exists for this account.

### Deploy mechanism (warning)

Do **NOT** register the orchestrator directly via `hermes cron create --script pr_triage_autopilot.py`. Hermes' cron runs scripts with hermes-agent's own interpreter — which lacks this repo's dependencies (e.g. `structlog`) — and only accepts scripts under `HERMES_HOME/scripts/`. Supported mechanisms:

- **(a) Plain crontab** — the line above, under the `hermes` system user.
- **(b) Thin shim in `HERMES_HOME/scripts/`** — a script that does `cd /opt/gflow-cli && exec uv run python scripts/autopilot/pr_triage_autopilot.py "$@"` (mirrors the existing EV ops-health shim pattern), registered with `hermes cron create "1h" --no-agent --script <shim> --deliver telegram`.

### Log rotation

`pr_triage.log` grows without bound otherwise. Install once, as root:

```bash
install -m 644 /opt/gflow-cli/deploy/logrotate-pr-triage /etc/logrotate.d/pr-triage
logrotate --debug /etc/logrotate.d/pr-triage    # dry-run
```

Scoped to `pr_triage.log` alone — the rest of `/var/log/hermes/` belongs to hermes-ops, and two packages rotating one glob is how logs get silently truncated.

### Check Logs & Status
- **Log Location**: `/var/log/hermes/pr_triage.log`
- **Triage Ledger**: `/opt/gflow-cli/pr_triage_ledger.jsonl` tracks verdicts and failure states.
- **Lock File**: `/tmp/pr_triage_autopilot.lock` prevents overlapping ticks.

---

## 5. Operations & Kill-Switches

### Incident: Infinite loop or excessive cost
1. **Pause Cron**: Comment out the cron entry in `crontab -e`.
2. **Kill Running Container**:
   ```bash
   docker ps | grep gflow-triage
   docker kill <container_id>
   ```
3. **Clean Up Lock**:
   ```bash
   rm -f /tmp/pr_triage_autopilot.lock
   ```

### Incident: Permanently Failed PR
If a PR enters the `FAILED_PERMANENT` state in `pr_triage_ledger.jsonl` due to 3 consecutive failures:
1. Examine `/var/log/hermes/pr_triage.log` for the exact trace.
2. Fix the underlying environmental or syntax issue.
3. To trigger a re-review, delete or edit the `FAILED_PERMANENT` entries for that PR number/SHA in `pr_triage_ledger.jsonl`, then run the script manually:
   ```bash
   uv run python scripts/autopilot/pr_triage_autopilot.py --repo-dir /opt/gflow-cli
   ```
