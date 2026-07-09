# PR-Triage Autopilot Operations Runbook

This runbook guides the deployment, configuration, monitoring, and administration of the hourly PR-Triage Autopilot on the VPS host.

---

## 1. Environment & Credentials

The autopilot orchestrator (`scripts/autopilot/pr_triage_autopilot.py`) runs as an hourly cron job and requires the following environment variables:

| Variable | Scope | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Autopilot orchestrator & sandbox | Mints the Claude agent tokens to execute the council review. |
| `GH_COMMENT_TOKEN` | Autopilot orchestrator (host) | The comment-only GitHub PAT used to post verdicts and reviews to `ffroliva/gflow-cli` PRs. |
| `TELEGRAM_BOT_TOKEN` | Autopilot orchestrator (host) | Bot token to dispatch alert messages. |
| `TELEGRAM_USER_ID` | Autopilot orchestrator (host) | The chat ID to receive triage alert messages. |

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
0 * * * * cd /opt/gflow-cli && export ANTHROPIC_API_KEY="xxx" && export GH_COMMENT_TOKEN="xxx" && export TELEGRAM_BOT_TOKEN="xxx" && export TELEGRAM_USER_ID="xxx" && uv run python scripts/autopilot/pr_triage_autopilot.py --repo-dir /opt/gflow-cli --memory-dir /opt/experience-vault/projects/C--development-github-gflow-cli/memory >> /var/log/hermes/pr_triage.log 2>&1
```

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
