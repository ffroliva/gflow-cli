"""Spike #1194: does a detached child gflow survive its parent and drive one generation?

Question: on this OS, a child started with the telemetry sender's flags (Windows:
CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, optionally CREATE_BREAKAWAY_FROM_JOB;
POSIX: start_new_session) keeps running after the parent exits, including when the
parent ran inside a job object (an IDE terminal, a CI step), and can launch Chrome and
finish a $0 `image t2i` with its output in a per-job log file.

Usage:
    python scripts/dev/spike_1194_detach.py spawn --profile P [--breakaway]
        -> prints the child pid and the log path, exits at once
    python scripts/dev/spike_1194_detach.py check LOG PID
        -> is the pid alive, and the log's last lines

Output: scripts/dev/_spike_out/1194/<stamp>.log (gitignored). Costs nothing: image
generation is $0 against the daily cap.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(__file__).parent / "_spike_out" / "1194"


def _flags(breakaway: bool) -> dict[str, object]:
    if sys.platform != "win32":
        return {"start_new_session": True}
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    if breakaway:
        flags |= 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
    return {"creationflags": flags}


def spawn(profile: str, breakaway: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log = OUT / f"{stamp}.log"
    out_file = OUT / f"{stamp}.png"
    command = [
        sys.executable,
        "-m",
        "gflow_cli",
        "image",
        "t2i",
        "a small blue ceramic tile, studio light",
        "--profile",
        profile,
        "--json",
        "--out-file",
        str(out_file),
    ]
    with log.open("wb") as sink:
        child = subprocess.Popen(  # noqa: S603 - fixed command, spike only
            command,
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.STDOUT,
            close_fds=True,
            **_flags(breakaway),  # type: ignore[arg-type]
        )
    print(f"pid={child.pid} log={log} breakaway={breakaway} parent={os.getpid()}")


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def check(log: str, pid: int) -> None:
    text = Path(log).read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"alive={_alive(pid)} lines={len(text)}")
    for line in text[-6:]:
        print("  " + line[:220])


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("spawn")
    s.add_argument("--profile", required=True)
    s.add_argument("--breakaway", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("log")
    c.add_argument("pid", type=int)
    args = parser.parse_args()
    if args.cmd == "spawn":
        spawn(args.profile, args.breakaway)
    else:
        check(args.log, args.pid)


if __name__ == "__main__":
    main()
