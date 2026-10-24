#!/usr/bin/env python3
"""Measure startup / block / hit advantage for one attack, live.

    python tools/frametest.py --name b1 --press square
    python tools/frametest.py --name bdf1 --hold down --press square

Captures a ~60 Hz RAM movie of both player structs while the button is sent,
then derives the numbers from the state machine:

    startup = attacker's state counter on the impact frame
    A_state = frames the attacker stays in the move state after impact
    D_state = frames the defender stays in a stunned state after impact
    V       = D_state - (A_state - 7)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psp_mem  # noqa: E402
import t6scan  # noqa: E402
from pad import Pad  # noqa: E402

P1 = 0x08C078C0
P2 = 0x08C081F0
STATE = 0x0AC
COUNTER = 0x07C
HEALTH = 0x002

IDLE = {6482, 6466, 14633}  # standing (P1/P2 variants), crouching
STANDING = 6482


def field(frames, start, addr, fmt):
    n = struct.calcsize(fmt)
    out = []
    for f in frames:
        off = addr - start
        out.append(struct.unpack_from(fmt, f, off)[0])
    return out


def analyse(name: str, verbose: bool = True):
    fr, st, ts = t6scan.load_movie(name)
    p1s = field(fr, st, P1 + STATE, "<H")
    p2s = field(fr, st, P2 + STATE, "<H")
    p1c = field(fr, st, P1 + COUNTER, "<I")
    p2c = field(fr, st, P2 + COUNTER, "<I")
    hp2 = field(fr, st, P2 + HEALTH, "<H")

    # move start = first sample in a non-idle state
    start = None
    for i in range(1, len(p1s)):
        if p1s[i] not in IDLE and p1s[i - 1] in IDLE:
            start = i
            break
    if start is None:
        print(f"{name}: no move detected")
        return None

    # impact = first change on P2 (health or state) after the move started
    impact = None
    for i in range(start + 1, len(p2s)):
        if p2s[i] != p2s[i - 1] or hp2[i] != hp2[i - 1]:
            impact = i
            break

    res = {
        "name": name,
        "start": start,
        "move_state": p1s[start],
        "startup": None,
        "damage": 0,
        "A_state": None,
        "D_state": None,
        "V": None,
        "outcome": "whiff",
    }
    if impact is None:
        if verbose:
            print(f"{name}: WHIFF (P2 never reacted)")
        return res

    dmg = hp2[start - 1] - hp2[impact]
    res["startup"] = p1c[impact]
    res["damage"] = dmg

    p1end = max(i for i in range(len(p1s)) if p1s[i] not in IDLE and i >= start)
    p1_free = p1end + 1

    p2_busy = [i for i in range(impact, len(p2s)) if p2s[i] == p2s[impact]]
    p2end = max(p2_busy)
    p2_free = p2end + 1

    # sample-counted durations (jittery: sampling runs at ~59 Hz vs 60 Hz game)
    res["A_state"] = p1_free - impact
    res["D_state"] = p2_free - impact

    # counter-based durations: the in-game counters advance exactly once per
    # game frame, so these do not depend on our sampling rate.
    res["A_exact"] = p1c[p1end] - p1c[impact]
    res["D_exact"] = p2c[p2end] - p2c[impact]
    res["V_raw"] = res["D_exact"] - res["A_exact"]
    res["V"] = res["V_raw"]
    res["outcome"] = "hit" if dmg > 0 else "block"
    res["p2_state"] = p2s[impact]

    if verbose:
        print(
            f"{name}: {res['outcome'].upper():5s} startup={res['startup']:2d} "
            f"dmg={dmg:2d}  A={res['A_exact']:2d} D={res['D_exact']:2d} "
            f"-> raw={res['V_raw']:+d}  (p1 {p1s[start]}, p2 {p2s[impact]})"
        )
    return res


def capture(name, button, hold, pre, duration, frames_btn, start, size, rate=0.0, gap=0.1):
    pid = psp_mem.find_pid()
    h = psp_mem.open_process(pid)
    pad = Pad()
    pad.wait_connected(3)

    def seq():
        time.sleep(pre)
        for b in hold:
            pad.raw({"event": "input.buttons.send", "buttons": {b: True}})
            time.sleep(0.08)
        if button:
            # "circle,cross" = string: press circle, wait `gap`, press cross
            for i, item in enumerate([p for p in button.split(",") if p]):
                if i:
                    time.sleep(gap)
                parts = item.split("+")
                if len(parts) > 1:
                    pad.raw(
                        {"event": "input.buttons.send", "buttons": {b: True for b in parts}},
                        0.05,
                    )
                    time.sleep(0.03)
                    pad.raw(
                        {"event": "input.buttons.send", "buttons": {b: False for b in parts}},
                        0.05,
                    )
                else:
                    t = pad.press(item, frames_btn)
                    pad.wait_ticket(t, timeout=6)
        time.sleep(0.1)
        if hold:
            pad.raw(
                {"event": "input.buttons.send", "buttons": {b: False for b in hold}}
            )

    threading.Thread(target=seq, daemon=True).start()

    frames, times = [], []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < duration:
        frames.append(t6scan.psp_read(h, start, size))
        times.append(time.perf_counter() - t0)
        if rate:
            nxt = t0 + len(frames) / rate
            while time.perf_counter() < nxt:
                pass
    os.makedirs(t6scan.SNAPDIR, exist_ok=True)
    with open(t6scan.snap_path(name), "wb") as f:
        f.write(b"".join(frames))
    json.dump(
        {
            "start": start,
            "size": size,
            "count": len(frames),
            "times": [round(t, 6) for t in times],
            "movie": True,
            "button": button,
            "hold": hold,
        },
        open(t6scan.snap_path(name) + ".json", "w"),
    )
    pad.close()
    return len(frames)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="exp")
    ap.add_argument("--press")
    ap.add_argument("--hold", default="")
    ap.add_argument("--pre", type=float, default=0.55)
    ap.add_argument("--duration", type=float, default=3.0)
    ap.add_argument("--frames", type=int, default=2, help="button hold in vblanks")
    ap.add_argument("--start", default="0x08C078C0")
    ap.add_argument("--size", default="0x1290")
    ap.add_argument("--analyse-only", action="store_true")
    ap.add_argument("--batch", help="comma list of 'name:button[:hold]'")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--rate", type=float, default=0.0, help="samples/s, 0 = max")
    ap.add_argument("--gap", type=float, default=0.1, help="seconds between string presses")
    a = ap.parse_args()

    start, size = int(a.start, 0), int(a.size, 0)

    if a.batch:
        results = []
        items = []
        for item in a.batch.split(","):
            parts = item.split(":")
            items.append(
                (
                    parts[0],
                    parts[1] if len(parts) > 1 else None,
                    parts[2].split("+") if len(parts) > 2 else [],
                )
            )
        for rep in range(a.repeat):
            for name, button, hold in items:
                run = f"{name}" if a.repeat == 1 else f"{name}{rep}"
                n = capture(run, button, hold, a.pre, a.duration, a.frames, start, size, a.rate, a.gap)
                print(f"  {run}: {n} frames", flush=True)
                results.append(analyse(run))
                time.sleep(0.6)

        # aggregate: misses can only shorten a duration, so take the max
        agg = {}
        for r in results:
            if not r or r.get("A_exact") is None:
                continue
            key = r["name"].rstrip("0123456789")
            cur = agg.setdefault(
                key,
                {
                    "name": key,
                    "outcome": r["outcome"],
                    "startup": 0,
                    "damage": r["damage"],
                    "A": 0,
                    "D": 0,
                    "n": 0,
                    "p2": r.get("p2_state"),
                },
            )
            cur["startup"] = max(cur["startup"], r["startup"])
            cur["A"] = max(cur["A"], r["A_exact"])
            cur["D"] = max(cur["D"], r["D_exact"])
            cur["outcome"] = r["outcome"]
            cur["n"] += 1

        print()
        print(f"{'move':8s} {'outcome':7s} {'n':>2s} {'startup':>7s} {'dmg':>4s} {'A':>3s} {'D':>3s} {'V':>4s}")
        for k, v in agg.items():
            print(
                f"{k:8s} {v['outcome']:7s} {v['n']:>2d} {v['startup']:>7d} "
                f"{v['damage']:>4d} {v['A']:>3d} {v['D']:>3d} {v['D']-v['A']:>+4d}"
            )
        return

    if not a.analyse_only:
        n = capture(
            a.name, a.press, [h for h in a.hold.split("+") if h],
            a.pre, a.duration, a.frames, start, size, a.rate, a.gap,
        )
        print(f"captured {n} frames")
    analyse(a.name)


if __name__ == "__main__":
    main()
