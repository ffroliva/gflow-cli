---
name: agents-md-vs-llms-txt
description: "AGENTS.md is a repo-level coding-agent spec (agents.md, 60k+ repos, Cursor/Codex/Aider/Gemini CLI/Claude Code etc.); llms.txt is a website-level spec (llmstxt.org, served at /llms.txt). Repo-level llms.txt is forward-staged for future docs site."
---

Two different conventions, easy to confuse:

| File | Spec | Audience | Where it lives |
|---|---|---|---|
| `AGENTS.md` | [agents.md](https://agents.md) | AI coding agents (Cursor, Codex, Aider, Gemini CLI, Jules, Devin, Windsurf, Zed, Warp, opencode, Copilot, Claude Code, …) | Repo root |
| `llms.txt` | [llmstxt.org](https://llmstxt.org) | LLMs reading docs at inference time (like robots.txt but for LLM crawlers) | Served at `https://<docs-site>/llms.txt` |

**Practical implications:**

- **Add `AGENTS.md` to every repo** with non-trivial conventions. Conventional sections: `## Dev environment tips`, `## Testing instructions`, `## Code style`, `## PR instructions`. No required fields. Tools find the closest AGENTS.md to the edited file (nested AGENTS.md supported for monorepos).
- **Add `llms.txt` only when you have a docs site** (mkdocs, sphinx, GitHub Pages). The format is markdown: H1 project name + blockquote summary + H2 link lists (`## Docs`, `## Optional`).
- **In a repo without a docs site**, dropping `llms.txt` at the root is harmless and forward-stages the file for when you do publish docs — some LLM agents already check repo roots for it. Mark it explicitly as "forward-staged" in the file body so future maintainers know.
- **CLAUDE.md is Claude-Code-specific.** It is NOT a substitute for AGENTS.md. The pattern that works: CLAUDE.md carries auto-load instructions Claude Code reads natively, and it cross-references AGENTS.md for the universal rules. ~25 lines for CLAUDE.md is enough.

Related: [[readme-hybrid-router-pattern]].
