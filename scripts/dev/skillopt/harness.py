#!/usr/bin/env python3
"""
SkillOpt mock harness for gflow-cli skills.

Runs a scored task suite against a skill document to measure how well
agents guided by that skill answer gflow-cli usage questions.
Mimics the SkillOpt rollout→score loop without the automated edit step,
making it easy to measure baseline accuracy before / after manual edits.

Supports multiple LLM providers so you can compare how different agents
perform against the same skill doc:

Provider: anthropic (default)
    ANTHROPIC_API_KEY=sk-ant-... python scripts/dev/skillopt/harness.py
    python scripts/dev/skillopt/harness.py --model claude-sonnet-4-6

Provider: openai  (GPT-4o, Gemini OpenAI-compat, local Ollama, LM Studio, …)
    OPENAI_API_KEY=sk-... python scripts/dev/skillopt/harness.py \\
        --provider openai --model gpt-4o

    # Gemini via its OpenAI-compatible endpoint
    OPENAI_API_KEY=$GEMINI_API_KEY python scripts/dev/skillopt/harness.py \\
        --provider openai \\
        --base-url https://generativelanguage.googleapis.com/v1beta/openai/ \\
        --model gemini-2.0-flash

    # Local model via Ollama
    python scripts/dev/skillopt/harness.py \\
        --provider openai --base-url http://localhost:11434/v1 \\
        --model llama3.2 --api-key ollama

Other flags:
    --dry-run          Print prompts + scoring spec; no API call
    --skill PATH       Point at a different skill version
    --tags auth,video  Filter tasks by tag
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
TASKS_PATH = Path(__file__).parent / "tasks.json"
DEFAULT_SKILL_PATH = REPO_ROOT / "skills" / "gflow-cli" / "SKILL.md"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_PROVIDER = "anthropic"

SYSTEM_TEMPLATE = """\
You are an agent assistant helping users operate gflow-cli, the unofficial terminal CLI for Google Flow (Veo video generation and Imagen image generation).

Your job is to answer the user's question with the exact CLI command(s) or code they should run.
- Output only the command(s) / code snippet — no markdown fences unless showing multi-line code.
- If the task asks a yes/no or remediation question, give a concise direct answer followed by the command.
- Do not add lengthy explanations unless the task is specifically about concepts.

Use the skill reference below as your authoritative source.

