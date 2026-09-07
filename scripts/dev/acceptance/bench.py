r"""Score a frame judge against a matched pair of clips with known answers.

A judge is only worth its verdict if it can be shown to catch a defect it has never
seen described. This runs the smallest honest test of that: two clips of the SAME beat,
same cast, same plate, same model, same prompt shape — one carrying a temporal defect and
one not — and requires the judge to reject the first AND accept the second.

**Both arms are required.** A benchmark with only the bad arm is won by rejecting
everything, and a judge that rejects everything is as useless as one that accepts
everything; it just fails in the direction that looks diligent.

Before either arm runs, the judge must pass ``prove_sight()`` — shown a flat colour and
made to name it. A gateway can route a request to a text-only model that answers every
question plausibly having seen nothing, and that failure is invisible in the output.

    uv run --with openai python scripts/dev/acceptance/bench.py \
        --reject path/to/defective.mp4 --accept path/to/clean.mp4

Costs one vision call per arm plus one for the sight proof. No video is generated.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acceptance import (  # noqa: E402
    Assessment,
    ContactSheet,
    JudgeSettings,
    JudgeUnavailableError,
    Verdict,
    VisionFrameJudge,
)

#: One cell per second, laid out 4 across. Matches the skill's temporal pass, which says
#: to read the frames "every second, in order" — the judge must see what a human would.
CELLS_ACROSS = 4


def build_sheet(clip: Path, out_dir: Path, *, fps: float = 1.0, width: int = 380) -> ContactSheet:
    """A contact sheet of *clip* at *fps*, written next to nothing the caller cares about.

    ffmpeg only — the same dependency floor `clip_qa.py` holds to, so producing the
    evidence never requires more than producing the clip did.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    sheet = out_dir / f"{clip.stem}_{fps:g}fps.png"
    duration = float(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(clip),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    cells = max(1, int(duration * fps))
    rows = max(1, -(-cells // CELLS_ACROSS))
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(clip),
            "-vf",
            f"fps={fps},scale={width}:-1,tile={CELLS_ACROSS}x{rows}",
            "-frames:v",
            "1",
            str(sheet),
        ],
        check=True,
    )
    return ContactSheet(path=sheet, fps=fps, cells=cells, clip_name=clip.name)


def _describe(arm: str, expected: Verdict, got: Assessment) -> bool:
    ok = got.verdict is expected
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {arm}: expected {expected.value}, got {got.verdict.value} ({got.model})")
    for f in got.findings:
        print(f"        {f.failure.value} @ cell {f.first_cell}: {f.evidence[:150]}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reject", required=True, type=Path, help="clip that MUST be rejected")
    ap.add_argument("--accept", required=True, type=Path, help="clip that MUST be accepted")
    ap.add_argument("--out", type=Path, default=Path("_bench_out"))
    args = ap.parse_args()

    judge = VisionFrameJudge(JudgeSettings.from_env())

    # The sight proof runs FIRST and is fatal. A judge that cannot demonstrate it is
    # looking at the image has nothing to say about either arm, and letting it proceed
    # would produce two verdicts that mean nothing while looking like data.
    try:
        model = judge.prove_sight(expected_colour="red", tmp_dir=args.out)
        print(f"[ OK ] sight proof passed on {model}")
    except JudgeUnavailableError as exc:
        print(f"[FAIL] sight proof: {exc}", file=sys.stderr)
        return 2

    results: list[bool] = []
    for arm, clip, expected in (
        ("reject-arm", args.reject, Verdict.REJECT),
        ("accept-arm", args.accept, Verdict.ACCEPT),
    ):
        sheet = build_sheet(clip, args.out)
        print(f"       {arm}: {sheet.cells} cells from {clip.name}")
        try:
            results.append(_describe(arm, expected, judge.assess(sheet)))
        except JudgeUnavailableError as exc:
            # An unavailable judge is NOT a failed arm — it is no measurement at all,
            # and scoring it as a miss would blame the judge for the gateway.
            print(f"[????] {arm}: no verdict — {exc}", file=sys.stderr)
            results.append(False)

    print()
    print("BENCH:", "PASS" if all(results) else "FAIL", f"({sum(results)}/2 arms correct)")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
