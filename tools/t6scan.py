#!/usr/bin/env python3
"""Tekken 6 (PSP) differential memory scanner.

Snapshots PSP user RAM through the host process (tools/psp_mem.py helpers),
then compares snapshots to isolate live game state fields.

Commands
  snap NAME [--start 0x08800000] [--size 0x01800000]
  show NAME ADDR LEN [--hex|--u32|--u16|--float]
  find NAME KIND VALUE          kind: u8|u16|u32|f32
  changes A B                   offsets that differ
  tri A B C                     offsets where A == C but B differs
  stable A B                    offsets where A == B (useful with tri)
  list

Snapshots live in .cache/snap/NAME.bin with a .json sidecar.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psp_mem  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPDIR = os.path.join(ROOT, ".cache", "snap")
DEFAULT_START = 0x08800000
DEFAULT_SIZE = 0x01700000  # 0x08800000..0x09F00000 (host view is 31 MB)
CHUNK = 0x100000


_RAM_CACHE: dict = {}


def psp_read(handle, start: int, size: int) -> bytes:
    key = int(handle or 0)
    cached = _RAM_CACHE.get(key)
    if cached is None:
        cached = psp_mem.find_ram(handle)
        _RAM_CACHE[key] = cached
    base, ram_size = cached
    ram_start = psp_mem.PSP_RAM_START
    end = start + size
    if start < ram_start or end > ram_start + ram_size:
        raise SystemExit(
            f"range 0x{start:08X}+0x{size:X} outside PSP RAM "
            f"0x{ram_start:08X}+0x{ram_size:X}"
        )
    out = bytearray()
    cur = start
    while cur < end:
        n = min(CHUNK, end - cur)
        host = base + (cur - ram_start)
        data = psp_mem.read_mem(handle, host, n)
        if data is None:
            raise SystemExit(f"read failed at PSP 0x{cur:08X} (host 0x{host:X})")
        out += data
        cur += n
    return bytes(out)


def snap_path(name: str):
    return os.path.join(SNAPDIR, name + ".bin")


def load_snap(name: str):
    p = snap_path(name)
    if not os.path.exists(p):
        raise SystemExit(f"snapshot not found: {p}")
    meta = json.load(open(p + ".json"))
    return open(p, "rb").read(), meta["start"]


def cmd_snap(args, handle):
    os.makedirs(SNAPDIR, exist_ok=True)
    start = int(args.start, 0)
    size = int(args.size, 0)
    data = psp_read(handle, start, size)
    with open(snap_path(args.name), "wb") as f:
        f.write(data)
    json.dump(
        {"start": start, "size": size, "md5": __import__("hashlib").md5(data).hexdigest()},
        open(snap_path(args.name) + ".json", "w"),
    )
    nz = sum(1 for b in data if b)
    print(
        f"saved {args.name}: start=0x{start:08X} size=0x{size:X} "
        f"nonzero={nz} ({100.0*nz/len(data):.1f}%)"
    )


def cmd_burst(args, handle):
    """Take N snapshots from one process/handle without re-detecting the base."""
    import time

    os.makedirs(SNAPDIR, exist_ok=True)
    start = int(args.start, 0)
    size = int(args.size, 0)
    interval = float(args.interval)
    t0 = time.time()
    for i in range(args.count):
        data = psp_read(handle, start, size)
        name = f"{args.name}{i:03d}"
        with open(snap_path(name), "wb") as f:
            f.write(data)
        json.dump(
            {"start": start, "size": size, "t": time.time() - t0},
            open(snap_path(name) + ".json", "w"),
        )
        print(f"{name} t={time.time()-t0:.3f}s", flush=True)
        if i + 1 < args.count:
            time.sleep(interval)


def cmd_movie(args, handle):
    """High-rate capture of a small window into one file.

    Layout: concat of `count` frames of `size` bytes at `start`, plus a JSON
    sidecar with per-frame host timestamps.
    """
    import time

    os.makedirs(SNAPDIR, exist_ok=True)
    start = int(args.start, 0)
    size = int(args.size, 0)
    times = []
    with open(snap_path(args.name), "wb") as f:
        t0 = time.perf_counter()
        for _ in range(args.count):
            data = psp_read(handle, start, size)
            f.write(data)
            times.append(round(time.perf_counter() - t0, 6))
    json.dump(
        {"start": start, "size": size, "count": args.count, "times": times, "movie": True},
        open(snap_path(args.name) + ".json", "w"),
    )
    span = times[-1] - times[0] if len(times) > 1 else 0
    print(
        f"movie {args.name}: {args.count} frames x 0x{size:X} B "
        f"in {times[-1]:.2f}s ({args.count/max(times[-1],1e-9):.1f} fps)"
    )


def load_movie(name: str):
    meta = json.load(open(snap_path(name) + ".json"))
    if not meta.get("movie"):
        raise SystemExit("not a movie snapshot")
    data = open(snap_path(name), "rb").read()
    size = meta["size"]
    frames = [data[i * size : (i + 1) * size] for i in range(meta["count"])]
    return frames, meta["start"], meta["times"]


def cmd_series(args, _handle):
    """Print one field across a movie."""
    frames, start, times = load_movie(args.name)
    addr = int(args.addr, 0) - start
    fmt = {"u8": "<B", "u16": "<H", "u32": "<I", "f32": "<f"}[args.type]
    n = struct.calcsize(fmt)
    vals = []
    for i, fr in enumerate(frames):
        if addr < 0 or addr + n > len(fr):
            raise SystemExit("address outside movie window")
        vals.append(struct.unpack_from(fmt, fr, addr)[0])
    for i, v in enumerate(vals):
        print(f"{i:4d} t={times[i]:7.3f}  {v}")


def cmd_filter(args, _handle):
    """List offsets inside a movie that behave in a given way.

    Finds u16/u32 fields whose value changes and then returns to its start
    value within the movie (a move/state pulse), or that change monotonically.
    """
    frames, start, times = load_movie(args.name)
    width = 2 if args.u16 else 4
    fmt = "<H" if args.u16 else "<I"
    nframes = len(frames)
    hits = []
    for off in range(0, len(frames[0]) - width, width):
        vals = [struct.unpack_from(fmt, f, off)[0] for f in frames]
        v0 = vals[0]
        changed = any(v != v0 for v in vals)
        if not changed:
            continue
        if args.mode == "pulse":
            # must return to v0 at the end and stay at v0 for >=2 frames
            if vals[-1] != v0 or vals[-2] != v0:
                continue
            last_change = max(i for i, v in enumerate(vals) if v != v0)
            first_change = min(i for i, v in enumerate(vals) if v != v0)
            if last_change - first_change < args.min_span:
                continue
        elif args.mode == "mono":
            deltas = [vals[i + 1] - vals[i] for i in range(nframes - 1)]
            if any(d < 0 for d in deltas) or not any(d > 0 for d in deltas):
                continue
        hits.append((off, v0, vals))
    print(f"{len(hits)} candidate field(s), mode={args.mode}")
    for off, v0, vals in hits[: args.limit]:
        seq = " ".join(str(v) for v in vals[: args.cols])
        print(f"  0x{start+off:08X}  v0={v0:<10} {seq}")
    if len(hits) > args.limit:
        print(f"  ... {len(hits)-args.limit} more")


def cmd_show(args, _handle):
    data, start = load_snap(args.name)
    off = int(args.addr, 0) - start
    n = int(args.len, 0)
    if off < 0 or off + n > len(data):
        raise SystemExit("range outside snapshot")
    chunk = data[off : off + n]
    base = start + off
    if args.u32:
        for i in range(0, len(chunk) - 3, 4):
            v = struct.unpack_from("<I", chunk, i)[0]
            print(f"{base+i:08X}  u32 {v:12d}  0x{v:08X}")
    elif args.u16:
        for i in range(0, len(chunk) - 1, 2):
            v = struct.unpack_from("<H", chunk, i)[0]
            print(f"{base+i:08X}  u16 {v:12d}  0x{v:04X}")
    elif args.float:
        for i in range(0, len(chunk) - 3, 4):
            v = struct.unpack_from("<f", chunk, i)[0]
            print(f"{base+i:08X}  f32 {v:.6f}")
    else:
        for i in range(0, len(chunk), 16):
            part = chunk[i : i + 16]
            hx = " ".join(f"{b:02X}" for b in part)
            print(f"{base+i:08X}  {hx:<47}")


def cmd_find(args, _handle):
    data, start = load_snap(args.name)
    kind = args.kind
    if kind == "u8":
        needle = struct.pack("<B", int(args.value, 0))
        align = 1
    elif kind == "u16":
        needle = struct.pack("<H", int(args.value, 0))
        align = 2
    elif kind == "u32":
        needle = struct.pack("<I", int(args.value, 0))
        align = 4
    elif kind == "f32":
        needle = struct.pack("<f", float(args.value))
        align = 4
    else:
        raise SystemExit("bad kind")
    hits = []
    pos = data.find(needle)
    while pos >= 0:
        if pos % align == 0:
            hits.append(pos)
        pos = data.find(needle, pos + 1)
    print(f"{len(hits)} match(es) for {kind} {args.value}")
    for h in hits[: args.limit]:
        print(f"  0x{start+h:08X}")
    if len(hits) > args.limit:
        print(f"  ... {len(hits)-args.limit} more")
    json.dump([start + h for h in hits], open(snap_path(args.name) + f".{kind}.{args.value}.json", "w"))


def _diff_bytes(a: bytes, b: bytes):
    if len(a) != len(b):
        raise SystemExit("snapshot sizes differ")
    # fast path: find first mismatch, then compare in blocks
    if a == b:
        return []
    n = len(a)
    out = []
    step = 4096
    for base in range(0, n, step):
        blk_a = a[base : base + step]
        blk_b = b[base : base + step]
        if blk_a == blk_b:
            continue
        for i in range(len(blk_a)):
            if blk_a[i] != blk_b[i]:
                out.append(base + i)
    return out


def _runs(offsets, gap=8):
    runs = []
    for o in offsets:
        if runs and o - runs[-1][1] <= gap:
            runs[-1][1] = o
        else:
            runs.append([o, o])
    return runs


def cmd_changes(args, _handle):
    a, start = load_snap(args.a)
    b, start_b = load_snap(args.b)
    if start != start_b:
        raise SystemExit("snapshots have different start addresses")
    ch = _diff_bytes(a, b)
    print(f"{len(ch)} byte(s) differ")
    for lo, hi in _runs(ch, args.gap):
        print(f"  0x{start+lo:08X} .. 0x{start+hi:08X}  ({hi-lo+1} B)")


def cmd_tri(args, _handle):
    a, start = load_snap(args.a)
    b, start_b = load_snap(args.b)
    c, start_c = load_snap(args.c)
    if not (start == start_b == start_c):
        raise SystemExit("snapshots have different start addresses")
    if not (len(a) == len(b) == len(c)):
        raise SystemExit("snapshot sizes differ")
    hits = [i for i in range(len(a)) if a[i] == c[i] and a[i] != b[i]]
    print(f"{len(hits)} byte(s) match A==C != B")
    for lo, hi in _runs(hits, args.gap):
        print(f"  0x{start+lo:08X} .. 0x{start+hi:08X}  ({hi-lo+1} B)")


def cmd_tri32(args, _handle):
    """u32-level tri: report offsets where A == C but B differs, with values."""
    a, start = load_snap(args.a)
    b, _ = load_snap(args.b)
    c, _ = load_snap(args.c)
    width = 2 if args.u16 else 4
    fmt = "<H" if args.u16 else "<I"
    n = min(len(a), len(b), len(c)) - width
    hits = []
    for i in range(0, n, width):
        va = struct.unpack_from(fmt, a, i)[0]
        vb = struct.unpack_from(fmt, b, i)[0]
        vc = struct.unpack_from(fmt, c, i)[0]
        if va == vc and va != vb:
            hits.append((i, va, vb, vc))
    print(f"{len(hits)} aligned u{width*8} value(s) with A==C != B")
    for i, va, vb, vc in hits[: args.limit]:
        print(f"  0x{start+i:08X}  {va:>10} -> {vb:>10} -> {vc:>10}")
    if len(hits) > args.limit:
        print(f"  ... {len(hits)-args.limit} more")


def cmd_stable(args, _handle):
    a, start = load_snap(args.a)
    b, _ = load_snap(args.b)
    same = [i for i in range(len(a)) if a[i] == b[i]]
    print(f"{len(same)} byte(s) identical ({100.0*len(same)/len(a):.1f}%)")


def cmd_list(args, _handle):
    os.makedirs(SNAPDIR, exist_ok=True)
    for f in sorted(os.listdir(SNAPDIR)):
        if f.endswith(".bin"):
            p = os.path.join(SNAPDIR, f)
            print(f"{f[:-4]:<24} {os.path.getsize(p):>10} B")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snap")
    s.add_argument("name")
    s.add_argument("--start", default=hex(DEFAULT_START))
    s.add_argument("--size", default=hex(DEFAULT_SIZE))

    s = sub.add_parser("burst")
    s.add_argument("name")
    s.add_argument("--count", type=int, default=10)
    s.add_argument("--interval", default="0")
    s.add_argument("--start", default=hex(DEFAULT_START))
    s.add_argument("--size", default=hex(DEFAULT_SIZE))

    s = sub.add_parser("movie")
    s.add_argument("name")
    s.add_argument("--count", type=int, default=120)
    s.add_argument("--start", default="0x08C06000")
    s.add_argument("--size", default="0x6000")

    s = sub.add_parser("series")
    s.add_argument("name")
    s.add_argument("addr")
    s.add_argument("--type", default="u32", choices=["u8", "u16", "u32", "f32"])

    s = sub.add_parser("filter")
    s.add_argument("name")
    s.add_argument("--mode", default="pulse", choices=["pulse", "mono"])
    s.add_argument("--u16", action="store_true")
    s.add_argument("--min-span", type=int, default=2)
    s.add_argument("--limit", type=int, default=60)
    s.add_argument("--cols", type=int, default=24)

    s = sub.add_parser("show")
    s.add_argument("name")
    s.add_argument("addr")
    s.add_argument("len")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--hex", action="store_true")
    g.add_argument("--u32", action="store_true")
    g.add_argument("--u16", action="store_true")
    g.add_argument("--float", action="store_true")

    s = sub.add_parser("find")
    s.add_argument("name")
    s.add_argument("kind", choices=["u8", "u16", "u32", "f32"])
    s.add_argument("value")
    s.add_argument("--limit", type=int, default=64)

    s = sub.add_parser("changes")
    s.add_argument("a")
    s.add_argument("b")
    s.add_argument("--gap", type=int, default=8)

    s = sub.add_parser("tri")
    s.add_argument("a")
    s.add_argument("b")
    s.add_argument("c")
    s.add_argument("--gap", type=int, default=8)

    s = sub.add_parser("tri32")
    s.add_argument("a")
    s.add_argument("b")
    s.add_argument("c")
    s.add_argument("--u16", action="store_true")
    s.add_argument("--limit", type=int, default=80)

    s = sub.add_parser("stable")
    s.add_argument("a")
    s.add_argument("b")

    sub.add_parser("list")

    args = ap.parse_args()
    handle = None
    if args.cmd in ("snap", "burst", "movie"):
        pid = args.pid or psp_mem.find_pid()
        handle = psp_mem.open_process(pid)
        print(f"pid={pid}", file=sys.stderr)

    {
        "snap": cmd_snap,
        "burst": cmd_burst,
        "movie": cmd_movie,
        "series": cmd_series,
        "filter": cmd_filter,
        "show": cmd_show,
        "find": cmd_find,
        "changes": cmd_changes,
        "tri": cmd_tri,
        "tri32": cmd_tri32,
        "stable": cmd_stable,
        "list": cmd_list,
    }[args.cmd](args, handle)


if __name__ == "__main__":
    main()
