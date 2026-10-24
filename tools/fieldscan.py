#!/usr/bin/env python3
"""Scan a recorded struct for fields that mark the actionable frame.

Input comes from `inputrec.py --bin`.  The actionable frame is taken as the
frame where the *second* move actually starts (the injected probe press was
accepted there).

    python tools/fieldscan.py f13
    python tools/fieldscan.py f13 --player 2 --event impact
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDIR = os.path.join(ROOT, ".cache", "in")

P1 = 0x08C078C0
P2 = 0x08C081F0
STATE = 0x0AC
MOVEID = 0x144
COUNTER = 0x07C
HEALTH = 0x002
IDLE = {6482, 6466, 14633}
STRIDE = P2 - P1


def load(name):
    d = json.load(open(os.path.join(INDIR, name + ".json")))
    span = d["meta"]["span"]
    raw = open(os.path.join(INDIR, name + ".bin"), "rb").read()
    n = len(raw) // span
    recs = [raw[i * span : (i + 1) * span] for i in range(n)]
    return d["samples"], recs


def frames(samples, recs, base):
    """Group samples into game frames (new frame when p1c changes)."""
    out = []
    cur = None
    for s, r in zip(samples, recs):
        t, p, s1, c1, s2, c2, hp, m1, m2 = s
        c = struct.unpack_from("<I", r, COUNTER)[0]
        if c != cur:
            out.append([t, r])
            cur = c
        else:
            out[-1][0] = t
            out[-1][1] = r
    return out


def u16(r, off):
    return struct.unpack_from("<H", r, off)[0]


def u32(r, off):
    return struct.unpack_from("<I", r, off)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--player", type=int, default=1)
    ap.add_argument("--event", choices=["action", "impact"], default="action")
    args = ap.parse_args()

    base = 0 if args.player == 1 else STRIDE
    samples, recs = load(args.name)
    fr = frames(samples, recs, base)
    print(f"{len(fr)} frames")

    mid = [u32(r, base + MOVEID) for _, r in fr]
    st1 = [u16(r, base + STATE) for _, r in fr]
    st2 = [u16(r, STRIDE + STATE) for _, r in fr]

    start = next((i for i in range(len(fr)) if mid[i] != 0), None)
    if start is None:
        raise SystemExit("no move found")

    if args.event == "action":
        target = next(
            (i for i in range(start + 1, len(fr)) if mid[i] not in (0, mid[start])),
            None,
        )
        label = "second move"
    else:
        target = next(
            (i for i in range(start, len(fr)) if st2[i] not in IDLE),
            None,
        )
        label = "impact"
    if target is None:
        raise SystemExit(f"{label} not found")

    print(f"start frame {start} (mid={mid[start]})  {label} at frame {target}")
    lo = max(0, target - 4)
    hi = min(len(fr), target + 4)
    for i in range(lo, hi):
        c = u32(fr[i][1], base + COUNTER)
        mark = "  <<<" if i == target else ""
        print(
            f"  f{i:3d} t={fr[i][0]:6.3f} p1c={c:4d} mid={mid[i]:3d} "
            f"s1={st1[i]:6d} s2={st2[i]:6d}{mark}"
        )

    seq = {}
    for w in (2, 4):
        for off in range(0, 0x930 - w, 2):
            seq[(off, w)] = [
                struct.unpack_from("<H" if w == 2 else "<I", r, base + off)[0]
                for _, r in fr
            ]

    print("\ncountdown -> 0 at the target frame:")
    for (off, w), v in seq.items():
        if v[target] != 0 or v[target - 1] == 0:
            continue
        if any(v[i] < v[i + 1] for i in range(start, target)):
            continue
        tail = v[start : target + 1]
        if len(set(tail)) < 3:
            continue
        print(f"  +0x{off:03X} w{w}: {tail}")

    print("\n0 during recovery, non-zero at the target frame:")
    for (off, w), v in seq.items():
        if v[target] == 0 or v[target - 1] != 0:
            continue
        if any(x != 0 for x in v[start + 2 : target]):
            continue
        before = v[max(0, start - 3) : start]
        print(f"  +0x{off:03X} w{w}: before={before} -> {v[target]}")

    print("\nnon-zero during recovery, 0 at the target frame:")
    for (off, w), v in seq.items():
        if v[target] != 0 or v[target - 1] == 0:
            continue
        if any(x == 0 for x in v[start + 2 : target]):
            continue
        print(f"  +0x{off:03X} w{w}: recovery={v[start + 2]} -> 0")


if __name__ == "__main__":
    main()
