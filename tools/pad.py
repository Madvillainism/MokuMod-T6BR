#!/usr/bin/env python3
"""Client for the persistent PPSSPP telemetry bus (tools/ppsspp_ws.py).

The bus owns the single long-lived WebSocket to PPSSPP; this module talks
line-delimited JSON to it on 127.0.0.1:8098 and spawns it on demand.  It is
pure Python now: **Node.js is no longer needed**.

    with Pad() as pad:
        t = pad.press("down", 120)      # hold ~2 s
        pad.wait_ticket(t)              # optional: block until released
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAEMON = os.path.join(ROOT, "tools", "ppsspp_ws.py")
HOST = "127.0.0.1"
PORT = 8098


def _daemon_running(timeout: float = 0.4) -> bool:
    try:
        s = socket.create_connection((HOST, PORT), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def ensure_daemon(start_timeout: float = 8.0, verbose: bool = False) -> bool:
    if _daemon_running():
        return False
    logdir = os.path.join(ROOT, ".cache")
    os.makedirs(logdir, exist_ok=True)
    out = open(os.path.join(logdir, "padd.log"), "a")
    subprocess.Popen(
        [sys.executable, DAEMON, "--serve", "--verbose"],
        cwd=ROOT,
        stdout=out,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    end = time.time() + start_timeout
    while time.time() < end:
        if _daemon_running():
            return True
        time.sleep(0.1)
    raise SystemExit("telemetry bus did not start (see .cache/padd.log)")


class Pad:
    def __init__(self, verbose: bool = False, timeout: float = 10.0):
        ensure_daemon(verbose=verbose)
        self.verbose = verbose
        self.timeout = timeout
        self.sock = socket.create_connection((HOST, PORT), timeout=5)
        self.sock.settimeout(None)
        self.buf = b""
        self.events: list[dict] = []
        self.tickets: dict[int, dict] = {}
        self.connected = False
        self._lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while True:
            try:
                chunk = self.sock.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            self.buf += chunk
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if self.verbose:
                    print("[pad]", line.decode("utf-8", "replace"))
                with self._lock:
                    self.events.append(msg)
                    if "connected" in msg:
                        self.connected = bool(msg["connected"])
                    if "rx" in msg:
                        ev = msg["rx"]
                        if isinstance(ev, dict) and ev.get("ticket") is not None:
                            self.tickets[ev["ticket"]] = ev

    def _request(self, obj: dict, settle: float = 0.0) -> dict:
        self.sock.sendall((json.dumps(obj) + "\n").encode())
        end = time.time() + max(settle, 0.001)
        while time.time() < end:
            with self._lock:
                for e in self.events:
                    if "ok" in e:
                        return e
            time.sleep(0.01)
        # take the latest reply
        with self._lock:
            replies = [e for e in self.events if "ok" in e]
        return replies[-1] if replies else {}

    def wait_connected(self, timeout: float | None = None) -> bool:
        # the daemon only broadcasts `connected` when the state changes, so ask
        # for the current status first (it replies with a `connected` field)
        try:
            self.sock.sendall(b'{"cmd":"status"}\n')
        except OSError:
            return False
        end = time.time() + (timeout or self.timeout)
        while time.time() < end:
            if self.connected:
                return True
            time.sleep(0.05)
        return False

    def press(self, button: str, frames: int = 2) -> int:
        r = self._request({"cmd": "press", "button": button, "frames": frames}, 0.3)
        return int(r.get("ticket", -1))

    def wait_ticket(self, ticket: int, timeout: float | None = None) -> bool:
        end = time.time() + (timeout or self.timeout)
        while time.time() < end:
            with self._lock:
                if ticket in self.tickets:
                    return True
            time.sleep(0.02)
        return False

    def raw(self, obj: dict, wait: float = 0.25) -> dict | None:
        r = self._request({"cmd": "raw", **obj}, wait)
        t = r.get("ticket")
        if t is None:
            return None
        end = time.time() + self.timeout
        while time.time() < end:
            with self._lock:
                if t in self.tickets:
                    return self.tickets[t]
            time.sleep(0.02)
        return None

    def clear(self):
        with self._lock:
            self.events.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("button", nargs="?")
    ap.add_argument("--frames", type=int, default=2)
    ap.add_argument("--combo", help="comma/simultaneous press, e.g. square+triangle")
    ap.add_argument("--json")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--daemon", action="store_true", help="just start the telemetry bus")
    a = ap.parse_args()
    ensure_daemon(verbose=a.verbose)
    if a.daemon:
        print("telemetry bus ready on 127.0.0.1:8098 (Python, no Node)")
        raise SystemExit(0)
    with Pad(verbose=a.verbose) as p:
        if not p.wait_connected(3):
            print("warning: bus has no PPSSPP connection")
        if a.combo:
            parts = a.combo.split("+")
            p.raw({"event": "input.buttons.send", "buttons": {b: True for b in parts}}, 0.05)
            time.sleep(0.035)
            p.raw({"event": "input.buttons.send", "buttons": {b: False for b in parts}}, 0.05)
            print("combo", a.combo)
        elif a.json:
            print(p.raw(json.loads(a.json)))
        elif a.button:
            t = p.press(a.button, a.frames)
            p.wait_ticket(t)
            print("released", t)
        else:
            print(p.raw({"event": "game.status"}))
