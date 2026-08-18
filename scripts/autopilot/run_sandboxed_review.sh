#!/usr/bin/env bash
# run_sandboxed_review.sh — Run the PR review skill in an ephemeral Docker sandbox.
# Enforces read-only mounts, non-root user, and egress firewall rules.

set -eo pipefail

# Print usage
usage() {
  echo "Usage: $0 --pr <num> --repo <path> --memory <path> --token <gh_read_token>"
  echo "  --token MUST be read-only (GH_SANDBOX_TOKEN). The container reads the PR;"
  echo "  the host orchestrator posts the comment with the write-scoped token."
  echo "  Claude auth comes from CLAUDE_CODE_OAUTH_TOKEN in the environment."
  exit 1
}

PR_NUM=""
HOST_REPO=""
HOST_MEMORY=""
GH_TOKEN=""

# Parse arguments
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --pr) PR_NUM="$2"; shift 2 ;;
    --repo) HOST_REPO="$2"; shift 2 ;;
    --memory) HOST_MEMORY="$2"; shift 2 ;;
    --token) GH_TOKEN="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

if [ -z "$PR_NUM" ] || [ -z "$HOST_REPO" ] || [ -z "$HOST_MEMORY" ] || [ -z "$GH_TOKEN" ]; then
  echo "Error: Missing required arguments."
  usage
fi

# Claude auth: the subscription token minted by `claude setup-token`, read from
# the environment (sourced from /opt/hermes/.env by the cron line). Deliberately
# NOT a CLI flag -- an argv secret is visible to every local user via `ps`,
# which is what the original --key <anthropic_key> design did.
#
# Note this is NOT ~/.claude/.credentials.json: `setup-token` does not write
# that file (verified on the ops VPS 2026-08-02 -- it still held the expired
# 2026-07-16 interactive-login token afterwards). The two are separate
# mechanisms and only the env var carries the 1-year credential.
if [ -z "$CLAUDE_CODE_OAUTH_TOKEN" ]; then
  echo "Error: CLAUDE_CODE_OAUTH_TOKEN is not set."
  echo "  Mint one with: sudo -u hermes -H claude setup-token   (valid 1 year)"
  echo "  Then store it in hermes-ops secrets/vps-prod.env.sops.yaml."
  exit 1
fi

# Ensure absolute paths. Guarded first: a bare `cd` reports only the path, so a
# bad argument reads as a mystery filesystem error rather than a named flag.
[ -d "$HOST_REPO" ] || { echo "Error: --repo is not a directory: $HOST_REPO"; exit 1; }
[ -d "$HOST_MEMORY" ] || { echo "Error: --memory is not a directory: $HOST_MEMORY"; exit 1; }
HOST_REPO=$(cd "$HOST_REPO" && pwd)
HOST_MEMORY=$(cd "$HOST_MEMORY" && pwd)

# Self-heal before we start. The cleanup trap below fires on EXIT, which a
# SIGKILL (OOM, hard reboot) skips entirely -- stranding that run's network.
# `docker network rm` refuses a network with attached containers, so this can
# never disturb a concurrent review.
for stale_net in $(docker network ls --filter "name=triage-net-" --format '{{.Name}}' 2>/dev/null); do
  docker network rm "$stale_net" &>/dev/null && echo "Swept stale network $stale_net" || true
done

echo "Building Docker sandbox image..."
SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
docker build -t gflow-triage:latest -f "$SCRIPT_DIR/Dockerfile.triage" "$SCRIPT_DIR"

# The build reuses one tag, so whenever the Dockerfile or its context changes
# the previous ~1GB image is orphaned as dangling. The label filter keeps this
# scoped to our own images -- never other projects' on a shared host.
docker image prune -f --filter "label=app=gflow-triage" &>/dev/null || true

NET_NAME="triage-net-$PR_NUM"
echo "Creating network $NET_NAME..."
docker network create "$NET_NAME" || true

SUBNET=$(docker network inspect "$NET_NAME" -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null || true)

# Cleanup trap
cleanup() {
  echo "Cleaning up network rules and Docker network..."
  if [ -n "$SUBNET" ] && command -v iptables &> /dev/null; then
    sudo iptables -D FORWARD -s "$SUBNET" -j DROP &>/dev/null || true
    for ip in $(getent ahosts github.com | awk '{print $1}' | sort -u); do
      sudo iptables -D FORWARD -s "$SUBNET" -d "$ip" -p tcp --dport 443 -j ACCEPT &>/dev/null || true
    done
    for ip in $(getent ahosts api.anthropic.com | awk '{print $1}' | sort -u); do
      sudo iptables -D FORWARD -s "$SUBNET" -d "$ip" -p tcp --dport 443 -j ACCEPT &>/dev/null || true
    done
    sudo iptables -D FORWARD -s "$SUBNET" -p tcp --dport 53 -j ACCEPT &>/dev/null || true
    sudo iptables -D FORWARD -s "$SUBNET" -p udp --dport 53 -j ACCEPT &>/dev/null || true
  fi
  docker network rm "$NET_NAME" &>/dev/null || true
}
trap cleanup EXIT

# Apply host iptables firewall restrictions if run with sudo/iptables access
if [ -n "$SUBNET" ] && command -v iptables &> /dev/null; then
  echo "Hardening network isolation for subnet $SUBNET via iptables..."
  # Allow DNS
  sudo iptables -I FORWARD -s "$SUBNET" -p udp --dport 53 -j ACCEPT
  sudo iptables -I FORWARD -s "$SUBNET" -p tcp --dport 53 -j ACCEPT
  
  # Allow api.anthropic.com
  for ip in $(getent ahosts api.anthropic.com | awk '{print $1}' | sort -u); do
    sudo iptables -I FORWARD -s "$SUBNET" -d "$ip" -p tcp --dport 443 -j ACCEPT
  done
  
  # Allow github.com
  for ip in $(getent ahosts github.com | awk '{print $1}' | sort -u); do
    sudo iptables -I FORWARD -s "$SUBNET" -d "$ip" -p tcp --dport 443 -j ACCEPT
  done
  
  # Drop everything else from this subnet
  sudo iptables -A FORWARD -s "$SUBNET" -j DROP
else
  echo "Warning: iptables or network subnet lookup not available. Firewall rules skipped."
fi

echo "Launching sandboxed review for PR $PR_NUM..."
# The council memory mount target is not arbitrary: SKILL.md D5 tells the
# reviewer to inspect ~/.claude/projects/<slug>/memory, and $HOME in the image
# is /home/nonroot. It was /memory until 2026-08-18, so the reviewer read a
# path that did not exist and the council ran with no memory at all while the
# mount looked perfectly healthy from the host.
docker run --rm \
  --net "$NET_NAME" \
  -v "$HOST_REPO:/workspace:ro" \
  -v "$HOST_MEMORY:/home/nonroot/.claude/projects/C--development-github-gflow-cli/memory:ro" \
  -e CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN" \
  -e GH_TOKEN="$GH_TOKEN" \
  -e GITHUB_TOKEN="$GH_TOKEN" \
  gflow-triage:latest \
  claude -p "Conduct a multi-dimensional council review of PR $PR_NUM in autonomous mode following /workspace/skills/pr-council-review/SKILL.md."
