# Tekken 6 (PSP) — memory layout and frame-data field guide

**Target:** `Tekken 6 (Europe)`, disc ID **ULES01376**, PPSSPP **1.19.3** (Windows x64)
**Method:** read-only. ISO headers, CWCheat databases, PPSSPP 1.19.3 source, PS3 frame-data tool source. PPSSPP was **not** launched and no live game memory was read.
**Goal:** what the HUD must compute, which PSP addresses are real, which PS3 fields transfer, and how to prove the rest.

> **Status / blocker:** there is **no Tekken 6 savestate** on this machine. The only `.ppst` present is **Persona 3 Portable (`ULES01523`)**, so offline RAM diffing cannot start yet. Every address below is therefore either (a) verified from static artifacts, (b) derived from the PS3 tool, or (c) explicitly marked unknown.

---

## 0. TL;DR

| Fact | Value | Conf. |
| --- | --- | --- |
| Disc ID / title / app version | `ULES01376` / `TEKKEN 6` / `01.00` | Verified |
| P1 health slot (u32, health in high u16) | `0x088078C0` → health u16 at `0x088078C2` = 180 | Verified |
| P2 health slot | `0x088081F0` → health u16 at `0x088081F2` | Verified |
| Player struct stride | `0x930` | Verified |
| Float triplet at `0x08807860` / `0x08808190` | 3× f32 (position / orientation?) | Likely, semantics uncertain |
| PS3-equivalent struct anchor | `0x08806FD0` / `0x08807900` | Hypothesis (H1) |
| Practice-mode numeric frame data | **none** (T7 feature) | Verified (external) |
| Per-frame read cost | one `memory.read` of ~7.4 KB covering both players | Recommendation |
| `memory.search` on 1.19.3 | **does not exist**; master-only | Verified |

**Bottom line:** the *math* of the HUD is settled and portable. The *addresses* are half-settled: health and stride are hard facts, the rest of the struct is a PS3-derived hypothesis with a handful of anomalies. The five experiments in §13 are designed to settle it in one sitting once PPSSPP can be run.

---

## 1. What was inspected

| Artifact | Result |
| --- | --- |
| ISO `Tekken 6 (Europe) (…).iso` | 855,638,016 bytes; `PARAM.SFO` `DISC_ID = ULES01376`, `TITLE = TEKKEN 6`, `APP_VER = 01.00`; `EBOOT.BIN` is `~PSP` encrypted; `USRDIR` holds only `DATA401.IDX`, `DINF_400.BIN`, `modules/`, `movie/` |
| `PPSSPP_STATE\ULES01523_1.01_0.ppst` | Header title `Persona3 PORTABLE`; decompressed RAM contains `ULES01523 -INST00.PERSONA 3 PORTABLE`; 41 MB zstd payload. **Not Tekken 6.** |
| `Cheats\ULES01376.ini`, `Cheats\ULUS10466.ini` | both empty (4 bytes) |
| `cheat.db` (CWCheat Database Plus) | `_S ULES-01376` at line 302942; `_S ULUS-10466` at line 303654 |
| PPSSPP source | v1.19.3: `Core/MemMap.cpp`, `Core/CwCheat.h`; master: `Core/Debugger/WebSocket/MemorySubscriber.cpp` |
| PS3 tool `4shadoww/t6framedatatool` | `address_config.h`, `frame_data_analyser.cpp`, `frame_data_defs.hpp`, `moveset_structs.h` |

---

## 2. Disc identity — blocker #1

`ULES01523` is **Persona 3 Portable (EU)**, not Tekken 6. The `.agents/skills/tekken6-psp/SKILL.md` frontmatter still lists `ULES01523/ULUS10466` as the lookup key, and the only savestate on disk is a P3P state. Consequences:

* Any address or cheat keyed to `ULES01523` is invalid for this game.
* Any savestate diff done against the existing `.ppst` would produce 41 MB of P3P data and nonsense offsets.
* The correct key is **`ULES01376`** (EU) / **`ULUS10466`** (US). The EU and US cheat sets share the same player health/position addresses and the same 30 FPS timing fields (see §5), so the struct layout is at least very similar between the two builds.

**Action before any dynamic work:** create a Tekken 6 savestate in PPSSPP (idle in Practice, and a second one mid-jab). The zstd decompression path is already proven on the P3P state, so the diff tooling will work unchanged.

