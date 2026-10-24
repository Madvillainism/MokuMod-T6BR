#!/usr/bin/env python3
"""Read-only PPSSPP host memory inspector.

PPSSPP maps PSP RAM at host address  base + 0x08000000  where `base` is a
random 64K-aligned arena base.  This tool finds that view and exposes
PSP-address reads without touching the emulator.

Usage:
  python tools/psp_mem.py --list
  python tools/psp_mem.py --find
  python tools/psp_mem.py --read 0x08800000 64
  python tools/psp_mem.py --read 0x08900000 16 --hex
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import struct
import sys

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01

PSP_RAM_START = 0x08000000
PSP_USER_START = 0x08800000
PSP_RAM_SIZES = {0x01800000, 0x02000000, 0x04000000}


class MEMORY_BASIC_INFORMATION64(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", wt.DWORD),
        ("__alignment1", wt.DWORD),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("__alignment2", wt.DWORD),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.VirtualQueryEx.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION64),
    ctypes.c_size_t,
]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.ReadProcessMemory.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL


def find_pid(name: str = "PPSSPPWindows64.exe") -> int:
    import subprocess

    out = subprocess.check_output(
        ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
        text=True,
        errors="ignore",
    )
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == name.lower():
            return int(parts[1])
    raise SystemExit(f"process not found: {name}")


def open_process(pid: int) -> wt.HANDLE:
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        raise SystemExit(f"OpenProcess failed: {ctypes.get_last_error()}")
    return h


def find_pid_or_none(name: str = "PPSSPPWindows64.exe") -> int | None:
    """Like find_pid() but returns None instead of exiting."""
    try:
        return find_pid(name)
    except BaseException:
        return None


def emulator_alive(pid: int | None) -> bool:
    """True while `pid` is still the running emulator (used by the HUD watchdog)."""
    if not pid:
        return False
    return find_pid_or_none() == pid


def iter_regions(h):
    addr = 0
    mbi = MEMORY_BASIC_INFORMATION64()
    while addr < 0x7FFFFFFFFFFF:
        got = kernel32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not got:
            break
        yield mbi
        nxt = mbi.BaseAddress + mbi.RegionSize
        if nxt <= addr:
            break
        addr = nxt


def read_mem(h, addr: int, size: int) -> bytes | None:
    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        h, ctypes.c_void_p(addr), buf, size, ctypes.byref(read)
    )
    if not ok or read.value != size:
        return None
    return buf.raw


def read_into(h, addr: int, buf) -> bool:
    """ReadProcessMemory into an existing buffer (no allocation per call)."""
    read = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        h, ctypes.c_void_p(addr), buf, len(buf), ctypes.byref(read)
    )
    return bool(ok) and read.value == len(buf)


def list_regions(h):
    rows = []
    for m in iter_regions(h):
        if m.State != MEM_COMMIT:
            continue
        if m.Protect & (PAGE_NOACCESS | PAGE_GUARD):
            continue
        if m.RegionSize >= 0x100000:
            rows.append((m.BaseAddress, m.RegionSize, m.Protect, m.Type))
    rows.sort(key=lambda r: r[0])
    print(f"{'base':>18} {'size':>12} {'protect':>9} {'type':>6}")
    for base, size, protect, typ in rows:
        print(f"0x{base:016X} 0x{size:010X} 0x{protect:08X} {typ:6d}")
    return rows


def find_ram(h):
    """Return (psp_base_host, psp_size) for the PSP RAM view (PSP 0x08000000).

    PPSSPP maps four mirrors of RAM, 0x40000000 apart, each MEM_MAPPED.
    Pick the lowest base whose +0x900000 (PSP 0x08900000) holds real code.
    """
    candidates = []
    for m in iter_regions(h):
        if m.State != MEM_COMMIT:
            continue
        if m.Protect & (PAGE_NOACCESS | PAGE_GUARD):
            continue
        if m.Type != 262144:  # MEM_MAPPED
            continue
        size = m.RegionSize
        if not (0x01000000 <= size <= 0x0A000000):
            continue
        data = read_mem(h, m.BaseAddress + 0x00900000, 16)
        if data and sum(b != 0 for b in data) >= 8:
            candidates.append((m.BaseAddress, size, data))
    if not candidates:
        raise SystemExit("PSP RAM view not found; run --candidates")
    # Real RAM appears as >=2 identical mirrors 0x40000000 apart.  The wrong
    # 18 MB table region is unique, so require the mirror relationship.
    by_base = {base: (base, size, data) for base, size, data in candidates}
    groups = []
    used = set()
    for base, size, data in candidates:
        if base in used:
            continue
        group = [(base, size, data)]
        for step in (0x40000000, -0x40000000, 0x80000000, -0x80000000):
            other = by_base.get(base + step)
            if other and other[2] == data:
                group.append(other)
        if len(group) >= 2:
            groups.append(group)
            used.update(g[0] for g in group)
    if not groups:
        raise SystemExit("PSP RAM mirrors not found; run --candidates")
    groups.sort(key=lambda g: (-max(x[1] for x in g), min(x[0] for x in g)))
    best = min(groups[0], key=lambda x: x[0])
    return best[0], best[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--candidates", action="store_true")
    ap.add_argument("--find", action="store_true")
    ap.add_argument("--read", nargs=2, metavar=("PSP_ADDR", "SIZE"))
    ap.add_argument("--hex", action="store_true")
    args = ap.parse_args()

    pid = args.pid or find_pid()
    h = open_process(pid)
    print(f"pid={pid}", file=sys.stderr)

    if args.list:
        list_regions(h)
        return

    if args.candidates:
        rows = []
        for m in iter_regions(h):
            if m.State != MEM_COMMIT or (m.Protect & (PAGE_NOACCESS | PAGE_GUARD)):
                continue
            if m.RegionSize < 0x01000000:
                continue
            head = read_mem(h, m.BaseAddress + 0x00800000, 16)
            code = read_mem(h, m.BaseAddress + 0x00900000, 16)
            rows.append((m.BaseAddress, m.RegionSize, m.Protect, m.Type, head, code))
        print(f"{'base':>18} {'size':>10} {'type':>6}  +0x800000            +0x900000")
        for base, size, protect, typ, head, code in rows:
            hx = head.hex() if head else "-"
            cx = code.hex() if code else "-"
            print(f"0x{base:016X} 0x{size:08X} {typ:6d}  {hx:<20} {cx}")
        return

    if args.find:
        base, size = find_ram(h)
        print(f"ram_host=0x{base:016X} ram_size=0x{size:X}")
        print(f"psp_0x08800000_host=0x{base + 0x00800000:016X}")
        return

    if args.read:
        psp_addr = int(args.read[0], 0)
        size = int(args.read[1], 0)
        base, _ = find_ram(h)
        host = base + (psp_addr - PSP_RAM_START)
        data = read_mem(h, host, size)
        if data is None:
            raise SystemExit(f"read failed at host 0x{host:X}")
        if args.hex:
            for i in range(0, len(data), 16):
                chunk = data[i : i + 16]
                hx = " ".join(f"{b:02X}" for b in chunk)
                print(f"{psp_addr + i:08X}  {hx:<47}")
        else:
            print(f"host=0x{host:X} bytes={len(data)}")
            print(data.hex())
        return

    ap.print_help()


if __name__ == "__main__":
    main()