=== SKILL REFERENCE (gflow-cli v{version}, epoch {epoch}) ===
{skill_body}
=== END SKILL REFERENCE ==="""


def load_tasks(path: Path, tags: list[str] | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = json.loads(path.read_text())
    if tags:
        tag_set = set(tags)
        items = [t for t in items if tag_set.intersection(t.get("tags", []))]
    return items


def load_skill(path: Path) -> tuple[str, str, int]:
    """Return (full_text, version, epoch) from the skill file."""
    text = path.read_text()
    version = "unknown"
    epoch = 0
    for line in text.splitlines():
        if line.startswith("version:"):
            version = line.split(":", 1)[1].strip().strip('"')
        elif line.startswith("skillopt_epoch:"):
            try:
                epoch = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    return text, version, epoch


def score_response(response: str, expected: dict[str, Any]) -> tuple[float, list[str]]:
    """Score a response. Returns (score 0.0–1.0, list of reason strings)."""
    resp_lower = response.lower()
    reasons: list[str] = []

    must_include: list[str] = expected.get("must_include", [])
    must_not_include: list[str] = expected.get("must_not_include", [])
    partial_credit: list[str] = expected.get("partial_credit", [])

    hits = sum(1 for item in must_include if item.lower() in resp_lower)
    misses = [item for item in must_include if item.lower() not in resp_lower]
    base = hits / len(must_include) if must_include else 1.0

    forbidden_hits = [p for p in must_not_include if p.lower() in resp_lower]
    penalty = min(len(forbidden_hits) * 0.3, 0.9)

    bonus_hits = [p for p in partial_credit if p.lower() in resp_lower]
    bonus = min(len(bonus_hits) * 0.1, 0.3)

    score = max(0.0, min(1.0, base - penalty + bonus))

    if misses:
        reasons.append(f"MISS: {misses}")
    if forbidden_hits:
        reasons.append(f"FORBIDDEN hit: {forbidden_hits}")
    if bonus_hits:
        reasons.append(f"BONUS: {bonus_hits}")

    return score, reasons


def format_prompt(skill_text: str, version: str, epoch: int, task: dict[str, Any]) -> tuple[str, str]:
    system = SYSTEM_TEMPLATE.format(
        version=version, epoch=epoch, skill_body=skill_text
    )
    question = task["question"]
    context = task.get("context")
    user = f"Context: {context}\n\n{question}" if context else question
    return system, user


def run_dry(tasks: list[dict[str, Any]], skill_text: str, version: str, epoch: int) -> None:
    print(f"=== DRY RUN — {len(tasks)} task(s) — skill v{version} epoch {epoch} ===\n")
    for task in tasks:
        system, user = format_prompt(skill_text, version, epoch, task)
        print(f"--- [{task['id']}] {task['question'][:80]} ---")
        print(f"USER PROMPT:\n{user}\n")
        print(f"SCORING: {task['expected']}\n")
        print(f"NOTES: {task.get('notes', '')}\n")
        print()


def _call_anthropic(system: str, user: str, model: str, api_key: str) -> str:
    try:
        import anthropic
    except ImportError:
        sys.exit(
            "anthropic package not found. Install it:\n"
            "  uv pip install anthropic"
        )
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text.strip()


def _call_openai_compat(
    system: str, user: str, model: str, api_key: str, base_url: str | None
) -> str:
    try:
        import openai
    except ImportError:
        sys.exit(
            "openai package not found. Install it:\n"
            "  uv pip install openai"
        )
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = openai.OpenAI(**kwargs)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=512,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def run_live(
    tasks: list[dict[str, Any]],
    skill_text: str,
    version: str,
    epoch: int,
    model: str,
    api_key: str,
    provider: str = DEFAULT_PROVIDER,
    base_url: str | None = None,
) -> None:
    results: list[dict[str, Any]] = []

    print(
        f"=== LIVE RUN — {len(tasks)} task(s) — skill v{version} epoch {epoch}"
        f" — provider {provider} — model {model} ===\n"
    )

    for i, task in enumerate(tasks, 1):
        system, user = format_prompt(skill_text, version, epoch, task)
        print(f"[{i}/{len(tasks)}] {task['id']}: {task['question'][:70]}...")

        if provider == "anthropic":
            response = _call_anthropic(system, user, model, api_key)
        elif provider == "openai":
            response = _call_openai_compat(system, user, model, api_key, base_url)
        else:
            sys.exit(f"Unknown provider '{provider}'. Use 'anthropic' or 'openai'.")

        score, reasons = score_response(response, task["expected"])

        status = "PASS" if score >= 0.8 else ("PARTIAL" if score >= 0.4 else "FAIL")
        print(f"  Score: {score:.2f} [{status}]  {' | '.join(reasons) if reasons else 'OK'}")
        print(f"  Response: {response[:120].replace(chr(10), ' ')}")

        results.append(
            {
                "id": task["id"],
                "tags": task.get("tags", []),
                "score": score,
                "status": status,
                "response_preview": response[:200],
                "reasons": reasons,
            }
        )

    _print_summary(results)


def _print_summary(results: list[dict[str, Any]]) -> None:
    total = len(results)
    if total == 0:
        print("\nNo results.")
        return

    avg = sum(r["score"] for r in results) / total
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = [r for r in results if r["status"] == "FAIL"]

    print(f"\n{'='*60}")
    print(f"SUMMARY: {passed}/{total} passed  avg score {avg:.3f}")

    if failed:
        print(f"\nFailed tasks ({len(failed)}):")
        for r in failed:
            print(f"  {r['id']}: {r['reasons']}")

    tag_scores: dict[str, list[float]] = {}
    for r in results:
        for tag in r.get("tags", []):
            tag_scores.setdefault(tag, []).append(r["score"])

    if tag_scores:
        print("\nPer-tag averages:")
        for tag, scores in sorted(tag_scores.items()):
            print(f"  {tag:<20} {sum(scores)/len(scores):.3f}  (n={len(scores)})")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SkillOpt mock harness for gflow-cli",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--skill",
        type=Path,
        default=DEFAULT_SKILL_PATH,
        help="Path to the skill markdown file (default: skills/gflow-cli/SKILL.md)",
    )
    parser.add_argument(
        "--tasks",
        type=Path,
        default=TASKS_PATH,
        help="Path to the tasks JSON file (default: scripts/dev/skillopt/tasks.json)",
    )
    parser.add_argument(
        "--tags",
        type=str,
        default=None,
        help="Comma-separated tag filter, e.g. 'auth,video'",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=DEFAULT_PROVIDER,
        choices=["anthropic", "openai"],
        help=(
            "LLM provider (default: anthropic). "
            "Use 'openai' for GPT-4o, Gemini (via --base-url), or any OpenAI-compat endpoint."
        ),
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help=(
            f"Model ID. Defaults: anthropic={DEFAULT_MODEL}, openai=gpt-4o. "
            "For Gemini: gemini-2.0-flash. For Ollama: llama3.2."
        ),
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        dest="base_url",
        help=(
            "OpenAI-compatible base URL (openai provider only). "
            "Examples: https://generativelanguage.googleapis.com/v1beta/openai/ (Gemini), "
            "http://localhost:11434/v1 (Ollama). "
            "WARNING: do not point at internal/metadata endpoints in CI — "
            "this value is passed directly to the SDK without validation."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print prompts and scoring spec without calling the API",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help=(
            "API key. Prefer env vars ($ANTHROPIC_API_KEY / $OPENAI_API_KEY) over this flag — "
            "CLI flags are visible in process listings (ps aux). "
            "This flag exists only for scripting contexts where env vars are unavailable."
        ),
    )
    args = parser.parse_args()

    if not args.skill.exists():
        sys.exit(f"Skill file not found: {args.skill}")
    if not args.tasks.exists():
        sys.exit(f"Tasks file not found: {args.tasks}")

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else None
    tasks = load_tasks(args.tasks, tags)
    if not tasks:
        sys.exit("No tasks matched the given filter.")

    skill_text, version, epoch = load_skill(args.skill)

    provider_defaults = {"anthropic": DEFAULT_MODEL, "openai": "gpt-4o"}
    model = args.model or provider_defaults.get(args.provider, DEFAULT_MODEL)

    if args.dry_run:
        run_dry(tasks, skill_text, version, epoch)
    else:
        env_key = "ANTHROPIC_API_KEY" if args.provider == "anthropic" else "OPENAI_API_KEY"
        api_key = args.api_key or os.environ.get(env_key)
        if not api_key:
            sys.exit(f"No API key. Set ${env_key} or pass --api-key.")
        run_live(
            tasks, skill_text, version, epoch, model, api_key,
            provider=args.provider, base_url=args.base_url,
        )


if __name__ == "__main__":
    main()
