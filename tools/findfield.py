#!/usr/bin/env python3
"""Search the defender's struct for per-move stun timers.

For each movie, index samples by the defender's own state counter (1 on the
impact frame, +1 per game frame), then read every aligned u16 of the struct at
those frames.  Comparing movies whose block advantage differs shows which
field carries the stun duration.

    python tools/findfield.py c1 c2 c3 c4
"""
from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t6scan  # noqa: E402

P2 = 0x08C081F0
STATE, COUNTER = 0x0AC, 0x07C
IDLE = {6482, 6466, 14633}
FRAMES = 14


def frame_values(name):
    """-> (stun_state, {frame: raw_sample_bytes})"""
    fr, st, ts = t6scan.load_movie(name)
    p2s = [struct.unpack_from("<H", f, P2 + STATE - st)[0] for f in fr]
    p2c = [struct.unpack_from("<I", f, P2 + COUNTER - st)[0] for f in fr]

    impact = next(
        (i for i in range(1, len(p2s)) if p2s[i] not in IDLE and p2s[i - 1] in IDLE),
        None,
    )
    if impact is None:
        return None, None, None

    stun_state = p2s[impact]
    last = {}
    for i in range(impact, len(p2s)):
        if p2s[i] == stun_state and p2c[i] > 0:
            last[p2c[i]] = i
    return stun_state, {k: fr[i] for k, i in last.items()}, st


def main():
    names = sys.argv[1:] or ["c1", "c2", "c3", "c4"]
    data = {}
    for n in names:
        st_, rows, start = frame_values(n)
        if not rows:
            print(f"{n}: no stun")
            continue
        data[n] = (st_, rows, start)
        print(f"{n}: stun state {st_}, frames {min(rows)}..{max(rows)}")

    if not data:
        return
    frames = list(range(1, FRAMES + 1))
    if any(not all(k in rows for k in frames) for _, rows, _ in data.values()):
        print("not enough frames in some movie")
        return

    print()
    print(f"fields that fall monotonically over frames 1..{FRAMES}:")
    for off in range(0, 0x930 - 1, 2):
        seqs = {}
        for n, (_, rows, start) in data.items():
            seqs[n] = [
                struct.unpack_from("<H", rows[k], P2 + off - start)[0]
                for k in frames
            ]
        if not all(
            all(b <= a for a, b in zip(v, v[1:])) and v[0] - v[-1] >= 3
            for v in seqs.values()
        ):
            continue
        starts = [v[0] for v in seqs.values()]
        if max(starts) < 6:
            continue
        # only interesting when the start value differs between movies
        if len(set(starts)) < 2:
            continue
        print(f"  +0x{off:03X}: " + "   ".join(f"{n}={seqs[n]}" for n in names))


if __name__ == "__main__":
    main()
