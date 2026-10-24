#!/usr/bin/env python3
"""Run a battery of moves and compare the live advantage with the official
T6 frame data.

    python tools/validate.py            # 3 trials per move
    python tools/validate.py --reps 5

Every trial is a recording from `inputrec.py --bin`; the analysis uses
+0x294 (can act) and +0x295 (recovery tail) read from both player structs.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDIR = os.path.join(ROOT, ".cache", "in")
TOOLS = os.path.join(ROOT, "tools")

MOVES = [
    # name, buttons, official startup, official block advantage
    ("1", ["square"], 10, 1),
    ("2", ["triangle"], 10, 1),
    ("3", ["cross"], 16, -4),
    ("4", ["circle"], 12, -5),
    ("1+2", ["square", "triangle"], 14, -10),
    ("3+4", ["cross", "circle"], 18, -8),
]

FRAME = 1 / 60.0


def record(name, buttons, at=0.7, seconds=2.4):
    cmd = [
        sys.executable,
        os.path.join(TOOLS, "inputrec.py"),
        "--name",
        name,
        "--bin",
        "--press",
        buttons[0],
        "--at",
        str(at),
        "--seconds",
        str(seconds),
    ]
    if len(buttons) > 1:
        cmd += ["--press2", buttons[1]]
    subprocess.run(cmd, cwd=ROOT, capture_output=True)


def analyse(name):
    d = json.load(open(os.path.join(INDIR, name + ".json")))
    span = d["meta"]["span"]
    raw = open(os.path.join(INDIR, name + ".bin"), "rb").read()
    n = len(raw) // span
    recs = [raw[i * span : (i + 1) * span] for i in range(n)]
    samples = d["samples"]

    press = None
    for (t, pad, *_), r in zip(samples, recs):
        if press is None and pad != 0:
            press = t
        if press is not None:
            break
    if press is None:
        return None

    impact = None
    for i, ((t, pad, s1, c1, s2, c2, hp, m1, m2), r) in enumerate(zip(samples, recs)):
        if t < press:
            continue
        if (
            r[0x930 + 0x294] == 0
            and s2 not in (6466, 6482, 14633)
            and all(recs[j][0x930 + 0x294] == 0 for j in range(i, min(i + 3, n)))
        ):
            impact = t
            break
    if impact is None:
        return None

    def rise(off, after):
        for (t, *_), r in zip(samples, recs):
            if t <= after:
                continue
            if r[off] == 1:
                return t
        return None

    p1 = rise(0x295, impact)
    p2 = rise(0x930 + 0x295, impact)
    if p1 is None or p2 is None:
        return None
    return {
        "startup": round((impact - press) / FRAME),
        "adv": round((p2 - p1) / FRAME),
        "raw_adv": (p2 - p1) / FRAME,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    args = ap.parse_args()

    print(f"{'move':5s} {'official':>9s} {'startup':>8s} {'adv':>12s} {'n':>3s}")
    for name, buttons, off_su, off_adv in MOVES:
        vals, sus = [], []
        for r in range(args.reps):
            rec = f"val_{name.replace('+', 'p')}_{r}"
            record(rec, buttons)
            a = analyse(rec)
            if a:
                vals.append(a["adv"])
                sus.append(a["startup"])
        if not vals:
            print(f"{name:5s} {off_adv:+9d} {'-':>8s} {'fail':>12s}")
            continue
        med = statistics.median(vals)
        print(
            f"{name:5s} {off_adv:+9d} {statistics.median(sus):8.0f} "
            f"{med:+6.0f}  {vals} n={len(vals)}"
        )


if __name__ == "__main__":
    main()
