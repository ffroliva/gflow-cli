"""Quality gates for generated dialogue clips: fluidity, lip sync, A/V drift.
ffmpeg and ffprobe only — no model, no numpy, no network. Companion to SKILL.md.

  python clip_qa.py <clips_dir>         every <xx00>.mp4 in the directory
  python clip_qa.py <clip.mp4>          one file — use this on the assembled cut
  python clip_qa.py --selftest <clip>   shift a known clip and prove the sync detector sees it
  python clip_qa.py --strip <clip|dir>  write the 1 fps contact sheet(s) the eye gate needs

A directory run writes the strips too, on purpose. Every number below is a PER-FRAME or
signal property; a clip whose individual frames are all fine can still be wrong across
TIME -- an object that grows, materialises or dissolves mid-shot passes every gate here
and RAISES the motion score. That class is only catchable by looking at the whole clip in
order, so the strip is produced automatically rather than left to whoever remembers.
Measured 2026-09-07: a beat whose background monolith tripled in size and appeared
top-down passed bars, stream lengths, audio and the motion gates, and was caught by eye.

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
import tempfile
from pathlib import Path

FACE_CROP = "crop=iw*0.6:ih*0.75:iw*0.2:0,scale=320:240"
SYNC_EARLY, SYNC_LATE, SYNC_MIN_R, MIN_PROMINENCE = -0.045, 0.125, 0.3, 0.05
AUDIO = ["-map", "0:a:0"]  # the stream probe_streams reads, whatever ffmpeg would pick
AV_DURATION_TOLERANCE = 0.1  # video vs audio stream length on an assembled cut


def run(args: list[str], cwd: Path) -> str:
    """ffmpeg, failing loudly: a silent failure would surface later as missing output."""
    r = subprocess.run(  # noqa: S603 - fixed ffmpeg args
        args, capture_output=True, encoding="utf-8", errors="replace", cwd=cwd
    )
    if r.returncode != 0:
        target = args[args.index("-i") + 1]
        raise RuntimeError(f"ffmpeg failed ({r.returncode}) on {target}:\n{r.stderr[-800:]}")
    return r.stderr


def ffprobe(clip: Path, entries: str, *options: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed ffprobe args
        ["ffprobe", "-v", "error", *options]  # noqa: S607 - system tool
        + ["-show_entries", entries, str(clip.absolute())],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()


def strip(clip: Path, out_dir: Path, fps: float = 1.0) -> Path:
    """A contact sheet at *fps*, row-major, written beside the clip.

    Cell k is second k, so a defect is cited as a second without labels on the image --
    deliberately, because `drawtext` needs libfreetype and this file's contract is ffmpeg
    and ffprobe only (minimal Windows builds ship without it, and a gate that cannot run
    is not a gate).
    """
    dur = float(ffprobe(clip, "format=duration", "-of", "csv=p=0") or 0)
    n = max(1, round(dur * fps))
    cols = min(6, n)
    rows = -(-n // cols)  # ceil
    out = out_dir / f"{clip.stem}_{fps:g}fps.png"
    run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(clip.absolute()),
         "-vf", f"fps={fps},scale=420:-1,tile={cols}x{rows}",
         "-frames:v", "1", str(out.absolute())],
        out_dir,
    )
    return out


def probe_fps(clip: Path) -> float:
    out = ffprobe(clip, "stream=r_frame_rate", "-select_streams", "v:0", "-of", "csv=p=0")
    num, _, den = out.partition("/")
    if not num.strip().isdigit():
        raise RuntimeError(
            f"{clip.name}: ffprobe found no video stream (got {out!r}) — corrupt or not a video"
        )
    return float(num) / float(den or 1)


def frame_metadata(clip: Path, flag: str, filters: str) -> str:
    """What the filter chain's closing print filter writes, kept out of the clip's folder.

    The file is named relative to ffmpeg's cwd: a drive colon would end the filter option.
    """
    with tempfile.TemporaryDirectory() as d:
        run(
            ["ffmpeg", "-v", "error", "-i", str(clip.resolve())]
            + (AUDIO if flag == "-af" else [])
            + [flag, f"{filters}:file=out.txt", "-f", "null", "-"],
            Path(d),
        )
        return (Path(d) / "out.txt").read_text()


def yavg(clip: Path, vf: str) -> list[float]:
    """Per-frame mean luma of the frame difference — how much moved between frames."""
    text = frame_metadata(
        clip,
        "-vf",
        f"{vf},tblend=all_mode=difference,signalstats,metadata=print:key=lavfi.signalstats.YAVG",
    )
    vals = [float(x) for x in re.findall(r"YAVG=([0-9.]+)", text)]
    if not vals:
        raise RuntimeError(f"no frame data from {clip.name} — check the filter: {vf}")
    return vals


def audio_envelope(clip: Path, fps: float) -> list[float]:
    """Per-VIDEO-frame loudness as LINEAR amplitude, by cutting the audio into frame-sized
    chunks. Linear, not dB: silence in dB is a large negative outlier that dominates a
    correlation, while in amplitude it is simply zero."""
    text = frame_metadata(
        clip,
        "-af",
        f"aresample=48000,asetnsamples={round(48000 / fps)}:p=0,astats=metadata=1:reset=1,"
        "ametadata=print:key=lavfi.astats.Overall.RMS_level",
    )
    db = [
        -91.0 if x.lstrip("-") == "inf" else float(x)
        for x in re.findall(r"RMS_level=(-?inf|-?[0-9.]+)", text)
    ]
    return [10 ** (x / 20) for x in db]


def on_video_timeline(envelope: list[float], audio_start: float, fps: float) -> list[float]:
    """The envelope as heard: a late stream opens with silence, an early one loses its head."""
    frames = round(audio_start * fps)
    return [0.0] * frames + envelope[max(0, -frames) :]


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


def probe_streams(clip: Path) -> tuple[float, float]:
    """(audio start minus video start, video length minus audio length), in seconds.

    The length delta: the 25 fps title cards concatenated into 24 fps clips produced 164 s of
    video under 171 s of audio. A truncated clip is refused: ffmpeg exits 0 on one and its
    header keeps the full durations, so those are checked against the packets in the file.
    """
    entries = "stream=index,codec_type,start_time,duration"
    entries += ":packet=stream_index,pts_time,dts_time,duration_time"
    found = json.loads(ffprobe(clip, entries, "-of", "json") or "{}")
    ends: dict[int, float] = {}
    for p in found.get("packets", []):
        time = p.get("pts_time", p.get("dts_time"))  # AVI stores no presentation times
        if time is not None:
            end = float(time) + float(p.get("duration_time", 0))
            ends[p["stream_index"]] = max(end, ends.get(p["stream_index"], end))
    start, length = {}, {}
    for s in found.get("streams", []):
        kind = s["codec_type"]
        if kind in start:  # the first stream of a type is the one measured
            continue
        start[kind], length[kind] = float(s.get("start_time", 0)), float(s.get("duration", 0))
        if kind not in ("video", "audio"):
            continue
        missing = start[kind] + length[kind] - ends.get(s["index"], start[kind])
        if missing > AV_DURATION_TOLERANCE:
            raise RuntimeError(f"{clip.name}: truncated, {missing:.2f} s of {kind} is missing")
    if "audio" not in start:
        raise RuntimeError(f"{clip.name}: no audio stream, so speech and sync cannot be measured")
    return start["audio"] - start["video"], round(length["video"] - length["audio"], 3)


def measure(clip: Path) -> tuple[dict, dict]:
    fps = probe_fps(clip)
    audio_start, duration_delta = probe_streams(clip)
    whole = yavg(clip, "scale=180:320")
    face = yavg(clip, FACE_CROP)
    heard = on_video_timeline(audio_envelope(clip, fps), audio_start, fps)
    lag, r, prom, edge = sync(face, heard, fps)
    err = run(
        ["ffmpeg", "-i", clip.name, *AUDIO]
        + ["-af", "silencedetect=n=-35dB:d=0.3", "-f", "null", "-"],
        clip.parent,
    )
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", err)]
    begins = max(0.0, audio_start)  # a late stream's first sample is not at zero
    onset = ends[0] if starts and starts[0] < begins + 0.05 and ends else begins
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
            "audio_start_s": round(audio_start, 3),
            "av_duration_delta_s": duration_delta,
        },
    )


def verdict(fa: dict, is_beat: bool = True) -> str:
    """is_beat=False for an assembled cut: it opens on a title card, so the speech-onset
    and per-shot sync gates do not apply — the stream-length check does."""
    if abs(fa["av_duration_delta_s"]) > AV_DURATION_TOLERANCE:
        return "DRIFT"
    starts_together = SYNC_EARLY <= fa.get("audio_start_s", 0.0) <= SYNC_LATE
    if not is_beat:
        return "ok" if starts_together else "CHECK"
    live = fa["speech_onset_s"] <= 1.6 and fa["face_motion_p10"] > 0.15
    # a weak correlation means the shot gives the detector nothing to align, not that it drifts
    measurable = fa["sync_r"] >= SYNC_MIN_R and not fa.get("sync_edge")
    in_sync = not measurable or SYNC_EARLY <= fa["sync_lag_s"] <= SYNC_LATE
    suppressed = 0 < fa.get("sync_prominence", 0.0) < MIN_PROMINENCE
    if (not measurable or suppressed) and not starts_together:
        return "CHECK"  # the stored offset may be a fault, and nothing here can tell
    return "fluid" if live and in_sync else ("DRIFT" if live else "CHECK")


def selftest(clip: Path, shift_s: float = 0.2) -> None:
    """Delay the audio by a known amount; the detector must report that delay."""
    base = measure(clip)[1]
    assert base["sync_r"] >= SYNC_MIN_R, (
        f"{clip.name} correlates at r={base['sync_r']} — too weak to test against. "
        "Pick a single with one person talking, not a busy two-shot."
    )
    with tempfile.TemporaryDirectory() as d:
        shifted = Path(d) / "shifted.mp4"
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(clip.resolve()),
                "-af",
                f"adelay={int(shift_s * 1000)}:all=1",
                "-c:v",
                "copy",
                shifted.name,
            ],
            shifted.parent,
        )
        moved = measure(shifted)[1]
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
    # A console code page that cannot show a clip's name must not end the run.
    sys.stdout.reconfigure(errors="backslashreplace")
    if sys.argv[1] == "--selftest":
        selftest(Path(sys.argv[2]))
        raise SystemExit(0)
    if sys.argv[1] == "--strip":
        tgt = Path(sys.argv[2])
        srcs = (
            [tgt]
            if tgt.is_file()
            else sorted(p for p in tgt.glob("*.mp4") if re.fullmatch(r"[a-z]{2}\d{2}\.mp4", p.name))
        )
        if not srcs:
            raise SystemExit(f"no clips matched in {tgt} (expected <xx00>.mp4)")
        for c in srcs:
            print(f"strip {strip(c, tgt if tgt.is_dir() else tgt.parent).name}")
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
        try:
            motion[clip.stem], face[clip.stem] = measure(clip)
        except RuntimeError as e:
            print(f"REFUSED {e}")
            continue
        f, m = face[clip.stem], motion[clip.stem]
        print(
            f"{verdict(f, is_beat):5} {clip.stem} onset={f['speech_onset_s']:.2f}s "
            f"face={f['face_motion_median']}/{f['face_motion_p10']} frame={m['median']} "
            f"sync={f['sync_lag_s']:+.3f}s r={f['sync_r']} prom={f['sync_prominence']:+.3f} "
            f"a/v={f['av_duration_delta_s']:+.3f}s"
        )
    for clip in (c for c in clips if c.stem in motion):
        # Not optional, and not left to the operator's memory: the numbers above cannot
        # see a temporal artifact, so the artifact that CAN is produced every run.
        print(f"strip {strip(clip, out_dir).name}  (cell k = second k, read in order)")
    if target.is_dir():  # a single-file run is a spot check; do not clobber the batch results
        (out_dir / "motion.json").write_text(json.dumps(motion, indent=1))
        (out_dir / "motion_face.json").write_text(json.dumps(face, indent=1))
    raise SystemExit(len(motion) < len(clips))
