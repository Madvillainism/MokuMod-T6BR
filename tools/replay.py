#!/usr/bin/env python3
"""Replay a saved memory recording through the HUD tracker.

    python tools/replay.py s43a
    python tools/replay.py s43a --mids       # also show move-id changes
    python tools/replay.py val_1_0 val_3_0   # several at once
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hud import Tracker, load_recording  # noqa: E402


def replay(name, show_mids=False, verbose=False):
    samples = load_recording(name)
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
