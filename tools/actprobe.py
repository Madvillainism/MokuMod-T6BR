#!/usr/bin/env python3
"""Actionability probe using the move ID at P1+0x144.

The state counter is useless as a "new action" marker because it also resets
at phase changes inside a move.  The move ID does not: it changes only when a
different move really starts, so mashing a *different* button lets us see the
exact frame on which the character becomes actionable again.

    python tools/actprobe.py --move square+triangle --mash square
    python tools/actprobe.py --move circle --mash square
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
STATE, COUNTER, HEALTH, MOVE_ID = 0x0AC, 0x07C, 0x002, 0x144
IDLE = {6482, 6466, 14633}
START, SIZE = 0x08C078C0, 0x1290


def press(pad, combo, frames=2):
    parts = [p for p in combo.split("+") if p]
    if len(parts) > 1:
        pad.raw({"event": "input.buttons.send", "buttons": {b: True for b in parts}}, 0.05)
        time.sleep(0.03)
        pad.raw({"event": "input.buttons.send", "buttons": {b: False for b in parts}}, 0.05)
    else:
        t = pad.press(parts[0], frames)
        pad.wait_ticket(t, timeout=6)


def capture(name, move, mash, delay, duration, rate):
    h = psp_mem.open_process(psp_mem.find_pid())
    pad = Pad()
    pad.wait_connected(3)

    def seq():
        time.sleep(0.5)
        press(pad, move, 2)
        time.sleep(delay)
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < duration - delay - 0.7:
            press(pad, mash, 2)
            time.sleep(0.012)

    threading.Thread(target=seq, daemon=True).start()

    frames, times = [], []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < duration:
        frames.append(t6scan.psp_read(h, START, SIZE))
        times.append(time.perf_counter() - t0)
        nxt = t0 + len(frames) / rate
        while time.perf_counter() < nxt:
            pass

    with open(t6scan.snap_path(name), "wb") as f:
        f.write(b"".join(frames))
    json.dump(
        {"start": START, "size": SIZE, "count": len(frames),
         "times": [round(t, 6) for t in times], "movie": True},
        open(t6scan.snap_path(name) + ".json", "w"),
    )
    pad.close()
    return len(frames)


def analyse(name):
    fr, st, ts = t6scan.load_movie(name)

    def col(addr, fmt):
        return [struct.unpack_from(fmt, f, addr - st)[0] for f in fr]

    p1s = col(P1 + STATE, "<H")
    p1c = col(P1 + COUNTER, "<I")
    p2s = col(P2 + STATE, "<H")
    mid = col(P1 + MOVE_ID, "<I")

    # frame period from the counter
    bounds = [i for i in range(1, len(ts)) if p1c[i] != p1c[i - 1]]
    d = sorted(ts[b] - ts[a] for a, b in zip(bounds, bounds[1:]))
    T = d[len(d) // 2] if d else 1 / 60

    first_id = next((v for v in mid if v), None)
    start = next((i for i, v in enumerate(mid) if v == first_id), None)
    if start is None:
        print(f"{name}: no move")
        return
    impact = next(
        (i for i in range(start, len(p2s)) if p2s[i] not in IDLE and p2s[i - 1] in IDLE),
        None,
    )
    if impact is None:
        print(f"{name}: whiff (id {first_id})")
        return

    # ids seen inside the sequence, in order
    seq_ids = []
    last = None
    for i in range(start, len(ts)):
        v = mid[i]
        if v and v != last:
            seq_ids.append((round((ts[i] - ts[impact]) * 1000 / (T * 1000), 1), v))
            last = v

    # first sample after impact+2 where the ID is a *different* move again
    later = next(
        (
            i
            for i in range(impact + 2, len(mid))
            if mid[i] and mid[i] != first_id and p1s[i] not in IDLE and p1c[i] > 1
        ),
        None,
    )
    print(f"{name}: move id {first_id}, startup counter {p1c[impact]}")
    print(f"  ids after impact (frames, id): {seq_ids[:10]}")
    if later:
        r = (ts[later] - ts[impact]) / T
        print(
            f"  next different action at frame {r:.2f} after the impact "
            f"(id {mid[later]}, p1c {p1c[later]}, state {p1s[later]})"
        )
    else:
        print("  no second action detected")
    end = next((i for i in range(impact, len(p1s)) if p1s[i] in IDLE), None)
    if end:
        print(f"  attacker state ends {((ts[end]-ts[impact])/T):.2f} frames after impact")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="ap")
    ap.add_argument("--move", default="square+triangle")
    ap.add_argument("--mash", default="square")
    ap.add_argument("--delay", type=float, default=0.05)
    ap.add_argument("--duration", type=float, default=2.4)
    ap.add_argument("--rate", type=float, default=2000)
    ap.add_argument("--analyse-only", action="store_true")
    a = ap.parse_args()
    if not a.analyse_only:
        print("captured", capture(a.name, a.move, a.mash, a.delay, a.duration, a.rate))
    analyse(a.name)


if __name__ == "__main__":
    main()
