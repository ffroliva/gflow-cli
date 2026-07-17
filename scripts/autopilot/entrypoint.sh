#!/bin/bash
set -e

# Setup writable project memory via symlink to the mounted read-only memory dir
mkdir -p /tmp/claude/projects/C--development-github-gflow-cli
ln -sf /memory /tmp/claude/projects/C--development-github-gflow-cli/memory

# Execute the passed command
exec "$@"
