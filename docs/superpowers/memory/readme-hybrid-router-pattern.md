---
name: readme-hybrid-router-pattern
description: "README structure that worked for v0.8.1 — ~150-line hybrid router (claude-code + llm + ripgrep patterns) leading with a trust-signal warning, 60-second quick start, in-depth link, demo, docs TOC, AI-agent block, status, license; offload everything else to docs/INDEX.md"
---

For gflow-cli READMEs, target **~150 lines** structured as a router, not a manual.

**Sections in order** (proven on v0.8.1):
1. H1 + one-line tagline + 5 badges.
2. **Trust-signal warnings as banners** (not footnotes): unofficial-tool callout + headed-browser-today line above the fold. Counter-intuitively, leading with caveats builds trust faster than hiding them.
3. "Why X?" — 3 bullets, max 12 lines.
4. **60-second quick start** — 3 numbered commands, copy-paste-runnable.
5. In-depth quick start — one line linking to `docs/USER_GUIDE.md § Journey 1`.
6. Demo (keep distinctive GIF if you have one).
7. Documentation TOC — ripgrep-style emoji-categorised quick-links table pointing at `docs/INDEX.md`.
8. **For AI agents & LLMs** — table of agent entry points (AGENTS.md / CLAUDE.md / llms.txt / SKILL.md) with audience + tool list per row, plus a one-line "paste this prompt to onboard your agent" snippet (continue.dev pattern).
9. Architecture & current limitations — short prose, link to `docs/ARCHITECTURE.md` for depth.
10. Project status — 1 sentence + link to `docs/PROJECT_STATUS.md`.
11. License & legal.
12. Optional acknowledgements footer.

**Why:** The hybrid claude-code/llm/ripgrep pattern outperformed the 398-line "everything in README" approach across all 3 LLM-council auditors on v0.8.1. First-time-user path measured under 5 minutes. Distinctive voice preserved (credit-burning pitch, demo GIF) while skim-time dropped from "scroll-fatigue" to "one screen."

**How to apply:** Use this skeleton on every major README rewrite. Aggressively offload to `docs/`: milestone tables → `docs/PROJECT_STATUS.md`; ASCII architecture diagrams → `docs/ARCHITECTURE.md`; release protocol → `RELEASE.md`. README's job is to route, not to teach.

Related: [[agents-md-vs-llms-txt]], [[llm-council-audit-protocol]], [[pypi-readme-staleness-fix]].
