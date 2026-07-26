# Codex `gflow:*` Plugin Design

## Goal

Make the canonical workflows under `skills/` directly installable in Codex under the
`gflow:` namespace while preserving the existing Claude Code `/gflow:*` commands.

## Design

The repository root becomes a skills-only Codex plugin. A
`.codex-plugin/plugin.json` manifest gives the bundle the stable plugin name `gflow`
and points at the existing `./skills/` directory. The canonical skill bodies remain
where they are; no copies or generated wrappers are introduced.

A repo marketplace at `.agents/plugins/marketplace.json` exposes that root plugin to
Codex. Its local source is `./`, relative to the repository root. Codex CLI users
register the marketplace once with `codex plugin marketplace add .`, then install with
`codex plugin add gflow@gflow-cli`. After installation, Codex namespaces each bundled
skill by plugin identity, so `status` is invoked as `$gflow:status`, `check` as
`$gflow:check`, and so on. Codex custom slash prompts are deprecated and local-only, so
this design does not attempt to recreate Claude Code's `/gflow:*` spelling.

## Compatibility

- Claude Code continues to use the thin wrappers in `.claude/commands/gflow/`.
- Codex CLI and the Codex desktop app use the installable `gflow` plugin.
- Other agents continue to consume the canonical `skills/*/SKILL.md` files directly.
- The Codex IDE extension does not currently support plugins; its fallback remains the
  repository instructions and direct skill-file loading documented in `AGENTS.md`.

## Validation

A focused pytest contract will verify that:

1. the plugin manifest uses the `gflow` namespace and targets `./skills/`;
2. the repo marketplace exposes the root plugin with the required install policy; and
3. every directory packaged from `skills/` contains a `SKILL.md` whose frontmatter name
   matches its directory; and
4. the canonical release instructions bump the plugin version with the package version.

The built-in Codex plugin validator and the installed Codex CLI will then validate the
real package and marketplace discovery. Repository hygiene and documentation link gates
remain required. No live Google Flow run is needed because this change does not touch a
generation path.
