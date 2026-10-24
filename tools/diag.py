#!/usr/bin/env python3
"""Registro en vivo de frames + eventos (herramienta de desarrollo).

Usa el mismo motor que el HUD (`TrackerThread.feed`), así que lo que aparece
aquí es exactamente lo que decide el overlay, y además vuelca cada frame:

    python tools/diag.py 120            # 120 s -> .cache/diag.log
    $env:P1_CHAR="bryan"; $env:P2_CHAR="bruce"; python tools/diag.py 60

Dos tipos de línea:

    f  1234 t= 12.345 P1 c=  12 mid=  7 tail=0 free=1 s= 2114 hp=180 | P2 ...
      >> f  1239 P1 impact 1     i10    - HIT  ~+8  PUNISH  [1230->1239]

La primera es un frame (cuando cambia el contador de P1); la segunda es un
evento con el frame del reloj compartido, el arranque medido, la ventaja, el
tipo, el valor adelantado (`~`) y, en los impactos, el motivo de (no) PUNISH.
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

from hud import TrackerThread, load_notation  # noqa: E402


class NullQueue:
    def put(self, _item):
        pass


class DiagTracker(TrackerThread):
    """TrackerThread que escribe los eventos al log en vez de a la cola."""

    def __init__(self, log, **kw):
        super().__init__(NullQueue(), debug=False, **kw)
        self.log = log

    def emit(self, ev):
        adv = "-" if ev["adv"] is None else f"{ev['adv']:+d}"
        pred = "" if ev.get("predict") is None else f" ~{ev['predict']:+d}"
        tag = ""
        if ev.get("punish"):
            tag = "  PUNISH"
        elif ev["phase"] == "impact" and ev.get("punish_why") not in (None, "ok"):
            tag = f"  ({ev['punish_why']})"
        self.log.write(
            f"  >> f{ev['frame']:6d} {ev['side']} {ev['phase']:6s} "
            f"{ev['label']:5s} i{ev['startup']:<3d}{adv:>4} {ev['kind']:5s}"
            f"{pred}{tag}  [{ev['start']}->{ev['impact']}]\n"
        )


def main() -> None:
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    path = os.path.join(ROOT, ".cache", "diag.log")
    out = open(path, "w", encoding="utf-8")
    tt = DiagTracker(
        out,
        p1_notation=load_notation(os.environ.get("P1_CHAR")),
        p2_notation=load_notation(os.environ.get("P2_CHAR")),
    )

    t0 = time.time()
    frames = 0
    while time.time() - t0 < secs:
        s = tt.rd.sample()
        if s is None:
            time.sleep(0.05)
            continue
        frames += 1
        if s["c1"] != tt.clock_c:
            out.write(
                f"f{tt.clock + 1:6d} t={time.time() - t0:7.3f} "
                f"P1 c={s['c1']:5d} mid={s['mid']:3d} tail={s['tail1']} "
                f"free={s['free1']} s={s['s1']:5d} hp={s['hp1']:3d} | "
                f"P2 c={s['c2']:5d} mid={s['mid2']:3d} tail={s['tail2']} "
                f"free={s['free2']} s={s['s2']:5d} hp={s['hp2']:3d}\n"
            )
        tt.feed(s)
        out.flush()
        time.sleep(0.0005)

    out.write(f"# fin: {frames} frames\n")
    out.close()
    print(f"diag -> {path} ({frames} frames)")


if __name__ == "__main__":
    main()
