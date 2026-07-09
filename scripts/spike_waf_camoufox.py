"""Phase 2 WAF-evidence spike — measure the Flow 403 / WAF-rejection rate per engine.

This is the ADR-13 gate for the Camoufox adoption roadmap
(``docs/superpowers/plans/2026-07-09-camoufox-adoption/PLAN.md``). ADR-13 parks any
stealth-engine alternative "until the stealth-flag fix is **confirmed insufficient**."
This script produces that evidence: it drives N real image generations on one authed
profile through a chosen browser engine and counts how many are blocked by Flow's WAF
(HTTP 403 → :class:`WafRejectionError`).

**What it establishes — the baseline.** Run it with the default (``playwright``) engine
first. If the 403 rate is ~0%, ADR-13's premise is UNMET — the current stealth stack is
sufficient and the Camoufox engine (roadmap Phase 3) is unjustified. STOP there. Only a
materially non-zero baseline 403 rate justifies building and A/B-testing Camoufox.

**Camoufox arm.** ``camoufox`` is NOT a valid engine on ``develop`` — it is roadmap
Phase 3, gated on this very spike. Passing ``--engine camoufox`` therefore fails fast
with a pointer back to the baseline: measure the problem before building the fix.

NOT imported by the ``gflow_cli`` package — ``scripts/`` only. No product code changes.

Usage:
    # Dry-run — validate setup, print the plan + credit estimate, spend NOTHING:
    uv run python scripts/spike_waf_camoufox.py --engine playwright --dry-run

    # Baseline — 20 real generations on the default engine (~20 image credits):
    uv run python scripts/spike_waf_camoufox.py --engine playwright -n 20 --profile ffroliva

    # Patchright arm (requires `pip install patchright`):
    uv run python scripts/spike_waf_camoufox.py --engine patchright -n 20 --profile ffroliva

Each successful attempt burns ~1 image credit. `-n 20` ≈ 20 credits. Credits are
Imagen (image) credits, not Veo — cheaper, but still real spend: the script requires an
explicit ``--yes`` or an interactive confirmation before it generates anything.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

# ---------------------------------------------------------------------------
# sys.path bootstrap + shared spike helpers (scripts/dev/).
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(_ROOT / "scripts" / "dev"))

from _spike_common import default_out_path, resolve_profile_dir, step  # noqa: E402

if TYPE_CHECKING:
    pass

# Engines this spike can actually drive through product code on ``develop``.
# camoufox is deliberately absent — it is roadmap Phase 3 (see module docstring).
_DRIVABLE_ENGINES = ("playwright", "patchright")

# Outcome classes for one generation attempt.
_OK = "success"
_WAF = "waf_403"
_AUTH = "auth_401"
_RATE = "rate_limited_429"
_OTHER = "other_error"


@dataclass
class Attempt:
    """One generation attempt's outcome."""

    index: int
    outcome: str
    latency_s: float
    error_type: str | None = None
    error_msg: str | None = None


@dataclass
class SpikeResult:
    """The full spike record — serialized to JSON."""

    engine: str
    profile: str
    project_id: str
    prompt: str
    model: str
    aspect: str
    requested: int
    started_at: str
    ended_at: str
    wall_clock_s: float
    attempts: list[Attempt] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {_OK: 0, _WAF: 0, _AUTH: 0, _RATE: 0, _OTHER: 0}
        for a in self.attempts:
            out[a.outcome] = out.get(a.outcome, 0) + 1
        return out

    def waf_rate(self) -> float:
        n = len(self.attempts)
        return (self.counts()[_WAF] / n) if n else 0.0

    def success_rate(self) -> float:
        n = len(self.attempts)
        return (self.counts()[_OK] / n) if n else 0.0


