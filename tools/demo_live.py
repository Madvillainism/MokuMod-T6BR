#!/usr/bin/env python3
"""Demos de entrada en vivo por el bus de PPSSPP (sin tocar el teclado).

    python tools/demo_live.py moves     # 1, 3, 4, 1+2 y 3+4 con pausas
    python tools/demo_live.py punish    # arranca el drill, bloquea y castiga

Necesita el bus (`python tools/pad.py --daemon`) y PPSSPP con Tekken 6 en
Práctica.  Sirve para validar el HUD cuando no se puede usar el mando: los
golpes se inyectan por el WebSocket del debugger y el HUD los mide igual que
si los jugara una persona.

Mapeo PSP observado: square=1, triangle=2, cross=3, circle=4.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hud  # noqa: E402
from pad import Pad  # noqa: E402

# (notación, botones, espera tras el golpe)
MOVES = [
    ("1", ["square"], 1.4),
    ("3", ["cross"], 1.4),
    ("4", ["circle"], 1.4),
    ("1+2", ["square", "triangle"], 1.4),
    ("3+4", ["circle", "cross"], 1.8),
]


def send(pad: Pad, buttons: dict) -> None:
    """Manda un estado de botones sin esperar el ticket (baja latencia)."""
    pad.sock.sendall(
        (json.dumps({"cmd": "buttons", "buttons": buttons}) + "\n").encode()
    )


def tap(pad: Pad, buttons: list[str], frames: int = 2) -> None:
    send(pad, {b: True for b in buttons})
    time.sleep(frames / 60.0)
    send(pad, {b: False for b in buttons})


def demo_moves(pad: Pad) -> None:
    for label, buttons, wait in MOVES:
        tap(pad, buttons)
        print(f"pressed {label}", flush=True)
        time.sleep(wait)


def drill_running(reader: hud.Reader, samples: int = 40) -> bool:
    for _ in range(samples):
        s = reader.sample()
        if s and s["mid2"]:
            return True
        time.sleep(0.02)
    return False


def demo_punish(pad: Pad) -> None:
    """Bloquea el ataque del dummy y castiga en cuanto P1 queda libre."""
    reader = hud.Reader()
    if drill_running(reader):
        print("drill already running", flush=True)
    else:
        tap(pad, ["select", "rtrigger"], frames=6)
        print("drill started (SELECT+R)", flush=True)

    t0 = time.time()
    prev_tail = None
    jabs = 0
    while time.time() - t0 < 12 and jabs < 3:
        s = reader.sample()
        if s is None:
            continue
        # tail1 pasa a 1 en el frame exacto en que P1 puede volver a actuar
        if s["tail1"] != prev_tail:
            prev_tail = s["tail1"]
            if s["tail1"] == 1 and s["mid2"]:
                tap(pad, ["square"])
                jabs += 1
                print(f"{time.time() - t0:5.2f} jab -> ventana de castigo", flush=True)
        time.sleep(1 / 240)
    print("done", jabs, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=("moves", "punish"))
    ap.add_argument("--walk", type=int, default=0,
                    help="frames de 'up' (acercarse) antes de los golpes")
    a = ap.parse_args()

    pad = Pad()
    if not pad.wait_connected(5):
        raise SystemExit("telemetry bus not connected (python tools/pad.py --daemon)")
    try:
        if a.mode == "moves":
            if a.walk:
                tap(pad, ["up"], frames=a.walk)
                time.sleep(0.4)
            demo_moves(pad)
        else:
            demo_punish(pad)
    finally:
        pad.close()


if __name__ == "__main__":
    main()
