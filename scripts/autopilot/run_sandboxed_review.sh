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
    for ip in $(getent ahostsv4 github.com | awk '{print $1}' | sort -u); do
      sudo iptables -D FORWARD -s "$SUBNET" -d "$ip" -p tcp --dport 443 -j ACCEPT &>/dev/null || true
    done
    for ip in $(getent ahostsv4 api.anthropic.com | awk '{print $1}' | sort -u); do
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
  
  # ahostsv4, not ahosts: api.anthropic.com also resolves to an AAAA record,
  # and iptables (v4) rejects an IPv6 destination outright --
  #   iptables v1.8.10 (nf_tables): host/network '2607:6bc0::10' not found
  # Under `set -e` that aborted the whole script before `docker run`, so every
  # review died during firewall setup. The bridge network is IPv4-only
  # (no --ipv6 on `docker network create`), so v4 rules cover all its egress.
  # Allow api.anthropic.com
  for ip in $(getent ahostsv4 api.anthropic.com | awk '{print $1}' | sort -u); do
    sudo iptables -I FORWARD -s "$SUBNET" -d "$ip" -p tcp --dport 443 -j ACCEPT
  done
  
  # Allow github.com
  for ip in $(getent ahostsv4 github.com | awk '{print $1}' | sort -u); do
    sudo iptables -I FORWARD -s "$SUBNET" -d "$ip" -p tcp --dport 443 -j ACCEPT
  done
  
  # Drop everything else from this subnet
  sudo iptables -A FORWARD -s "$SUBNET" -j DROP
else
  echo "Warning: iptables or network subnet lookup not available. Firewall rules skipped."
fi

echo "Launching sandboxed review for PR $PR_NUM..."
COUNCIL_TOOLS="Bash(gh auth status:*) Bash(gh pr view:*) Bash(gh pr diff:*) Bash(gh pr checks:*) Bash(gh pr list:*) Bash(git show:*) Bash(git rev-parse:*) Bash(git diff:*) Bash(git log:*) Bash(git ls-remote:*) Bash(grep:*) Bash(sort:*) Bash(head:*) Bash(tail:*) Bash(wc:*) Bash(awk:*) Bash(jq:*) Bash(cat:*) Bash(ls:*) Bash(comm:*) Bash(cut:*) Bash(uniq:*) Bash(tr:*) Read Grep Glob Task TodoWrite"
COUNCIL_MEMORY_DIR="/home/nonroot/.claude/projects/C--development-github-gflow-cli/memory"
# The council memory mount target is not arbitrary: SKILL.md D5 tells the
# reviewer to inspect ~/.claude/projects/<slug>/memory, and $HOME in the image
# is /home/nonroot. It was /memory until 2026-08-18, so the reviewer read a
# path that did not exist and the council ran with no memory at all while the
# mount looked perfectly healthy from the host.
# --add-dir is the other half: the tree sits outside the /workspace cwd, so
# without it the agent is permission-denied and reports no memory at all.
# It must follow the positional prompt -- it is variadic and swallows it.
# COUNCIL_TOOLS: in -p mode a permission prompt is an auto-deny, not a pause --
# every `gh` call came back "This command requires approval" and the reviewer
# halted to ask a question nobody reads. The gate still has to go, but only for
# the reads the protocol actually makes.
#
# Deliberately NOT --dangerously-skip-permissions. That would also unlock Write,
# Edit and arbitrary Bash, and the permission gate is the only technical
# enforcement of SKILL.md section 9's no-write-tools rule. The container bounds a
# different threat (host and repo compromise) than that rule does: the agent
# ingests PR diffs and comments an external contributor controls, so an
# unrestricted tool surface in here is a confused-deputy path, not a sandbox
# escape. Raised by the council reviewing PR #557 against itself.
#
# The list is enumerated from SKILL.md's own invocations, read-only subcommands
# only. Deliberately absent: gh pr merge/review/ready/comment/close and gh auth
# login, git push/stash/tag/worktree/branch. The host posts the review comment
# with the write-scoped token; the container's PAT 403s on POST regardless.
#
# Widen only with evidence: a missing entry shows up as the reviewer stalling on
# a denied tool (`gh auth status` was the first, found by running it), never as
# a wrong verdict.
docker run --rm \
  --net "$NET_NAME" \
  -v "$HOST_REPO:/workspace:ro" \
  -v "$HOST_MEMORY:$COUNCIL_MEMORY_DIR:ro" \
  -e CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN" \
  -e GH_TOKEN="$GH_TOKEN" \
  -e GITHUB_TOKEN="$GH_TOKEN" \
  gflow-triage:latest \
  claude -p "Conduct a multi-dimensional council review of PR $PR_NUM in autonomous mode following /workspace/skills/pr-council-review/SKILL.md." \
  --add-dir "$COUNCIL_MEMORY_DIR" \
  --allowedTools "$COUNCIL_TOOLS"
