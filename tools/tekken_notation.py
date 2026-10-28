#!/usr/bin/env python3
"""Notación Tekken ↔ botones de PSP ↔ catálogo `data/raw/*_raw.json`.

La notación Tekken es el idioma común para escribir entradas y propiedades de
un movimiento.  Este módulo la **parsea** (no la adivina), la traduce a los
botones reales de la PSP y la enlaza con la frame data que el scraper guardó en
`data/raw/`.

## Botones (PSP)

| Botón PSP | Notación | Nombre |
|---|---|---|
| Cuadrado (□) | `1` | LP (left punch) |
| Triángulo (△) | `2` | RP (right punch) |
| Cruz (X) | `3` | LK (left kick) |
| Círculo (○) | `4` | RK (right kick) |

`1+2` = los dos puñetazos a la vez; `1,2` = primero `1` y después `2`.

## Direcciones

`n` neutro · `f` adelante · `b` atrás · `u` arriba · `d` abajo ·
`df` abajo+adelante · `db` abajo+atrás · `uf` arriba+adelante ·
`ub` arriba+atrás.

Mayúscula = **mantener** (`F` = forward mantenido / carrera).  `f,F+2` es
"adelante, mantener adelante y `2`", que no es lo mismo que `ff+2`.

## Mociones

| Notación | Entrada |
|---|---|
| `qcf` | `d,df,f` (cuarto de círculo adelante) |
| `qcb` | `d,db,b` (cuarto de círculo atrás) |
| `hcf` | `b,db,d,df,f` (medio círculo adelante) |
| `hcb` | `f,df,d,db,b` (medio círculo atrás) |
| `dp` | `f,d,df` (dragon punch) |
| `rdp` | `b,d,db` (reverse dragon punch) |
| `ff` / `bb` | dos veces adelante / atrás (dash) |
| `iws` / `iwr` | while standing / running instantáneo |

## Posturas

`FC` full crouch (agachado) · `WS`/`ws` while standing (levantándose) ·
`BT` de espaldas · `SS` sidestep (`SSL`/`SSR` a un lado) · `SW` sidewalk ·
`WR` corriendo · `CD` crouch dash · `OTG` rival en el suelo · `FUFT`/`FUFA`/
`FDFT`/`FDFA` posición del rival caído.  Las abreviaturas propias de cada
personaje (`RFF`, `LFS`, `RFS`, `SAV`, `MNT`, `SCR`, `AOP`, `HMS`, `FLK`,
`PAB`, `DGF`, `KIN`, `IND`, `MED`, `RLX`, `SNK`, `DCH`, `DRU`…) se aceptan tal
cual: cada personaje tiene su catálogo.

## Propiedades del movimiento (lo que lee el HUD)

- `type`: `h` alto · `m` medio · `l` bajo · `sm` medio especial · `sl` bajo
  especial (en mayúscula, `H`/`M`/`L`, cuando el golpe también alcanza al rival
  en el suelo).
- `startup`: `i10` = frame activo 10.
- `on_block` / `on_hit`: ventaja en frames.  Un número (`-14`) o una etiqueta
  cualitativa cuando no hay ventaja que medir: `KND` (knockdown / derribo),
  `CH`, `CS`…  Si es etiqueta, el HUD **no** calcula frames: la enseña.
- Sufijos del valor: `c` = el rival se recupera agachado, `g` = puede
  bloquear durante la recuperación, `w` = wall crush, `a` = queda en el aire.

Referencias: <https://wavu.wiki/t/Notation> (estilo de la comunidad) y
<https://tekkendocs.com/t6/bryan> (origen de `data/raw/`).

Uso
---
    python tools/tekken_notation.py --tables            # chuleta completa
    python tools/tekken_notation.py bryan df+3          # botones + frame data
    python tools/tekken_notation.py bryan --search KND  # movimientos con KND
    python tools/tekken_notation.py --check-raw         # cobertura del parser
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_DATA_DIR = os.path.join(ROOT, "data")

# --------------------------------------------------------------------------
# Vocabulario
# --------------------------------------------------------------------------
#: botón -> (nombre Tekken, botón de PSP, nombre en español)
BUTTONS = {
    1: ("LP", "square", "cuadrado"),
    2: ("RP", "triangle", "triángulo"),
    3: ("LK", "cross", "cruz"),
    4: ("RK", "circle", "círculo"),
}

#: dirección -> (inglés, español, teclas de PSP con el jugador mirando a la
#: derecha; en P2 se invierte f/b)
DIRECTIONS = {
    "n": ("neutral", "neutro", ()),
    "f": ("forward", "adelante", ("right",)),
    "b": ("back", "atrás", ("left",)),
    "u": ("up", "arriba", ("up",)),
    "d": ("down", "abajo", ("down",)),
    "uf": ("up-forward", "arriba+adelante", ("up", "right")),
    "ub": ("up-back", "arriba+atrás", ("up", "left")),
    "df": ("down-forward", "abajo+adelante", ("down", "right")),
    "db": ("down-back", "abajo+atrás", ("down", "left")),
}

#: mantener (mayúscula) -> mismo significado que la minúscula
HELD = {"F", "B", "U", "D", "UF", "UB", "DF", "DB"}

#: moción -> pasos de dirección
MOTIONS = {
    "qcf": ("d", "df", "f"),
    "qcb": ("d", "db", "b"),
    "hcf": ("b", "db", "d", "df", "f"),
    "hcb": ("f", "df", "d", "db", "b"),
    "dp": ("f", "d", "df"),
    "rdp": ("b", "d", "db"),
    "ff": ("f", "f"),
    "bb": ("b", "b"),
    "dash": ("f", "f"),
}

#: posturas y estados
STANCES = {
    "FC": "full crouch (agachado completo)",
    "HFC": "half crouch",
    "WS": "while standing (levantándose)",
    "BT": "back turned (de espaldas)",
    "SS": "sidestep",
    "SSL": "sidestep a la izquierda",
    "SSR": "sidestep a la derecha",
    "SW": "sidewalk",
    "SWL": "sidewalk a la izquierda",
    "SWR": "sidewalk a la derecha",
    "WR": "while running (corriendo)",
    "CD": "crouch dash",
    "IWS": "instant while standing",
    "IWR": "instant while running",
    "OTG": "rival en el suelo (on the ground)",
    "FUFT": "rival boca arriba, pies hacia ti",
    "FUFA": "rival boca arriba, pies lejos",
    "FDFT": "rival boca abajo, pies hacia ti",
    "FDFA": "rival boca abajo, pies lejos",
    # posturas de personaje (Tekken 6)
    "RFF": "right foot forward (Hwoarang)",
    "LFF": "left foot forward (Hwoarang)",
    "LFS": "left flamingo stance (Hwoarang)",
    "RFS": "right flamingo stance (Hwoarang)",
    "SAV": "savage stance (Miguel)",
    "MNT": "mantis stance (Zafina)",
    "SCR": "scarecrow stance (Zafina)",
    "AOP": "art of phoenix (Ling)",
    "HMS": "hitman stance (Lee)",
    "FLK": "flicker stance (Steve)",
    "PAB": "peekaboo (Steve)",
    "DGF": "manji dragonfly (Yoshimitsu)",
    "KIN": "kincho (Yoshimitsu)",
    "IND": "indian stance (Yoshimitsu)",
    "MED": "meditation (Yoshimitsu)",
    "FLY": "fly stance",
    "HSP": "handstand",
    "DCK": "ducking",
}

#: modificadores entre golpes
MODIFIERS = {
    "+": "a la vez",
    ",": "después",
    "~": "después, inmediato",
    "*": "mantenido",
    "<>": "después, con retardo",
    "/": "o",
}

#: etiquetas cualitativas de `on_hit` / `on_block` (no son frames)
QUALITATIVE = {
    "KND": "knockdown (derribo)",
    "HKD": "hard knockdown",
    "CH": "counter hit",
    "CS": "queda agachado",
    "SLD": "slide",
    "W!": "wall splat",
}

_TYPE_NAMES = {
    "h": "alto",
    "m": "medio",
    "l": "bajo",
    "sm": "medio especial",
    "sl": "bajo especial",
    "!": "agarre / unblockable",
    "t": "throw (agarre)",
}

_TOKEN_RE = re.compile(r"<>|\d+|[A-Za-z]+|[,\+\./~\*\(\)]")
_SEPARATORS = set(",+./~*()")
_BUTTON_SEPS = {"+", ".", "~", "*", "/"}
#: entradas que no son notación sino un nombre ("Left throw", "Back throw")
_NAMED_RE = re.compile(r"^[A-Za-z][A-Za-z ]*$")


def is_named_entry(text: str) -> bool:
    """¿La clave del catálogo es un nombre (throw) y no una notación?"""
    text = str(text).strip()
    return bool(_NAMED_RE.match(text)) and " " in text


class Part:
    """Un golpe dentro de una notación (un string tiene varios)."""

    __slots__ = ("tokens", "buttons", "btn_sep", "trail", "raw")

    def __init__(self, tokens, buttons, btn_sep, trail, raw):
        self.tokens = tokens      # [(separador_previo, token)] direcciones/posturas
        self.buttons = buttons    # (1, 2) ...
        self.btn_sep = btn_sep    # separador entre el prefijo y los botones
        self.trail = trail        # separador que cierra el golpe ("," "~" "*")
        self.raw = raw

    @property
    def prefix(self):
        return [tok for _sep, tok in self.tokens]

    def __repr__(self):  # pragma: no cover - ayuda de depuración
        return f"Part({self.raw!r}, {self.tokens}, {self.buttons})"


def parse(text: str) -> list[Part]:
    """Notación -> lista de golpes.

    Un `,` (o `~`, `*`) solo separa golpes cuando ya hay un botón delante; si
    no, forma parte de una moción (`f,n,d,df+3`).  Nunca lanza excepciones: lo
    que no entiende lo deja como token de postura.
    """
    raw_text = re.sub(r"\s+", "", str(text).strip())
    if is_named_entry(text):
        return [Part([("", str(text).strip())], (), "", "", str(text).strip())]
    parts: list[Part] = []
    tokens: list[tuple[str, str]] = []
    buttons: list[int] = []
    btn_sep = ""
    last_sep = ""
    trail = ""

    def flush():
        nonlocal tokens, buttons, btn_sep, last_sep, trail
        if not tokens and not buttons:
            return
        parts.append(Part(tokens, tuple(buttons), btn_sep, trail, raw_text))
        tokens, buttons, btn_sep, last_sep, trail = [], [], "", "", ""

    # `j24` (5 entradas del catálogo) son dos pulsaciones seguidas, no el botón 24
    toks: list[str] = []
    for tok in _TOKEN_RE.findall(raw_text):
        if tok.isdigit() and len(tok) > 1 and set(tok) <= set("1234"):
            for i, ch in enumerate(tok):
                if i:
                    toks.append(",")
                toks.append(ch)
        else:
            toks.append(tok)

    for tok in toks:
        if tok.isdigit():
            if buttons:
                if last_sep == "+":
                    buttons.append(int(tok))
                else:
                    trail = last_sep
                    flush()
                    buttons = [int(tok)]
                    btn_sep = ""
            else:
                buttons = [int(tok)]
                btn_sep = last_sep
            last_sep = ""
        elif tok in _SEPARATORS or tok == "<>":
            last_sep = tok
        else:
            tokens.append((last_sep, tok))
            last_sep = ""
    if last_sep in ("*", "~"):
        trail = last_sep
    flush()
    return parts


# --------------------------------------------------------------------------
# Normalización y etiquetas
# --------------------------------------------------------------------------
def _norm_token(tok: str) -> str:
    # Mayúscula = mantener (`F` no es lo mismo que `f`): se respeta.
    # Solo las direcciones de una letra conservan la mayúscula; `DF`/`UF`… son
    # casi siempre la misma dirección escrita en mayúsculas por comodidad.
    if len(tok) == 1 and tok.isupper() and tok.upper() in HELD:
        return tok.upper()
    low = tok.lower()
    if low in DIRECTIONS or low in MOTIONS:
        return low
    if low in ("ws", "iws", "iwr"):
        return low
    if tok.upper() in STANCES:
        return tok.upper()
    if tok.isupper():
        return tok.upper()          # postura de personaje (RLX, SNK, DCH…)
    return tok


def _build_prefix(tokens) -> str:
    """Une los tokens del prefijo: `d` + `/` + `f` -> `df` (no `d/f`)."""
    prefix = ""
    prev = ""
    for sep, tok in tokens:
        norm = _norm_token(tok)
        if sep == "/" and prev and (prev + norm).lower() in DIRECTIONS:
            merged = (prev + norm).lower()
            prefix = prefix[: -len(prev)] + merged
            prev = merged
            continue
        prefix += sep + norm
        prev = norm
    return prefix


def canonical(text: str) -> str:
    """Forma canónica (la que usan los `_raw.json`) de una notación.

    `DF+3`, `d/f+3`, `df 3` -> `df+3`; `ws + 2` -> `ws2`; `fc.4` -> `FC.4`;
    `1,2` sigue siendo `1,2` (dos golpes) y `1+2` sigue siendo `1+2`.
    """
    out = []
    for part in parse(text):
        prefix = _build_prefix(part.tokens)
        body = "+".join(str(b) for b in part.buttons)
        sep = "" if part.btn_sep == "/" else part.btn_sep
        if prefix.lower() in ("ws", "iws", "iwr"):
            sep = ""               # el catálogo escribe `ws2`, no `ws+2`
        if body:
            out.append(f"{prefix}{sep}{body}{part.trail}")
        else:
            out.append(f"{prefix}{part.trail}")
    return "".join(out)


def label(text: str) -> str:
    """Etiqueta corta para el HUD: canónica si se entiende, si no el original."""
    try:
        can = canonical(text)
    except Exception:  # noqa: BLE001 - nunca romper el HUD por una etiqueta
        return str(text)
    return can or str(text)


def buttons(text: str) -> tuple[int, ...]:
    """Botones de todos los golpes de la notación, en orden y sin repetir."""
    out: list[int] = []
    for part in parse(text):
        for b in part.buttons:
            if b not in out:
                out.append(b)
    return tuple(out)


def psp_steps(text: str, facing: str = "right") -> list[tuple[str, ...]]:
    """Entrada completa en pasos, como la pulsaría un jugador de PSP.

    Cada paso es un conjunto de teclas simultáneas; el botón se pulsa en el
    último paso de su golpe.  `facing="left"` invierte adelante/atrás (P2).
    """
    flip = {"right": "left", "left": "right"}
    steps: list[tuple[str, ...]] = []
    for part in parse(text):
        dirs: list[str] = []
        for tok in part.prefix:
            low = tok.lower()
            if low in MOTIONS:
                dirs.extend(MOTIONS[low])
            elif low in DIRECTIONS:
                dirs.append(low)
            # las posturas (FC, WS, BT…) no son pulsaciones: se llega a ellas
        if not dirs:
            dirs = ["n"]
        for i, d in enumerate(dirs):
            keys = list(DIRECTIONS[d][2])
            if facing == "left":
                keys = [flip.get(k, k) for k in keys]
            if i == len(dirs) - 1:
                keys.extend(BUTTONS[b][1] for b in part.buttons if b in BUTTONS)
            steps.append(tuple(keys))
    return steps


def button_names(text: str) -> list[str]:
    """`df+3` -> ['down', 'right', 'cross'] (botones reales de la PSP)."""
    names: list[str] = []
    for step in psp_steps(text):
        for key in step:
            if key not in names:
                names.append(key)
    return names


def describe(text: str) -> str:
    """Descripción en español de la entrada, golpe a golpe."""
    chunks = []
    for part in parse(text):
        bits = []
        for tok in part.prefix:
            low = tok.lower()
            if low in MOTIONS:
                bits.append(f"{tok} ({','.join(MOTIONS[low])})")
            elif low in DIRECTIONS:
                bits.append(DIRECTIONS[low][1])
            else:
                bits.append(f"postura {tok}")
        for b in part.buttons:
            if b in BUTTONS:
                bits.append(f"{BUTTONS[b][2]} ({BUTTONS[b][0]})")
            else:
                bits.append(f"?{b}")
        chunks.append(" + ".join(bits) if bits else part.raw)
    return " , ".join(chunks)


def is_qualitative(value) -> bool:
    """¿El valor de la tabla es una etiqueta (KND, CH…) en vez de frames?"""
    if isinstance(value, int):
        return False
    text = str(value or "").strip()
    return bool(text) and text not in ("-", "—", "?")


def frame_text(value) -> str:
    """`-14` -> `-14`, `KND` -> `KND`, `+5c` -> `+5c`, vacío -> `?`."""
    if isinstance(value, int):
        return f"{value:+d}"
    text = str(value or "").strip()
    return text or "?"


# --------------------------------------------------------------------------
# Catálogo `data/raw/*_raw.json`
# --------------------------------------------------------------------------
def slugify(name: str) -> str:
    """Nombre de personaje -> nombre de archivo (`Armor King` -> `armor_king`)."""
    text = str(name).strip().lower()
    for ch in (" ", "-", "."):
        text = text.replace(ch, "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def raw_path(char: str, data_dir: str | None = None) -> str:
    base = os.path.abspath(data_dir or DEFAULT_DATA_DIR)
    name = str(char).strip().lower()
    # `jack-6` se llama así en disco: se prueba el nombre tal cual antes de
    # slugificarlo, o el personaje se queda sin catálogo.
    for candidate in (name, slugify(name), name.replace(" ", "")):
        path = os.path.join(base, "raw", f"{candidate}_raw.json")
        if os.path.exists(path):
            return path
    return os.path.join(base, "raw", f"{slugify(name)}_raw.json")


def load_moves(char: str, data_dir: str | None = None) -> dict:
    """notación -> datos del scraper (dict vacío si el personaje no existe)."""
    try:
        with open(raw_path(char, data_dir), encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return {}
    return payload.get("moves") or {}


def _loose(text: str) -> str:
    return (text.lower().replace("+", "").replace(".", "")
            .replace("/", "").replace(" ", ""))


def character_stances(char: str, data_dir: str | None = None) -> dict[str, int]:
    """Posturas que usa un personaje, sacadas de su propio catálogo.

    No hay tabla universal: `RFF` es Hwoarang y `SAV` es Miguel.  Se leen de las
    claves del `_raw.json` (prefijo en mayúsculas antes de `.` o `+`), así que
    lo que el HUD acepta es exactamente lo que trae el dato.
    """
    found: dict[str, int] = {}
    for key in load_moves(char, data_dir):
        if is_named_entry(key):
            continue
        for part in parse(key):
            for tok in part.prefix:
                if tok.isupper() and tok.isalpha() and len(tok) >= 2:
                    found[tok] = found.get(tok, 0) + 1
    return dict(sorted(found.items(), key=lambda kv: (-kv[1], kv[0])))


def find_key(moves: dict, text: str) -> str | None:
    """Notación escrita por el usuario -> clave real del `_raw.json`."""
    if not moves:
        return None
    if text in moves:
        return text
    can = canonical(text)
    if can in moves:
        return can
    low = {k.lower(): k for k in moves}
    if can.lower() in low:
        return low[can.lower()]
    if text.lower() in low:
        return low[text.lower()]
    # último recurso: sin separadores (df3 == df+3)
    loose = _loose(can)
    for key in moves:
        if _loose(key) == loose:
            return key
    return None


def lookup(char: str, text: str, data_dir: str | None = None) -> dict | None:
    """Ficha completa de un movimiento: notación, botones y frame data real."""
    moves = load_moves(char, data_dir)
    key = find_key(moves, text)
    if key is None:
        return None
    info = dict(moves[key])
    info["notation"] = key
    info["buttons"] = button_names(key)
    info["psp"] = psp_steps(key)
    info["describe"] = describe(key)
    return info


def format_move(char: str, text: str, data_dir: str | None = None) -> str:
    """Ficha legible de un movimiento (para consola y para `--learn`)."""
    info = lookup(char, text, data_dir)
    if info is None:
        return f"{char}: no encuentro {text!r} en data/raw/"
    level = str(info.get("type") or "").strip()
    lines = [
        f"{info['notation']}   ({info['describe']})",
        f"  botones : {' + '.join(info['buttons'])}",
        ("  nivel   : %s (%s)" % (level, _TYPE_NAMES.get(level.lower(), "?"))
         if level else "  nivel   : ?"),
        f"  startup : i{info.get('startup')}",
        f"  on block: {frame_text(info.get('on_block'))}",
        f"  on hit  : {frame_text(info.get('on_hit'))}",
        f"  daño    : {info.get('damage')}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _check_raw(data_dir: str) -> int:
    """Cobertura del parser sobre TODOS los `_raw.json` (no adivina, informa)."""
    files = sorted(glob.glob(os.path.join(data_dir, "raw", "*_raw.json")))
    total = ok = named = 0
    unknown: dict[str, int] = {}
    bad: list[tuple[str, str, str]] = []
    for path in files:
        char = os.path.basename(path)[: -len("_raw.json")]
        for key in load_moves(char, data_dir):
            total += 1
            if is_named_entry(key):
                named += 1
                ok += 1
                continue
            parts = parse(key)
            trouble: list[str] = []
            if not parts:
                trouble.append("vacío")
            for part in parts:
                if not part.tokens and not part.buttons:
                    trouble.append("vacío")
                for tok in part.prefix:
                    low = tok.lower()
                    known = (low in DIRECTIONS or low in MOTIONS
                             or tok.upper() in STANCES or tok.upper() in HELD
                             or low in ("ws", "iws", "iwr", "j", "h"))
                    if not known:
                        unknown[tok] = unknown.get(tok, 0) + 1
                for b in part.buttons:
                    if b not in BUTTONS:
                        unknown[f"?{b}"] = unknown.get(f"?{b}", 0) + 1
            if trouble:
                bad.append((char, key, ", ".join(sorted(set(trouble)))))
            else:
                ok += 1
    print(f"archivos      : {len(files)}")
    print(f"movimientos   : {total}  (incluye {named} con nombre: throws)")
    pct = (100.0 * ok / total) if total else 0.0
    print(f"estructura ok : {ok} ({pct:.1f}%)")
    print(f"ilegibles     : {len(bad)}")
    if unknown:
        print(f"\nvocabulario no catalogado ({len(unknown)} tokens, "
              "se aceptan como postura):")
        for tok, n in sorted(unknown.items(), key=lambda kv: -kv[1])[:30]:
            print(f"  {n:4d}  {tok!r}")
    if bad:
        print("\nprimeros 20 ilegibles:")
        for char, key, why in bad[:20]:
            print(f"  {char:14s} {key:24s} {why}")
    return 0 if not bad else 1


def _tables() -> None:
    print("BOTONES (PSP)")
    for num, (tk, psp, es) in BUTTONS.items():
        print(f"  {num}  {psp:8s} {es:10s} {tk}")
    print("\nDIRECCIONES")
    for tok, (en, es, keys) in DIRECTIONS.items():
        print(f"  {tok:3s} {en:14s} {es:20s} {'+'.join(keys) or '-'}")
    print("\nMOCIONES")
    for tok, steps in MOTIONS.items():
        print(f"  {tok:5s} {','.join(steps)}")
    print("\nPOSTURAS")
    for tok, desc in STANCES.items():
        print(f"  {tok:5s} {desc}")
    print("\nNIVEL DE GOLPE (type)")
    for tok, desc in _TYPE_NAMES.items():
        print(f"  {tok:4s} {desc}")
    print("\nETIQUETAS CUALITATIVAS (on_hit / on_block)")
    for tok, desc in QUALITATIVE.items():
        print(f"  {tok:5s} {desc}")


def _match(char: str, *nums: str, data_dir: str | None = None) -> int:
    """Movimientos del catálogo que encajan con una medición en vivo.

    Sirve para identificar un Move ID sin mapear: el HUD mide (startup,
    ventaja) y aquí se buscan las entradas de la tabla que coinciden.  La
    ventaja medida se compara con el `on block` **y** con el `on hit` (el HUD
    no siempre sabe si el rival bloqueó o comió el golpe) y se dice por cuál
    encaja.  La ventaja es el dato más fiable; el startup cambia si el golpe
    sale de una transición, así que solo desempata.

    Un solo número se interpreta como ventaja; dos, como `startup` y ventaja.
    """
    def _int(text):
        text = str(text).strip()
        return int(text) if text.lstrip("+-").isdigit() else None

    values = [_int(n) for n in nums if str(n).strip()]
    values = [v for v in values if v is not None]
    if not values:
        print("--match necesita números: startup y/o ventaja")
        return 2
    want_s, want_adv = (None, values[0]) if len(values) == 1 else (values[0], values[1])
    rows = []
    for key, info in load_moves(char, data_dir).items():
        s = info.get("startup")
        candidates = []
        for field, name in (("on_block", "b"), ("on_hit", "h")):
            value = info.get(field)
            if isinstance(value, int):
                candidates.append((name, value))
        if not candidates:
            continue
        if want_adv is None:
            field, da = "b", 0
        else:
            field, value = min(candidates, key=lambda fv: abs(fv[1] - want_adv))
            da = abs(value - want_adv)
        if want_s is None:
            ds = 0
        elif isinstance(s, int):
            ds = abs(int(s) - want_s)
        else:
            ds = 99
        rows.append((da, ds, key, info, field))
    if not rows:
        print(f"{char}: el catálogo no tiene ventajas numéricas")
        return 1
    rows.sort(key=lambda r: (r[0], r[1]))
    print(f"candidatos para {slugify(char)} "
          f"(startup {want_s if want_s is not None else '?'}, "
          f"ventaja {want_adv if want_adv is not None else '?'}):")
    for da, ds, key, info, field in rows[:12]:
        if da > 3 or (want_s is not None and ds > 6):
            break
        print("  %-22s i%-4s b%-5s h%-8s %-4s   (d=%d via %s, di=%s)" % (
            key, info.get("startup"), frame_text(info.get("on_block")),
            frame_text(info.get("on_hit")), info.get("type"),
            da, field, "-" if ds == 99 else ds))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Notación Tekken ↔ botones ↔ raw")
    ap.add_argument("char", nargs="?", help="personaje (bryan, armor king…)")
    ap.add_argument("notation", nargs="?", help="notación a consultar (df+3)")
    ap.add_argument("--data", dest="data_dir", default=None, help="carpeta data/")
    ap.add_argument("--tables", action="store_true", help="chuleta de notación")
    ap.add_argument("--check-raw", action="store_true",
                    help="valida el parser contra todos los _raw.json")
    ap.add_argument("--search", metavar="TEXTO",
                    help="busca en el catálogo del personaje (notación o valor)")
    ap.add_argument("--buttons", metavar="NOTACION",
                    help="solo traduce a botones de PSP")
    ap.add_argument("--canonical", metavar="NOTACION",
                    help="enseña la forma canónica")
    ap.add_argument("--stances", action="store_true",
                    help="posturas que usa el personaje (leídas de su raw)")
    ap.add_argument("--match", nargs="+", metavar="NUM",
                    help="movimientos del catálogo que encajan con lo medido: "
                         "uno o dos números (startup [on block]); "
                         "p. ej. --match 27 -29 o --match -29")
    ap.add_argument("--steps", metavar="NOTACION",
                    help="entrada paso a paso en la PSP (dibujo de la pulsación)")
    a = ap.parse_args(argv)
    data_dir = a.data_dir or DEFAULT_DATA_DIR

    if a.tables:
        _tables()
        return 0
    if a.steps:
        for step in psp_steps(a.steps):
            print(" + ".join(step))
        return 0
    if a.check_raw:
        return _check_raw(data_dir)
    if a.buttons:
        print(" + ".join(button_names(a.buttons)))
        return 0
    if a.canonical:
        print(canonical(a.canonical))
        return 0
    if not a.char:
        ap.print_help()
        return 2
    if a.notation:
        print(format_move(a.char, a.notation, data_dir))
        return 0
    moves = load_moves(a.char, data_dir)
    if not moves:
        print(f"no hay data/raw/{slugify(a.char)}_raw.json")
        return 1
    if a.stances:
        stances = character_stances(a.char, data_dir)
        print(f"posturas de {slugify(a.char)} ({len(stances)}):")
        for tok, n in stances.items():
            desc = STANCES.get(tok, "postura del personaje")
            print(f"  {tok:5s} {n:4d} movs   {desc}")
        return 0
    if a.match:
        return _match(a.char, *a.match, data_dir=data_dir)
    needle = (a.search or "").lower()
    for key in moves:
        info = moves[key]
        hay = f"{key} {info.get('on_hit')} {info.get('type')}".lower()
        if needle and needle not in hay:
            continue
        print("%-22s i%-4s b%-5s h%-8s %-4s" % (
            key, info.get("startup"), frame_text(info.get("on_block")),
            frame_text(info.get("on_hit")), info.get("type")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
