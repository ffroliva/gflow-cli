#!/usr/bin/env python3
r"""
SkillOpt mock harness for gflow-cli skills.

Runs a scored task suite against a skill document to measure how well
agents guided by that skill answer gflow-cli usage questions.
Mimics the SkillOpt rollout→score loop without the automated edit step,
making it easy to measure baseline accuracy before / after manual edits.

Run it through ``uv run`` — it imports ``gflow_cli`` for its LLM configuration
and transport, so a bare ``python`` outside the project venv cannot import it.

Uses the project's own LLM configuration — the same ``GFLOW_CLI_LLM_*`` settings
the prompt tools use (docs/CONFIGURATION.md). Any OpenAI-compatible Chat
Completions endpoint works: OpenAI, a gateway/proxy (OpenRouter, LiteLLM,
freellmapi, ...), a local Ollama/LM Studio, or Google's compat endpoint.

    # Google's compat endpoint (the default) — a key is all you need
    GFLOW_CLI_LLM_API_KEY=AIza... uv run python scripts/dev/skillopt/harness.py

    # OpenRouter, or any gateway
    GFLOW_CLI_LLM_API_KEY=sk-or-... \
    GFLOW_CLI_LLM_BASE_URL=https://openrouter.ai/api/v1 \
    GFLOW_CLI_LLM_MODEL=openai/gpt-4o-mini \
        uv run python scripts/dev/skillopt/harness.py

    # Local keyless gateway — no credential is sent at all
    GFLOW_CLI_LLM_BASE_URL=http://127.0.0.1:11434/v1 \
    GFLOW_CLI_LLM_MODEL=llama3.2 uv run python scripts/dev/skillopt/harness.py

The endpoint and credential come from those settings only — no second place to
configure a provider, and no way for the two to disagree.

Other flags:
    --dry-run          Print prompts + scoring spec; no API call, no key needed
    --skill PATH       Point at a different skill version
    --tasks PATH       Task suite to score against — must match --skill; the
                       defaults are the gflow-cli skill and ITS tasks, so a
                       --skill from elsewhere needs its --tasks passed too
    --model ID         Override GFLOW_CLI_LLM_MODEL for one comparison run
    --tags auth,video  Filter tasks by tag
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

from gflow_cli.config import DEFAULT_LLM_BASE_URL, get_settings
from gflow_cli.data.redaction import redact_error_detail
from gflow_cli.tools.expander import (
    _RETRYABLE_STATUS,
    DEFAULT_TIMEOUT,
    LlmHttpError,
    PromptExpander,
    _default_transport,
    resolve_model,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TASKS_PATH = Path(__file__).parent / "tasks.json"
DEFAULT_SKILL_PATH = REPO_ROOT / "skills" / "gflow-cli" / "SKILL.md"
#: Retry budget per task, and the wait that clears one per-minute quota window.
_MAX_ATTEMPTS = 4
_QUOTA_WINDOW_SECONDS = 30


class RolloutError(RuntimeError):
    """One task's rollout could not be completed. The suite records it and goes on."""

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

    # Rounded because the grade is compared against a hard >= 0.8 threshold and
    # 1.0 - 0.3 + 0.1 is 0.7999999999999999 in binary float — a task scoring
    # exactly the threshold graded PARTIAL while printing "0.80". 4 decimals is
    # finer than any rule here produces; the coarsest term is 0.1.
    score = round(max(0.0, min(1.0, base - penalty + bonus)), 4)

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


def _call_llm(system: str, user: str, model: str | None, settings: Any) -> str:
    """One Chat Completions call through the tools layer's own transport.

    Reuses :func:`gflow_cli.tools.expander._default_transport` rather than an SDK:
    it already puts the key in a header (never the URL), refuses redirects, and
    is reached only through a ``base_url`` that ``Settings._validate_llm_base_url``
    has gated to https (or loopback http). A second HTTP client here would be a
    second trust boundary to audit.
    """
    payload: dict[str, object] = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.0,
        "max_tokens": 512,
    }
    if model:
        # Omitted when unset so a gateway applies its own default — sending a
        # vendor-specific name to an endpoint that does not serve it is a silent 400.
        payload["model"] = model

    key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"

    # 429 backs off in whole quota-windows because free tiers meter per MINUTE
    # (Google's flash is 10 RPM), so a 1-2s retry only burns another attempt inside
    # the same window — that is what killed an unpaced 19-task run at task 6. Server
    # errors keep the short schedule: a flapping 502 has no per-minute window to wait
    # out, and paying 30s for it would cost minutes across a suite.
    # _RETRYABLE_STATUS is imported so this and the tools layer cannot disagree.
    node: dict[str, object] | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            node = _default_transport(url, payload, DEFAULT_TIMEOUT, key)
            break
        except LlmHttpError as exc:
            # Gateways echo the request on error, Authorization header included —
            # the same reason expander.py redacts this exact field.
            detail = redact_error_detail(exc.detail)[:300]
            if exc.status not in _RETRYABLE_STATUS or attempt == _MAX_ATTEMPTS - 1:
                msg = f"HTTP {exc.status}: {detail}"
                raise RolloutError(msg) from exc
            delay = _QUOTA_WINDOW_SECONDS * (attempt + 1) if exc.status == 429 else 2**attempt
            print(f"  (HTTP {exc.status} — retrying in {delay}s)")
            time.sleep(delay)
        except Exception as exc:
            msg = f"{type(exc).__name__}: {redact_error_detail(str(exc))[:300]}"
            raise RolloutError(msg) from exc
    if node is None:  # pragma: no cover — range(_MAX_ATTEMPTS) is never empty
        raise RolloutError("exhausted retries without a response")

    # Same defensive extraction the prompt tools use: `content` is legitimately
    # None on a refusal, and gateways that route multimodal models can return it
    # as a parts list. str()-ing either would feed the scorer a keyword-matchable
    # blob and silently corrupt the benchmark, so an unusable shape is an error.
    text = PromptExpander._extract_text(node)  # noqa: SLF001 — one defensive parser, not two
    if not text.strip():
        raise RolloutError("response carried no assistant text (refusal or unexpected shape)")
    return text.strip()


