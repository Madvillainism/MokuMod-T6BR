#!/usr/bin/env python3
"""Print a frame-by-frame timeline of a captured RAM movie.

    python tools/timeline.py h2 [--start 0x08C078C0] [--size 0xD00]

Dedupes samples by (p1 state/counter, p2 state/counter, hp) and prints the
timestamp of every change, so transitions can be aligned to the exact frame.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t6scan  # noqa: E402

P1 = 0x08C078C0
P2 = 0x08C081F0
STATE, COUNTER, HEALTH = 0x0AC, 0x07C, 0x002


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--start", default="0x08C078C0")
    ap.add_argument("--size", default="0xD00")
    ap.add_argument("--extra", default="", help="extra hex offsets split by comma")
    a = ap.parse_args()

    start, size = int(a.start, 0), int(a.size, 0)
    fr, st, ts = t6scan.load_movie(a.name)
    extras = [int(x, 0) for x in a.extra.split(",") if x]

    def u16(addr, f):
        return struct.unpack_from("<H", f, addr - st)[0]

    def u32(addr, f):
        return struct.unpack_from("<I", f, addr - st)[0]

    def row(i):
        f = fr[i]
        return (
            u16(P1 + STATE, f),
            u32(P1 + COUNTER, f),
            u16(P2 + HEALTH, f),
            u16(P2 + STATE, f),
            u32(P2 + COUNTER, f),
            tuple(u32(x, f) for x in extras),
        )

    hdr = f"{'idx':>7} {'t':>7}  p1st  p1c  hp  p2st   p2c"
    if extras:
        hdr += "  " + "  ".join(f"{x:08X}" for x in extras)
    print(hdr)

    prev, t_prev = None, 0.0
    for i in range(len(fr)):
        r = row(i)
        if r != prev:
            dt = (ts[i] - t_prev) * 1000
            line = (
                f"{i:7d} {ts[i]:7.3f} {r[0]:5d} {r[1]:4d} {r[2]:4d}"
                f" {r[3]:5d} {r[4]:4d}"
            )
            if extras:
                line += "  " + "  ".join(f"{v:8d}" for v in r[5])
            line += f"   (+{dt:.1f}ms)"
            print(line)
            prev, t_prev = r, ts[i]


if __name__ == "__main__":
    main()
