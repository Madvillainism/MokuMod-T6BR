#!/usr/bin/env python3
"""Find the attacker's recovery timer.

The attacker's state ends *before* it can act, and the gap depends on the move
(0 frames for a jab, ~13 for 1+2).  This scans the attacker's struct for a
field that counts down and reaches zero when the character becomes actionable.

    python tools/countdown.py c1 c2 c3 c4 c12 c34
"""
from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t6scan  # noqa: E402

P1 = 0x08C078C0
P2 = 0x08C081F0
STATE, COUNTER = 0x0AC, 0x07C
IDLE = {6482, 6466, 14633}
MAXF = 70


def per_frame(name):
    fr, st, ts = t6scan.load_movie(name)
    p1s = [struct.unpack_from("<H", f, P1 + STATE - st)[0] for f in fr]
    p2s = [struct.unpack_from("<H", f, P2 + STATE - st)[0] for f in fr]

    start = next(
        (i for i in range(1, len(p1s)) if p1s[i] not in IDLE and p1s[i - 1] in IDLE),
        None,
    )
    if start is None:
        return None
    impact = next(
        (i for i in range(start, len(p2s)) if p2s[i] not in IDLE and p2s[i - 1] in IDLE),
        None,
    )
    if impact is None:
        return None

    # frame grid from the impact time; the period comes from the counter,
    # which ticks once per game frame
    p1c = [struct.unpack_from("<I", f, P1 + COUNTER - st)[0] for f in fr]
    bounds = [i for i in range(1, len(ts)) if p1c[i] != p1c[i - 1]]
    deltas = sorted(ts[b] - ts[a] for a, b in zip(bounds, bounds[1:]))
    T = deltas[len(deltas) // 2] if deltas else 1 / 60
    t0 = ts[impact]
    sample = {}
    for k in range(MAXF + 1):
        lo, hi = t0 + k * T, t0 + (k + 1) * T
        idx = [i for i in range(impact, len(ts)) if lo <= ts[i] < hi]
        if idx:
            sample[k] = fr[idx[-1]]
    return sample, st


def main():
    names = sys.argv[1:] or ["c1", "c2", "c3", "c4", "c12", "c34"]
    data = {}
    for n in names:
        r = per_frame(n)
        if r:
            data[n] = r
    if not data:
        return

    print("frame->state (attacker) for reference:")
    for n, (sample, st) in data.items():
        row = []
        prev = None
        for k in sorted(sample):
            s = struct.unpack_from("<H", sample[k], P1 + STATE - st)[0]
            if s != prev:
                row.append(f"{k}:{s}")
                prev = s
        print(f"  {n:5s} " + " ".join(row))

    print()
    print("countdown fields (u16) that reach 0 during frames 15..60:")
    for off in range(0, 0x930 - 1, 2):
        hits = {}
        for n, (sample, st) in data.items():
            seq = []
            for k in range(0, MAXF + 1):
                f = sample.get(k)
                if f is None:
                    continue
                seq.append((k, struct.unpack_from("<H", f, P1 + off - st)[0]))
            zero = next((k for k, v in seq if v == 0 and k > 10), None)
            start_v = next((v for k, v in seq if k <= 3 and v), None)
            if zero is not None and start_v and start_v > 3:
                hits[n] = (start_v, zero)
        if len(hits) >= max(2, len(data) - 1):
            print(
                f"  +0x{off:03X}: "
                + "  ".join(f"{n}={v[0]}->0@{v[1]}" for n, v in hits.items())
            )


if __name__ == "__main__":
    main()