def run_live(
    tasks: list[dict[str, Any]],
    skill_text: str,
    version: str,
    epoch: int,
    model: str | None,
    settings: Any,
) -> None:
    results: list[dict[str, Any]] = []

    print(
        f"=== LIVE RUN — {len(tasks)} task(s) — skill v{version} epoch {epoch}"
        f" — {urllib.parse.urlsplit(settings.llm_base_url).netloc}"
        f" — model {model or '(gateway default)'} ===\n"
    )

    for i, task in enumerate(tasks, 1):
        system, user = format_prompt(skill_text, version, epoch, task)
        print(f"[{i}/{len(tasks)}] {task['id']}: {task['question'][:70]}...")

        # A rollout that cannot complete is recorded and the suite carries on.
        # Exiting here would discard every score already paid for -- which on a
        # metered free tier is the expensive half of the run, and is exactly what
        # happened twice while this harness was being exercised.
        try:
            response = _call_llm(system, user, model, settings)
        except RolloutError as exc:
            print(f"  ERROR: {exc}")
            results.append(
                {
                    "id": task["id"],
                    "tags": task.get("tags", []),
                    "score": 0.0,
                    "status": "ERROR",
                    "response_preview": "",
                    "reasons": [f"ERROR: {exc}"],
                }
            )
            continue

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

    # An ERROR is missing data, not a zero: the model never answered. Averaging it
    # in would understate the skill and make a quota-truncated run look like a
    # regression, so errors are counted and named but kept out of the score.
    errored = [r for r in results if r["status"] == "ERROR"]
    scored = [r for r in results if r["status"] != "ERROR"]
    passed = sum(1 for r in scored if r["status"] == "PASS")
    failed = [r for r in scored if r["status"] == "FAIL"]

    print(f"\n{'='*60}")
    if scored:
        avg = sum(r["score"] for r in scored) / len(scored)
        print(f"SUMMARY: {passed}/{len(scored)} passed  avg score {avg:.3f}")
    else:
        print("SUMMARY: no task completed a rollout")
    if errored:
        print(f"  {len(errored)} of {total} task(s) did not complete and are excluded:")
        for r in errored:
            print(f"    {r['id']}: {r['reasons'][0]}")

    if failed:
        print(f"\nFailed tasks ({len(failed)}):")
        for r in failed:
            print(f"  {r['id']}: {r['reasons']}")

    tag_scores: dict[str, list[float]] = {}
    for r in scored:
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
        "--model",
        type=str,
        default=None,
        help=(
            "Model ID, overriding GFLOW_CLI_LLM_MODEL for this run "
            "(e.g. openai/gpt-4o-mini, gemini-2.5-flash, llama3.2). "
            "Unset falls through the normal precedence and may let the gateway choose."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print prompts and scoring spec without calling the API",
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

    if args.dry_run:
        run_dry(tasks, skill_text, version, epoch)
        return

    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001 — a bad GFLOW_CLI_LLM_* value is user error
        sys.exit(f"Invalid GFLOW_CLI_* configuration: {type(exc).__name__}: {exc}")
    # Same rule the prompt tools use: a key alone is enough on the default
    # endpoint, and a chosen endpoint is enough alone because local gateways
    # need no credential. Neither means the user has not configured this.
    # rstrip matches resolve_model and PromptExpander._is_configured — without it
    # a trailing slash on the default URL reads as a chosen endpoint and skips this.
    if not settings.llm_api_key and settings.llm_base_url.rstrip("/") == DEFAULT_LLM_BASE_URL:
        sys.exit(
            "No LLM endpoint configured. Set GFLOW_CLI_LLM_API_KEY (and optionally "
            "GFLOW_CLI_LLM_BASE_URL / GFLOW_CLI_LLM_MODEL) — the same settings the "
            "prompt tools use. See docs/CONFIGURATION.md and .env.template."
        )
    model = resolve_model(args.model, settings.llm_model, settings.llm_base_url)
    run_live(tasks, skill_text, version, epoch, model, settings)


if __name__ == "__main__":
    main()
