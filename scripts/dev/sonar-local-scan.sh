#!/usr/bin/env bash
# sonar-local-scan.sh — run the shared SonarQube Community Edition scan against
# gflow-cli's own sonar-project.properties, for use when SonarCloud (the CI
# primary — this project is open-source and gets free SonarCloud coverage) is
# unreachable. NOT wired into CI: the local SonarQube CE server (docker, on
# this machine / shared-infra) is never reachable from GitHub-hosted runners.
# This is a manual/local pre-flight, or what the pre-push hook falls back to.
#
# gflow-cli-specific wrapper around shared-infra/sonarqube/sonar-gate.sh: this
# repo's coverage command must be the Windows-safe form (`uv run pytest` is
# broken on Windows for this project — see memory windows-dev-quirks), which
# the shared gate's own auto-default does not know.
#
# Usage (from repo root):
#   bash scripts/dev/sonar-local-scan.sh
#
# Env:
#   SHARED_INFRA_DIR   default ../shared-infra (sibling checkout)
#   SONAR_HOST_URL      default http://localhost:9000
#   SONAR_TOKEN         required — SonarQube > My Account > Security > Generate Tokens
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

SHARED="${SHARED_INFRA_DIR:-$ROOT/../shared-infra}"
GATE="$SHARED/sonarqube/sonar-gate.sh"

# Graceful skip (exit 0), not a hard failure: most contributors won't have
# shared-infra checked out, and that must never block anything -- matches
# sonar-gate.sh's own "never block on missing optional infra" convention.
if [ ! -f "$GATE" ]; then
  echo "[sonar-local] SKIP: shared-infra not found at $SHARED (set SHARED_INFRA_DIR to enable)."
  exit 0
fi

echo "[sonar-local] scanning gflow-cli against the local SonarQube instance ..."
echo "[sonar-local] (start it first if needed: bash $SHARED/sonarqube/start.sh)"

SONAR_COVERAGE_CMD="${SONAR_COVERAGE_CMD:-.venv/Scripts/python.exe -m pytest --cov=src --cov-report=xml -q}" \
  bash "$GATE"
