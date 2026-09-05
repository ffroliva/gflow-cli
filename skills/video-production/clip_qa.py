"""Quality gates for generated dialogue clips: fluidity, lip sync, A/V drift.
ffmpeg and ffprobe only — no model, no numpy, no network. Companion to SKILL.md.

  python clip_qa.py <clips_dir>         every <xx00>.mp4 in the directory
  python clip_qa.py <clip.mp4>          one file — use this on the assembled cut
  python clip_qa.py --selftest <clip>   shift a known clip and prove the sync detector sees it

Writes motion.json (whole-frame median, cut spikes) and motion_face.json (speech onset,
face-region median / p10, sync lag and correlation) beside the clips.

Gates: speech_onset_s <= 1.6 · face_motion_p10 > 0.15 (a held frame sits near 0) ·
sync_lag_s within -0.045 s (audio early) to +0.125 s (audio late), the ITU-R BT.1359
detectability window, and only when sync_r >= 0.3.

Known limits, read before trusting a number: the face crop is a fixed rectangle, not a
detector, so on a two-shot it straddles both actors; a hallucinated object is motion and
RAISES the score, so this measures presence of change, never correctness of change; the
motion series is inter-frame, giving a systematic half-frame (~0.02 s) bias in the lag.

Sync is only measurable where one person is talking and little else moves. On a busy
two-shot sync_r collapses to ~0.1 and the lag is noise — that is why a low sync_r means
"no opinion", never "in sync". Measured 2026-09-05: singles r=0.6-0.8, two-shot r=0.1.
"""

import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

FACE_CROP = "crop=iw*0.6:ih*0.75:iw*0.2:0,scale=320:240"
SYNC_EARLY, SYNC_LATE, SYNC_MIN_R, MIN_PROMINENCE = -0.045, 0.125, 0.3, 0.05
AV_DURATION_TOLERANCE = 0.1  # video vs audio stream length on an assembled cut


def run(args: list[str], cwd: Path) -> str:
    """ffmpeg, failing loudly. A silent failure here would leave the previous clip's
    metadata file on disk and report ITS numbers as this clip's."""
    r = subprocess.run(args, capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        target = args[args.index("-i") + 1]
        raise RuntimeError(f"ffmpeg failed ({r.returncode}) on {target}:\n{r.stderr[-800:]}")
    return r.stderr


def probe_fps(clip: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "csv=p=0",
            clip.name,
        ],
        capture_output=True,
        text=True,
        cwd=clip.parent,
    ).stdout.strip()
    num, _, den = out.partition("/")
    if not num.strip().isdigit():
        raise RuntimeError(
            f"{clip.name}: ffprobe found no video stream (got {out!r}) — corrupt or not a video"
        )
    return float(num) / float(den or 1)


def yavg(clip: Path, vf: str) -> list[float]:
    """Per-frame mean luma of the frame difference — how much moved between frames."""
    d = clip.parent
    m = d / "_m.txt"
    m.unlink(missing_ok=True)  # never read a stale file if the next call misbehaves
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            clip.name,
            "-vf",
            f"{vf},tblend=all_mode=difference,signalstats,"
            f"metadata=print:key=lavfi.signalstats.YAVG:file={m.name}",
            "-f",
            "null",
            "-",
        ],
        d,
    )
    vals = [float(x) for x in re.findall(r"YAVG=([0-9.]+)", m.read_text())]
    m.unlink(missing_ok=True)
    if not vals:
        raise RuntimeError(f"no frame data from {clip.name} — check the filter: {vf}")
    return vals