---

## 3. Frame-data theory → the equations the HUD needs

### 3.1 Definitions

| Symbol | Meaning |
| --- | --- |
| `f` | in-game frame counter (60 ticks/s) |
| `S` | startup: frames from input to the **first active frame** |
| `A` | attacker's remaining recovery frames after impact |
| `D` | defender's remaining stun frames after impact (blockstun or hitstun) |
| `V` | frame advantage = `D − A` |

**Sign convention:** `V > 0` means the attacker recovers first (advantage). `V < 0` means the defender recovers first (disadvantage). This matches community notation ("+1 on block") and the PS3 tool.

### 3.2 The two equations that matter

```
V_block = D_blockstun(f_impact) − A_recovery(f_impact)
V_hit   = D_hitstun(f_impact)  − A_recovery(f_impact)
```

Sample both timers on the **first frame of impact**, before either decrements. Hitstop freezes both players equally, so it cancels out of the subtraction.

The PS3 tool implements exactly this (`frame_data_analyser.cpp`):

```cpp
// single-attack branch (frame_data_analyser.cpp, L667+)
startup_frames = current->game_frame - startup.game_frame;

if (recovery_reset(previous, player) || player_in_stasis(player)) {
    frame_advantage = opponent->recovery_frames - player->recovery_frames;
} else {
    frame_advantage = startup_frames
                    - (startup.recovery_frames - opponent->recovery_frames);
}
```

The subtraction in the `if` branch is the one to port. The `else` branch exists because the tool samples `recovery_frames` at the *start* of the move and at the connection, not always at the same instant; the simple subtraction is correct when both timers are sampled on the impact frame. Implement the simple form first, and add the compensation only if Experiment 5 shows a constant offset proportional to startup.

Note the PS3 tool uses `first_connection` (the frame the hit connects), not the move's declared active frame, for the empirical startup. The declared `move_struct.first_active_frame` is the authoritative value for display; the empirical value is the validation.

### 3.3 Punish window

```
punishable   = V_block < 0
punish_limit = −V_block                       // frames the defender has
best_punish  = min startup(m) over m with startup(m) ≤ punish_limit
```

`AGENTS.md` states `P1_Startup <= Abs(P2_Last_Advantage) && P1_Hit == true`. That is correct only if `P2_Last_Advantage` is the **block** advantage and `P1_Startup` is the **defender's** punish startup. It must also require the punish move to actually connect inside the window — a whiffed i10 does not punish a −10 move.

### 3.4 Calibration targets (T6 Kazuya, community data)

| Move | Startup | On block | On hit |
| --- | --- | --- | --- |
| `1` (jab) | i10 | **+1** | +8 |
| `df+1` | i16 | **−7** | +9 |
| `df+2` | i14–15 | −12…−11 | launch |
| `uf+4` | i25 | — | — |

Use `1` as the primary calibration: it is the shortest, most repeatable move and its +1 block advantage is a single-frame signal, which is exactly what the recovery subtraction should reproduce.

> These are external community values (TekkenDocs), not measured here. Treat them as expected values, not ground truth, until Experiment 5 confirms them in-game.

### 3.5 Detection model

There is no need to identify the move by name to compute advantage. The minimum viable state machine is:

```
every frame:
  read both player blocks (one bulk read)
  if signature(p) changed:  p.frames_in_state = 0
  else:                     p.frames_in_state += 1
  if defender entered stun/guard state:
      V = defender.recovery − attacker.recovery
      latch V for 30–60 frames for the HUD
```

`signature(p) = (state, move_id, string_type)` — not `state` alone. `state` values such as `STANDING = 6482` persist across many frames, so a single-field debounce (as `AGENTS.md` proposes) will not reset the counters when a new move starts from neutral.

---

## 4. What Tekken 6 PSP Practice mode actually gives you

**Verified externally:** T6 practice mode has **no numeric frame-data display**. The in-game "Frame Data Display" is a Tekken 7 paid feature. T6 gives qualitative feedback only.

What is present (Likely — label order varies by region):

* Main Menu → **Practice** → character select → stage select → dummy settings.
* In-match pause → practice settings, including:
  * **Command History** (input list),
  * **Hit Analysis** (damage / hit level / counter-hit feedback),
  * **Position Reset**,
  * dummy behaviour (e.g. "Stand To Guard", guard all, etc.).

