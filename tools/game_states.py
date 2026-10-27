#!/usr/bin/env python3
"""Estados de reacción del juego (guardia, idle, ataque) en data/states.json.

El estado `4178` es el blockstun de pie verificado.  La guardia agachada usa
otros valores: en vez de fijarlos a ciegas, el HUD los aprende con `--learn`
cuando ve una reacción de guardia desconocida y los guarda aquí.  Así el motor
funciona con cualquier personaje y cualquier variante de guardia sin tocar
código.

    {"block_states": [4178, 4182],
     "idle_states": [6466, 6482, 14633],
     "attack_states": [2114]}
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_DATA_DIR = os.path.join(ROOT, "data")

DEFAULTS = {
    "block_states": [4178],
    "idle_states": [6466, 6482, 14633],
    "attack_states": [2114],
}


def states_path(data_dir: str | None = None) -> str:
    base = os.path.abspath(data_dir or DEFAULT_DATA_DIR)
    return os.path.join(base, "states.json")


def load_states(data_dir: str | None = None) -> dict[str, set[int]]:
    """Devuelve los tres conjuntos de estados (con los valores por defecto)."""
    payload = dict(DEFAULTS)
    path = states_path(data_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for key in DEFAULTS:
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


def add_block_state(state: int, data_dir: str | None = None) -> bool:
    """Añade un estado de guardia nuevo.  False si ya estaba."""
    states = load_states(data_dir)
    if int(state) in states["block_states"]:
        return False
    states["block_states"].add(int(state))
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
    args = ap.parse_args()
    if args.add_block is not None:
        print("añadido" if add_block_state(args.add_block, args.data_dir) else "ya estaba")
    print(json.dumps({k: sorted(v) for k, v in load_states(args.data_dir).items()}, indent=2))
