#!/usr/bin/env bash
# sonar-pre-push-gate.sh — SonarCloud health-check + local-Sonar fallback,
# run at git pre-push (wired via pre-commit's `stages: [pre-push]`).
#
# SonarCloud is the PRIMARY gate for gflow-cli (this project is open-source
# and gets free SonarCloud coverage), and CI already enforces it on every
# push/PR. This hook exists only so a push isn't silently left unassessed
# during a SonarCloud outage (seen live 2026-07-16):
#   - SonarCloud reachable -> CI will do the real enforcement; skip here so
#     pushes stay fast (no local Docker scan on every push).
#   - SonarCloud unreachable -> fall back to the shared local SonarQube
#     instance (scripts/dev/sonar-local-scan.sh) so there's still a real
#     quality-gate signal before the code ships. Gracefully skips (never
#     blocks a push) if the local infra isn't set up either -- most
#     contributors won't have it, and that must never be a blocker.
set -uo pipefail

if curl -sf -m 8 "https://sonarcloud.io/api/system/status" 2>/dev/null | grep -q '"status":"UP"'; then
  echo "[sonar] SonarCloud is UP -- CI will enforce the gate on push, skipping local scan."
  exit 0
fi

echo "[sonar] SonarCloud unreachable -- falling back to local SonarQube for this push."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$SCRIPT_DIR/sonar-local-scan.sh"
rc=$?
# sonar-local-scan.sh exits 0 for both "gate passed" and "infra unavailable,
# gracefully skipped" -- a non-zero here is always a genuine Quality Gate
# failure, never a missing-infra false alarm.
if [ "$rc" -ne 0 ]; then
  echo "[sonar] local Sonar Quality Gate FAILED -- push blocked. Fix the issues above."
fi
exit "$rc"
