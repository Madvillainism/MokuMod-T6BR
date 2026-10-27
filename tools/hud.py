#!/usr/bin/env python3
"""Live Tekken 6 frame-data HUD (practice mode).

Reads both player structs straight out of PPSSPP's memory at ~1 kHz and paints
the result on a transparent, click-through window over the emulator.

Measurement model (hybrid: local table + live telemetry):

  * startup   = the game's own animation counter on the contact frame
  * advantage = P2 unlock frame - P1 unlock frame

The local database (`data/compiled/<char>.json`) is only a **prediction**: at
the frame the move id changes it paints the theoretical value (`i10~ +1~`,
grey, "TABLA").  Memory always has the last word: the impact replaces the
startup and the unlock replaces the advantage.

The counter (`+0x7C`) restarts at 1 on the first frame of every animation and
counts up one per game frame, so its value *is* the move's frame number.  That
is the reference used for the startup: counting samples instead made the
measurement drift (a jab on block came out as i11 when the move id appeared a
sample before the counter reset, and as i8 when the reader lost a frame).

where "unlock" is the `recovery tail` byte (+0x295) rising to 1 for each
player, i.e. the exact frame in which that player can act again.  The old
model used the end of the animation state, which is wrong: the animation keeps
playing ~12 frames after the character is already actionable.

Strings (Bryan `4,3`, ...) switch the move id *on* the contact frame, so every
contact is measured as its own record and a contact that is interrupted by a
new hit is published without a value (the string is still running).

Both trackers share ONE game-frame clock.  Each player's animation counter
repeats a value now and then, so a clock per tracker drifted apart and the
punish window (a P1 frame compared against a P2 frame) was garbage.

Move ids are per character, so nothing is assumed about them: without a table
the label is the raw id (`#7`) and the measurement is identical for every
character.  With `--p1-char/--p2-char` the label and the prediction come from
`data/` (scraper + mapper + `--learn`); `tools/notation.json` still works as a
manual fallback.

    python tools/hud.py
    python tools/hud.py --x 40 --y 40        # fixed position
    python tools/hud.py --debug              # also print to the console
    python tools/hud.py --p1-char bryan --p2-char bruce
    python tools/hud.py --no-predict         # no ~adelanto
    python tools/hud.py --learn              # learn unknown ids/states
    python tools/hud.py --settings           # F8 panel (position, opacity, font)
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = bool(getattr(sys, "frozen", False))
# En el .exe (PyInstaller) los datos viajan junto al ejecutable o en _MEIPASS.
ROOT = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(HERE)


def _resource_dir(name):
    """data/ dentro del .exe (onedir/onefile) o del repositorio."""
    for base in (ROOT, getattr(sys, "_MEIPASS", ROOT)):
        candidate = os.path.join(base, name)
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(ROOT, name)


DATA_DIR = _resource_dir("data")
CONFIG_PATH = os.path.join(ROOT, "config", "hud.json")

sys.path.insert(0, HERE)
import game_states  # noqa: E402
import psp_mem  # noqa: E402
from learn import Learner  # noqa: E402
from lookup_engine import FrameTableLookup  # noqa: E402
from tracker_hybrid import HybridTracker  # noqa: E402

# ---------------------------------------------------------------- addresses --
P1 = 0x08C078C0
P2 = 0x08C081F0
STRIDE = P2 - P1
SPAN = STRIDE + 0x930

OFF_STATE = 0x0AC
OFF_COUNTER = 0x07C
OFF_HEALTH = 0x002
OFF_MOVEID = 0x144
OFF_FREE = 0x294          # 1 = able to act, 0 = locked
OFF_TAIL = 0x295          # rises exactly on the actionable frame

# Move ids are **per character** (id 7 is Bryan's jab but a different move for
# anyone else), so no table is assumed by default: the HUD prints the raw id
# ("#7", "#21") and every number it shows is measured, never looked up.  That
# is what makes the tool work with any character out of the box.
#
# Optional labels live in tools/notation.json, one block per character:
#
#     {"bryan": {"7": "1", "10": "2", "14": "3"},
#      "bruce": {"7": "1", "8": "2"}}
#
# and are selected with --p1-char / --p2-char.  A few are built in below.
NOTATION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notation.json")
BUILTIN_NOTATION = {
    "bryan": {7: "1", 10: "2", 14: "3", 15: "4", 13: "1+2", 20: "3+4", 12: "4,3"},
}


def load_notation(name):
    """Id -> label table for `name`, or {} (raw ids) when unknown."""
    if not name:
        return {}
    table = dict(BUILTIN_NOTATION.get(name.strip().lower(), {}))
    try:
        with open(NOTATION_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        for k, v in (data.get(name.strip().lower()) or {}).items():
            table[int(k)] = str(v)
    except (OSError, ValueError, AttributeError):
        pass
    return table

# Estados de reacción.  4178 (blockstun de pie) está verificado; la guardia
# agachada y otras variantes se aprenden con --learn y se guardan en
# data/states.json, sin tocar el código.
_STATES = game_states.load_states()
IDLE_STATES = _STATES["idle_states"]
BLOCK_STATES = _STATES["block_states"]
ATTACK_STATES = _STATES["attack_states"]
BLOCK_STATE = 4178                  # alias histórico (blockstun de pie)
ATTACK_STATE = 2114                 # attack *and* hitstun: never used alone

# ------------------------------------------------------------------ colours --
BG_KEY = "#010203"
C_PLUS = "#00E676"
C_NEUTRAL = "#FFFFFF"
C_MINUS = "#FF6D00"
C_BAD = "#FF1744"
C_HIT = "#40C4FF"
C_DIM = "#9E9E9E"
C_P1 = "#80CBC4"
C_P2 = "#FFB74D"


# ------------------------------------------------------------------- memory --
class Reader:
    def __init__(self):
        self.h = None
        self.pid = None
        self.buf = None
        self.host = None
        self.game_id = None     # disc ID detectado en RAM (ULES01376, ...)
        self._id_probed = False
        self.dead = False       # emulator gone -> the overlay shuts itself down
        self.t0 = time.time()   # used for the "no emulator at all" grace period

    def _probe_game_id(self, base, size):
        try:
            self.game_id = psp_mem.detect_game_id(self.h, base, size)
        except BaseException:
            self.game_id = None

    def ensure(self):
        if self.h is not None:
            return True
        try:
            self.pid = psp_mem.find_pid()
            self.h = psp_mem.open_process(self.pid)
            self.buf = ctypes.create_string_buffer(SPAN)
            base, _size = psp_mem.find_ram(self.h)
            self.host = base + (P1 - psp_mem.PSP_RAM_START)
            if not self._id_probed:
                # 32 MB de escaneo: en un hilo aparte para no retrasar la
                # primera muestra de telemetría.
                self._id_probed = True
                threading.Thread(
                    target=self._probe_game_id, args=(base, _size), daemon=True
                ).start()
            return True
        except BaseException:
            self.h = None
            return False

    def sample(self):
        if self.h is None and not self.ensure():
            # Reconnecting failed.  If the process we were reading is gone,
            # the emulator was closed: leave `dead` set so the window closes
            # instead of leaving a frozen overlay on the desktop.  The same
            # happens if PPSSPP never appeared (stale overlay blocking the
            # single-instance mutex for the next run).
            if self.pid is None:
                self.dead = time.time() - self.t0 > 20
            elif not psp_mem.emulator_alive(self.pid):
                self.dead = True
            return None
        try:
            ok = psp_mem.read_into(self.h, self.host, self.buf)
        except BaseException:
            self.h = None
            return None
        if not ok:
            self.h = None
            return None
        d = self.buf.raw
        return {
            "c1": struct.unpack_from("<I", d, OFF_COUNTER)[0],
            "mid": struct.unpack_from("<I", d, OFF_MOVEID)[0],
            "free1": d[OFF_FREE],
            "tail1": d[OFF_TAIL],
            "s1": struct.unpack_from("<H", d, OFF_STATE)[0],
            "hp1": struct.unpack_from("<H", d, OFF_HEALTH)[0],
            "mid2": struct.unpack_from("<I", d, STRIDE + OFF_MOVEID)[0],
            "s2": struct.unpack_from("<H", d, STRIDE + OFF_STATE)[0],
            "c2": struct.unpack_from("<I", d, STRIDE + OFF_COUNTER)[0],
            "hp2": struct.unpack_from("<H", d, STRIDE + OFF_HEALTH)[0],
            "free2": d[STRIDE + OFF_FREE],
            "tail2": d[STRIDE + OFF_TAIL],
        }


def mirror(s):
    """Swap both players so the same Tracker can measure P2 attacking.

    P2's move id / counter / free flag become the attacker's fields and P1's
    state / counter / HP become the defender's.  Both trackers are stepped with
    the same explicit game frame (see TrackerThread.feed), so events from the
    two of them can be compared directly.
    """
    return {
        "c1": s["c2"],
        "mid": s["mid2"],
        "free1": s["free2"],
        "tail1": s["tail2"],
        "s2": s["s1"],
        "c2": s["c1"],
        "hp2": s["hp1"],
        "free2": s["free1"],
        "tail2": s["tail1"],
    }


# ------------------------------------------------------------------ tracker --
class Tracker:
    """Turns a stream of field samples into frame-data events.

    Pure and single-threaded: `step(sample)` returns the events produced by
    that sample.  That makes the exact same logic replayable offline against a
    saved recording (see tools/replay.py).
    """

    def __init__(self, notation=None, block_states=None, emit_start=False):
        self.notation = {} if notation is None else notation
        self.block_states = set(block_states or BLOCK_STATES)
        self.emit_start = emit_start   # avisa del Move ID en el frame 0
        self.frame = 0
        self.prev_c1 = None
        self.prev_tail1 = 0
        self.prev_tail2 = 0
        self.prev_hp = None
        self.hp_drop_frame = -999
        self.prev_s2 = None
        self.prev_c2 = None
        self.unknown_state = None  # reacción de guardia no catalogada (--learn)

        # Move-id debounce: the game blips some ids for a single sample
        # (e.g. 15 -> 16 -> 12 on the same frame) before settling.
        self.mid_cand = 0
        self.mid_count = 0
        self.mid_frame = 0
        self.mid_c1 = None        # counter on the sample the id first appeared
        self.mid = 0

        self.segments = []        # [(start_frame, label)] current animation
        self.seg_start = None
        self.seg_c0 = None        # animation counter on the segment's 1st frame
        self.contact = None       # measurement in progress
        self.last_c1 = None       # raw counter of the previous sample

    # -- helpers --------------------------------------------------------
    def label_for(self, mid):
        # Unknown characters keep the raw id: the measurement is universal,
        # only the pretty name depends on the character's move list.
        return self.notation.get(mid) or f"#{mid}"

    def pick_segment(self, impact):
        """Segment a contact belongs to.

        The game switches the move id *on* the contact frame for some hits
        (strings); when that happens the hit belongs to the previous segment.
        """
        if not self.segments:
            return (impact, "?")
        seg = self.segments[-1]
        if len(self.segments) >= 2 and impact - seg[0] < 3:
            seg = self.segments[-2]
        return seg

    def event(self, phase, contact, adv):
        # The startup comes from the animation counter when the segment was
        # timed with it; the clock difference is only the fallback for moves
        # whose counter did not restart (rare cancels).
        startup = contact.get("startup")
        if startup is None:
            startup = max(1, contact["impact"] - contact["start"] + 1)
        return {
            "phase": phase,
            "frame": self.frame,
            "impact": contact["impact"],
            "start": contact["start"],
            "startup": startup,
            "adv": adv,
            "kind": contact["kind"],
            "label": contact["label"],
            "mid": contact.get("mid", 0),
            "p1u": contact.get("p1u"),
            "p2u": contact.get("p2u"),
            "punished": bool(contact.get("punished")),
        }

    def reset_state(self):
        self.segments = []
        self.seg_start = None
        self.seg_c0 = None
        self.contact = None

    # -- main -----------------------------------------------------------
    def step(self, s, frame=None):
        """Advance one sample.

        `frame` lets several trackers share a single game-frame clock.  That is
        essential for comparing events from two trackers: each player's
        animation counter repeats a value now and then, so a clock driven by
        one counter per tracker drifts apart over a session and every
        cross-tracker comparison (the punish window) becomes meaningless.
        """
        events = []

        if frame is not None:
            self.frame = frame
        elif s["c1"] != self.prev_c1:
            self.frame += 1
            self.prev_c1 = s["c1"]

        # ---- move id, debounced ---------------------------------------
        raw = s["mid"]
        c1 = s["c1"]
        # The game restarts the animation counter at 1 on the first frame of
        # every animation, so a counter that goes back is a brand-new move.
        c_reset = self.last_c1 is not None and c1 < self.last_c1 and c1 <= 2
        mid_changed = False

        if raw == self.mid_cand:
            self.mid_count += 1
        else:
            self.mid_cand = raw
            self.mid_count = 1
            self.mid_frame = self.frame
            self.mid_c1 = c1

        # accept once it survives a second sample or crosses into a new
        # frame; the segment starts on the frame the id first appeared
        if raw != self.mid and (self.mid_count >= 2 or self.frame > self.mid_frame):
            self.mid = raw
            mid_changed = True
            if raw:
                start = self.mid_frame
                if self.seg_start is None:
                    self.segments = [(start, self.label_for(raw))]
                else:
                    self.segments.append((start, self.label_for(raw)))
                    del self.segments[:-4]
                self.seg_start = start
                # Counter-timed only when the animation restarted with the id:
                # then its value is the move's frame number (1, 2, 3, ...).
                # The counter is read on the sample the id *appeared*, not on
                # the sample the debounce accepted it (already +1).
                self.seg_c0 = self.mid_c1 if (self.mid_c1 or 99) <= 3 else None
                if self.emit_start:
                    # Frame 0 del movimiento: el motor híbrido proyecta la
                    # tabla aquí, antes de que exista ningún impacto.
                    events.append({
                        "phase": "start",
                        "frame": self.frame,
                        "start": start,
                        "impact": None,
                        "startup": None,
                        "adv": None,
                        "kind": "",
                        "label": self.label_for(raw),
                        "mid": raw,
                    })
            else:
                self.seg_start = None
                self.seg_c0 = None
                if self.contact is None:
                    self.segments = []

        if c_reset and self.seg_start is not None:
            if self.mid and not mid_changed:
                # Same move id pressed again (jab -> jab): the id never goes
                # back to 0, only the counter restarts.  Without this the
                # second jab was measured from the first one (i43, i45...).
                self.seg_start = self.frame
                self.segments.append((self.frame, self.label_for(self.mid)))
                del self.segments[:-4]
            self.seg_c0 = c1

        # ---- contacts --------------------------------------------------
        # Two unambiguous signals:
        #   * a hit lowers the defender's HP on the exact frame the hitbox
        #     connects (the reaction state arrives one frame later);
        #   * a block switches the defender's state to blockstun (4178) on
        #     that same frame.
        # `state != idle` is NOT usable: 2114 is attack *and* hitstun, and the
        # game writes the state a few samples before the move id, so when the
        # *defender* started his own move it looked like a reaction (that was
        # the bug that corrupted the numbers after every jab).
        hp_drop = self.prev_hp is not None and s["hp2"] < self.prev_hp
        if hp_drop:
            self.hp_drop_frame = self.frame

        d_state = s["s2"]
        d_anim = s["c2"] == 1 and self.prev_c2 not in (1, None)
        d_new = self.prev_s2 is not None and d_state != self.prev_s2

        hit = hp_drop
        blocked = d_state in self.block_states and (d_new or d_anim)

        # Reacción de guardia no catalogada (p. ej. guardia agachada): el
        # contador del defensor reinicia dentro de un movimiento activo y el
        # estado no es idle/ataque/guardia conocida.  El HUD con --learn lo
        # ofrece para añadirlo a data/states.json.
        self.unknown_state = None
        if (self.seg_start is not None and d_anim and not hit
                and d_state not in self.block_states
                and d_state not in IDLE_STATES
                and d_state not in ATTACK_STATES):
            self.unknown_state = d_state

        if self.seg_start is not None and (hit or blocked):
            impact = self.frame
            self.contact = None      # a new hit means the string goes on
            seg = self.pick_segment(impact)
            # Startup: the animation counter is the move's frame number, so it
            # does not care about samples the reader lost nor about the move id
            # appearing one sample before the counter restarts.  Both used to
            # shift the number by +-1 (block read i11, hit read i8).
            # Exception: when the counter restarts *on* the contact (the next
            # hit of a string), its value is 1 and the clock is the reference.
            startup = None
            if not c_reset and self.seg_c0 is not None and self.seg_c0 <= c1 <= 300:
                startup = c1 - self.seg_c0 + 1
            self.contact = {
                "impact": impact,
                "start": seg[0],
                "startup": startup,
                "label": seg[1],
                "mid": s["mid"],
                "kind": "HIT" if hit else "BLOCK",
                "hp_pre": self.prev_hp,
                "hp_min": s["hp2"],
                "p1u": None,
                "p2u": None,
            }
            events.append(self.event("impact", self.contact, None))

        # ---- unlocks ---------------------------------------------------
        c = self.contact
        if c is not None:
            if s["hp2"] < c["hp_min"]:
                c["hp_min"] = s["hp2"]
            if c["p1u"] is None and s["tail1"] == 1 and self.prev_tail1 == 0:
                c["p1u"] = self.frame
            if c["p2u"] is None and s["tail2"] == 1 and self.prev_tail2 == 0:
                c["p2u"] = self.frame
            if c["p1u"] is not None and c["p2u"] is not None:
                if c["hp_pre"] is not None and c["hp_min"] < c["hp_pre"]:
                    c["kind"] = "HIT"
                events.append(self.event("final", c, c["p2u"] - c["p1u"]))
                self.contact = None
            elif self.frame - c["impact"] > 120:
                self.contact = None

        self.prev_s2 = d_state
        self.prev_c2 = s["c2"]
        self.prev_tail1 = s["tail1"]
        self.prev_tail2 = s["tail2"]
        self.prev_hp = s["hp2"]
        self.last_c1 = c1
        return events


class TrackerThread(threading.Thread):
    """Measures both sides on one shared game-frame clock.

    The two trackers used to count frames with their own player's animation
    counter.  Those counters repeat a value now and then, so the clocks drifted
    apart (measured: ~17 frames after a few minutes) and the punish window -
    which compares a P1 frame against a P2 frame - was meaningless.  A single
    clock feeds both trackers now, so every cross-side comparison is exact.
    """

    WINDOW = 60          # sanity bound between a block and the punish attempt
    CACHE_TTL = 120.0    # seconds a measured value is reused as a prediction

    def __init__(self, out, debug=False, p1_notation=None, p2_notation=None,
                 predict=True, block_states=None, hybrid=None, learner=None,
                 data_dir=None):
        super().__init__(daemon=True)
        self.out = out
        self.debug = debug
        self.predict = predict
        self.data_dir = data_dir
        self.rd = Reader()
        self.block_states = set(block_states or BLOCK_STATES)
        self.t1 = Tracker(p1_notation, self.block_states, emit_start=True)
        self.t2 = Tracker(p2_notation, self.block_states, emit_start=True)
        self.hybrid = hybrid        # proyección de tabla + confirmación
        self.learner = learner      # modo --learn (IDs y estados nuevos)
        self.display = {"P1": None, "P2": None}
        self.running = True
        self.replay_done = False   # modo --replay: la demo terminó
        self.clock = 0
        self.clock_c = None
        self.last_block = None   # last BLOCK exchange, kept after its final
        self.cache = {}          # (move id, kind) -> (advantage, timestamp)

    def reload_states(self):
        """Recarga data/states.json (--learn añadió una guardia nueva)."""
        self.block_states = game_states.load_states(self.data_dir)["block_states"]
        for tracker in (self.t1, self.t2):
            tracker.block_states = self.block_states

    # -- punish ---------------------------------------------------------
    def punish_reason(self, ev):
        """Is `ev` a punish?  Returns "ok" or the reason it is not.

        A punish is a hit that lands *while the dummy is still recovering* from
        the move it used, and that started after P1 was free to act.  Both
        frames come from the shared clock, so they are directly comparable.
        """
        lb = self.last_block
        if lb is None:
            return "sin bloqueo previo"
        if lb["impact"] >= ev["impact"]:
            return "bloqueo posterior"
        if ev["impact"] - lb["impact"] > self.WINDOW:
            return f"bloqueo viejo (+{ev['impact'] - lb['impact']}f)"
        if ev["kind"] != "HIT":
            return f"no conecto ({ev['kind']})"
        u_p = lb.get("p2u")      # P1 free (defender in the mirror)
        u_d = lb.get("p1u")      # dummy free (attacker in the mirror)
        if u_d is None and self.t2.contact is not lb:
            # The block contact was dropped (new hit of the string / timeout)
            # without ever seeing the dummy unlock: we cannot claim anything.
            return "bloqueo sin liberar"
        if u_p is not None and ev["start"] < u_p:
            return f"empezo en stun ({ev['start']}<{u_p})"
        if u_d is not None:
            if ev["impact"] > u_d + 1:
                return f"tarde ({ev['impact']}>{u_d}+1)"
            if u_p is not None and ev["startup"] > abs(u_p - u_d) + 1:
                return f"i{ev['startup']} no cabe en {u_p - u_d:+d}"
        return "ok"

    # -- fast feedback --------------------------------------------------
    def _key(self, ev):
        # The move id is per character, so the side goes in the key: P1 and P2
        # can be different characters and share ids.
        return (ev["side"], ev["mid"], ev["kind"])

    def predicted(self, ev):
        """Last measured value for this move, shown while the new one is
        being measured (the exact number replaces it a few frames later)."""
        if not self.predict:
            return None
        item = self.cache.get(self._key(ev))
        if item is None:
            return None
        adv, when = item
        return adv if time.time() - when <= self.CACHE_TTL else None

    def remember(self, ev):
        adv = ev["adv"]
        if adv is not None and abs(adv) <= 40:
            self.cache[self._key(ev)] = (adv, time.time())
            return adv
        return self.predicted(ev)

    # -- output ---------------------------------------------------------
    def emit(self, ev):
        self.out.put(ev)
        if not self.debug:
            return
        if ev["phase"] == "start":
            display = ev.get("display")
            hint = ""
            if display is not None and display.hit_type == "PREDICT":
                hint = f"  tabla {display.startup_str} {display.advantage_str}"
            print(f"  f{ev['frame']:6d} {ev['side']} start  {ev['label']:5s}{hint}",
                  flush=True)
            return
        adv = "-" if ev["adv"] is None else f"{ev['adv']:+d}"
        pred = "" if ev.get("predict") is None else f" ~{ev['predict']:+d}"
        tag = ""
        if ev.get("punish"):
            tag = "  PUNISH"
        elif ev["phase"] == "impact" and ev.get("punish_why") not in (None, "ok"):
            tag = f"  ({ev['punish_why']})"
        print(
            f"  f{ev['frame']:6d} {ev['side']} {ev['phase']:6s} {ev['label']:5s} "
            f"i{ev['startup']:<3d}{adv:>4} {ev['kind']:5s}{pred}{tag}"
            f"  [{ev['start']}->{ev['impact']}]",
            flush=True,
        )

    def tick(self, s):
        """Advance the shared clock by the game frames this sample represents.

        The counter changes once per game frame, so the *difference* is the
        number of frames that went by.  Counting changes instead lost every
        frame the reader missed (measured gaps of 2-3 frames made a jab come
        out as i8).  A counter that goes backwards is a new animation: one
        frame.
        """
        c1 = s["c1"]
        if c1 != self.clock_c:
            if self.clock_c is None:
                self.clock += 1
            else:
                d = c1 - self.clock_c
                self.clock += d if 1 <= d <= 10 else 1
            self.clock_c = c1
        return self.clock

    def _project(self, ev):
        """Fase 1 del motor híbrido: dato de tabla en el frame 0 del movimiento."""
        side = ev["side"]
        player = 1 if side == "P1" else 2
        display = None
        if self.hybrid is not None:
            display = self.hybrid.project(player, ev["mid"])
        self.display[side] = display
        ev["display"] = display
        ev["predict"] = None
        if self.learner is not None:
            self.learner.offer_move(side, ev["mid"])

    def _confirm(self, ev):
        """Fase 2: la medición de memoria sustituye al dato proyectado."""
        if self.hybrid is None:
            return
        side = ev["side"]
        adv = ev["adv"] if ev["phase"] == "final" else None
        kind = "PUNISH" if ev.get("punish") else ev["kind"]
        display = self.hybrid.confirm(
            self.display.get(side), ev["mid"], ev["startup"], adv, kind, side
        )
        if display.hit_type not in ("HIT", "BLOCK", "PUNISH"):
            # Golpe que derriba (KND): la tabla pone la etiqueta y el número
            # medido (tiempo de levantarse) no se publica como ventaja.
            ev["kind"] = display.hit_type
            if ev["phase"] == "final":
                ev["adv"] = None
        ev["display"] = display

    def _learn_state(self, state):
        if self.learner is not None and state:
            self.learner.offer_state(state)

    def feed(self, s):
        """Process one sample: advance the clock and run both trackers.

        Split out of `run()` so the offline logger (tools/diag.py) can drive the
        exact same pipeline.
        """
        # One clock for both sides.  P1's animation counter changes exactly
        # once per game frame and freezes only when the game does.
        self.tick(s)

        # P1 as attacker.
        for ev in self.t1.step(s, self.clock):
            ev["side"] = "P1"
            if ev["phase"] == "start":
                self._project(ev)
            elif ev["phase"] == "impact":
                why = self.punish_reason(ev)
                ev["punish"] = why == "ok"
                ev["punish_why"] = why
                ev["predict"] = self.predicted(ev)
                c2 = self.t2.contact
                if c2 is not None and c2["kind"] == "BLOCK" and ev["kind"] == "HIT":
                    # P1's hit cut the dummy's recovery short, so its block
                    # measurement is no longer the block value (it used to
                    # publish nonsense like -55 from a spurious unlock).
                    c2["punished"] = True
                    # One block, one punish: a combo hit must not be flagged
                    # again against the same exchange.
                    self.last_block = None
                self._confirm(ev)
            else:
                ev["predict"] = self.remember(ev)
                self._confirm(ev)
            self.emit(ev)
        self._learn_state(self.t1.unknown_state)

        # P2 as attacker (mirrored): the defense side.
        for ev in self.t2.step(mirror(s), self.clock):
            ev["side"] = "P2"
            if ev["phase"] == "start":
                self._project(ev)
            elif ev["phase"] == "impact":
                ev["predict"] = self.predicted(ev)
                if ev["kind"] == "BLOCK":
                    self.last_block = self.t2.contact
                self._confirm(ev)
            else:
                # Drop the interrupted value *before* caching it: its recovery
                # was cut short, so it is not this move's real advantage.
                if ev["punished"]:
                    ev["adv"] = None
                ev["predict"] = self.remember(ev)
                self._confirm(ev)
            self.emit(ev)
        self._learn_state(self.t2.unknown_state)

    def run(self):
        boost_timer()
        fails = 0
        while self.running:
            s = self.rd.sample()
            if s is None:
                # A single failed read must not look like a new contact: keep
                # the previous field values and only reset after a real gap.
                fails += 1
                if fails >= 20:
                    for t in (self.t1, self.t2):
                        t.prev_c1 = None
                        t.prev_s2 = None
                        t.prev_c2 = None
                    fails = 0
                time.sleep(0.05)
                continue
            fails = 0
            self.feed(s)
            time.sleep(0.0005)


# ------------------------------------------------------------------ overlay --
def find_game_window(pid):
    """Largest visible top-level window owned by `pid` -> (x, y, w, h)."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        r = ctypes.create_string_buffer(16)
        user32.GetClientRect(hwnd, r)
        w, h = struct.unpack_from("<ii", r.raw, 8)
        if w > 200 and h > 150:
            pt2 = ctypes.create_string_buffer(8)
            struct.pack_into("<ii", pt2, 0, 0, 0)
            user32.ClientToScreen(hwnd, pt2)
            x, y = struct.unpack("<ii", pt2.raw)
            found.append((w * h, hwnd, (x, y, w, h)))
        return True

    user32.EnumWindows(cb, 0)
    if not found:
        return None
    found.sort(reverse=True)
    return found[0][2]


