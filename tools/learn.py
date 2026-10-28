#!/usr/bin/env python3
"""Modo `--learn` del HUD: enriquece el mapa Move ID -> notación en caliente.

Cuando el HUD ve un ID de movimiento que no está en data/mappings, lo encola y
un hilo pregunta por consola la notación del juego (la que muestra la lista de
comandos de Practice).  Al responder:

    1. añade el ID a data/mappings/<personaje>_map.json,
    2. recompila data/compiled/<personaje>.json con tools/mapper.py,
    3. avisa al HUD para que recargue la tabla (sin reiniciar ni parar PPSSPP).

También aprende **estados de guardia** nuevos (guardia agachada, etc.) hacia
data/states.json.

    python tools/hud.py --p1-char bryan --learn
"""
from __future__ import annotations

import queue
import sys
import threading

import game_states
import mapper


class Learner:
    """Preguntas interactivas sin bloquear el hilo de telemetría del HUD."""

    def __init__(self, characters: dict[str, str], data_dir: str | None = None,
                 lookup=None, on_change=None, ask=None, stream=None):
        # characters: {"P1": "bryan", "P2": "bruce"} (solo los que tengan tabla)
        self.characters = {
            side: name.strip().lower()
            for side, name in (characters or {}).items()
            if name and name.strip()
        }
        self.data_dir = data_dir
        self.lookup = lookup
        self.on_change = on_change
        self.ask = ask or input
        self.stream = stream or sys.stdout
        self.queue: queue.Queue = queue.Queue()
        self.muted = False
        self.asked_moves: set[tuple[str, object]] = set()
        self.asked_states: set[int] = set()
        self.thread = None
        if self.characters:
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    # -- API para el tracker --------------------------------------------
    def offer_move(self, side: str, move_id: int, crouch: bool = False) -> None:
        """Un Move ID desconocido acaba de empezar (frame 0).

        `crouch` marca los movimientos que salen de la agachada: el motor
        reutiliza IDs entre posturas (el 20 es "3+4" de pie y qcb+3 agachado),
        así que un ID que solo se ha visto agachado se guarda como
        `crouch:<id>` y no pisa la entrada de pie.
        """
        if self.muted or not move_id:
            return
        char = self.characters.get(side)
        if not char:
            return
        key_id = f"crouch:{int(move_id)}" if crouch else int(move_id)
        key = (char, key_id)
        if key in self.asked_moves:
            return
        if self.lookup is not None and self.lookup.get_move_info(char, key_id):
            return
        self.asked_moves.add(key)
        self.queue.put(("move", char, key_id))

    def offer_state(self, state: int) -> None:
        """Reacción de guardia con un estado que no está en states.json."""
        if self.muted or state in self.asked_states:
            return
        self.asked_states.add(int(state))
        self.queue.put(("state", None, int(state)))

    def mute(self) -> None:
        self.muted = True
        try:
            while True:
                self.queue.get_nowait()
        except queue.Empty:
            pass

    # -- hilo ------------------------------------------------------------
    def _say(self, text: str) -> None:
        print(text, file=self.stream, flush=True)

    def _loop(self) -> None:
        while True:
            kind, char, value = self.queue.get()
            try:
                if kind == "move":
                    self._ask_move(char, value)
                else:
                    self._ask_state(value)
            except (EOFError, OSError, KeyboardInterrupt):
                self.muted = True
                return
            except Exception as exc:  # nunca tumbar el HUD por una pregunta
                self._say(f"[learn] error: {exc}")

    def _ask_move(self, char: str, move_id: int) -> None:
        self._say(
            f"\n[learn] {char}: ID #{move_id} sin mapear. "
            f"Notación del juego (Enter = omitir, m = silenciar):"
        )
        answer = self.ask("> ").strip()
        if answer.lower() == "m":
            self.mute()
            return
        if not answer:
            return
        mapper.add_mapping(char, move_id, answer, self.data_dir)
        if mapper.compile_character_db(char, self.data_dir, quiet=True):
            self._say(f"[learn] #{move_id} -> {answer} guardado y compilado")
        else:
            self._say(f"[learn] #{move_id} -> {answer} guardado (sin datos raw todavía)")
        if self.on_change:
            self.on_change("move", char)

    def _ask_state(self, state: int) -> None:
        self._say(
            f"\n[learn] estado {state} desconocido tras una reacción de guardia. "
            f"¿Añadirlo como guardia/bloqueo? [s/N/m]:"
        )
        answer = self.ask("> ").strip().lower()
        if answer == "m":
            self.mute()
            return
        if answer in ("s", "si", "sí", "y", "yes"):
            if game_states.add_block_state(state, self.data_dir):
                self._say(f"[learn] estado {state} añadido a la guardia")
                if self.on_change:
                    self.on_change("state", str(state))
