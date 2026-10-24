#!/usr/bin/env python3
"""Locate the emulated pad state inside PSP user RAM.

The game must keep a copy of SceCtrlData somewhere in user memory; if we find
it we can timestamp every button press frame-accurately *without* injecting
any input (the user plays normally).

Method: three full-RAM snapshots (idle / LEFT held / UP held) and keep the
offsets whose value is exactly the D-pad bitmask for each state.

    python tools/padfind.py
    python tools/padfind.py --verify 0x08xxxxxx
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psp_mem  # noqa: E402

LEFT = 0x0080
UP = 0x0010
RIGHT = 0x0020
DOWN = 0x0040

SCAN_START = 0x08800000
SCAN_SIZE = 0x01700000


class Mem:
    def __init__(self):
        self.pid = psp_mem.find_pid()
        self.h = psp_mem.open_process(self.pid)
        self.base, self.size = psp_mem.find_ram(self.h)

    def read(self, psp_addr: int, size: int) -> bytes:
        host = self.base + (psp_addr - psp_mem.PSP_RAM_START)
        buf = ctypes_buf(size)
        if not psp_mem.read_into(self.h, host, buf):
            raise SystemExit(f"read failed at 0x{psp_addr:08X}")
        return bytes(buf)


def ctypes_buf(size):
    import ctypes

    return ctypes.create_string_buffer(size)


def hold(pad, name, on=True):
    pad.raw({"event": "input.buttons.send", "buttons": {name: on}}, 0.02)


def snap(mem):
    return mem.read(SCAN_START, SCAN_SIZE)


def find(idle, left, up):
    """(offset,width) whose u8/u16/u32 value is 0 / LEFT / UP."""
    hits = []
    for w in (1, 2, 4):
        pat = LEFT.to_bytes(w, "little")
        zero = b"\x00" * w
        upv = UP.to_bytes(w, "little")
        pos = left.find(pat)
        while pos != -1:
            if idle[pos : pos + w] == zero and up[pos : pos + w] == upv:
                hits.append((pos, w))
            pos = left.find(pat, pos + 1)
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", type=lambda s: int(s, 0))
    ap.add_argument("--seconds", type=float, default=6.0)
    args = ap.parse_args()

    mem = Mem()
    print(f"pid={mem.pid} ram_host=0x{mem.base:016X}")

    from pad import Pad

    with Pad() as pad:
        if not pad.wait_connected(3):
            raise SystemExit("daemon has no PPSSPP connection")

        time.sleep(0.4)
        print("snapshot: idle")
        idle = snap(mem)

        hold(pad, "left")
        time.sleep(0.45)
        print("snapshot: left held")
        left = snap(mem)
        hold(pad, "left", False)
        time.sleep(0.35)

        hold(pad, "up")
        time.sleep(0.45)
        print("snapshot: up held")
        up = snap(mem)
        hold(pad, "up", False)

    if args.verify is not None:
        verify(args.verify, args.seconds)
        return

    hits = find(idle, left, up)
    print(f"\n{len(hits)} candidate offset(s):")
    for off, w in hits:
        psp = SCAN_START + off
        print(f"  psp=0x{psp:08X} width={w}")

    # cluster by 16-byte neighbourhood to expose the struct layout
    if hits:
        groups = []
        for off, w in sorted(hits):
            if groups and off - groups[-1][-1][0] <= 32:
                groups[-1].append((off, w))
            else:
                groups.append([(off, w)])
        print("\ngroups:")
        for g in groups:
            lo = SCAN_START + g[0][0]
            print(
                f"  0x{lo:08X} .. 0x{SCAN_START + g[-1][0]:08X} "
                f"({len(g)} hits)"
            )


def verify(addr: int, seconds: float):
    from pad import Pad

    import ctypes

    mem = Mem()
    buf = ctypes.create_string_buffer(8)
    print(f"watching 0x{addr:08X} for {seconds}s; pressing left/right/up/down")
    seq = ["left", "right", "up", "down"]
    t0 = time.time()
    i = 0
    with Pad() as pad:
        while time.time() - t0 < seconds:
            btn = seq[i % len(seq)]
            i += 1
            hold(pad, btn)
            t_end = time.time() + 0.9
            while time.time() < t_end:
                host = mem.base + (addr - psp_mem.PSP_RAM_START)
                psp_mem.read_into(mem.h, host, buf)
                u16 = struct.unpack_from("<H", buf.raw, 0)[0]
                u32 = struct.unpack_from("<I", buf.raw, 0)[0]
                print(f"  {btn:6s} u16=0x{u16:04X} u32=0x{u32:08X}", end="\r")
                time.sleep(0.05)
            hold(pad, btn, False)
            time.sleep(0.1)
    print()


if __name__ == "__main__":
    main()