What is **not** present:

* numeric startup / block advantage / hit advantage,
* a frame counter,
* a punish-window indicator.

That is the gap the HUD fills. It also means the HUD cannot read frame data *from* practice mode — it must compute it from the player structs.

---

## 5. Public cheats and verified addresses

### 5.1 CWCheat → PSP address translation

PPSSPP `Core/CwCheat.h`:

```cpp
static u32 GetAddress(u32 value) {
    u32 address = (value + 0x08800000) & 0x3FFFFFFF;
    ...
}
```

So a CWCheat code `0x004078C0` maps to `0x088078C0`. The `0x0040xxxx` range is the game's static data, not a pointer. This is why the codes are stable across boots.

### 5.2 Cheat-derived addresses (EU `ULES-01376` and US `ULUS-10466` agree)

| PSP address | Width | Meaning | Evidence |
| --- | --- | --- | --- |
| `0x088078C0` | u32 | P1 health slot; `0x00B40000` = full (180) | `Player 1: Infinite Health v2` = `0x204078C0 0x00B40000` (EU + US) |
| `0x088078C2` | u8/u16 | P1 health value = 180 | `Inf. HP` = `0x004078C2 0x000000FF` |
| `0x088081F0` | u32 | P2 health slot | `Player 2: Infinite Health` |
| `0x088081F2` | u16 | P2 health value | `Player 2: 1 Hit Kill` = `0x804081F2 …` |
| `0x08807860/68/6C` | f32×3 | P1 float triplet — position *or* orientation/scale | `P2: Side` writes `0x40000000` (2.0) to all three |
| `0x08808190/98/9C` | f32×3 | P2 float triplet — same caveat | same code, `0x45000000` (2048.0), `0x40000000`, `0xC0000000` (−2.0) |
| `0x08807AD4` | u32 | P1 control-by-CPU | `P1: Control By CPU` = `0x00407AD4 0x00000001` |
| `0x08807C61` | u8 | rage / "yellow rage" flag = `0x8D` | `Always Yellow Rage Enabled` = `0x20407C61 0x0000008D` |
| `0x08807974`, `0x088082A4` | u32 | 30 FPS timing field = `0x1FF8` | `30 FPS (Game Speed Mod)` |
| `0x08808338` | u32 | "P1 Untouchable" (label semantics odd — see §6.3) | `Player 1 Untouchable` |

Health and the stride are hard facts; the position rows are inferred from the float values; the flags are exactly what the labels say. **Key arithmetic:** `0x81F0 − 0x78C0 = 0x930`. The same `0x930` separates `0x7974/0x82A4` and `0x7860/0x8190`. That is three independent confirmations of the stride.

### 5.3 Save editors and training mods

| Category | Finding |
| --- | --- |
| PSP save editors | Edit **savedata only** (gold, profile items, character customization). They do not touch live match memory and are irrelevant to frame data. |
| Training mods (PSP) | **None found.** No public frame-data overlay/training mod exists for T6 PSP; the scene's tooling is PS3-side. |
| Training mods (PS3) | The `t6framedatatool` project is the reference implementation; its algorithm transfers, its addresses do not. |
| Cheat databases | The only public *memory map* for T6 PSP. CWCheat Database Plus is the most complete; the EU and US sections are the useful ones. |

There is no public "T6 PSP frame data address list". That is why §13 is experiment-driven rather than a lookup table.

### 5.4 Why `0x00B40000` matters

Little-endian `0x00B40000` stores bytes `00 00 B4 00` at `0x78C0..0x78C3`. Therefore:

* the health value 180 (`0xB4`) is the **u16 at +2**,
* the u16 at +0 is zero in the full-health state,
* the slot is best treated as a small stat array, not a scalar.

This also gives the single best first scan in the whole project: searching for `0x00B40000` as a u32 finds the health slot directly, with almost no false positives (§13.1).

---

## 6. Candidate PSP player struct

### 6.1 The anchor problem

The PS3 tool uses a base pointer `0x300B2C140` and these offsets:

