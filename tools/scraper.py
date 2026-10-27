#!/usr/bin/env python3
"""Scraper de frame data pública -> data/raw/<personaje>_raw.json.

Fuente principal: TekkenDocs (https://tekkendocs.com/t6/<personaje>).
La estructura HTML se lee por **cabeceras** (Command / Startup / Block / Hit),
no por posición fija, y si una tabla no trae cabeceras se cae al formato
posicional del roadmap.  Solo usa la biblioteca estándar (`html.parser`): no
necesita BeautifulSoup ni ninguna instalación extra.

    python tools/scraper.py bryan
    python tools/scraper.py bryan jin kazuya
    python tools/scraper.py --roster              # los 40 del roster de T6
    python tools/scraper.py bryan --url https://otra/fuente

Salida:
    {"character": "bryan",
     "source": "https://tekkendocs.com/t6/bryan",
     "moves": {"1": {"notation": "1", "startup": 10, "on_block": 1, ...}}}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser

from lookup_engine import slugify

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_DATA_DIR = os.path.join(ROOT, "data")
DEFAULT_URL = "https://tekkendocs.com/t6/{character}"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FRAMEMOD/1.0"

# Roster de Tekken 6 / Bloodline Rebellion (40 personajes).
ROSTER = [
    "armor king", "anna", "asuka", "baek", "bob", "bruce", "bryan", "christie",
    "craig marduk", "devil jin", "eddy", "feng", "ganryu", "heihachi",
    "hwoarang", "jack-6", "jin", "julia", "kazuya", "king", "kuma", "lars",
    "lee", "lei", "leo", "lili", "ling", "marshall law", "miguel", "mokujin",
    "nina", "panda", "paul", "raven", "roger jr.", "sergei dragunov", "steve",
    "wang", "yoshimitsu", "zafina",
]

# TekkenDocs no usa el nombre del roster en todos los casos (404 comprobado).
SLUG_OVERRIDES = {
    "craig marduk": "marduk",
    "ling": "xiaoyu",
    "marshall law": "law",
    "sergei dragunov": "dragunov",
}

# Sin página propia: no hay frame data que descargar.
NO_TABLE = {
    "mokujin": "es mimético: usa la tabla del personaje que imita",
    "panda": "comparte la tabla de Kuma",
}

HEADER_ALIASES = {
    "notation": ("command", "move", "notation", "input", "attack"),
    "type": ("hit level", "level", "type", "h/l", "hit type"),
    "startup": ("startup", "start up", "start", "i"),
    "on_block": ("block", "on block", "on_block", "block frame"),
    "on_hit": ("hit", "on hit", "on_hit", "hit frame"),
    "on_ch": ("ch", "counter hit", "on ch", "on_ch", "ch frame"),
    "damage": ("damage", "dmg"),
}

ALL_ALIASES = {alias for aliases in HEADER_ALIASES.values() for alias in aliases}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def to_frame(value: str):
    """`i10`, `+1`, `-7`, `10`, `KND` -> int cuando es un frame."""
    text = clean(value)
    if not text or text in ("-", "--", "?"):
        return None
    match = re.fullmatch(r"[+-]?\d+", re.sub(r"^[iI]", "", text))
    return int(match.group(0)) if match else text


class TableParser(HTMLParser):
    """Extrae todas las tablas como listas de filas (lista de celdas de texto)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _column_map(header: list[str]) -> dict[str, int]:
    """Cabecera -> índice de columna, tolerando nombres alternativos.

    Primero se buscan coincidencias **exactas** y solo después prefijos: si no,
    `Hit level` se comía la columna de `Hit` (startswith("hit")) y la ventaja
    en golpe salía de la columna equivocada.
    """
    lowered = [clean(h).lower() for h in header]
    mapping: dict[str, int] = {}
    for key, aliases in HEADER_ALIASES.items():
        for idx, name in enumerate(lowered):
            if name in aliases:
                mapping[key] = idx
                break
    used = set(mapping.values())
    for key, aliases in HEADER_ALIASES.items():
        if key in mapping:
            continue
        for idx, name in enumerate(lowered):
            if idx in used:
                continue
            if any(name.startswith(alias) for alias in aliases):
                mapping[key] = idx
                used.add(idx)
                break
    return mapping


