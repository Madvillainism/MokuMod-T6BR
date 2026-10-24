#!/usr/bin/env python3
"""Frame-accurate recorder of pad + both player structs.

Reads the emulated pad (`PAD_ADDR`) and the P1/P2 structs in one sample, so
every button edge can be aligned to the exact game frame.  Injected presses go
through PPSSPP's `input.buttons.press` (duration in emulated frames), not wall
clock.

    python tools/inputrec.py --name i1 --press square --at 0.7 --seconds 2
    python tools/inputrec.py --name i2 --press square --at 0.7 \
        --mash triangle --mash-at 0.95 --every 3 --seconds 3
    python tools/inputrec.py --name i3 --seconds 4          # watch the user

Saves .cache/in/NAME.json and prints the change log.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psp_mem  # noqa: E402
from pad import Pad  # noqa: E402

P1 = 0x08C078C0
P2 = 0x08C081F0
STATE = 0x0AC
COUNTER = 0x07C
HEALTH = 0x002
MOVEID = 0x144
PAD_ADDR = 0x08BFE4AC

IDLE = {6482, 6466, 14633}

BTN = {
    "select": 0x0001,
    "start": 0x0008,
    "up": 0x0010,
    "right": 0x0020,
    "down": 0x0040,
    "left": 0x0080,
    "l": 0x0100,
    "r": 0x0200,
    "triangle": 0x1000,
    "circle": 0x2000,
    "cross": 0x4000,
    "square": 0x8000,
}
NAME = {v: k for k, v in BTN.items()}
SPAN = P2 - P1 + 0x930

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, ".cache", "in")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="rec")
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--press")
    ap.add_argument("--press2")
    ap.add_argument("--at", type=float, default=0.7)
    ap.add_argument("--mash")
    ap.add_argument("--mash-at", type=float, default=None)
    ap.add_argument("--frames", type=int, default=2)
    ap.add_argument("--every", type=int, default=3)
    ap.add_argument("--bin", action="store_true", help="dump full struct per sample")
    ap.add_argument("--no-wait", action="store_true", help="do not wait for idle")
    args = ap.parse_args()

    pid = psp_mem.find_pid()
    h = psp_mem.open_process(pid)
    base, _ = psp_mem.find_ram(h)
    host = base + (P1 - psp_mem.PSP_RAM_START)
    host_pad = base + (PAD_ADDR - psp_mem.PSP_RAM_START)
    buf = ctypes.create_string_buffer(SPAN)
    padbuf = ctypes.create_string_buffer(2)

    os.makedirs(OUT, exist_ok=True)
    samples = []
    blanks = []
    t0 = time.perf_counter()

    def read():
        psp_mem.read_into(h, host, buf)
        psp_mem.read_into(h, host_pad, padbuf)
        d = buf.raw
        return (
            struct.unpack_from("<H", padbuf.raw, 0)[0],
            struct.unpack_from("<H", d, STATE)[0],
            struct.unpack_from("<I", d, COUNTER)[0],
            struct.unpack_from("<H", d, P2 - P1 + STATE)[0],
            struct.unpack_from("<I", d, P2 - P1 + COUNTER)[0],
            struct.unpack_from("<H", d, P2 - P1 + HEALTH)[0],
            struct.unpack_from("<I", d, MOVEID)[0],
            struct.unpack_from("<I", d, P2 - P1 + MOVEID)[0],
        )

    pad = None
    if args.press or args.mash:
        pad = Pad()
        if not pad.wait_connected(3):
            raise SystemExit("daemon has no PPSSPP connection")

    def tap(button, frames):
        pad.sock.sendall(
            (
                json.dumps(
                    {"cmd": "press", "button": button, "frames": frames}
                )
                + "\n"
            ).encode()
        )

    mash_at = args.mash_at
    fired_press = False
    next_mash = None if mash_at is None else mash_at
    sent_marks = []

    if not args.no_wait:
        idle_since = None
        while True:
            s = read()
            if s[1] in IDLE and s[6] == 0:
                if idle_since is None:
                    idle_since = time.perf_counter()
                elif time.perf_counter() - idle_since > 0.30:
                    break
            else:
                idle_since = None
            time.sleep(0.001)
        t0 = time.perf_counter()

    last = None
    while True:
        now = time.perf_counter()
        t = now - t0
        if t >= args.seconds:
            break

        if args.press and not fired_press and t >= args.at:
            tap(args.press, args.frames)
            if args.press2:
                tap(args.press2, args.frames)
            sent_marks.append((t, args.press))
            fired_press = True

        if args.mash and next_mash is not None and t >= next_mash:
            tap(args.mash, args.frames)
            sent_marks.append((t, args.mash + "*"))
            next_mash += args.every / 60.0
            if next_mash >= args.seconds - 0.05:
                next_mash = None

        s = read()
        if s != last:
            samples.append((t, *s))
            if args.bin:
                blanks.append(buf.raw[:SPAN])
            last = s
        time.sleep(0.0005)

    if pad:
        pad.close()

    data = {
        "name": args.name,
        "samples": [list(s) for s in samples],
        "sent": sent_marks,
        "meta": {"p1": P1, "pad": PAD_ADDR, "seconds": args.seconds,
                 "span": SPAN, "bin": args.bin},
    }
    if args.bin:
        bpath = os.path.join(OUT, args.name + ".bin")
        with open(bpath, "wb") as f:
            for b in blanks:
                f.write(b)
    path = os.path.join(OUT, args.name + ".json")
    with open(path, "w") as f:
        json.dump(data, f)

    print(f"saved {path}  samples={len(samples)}  sent={len(sent_marks)}")
    report(samples)


def report(samples):
    print("     t      pad      p1s   p1c   p2s   p2c  hp  mid1  mid2  note")
    prev = None
    for t, p, s1, c1, s2, c2, hp, m1, m2 in samples:
        note = []
        if prev:
            pp, ps1, pc1, pm1 = prev
            on = p & ~pp
            off = pp & ~p
            for bit in range(16):
                b = 1 << bit
                if on & b and b in NAME:
                    note.append(NAME[b] + "+")
                if off & b and b in NAME:
                    note.append(NAME[b] + "-")
            if c1 < pc1 and c1 <= 3:
                note.append(f"p1c<{c1}")
            if m1 != pm1:
                note.append(f"MID1 {pm1}>{m1}")
        print(
            f"{t:6.3f} 0x{p:04X} {s1:6d} {c1:5d} {s2:6d} {c2:5d} {hp:4d} "
            f"{m1:5d} {m2:5d}  {' '.join(note)}"
        )
        prev = (p, s1, c1, m1)


if __name__ == "__main__":
    main()