| Field | PS3 offset |
| --- | --- |
| `frames_last_action` | `+124` (`0x7C`) |
| `state` | `+172` (`0xAC`) |
| `move` (move id) | `+332` (`0x14C`) |
| `string_type` | `+356` (`0x164`) |
| `recovery_frames` | `+392` (`0x188`) |
| `intent` | `+472` (`0x1D8`) |
| `move_struct` (pointer) | `+1832` (`0x728`) |
| `position` | `+2192` (`0x890`) |
| `string_state` | `+178532` |
| `attack_seq_ptr` | `+342632` (then `+312`) |
| current game frame (global, not per-player) | `+270587472` |

PS3 P2 is `+0xC40` (`+3136`) for most fields, but **not** for position: `P2_POSITION` is `+5248`, i.e. `P2+0x840`, not `P2+0x890`. So the PS3 struct already has a position anomaly; do not expect the PSP side to copy it. On PSP the stride is a uniform `+0x930` for health, position and the 30 FPS field.

If the PS3 position offset also holds on PSP, the anchor falls out of the one *observed* position address:

```
anchor   = position − 0x890 = 0x08807860 − 0x890 = 0x08806FD0
health   = anchor + 0x8F0 = 0x088078C0      ✅ same struct, +0x60 after position
P2       = anchor + 0x930 = 0x08807900      ✅ P2 health 0x088081F0
```

This is a *one-observation hypothesis*, not a proof, and it rests on a second assumption: that `0x7860` is really the `position` field. The cheat writes `(2,2,2)` for P1 and `(2048,2,−2)` for P2, which could just as well be a direction/scale vector or a camera transform. If `0x7860` is not position, H1 collapses and the anchor must come from the `state` value search instead.

Still, it is coherent and falsifiable, and it explains why `0x6FD0` is a plausible EBOOT+`0x2FD0` static address. **H1 (anchor `0x08806FD0`) is the leading working hypothesis — verify it with §13.2, not by assumption.**

### 6.2 H1 address table

| Field | PS3 off | P1 address | P2 address (+0x930) | Conf. |
| --- | --- | --- | --- | --- |
| `frames_last_action` | `+0x07C` | `0x0880704C` | `0x0880797C` | Hypothesis |
| `state` | `+0x0AC` | `0x0880707C` | `0x088079AC` | Hypothesis |
| `move` (id) | `+0x14C` | `0x0880711C` | `0x08807A4C` | Hypothesis |
| `string_type` | `+0x164` | `0x08807134` | `0x08807A64` | Hypothesis |
| `recovery_frames` | `+0x188` | `0x08807158` | `0x08807A88` | Hypothesis |
| `intent` | `+0x1D8` | `0x088071A8` | `0x08807AD8` | Hypothesis |
| `move_struct` ptr | `+0x728` | `0x088076F8` | `0x08808028` | Hypothesis |
| `position` | `+0x890` | `0x08807860` | `0x08808190` | Likely |
| `health` | `+0x8F0` | `0x088078C0` | `0x088081F0` | Verified |

### 6.3 Anomalies that must be resolved

| Observation | Why it is odd |
| --- | --- |
| `0x08807974` = P2 anchor `+0x74`, not `+0x7C` | 8 bytes before the predicted `frames_last_action` |
| `0x08807AD4` = P2 anchor `+0x1D4`, not `+0x1D8` | 4 bytes before predicted `intent` |
| `0x08808338` = P2 anchor `+0x438` | labelled "Player 1 Untouchable" but sits in P2's block |
| `0x08808102` (the "P1 - Fly" float high-half write) = P2 anchor `+0x802` | does not fit the simple map at all |

Two readings are possible:

1. The PS3 offsets are *approximately* right and the PSP struct has a few 4–8 byte shifts (different alignment / removed fields). Then the H1 table is a search seed, not a final map.
2. There are **two blocks per player** (a state block and an animation/move block), and some cheats target the second one. Then `0x8338`/`0x8102` belong to the other block.

Either way, do **not** hardcode H1 until the `state` value search in §13.2 confirms it.

> The earlier working map in the checkpoint (`state` at `0x0880796C`, i.e. health `+0xAC`) is a *different* hypothesis: it applies the PS3 offsets to the **health** base rather than to the `0x6FD0` anchor. That map cannot also produce position at `0x7860`, so it is internally inconsistent. Prefer H1.

### 6.4 What the cheats actually prove

They prove:

