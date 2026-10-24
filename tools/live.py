#!/usr/bin/env python3
"""Live Tekken 6 frame-advantage tracker (practice mode), console version.

Uses the exact same measurement engine as the HUD (tools/hud.py), so what you
see here and on the overlay can never disagree.

    python tools/live.py
    python tools/live.py --demo          # injects a few moves for testing
    python tools/live.py --seconds 20    # stop after N seconds

Lines look like:

    1      i10   +1 BLOCK
    3      i16   -4 BLOCK
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hud import Reader, Tracker  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--seconds", type=float, default=0.0, help="stop after N s")
    args = ap.parse_args()

    rd = Reader()
    while not rd.ensure():
        time.sleep(0.5)
    print(f"tracking pid={rd.pid} ... ctrl-c to stop")

    tracker = Tracker()

    demo = None
    if args.demo:
        from pad import Pad

        demo = Pad()
        demo.wait_connected(3)

    t0 = time.perf_counter()
    last = 0.0
    cycle = ["square", "triangle", "cross", "circle"]
    n = 0

    while True:
        s = rd.sample()
        if s is not None:
            for ev in tracker.step(s):
                adv = " --" if ev["adv"] is None else f"{ev['adv']:+d}"
                print(
                    f"{time.perf_counter() - t0:7.2f}  {ev['label']:<5s} "
                    f"i{ev['startup']:<3d} {adv:>3s} {ev['kind']}",
                    flush=True,
                )

        if args.seconds and time.perf_counter() - t0 > args.seconds:
            break
        if demo and time.perf_counter() - last > 1.7:
            last = time.perf_counter()
            btn = cycle[n % len(cycle)]
            n += 1
            demo.sock.sendall(
                ('{"cmd":"press","button":"%s","frames":2}\n' % btn).encode()
            )
        time.sleep(0.0005)


if __name__ == "__main__":
    main()