def make_click_through(root):
    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    try:
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd,
            GWL_EXSTYLE,
            ex | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW,
        )
    except Exception:
        pass


def colour(adv, kind):
    if kind == "HIT" and adv is not None and adv >= 0:
        return C_HIT
    if adv is None:
        return C_DIM
    if adv > 0:
        return C_PLUS
    if adv == 0:
        return C_NEUTRAL
    if adv >= -9:
        return C_MINUS
    return C_BAD


def boost_timer():
    """Ask Windows for a 1 ms timer resolution.

    Without it `time.sleep(0.0005)` really sleeps ~15.6 ms on Windows: the
    reader sampled *slower* than the game and lost whole frames (the log shows
    2-3 frame holes), which corrupted every measurement that counts frames.
    """
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass


def enable_dpi_awareness():
    """Make Windows report real pixels to this process.

    Without this, Tk places the window using physical pixels while
    GetWindowRect/ClientToScreen (DPI-unaware) report virtual ones, so on a
    150% display the overlay lands 1.5x off target (over the round timer).
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def single_instance(name="Local\\T6FramesHud"):
    """Hold a named mutex so a second copy cannot stack a duplicate overlay."""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    h = kernel32.CreateMutexW(None, False, name)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return h


# ------------------------------------------------------------------- config --
DEFAULT_CONFIG = {
    "x": None,
    "y": None,
    "opacity": 100,
    "font_size": 28,
    "always_on_top": True,
    "hotkey_settings": "F8",
}

VK = {"F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for key in DEFAULT_CONFIG:
                if key in data:
                    cfg[key] = data[key]
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except OSError as exc:
        print(f"[config] no se pudo guardar: {exc}", file=sys.stderr)


def hotkey_down(name):
    """True una sola vez por pulsación (borde) para la tecla indicada."""
    vk = VK.get(str(name).upper())
    if not vk:
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x0001)


def notation_for(char, lookup):
    """Etiquetas: tools/notation.json + las del catálogo compilado."""
    table = load_notation(char)
    if char and lookup is not None and lookup.has_character(char):
        key = char.strip().lower()
        source = lookup.sources.get(key, "")
        # `data/raw/` está indexado por NOTACIÓN, no por Move ID: usarlo como
        # etiqueta pondría el ID 1 como "1" (coincidencia falsa).
        if not source.endswith("_raw.json"):
            for move_id, info in lookup.tables[key].items():
                if info.get("notation") and str(move_id).isdigit():
                    table[int(move_id)] = str(info["notation"])
    return table


def load_recording(name, cache_dir=None):
    """Carga una grabación de `.cache/in/<nombre>.bin` (+ `.json`) como muestras.

    Formato: `inputrec.py --bin` escribe el bloque de memoria de cada muestra y
    un JSON con los tiempos.  Lo usan `tools/replay.py` y `hud.py --replay`.
    """
    base_dir = cache_dir or os.path.join(ROOT, ".cache", "in")
    base = os.path.join(base_dir, str(name).removesuffix(".bin"))
    with open(base + ".json", encoding="utf-8") as fh:
        meta = json.load(fh)
    span = meta["meta"]["span"]
    samples = meta["samples"]
    with open(base + ".bin", "rb") as fh:
        raw = fh.read()
    out = []
    for i in range(len(raw) // span):
        d = raw[i * span:(i + 1) * span]
        out.append((
            samples[i][0],
            {
                "c1": struct.unpack_from("<I", d, OFF_COUNTER)[0],
                "mid": struct.unpack_from("<I", d, OFF_MOVEID)[0],
                "free1": d[OFF_FREE],
                "tail1": d[OFF_TAIL],
                "s1": struct.unpack_from("<H", d, OFF_STATE)[0],
                "hp1": struct.unpack_from("<H", d, OFF_HEALTH)[0],
                "mid2": struct.unpack_from("<I", d, STRIDE + OFF_MOVEID)[0],
                "s2": struct.unpack_from("<H", d, STRIDE + OFF_STATE)[0],
                "c2": struct.unpack_from("<I", d, STRIDE + OFF_COUNTER)[0],
                "hp2": struct.unpack_from("<H", d, STRIDE + OFF_HEALTH)[0],
                "free2": d[STRIDE + OFF_FREE],
                "tail2": d[STRIDE + OFF_TAIL],
            },
        ))
    return out


def run_replay(tracker, name, speed=1.0):
    """Alimenta el tracker con una grabación, a velocidad real, sin PPSSPP."""
    boost_timer()
    samples = load_recording(name)
    t0 = time.time()
    for t, sample in samples:
        if speed > 0:
            delay = t0 + (t / speed) - time.time()
            if delay > 0:
                time.sleep(delay)
        tracker.feed(sample)
    time.sleep(2.5)          # deja ver el último resultado
    tracker.replay_done = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", type=int, default=None)
    ap.add_argument("--y", type=int, default=None)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--no-click-through", action="store_true")
    ap.add_argument("--p1-char", default=None,
                    help="personaje de P1 (tabla de datos y notación: bryan, jin...)")
    ap.add_argument("--p2-char", default=None,
                    help="personaje de P2")
    ap.add_argument("--no-predict", action="store_true",
                    help="no adelantar el valor medido la última vez")
    ap.add_argument("--no-table", action="store_true",
                    help="solo telemetría: no proyectar la tabla teórica")
    ap.add_argument("--data-dir", default=None,
                    help="carpeta data/ (raw, mappings, compiled, states.json)")
    ap.add_argument("--learn", action="store_true",
                    help="aprender IDs y estados nuevos por consola")
    ap.add_argument("--settings", action="store_true",
                    help="abrir el panel de ajustes (también con F8)")
    ap.add_argument("--replay", default=None,
                    help="reproducir una grabación de .cache/in/ sobre el overlay "
                         "(no necesita PPSSPP)")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="velocidad del --replay (2 = doble, 0 = sin esperas)")
    a = ap.parse_args()

    enable_dpi_awareness()

    lock = single_instance()
    if lock is None:
        print("overlay already running", file=sys.stderr)
        return

    cfg = load_config()
    if a.x is not None:
        cfg["x"] = a.x
    if a.y is not None:
        cfg["y"] = a.y

    data_dir = a.data_dir or DATA_DIR
    states = game_states.load_states(data_dir)
    lookup = FrameTableLookup(data_dir)
    for char in (a.p1_char, a.p2_char):
        if char:
            lookup.load_character(char)
    hybrid = None if a.no_table else HybridTracker(lookup, a.p1_char, a.p2_char)

    results: queue.Queue = queue.Queue()
    holder = {}

    def on_learn(kind, value):
        if kind == "move":
            lookup.reload(value)
        else:
            tracker = holder.get("tracker")
            if tracker is not None:
                tracker.reload_states()

    learner = None
    if a.learn:
        if not (a.p1_char or a.p2_char):
            print("[learn] --learn necesita --p1-char y/o --p2-char", file=sys.stderr)
        else:
            learner = Learner(
                {"P1": a.p1_char, "P2": a.p2_char},
                data_dir=data_dir, lookup=lookup, on_change=on_learn,
            )

    tracker = TrackerThread(
        results,
        debug=a.debug,
        p1_notation=notation_for(a.p1_char, lookup),
        p2_notation=notation_for(a.p2_char, lookup),
        predict=not a.no_predict,
        block_states=states["block_states"],
        hybrid=hybrid,
        learner=learner,
        data_dir=data_dir,
    )
    holder["tracker"] = tracker
    if a.replay:
        # Modo demo: la grabación sustituye a la lectura de memoria.
        tracker.replay_done = False
        threading.Thread(
            target=run_replay, args=(tracker, a.replay, a.speed), daemon=True
        ).start()
    else:
        tracker.start()

    root = tk.Tk()
    root.title("T6 frames")
    root.overrideredirect(True)
    root.attributes("-topmost", bool(cfg.get("always_on_top", True)))
    root.attributes("-transparentcolor", BG_KEY)
    root.configure(bg=BG_KEY)
    root.attributes("-alpha", max(30, int(cfg.get("opacity", 100))) / 100.0)

    f_big = tkfont.Font(family="Consolas", size=int(cfg.get("font_size", 28)),
                        weight="bold")
    f_small = tkfont.Font(family="Consolas", size=12, weight="bold")

    W, H = 620, 96
    cv = tk.Canvas(root, width=W, height=H, bg=BG_KEY, highlightthickness=0, bd=0)
    cv.pack()

    def pair(x, y, s, font, fill, anchor="w"):
        """Text drawn twice: 2 px offset black copy for contrast over the game."""
        sid = cv.create_text(x + 2, y + 2, text=s, font=font, fill="#000000", anchor=anchor)
        mid = cv.create_text(x, y, text=s, font=font, fill=fill, anchor=anchor)
        return (sid, mid)

    def put(p, s, fill=None):
        sid, mid = p
        if cv.itemcget(mid, "text") != s:
            cv.itemconfigure(sid, text=s)
            cv.itemconfigure(mid, text=s)
        if fill:
            cv.itemconfigure(mid, fill=fill)

    rows = {}
    for i, (side, tint) in enumerate((("P1", C_P1), ("P2", C_P2))):
        y = 6 + i * 44
        cell = {}
        cell["bar"] = cv.create_rectangle(10, y + 4, 14, y + 36, fill=tint, outline="")
        cv.create_text(25, y + 21, text=side, font=f_small, fill="#000000", anchor="w")
        cell["side"] = cv.create_text(23, y + 19, text=side, font=f_small, fill=tint, anchor="w")
        cell["move"] = pair(74, y + 19, "", f_small, C_DIM)
        cell["startup"] = pair(300, y + 21, "", f_big, "#F2F2F2", anchor="e")
        cell["value"] = pair(410, y + 21, "", f_big, C_NEUTRAL, anchor="e")
        cell["kind"] = pair(426, y + 19, "", f_small, C_DIM)
        cell["until"] = 0.0
        cell["punish"] = False
        rows[side] = cell

    def place():
        if cfg.get("x") is not None and cfg.get("y") is not None:
            return int(cfg["x"]), int(cfg["y"])
        if a.replay:
            return (root.winfo_screenwidth() - W) // 2, (root.winfo_screenheight() - H) // 2
        pid = tracker.rd.pid
        if pid:
            rect = find_game_window(pid)
            if rect:
                gx, gy, gw, gh = rect
                return gx + (gw - W) // 2, gy + int(gh * 0.19)
        return 80, 80

    x, y = place()
    root.geometry(f"{W}x{H}+{x}+{y}")

    if not a.no_click_through:
        root.update_idletasks()
        make_click_through(root)

    def clear(cell):
        for k in ("move", "startup", "value"):
            put(cell[k], "")
        put(cell["kind"], "", C_DIM)
        cell["punish"] = False
        cell["until"] = 0.0

    def poll():
        if tracker.replay_done:
            # Demo terminada: cerrar el overlay (no hay PPSSPP que seguir).
            root.destroy()
            return
        if tracker.rd.dead:
            # The emulator closed: take the overlay with it.
            root.destroy()
            return
        now = time.time()
        try:
            while True:
                res = results.get_nowait()
                side = res.get("side", "P1")
                other = "P2" if side == "P1" else "P1"
                cell = rows[side]
                oc = rows[other]
                adv = res.get("adv")
                phase = res["phase"]
                display = res.get("display")

                if phase == "start":
                    # Fase 1 del motor híbrido: dato de tabla al instante.
                    cell["punish"] = False
                    put(cell["move"], res.get("label", ""), C_DIM)
                    if display is not None and display.hit_type == "PREDICT":
                        put(cell["startup"], display.startup_str, C_DIM)
                        put(cell["value"], display.advantage_str, C_DIM)
                        put(cell["kind"], "TABLA", C_DIM)
                    else:
                        put(cell["startup"], "", C_DIM)
                        put(cell["value"], "—", C_DIM)
                        put(cell["kind"], "", C_DIM)
                elif phase == "impact":
                    cell["punish"] = bool(res.get("punish"))
                    put(cell["move"], res.get("label", ""), C_DIM)
                    startup_txt = display.startup_str if display else f"i{res['startup']}"
                    put(cell["startup"], startup_txt, "#F2F2F2")
                    pred = res.get("predict")
                    # The exact advantage is only known when the *later* player
                    # unlocks (~0.5 s).  Until then show the value measured the
                    # last time for this move, marked with "~".
                    if pred is None:
                        put(cell["value"], "—", C_DIM)
                    else:
                        put(cell["value"], f"~{pred:+d}", C_DIM)
                    if cell["punish"]:
                        put(cell["kind"], "PUNISH", C_BAD)
                    else:
                        put(cell["kind"], res.get("kind", ""), C_DIM)
                elif phase == "final":
                    qualitative = (display is not None
                                   and display.hit_type not in
                                   ("HIT", "BLOCK", "PUNISH", "PREDICT", "WAIT"))
                    if adv is None and res.get("predict") is not None:
                        put(cell["value"], f"~{res['predict']:+d}", C_DIM)
                    else:
                        put(cell["value"], "—" if adv is None else f"{adv:+d}",
                            colour(adv, res.get("kind")))
                    if cell["punish"]:
                        put(cell["kind"], "PUNISH", C_BAD)
                    elif qualitative:
                        put(cell["kind"], display.hit_type, C_HIT)
                    else:
                        put(cell["kind"], res.get("kind", ""), "#B0BEC5")
                    # Both sides of the coin: the defender sees the opposite
                    # sign of the same exchange (`P1 +6` / `P2 -6`).
                    # A row flagged PUNISH keeps its own punish value instead
                    # of being overwritten by the next exchange.
                    if adv is not None and not oc["punish"]:
                        put(oc["value"], f"{-adv:+d}", colour(-adv, res.get("kind")))
                        put(oc["kind"], res.get("kind", ""), C_DIM)
                        oc["until"] = now + 5.0
                    elif qualitative and not oc["punish"]:
                        # Derribo: el rival tampoco tiene una ventaja numérica.
                        put(oc["value"], "—", C_DIM)
                        put(oc["kind"], display.hit_type, C_DIM)
                        oc["until"] = now + 5.0
                cell["until"] = now + 5.0
        except queue.Empty:
            pass
        for c in rows.values():
            if c["until"] and now > c["until"]:
                clear(c)
        root.after(25, poll)

    def reposition():
        if cfg.get("x") is None:
            nx, ny = place()
            root.geometry(f"+{nx}+{ny}")
        root.after(700, reposition)

    # ---- panel de ajustes (F8 o --settings) -----------------------------
    settings = {"win": None}
    ui = {"hotkey": None}

    def apply_settings(*_):
        cfg["opacity"] = int(sv_opacity.get())
        cfg["font_size"] = int(sv_font.get())
        cfg["always_on_top"] = bool(sv_top.get())
        if ui["hotkey"] is not None:
            cfg["hotkey_settings"] = ui["hotkey"].get()
        root.attributes("-alpha", max(30, cfg["opacity"]) / 100.0)
        root.attributes("-topmost", cfg["always_on_top"])
        f_big.configure(size=cfg["font_size"])
        raw_x = sv_x.get().strip()
        raw_y = sv_y.get().strip()
        cfg["x"] = int(raw_x) if raw_x.lstrip("-").isdigit() else None
        cfg["y"] = int(raw_y) if raw_y.lstrip("-").isdigit() else None
        if cfg["x"] is not None and cfg["y"] is not None:
            root.geometry(f"+{cfg['x']}+{cfg['y']}")

    def close_settings():
        win = settings["win"]
        if win is not None:
            win.withdraw()

    def open_settings():
        if settings["win"] is not None:
            settings["win"].deiconify()
            settings["win"].lift()
            return
        win = tk.Toplevel(root)
        settings["win"] = win
        win.title("FRAMEMOD - Ajustes")
        win.geometry("380x360+60+60")
        win.attributes("-topmost", True)
        win.configure(bg="#202020")
        win.protocol("WM_DELETE_WINDOW", close_settings)

        fg = "#E0E0E0"
        sv_x.set("" if cfg.get("x") is None else str(cfg["x"]))
        sv_y.set("" if cfg.get("y") is None else str(cfg["y"]))
        sv_opacity.set(int(cfg.get("opacity", 100)))
        sv_font.set(int(cfg.get("font_size", 28)))
        sv_top.set(bool(cfg.get("always_on_top", True)))

        tk.Label(win, text="Posición X / Y (vacío = automática)", bg="#202020",
                 fg=fg, anchor="w").pack(fill="x", padx=12, pady=(10, 2))
        row = tk.Frame(win, bg="#202020")
        row.pack(fill="x", padx=12)
        tk.Entry(row, textvariable=sv_x, width=8).pack(side="left")
        tk.Entry(row, textvariable=sv_y, width=8).pack(side="left", padx=8)

        tk.Label(win, text="Opacidad (%)", bg="#202020", fg=fg,
                 anchor="w").pack(fill="x", padx=12, pady=(10, 0))
        tk.Scale(win, from_=30, to=100, orient="horizontal", variable=sv_opacity,
                 bg="#202020", fg=fg, highlightthickness=0).pack(fill="x", padx=12)

        tk.Label(win, text="Tamaño de fuente", bg="#202020", fg=fg,
                 anchor="w").pack(fill="x", padx=12)
        tk.Scale(win, from_=14, to=44, orient="horizontal", variable=sv_font,
                 bg="#202020", fg=fg, highlightthickness=0).pack(fill="x", padx=12)

        tk.Checkbutton(win, text="Siempre encima", variable=sv_top, bg="#202020",
                       fg=fg, selectcolor="#202020",
                       activebackground="#202020").pack(anchor="w", padx=12)

        tk.Label(win, text="Tecla del panel", bg="#202020", fg=fg,
                 anchor="w").pack(fill="x", padx=12, pady=(8, 0))
        hotkey = tk.StringVar(value=str(cfg.get("hotkey_settings", "F8")).upper())
        ui["hotkey"] = hotkey
        menu = tk.OptionMenu(win, hotkey, *sorted(VK))
        menu.configure(bg="#202020", fg=fg, highlightthickness=0)
        menu.pack(anchor="w", padx=12)

        info = tk.Label(win, text="", bg="#202020", fg="#9E9E9E", anchor="w")
        info.pack(fill="x", padx=12, pady=(6, 0))

        def refresh_info():
            pid = tracker.rd.pid
            disc = getattr(tracker.rd, "game_id", None)
            info.configure(
                text=f"PPSSPP: {'sí' if pid else 'no'}   juego: {disc or '—'}"
                     f"   datos: {'sí' if hybrid else 'no'}"
            )
            win.after(1000, refresh_info)

        buttons = tk.Frame(win, bg="#202020")
        buttons.pack(fill="x", padx=12, pady=10)

        def save_and_close():
            apply_settings()
            save_config(cfg)
            close_settings()

        tk.Button(buttons, text="Guardar", command=save_and_close).pack(side="right")
        tk.Button(buttons, text="Aplicar", command=apply_settings).pack(side="right", padx=6)
        tk.Button(buttons, text="Cerrar", command=close_settings).pack(side="right")
        refresh_info()

    sv_x = tk.StringVar()
    sv_y = tk.StringVar()
    sv_opacity = tk.IntVar()
    sv_font = tk.IntVar()
    sv_top = tk.BooleanVar()

    def watch_hotkey():
        if hotkey_down(cfg.get("hotkey_settings", "F8")):
            if settings["win"] is not None and settings["win"].winfo_viewable():
                close_settings()
            else:
                open_settings()
        root.after(120, watch_hotkey)

    if a.settings:
        open_settings()

    poll()
    reposition()
    watch_hotkey()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