* health base and stride,
* the float-triplet base and stride (if `0x7860` is indeed position),
* a small number of individual fields/flags (`+0x214`, `+0x3A1`, `+0x0B4`),
* cross-region address identity.

They do **not** prove the `state` / `move` / `recovery` offsets. Those are PS3-derived and must be confirmed by value search and by the frame-advantage experiment.

---

## 7. PS3 tool → PSP: what transfers

### 7.1 Fully reusable

| Item | Why |
| --- | --- |
| Frame-data definitions | startup, active window, advantage are engine-independent |
| `V = defender.recovery − attacker.recovery` | same semantics |
| Startup measurement (`connection − action start`) | same semantics |
| Punish logic | same semantics |
| String/multi-hit handling (`string_type`, `string_state`) | concept carries over |
| Move-struct fields (`first_active_frame`, `last_active_frame`, `anim_len`) | likely present in the PSP move table |

### 7.2 Not portable

| Item | Why |
| --- | --- |
| Base address `0x300B2C140` | PS3 ELF/global; PSP is `0x08806FD0` (H1) |
| Stride `0xC40` | PSP is `0x930` |
| PS3 pointer values (`0x3xxxxxxx`) | PSP pointers are `0x08xxxxxx`/`0x09xxxxxx` |
| `string_state +178532`, `attack_seq +342632` | large PS3-only arrays; no PSP evidence |
| `ReadProcessMemory` polling | PPSSPP needs WebSocket `memory.read`, which pauses the CPU |

### 7.3 The move struct

`moveset_structs.h` defines (PS3, 32-bit PPU so pointers are 32-bit):

```
first_active_frame, last_active_frame, anim_len,
hit_condition_addr, cancel_addr, ...
```

If the PSP `move_struct` pointer at `+0x728` is valid, the HUD can read the declared active window directly and skip empirical startup measurement. This is the single highest-leverage unknown: it converts "count frames and guess" into "read the move definition".

**Caveat:** the PSP move table may be compressed or relocated compared with PS3. Validate the pointer before trusting the fields: it must be a canonical PSP user address (`0x08800000–0x09FFFFFF`), 4-byte aligned, and stable across frames while the move is active.

---

## 8. Discovery protocol

### 8.1 `memory.search` — exact schema (current master)

`Core/Debugger/WebSocket/MemorySubscriber.cpp`:

| Param | Type | Notes |
| --- | --- | --- |
| `address` | u32 | start of range |
| `size` | u32 | bytes to scan; no cap beyond valid range |
| `type` | string | `u8`, `u16`, `u32`, `float`, `bytes` |
| `value` | u32 / string | integer for u8/u16/u32; **string** for float (`"1.5"`) |
| `base64` | string | pattern for `bytes` |
| `maskBase64` | string | optional, same length; `0x00` = don't care |
| `align` | u32 | optional; default = type size (1 for bytes) |
| `maxResults` | u32 | default 1000, hard cap 100000 |

Response: `{"event":"memory.search","matches":[…absolute addresses…],"truncated":bool}`.

Important properties:

* it runs on the CPU thread and blocks the frame pump for the duration — a 24 MB scan is a visible stall;
* each call is **independent**: there is no server-side "next scan"; intersect results client-side;
* `float` values must be sent as JSON **strings**, not numbers.

### 8.2 PPSSPP 1.19.3 reality

`memory.search` is **not present in 1.19.3** (see task 01, §3). It exists only in master/nightly. On the installed build the only options are:

1. **Chunked `memory.read` + client-side scan.** Read 32–64 KB at a time, decode base64, scan little-endian in Lua/JS. ~400–700 round trips for the full 24 MB; perfectly fine as a one-time operation.
2. **Upgrade PPSSPP** to a build that has `memory.search`.

Because the target addresses are static and confined to the first 64 KB of user RAM, the practical answer is: **scan once, hardcode, never scan at runtime.** The HUD should not depend on `memory.search` at all.

### 8.3 Scan range

```
0x08800000 .. 0x08810000   (64 KB)
```

Under H1 the two players occupy `0x08806FD0..0x08808230` (P1 `0x6FD0`, P2 `0x7900`, stride `0x930`). The scan range also covers the anomaly addresses up to `0x08808338`, so it catches both the struct and the "second block" candidates. It is well inside the EBOOT's static data region, so no pointer chasing is needed for the first pass.

