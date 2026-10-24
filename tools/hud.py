#!/usr/bin/env python3
"""Live Tekken 6 frame-data HUD (practice mode).

Reads both player structs straight out of PPSSPP's memory at ~1 kHz and paints
the result on a transparent, click-through window over the emulator.

Measurement model (no move table):

  * startup   = contact frame - frame the game switched to the move
  * advantage = P2 unlock frame - P1 unlock frame

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

Move ids are per character, so nothing is assumed about them: the label is the
raw id (`#7`) and the measurement is identical for every character.  Optional
names come from tools/notation.json (`--p1-char`, `--p2-char`).

    python tools/hud.py
    python tools/hud.py --x 40 --y 40        # fixed position
    python tools/hud.py --debug              # also print to the console
    python tools/hud.py --p1-char bryan --p2-char bruce
    python tools/hud.py --no-predict         # no ~adelanto
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psp_mem  # noqa: E402

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

IDLE_STATES = {6466, 6482, 14633}   # defender state values while neutral
BLOCK_STATE = 4178                  # defender state while in blockstun
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
        self.dead = False       # emulator gone -> the overlay shuts itself down
        self.t0 = time.time()   # used for the "no emulator at all" grace period

    def ensure(self):
        if self.h is not None:
            return True
        try:
            self.pid = psp_mem.find_pid()
            self.h = psp_mem.open_process(self.pid)
            self.buf = ctypes.create_string_buffer(SPAN)
            base, _size = psp_mem.find_ram(self.h)
            self.host = base + (P1 - psp_mem.PSP_RAM_START)
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

    def __init__(self, notation=None):
        self.notation = {} if notation is None else notation
        self.frame = 0
        self.prev_c1 = None
        self.prev_tail1 = 0
        self.prev_tail2 = 0
        self.prev_hp = None
        self.hp_drop_frame = -999
        self.prev_s2 = None
        self.prev_c2 = None

        # Move-id debounce: the game blips some ids for a single sample
        # (e.g. 15 -> 16 -> 12 on the same frame) before settling.
        self.mid_cand = 0
        self.mid_count = 0
        self.mid_frame = 0
        self.mid = 0

        self.segments = []        # [(start_frame, label)] current animation
        self.seg_start = None
        self.contact = None       # measurement in progress

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
        return {
            "phase": phase,
            "frame": self.frame,
            "impact": contact["impact"],
            "start": contact["start"],
            "startup": max(1, contact["impact"] - contact["start"] + 1),
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
        if raw == self.mid_cand:
            self.mid_count += 1
        else:
            self.mid_cand = raw
            self.mid_count = 1
            self.mid_frame = self.frame

        # accept once it survives a second sample or crosses into a new
        # frame; the segment starts on the frame the id first appeared
        if raw != self.mid and (self.mid_count >= 2 or self.frame > self.mid_frame):
            self.mid = raw
            if raw:
                start = self.mid_frame
                if self.seg_start is None:
                    self.segments = [(start, self.label_for(raw))]
                else:
                    self.segments.append((start, self.label_for(raw)))
                    del self.segments[:-4]
                self.seg_start = start
            else:
                self.seg_start = None
                if self.contact is None:
                    self.segments = []

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
        blocked = d_state == BLOCK_STATE and (d_new or d_anim)

        if self.seg_start is not None and (hit or blocked):
            impact = self.frame
            self.contact = None      # a new hit means the string goes on
            seg = self.pick_segment(impact)
            self.contact = {
                "impact": impact,
                "start": seg[0],
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
                 predict=True):
        super().__init__(daemon=True)
        self.out = out
        self.debug = debug
        self.predict = predict
        self.rd = Reader()
        self.t1 = Tracker(p1_notation)      # P1 attacks, P2 defends
        self.t2 = Tracker(p2_notation)      # P2 attacks, P1 defends (mirrored)
        self.running = True
        self.clock = 0
        self.clock_c = None
        self.last_block = None   # last BLOCK exchange, kept after its final
        self.cache = {}          # (move id, kind) -> (advantage, timestamp)

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

    def feed(self, s):
        """Process one sample: advance the clock and run both trackers.

        Split out of `run()` so the offline logger (tools/diag.py) can drive the
        exact same pipeline.
        """
        # One clock for both sides.  P1's animation counter changes exactly
        # once per game frame and freezes only when the game does.
        if s["c1"] != self.clock_c:
            self.clock += 1
            self.clock_c = s["c1"]

        # P1 as attacker.
        for ev in self.t1.step(s, self.clock):
            ev["side"] = "P1"
            if ev["phase"] == "impact":
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
            else:
                ev["predict"] = self.remember(ev)
            self.emit(ev)

        # P2 as attacker (mirrored): the defense side.
        for ev in self.t2.step(mirror(s), self.clock):
            ev["side"] = "P2"
            if ev["phase"] == "impact":
                ev["predict"] = self.predicted(ev)
                if ev["kind"] == "BLOCK":
                    self.last_block = self.t2.contact
            else:
                # Drop the interrupted value *before* caching it: its recovery
                # was cut short, so it is not this move's real advantage.
                if ev["punished"]:
                    ev["adv"] = None
                ev["predict"] = self.remember(ev)
            self.emit(ev)

    def run(self):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", type=int, default=None)
    ap.add_argument("--y", type=int, default=None)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--no-click-through", action="store_true")
    ap.add_argument("--p1-char", default=None,
                    help="tabla de notación para P1 (bryan, o una clave de tools/notation.json)")
    ap.add_argument("--p2-char", default=None,
                    help="tabla de notación para P2")
    ap.add_argument("--no-predict", action="store_true",
                    help="no adelantar el valor medido la última vez")
    a = ap.parse_args()

    enable_dpi_awareness()

    lock = single_instance()
    if lock is None:
        print("overlay already running", file=sys.stderr)
        return

    results: queue.Queue = queue.Queue()
    tracker = TrackerThread(
        results,
        debug=a.debug,
        p1_notation=load_notation(a.p1_char),
        p2_notation=load_notation(a.p2_char),
        predict=not a.no_predict,
    )
    tracker.start()

    root = tk.Tk()
    root.title("T6 frames")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-transparentcolor", BG_KEY)
    root.configure(bg=BG_KEY)

    f_big = tkfont.Font(family="Consolas", size=28, weight="bold")
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
        if a.x is not None and a.y is not None:
            return a.x, a.y
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

                if phase == "impact":
                    cell["punish"] = bool(res.get("punish"))
                    put(cell["move"], res.get("label", ""), C_DIM)
                    put(cell["startup"], f"i{res['startup']}", "#F2F2F2")
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
                    if adv is None and res.get("predict") is not None:
                        put(cell["value"], f"~{res['predict']:+d}", C_DIM)
                    else:
                        put(cell["value"], "—" if adv is None else f"{adv:+d}",
                            colour(adv, res.get("kind")))
                    if cell["punish"]:
                        put(cell["kind"], "PUNISH", C_BAD)
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
                cell["until"] = now + 5.0
        except queue.Empty:
            pass
        for c in rows.values():
            if c["until"] and now > c["until"]:
                clear(c)
        root.after(25, poll)

    def reposition():
        if a.x is None:
            nx, ny = place()
            root.geometry(f"+{nx}+{ny}")
        root.after(700, reposition)

    poll()
    reposition()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
