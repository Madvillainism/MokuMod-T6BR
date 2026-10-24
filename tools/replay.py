#!/usr/bin/env python3
"""Replay a saved memory recording through the HUD tracker.

    python tools/replay.py s43a
    python tools/replay.py s43a --mids       # also show move-id changes
    python tools/replay.py val_1_0 val_3_0   # several at once
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hud import (  # noqa: E402
    OFF_COUNTER,
    OFF_FREE,
    OFF_HEALTH,
    OFF_MOVEID,
    OFF_STATE,
    OFF_TAIL,
    STRIDE,
    Tracker,
)

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".cache", "in")


def load(name):
    base = os.path.join(CACHE, name)
    meta = json.load(open(base + ".json"))
    span = meta["meta"]["span"]
    raw = open(base + ".bin", "rb").read()
    n = len(raw) // span
    samples = meta["samples"]
    out = []
    for i in range(n):
        d = raw[i * span : (i + 1) * span]
        out.append(
            (
                samples[i][0],
                {
                    "c1": struct.unpack_from("<I", d, OFF_COUNTER)[0],
                    "mid": struct.unpack_from("<I", d, OFF_MOVEID)[0],
                    "free1": d[OFF_FREE],
                    "tail1": d[OFF_TAIL],
                    "s2": struct.unpack_from("<H", d, STRIDE + OFF_STATE)[0],
                    "c2": struct.unpack_from("<I", d, STRIDE + OFF_COUNTER)[0],
                    "hp2": struct.unpack_from("<H", d, STRIDE + OFF_HEALTH)[0],
                    "free2": d[STRIDE + OFF_FREE],
                    "tail2": d[STRIDE + OFF_TAIL],
                },
            )
        )
    return out


def replay(name, show_mids=False, verbose=False):
    samples = load(name)
    tr = Tracker()
    print(f"=== {name} ({len(samples)} samples) ===")
    prev_mid = None
    last_frame = -1
    for i, (t, s) in enumerate(samples):
        if show_mids:
            if s["mid"] != prev_mid:
                print(f"   sample {i:3d} t={t:6.3f} mid -> {s['mid']}")
            prev_mid = s["mid"]
        for ev in tr.step(s):
            print(
                f"   sample {i:3d} t={t:6.3f} frame={tr.frame:3d} "
                f"{ev['phase']:6s} {ev['label']:4s} i{ev['startup']} "
                f"{'-' if ev['adv'] is None else ev['adv']:>4} {ev['kind']}"
            )
        if verbose and tr.frame != last_frame:
            last_frame = tr.frame
    return tr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+")
    ap.add_argument("--mids", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    for name in a.names:
        replay(name, a.mids, a.verbose)


if __name__ == "__main__":
    main()
