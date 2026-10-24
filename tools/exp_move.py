#!/usr/bin/env python3
"""Capture a high-rate RAM "movie" while pressing a button, then report it.

    python tools/exp_move.py --name jab --button square --pre 0.6 --duration 3

The movie is stored by tools/t6scan.py and analysed with
    python tools/t6scan.py filter jab --u16
    python tools/t6scan.py series jab 0x08C078C0 --type u16
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psp_mem  # noqa: E402
import t6scan  # noqa: E402
from pad import Pad  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--button", default="square")
    ap.add_argument("--frames", type=int, default=3)
    ap.add_argument("--pre", type=float, default=0.6)
    ap.add_argument("--duration", type=float, default=3.0)
    ap.add_argument("--start", default="0x08C06000")
    ap.add_argument("--size", default="0x6000")
    ap.add_argument("--no-press", action="store_true")
    a = ap.parse_args()

    start = int(a.start, 0)
    size = int(a.size, 0)
    pid = psp_mem.find_pid()
    handle = psp_mem.open_process(pid)
    pad = Pad()
    pad.wait_connected(3)

    pressed_at = []

    def presser():
        time.sleep(a.pre)
        t = pad.press(a.button, a.frames)
        pressed_at.append(time.perf_counter())
        pad.wait_ticket(t, timeout=8)

    if not a.no_press:
        threading.Thread(target=presser, daemon=True).start()

    os.makedirs(t6scan.SNAPDIR, exist_ok=True)
    frames = []
    times = []
    t0 = time.perf_counter()
    with open(t6scan.snap_path(a.name), "wb") as f:
        while time.perf_counter() - t0 < a.duration:
            data = t6scan.psp_read(handle, start, size)
            f.write(data)
            frames.append(data)
            times.append(time.perf_counter() - t0)

    import json

    json.dump(
        {
            "start": start,
            "size": size,
            "count": len(frames),
            "times": [round(t, 6) for t in times],
            "movie": True,
            "button": None if a.no_press else a.button,
            "press_offset": (pressed_at[0] - t0) if pressed_at else None,
        },
        open(t6scan.snap_path(a.name) + ".json", "w"),
    )

    fps = len(frames) / max(times[-1], 1e-9)
    print(f"movie {a.name}: {len(frames)} frames in {times[-1]:.2f}s ({fps:.0f} fps)")
    if pressed_at:
        print(f"button '{a.button}' sent at t+{pressed_at[0]-t0:.3f}s")
    pad.close()


if __name__ == "__main__":
    main()