### 8.4 Validation rules

A candidate is only accepted if **all** of these hold:

1. it changes when the corresponding on-screen value changes, and only then;
2. it is stable across a pause/resume;
3. it is mirrored at `+0x930` for the other player;
4. it survives a round reset in a way consistent with its semantics (health resets to 180, state resets to `STANDING`, move id resets to idle).

Rule 3 is the strongest filter and the one that eliminates 90% of false positives.

---

## 9. PSP memory map (PPSSPP `Core/MemMap.cpp`)

| Range | Size | Meaning |
| --- | --- | --- |
| `0x00000000–0x0000FFFF` | 64 KB | null page, usually unmapped |
| `0x00010000–0x00013FFF` | 16 KB | scratchpad (CPU-only) |
| `0x04000000–0x047FFFFF` | 8 MB | VRAM, 4×2 MB mirrors; uncached at `0x44000000` |
| `0x08000000` | 32 MB (64 MB slim) | main RAM; **user-usable from `0x08800000`** (last 24 MB on phat) |
| `0x48000000` | — | uncached RAM mirror |
| `0x88000000` | — | kernel RAM mirror |
| `0xC8000000` | — | kernel uncached RAM mirror |

The game's static data at `0x08806FD0` sits ~28 KB into user RAM — i.e. inside the loaded EBOOT image, not the heap. That is why CWCheat codes work without a pointer.

**Use `0x08xxxxxx`, not `0x48xxxxxx`.** PPSSPP resolves mirrors, but mixing them makes logs and savestate diffs inconsistent.

---

## 10. Verified / Likely / Unknown

### Verified

* Disc ID `ULES01376`, title `TEKKEN 6`, app version `01.00`.
* `ULES01523` is Persona 3 Portable; the existing `.ppst` is P3P and unusable.
* CWCheat translation `(value + 0x08800000) & 0x3FFFFFFF`.
* P1 health slot `0x088078C0`, value u16 at `+2`, full = 180.
* P2 health slot `0x088081F0`; stride `0x930`.
* `0x08807AD4` = control-by-CPU; `0x08807C61` = rage flag.
* EU and US builds share the health/position addresses and the 30 FPS timing fields.
* PPSSPP 1.19.3 has no `memory.search`; master's schema is as in §8.1.
* T6 practice mode has no numeric frame data.
* PS3 tool algorithm and offsets (source-verified).

### Likely

* H1 anchor `0x08806FD0` / `0x08807900` — the leading hypothesis, **conditional** on `0x7860` being `position`; verify with §13.2.
* Float triplet at `0x08807860` and `0x08808190` (written by the side-swap cheat; position vs. orientation/scale unresolved).
* `0x0880711C` / `0x08807A4C` = move id.
* `0x08807158` / `0x08807A88` = recovery frames.
* `0x0880707C` / `0x088079AC` = state.
* `0x088076F8` / `0x08808028` = move-struct pointer.
* EBOOT loads at `0x08804000` (standard PSP user-module base).
* The `0x930` stride also holds for the animation/move block.

### Unknown

* Whether the PSP `recovery_frames` is populated at all in T6 (the PS3 engine does; the PSP port may not).
* The exact `state` enum values on PSP (PS3 values are the search seed, not proof).
* Whether `move_struct` is a direct pointer, an index, or a relocated table.
* Whether `0x7860` is a world position, an orientation/scale vector, or a camera transform.
* The meaning of `0x7974`/`0x82A4` and `0x8338`.
* Where the global game-frame counter lives (PS3 has one at `+270587472`; no PSP equivalent found).
* Whether the HUD can detect impact without a hit flag (state-change detection may be enough).
* Frame-advantage behaviour under the 30 FPS speed mod (not representative of 60 Hz).

---

## 11. Sources

