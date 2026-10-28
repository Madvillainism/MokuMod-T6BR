#!/usr/bin/env python3
"""Estados de reacción del juego (guardia, idle, ataque) en data/states.json.

`4178` es el blockstun de pie y `12329` el de agachado (los golpes bajos solo
se bloquean agachado).  Los dos están verificados en vivo.  Si un personaje
usa variantes distintas, el HUD las aprende con `--learn` cuando ve una
reacción de guardia desconocida y las guarda aquí; así el motor funciona con
cualquier personaje sin tocar código.

    {"block_states": [4178],
     "crouch_block_states": [12329],
     "idle_states": [6466, 6482, 14633],
     "crouch_idle_states": [14633],
     "attack_states": [2114],
     "crouch_attack_states": [10273]}
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_DATA_DIR = os.path.join(ROOT, "data")

DEFAULTS = {
    "block_states": [4178],
    "crouch_block_states": [12329],
    "idle_states": [6466, 6482, 14633],
    "crouch_idle_states": [14633],
    "attack_states": [2114],
    "crouch_attack_states": [10273],
}

# Claves que son conjuntos de estados (el resto del JSON se ignora).
STATE_KEYS = tuple(DEFAULTS)


def states_path(data_dir: str | None = None) -> str:
    base = os.path.abspath(data_dir or DEFAULT_DATA_DIR)
    return os.path.join(base, "states.json")


def load_states(data_dir: str | None = None) -> dict[str, set[int]]:
    """Devuelve los conjuntos de estados (con los valores por defecto)."""
    payload = {key: list(values) for key, values in DEFAULTS.items()}
    path = states_path(data_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for key in STATE_KEYS:
            if isinstance(data.get(key), list):
                payload[key] = data[key]
    except (OSError, ValueError):
        pass
    return {key: {int(v) for v in values} for key, values in payload.items()}


def save_states(states: dict[str, set[int]] | dict[str, list[int]],
                data_dir: str | None = None) -> str:
    path = states_path(data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {key: sorted(int(v) for v in values) for key, values in states.items()}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def add_block_state(state: int, data_dir: str | None = None,
                    crouch: bool = False) -> bool:
    """Añade un estado de guardia nuevo.  False si ya estaba.

    `crouch=True` lo guarda como guardia agachada (la de los golpes bajos).
    """
    states = load_states(data_dir)
    key = "crouch_block_states" if crouch else "block_states"
    if int(state) in states[key]:
        return False
    states[key].add(int(state))
    save_states(states, data_dir)
    return True


def ensure_file(data_dir: str | None = None) -> str:
    """Crea data/states.json con los valores por defecto si no existe."""
    path = states_path(data_dir)
    if not os.path.exists(path):
        save_states(load_states(data_dir), data_dir)
    return path


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--add-block", type=int, default=None)
    ap.add_argument("--add-crouch-block", type=int, default=None)
    args = ap.parse_args()
    if args.add_block is not None:
        print("añadido" if add_block_state(args.add_block, args.data_dir) else "ya estaba")
    if args.add_crouch_block is not None:
        print("añadido" if add_block_state(args.add_crouch_block, args.data_dir,
                                           crouch=True) else "ya estaba")
    print(json.dumps({k: sorted(v) for k, v in load_states(args.data_dir).items()}, indent=2))
