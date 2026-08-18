#!/bin/bash
set -e

# No memory symlink here: council memory is bind-mounted straight to the path
# SKILL.md D5 reads. This used to link /memory into CLAUDE_CONFIG_DIR, which
# resolved to nothing -- Claude derives its project slug from the cwd
# (/workspace), never from the workstation slug this path was named after.

# Execute the passed command
exec "$@"
