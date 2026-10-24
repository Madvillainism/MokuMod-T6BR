#!/usr/bin/env python3
"""Timing-based analysis of a captured movie.

The state counters tell us *what* frame we are on; the sample timestamps tell
us *when* each transition happened, with sub-frame precision.  Measuring the
transitions in time and dividing by the frame period removes the +-1 frame
jitter that pure sample counting has.

    python tools/tanalyse.py c1 c2 cdf2
"""
from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t6scan  # noqa: E402

P1 = 0x08C078C0
P2 = 0x08C081F0
STATE, COUNTER, HEALTH = 0x0AC, 0x07C, 0x002
IDLE = {6482, 6466, 14633}


def series(fr, st, addr, fmt):
    return [struct.unpack_from(fmt, f, addr - st)[0] for f in fr]


def analyse(name, verbose=True):
    fr, st, ts = t6scan.load_movie(name)
    p1s = series(fr, st, P1 + STATE, "<H")
    p2s = series(fr, st, P2 + STATE, "<H")
    p1c = series(fr, st, P1 + COUNTER, "<I")
    p2c = series(fr, st, P2 + COUNTER, "<I")
    hp2 = series(fr, st, P2 + HEALTH, "<H")

    # frame period = median interval between consecutive frame boundaries,
    # where a boundary is any sample whose counter differs from the previous one
    bounds = [i for i in range(1, len(ts)) if p1c[i] != p1c[i - 1]]
    deltas = sorted(ts[b] - ts[a] for a, b in zip(bounds, bounds[1:]))
    T = deltas[len(deltas) // 2] if deltas else 1 / 60

    # move start: first sample in a non-idle state after idle
    start = next(
        (i for i in range(1, len(p1s)) if p1s[i] not in IDLE and p1s[i - 1] in IDLE),
        None,
    )
    if start is None:
        print(f"{name}: no move")
        return None

    # impact: first sample where the defender leaves idle (or loses health)
    impact = next(
        (
            i
            for i in range(start + 1, len(p2s))
            if (p2s[i] not in IDLE and p2s[i - 1] in IDLE) or hp2[i] != hp2[i - 1]
        ),
        None,
    )
    if impact is None:
        print(f"{name}: whiff (P1 state {p1s[start]})")
        return None

    # first idle sample after the impact for each player
    p1_free = next((i for i in range(impact, len(p1s)) if p1s[i] in IDLE), None)
    p2_free = next((i for i in range(impact, len(p2s)) if p2s[i] in IDLE), None)

    def mid(i):
        """time of the transition into sample i (between i-1 and i)"""
        return (ts[i - 1] + ts[i]) / 2 if i else ts[0]

    ti = mid(impact)
    ta = mid(p1_free) if p1_free else None
    td = mid(p2_free) if p2_free else None

    res = {
        "name": name,
        "outcome": "hit" if hp2[impact] < hp2[start - 1] else "block",
        "startup": p1c[impact],
        "dmg": hp2[start - 1] - hp2[impact],
        "T": T * 1000,
        "A": round((ta - ti) / T) if ta else None,
        "D": round((td - ti) / T) if td else None,
        "p1_state": p1s[start],
        "p2_state": p2s[impact],
        "raw_ms": (td - ta) * 1000 if (ta and td) else None,
    }
    if res["A"] is not None and res["D"] is not None:
        res["V"] = res["D"] - res["A"]
        res["V_float"] = (td - ta) / T
    else:
        res["V"] = None
    if verbose:
        print(
            f"{name:8s} {res['outcome']:5s} i{res['startup']:<3d} dmg={res['dmg']:<3d} "
            f"A={res['A']} D={res['D']} V={res['V']}  "
            f"(T={res['T']:.2f}ms raw={res['raw_ms']:.1f}ms "
            f"Vf={res.get('V_float', 0):+.2f} "
            f"p1={res['p1_state']} p2={res['p2_state']})"
        )
    return res


def main():
    names = sys.argv[1:]
    if not names:
        names = ["c1", "c2", "cdf2"]
    for n in names:
        analyse(n)


if __name__ == "__main__":
    main()
