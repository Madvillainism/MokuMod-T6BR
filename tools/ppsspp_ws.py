#!/usr/bin/env python3
"""Cliente WebSocket nativo del depurador de PPSSPP (sin Node.js).

PPSSPP expone su depurador en ws://127.0.0.1:8099/debugger con el
subprotocolo `debugger.ppsspp.org`.  Este módulo implementa el handshake y
las tramas RFC 6455 con la librería estándar, y ofrece dos usos:

1. **Bus persistente** (recomendado, igual que el antiguo daemon de Node):

       python tools/ppsspp_ws.py --serve

   Mantiene *una sola* conexión WebSocket viva con PPSSPP y la reexpone como
   JSON por líneas en 127.0.0.1:8098.  Así los scripts pueden entrar y salir
   sin abrir/cerrar conexiones contra el emulador (PPSSPP 1.19.x se cae si
   eso ocurre durante un cambio de estado del juego).

       cliente -> {"cmd":"press","button":"cross","frames":3}
       cliente -> {"cmd":"raw","event":"game.status"}
       cliente -> {"cmd":"ping"} / {"cmd":"status"} / {"cmd":"buttons",...}
       servidor -> {"ok":true,"ticket":5}
       servidor -> {"rx":<evento de PPSSPP>}
       servidor -> {"connected":true|false}

2. **Cliente directo** para pruebas puntuales:

       python tools/ppsspp_ws.py --press cross --frames 2
       python tools/ppsspp_ws.py --raw '{"event":"game.status"}'
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time

HOST = "127.0.0.1"
WS_PORT = 8099
TCP_PORT = 8098
WS_PATH = "/debugger"
WS_PROTOCOL = "debugger.ppsspp.org"
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ------------------------------------------------------------------ tramas --
def _frame(opcode: int, payload: bytes) -> bytes:
    """Trama de cliente (siempre enmascarada, FIN=1)."""
    length = len(payload)
    mask = os.urandom(4)
    if length < 126:
        header = bytes([0x80 | opcode, 0x80 | length])
    elif length < 65536:
        header = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", length)
    else:
        header = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack(">Q", length)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return header + mask + masked


class PPSSPPDebugger:
    """Conexión persistente con reconexión automática y tickets."""

    def __init__(self, host: str = HOST, port: int = WS_PORT,
                 verbose: bool = False, reconnect: bool = True):
        self.host = host
        self.port = port
        self.verbose = verbose
        self.reconnect = reconnect
        self.sock: socket.socket | None = None
        self.connected = False
        self.closed = False
        self._buf = b""
        self._ticket = 1
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self.tickets: dict[int, dict] = {}
        self.listeners = []          # callbacks(dict)
        self._thread: threading.Thread | None = None

    # -- conexión -------------------------------------------------------
    def _log(self, *args):
        if self.verbose:
            print(time.strftime("%H:%M:%S"), *args, flush=True)

    def connect(self, timeout: float = 5.0) -> bool:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=timeout)
        except OSError as exc:
            self._log("ws connect failed:", exc)
            return False
        sock.settimeout(None)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {WS_PATH} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Protocol: {WS_PROTOCOL}\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("latin1"))
        sock.settimeout(timeout)

        header = b""
        deadline = time.time() + timeout
        while b"\r\n\r\n" not in header:
            if time.time() > deadline:
                sock.close()
                return False
            try:
                chunk = sock.recv(4096)
            except OSError:
                sock.close()
                return False
            if not chunk:
                sock.close()
                return False
            header += chunk
        sock.settimeout(None)

        head, rest = header.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0].decode("latin1", "replace")
        if "101" not in status:
            self._log("handshake rejected:", status)
            sock.close()
            return False
        expected = base64.b64encode(
            hashlib.sha1((key + GUID).encode()).digest()
        ).decode()
        if expected.lower() not in head.decode("latin1", "replace").lower():
            self._log("handshake: Sec-WebSocket-Accept ausente (sigo igualmente)")

        self.sock = sock
        self._buf = rest
        self.connected = True
        self._log("debugger conectado")
        self._emit({"connected": True})
        if self._buf:
            self._parse()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        return True

    def wait_connected(self, timeout: float = 10.0) -> bool:
        if self.connected:
            return True
        if self.sock is None and not self.connect():
            return False
        end = time.time() + timeout
        while time.time() < end:
            if self.connected:
                return True
            time.sleep(0.05)
        return False

    # -- recepción ------------------------------------------------------
    def _emit(self, msg: dict) -> None:
        for callback in list(self.listeners):
            try:
                callback(msg)
            except Exception as exc:  # nunca romper el hilo lector
                self._log("listener error:", exc)

    def _read_loop(self) -> None:
        while not self.closed:
            try:
                chunk = self.sock.recv(65536) if self.sock else b""
            except OSError:
                chunk = b""
            if not chunk:
                break
            self._buf += chunk
            self._parse()

        self.connected = False
        self._emit({"connected": False})
        self._log("ws cerrado")
        if not self.closed and self.reconnect:
            delay = 1.0
            while not self.closed:
                time.sleep(delay)
                delay = min(delay * 2, 15.0)
                if self.connect():
                    return

    def _parse(self) -> None:
        while True:
            if len(self._buf) < 2:
                return
            b0, b1 = self._buf[0], self._buf[1]
            opcode = b0 & 0x0F
            fin = bool(b0 & 0x80)
            masked = bool(b1 & 0x80)
            length = b1 & 0x7F
            offset = 2
            if length == 126:
                if len(self._buf) < 4:
                    return
                length = struct.unpack(">H", self._buf[2:4])[0]
                offset = 4
            elif length == 127:
                if len(self._buf) < 10:
                    return
                length = struct.unpack(">Q", self._buf[2:10])[0]
                offset = 10
            mask = None
            if masked:
                if len(self._buf) < offset + 4:
                    return
                mask = self._buf[offset:offset + 4]
                offset += 4
            if len(self._buf) < offset + length:
                return
            payload = self._buf[offset:offset + length]
            self._buf = self._buf[offset + length:]
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

            if opcode in (0x1, 0x2):
                self._on_message(payload.decode("utf-8", "replace"), fin)
            elif opcode == 0x8:
                self._log("close frame del servidor")
                try:
                    self.sock.close()
                except OSError:
                    pass
                return
            elif opcode == 0x9:  # ping -> pong
                self._send_raw(0xA, payload)

    def _on_message(self, text: str, fin: bool) -> None:
        # PPSSPP manda mensajes pequeños y completos; acumulamos por si acaso.
        if not fin:
            self._frag = getattr(self, "_frag", "") + text
            return
        if getattr(self, "_frag", ""):
            text = self._frag + text
            self._frag = ""
        self._log("RX", text)
        try:
            obj = json.loads(text)
        except ValueError:
            obj = {"raw": text}
        if isinstance(obj, dict) and obj.get("ticket") is not None:
            with self._lock:
                self.tickets[obj["ticket"]] = obj
        self._emit({"rx": obj})

    # -- envío ----------------------------------------------------------
    def _send_raw(self, opcode: int, payload: bytes) -> None:
        with self._send_lock:
            if not self.sock:
                raise ConnectionError("debugger no conectado")
            self.sock.sendall(_frame(opcode, payload))

    def send(self, obj: dict) -> int:
        """Envía un evento y devuelve el ticket asignado."""
        if not self.connected:
            raise ConnectionError("debugger no conectado")
        with self._lock:
            ticket = obj.get("ticket") or self._ticket
            self._ticket = max(self._ticket + 1, ticket + 1)
        msg = dict(obj)
        msg["ticket"] = ticket
        text = json.dumps(msg)
        self._log("TX", text)
        self._send_raw(0x1, text.encode("utf-8"))
        return ticket

    def wait_ticket(self, ticket: int, timeout: float = 10.0) -> dict | None:
        end = time.time() + timeout
        while time.time() < end:
            with self._lock:
                if ticket in self.tickets:
                    return self.tickets[ticket]
            time.sleep(0.01)
        return None

    def request(self, obj: dict, timeout: float = 2.0) -> dict | None:
        return self.wait_ticket(self.send(obj), timeout)

    def press(self, button: str, frames: int = 2) -> int:
        return self.send({
            "event": "input.buttons.press",
            "button": button,
            "duration": int(frames),
        })

    def close(self) -> None:
        self.closed = True
        self.connected = False
        if self.sock:
            try:
                self._send_raw(0x8, struct.pack(">H", 1000))
            except (OSError, ConnectionError):
                pass
            try:
                self.sock.close()
            except OSError:
                pass


# ------------------------------------------------------- bus TCP persistente --
class Bus:
    """Reexpone la conexión WebSocket como JSON por líneas (puerto 8098)."""

    def __init__(self, debugger: PPSSPPDebugger, host: str = HOST, port: int = TCP_PORT,
                 verbose: bool = False):
        self.dbg = debugger
        self.host = host
        self.port = port
        self.verbose = verbose
        self.clients: set[socket.socket] = set()
        self.lock = threading.Lock()
        debugger.listeners.append(self._forward)

    def _forward(self, msg: dict) -> None:
        self.broadcast(msg)

    def broadcast(self, msg: dict) -> None:
        line = (json.dumps(msg) + "\n").encode()
        with self.lock:
            dead = []
            for client in self.clients:
                try:
                    client.sendall(line)
                except OSError:
                    dead.append(client)
            for client in dead:
                self.clients.discard(client)

    def _handle(self, client: socket.socket, line: str) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            client.sendall(b'{"ok":false,"error":"bad json"}\n')
            return
        cmd = msg.get("cmd")
        try:
            if cmd == "ping":
                reply = {"ok": True, "pong": int(time.time() * 1000)}
            elif cmd == "status":
                reply = {"connected": self.dbg.connected}
            elif cmd == "press":
                reply = {"ok": True, "ticket": self.dbg.press(
                    msg.get("button", "cross"), msg.get("frames", 2))}
            elif cmd == "buttons":
                reply = {"ok": True, "ticket": self.dbg.send({
                    "event": "input.buttons.send",
                    "buttons": msg.get("buttons") or {},
                })}
            elif cmd == "raw":
                payload = {k: v for k, v in msg.items() if k != "cmd"}
                reply = {"ok": True, "ticket": self.dbg.send(payload)}
            else:
                reply = {"ok": False, "error": "unknown cmd"}
        except (ConnectionError, OSError) as exc:
            reply = {"ok": False, "error": str(exc)}
        client.sendall((json.dumps(reply) + "\n").encode())

    def _client_loop(self, client: socket.socket) -> None:
        buf = ""
        with self.lock:
            self.clients.add(client)
        try:
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", "replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if line.strip():
                        self._handle(client, line.strip())
        except OSError:
            pass
        finally:
            with self.lock:
                self.clients.discard(client)
            try:
                client.close()
            except OSError:
                pass

    def serve_forever(self) -> None:
        server = socket.create_server((self.host, self.port), reuse_port=False)
        server.listen(8)
        print(f"[ws] bus en {self.host}:{self.port}", flush=True)
        while True:
            client, _ = server.accept()
            threading.Thread(target=self._client_loop, args=(client,), daemon=True).start()


def serve(ws_port: int = WS_PORT, tcp_port: int = TCP_PORT,
          verbose: bool = False) -> None:
    debugger = PPSSPPDebugger(port=ws_port, verbose=verbose, reconnect=True)
    bus = Bus(debugger, port=tcp_port, verbose=verbose)

    def connect_loop() -> None:
        # El bus escucha desde el primer segundo; la conexión con PPSSPP se
        # reintenta hasta que el emulador esté abierto (el HUD puede arrancar
        # antes que el juego).
        while not debugger.closed and not debugger.connected:
            if debugger.connect():
                return
            time.sleep(2.0)

    threading.Thread(target=connect_loop, daemon=True).start()
    try:
        bus.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        debugger.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="bus persistente en 8098")
    ap.add_argument("--port", type=int, default=WS_PORT, help="puerto del depurador")
    ap.add_argument("--tcp", type=int, default=TCP_PORT, help="puerto del bus")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--press", default=None, help="pulsa un botón y sale")
    ap.add_argument("--frames", type=int, default=2)
    ap.add_argument("--raw", default=None, help="envía un evento JSON y sale")
    args = ap.parse_args()

    if args.serve:
        serve(args.port, args.tcp, args.verbose)
        return

    dbg = PPSSPPDebugger(port=args.port, verbose=args.verbose, reconnect=False)
    if not dbg.connect():
        raise SystemExit("no se pudo conectar al depurador de PPSSPP (¿está activo?)")
    if args.press:
        ticket = dbg.press(args.press, args.frames)
        print("ticket", ticket, "->", dbg.wait_ticket(ticket, 3.0))
    elif args.raw:
        print(dbg.request(json.loads(args.raw)))
    else:
        print(dbg.request({"event": "game.status"}, 2.0))
    dbg.close()


if __name__ == "__main__":
    main()
