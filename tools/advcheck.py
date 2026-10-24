#!/usr/bin/env python3
"""Frame advantage from the `can act` flag (+0x294).

    python tools/advcheck.py st2 [more names...]

Reports startup, attacker recovery, defender stun and the resulting advantage
from per-frame recordings made with `inputrec.py --bin`.
"""
from __future__ import annotations

import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDIR = os.path.join(ROOT, ".cache", "in")

P1 = 0x08C078C0
P2 = 0x08C081F0
STRIDE = P2 - P1
FREE = 0x294
TAIL = 0x295
MOVEID = 0x144
STATE = 0x0AC
COUNTER = 0x07C
IDLE = {6482, 6466, 14633}


def load(name):
    d = json.load(open(os.path.join(INDIR, name + ".json")))
    span = d["meta"]["span"]
    raw = open(os.path.join(INDIR, name + ".bin"), "rb").read()
    n = len(raw) // span
    recs = [raw[i * span : (i + 1) * span] for i in range(n)]
    return d["samples"], recs


def frames(samples, recs):
    out = []
    cur = None
    for s, r in zip(samples, recs):
        c = struct.unpack_from("<I", r, COUNTER)[0]
        if c != cur:
            out.append([s[0], r])
            cur = c
        else:
            out[-1][0] = s[0]
            out[-1][1] = r
    return out


def analyse(name, verbose=True):
    samples, recs = load(name)
    fr = frames(samples, recs)
    free1 = [r[FREE] for _, r in fr]
    free2 = [r[STRIDE + FREE] for _, r in fr]
    mid1 = [struct.unpack_from("<I", r, MOVEID)[0] for _, r in fr]
    st1 = [struct.unpack_from("<H", r, STATE)[0] for _, r in fr]
    st2 = [struct.unpack_from("<H", r, STRIDE + STATE)[0] for _, r in fr]

    start = next((i for i in range(1, len(fr)) if mid1[i] and not mid1[i - 1]), None)
    if start is None:
        print(f"{name}: no move")
        return None

    # impact = first frame where the defender loses the free flag
    impact = next(
        (i for i in range(start, len(fr)) if free2[i] == 0 and free2[i - 1] == 1),
        None,
    )
    if impact is None:
        print(f"{name}: no impact")
        return None

    p1_unlock = next((i for i in range(impact, len(fr)) if free1[i] == 1), None)
    p2_unlock = next((i for i in range(impact, len(fr)) if free2[i] == 1), None)

    res = {
        "name": name,
        "startup": impact - start,
        "p1_lock": next((i for i in range(start, impact + 3) if free1[i] == 0), None),
        "recovery": None if p1_unlock is None else p1_unlock - impact,
        "stun": None if p2_unlock is None else p2_unlock - impact,
        "adv": None if (p1_unlock is None or p2_unlock is None) else p2_unlock - p1_unlock,
        "move": mid1[start],
        "blocked": st2[impact] not in IDLE,
    }
    if verbose:
        print(
            f"{name:8s} move={res['move']:3d} startup={res['startup']:3d} "
            f"recovery={res['recovery']} stun={res['stun']} "
            f"adv={res['adv']:+d}" if res["adv"] is not None else f"{name}: incomplete"
        )
    return res


if __name__ == "__main__":
    for n in sys.argv[1:]:
        analyse(n)