def audio_envelope(clip: Path, fps: float) -> list[float]:
    """Per-VIDEO-frame loudness as LINEAR amplitude, by cutting the audio into frame-sized
    chunks. Linear, not dB: silence in dB is a large negative outlier that dominates a
    correlation, while in amplitude it is simply zero."""
    d = clip.parent
    a = d / "_a.txt"
    a.unlink(missing_ok=True)
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            clip.name,
            "-af",
            f"aresample=48000,asetnsamples={round(48000 / fps)}:p=0,astats=metadata=1:reset=1,"
            f"ametadata=print:key=lavfi.astats.Overall.RMS_level:file={a.name}",
            "-f",
            "null",
            "-",
        ],
        d,
    )
    db = [
        -91.0 if x.lstrip("-") == "inf" else float(x)
        for x in re.findall(r"RMS_level=(-?inf|-?[0-9.]+)", a.read_text())
    ]
    a.unlink(missing_ok=True)
    return [10 ** (x / 20) for x in db]


def smooth(v: list[float], k: int = 5) -> list[float]:
    """Centred moving average. Turns the spiky per-frame difference into an activity
    envelope; correlating a spike train against a plateau returns ~0 (measured)."""
    out = []
    for i in range(len(v)):
        w = v[max(0, i - k // 2) : i + k // 2 + 1]
        out.append(sum(w) / len(w))
    return out


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 8:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (vx * vy) ** 0.5


def sync(motion: list[float], envelope: list[float], fps: float, window_s: float = 0.6):
    """Cross-correlate face motion against loudness. Returns (lag_seconds, peak_r, prominence).

    Positive lag = the sound arrives AFTER the picture moves, i.e. audio is late.

    Prominence is peak_r minus the correlation at zero lag. A flat surface has an argmax
    but no meaning: bt05 measured a 0.208 s "lag" whose peak stood only 0.04 above zero,
    while a real alignment (bt04) fell from 0.86 to 0.04 across the same window. Below
    MIN_PROMINENCE the lag is reported as zero, because the data cannot tell it from zero.
    """
    n = min(len(motion), len(envelope))
    if n < 16:
        return 0.0, 0.0, 0.0, False
    mo, en = smooth(motion[:n]), smooth(envelope[:n])
    span = int(window_s * fps)
    curve = {}
    for lag in range(-span, span + 1):
        a = mo[-lag:] if lag < 0 else mo[: n - lag]
        b = en[: n + lag] if lag < 0 else en[lag:]
        k = min(len(a), len(b))
        curve[lag] = pearson(a[:k], b[:k])
    best_lag = max(curve, key=curve.get)
    peak_r, prominence = curve[best_lag], curve[best_lag] - curve[0]
    # A peak pinned to the boundary means the real maximum is outside any plausible lip-sync
    # range, so the correlation is aligning something else — on a two-line clip it can match
    # line 1's mouth against line 2's audio. hb02 peaked at +0.75 s that way. No opinion.
    at_edge = abs(best_lag) >= span
    if prominence < MIN_PROMINENCE or at_edge:
        best_lag = 0
    return round(best_lag / fps, 3), round(peak_r, 2), round(prominence, 3), at_edge


def av_duration_delta(clip: Path) -> float:
    """Video stream length minus audio stream length. The 25 fps title cards concatenated
    into 24 fps clips produced 164 s of video under 171 s of audio — this is that check."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,duration",
            "-of",
            "csv=p=0",
            clip.name,
        ],
        capture_output=True,
        text=True,
        cwd=clip.parent,
    ).stdout
    rows = (line.partition(",") for line in out.splitlines() if "," in line)
    d = {k: float(v) for k, _, v in rows if v.replace(".", "").isdigit()}
    return round(d.get("video", 0.0) - d.get("audio", 0.0), 3)


def measure(clip: Path) -> tuple[dict, dict]:
    fps = probe_fps(clip)
    whole = yavg(clip, "scale=180:320")
    face = yavg(clip, FACE_CROP)
    lag, r, prom, edge = sync(face, audio_envelope(clip, fps), fps)
    err = run(
        ["ffmpeg", "-i", clip.name, "-af", "silencedetect=n=-35dB:d=0.3", "-f", "null", "-"],
        clip.parent,
    )
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", err)]
    onset = ends[0] if starts and starts[0] < 0.05 and ends else 0.0
    return (
        {
            "median": round(statistics.median(whole), 2),
            "cut_spikes": sum(v >= 3.0 for v in whole),
            "fps": round(fps, 2),
        },
        {
            "speech_onset_s": round(onset, 2),
            "face_motion_median": round(statistics.median(face), 2),
            "face_motion_p10": round(sorted(face)[len(face) // 10], 2),
            "sync_lag_s": lag,
            "sync_r": r,
            "sync_prominence": prom,
            "sync_edge": edge,
            "av_duration_delta_s": av_duration_delta(clip),
        },
    )


def verdict(fa: dict, is_beat: bool = True) -> str:
    """is_beat=False for an assembled cut: it opens on a title card, so the speech-onset
    and per-shot sync gates do not apply — the stream-length check does."""
    if abs(fa["av_duration_delta_s"]) > AV_DURATION_TOLERANCE:
        return "DRIFT"
    if not is_beat:
        return "ok"
    live = fa["speech_onset_s"] <= 1.6 and fa["face_motion_p10"] > 0.15
    # a weak correlation means the shot gives the detector nothing to align, not that it drifts
    measurable = fa["sync_r"] >= SYNC_MIN_R and not fa.get("sync_edge")
    in_sync = not measurable or SYNC_EARLY <= fa["sync_lag_s"] <= SYNC_LATE
    return "fluid" if live and in_sync else ("DRIFT" if live else "CHECK")


def selftest(clip: Path, shift_s: float = 0.2) -> None:
    """Delay the audio by a known amount; the detector must report that delay."""
    base = measure(clip)[1]
    assert base["sync_r"] >= SYNC_MIN_R, (
        f"{clip.name} correlates at r={base['sync_r']} — too weak to test against. "
        "Pick a single with one person talking, not a busy two-shot."
    )
    shifted = clip.parent / "_selftest.mp4"
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            clip.name,
            "-af",
            f"adelay={int(shift_s * 1000)}:all=1",
            "-c:v",
            "copy",
            shifted.name,
        ],
        clip.parent,
    )
    moved = measure(shifted)[1]
    shifted.unlink(missing_ok=True)
    drift = moved["sync_lag_s"] - base["sync_lag_s"]
    print(
        f"baseline {base['sync_lag_s']:+.3f}s (r={base['sync_r']}) -> "
        f"delayed {moved['sync_lag_s']:+.3f}s "
        f"(r={moved['sync_r']}); detected {drift:+.3f}s, injected +{shift_s:.3f}s"
    )
    assert abs(drift - shift_s) <= 0.06, (
        f"detector missed the injected {shift_s}s shift (saw {drift:+.3f}s)"
    )
    assert verdict(moved) == "DRIFT", "a clip shifted by 200 ms must not pass the sync gate"
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1] == "--selftest":
        selftest(Path(sys.argv[2]))
        raise SystemExit(0)
    target = Path(sys.argv[1])
    clips = (
        [target]
        if target.is_file()
        else sorted(p for p in target.glob("*.mp4") if re.fullmatch(r"[a-z]{2}\d{2}\.mp4", p.name))
    )
    if not clips:
        raise SystemExit(f"no clips matched in {target} (expected <xx00>.mp4)")
    out_dir = target if target.is_dir() else target.parent
    motion, face = {}, {}
    for clip in clips:
        is_beat = bool(re.fullmatch(r"[a-z]{2}\d{2}", clip.stem))
        motion[clip.stem], face[clip.stem] = measure(clip)
        f, m = face[clip.stem], motion[clip.stem]
        print(
            f"{verdict(f, is_beat):5} {clip.stem} onset={f['speech_onset_s']:.2f}s "
            f"face={f['face_motion_median']}/{f['face_motion_p10']} frame={m['median']} "
            f"sync={f['sync_lag_s']:+.3f}s r={f['sync_r']} prom={f['sync_prominence']:+.3f} "
            f"a/v={f['av_duration_delta_s']:+.3f}s"
        )
    if target.is_dir():  # a single-file run is a spot check; do not clobber the batch results
        (out_dir / "motion.json").write_text(json.dumps(motion, indent=1))
        (out_dir / "motion_face.json").write_text(json.dumps(face, indent=1))