def _classify(exc: BaseException) -> tuple[str, str]:
    """Map an exception to (outcome_class, error_type_name)."""
    from gflow_cli.errors import AuthExpiredError, RateLimitError, WafRejectionError

    name = type(exc).__name__
    if isinstance(exc, WafRejectionError):
        return _WAF, name
    if isinstance(exc, AuthExpiredError):
        return _AUTH, name
    if isinstance(exc, RateLimitError):
        return _RATE, name
    return _OTHER, name


async def _run(args: argparse.Namespace, profile_dir: Path) -> SpikeResult:
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.image import Aspect, GenerateImageRequest, Model

    started = time.time()
    started_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(started))

    async with FlowApiClient(profile_dir=profile_dir, headless=args.headless) as client:
        project_id = args.project
        if project_id is None:
            project = await client.create_project(title=f"waf-spike {args.engine}")
            project_id = project.project_id
            step("project", f"created {project_id}")
        else:
            step("project", f"reusing {project_id}")

        req = GenerateImageRequest(
            prompt=args.prompt,
            aspect=Aspect.from_cli(args.aspect),
            model=Model.from_cli(args.model),
        )

        result = SpikeResult(
            engine=args.engine,
            profile=args.profile_name,
            project_id=project_id,
            prompt=args.prompt,
            model=args.model,
            aspect=args.aspect,
            requested=args.count,
            started_at=started_iso,
            ended_at="",
            wall_clock_s=0.0,
        )

        for i in range(1, args.count + 1):
            t0 = time.time()
            try:
                images = await client.generate_images_batch(project_id=project_id, req=req, count=1)
                latency = time.time() - t0
                if images:
                    result.attempts.append(Attempt(i, _OK, round(latency, 2)))
                    step("gen", f"[{i}/{args.count}] OK ({latency:.1f}s)")
                else:
                    result.attempts.append(
                        Attempt(i, _OTHER, round(latency, 2), "EmptyResponse", "no images returned")
                    )
                    step("gen", f"[{i}/{args.count}] EMPTY ({latency:.1f}s)")
            except Exception as exc:  # noqa: BLE001 — spike must classify, never crash the run
                latency = time.time() - t0
                outcome, etype = _classify(exc)
                result.attempts.append(
                    Attempt(i, outcome, round(latency, 2), etype, str(exc)[:200])
                )
                step("gen", f"[{i}/{args.count}] {outcome.upper()} ({etype}, {latency:.1f}s)")
                if outcome == _AUTH:
                    # A dead session poisons every subsequent attempt — abort early so
                    # the 403 rate isn't diluted by auth noise.
                    step("abort", "session expired (401) — stopping early; re-auth and re-run")
                    break

            if i < args.count and args.delay > 0:
                await asyncio.sleep(args.delay)

        ended = time.time()
        result.ended_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ended))
        result.wall_clock_s = round(ended - started, 1)
        return result


def _print_summary(result: SpikeResult, out_path: Path) -> None:
    counts = result.counts()
    n = len(result.attempts)
    print("\n" + "=" * 68)
    print(f"  WAF spike — engine={result.engine}  profile={result.profile}")
    print("=" * 68)
    print(f"  attempts run     : {n}/{result.requested}")
    print(f"  success          : {counts[_OK]}")
    print(f"  WAF 403 (blocked): {counts[_WAF]}")
    print(f"  auth 401         : {counts[_AUTH]}")
    print(f"  rate-limit 429   : {counts[_RATE]}")
    print(f"  other errors     : {counts[_OTHER]}")
    if n:
        print(f"  --> WAF 403 rate : {result.waf_rate() * 100:.1f}%")
        print(f"  --> success rate : {result.success_rate() * 100:.1f}%")
    print(f"  wall clock       : {result.wall_clock_s}s")
    print(f"  evidence written : {out_path}")
    print("-" * 68)
    print("  ADR-13 read: a ~0% baseline WAF rate on the DEFAULT engine means the")
    print("  current stealth stack is sufficient — STOP the Camoufox roadmap at")
    print("  Phase 2. A materially non-zero baseline justifies building the Camoufox")
    print("  engine (Phase 3) and re-running this spike as the A/B comparison.")
    print("=" * 68 + "\n")


