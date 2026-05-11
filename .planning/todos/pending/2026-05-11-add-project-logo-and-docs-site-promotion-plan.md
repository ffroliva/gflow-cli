---
created: 2026-05-11T11:34:56.295Z
title: Add project logo and docs site promotion plan
area: docs
files:
  - README.md:18
  - README.md:365
  - RELEASE.md:1
  - docs/INDEX.md:10
---

## Problem

The project is now public on GitHub and PyPI and has enough release/process
surface to benefit from a more polished presentation. The README was cleaned up
with working badges, a star-history chart, and a public `RELEASE.md`, but the
next promotion step is still undecided: whether to add a logo/social preview,
whether to create a GitHub Pages documentation site, and which static-docs stack
to use.

Jekyll is available on GitHub Pages, but for this Python CLI project the better
default is likely MkDocs Material: it fits Markdown-heavy CLI docs, has search
and navigation, and can publish to GitHub Pages cleanly. This should wait until
after the current prerelease E2E validation, because the real launch gate is
runtime confidence, not site polish.

## Solution

After E2E validation, decide a small promotion package:

1. Add a simple project logo and GitHub social preview image.
2. Keep README as the landing page and make the first screen clear for users.
3. If docs navigation grows cramped, add a MkDocs Material site published to
   GitHub Pages.
4. Avoid Jekyll unless the goal shifts toward a blog-style marketing site.
5. Keep `RELEASE.md`, README release policy, and `.claude/commands/release.md`
   in sync when the release process changes.
