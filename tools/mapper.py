#!/usr/bin/env python3
"""Compila la base final del HUD:  Move ID de RAM  ->  frame data.

Entradas:
    data/raw/<personaje>_raw.json       notación -> datos (salida del scraper)
    data/mappings/<personaje>_map.json  Move ID   -> notación (manual o --learn)

Salida (lo que lee el motor híbrido):
    data/compiled/<personaje>.json      Move ID   -> datos

    python tools/mapper.py bryan
    python tools/mapper.py --all
    python tools/mapper.py --list
"""
from __future__ import annotations

import argparse
import json
import os

from lookup_engine import slugify

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_DATA_DIR = os.path.join(ROOT, "data")


def _read(path: str, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def compile_character_db(char_name: str, data_dir: str | None = None,
                         quiet: bool = False) -> bool:
    """Genera data/compiled/<char>.json a partir de raw + mapa."""
    base = os.path.abspath(data_dir or DEFAULT_DATA_DIR)
    char = char_name.strip().lower()
    slug = slugify(char)
    raw_path = os.path.join(base, "raw", f"{slug}_raw.json")
    map_path = os.path.join(base, "mappings", f"{slug}_map.json")
    out_path = os.path.join(base, "compiled", f"{slug}.json")

    if not os.path.exists(raw_path):
        if not quiet:
            print(f"[Mapper] Falta {raw_path} (ejecuta tools/scraper.py {char})")
        return False
    if not os.path.exists(map_path):
        if not quiet:
            print(f"[Mapper] Falta {map_path} (usa el HUD con --learn)")
        return False

    raw_payload = _read(raw_path, {})
    raw = raw_payload.get("moves", raw_payload) if isinstance(raw_payload, dict) else {}
    id_map = _read(map_path, {})
    if not isinstance(raw, dict) or not isinstance(id_map, dict):
        return False

    compiled = {}
    missing = []
    for move_id, notation in id_map.items():
        move_id = str(move_id).lstrip("#").strip()
        if isinstance(notation, dict):
            notation = notation.get("notation", "")
        notation = str(notation).strip()
        info = raw.get(notation)
        if info is None:
            missing.append(f"{move_id}->{notation}")
            continue
        compiled[move_id] = dict(info)
        compiled[move_id]["notation"] = notation

    _write(out_path, {"character": char, "moves": compiled})
    if not quiet:
        extra = f"  (sin datos: {', '.join(missing)})" if missing else ""
        print(f"[Mapper] OK {out_path}: {len(compiled)} movimientos{extra}")
    return True


def mapped_characters(data_dir: str | None = None) -> list[str]:
    base = os.path.abspath(data_dir or DEFAULT_DATA_DIR)
    folder = os.path.join(base, "mappings")
    if not os.path.isdir(folder):
        return []
    return sorted(
        name[: -len("_map.json")]
        for name in os.listdir(folder)
        if name.endswith("_map.json")
    )


def compile_all(data_dir: str | None = None) -> int:
    ok = 0
    for char in mapped_characters(data_dir):
        ok += bool(compile_character_db(char, data_dir))
    return ok


def add_mapping(char_name: str, move_id: int | str, notation: str,
                data_dir: str | None = None) -> str:
    """Añade/actualiza un ID en el mapa (lo usa el modo --learn)."""
    base = os.path.abspath(data_dir or DEFAULT_DATA_DIR)
    char = char_name.strip().lower()
    map_path = os.path.join(base, "mappings", f"{slugify(char)}_map.json")
    id_map = _read(map_path, {})
    if not isinstance(id_map, dict):
        id_map = {}
    id_map[str(int(str(move_id).lstrip("#")))] = str(notation).strip()
    _write(map_path, dict(sorted(id_map.items(), key=lambda kv: int(kv[0]))))
    return map_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("characters", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--data-dir", default=None)
    args = ap.parse_args()

    if args.list:
        for char in mapped_characters(args.data_dir):
            print(char)
        return
    if args.all:
        total = compile_all(args.data_dir)
        print(f"[Mapper] {total} personajes compilados")
        return
    if not args.characters:
        ap.error("indica al menos un personaje (o --all)")
    for char in args.characters:
        compile_character_db(char, args.data_dir)


if __name__ == "__main__":
    main()
