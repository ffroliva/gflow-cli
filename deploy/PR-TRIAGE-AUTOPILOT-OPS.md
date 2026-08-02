# PR-Triage Autopilot Operations Runbook

This runbook guides the deployment, configuration, monitoring, and administration of the hourly PR-Triage Autopilot on the VPS host.

---

## 1. Environment & Credentials

The autopilot orchestrator (`scripts/autopilot/pr_triage_autopilot.py`) runs as an hourly cron job and requires the following environment variables:

| Variable | Scope | Purpose |
|---|---|---|
| `CLAUDE_CREDENTIALS_FILE` | Autopilot orchestrator (host), optional | Path to the Claude **subscription OAuth** credentials. Defaults to `~/.claude/.credentials.json`. This deployment is subscription-based and uses **no `ANTHROPIC_API_KEY`** — the orchestrator copies this file per-run into the sandbox at `CLAUDE_CONFIG_DIR=/tmp/claude`. |
| `GH_COMMENT_TOKEN` | Autopilot orchestrator (host) | The comment-only GitHub PAT used to post verdicts and reviews to `ffroliva/gflow-cli` PRs. |
| `TELEGRAM_BOT_TOKEN` | Autopilot orchestrator (host) | Bot token to dispatch alert messages. |
| `TELEGRAM_USER_ID` | Autopilot orchestrator (host) | The chat ID to receive triage alert messages. |
| `PR_TRIAGE_ENGINE` | Autopilot orchestrator (host), optional | Review engine selector. Default `council-claude`; any other value exits at startup (`council-multi-cli` is reserved backlog). |
| `HERMES_OPS_DIR` | Autopilot orchestrator (host), optional | Location of the hermes-ops checkout hosting the Resend email notifier. Default `/opt/hermes-ops`. |
| `RESEND_API_KEY` | Email notifier (hermes-ops) | Resend API key consumed by `$HERMES_OPS_DIR/scripts/notify/email_notify.py`. |
| `HERMES_NOTIFY_EMAIL_TO` | Email notifier (hermes-ops) | Recipient address for high-signal triage emails. |
| `HERMES_NOTIFY_EMAIL_FROM` | Email notifier (hermes-ops) | Verified sender address for the Resend account. |

**Email channel note:** on the VPS the three email vars live in `/opt/hermes/.env`, rendered from hermes-ops' SOPS store. When they are unset the email channel silently disables itself — the notifier logs "email disabled" and the autopilot run is unaffected (Telegram, ledger, and GitHub comments remain authoritative).

---

## 2. Directory Layout & Symlinks

Deploy the following layout on the VPS:
- `/opt/gflow-cli`: Dedicated git repository checkout representing the active branch.
- `/opt/experience-vault`: Host experience vault directory structure.
- `/opt/experience-vault/projects/C--development-github-gflow-cli/memory`: The project-specific memory namespace to mount.

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
0 * * * * set -a; . /opt/hermes/.env 2>/dev/null; set +a; cd /opt/gflow-cli && uv run python scripts/autopilot/pr_triage_autopilot.py --repo-dir /opt/gflow-cli --memory-dir /opt/experience-vault/projects/C--development-github-gflow-cli/memory >> /var/log/hermes/pr_triage.log 2>&1
```

Sourcing `/opt/hermes/.env` first (`set -a` exports everything it defines) is what delivers `GH_COMMENT_TOKEN`, `TELEGRAM_*`, and the `RESEND_API_KEY` / `HERMES_NOTIFY_EMAIL_*` vars to the process. Without it the email channel silently disables itself, and without `GH_COMMENT_TOKEN` the run exits 1.

> **`.env` must stay bash-sourceable.** This line sources the file, so any value containing spaces or shell metacharacters has to be quoted in the SOPS store. systemd's `EnvironmentFile` parser is more permissive than bash and will not warn you: an unquoted `HERMES_NOTIFY_EMAIL_FROM=Hermes Ops <noreply@...>` broke exactly this line on 2026-08-02 (`<` is a redirect), silently dropping every variable defined after it. Fixed in hermes-ops by quoting the value.

### Claude authentication (subscription OAuth, no API key)

The council review runs `claude -p` inside the sandbox, authenticated by the **subscription OAuth token**, not an API key. The orchestrator validates `~/.claude/.credentials.json` on the host *before* building the image, then `run_sandboxed_review.sh` copies it into a per-run temp dir mounted **writable** at `/tmp/claude` (`CLAUDE_CONFIG_DIR`). Writable because the CLI persists a refreshed token; per-run because a container reviewing an untrusted external PR must never write back to the operator's own credentials. The copy is removed by an `EXIT` trap.

**Rotation is manual and it is a real operational dependency.** A token with no `refreshToken` cannot self-renew — the 2026-07-16 token sat expired for 16 days and every run would have failed with a 401. The orchestrator now refuses to start and logs `Claude OAuth credentials unusable` instead. To renew:

```bash
sudo -u hermes -H claude          # complete the login flow, then /exit
sudo -u hermes -H claude -p "say OK"   # verify
```

**Accepted risk (operator, 2026-08-02):** the OAuth token carries the whole subscription (`user:inference`, `user:profile`, `user:sessions:claude_code`) — broader than a scoped API key would be — and it is mounted into a container that reviews untrusted external PRs. The egress firewall in §3 is the compensating control. Accepted because no API key exists for this account.

### Deploy mechanism (warning)

Do **NOT** register the orchestrator directly via `hermes cron create --script pr_triage_autopilot.py`. Hermes' cron runs scripts with hermes-agent's own interpreter — which lacks this repo's dependencies (e.g. `structlog`) — and only accepts scripts under `HERMES_HOME/scripts/`. Supported mechanisms:

- **(a) Plain crontab** — the line above, under the `hermes` system user.
- **(b) Thin shim in `HERMES_HOME/scripts/`** — a script that does `cd /opt/gflow-cli && exec uv run python scripts/autopilot/pr_triage_autopilot.py "$@"` (mirrors the existing EV ops-health shim pattern), registered with `hermes cron create "1h" --no-agent --script <shim> --deliver telegram`.

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
   uv run python scripts/autopilot/pr_triage_autopilot.py --repo-dir /opt/gflow-cli --memory-dir /opt/experience-vault/projects/C--development-github-gflow-cli/memory
   ```
