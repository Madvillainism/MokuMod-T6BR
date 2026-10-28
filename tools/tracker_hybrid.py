#!/usr/bin/env python3
"""Motor híbrido: proyección instantánea (tabla) + confirmación (memoria).

Fase 1 (t = 0): al cambiar el Move ID (`+0x144`) se consulta la tabla local y
se enseña el dato teórico al instante, marcado como provisional (`~`).

Fase 2 (t = impacto ... recuperación): la telemetría de memoria mide el
startup real y la ventaja real; ese valor **sobrescribe** al teórico.

    lookup = FrameTableLookup()
    hybrid = HybridTracker(lookup, "bryan", "bruce")
    d = hybrid.project(1, 7)              # i10~ +1~ (tabla, gris)
    d = hybrid.confirm(d, 10, 8, "HIT")   # i10 +8 (memoria, color)
"""
from __future__ import annotations

from dataclasses import dataclass

# Colores del roadmap -> HEX para Tk.
COLOR_HEX = {
    "gray": "#9E9E9E",
    "white": "#FFFFFF",
    "green": "#00E676",
    "orange": "#FF6D00",
    "red": "#FF1744",
    "bright_red": "#FF1744",
}


@dataclass
class FrameDisplayData:
    move_name: str
    startup_str: str
    advantage_str: str
    hit_type: str        # "BLOCK", "HIT", "PUNISH", "PREDICT", "WAIT"
    is_confirmed: bool   # False = dato teórico (tabla), True = medido en vivo
    color_code: str      # clave de COLOR_HEX
    move_id: int = 0
    side: str = "P1"

    @property
    def color(self) -> str:
        return COLOR_HEX.get(self.color_code, COLOR_HEX["white"])


def _colour_for(adv, kind: str) -> str:
    if kind == "PUNISH":
        return "bright_red"
    if adv is None:
        return "gray"
    if adv > 0:
        return "green"
    if adv <= -10:
        return "red"
    return "orange"


class HybridTracker:
    """Proyecta la tabla y confirma con la medición; sin estado de UI."""

    def __init__(self, lookup, char_p1: str | None, char_p2: str | None,
                 enabled: bool = True):
        self.lookup = lookup
        self.char_p1 = (char_p1 or "").strip().lower() or None
        self.char_p2 = (char_p2 or "").strip().lower() or None
        self.enabled = enabled

    def character(self, player_id: int) -> str | None:
        return self.char_p1 if int(player_id) == 1 else self.char_p2

    def project(self, player_id: int, move_id: int, key=None) -> FrameDisplayData:
        """Dato teórico inmediato (o fallback al ID crudo).

        `key` es la clave de tabla ya resuelta por el tracker ("20" o
        "crouch:20"): el mismo Move ID puede ser dos movimientos distintos
        según la postura y la proyección tiene que usar la entrada correcta.
        """
        side = "P1" if int(player_id) == 1 else "P2"
        char = self.character(player_id)
        info = None
        if self.enabled and char and move_id:
            info = self.lookup.get_move_info(char, key or move_id)
        if not info:
            return FrameDisplayData(
                move_name=f"#{move_id}", startup_str="i--", advantage_str="—",
                hit_type="WAIT", is_confirmed=False, color_code="white",
                move_id=int(move_id), side=side,
            )
        startup = info.get("startup")
        on_block = info.get("on_block")
        startup_str = f"i{startup}~" if isinstance(startup, int) else "i--"
        adv_str = f"{on_block:+d}~" if isinstance(on_block, int) else "—"
        return FrameDisplayData(
            move_name=str(info.get("notation") or f"#{move_id}"),
            startup_str=startup_str,
            advantage_str=adv_str,
            hit_type="PREDICT",
            is_confirmed=False,
            color_code="gray",
            move_id=int(move_id),
            side=side,
        )

    def qualitative_label(self, player_id: int, move_id: int,
                          kind: str = "HIT", key=None) -> str | None:
        """Etiqueta no numérica de la tabla (KND, CS, SLD...) o None.

        Tras un derribo el desbloqueo del rival tarda ~60 frames: publicar
        "+58" sería engañoso, así que se enseña la etiqueta de la tabla.
        """
        if kind != "HIT" or not self.enabled:
            return None
        char = self.character(player_id)
        if not (char and move_id):
            return None
        info = self.lookup.get_move_info(char, key or move_id) or {}
        on_hit = info.get("on_hit")
        if isinstance(on_hit, str):
            label = on_hit.strip().upper()
            if label and label not in ("-", "—", "?"):
                return label
        return None

    def confirm(self, display: FrameDisplayData | None, move_id: int,
                measured_startup: int | None, measured_adv: int | None,
                hit_type: str, side: str = "P1",
                qualitative: bool = True, key=None) -> FrameDisplayData:
        """La memoria manda: sustituye lo proyectado por lo medido.

        `qualitative=False` ignora la etiqueta de la tabla (KND...): se usa
        cuando la entrada del ID no es de fiar (p. ej. un ID compartido entre
        posturas), para no publicar el derribo de otro movimiento.
        """
        name = display.move_name if display else f"#{move_id}"
        startup = f"i{measured_startup}" if measured_startup is not None else "i--"
        label = None
        if hit_type == "HIT" and qualitative:
            label = self.qualitative_label(1 if side == "P1" else 2, move_id,
                                           key=key)
        if label:
            hit_type = label
            measured_adv = None
        adv = f"{measured_adv:+d}" if measured_adv is not None else "—"
        return FrameDisplayData(
            move_name=name,
            startup_str=startup,
            advantage_str=adv,
            hit_type=hit_type,
            is_confirmed=True,
            color_code=_colour_for(measured_adv, hit_type),
            move_id=int(move_id),
            side=side,
        )