def _row_to_move(row: list[str], cols: dict[str, int]) -> dict | None:
    def cell(key):
        idx = cols.get(key)
        return row[idx] if idx is not None and idx < len(row) else ""

    notation = clean(cell("notation")) or clean(row[0] if row else "")
    if not notation or len(notation) > 40:
        return None
    if notation.lower() in ("command", "move", "notation", "input"):
        return None
    move = {
        "notation": notation,
        "type": clean(cell("type")),
        "startup": to_frame(cell("startup")),
        "on_block": to_frame(cell("on_block")),
        "on_hit": to_frame(cell("on_hit")),
        "on_ch": to_frame(cell("on_ch")),
        "damage": to_frame(cell("damage")),
    }
    if all(move[key] is None for key in ("startup", "on_block", "on_hit")):
        return None
    return move


def parse_html(html: str) -> dict[str, dict]:
    """Devuelve {notación: datos} a partir del HTML del personaje."""
    parser = TableParser()
    parser.feed(html)
    moves: dict[str, dict] = {}
    positional = {"notation": 0, "type": 1, "startup": 2, "on_block": 3,
                  "on_hit": 4, "on_ch": 5}
    for table in parser.tables:
        if not table:
            continue
        cols = _column_map(table[0])
        # Cabecera de verdad: hace falta la columna de comando y una de frames.
        looks_header = "notation" in cols and ("startup" in cols or "on_block" in cols)
        # una tabla sin cabecera reconocible: formato posicional del roadmap
        if not looks_header:
            cols = positional
            first = {clean(c).lower() for c in table[0]}
            rows = table[1:] if first & ALL_ALIASES else table
        else:
            rows = table[1:]
        for row in rows:
            move = _row_to_move(row, cols)
            if move:
                moves[move["notation"]] = move
    return moves


def fetch(url: str, timeout: float = 30.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, "replace")


def scrape_character(char_name: str, data_dir: str | None = None,
                     url: str | None = None, timeout: float = 30.0) -> bool:
    base = os.path.abspath(data_dir or DEFAULT_DATA_DIR)
    char = clean(char_name).lower()
    slug = SLUG_OVERRIDES.get(char) or slugify(char).replace("_", "-")
    target = url or DEFAULT_URL.format(character=slug)
    print(f"[Scraper] {char}: {target}")
    try:
        html = fetch(target, timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        print(f"[Scraper] ERROR de red para {char}: {exc}")
        return False

    moves = parse_html(html)
    if not moves:
        print(f"[Scraper] AVISO: sin tablas reconocibles para {char} "
              f"(revisa el HTML o usa --url)")
        return False

    out_dir = os.path.join(base, "raw")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{slugify(char)}_raw.json")
    payload = {
        "character": char,
        "source": target,
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "moves": moves,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"[Scraper] OK {out_path} ({len(moves)} movimientos)")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("characters", nargs="*")
    ap.add_argument("--roster", action="store_true", help="los 40 personajes de T6")
    ap.add_argument("--url", default=None, help="URL completa de un personaje")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--delay", type=float, default=1.0, help="segundos entre descargas")
    args = ap.parse_args()

    chars = list(args.characters)
    if args.roster:
        chars = ROSTER
    if not chars:
        ap.error("indica personajes o --roster")

    ok = 0
    for index, char in enumerate(chars):
        if index:
            time.sleep(max(0.0, args.delay))
        key = clean(char).lower()
        if key in NO_TABLE:
            print(f"[Scraper] {char}: sin tabla propia ({NO_TABLE[key]})")
            continue
        ok += bool(scrape_character(char, args.data_dir, args.url))
    print(f"[Scraper] {ok}/{len(chars)} personajes guardados")


if __name__ == "__main__":
    main()
