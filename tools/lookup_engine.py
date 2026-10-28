#!/usr/bin/env python3
"""FrameTableLookup: base de datos local de frame data por personaje.

El motor híbrido del HUD consulta esta tabla en el mismo frame en que cambia
el Move ID (`+0x144`) para enseñar el dato **teórico** al instante, y la
telemetría de memoria lo **confirma** ~30 frames después.

Formatos aceptados (se prueban en este orden):

    data/compiled/<personaje>.json   {"character": "...", "moves": {"7": {...}}}
    data/<personaje>.json            {"7": {...}}   (formato del roadmap-2)
    data/raw/<personaje>_raw.json    {"character": "...", "moves": {"1": {...}}}

Los valores de frame se normalizan a entero cuando son números (`i10`, `+1`,
`-7`, `10`) y se dejan como texto cuando son cualitativos (`KND`, `CH`...).
"""
from __future__ import annotations

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_DATA_DIR = os.path.join(ROOT, "data")

FRAME_KEYS = ("startup", "on_block", "on_hit", "on_ch", "damage")


def slugify(name: str) -> str:
    """Nombre de personaje -> nombre de archivo estable.

    `armor king` -> `armor_king`, `roger jr.` -> `roger_jr`, `jack-6` -> `jack-6`.
    Se usa en `data/raw`, `data/mappings` y `data/compiled` para que los nombres
    con espacios o puntos no dependan del sistema de archivos.
    """
    text = (name or "").strip().lower().replace("'", "").replace("\u2019", "")
    text = re.sub(r"[\s.]+", "_", text)
    text = re.sub(r"[^a-z0-9_+-]", "", text)
    return text.strip("_") or "unknown"


def to_frame(value):
    """`i10` / `+1` / `-7` / `10` -> entero; `KND` -> texto; vacío -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text or text in ("-", "--", "?"):
        return None
    match = re.fullmatch(r"[+-]?\d+", text.replace("i", "").replace("I", "").strip())
    if match:
        return int(match.group(0))
    return text


def normalize_move(move: dict) -> dict:
    out = dict(move)
    for key in FRAME_KEYS:
        if key in out:
            out[key] = to_frame(out[key])
    if "notation" not in out and "command" in out:
        out["notation"] = out["command"]
    return out


class FrameTableLookup:
    """Carga perezosa y búsqueda O(1) por Move ID de RAM."""

    def __init__(self, data_dir: str | None = None):
        self.data_dir = os.path.abspath(data_dir or DEFAULT_DATA_DIR)
        self.tables: dict[str, dict[str, dict]] = {}
        self.sources: dict[str, str] = {}

    # -- rutas ----------------------------------------------------------
    def _candidates(self, char: str):
        slug = slugify(char)
        return [
            os.path.join(self.data_dir, "compiled", f"{slug}.json"),
            os.path.join(self.data_dir, f"{slug}.json"),
            os.path.join(self.data_dir, "raw", f"{slug}_raw.json"),
        ]

    def path_for(self, char: str) -> str | None:
        for path in self._candidates(char):
            if os.path.exists(path):
                return path
        return None

    # -- carga ----------------------------------------------------------
    def load_character(self, char: str) -> bool:
        if not char:
            return False
        char = char.strip().lower()
        path = self.path_for(char)
        if not path:
            self.tables.pop(char, None)
            return False
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"[Lookup] Error cargando {path}: {exc}")
            return False

        moves = payload.get("moves", payload) if isinstance(payload, dict) else {}
        if not isinstance(moves, dict):
            return False
        table = {}
        for move_id, info in moves.items():
            if not isinstance(info, dict):
                continue
            table[str(move_id).lstrip("#")] = normalize_move(info)
        self.tables[char] = table
        self.sources[char] = path
        return True

    def reload(self, char: str) -> bool:
        self.tables.pop(char.strip().lower(), None)
        return self.load_character(char)

    def is_id_indexed(self, char: str | None) -> bool:
        """True si la tabla cargada va por Move ID (compiled), no por notación (raw).

        `data/raw/` está indexado por notación (`"1"`, `"3+4"`).  Usarlo como si
        fueran Move IDs haría que el ID 1 mostrara la frame data del jab: una
        predicción falsa.  Solo `compiled/` (o `data/<pj>.json`) vale para IDs.
        """
        if not char:
            return False
        source = self.sources.get(char.strip().lower(), "")
        return bool(source) and not source.endswith("_raw.json")

    # -- consulta -------------------------------------------------------
    def get_move_info(self, char: str | None, move_id) -> dict | None:
        """Dato teórico del movimiento o None si el personaje/ID no está mapeado."""
        if not char:
            return None
        char = char.strip().lower()
        if char not in self.tables:
            self.load_character(char)
        if not self.is_id_indexed(char):
            return None
        key = str(move_id).lstrip("#").strip()
        if key.isdigit():
            key = str(int(key))
        return self.tables.get(char, {}).get(key)
    def has_character(self, char: str | None) -> bool:
        return bool(char) and char.strip().lower() in self.tables

    def characters(self) -> list[str]:
        return sorted(self.tables)

    def advantage(self, info: dict | None, kind: str):
        """Ventaja teórica según el tipo de contacto (BLOCK / HIT)."""
        if not info:
            return None
        key = "on_block" if kind == "BLOCK" else "on_hit"
        return to_frame(info.get(key))


def main() -> None:
    """Consulta rápida desde consola:  python tools/lookup_engine.py bryan 7"""
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("character")
    ap.add_argument("move_id", nargs="?", type=int)
    ap.add_argument("--data-dir", default=None)
    args = ap.parse_args()

    lookup = FrameTableLookup(args.data_dir)
    if not lookup.load_character(args.character):
        raise SystemExit(f"sin tabla para {args.character!r} (mira data/compiled/)")
    print(f"fuente: {lookup.sources[args.character.lower()]}")
    if args.move_id is None:
        def _order(kv):
            key = kv[0]
            return (0, int(key), "") if key.isdigit() else (1, 0, key)

        for move_id, info in sorted(lookup.tables[args.character.lower()].items(),
                                    key=_order):
            print(f"{move_id:>4}  {info.get('notation', '?'):<12} "
                  f"i{info.get('startup')}  b{info.get('on_block')}  h{info.get('on_hit')}")
    else:
        print(lookup.get_move_info(args.character, args.move_id))


if __name__ == "__main__":
    main()