def _confirm_spend(args: argparse.Namespace) -> bool:
    if args.yes:
        return True
    prompt = (
        f"\nThis will attempt {args.count} real image generation(s) on profile "
        f"'{args.profile_name}' via the '{args.engine}' engine — roughly "
        f"{args.count} Imagen credit(s) of real spend. Proceed? [y/N] "
    )
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        # Non-interactive (piped) without --yes → refuse to spend.
        print("[spike] non-interactive session and --yes not given; refusing to spend.")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine",
        default="playwright",
        help="Browser engine to drive (playwright | patchright). camoufox is Phase 3.",
    )
    parser.add_argument("--profile", dest="profile_name", default=None, help="Profile name.")
    parser.add_argument(
        "-n", "--count", type=int, default=20, help="Generations to attempt (default 20)."
    )
    parser.add_argument("--prompt", default="a single red apple on a plain white background")
    parser.add_argument("--model", default="narwhal", help="Image model alias.")
    parser.add_argument("--aspect", default="1:1", choices=["1:1", "9:16", "16:9", "4:3", "3:4"])
    parser.add_argument(
        "--project", default=None, help="Reuse an existing project id (else one is created)."
    )
    parser.add_argument(
        "--delay", type=float, default=3.0, help="Seconds between attempts (default 3.0)."
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headless (default headed — reCAPTCHA scores headed sessions lower).",
    )
    parser.add_argument("--out", default=None, help="Output JSON path (default: spike out dir).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate setup + print the plan and credit estimate; spend nothing.",
    )
    parser.add_argument(
        "--yes", action="store_true", help="Skip the interactive credit-spend confirmation."
    )
    args = parser.parse_args()

    # --- Camoufox guard: not a drivable engine on develop (roadmap Phase 3). ---
    if args.engine == "camoufox":
        print(
            "[spike] ERROR: 'camoufox' is not a drivable engine on develop — it is "
            "roadmap Phase 3, GATED ON THIS SPIKE.\n"
            "        Run the baseline first: --engine playwright. Per ADR-13, only a\n"
            "        materially non-zero baseline 403 rate justifies building Camoufox.",
            file=sys.stderr,
        )
        return 3
    if args.engine not in _DRIVABLE_ENGINES:
        print(
            f"[spike] ERROR: unknown engine {args.engine!r}; choose from {_DRIVABLE_ENGINES}.",
            file=sys.stderr,
        )
        return 2

    # Select the engine the same way a user would: via the env var, then rebuild
    # the cached settings so FlowApiClient picks it up.
    os.environ["GFLOW_CLI_BROWSER_ENGINE"] = args.engine
    from gflow_cli.config import get_settings, reset_settings

    reset_settings()
    settings = get_settings()
    args.profile_name = args.profile_name or settings.profile or "default"
    args.headless = bool(args.headless)  # normalize (default headed)

    profile_dir = resolve_profile_dir(args.profile_name)

    step(
        "plan",
        f"engine={args.engine} profile={args.profile_name} n={args.count} "
        f"model={args.model} aspect={args.aspect} headless={args.headless}",
    )
    step("plan", f"estimated spend ≈ {args.count} Imagen credit(s)")

    if args.dry_run:
        step("dry-run", "setup valid; no client built, no credits spent. Drop --dry-run to run.")
        return 0

    if not _confirm_spend(args):
        step("abort", "not confirmed — nothing generated.")
        return 0

    result = asyncio.run(_run(args, profile_dir))

    out_path = Path(args.out) if args.out else default_out_path("waf_spike", ".json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(result)
    payload["summary"] = {
        "counts": result.counts(),
        "waf_403_rate": round(result.waf_rate(), 4),
        "success_rate": round(result.success_rate(), 4),
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    _print_summary(result, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
