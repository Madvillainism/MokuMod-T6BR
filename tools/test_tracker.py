#!/usr/bin/env python3
"""Autotest determinista del motor de frame data (sin PPSSPP).

Construye partidas sintéticas muestra a muestra y comprueba lo que el HUD
publicaría.  Sirve para fijar los dos fallos que costó encontrar:

  * PUNISH compara frames de P1 y de P2 -> necesitan el MISMO reloj
    (los contadores de animación repiten valores y los relojes derivaban).
  * un golpe que interrumpe la recuperación del dummy no puede publicar una
    ventaja basura (-55) sacada de un pulso espurio de la cola.

    python tools/test_tracker.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from hud import TrackerThread, load_notation  # noqa: E402

IDLE = 6466
IDLE2 = 6482
BLOCK = 4178
ATTACK = 2114


class NullQueue:
    def put(self, _item):
        pass


class Sim:
    def __init__(self, p1_notation=None, p2_notation=None, predict=True):
        self.events = []
        self.tt = TrackerThread(
            NullQueue(), debug=False, p1_notation=p1_notation,
            p2_notation=p2_notation, predict=predict,
        )
        self.tt.emit = self.events.append
        self.n = 0

    def frame(self, **kw):
        """Un frame de juego (3 muestras, como el lector real a ~1 kHz)."""
        self.n += 1
        s = self._sample(**kw)
        for _ in range(3):
            self.tt.feed(s)
        return s

    def _sample(self, **kw):
        s = {
            "c1": self.n, "mid": 0, "free1": 1, "tail1": 0, "s1": IDLE2, "hp1": 180,
            "mid2": 0, "s2": IDLE, "c2": self.n, "hp2": 180, "free2": 1, "tail2": 0,
        }
        s.update(kw)
        return s

    def sample(self, **kw):
        """Muestra extra dentro del frame actual (el lector ve ~16 por frame)."""
        self.tt.feed(self._sample(**kw))

    def find(self, side, phase, label=None):
        for ev in self.events:
            if ev["side"] == side and ev["phase"] == phase:
                if label is None or ev["label"] == label:
                    return ev
        return None

    def all(self, side, phase):
        return [e for e in self.events if e["side"] == side and e["phase"] == phase]


def block_and_punish(jab_start=26, drift=False, cache=None, predict=True):
    """Dummy pega (bloqueado, -12) y P1 castiga con un jab i11."""
    sim = Sim(predict=predict)
    if cache:
        sim.tt.cache = dict(cache)
    hit = jab_start + 10                 # frame del impacto del jab
    recover = 37                         # el dummy recupera a los 37...
    if hit < recover:                    # ...salvo que el jab lo interrumpa
        recover = hit + 9                # (pulso tras el hitstun)
    hitstun_end = hit + 9
    p1_unlock = hit + 5

    for _ in range(9):
        sim.frame()
    # P2 arranca su movimiento 12
    sim.frame(mid2=12, s2=ATTACK, c2=1)
    for k in range(2, 10):
        sim.frame(mid2=12, s2=ATTACK, c2=k)
    # frame 19: bloqueo (P1 entra en blockstun)
    sim.frame(mid2=12, s2=ATTACK, c2=10, s1=BLOCK, c1=1)
    for k in range(2, 7):
        sim.frame(mid2=12, s2=ATTACK, c2=10 + k, s1=BLOCK, c1=k)
    # frame 25: P1 libre (pulso de la cola)
    sim.frame(mid2=12, s2=ATTACK, c2=16, s1=IDLE2, c1=7, tail1=1)
    # jab de P1
    for n in range(sim.n + 1, sim.n + 1 + 30):
        kw = {}
        if n == jab_start:
            kw.update(mid=7, s1=ATTACK, c1=1)
        elif jab_start < n < hit:
            kw.update(mid=7, s1=ATTACK, c1=n - jab_start + 1)
        elif n == hit:                   # impacto (baja la vida del dummy)
            kw.update(mid=7, s1=ATTACK, c1=hit - jab_start + 1, hp2=172)
        if n == recover:                 # el dummy vuelve a poder actuar
            kw.update(mid2=0, s2=IDLE, c2=1, tail2=1)
        if n == hitstun_end and n != recover:
            kw.update(tail2=1)
        if n == p1_unlock:               # P1 recupera
            kw.update(mid=0, s1=IDLE2, c1=1, tail1=1)
        if drift:
            kw["c2"] = n // 2   # el contador de P2 repite valores: relojes distintos derivarían
        sim.frame(**kw)
    return sim


def test_punish():
    sim = block_and_punish()
    ev = sim.find("P1", "impact", "#7")
    assert ev is not None, "no se registró el impacto del jab"
    assert ev["startup"] == 11, f"startup {ev['startup']} != 11"
    assert ev.get("punish") is True, f"sin PUNISH: {ev.get('punish_why')}"
    assert ev["impact"] == 36, f"impacto {ev['impact']} != 36"

    # la ventaja del bloqueo (-12) se mide pero NO se publica: el golpe de P1
    # cortó la recuperación del dummy
    fin = sim.find("P2", "final", "#12")
    assert fin is not None, "el dummy no publicó final"
    assert fin["adv"] is None, f"ventaja basura publicada: {fin['adv']}"

    # la ventaja del jab sí se publica y es correcta
    fin = sim.find("P1", "final", "#7")
    assert fin is not None and fin["adv"] == 4, f"ventaja del jab {fin and fin['adv']}"
    assert fin["kind"] == "HIT"
    print("ok  punish i11 en un -12, sin ventaja basura")


def test_clock_drift():
    sim = block_and_punish(drift=True)
    ev = sim.find("P1", "impact", "#7")
    assert ev is not None
    assert ev.get("punish") is True, (
        f"con contadores que repiten valores el PUNISH se pierde: {ev.get('punish_why')}"
    )
    print("ok  PUNISH correcto aunque el contador de P2 repita valores")


def test_late_hit_is_not_punish():
    sim = block_and_punish(jab_start=31)   # llega 5 frames tarde
    ev = sim.find("P1", "impact", "#7")
    assert ev is not None
    assert ev.get("punish") is False, f"falso PUNISH: {ev.get('punish_why')}"
    # el bloqueo sí se midió y se publicó antes del golpe
    fin = sim.find("P2", "final", "#12")
    assert fin is not None and fin["adv"] == -12, f"ventaja {fin and fin['adv']} != -12"
    print("ok  golpe tardío: no es PUNISH y el -12 se publica")


def test_prediction():
    import time

    # una medición real se guarda en la caché
    late = block_and_punish(jab_start=31)
    assert late.tt.cache.get(("P2", 12, "BLOCK"), (None,))[0] == -12, late.tt.cache

    sim = block_and_punish(jab_start=31, cache=late.tt.cache)
    ev = sim.find("P2", "impact", "#12")
    assert ev is not None and ev.get("predict") == -12, f"predicción {ev and ev.get('predict')}"

    off = block_and_punish(jab_start=31, cache=late.tt.cache, predict=False)
    ev = off.find("P2", "impact", "#12")
    assert ev is not None and ev.get("predict") is None
    print("ok  valor adelantado (~) desde la última medición, y --no-predict lo apaga")


def test_one_block_one_punish():
    """Dos golpes seguidos sobre el mismo bloqueo: solo el primero es castigo."""
    sim = Sim()
    for _ in range(9):
        sim.frame()
    sim.frame(mid2=12, s2=ATTACK, c2=1)
    for k in range(2, 10):
        sim.frame(mid2=12, s2=ATTACK, c2=k)
    sim.frame(mid2=12, s2=ATTACK, c2=10, s1=BLOCK, c1=1)
    for k in range(2, 7):
        sim.frame(mid2=12, s2=ATTACK, c2=10 + k, s1=BLOCK, c1=k)
    sim.frame(mid2=12, s2=ATTACK, c2=16, s1=IDLE2, c1=7, tail1=1)
    for n in range(26, 60):
        kw = {}
        if n == 26:
            kw.update(mid=7, s1=ATTACK, c1=1)
        elif 26 < n < 50:
            kw.update(mid=7, s1=ATTACK, c1=n - 25)
        if n == 36:
            kw.update(hp2=172)          # primer golpe: castigo
        if n == 39:
            kw.update(hp2=164)          # segundo golpe del combo: NO
        if n == 41:
            kw.update(mid=0, s1=IDLE2, c1=1, tail1=1)
        if n == 45:
            kw.update(mid2=0, s2=IDLE, c2=1, tail2=1)
        sim.frame(**kw)
    hits = [e for e in sim.events if e["side"] == "P1" and e["phase"] == "impact"]
    assert len(hits) == 2, f"{len(hits)} impactos"
    assert hits[0].get("punish") is True, hits[0].get("punish_why")
    assert hits[1].get("punish") is False, "el segundo golpe del combo se marcó como castigo"
    print("ok  un bloqueo = un castigo (el 2.º golpe del combo no se marca)")


def test_startup_from_counter():
    """El arranque sale del contador de animación, no de contar muestras.

    Dos fallos reales que este test fija:
      * el lector pierde frames (contador 1,2,4,5,7,8,9,10) -> el jab salía i8;
      * el id aparece una muestra antes del reset del contador -> salía i11.
    """
    # (a) frames perdidos por el lector
    sim = Sim()
    for _ in range(5):
        sim.frame()
    for c in (1, 2, 4, 5, 7, 8, 9, 10):
        kw = {"mid": 7, "s1": ATTACK, "c1": c}
        if c == 10:
            kw["hp2"] = 172
        sim.frame(**kw)
    ev = sim.find("P1", "impact", "#7")
    assert ev is not None and ev["startup"] == 10, f"i{ev and ev['startup']} (frames perdidos)"

    # (b) el id 7 se escribe con el contador viejo; el reset llega después
    sim = Sim()
    for _ in range(5):
        sim.frame()
    sim.sample(mid=7, s1=ATTACK, c1=5)      # id nuevo, contador aún viejo
    sim.frame(mid=7, s1=ATTACK, c1=1)       # primer frame real del jab
    for k in range(2, 10):
        sim.frame(mid=7, s1=ATTACK, c1=k)
    sim.frame(mid=7, s1=ATTACK, c1=10, s2=BLOCK, c2=1)
    ev = sim.find("P1", "impact", "#7")
    assert ev is not None and ev["startup"] == 10, f"i{ev and ev['startup']} (id antes del reset)"
    print("ok  arranque i10 aunque se pierdan frames o el id llegue antes del reset")


def test_same_id_pressed_again():
    """Dos jabs seguidos: el id no vuelve a 0, solo reinicia el contador."""
    sim = Sim()
    for _ in range(5):
        sim.frame()
    for c in range(1, 38):
        sim.frame(mid=7, s1=ATTACK, c1=c, hp2=172 if c == 10 else 180)
    for c in range(1, 11):
        sim.frame(mid=7, s1=ATTACK, c1=c, hp2=164 if c == 10 else 180)
    hits = sim.all("P1", "impact")
    assert len(hits) == 2, f"{len(hits)} impactos"
    assert [h["startup"] for h in hits] == [10, 10], [h["startup"] for h in hits]
    print("ok  jab repetido sin cambiar de id: i10 y i10 (antes i10 e i43)")


def test_notation():
    assert load_notation(None) == {}
    b = load_notation("bryan")
    assert b[7] == "1" and b[10] == "2"
    sim = Sim()
    sim.frame(mid=7, s1=ATTACK)
    sim.frame(mid=7, s1=ATTACK, c1=2)
    assert sim.tt.t1.label_for(7) == "#7"
    print("ok  sin tabla -> id crudo (#7); con --p1-char bryan -> 1")


def test_hybrid_projection():
    """Proyección de tabla al instante + confirmación por memoria."""
    from lookup_engine import FrameTableLookup
    from tracker_hybrid import HybridTracker

    root = os.path.dirname(HERE)
    lookup = FrameTableLookup(os.path.join(root, "data"))
    assert lookup.load_character("bryan"), "falta data/compiled/bryan.json"

    hybrid = HybridTracker(lookup, "bryan", None)
    d = hybrid.project(1, 7)
    assert d.hit_type == "PREDICT" and not d.is_confirmed, d
    assert d.move_name == "1" and d.startup_str == "i10~", (d.move_name, d.startup_str)
    assert d.advantage_str == "+1~", d.advantage_str
    assert d.color_code == "gray"

    d = hybrid.confirm(d, 7, 10, 8, "HIT")
    assert d.is_confirmed and d.startup_str == "i10" and d.advantage_str == "+8", d
    assert d.color_code == "green", d.color_code

    # id sin mapear / personaje sin tabla: nunca inventa
    d = hybrid.project(1, 999)
    assert d.hit_type == "WAIT" and d.move_name == "#999", d
    d = HybridTracker(lookup, "nadie", None).project(1, 7)
    assert d.hit_type == "WAIT" and d.advantage_str == "—", d

    # `raw/` va por notación, no por Move ID: usarlo como ID daría una
    # predicción falsa (ID 1 -> jab).  Debe ignorarse para IDs.
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "raw"))
        with open(os.path.join(tmp, "raw", "pj_raw.json"), "w", encoding="utf-8") as fh:
            json.dump({"moves": {"1": {"startup": 10, "on_block": 1}}}, fh)
        raw_lookup = FrameTableLookup(tmp)
        assert raw_lookup.load_character("pj")
        assert not raw_lookup.is_id_indexed("pj")
        assert raw_lookup.get_move_info("pj", 1) is None
        d = HybridTracker(raw_lookup, "pj", None).project(1, 1)
        assert d.hit_type == "WAIT" and d.move_name == "#1", d
    print("ok  híbrido: tabla al instante (i10~ +1~) y memoria manda (i10 +8)")


def test_mapper_compile():
    """raw + mappings -> compiled (y --learn recompila en caliente)."""
    import json
    import tempfile

    import mapper

    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "raw"))
        os.makedirs(os.path.join(tmp, "mappings"))
        with open(os.path.join(tmp, "raw", "test_raw.json"), "w", encoding="utf-8") as fh:
            json.dump({"moves": {"f+2": {"startup": 14, "on_block": -3}}}, fh)
        mapper.add_mapping("test", 42, "f+2", tmp)
        assert mapper.compile_character_db("test", tmp, quiet=True)
        with open(os.path.join(tmp, "compiled", "test.json"), encoding="utf-8") as fh:
            compiled = json.load(fh)
        assert compiled["moves"]["42"]["notation"] == "f+2"
        assert compiled["moves"]["42"]["startup"] == 14

        # un id mapeado a una notación que el raw no tiene no se inventa
        mapper.add_mapping("test", 43, "no-existe", tmp)
        mapper.compile_character_db("test", tmp, quiet=True)
        with open(os.path.join(tmp, "compiled", "test.json"), encoding="utf-8") as fh:
            compiled = json.load(fh)
        assert "43" not in compiled["moves"]
    print("ok  mapper: Move ID -> notación -> frame data, sin inventar datos")


def test_states_config():
    """data/states.json: guardia agachada aprendida sin tocar código."""
    import tempfile

    import game_states

    with tempfile.TemporaryDirectory() as tmp:
        states = game_states.load_states(tmp)
        assert 4178 in states["block_states"] and 2114 in states["attack_states"]
        assert game_states.add_block_state(4182, tmp)
        assert not game_states.add_block_state(4182, tmp)
        assert 4182 in game_states.load_states(tmp)["block_states"]
    print("ok  estados de guardia configurables (4178 + 4182 aprendido)")


def test_learner_asks_and_saves():
    """--learn: un id desconocido se pregunta y se guarda en el mapa."""
    import json
    import tempfile

    import learn
    import mapper

    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "raw"))
        os.makedirs(os.path.join(tmp, "mappings"))
        with open(os.path.join(tmp, "raw", "pj_raw.json"), "w", encoding="utf-8") as fh:
            json.dump({"moves": {"f+2": {"startup": 14, "on_block": -3}}}, fh)

        learner = learn.Learner({"P1": "pj"}, data_dir=tmp, ask=lambda _p="": "f+2")
        learner._ask_move("pj", 99)
        learner.muted = True

        with open(os.path.join(tmp, "mappings", "pj_map.json"), encoding="utf-8") as fh:
            assert json.load(fh)["99"] == "f+2"
        assert mapper.compile_character_db("pj", tmp, quiet=True)
        with open(os.path.join(tmp, "compiled", "pj.json"), encoding="utf-8") as fh:
            assert json.load(fh)["moves"]["99"]["startup"] == 14
    print("ok  --learn guarda el ID, recompila y el HUD lo puede recargar")


def test_scraper_parse():
    """El scraper lee por cabeceras y cae al formato posicional si no hay."""
    import scraper

    html = """
    <table>
      <tr><th>Command</th><th>Hit level</th><th>Startup</th><th>Block</th><th>Hit</th></tr>
      <tr><td>1</td><td>h</td><td>i10</td><td>+1</td><td>+8</td></tr>
      <tr><td>3+4</td><td>h</td><td>i18</td><td>-8</td><td>KND</td></tr>
    </table>
    """
    moves = scraper.parse_html(html)
    assert moves["1"]["startup"] == 10 and moves["1"]["on_block"] == 1
    assert moves["1"]["on_hit"] == 8
    assert moves["3+4"]["startup"] == 18 and moves["3+4"]["on_hit"] == "KND"

    # sin cabecera reconocible: columnas del roadmap (notación, tipo, i, b, h)
    html = "<table><tr><td>f+2</td><td>m</td><td>i14</td><td>-3</td><td>+5</td></tr></table>"
    moves = scraper.parse_html(html)
    assert moves["f+2"]["startup"] == 14 and moves["f+2"]["on_block"] == -3
    print("ok  scraper: cabeceras (Command/Startup/Block/Hit) y fallback posicional")


if __name__ == "__main__":
    test_punish()
    test_clock_drift()
    test_late_hit_is_not_punish()
    test_prediction()
    test_one_block_one_punish()
    test_startup_from_counter()
    test_same_id_pressed_again()
    test_notation()
    test_hybrid_projection()
    test_mapper_compile()
    test_states_config()
    test_learner_asks_and_saves()
    test_scraper_parse()
    print("TODO OK")
