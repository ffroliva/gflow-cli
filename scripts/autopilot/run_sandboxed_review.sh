#!/usr/bin/env bash
# run_sandboxed_review.sh — Run the PR review skill in an ephemeral Docker sandbox.
# Enforces read-only mounts, non-root user, and egress firewall rules.

set -eo pipefail

# Print usage
usage() {
  echo "Usage: $0 --pr <num> --repo <path> --memory <path> --token <gh_token> --creds <credentials.json>"
  exit 1
}

PR_NUM=""
HOST_REPO=""
HOST_MEMORY=""
GH_TOKEN=""
CREDS_FILE=""

# Parse arguments
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --pr) PR_NUM="$2"; shift 2 ;;
    --repo) HOST_REPO="$2"; shift 2 ;;
    --memory) HOST_MEMORY="$2"; shift 2 ;;
    --token) GH_TOKEN="$2"; shift 2 ;;
    --creds) CREDS_FILE="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

if [ -z "$PR_NUM" ] || [ -z "$HOST_REPO" ] || [ -z "$HOST_MEMORY" ] || [ -z "$GH_TOKEN" ] || [ -z "$CREDS_FILE" ]; then
  echo "Error: Missing required arguments."
  usage
fi

if [ ! -r "$CREDS_FILE" ]; then
  echo "Error: credentials file not readable: $CREDS_FILE"
  exit 1
fi

# Ensure absolute paths
HOST_REPO=$(cd "$HOST_REPO" && pwd)
HOST_MEMORY=$(cd "$HOST_MEMORY" && pwd)

# Per-run copy of the OAuth credentials, mounted WRITABLE at CLAUDE_CONFIG_DIR.
#
# Auth is the subscription OAuth token (~/.claude/.credentials.json), not an API
# key -- this deployment has no ANTHROPIC_API_KEY. Two reasons the container
# gets a copy rather than the host file itself:
#   1. writable: the claude CLI persists a refreshed token on renewal, which a
#      :ro mount turns into a hard failure mid-review;
#   2. per-run: a refresh (or corruption) inside a container reviewing an
#      untrusted external PR must never write back to the operator's own
#      credentials.
# The copy dies with the trap below, so a token never outlives its run.
CREDS_DIR=$(mktemp -d)
chmod 700 "$CREDS_DIR"
cp "$CREDS_FILE" "$CREDS_DIR/.credentials.json"
chmod 600 "$CREDS_DIR/.credentials.json"
# 65532 = the image's `nonroot` uid; the container must own its config dir to
# write a refreshed token back into it.
chown -R 65532:65532 "$CREDS_DIR" 2>/dev/null || true
trap 'rm -rf "$CREDS_DIR"' EXIT INT TERM

echo "Building Docker sandbox image..."
SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
docker build -t gflow-triage:latest -f "$SCRIPT_DIR/Dockerfile.triage" "$SCRIPT_DIR"

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
docker run --rm \
  --net "$NET_NAME" \
  -v "$HOST_REPO:/workspace:ro" \
  -v "$HOST_MEMORY:/memory:ro" \
  -v "$CREDS_DIR:/tmp/claude" \
  -e CLAUDE_CONFIG_DIR=/tmp/claude \
  -e GH_TOKEN="$GH_TOKEN" \
  -e GITHUB_TOKEN="$GH_TOKEN" \
  gflow-triage:latest \
  -p "Conduct a multi-dimensional council review of PR $PR_NUM in autonomous mode following /workspace/skills/pr-council-review/SKILL.md."
