#!/usr/bin/env python3
"""Mash probe: when does the attacker *really* become actionable?

    python tools/probe.py --move square --mash square

Sends the move, then spams the same button as fast as possible.  The frame on
which the second move starts is the first actionable frame, so

    tail = (last frame of the first move state) - (first frame of the 2nd move)

If `tail` is constant across moves, the advantage can be corrected by it.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psp_mem  # noqa: E402
import t6scan  # noqa: E402
from pad import Pad  # noqa: E402

P1 = 0x08C078C0
P2 = 0x08C081F0
STATE, COUNTER, HEALTH = 0x0AC, 0x07C, 0x002
IDLE = {6482, 6466, 14633}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--move", default="square")
    ap.add_argument("--mash", default="square")
    ap.add_argument("--hold", default="")
    ap.add_argument("--pre", type=float, default=0.5)
    ap.add_argument("--duration", type=float, default=2.2)
    ap.add_argument("--rate", type=float, default=2000)
    ap.add_argument("--name", default="probe")
    ap.add_argument("--analyse-only", action="store_true")
    a = ap.parse_args()

    start, size = 0x08C078C0, 0x1290

    if not a.analyse_only:
        h = psp_mem.open_process(psp_mem.find_pid())
        pad = Pad()
        pad.wait_connected(3)
        hold = [x for x in a.hold.split("+") if x]

        def seq():
            time.sleep(a.pre)
            for b in hold:
                pad.raw({"event": "input.buttons.send", "buttons": {b: True}})
                time.sleep(0.08)
            pad.press(a.move, 2)
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 1.4:
                pad.press(a.mash, 2)
                time.sleep(0.02)
            if hold:
                pad.raw(
                    {"event": "input.buttons.send", "buttons": {b: False for b in hold}}
                )

        threading.Thread(target=seq, daemon=True).start()

        frames, times = [], []
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < a.duration:
            frames.append(t6scan.psp_read(h, start, size))
            times.append(time.perf_counter() - t0)
            nxt = t0 + len(frames) / a.rate
            while time.perf_counter() < nxt:
                pass
        with open(t6scan.snap_path(a.name), "wb") as f:
            f.write(b"".join(frames))
        json.dump(
            {"start": start, "size": size, "count": len(frames),
             "times": [round(t, 6) for t in times], "movie": True},
            open(t6scan.snap_path(a.name) + ".json", "w"),
        )
        pad.close()
        print(f"captured {len(frames)}")

    fr, st, ts = t6scan.load_movie(a.name)

    def col(addr, fmt):
        return [struct.unpack_from(fmt, f, addr - st)[0] for f in fr]

    p1s = col(P1 + STATE, "<H")
    p1c = col(P1 + COUNTER, "<I")
    p2s = col(P2 + STATE, "<H")
    hp2 = col(P2 + HEALTH, "<H")

    # a new action = the state counter resets to 1 (works even when the
    # character never returns to idle because the mash chains moves)
    starts = [i for i in range(1, len(p1s)) if p1c[i] == 1 and p1c[i - 1] != 1]
    print("move starts (sample idx / frame):")
    for i in starts[:10]:
        print(f"  idx {i:6d}  state {p1s[i]:6d}  counter {p1c[i]}")

    if len(starts) < 2:
        print("need at least two moves")
        return
    first, second = starts[0], starts[1]

    impact = next(
        (i for i in range(first, len(p2s)) if p2s[i] not in IDLE and p2s[i - 1] in IDLE),
        None,
    )
    last_c = max(p1c[first:second])
    print(f"first move: samples {first}..{second - 1}")
    if impact:
        print(f"impact at sample {impact}, startup counter {p1c[impact]}")
    print(f"first move reached counter {last_c} -> state lasted {last_c} frames")
    delta_t = ts[second] - ts[first]
    print(
        f"second move starts {second - first} samples later "
        f"({delta_t * 1000:.1f} ms ~ {delta_t * 60:.2f} frames of 60 Hz)"
    )
    if impact:
        d = ts[second] - ts[impact]
        print(
            f"from impact to next action: {d * 1000:.1f} ms "
            f"~ {d * 60:.2f} frames"
        )


if __name__ == "__main__":
    main()