| Topic | Source |
| --- | --- |
| PPSSPP memory map | `Core/MemMap.cpp` L121–156 (v1.19.3) |
| CWCheat address translation | `Core/CwCheat.h` L73–76 |
| `memory.search` schema | `Core/Debugger/WebSocket/MemorySubscriber.cpp` L401–560 (master) |
| 1.19.3 event list | task 01, §3 |
| EU/US cheats | CWCheat Database Plus — `https://github.com/Saramagrean/CWCheat-Database-Plus-` |
| Community cheat list | `https://forums.ppsspp.org/showthread.php?tid=14081` |
| Cheat archive | `https://www.sadamitsu.ru/cheats4.htm` |
| US AR codes | `https://www.supercheats.com/psp/tekken-6/10745/ar-codes/tekken-6-cheats-codes-ulus1046` |
| PS3 frame-data tool | `https://github.com/4shadoww/t6framedatatool` (`src/memory_reader/address_config.h`, `src/frame_data/frame_data_analyser.cpp`) |
| PS3 offsets (raw) | `https://raw.githubusercontent.com/4shadoww/t6framedatatool/master/src/memory_reader/address_config.h` |
| T6 Kazuya frame data | `https://tekkendocs.com/t6/kazuya` |
| Practice mode history | `https://tekken.fandom.com/wiki/Practice_Mode` |
| Practice-mode discussion | `https://gamefaqs.gamespot.com/boards/959886-tekken-6/53398327` |

---

## 12. Corrections to `AGENTS.md`

| Claim in `AGENTS.md` | Reality |
| --- | --- |
| Skill frontmatter key `ULES01523` | `ULES01376` (EU) / `ULUS10466` (US); `ULES01523` is Persona 3 Portable |
| `ADDR_GAME_MODE` byte | no verified address; candidate not found |
| `P1_MOVE_ID` integer | candidate `0x0880711C` (H1 `+0x14C`), unverified |
| `P1_ANIM_FRAME` short | candidate `0x0880704C` (`frames_last_action`), unverified |
| `P2_STUN_STATE` byte `0/1` | no evidence of a binary stun flag; the PS3 model uses a countdown `recovery_frames` |
| `P2_STUN_TIMER` short | candidate `0x08807A88` (H1 `+0x188`), unverified |
| PUNISH condition | must use the **defender's** startup and the **block** advantage; `<=` is correct, but "hit == true" must mean "the punish connected inside the window" |

Also: the debounce key should be `(state, move_id, string_type)`, not "state or move id" alone, and the whole watch block should be read in **one** `memory.read` per frame rather than dozens of `read_u16`/`read_u32` calls — each separate call pauses the emulator (task 01, §6.1).

---

## 13. Top 5 experiments (do these in order)

Each experiment has a single purpose, an exact first action, and a pass/fail criterion. All addresses are **EU `ULES01376`**; the US build should behave identically.

### 13.1 Confirm health, stride, and width

**Goal:** prove the only two facts everything else is built on.

1. `memory.read_u16` at `0x088078C2` → expect **180**.
2. `memory.read_u16` at `0x088081F2` → expect **180**.
3. One-time scan (master `memory.search`): `type:"u32"`, `value: 11796480` (`0x00B40000`), `address: 142606336` (`0x08800000`), `size: 65536`, `align: 4`.
   * Expect matches at `0x088078C0` and `0x088081F0` (and possibly HUD/max-health copies).
   * On 1.19.3: chunked `memory.read` + scan for the same 4 bytes `00 00 B4 00`.
4. Take one hit; re-read `0x088078C2` → expect `< 180`; the u32 at `0x088078C0` becomes `0x00xx0000`.
   * **Disable infinite health for this step** — with the cheat on, the value is clamped every frame and will not move.

**Pass:** both health addresses found, difference exactly `0x930`, health drops on hit and resets to 180 on round restart.

### 13.2 Find the state field by value

**Goal:** settle H1 vs. the alternatives, and locate `state` without guessing.

1. Stand still (no input) in Practice.
2. Scan `0x08800000`, size `0x10000`, `type:"u32"`, `align:4`, `value: 6482` (`0x1952` = `STANDING`).
3. Crouch; scan `value: 14633` (`0x3929` = `CROUCHING`).
4. Walk forward; scan `value: 67650` (`0x10842` = `MOVE_FORWARDS`); walk back; `135250` (`0x21052` = `MOVE_BACKWARDS`).
5. Intersect the result sets client-side.
6. Separately, walk forward and sample the three floats at `0x08807860/68/6C`. The axis that changes smoothly is the movement axis; if none changes, `0x7860` is not position and H1's anchor basis is invalid.

