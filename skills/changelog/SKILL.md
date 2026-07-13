---
name: changelog
version: "1.0"
description: >
  Show unreleased changes and last tagged version — insight into recent work.
---

# `/gflow:changelog` — Recent changes

Read CHANGELOG.md and surface what's queued and what recently shipped.

## Steps

1. Read [CHANGELOG.md](../../CHANGELOG.md)
2. Return:
   - All entries under `## [Unreleased]` (empty = nothing queued yet)
   - The most recent versioned section (last tagged release) for comparison
3. One-line summary: "X features, Y fixes queued since vZ."

## When to call

- Before cutting a release (called automatically by `/gflow:release` as its first step)
- When you need a quick picture of recent work without opening the file
- When writing a commit message and unsure if a change is user-visible enough to log