**Pass:** two surviving addresses `0x930` apart. Compare with H1: `0x0880707C` / `0x088079AC`. If they match, the anchor is confirmed; the remaining fields inherit their addresses from it but still need §13.4/§13.5 to be trusted. If they do not, use the surviving address as the true `state` and re-base the whole table from it (`anchor = state_addr − 0xAC`), then re-run §13.4/§13.5.

### 13.3 Find the per-frame counter

**Goal:** get a reliable 60 Hz time base; without it, startup and advantage are not measurable.

1. Read `0x08806F00`..`0x08808C00` once per frame for 120 frames (tick on the per-frame `gpu.stats.get` event, or a 16.7 ms timer; do **not** use `input.buttons` — it only fires when the button state changes).
2. Diff consecutive buffers; collect offsets that change every frame.
3. A true frame counter increments by exactly **1** per frame and stops when the game is paused.
4. Check the H1 candidates: `0x0880704C` (`frames_last_action`, P1) and `0x0880797C` (P2).

**Pass:** one u16/u32 that increments by 1 at 60 Hz, stops in the pause menu, and resets or continues consistently across a round. If it is `frames_last_action`, it resets on every new action; if it is the global counter, it never resets. Either is usable, but they serve different purposes (per-move timing vs. absolute time).

### 13.4 Identify the move id

**Goal:** distinguish moves without a name table.

1. Snapshot the 7.4 KB block while idle.
2. Perform `1`; snapshot at the first active frame; diff.
3. Perform `df+1`; diff again. Repeat with `df+2`.
4. Keep offsets that (a) change at action start, (b) stay constant for the duration of the move, (c) differ between the three moves.

**Pass:** one u16/u32 at `0x0880711C` (H1 `+0x14C`) with three distinct values, mirrored at `0x08807A4C`. If the value changes every frame instead of holding, it is an animation frame, not a move id.

### 13.5 Validate recovery and the advantage equation

**Goal:** the actual HUD number.

1. Set the dummy to block all.
2. Read u16 at `0x08807158` (P1 recovery) and `0x08807A88` (P2 recovery) every frame.
3. Have **P1 attack** and the dummy block. At the impact frame, record both timers.
4. Compute `V = defender − attacker = P2.recovery − P1.recovery`; expect **+1** for a jab.
5. Repeat with `df+1`; expect **−7**. If you instead let P2 attack, the sign flips: `V = P1 − P2`.

**Pass:** sign and magnitude match the community values within ±1 frame. If `V` is always 0 or the fields never change, the PSP build does not populate `recovery_frames`; fall back to counting frames from the first active frame to the first actionable frame, and derive advantage from the defender's stun state duration instead.

**If all five pass:** freeze the address map, write it into a header, and build the HUD loop around a single 7.4 KB read per frame.

**If 13.2 fails:** stop and re-derive the struct from scratch with the savestate diff (idle vs. mid-jab, zstd-decompress, 32 MB diff, cluster offsets by `0x930` periodicity). That path is blocked today only because no Tekken 6 savestate exists.

### 13.6 First-search cheat sheet

All ranges are `address = 142606336` (`0x08800000`), `size = 65536`. On 1.19.3, replace `memory.search` with a chunked `memory.read` + client-side scan.

| # | Target | Type | Value | Align | Expected hit |
| --- | --- | --- | --- | --- | --- |
| 1 | health slot | `u32` | **11796480** (`0x00B40000`) | 4 | `0x088078C0`, `0x088081F0` |
| 2 | health value | `u16` | **180** (`0xB4`) | 2 | `0x088078C2`, `0x088081F2` |
| 3 | standing state | `u32` | **6482** (`0x1952`) | 4 | `0x0880707C` / `0x088079AC` (H1) |
| 4 | crouching state | `u32` | **14633** (`0x3929`) | 4 | same two addresses |
| 5 | walk forward | `u32` | **67650** (`0x10842`) | 4 | same two addresses |
| 6 | walk backward | `u32` | **135250** (`0x21052`) | 4 | same two addresses |
| 7 | move id | `u16` | differs per move | 2 | `0x0880711C` / `0x08807A4C` (H1) |
| 8 | recovery | `u16` | counts down | 2 | `0x08807158` / `0x08807A88` (H1) |

The first row alone is usually enough to anchor the whole map: it is a rare 4-byte constant, and the two player copies are `0x930` apart (any extra hit is a HUD/max-value copy).
