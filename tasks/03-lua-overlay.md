# LuaJIT HUD overlay for PPSSPP 1.19.3 — design, tested code, pitfalls

**Scope:** one process, one Lua state, no external binaries. It connects to `ws://127.0.0.1:8099/debugger`, polls one bulk `memory.read` per frame, renders a click-through always-on-top translucent panel over the PPSSPP window, and exits cleanly.

**Status of this document:** everything in §2–§7 was executed on this machine (LuaJIT 2.1 ROLLING, Win64, PPSSPP 1.19.3, `ULES01376`). Numbers marked *(measured)* come from those runs. Anything I could not confirm is listed in §12 as **unverified** and is also flagged inline.

**Companion reports:** `tasks/01-ppsspp-debugger.md` (protocol, authoritative) and `tasks/02-tekken6-memory.md` (addresses). This file does not repeat their findings except where the HUD design depends on them.

---

## 0. TL;DR

```
luajit.exe run.lua
   │
   ├─ wslib      Winsock2 FFI: non-blocking socket + select()
   ├─ wsproto    base64 / SHA-1 / RFC6455 frame codec
   ├─ wsclient   handshake, ticket dispatch, fragmentation, close
   ├─ json       rxi/json.lua (vendored, MIT)
   ├─ win32      FindWindowW("PPSSPPWnd"), ClientToScreen, QPC
   ├─ overlay    UpdateLayeredWindow + 32bpp top-down DIB + GDI mask text
   └─ game       watch-block decode + debounce + frame-data state machine
```

Four decisions that shape everything else:

| Decision | Choice | Why |
|---|---|---|
| Overlay window | `UpdateLayeredWindow` (`ULW_ALPHA`) | Color-key cannot do translucent panels or antialiased edges (§7.1) |
| Text rendering | GDI into a scratch DIB used as a **coverage mask**, composited premultiplied | GDI writes `0x00BBGGRR`, never alpha |
| Socket | Single thread, non-blocking + `select()` with a deadline | One frame of latency is inherent; blocking `select(…, 0)` would stall the render loop |
| Read granularity | One `memory.read` of a contiguous watch block per frame | `memory.read` breaks the CPU thread; 40 × `read_u32` per frame visibly stutters the game |

The `PPSSPP.DrawText` / `PPSSPP.DrawRect` API used in `AGENTS.md` **does not exist** in PPSSPP 1.19.3 (nor is there any drawing API in the debugger protocol). The HUD must be an external window owned by this process. That is not a workaround — it is the only option.

---

## 1. What the process actually has to do

Derived from the measured behaviour of the debugger, not from assumptions:

1. The debugger is a **request/response** protocol on a single WebSocket per client. There is no subscription for memory (broadcasts exist only for `game.*`, `log`, `input.buttons`, `cpu.stepping`).
2. `memory.read` is serviced on the **CPU thread**, so it costs a break/resume handshake: *measured* `version` round-trip **0.94 ms**, `memory.read` **15.3 ms avg / 17.4 ms p95** for 64–512 B. That is ≈ one emulated frame.
3. Therefore a 60 Hz HUD is **pipelined**, not synchronous: request snapshot *N* at the frame deadline, render whatever snapshot arrived (`N-1`). Never `while not reply do select() end` inside the render tick.
4. The reply is **fragmented** (TEXT + CONTINUE frames). Reassembly by `FIN` is mandatory; the ticket arrives in the first fragment.
5. Rendering must not gate the socket. `select()` timeout is computed from the next frame deadline, so data wakes the loop early and a quiet frame still ticks on time.

### 1.1 Why not PPSSPP's built-in Lua?

There is no Lua bridge in 1.19.3's debugger: the protocol exposes memory/input/CPU control only. Even in later builds there is no overlay drawing API. The `AGENTS.md` snippet is aspirational pseudo-code.

---

## 2. Runtime and module layout

Use the bundled `tools/luajit/luajit.exe` (LuaJIT 2.1 ROLLING, x64). Lua 5.1 alone is not enough — you need `ffi` and `bit`.

```
framemod/
  run.lua              -- arg parsing, signal handling, main loop, shutdown
  config.lua           -- host/port, watch block, HUD layout, colours, keybinds
  lib/
    wslib.lua          -- Winsock2: cdef, connect, send_all, recv_some, wait, close
    wsproto.lua        -- base64, SHA-1, accept key, frame encode, incremental parser
    wsclient.lua       -- handshake + ticket dispatch + fragmentation + close
    json.lua           -- vendored rxi/json.lua v0.1.2
    win32.lua          -- QPC clock, timeBeginPeriod, window discovery/tracking, DPI
    overlay.lua        -- layered window, DIB surfaces, draw primitives, text, present
    game.lua           -- watch-block decode, debounce, move state machine, advantage math
    hud.lua            -- panel layout, colours, formatting (pure: snapshot -> draw calls)
  vendor/
    json-LICENSE.txt
```

Rules that keep this maintainable:

* `hud.lua` is **pure**: `hud.draw(overlay, view)` where `view` is a plain table. It never touches the socket or memory.
* `game.lua` is **pure** too: `game.step(snapshot_bytes, now)` → `view`. No FFI in it once the bytes are decoded (decode lives in `game.decode`).
* `overlay.lua` never blocks and never calls `select`. One `present()` per frame, and only if something changed.

**Vendored JSON: `rxi/json.lua` v0.1.2, MIT, ~9.6 KB, single file, no dependencies.**
`https://github.com/rxi/json.lua` — vendoring is deliberate: no LuaRocks, no network at runtime, and the license permits redistribution (keep the header comment and add `vendor/json-LICENSE.txt`).

Why it is sufficient here: the debugger emits flat-ish objects with strings/numbers/booleans/`null`; base64 arrives as one long string. `json.decode` on a ~700-byte base64 payload is not measurable next to a 15 ms read. Numbers decode to doubles — fine up to `2^53`, which covers every PSP address (`≤ 0x0A000000`) and ticket.

> ⚠️ `json.lua` does not validate trailing garbage strictly and rejects some non-finite literals. Never feed it a partially reassembled frame; that is what produces `decode: expected value` at random intervals when fragmentation is mishandled.

---

## 3. Winsock2 layer (`wslib.lua`)

Verified struct sizes on Win64 (`ffi.sizeof`): `WSADATA` **408**, `sockaddr_in` **16**, `fd_set` **520** (`4 + 64×8` + padding), `timeval` **8**, `WSABUF` **16**.

```lua
local ffi = require("ffi")
local ws2 = ffi.load("ws2_32")            -- REQUIRED: ffi.load, not a global

ffi.cdef[[
typedef uintptr_t SOCKET;
typedef struct WSAData {
  unsigned short wVersion, wHighVersion;
  char szDescription[257];
  char szSystemStatus[129];
  unsigned short iMaxSockets, iMaxUdpDg;
  char *lpVendorInfo;
} WSADATA;
struct in_addr { uint32_t s_addr; };
struct sockaddr_in {
  short sin_family; unsigned short sin_port;
  struct in_addr sin_addr; char sin_zero[8];
};
struct sockaddr { unsigned short sa_family; char sa_data[14]; };
typedef struct fd_set { unsigned int fd_count; uintptr_t fd_array[64]; } fd_set;
struct timeval { long tv_sec; long tv_usec; };

int     WSAStartup(unsigned short, WSADATA *);
int     WSACleanup(void);
SOCKET  socket(int, int, int);
int     connect(SOCKET, const struct sockaddr *, int);
int     send(SOCKET, const char *, int, int);
int     recv(SOCKET, char *, int, int);
int     closesocket(SOCKET);
int     ioctlsocket(SOCKET, unsigned long, unsigned long *);
int     select(int, fd_set *, fd_set *, fd_set *, const struct timeval *);
int     getsockopt(SOCKET, int, int, char *, int *);
int     setsockopt(SOCKET, int, int, const char *, int);
unsigned long inet_addr(const char *);
unsigned short htons(unsigned short);
int     WSAGetLastError(void);
]]
```

Non-obvious bits that cost real time if wrong:

* `SOCKET` is **`uintptr_t`**, not `int`. Declaring it `int` truncates the handle on Win64 and every call fails with `WSAENOTSOCK`.
* `fd_set.fd_count` is `unsigned int`; on Windows `select()` ignores `nfds` (pass `0`).
* `timeval.tv_sec/tv_usec` are `long` = 4 bytes on Windows, so `timeval` is 8 bytes, not 16.
* Never leave the socket in blocking mode. `FIONBIO` is `0x8004667E` and expects a `u_long*` (non-zero = non-blocking), not a BOOL.
* `connect()` on a non-blocking socket returns `SOCKET_ERROR` with `WSAEWOULDBLOCK` (10035). `select()` writability alone does **not** mean success — check `SO_ERROR`.

Connect + I/O, as tested:

```lua
local M = {}
M.AF_INET, M.SOCK_STREAM, M.IPPROTO_TCP = 2, 1, 6
M.SOL_SOCKET, M.SO_ERROR, M.TCP_NODELAY = 0xFFFF, 0x1007, 0x0001
M.FIONBIO, M.SOCKET_ERROR = 0x8004667E, -1
M.INVALID_SOCKET = ffi.cast("SOCKET", -1)

local started = false
function M.startup()
  if started then return true end
  local d = ffi.new("WSADATA")
  local rc = ws2.WSAStartup(0x0202, d)          -- request 2.2
  if rc ~= 0 then return nil, "WSAStartup: " .. rc end
  started = true; return true
end

local function make_tv(ms)                       -- ms < 0 => infinite (nil)
  if ms < 0 then return nil end
  local tv = ffi.new("struct timeval")
  tv.tv_sec, tv.tv_usec = math.floor(ms / 1000), math.floor(ms % 1000) * 1000
  return tv
end

-- Returns true if ready, false on timeout, nil,err on failure.
function M.wait(s, for_write, timeout_ms)
  local set = ffi.new("fd_set")
  set.fd_count, set.fd_array[0] = 1, s
  local tv = make_tv(timeout_ms or 0)
  local n = for_write and ws2.select(0, nil, set, nil, tv)
                       or ws2.select(0, set, nil, nil, tv)
  if n == M.SOCKET_ERROR then return nil, "select: " .. ws2.WSAGetLastError() end
  return n > 0
end

function M.tcp_connect(host, port, timeout_ms)
  local ok, err = M.startup(); if not ok then return nil, err end
  local s = ws2.socket(M.AF_INET, M.SOCK_STREAM, M.IPPROTO_TCP)
  if s == M.INVALID_SOCKET then return nil, "socket: " .. ws2.WSAGetLastError() end

  local one = ffi.new("unsigned long[1]", 1)
  if ws2.ioctlsocket(s, M.FIONBIO, one) == M.SOCKET_ERROR then
    ws2.closesocket(s); return nil, "ioctlsocket: " .. ws2.WSAGetLastError()
  end

  local sa = ffi.new("struct sockaddr_in")
  sa.sin_family = M.AF_INET
  sa.sin_port   = ws2.htons(port)
  local a = ws2.inet_addr(host)
  if a == 0xFFFFFFFF then ws2.closesocket(s); return nil, "bad IPv4 literal" end
  sa.sin_addr.s_addr = a

  if ws2.connect(s, ffi.cast("const struct sockaddr *", sa), ffi.sizeof(sa)) == M.SOCKET_ERROR then
    local e = ws2.WSAGetLastError()
    if e ~= 10035 and e ~= 10036 then            -- WSAEWOULDBLOCK / WSAEINPROGRESS
      ws2.closesocket(s); return nil, "connect: " .. e
    end
    local ready, werr = M.wait(s, true, timeout_ms or 2000)
    if not ready then ws2.closesocket(s); return nil, werr or "connect: timeout" end
    local soerr = ffi.new("int[1]", 0)
    local len   = ffi.new("int[1]", 4)
    ws2.getsockopt(s, M.SOL_SOCKET, M.SO_ERROR, ffi.cast("char *", soerr), len)
    if soerr[0] ~= 0 then ws2.closesocket(s); return nil, "connect: " .. soerr[0] end
  end

  local nd = ffi.new("int[1]", 1)
  ws2.setsockopt(s, M.IPPROTO_TCP, M.TCP_NODELAY, ffi.cast("const char *", nd), 4)
  return s
end

function M.send_all(s, data, timeout_ms)
  local len, off = #data, 0
  local p = ffi.cast("const char *", data)
  while off < len do
    local n = ws2.send(s, p + off, len - off, 0)
    if n == M.SOCKET_ERROR then
      local e = ws2.WSAGetLastError()
      if e == 10035 then
        if not M.wait(s, true, timeout_ms or 1000) then return nil, "send: timeout" end
      else
        return nil, "send: " .. e
      end
    elseif n == 0 then
      return nil, "send: peer closed"
    else
      off = off + n
    end
  end
  return true
end

-- n>0 bytes, 0 graceful close/reset, nil,"timeout" when nothing arrived.
function M.recv_some(s, buf, cap, timeout_ms)
  while true do
    local n = ws2.recv(s, buf, cap, 0)
    if n == M.SOCKET_ERROR then
      local e = ws2.WSAGetLastError()
      if e == 10035 then
        if not M.wait(s, false, timeout_ms or 0) then return nil, "timeout" end
      elseif e == 10054 then
        return 0                                  -- reset: treat as closed
      else
        return nil, "recv: " .. e
      end
    else
      return n
    end
  end
end
```

`ffi.cast("const char *", data)` on a Lua string is legal and does not copy; the GC keeps the string alive for the duration of the call because it is still on the stack. Do **not** store that pointer.

---

## 4. RFC6455 (`wsproto.lua`)

Tested against RFC vectors and against PPSSPP. The pieces that must be exact:

* **Accept key:** `base64(sha1(client_key .. "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))`.
* **Client frames are always masked** (opcode bit 0x80 in the second byte + 4 mask bytes). PPSSPP *rejects* unmasked client frames.
* **Server frames are never masked.** Still parse the mask bit — do not assume.
* **Incremental parser.** TCP splits anywhere: the test harness fed every chunk size from 0 to 70000 bytes, plus fragmentation and an interleaved ping, and all cases reassembled.

Base64 and SHA-1 are pure Lua + `bit` (≈40 lines each; no `ffi` dependency beyond an optional fast path). Two details that matter:

```lua
-- base64: tolerate CRLF/whitespace on decode (some JSON writers wrap at 76 cols)
function M.b64decode(s)
  s = s:gsub("[^%w%+/=]", "")
  ...
end
```

Frame encoder (client side): 7-bit length, 16-bit extended, 64-bit extended. For the 64-bit case compute `hi`/`lo` separately (`hi = floor(n / 2^32)`) — a single `math.floor` on a value above 2^53 loses bits.

Incremental parser contract (this asymmetry is deliberate but easy to misuse):

```lua
local fin, opcode, payload = parser:next_frame()
-- fin == nil, err == nil  -> need more bytes
-- fin == nil, err ~= nil  -> fatal ("frame too big", "bad length")
-- else fin is boolean, opcode is a number, payload is a string
```

Key guard rails:

```lua
if len > 8 * 1024 * 1024 then return nil, "frame too big" end
```

…plus a hard cap on the receive buffer. Without it, one corrupt length byte turns into a multi-GB allocation. `memory.read` replies are small (a few hundred bytes to a few KB), so 8 MB is generous.

---

## 5. Handshake and request dispatch (`wsclient.lua`)

Request that PPSSPP 1.19.3 accepts (response status line is literally `HTTP/1.1 101 OK`, not "Switching Protocols" — do not string-match the reason phrase):

```
GET /debugger HTTP/1.1
Host: 127.0.0.1:8099
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: <base64 of 16 random bytes>
Sec-WebSocket-Version: 13
Sec-WebSocket-Protocol: debugger.ppsspp.org
```

Validation, in this order: status `101`; `Upgrade: websocket`; `Sec-WebSocket-Accept` equals the computed value; `Sec-WebSocket-Protocol` contains `debugger.ppsspp.org`. Then feed any bytes that arrived **after** `\r\n\r\n` into the frame parser — the server can already have queued a broadcast.

Random key: `SystemFunction036` (RtlGenRandom) from `advapi32`, which is exported even though it is not an officially documented API. Fall back to `math.random` if the `pcall` fails; the key only needs to be unique, not secret.

```lua
local advapi = ffi.load("advapi32")
ffi.cdef[[ int SystemFunction036(void *, unsigned long); ]]
```

Dispatch by `ticket`, keep the map:

```lua
function Client:poll(timeout_ms)      -- returns list of {opcode=, data=}
  -- loop: recv_some -> parser:feed -> next_frame until no more frames
  -- opcode 8  : echo close, mark closed
  -- opcode 9  : pong with the same payload        (server does not ping in 1.19.3)
  -- opcode 10 : record last_pong
  -- opcode 0  : append to fragment buffer; on FIN emit the whole message
  -- opcode 1/2: if not FIN start fragment, else emit
end
```

`poll()` must return after a **single** timeout wait; subsequent reads use timeout 0 so a burst is drained without extra latency.

Ticket discipline:

* Use monotonically increasing integers. `ticket` is echoed **verbatim** (`writeRaw`), so a string ticket would also work — but integers are cheaper to match.
* Errors arrive as `{"event":"error","message":…,"level":<int>,"ticket":…}`. Treat them as completing the ticket, and log them; silently dropped tickets are the #1 way this hangs.
* Never block on a ticket. Time out with `if now() - sent > 0.5 then drop end` and continue with the last good snapshot.
* Do **not** use `input.buttons.press` for HUD features: its reply is deferred until release, and with no vblank (paused emulation) it never arrives. 1.19.3 has no `input.buttons.release`; use `input.buttons.send` with `false`.

Clean shutdown:

```lua
-- 1. send close frame with code 1000
-- 2. poll for up to 300 ms to receive the echo
-- 3. closesocket(); WSACleanup(); timeEndPeriod(1)
```

PPSSPP answers a client close with a close frame and closes the TCP stream. If it doesn't within 300 ms, just `closesocket` — there is nothing to salvage.

---

## 6. Window discovery and tracking (`win32.lua`)

`version` does **not** return `pid`/`path` in 1.19.3, so there are two paths and both are cheap:

**Path A (preferred):** `FindWindowW(L"PPSSPPWnd", nil)`. Verified on this machine: returned the top-level window of pid 16452, title `PPSSPP v1.19.3 - ULES01376 : TEKKEN 6`. PPSSPP also creates `PPSSPPGameWnd` for the Vulkan path; treat it as a fallback.

**Path B (fallback):** `EnumWindows` + `GetWindowThreadProcessId`, filtered by class name. Necessary if a second PPSSPP instance exists, or if you launch the emulator yourself and know its pid.

Honest caveat: `FindWindowW` finds *a* PPSSPP, not necessarily *yours*. If multiple instances share the port (they can't — the second one falls back to an ephemeral port and its port is written back to the ini), the ambiguity resolves itself, but pin the port in `ppsspp.ini` and don't rely on luck.

Geometry, measured: `GetWindowRect` gave the frame; `GetClientRect` + `ClientToScreen(hwnd, {0,0})` gave the render area origin **240,115**, size **960×544** for a 960×544 client in windowed mode. The overlay must be positioned at the **client** origin, not the window rect, or a title bar's worth of offset appears at the top and the HUD bleeds over the frame.

```lua
local wr, cr, org = ffi.new("RECT"), ffi.new("RECT"), ffi.new("POINT", 0, 0)
user32.GetWindowRect(hwnd, wr)
user32.GetClientRect(hwnd, cr)
user32.ClientToScreen(hwnd, org)
-- overlay rect = {org.x, org.y, org.x + cr.right, org.y + cr.bottom}
```

Re-read this **every frame** (it is two cheap syscalls, ~2 µs) or on `EVENT_OBJECT_LOCATIONCHANGE`. Do not cache it: windowed PPSSPP can be dragged, resized, snapped, and switched to fullscreen at any time. Only **reallocate** the DIB when `cr.right/ cr.bottom` change; moving is just a new `UpdateLayeredWindow` dst point.

Hide the overlay when `IsIconic(hwnd)` or `IsWindowVisible(hwnd) == 0`; exit the process when `IsWindow(hwnd) == 0`. During testing the window reported `WS_VISIBLE` off while its rect was valid, so guard visibility explicitly instead of inferring it from successful rect calls.

### 6.1 DPI

PPSSPP's manifest is `<dpiAware>true</dpiAware>` — **system DPI aware**, not per-monitor v2. Match it with `SetProcessDPIAware()` in the overlay process. Consequences:

* Both processes see the same virtualised coordinates; `ClientToScreen` values can be used directly as overlay coordinates with no scaling.
* On a mixed-DPI multi-monitor setup, dragging PPSSPP to a monitor with a different scale factor makes Windows bitmap-stretch PPSSPP (system-aware apps are stretched on the non-primary monitor). The overlay would be stretched differently and **misalign**. Fixing it needs per-monitor v2 in *both* processes, which PPSSPP does not do. Documented as a known limitation; **unverified** (no mixed-DPI setup was available).
* Font size: `-MulDiv(11, GetDeviceCaps(screenDC, LOGPIXELSY), 72)` so 11 pt is 11 pt at 96, 120, 144 DPI.
* `MulDiv` resolves from `kernel32` on this build (also exported by `user32` on older ones). Declare it once and check that `ffi.load` found it; a nil symbol fails at call time, not at `cdef` time.

---

## 7. The overlay (`overlay.lua`)

### 7.1 Layered-window comparison

| | Color key (`SetLayeredWindowAttributes`, `LWA_COLORKEY`) | `UpdateLayeredWindow` + 32bpp DIB (`ULW_ALPHA`) |
|---|---|---|
| Opaque panel | ✅ | ✅ |
| Semi-transparent panel (e.g. 75 % black) | ❌ — alpha is per-window, not per-pixel | ✅ |
| Antialiased text edges | ❌ — every edge pixel is either key or solid, giving halos or jaggies | ✅ |
| Per-pixel alpha over a changing 3D scene | ❌ obvious fringing | ✅ |
| Click-through | ✅ (also `WS_EX_TRANSPARENT`) | ✅ (alpha 0 regions + `HTTRANSPARENT`) |
| Cost | lower (GDI draws straight to the window) | one full-surface blit per update |
| API complexity | low | medium (DIB, premultiply, `BLENDFUNCTION`) |
| Windows 7 | ✅ | ✅ |

Verdict: `UpdateLayeredWindow`. A frame-data HUD is *mostly* text over a translucent panel; color-key gives exactly the artifacts this project wants to avoid. The extra cost is a `420×150×4` = 252 KB memcpy per present — nothing at 60 Hz. At full 960×544 client size it is ~2 MB/frame; still fine, but see the dirty-check in §8.3.

### 7.2 Structs (exact, Win64)

Verified sizes: `WNDCLASSEXW` **80**, `PAINTSTRUCT` **72**, `BITMAPINFO` **44**, `BLENDFUNCTION` **4**, `MSG` **48**, `RECT` **16**, `POINT` **8**, `SIZE` **8**.

```lua
typedef struct tagWNDCLASSEXW {
  UINT      cbSize;        // must be sizeof(WNDCLASSEXW) or RegisterClassExW fails (87)
  UINT      style;
  WNDPROC   lpfnWndProc;   // keep a Lua reference or it is GC'd -> crash on first message
  int       cbClsExtra;
  int       cbWndExtra;
  HINSTANCE hInstance;
  HICON     hIcon;
  HCURSOR   hCursor;
  HBRUSH    hbrBackground; // NULL: we never paint via WM_ERASEBKGND
  const WCHAR *lpszMenuName;
  const WCHAR *lpszClassName;
  HICON     hIconSm;
} WNDCLASSEXW;

typedef struct tagPAINTSTRUCT {
  HDC  hdc;
  BOOL fErase;            // BOOL == int; 4 bytes, not 1
  RECT rcPaint;
  BOOL fRestore;
  BOOL fIncUpdate;
  BYTE rgbReserved[32];
} PAINTSTRUCT;            // 72 bytes

typedef struct tagBITMAPINFOHEADER {
  DWORD biSize;           // 40
  LONG  biWidth;
  LONG  biHeight;         // NEGATIVE = top-down rows
  WORD  biPlanes;         // 1
  WORD  biBitCount;       // 32
  DWORD biCompression;    // BI_RGB = 0 (no masks needed for 32bpp BI_RGB)
  DWORD biSizeImage;
  LONG  biXPelsPerMeter, biYPelsPerMeter;
  DWORD biClrUsed, biClrImportant;
} BITMAPINFOHEADER;

typedef struct tagBITMAPINFO { BITMAPINFOHEADER bmiHeader; RGBQUAD bmiColors[1]; } BITMAPINFO;

typedef struct tagBLENDFUNCTION {
  BYTE BlendOp;              // AC_SRC_OVER = 0
  BYTE BlendFlags;           // 0
  BYTE SourceConstantAlpha;  // 255 (per-pixel alpha does the work)
  BYTE AlphaFormat;          // AC_SRC_ALPHA = 1
} BLENDFUNCTION;
```

`BITMAPINFO` is 44 bytes because of the trailing `bmiColors[1]`; `CreateDIBSection` reads only `bmiHeader` for 32bpp BI_RGB, but allocating 44 is correct and avoids the classic overrun.

Nested struct by value (`tagBITMAPINFO` containing `tagBITMAPINFOHEADER`) is important: declaring `bmi.bmiHeader` as a pointer makes `biHeight` land in the wrong memory and you get a zero-size or upside-down surface instead of an error.

### 7.3 Call sequence (exact, tested)

```lua
SetProcessDPIAware()

RegisterClassExW(wc)                      -- cbSize set, hbrBackground = NULL
hwnd = CreateWindowExW(
  WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
  | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
  class, L"Framemod HUD",
  WS_POPUP, 0,0,w,h, NULL, NULL, hInstance, NULL)

screen = GetDC(NULL)                      -- screen DC, once
memdc  = CreateCompatibleDC(screen)       -- 1x1 monochrome by default
hbm    = CreateDIBSection(screen, &bmi, DIB_RGB_COLORS, &bits, NULL, 0)
oldbm  = SelectObject(memdc, hbm)

font   = CreateFontW(-MulDiv(11, dpiY, 72), 0,0,0, 600, 0,0,0,
                     DEFAULT_CHARSET, 0,0, ANTIALIASED_QUALITY, 0, L"Consolas")
SelectObject(memdc, font)

SetWindowPos(hwnd, HWND_TOPMOST, 0,0,0,0,
             SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)

-- per frame:
UpdateLayeredWindow(hwnd, screen, &dstPoint, &size, memdc, &srcPoint,
                    0, &blend, ULW_ALPHA)

-- shutdown:
DestroyWindow(hwnd); SelectObject(memdc, oldbm); DeleteObject(hbm);
DeleteDC(memdc); DeleteDC(maskdc); DeleteObject(maskbm); DeleteObject(font);
ReleaseDC(NULL, screen)
```

Window styles, and why each one:

| Style | Purpose |
|---|---|
| `WS_EX_LAYERED` | required for `UpdateLayeredWindow` |
| `WS_EX_TRANSPARENT` | paints after siblings; combined with per-pixel alpha the window does not take hits |
| `WS_EX_TOPMOST` | stays above PPSSPP |
| `WS_EX_TOOLWINDOW` | no taskbar button, no Alt+Tab entry |
| `WS_EX_NOACTIVATE` | clicking never steals focus (belt-and-braces with `WM_MOUSEACTIVATE` → `MA_NOACTIVATE`) |
| `WS_POPUP` | no non-client area; size == content size |

The WndProc must still handle `WM_PAINT` with `BeginPaint`/`EndPaint` (return 0) even though nothing is drawn there — otherwise Windows can keep the update region dirty and `UpdateLayeredWindow` interplay gets odd. Also: `WM_ERASEBKGND` → 1, `WM_NCHITTEST` → `HTTRANSPARENT` (−1), `WM_MOUSEACTIVATE` → `MA_NOACTIVATE` (3), `WM_DESTROY` → `PostQuitMessage(0)`.

```lua
-- MUST stay reachable from Lua for the process lifetime.
wndproc_ref = ffi.cast("WNDPROC", function(hwnd, msg, wp, lp) ... end)
```

An unreferenced Lua callback cast to `WNDPROC` is collected and the next message jumps into freed memory. This is the single most common crash in this design.

Register the class **once**; `RegisterClassExW` returns 0 with `GetLastError() == 1410` (`ERROR_CLASS_ALREADY_EXISTS`) on re-entry, which is harmless.

### 7.4 Text: the mask trick

GDI writing into a 32bpp DIB sets `0x00BBGGRR` and leaves the alpha byte at 0. If you blit that surface with `AC_SRC_ALPHA`, everything disappears. If you blit with `SourceConstantAlpha`, the whole panel becomes uniformly translucent and text loses its edges.

Solution, implemented and visually verified:

1. Keep a **second** 32bpp top-down DIB, same size, as a scratch mask.
2. Clear the target rectangle to black (`FillRect` with a black brush, or `ffi.fill`).
3. `SetBkMode(TRANSPARENT)`, `SetTextColor(RGB(255,255,255))`, `DrawTextW` in the mask DC, then `GdiFlush()` — GDI batches, and reading `bits` before the flush can yield stale pixels.
4. Treat the **blue channel** (`bits & 0xFF`) of each mask pixel as coverage 0–255, and composite `premul(rgb, coverage * alpha / 255)` into the real surface.

Use `ANTIALIASED_QUALITY`, not `CLEARTYPE_QUALITY`: ClearType writes subpixel RGB fringes into the coverage channels, which shows up as coloured halos when you read only one channel. Grayscale AA is exactly what a mask wants.

`DrawTextW` with `DT_CALCRECT` first for measurement, so the composite loop only touches the glyph bounding box instead of the full surface.

Premultiplication is mandatory with `AC_SRC_ALPHA`:

```lua
local function premul(rgb, a)                  -- 0xAARRGGBB in memory as B,G,R,A
  local r = bit.band(bit.rshift(rgb, 16), 0xFF)
  local g = bit.band(bit.rshift(rgb,  8), 0xFF)
  local b = bit.band(rgb, 0xFF)
  return bit.bor(bit.lshift(a, 24),
    bit.lshift(math.floor(r * a / 255), 16),
    bit.lshift(math.floor(g * a / 255),  8),
    math.floor(b * a / 255))
end
```

Skipping it produces the classic "glowing halo on black text" over a light background. Note the pixel is `uint32` little-endian, so `0xAARRGGBB` written to memory is bytes `B,G,R,A` — which is what `BI_RGB` 32bpp expects (`BI_RGB` = BGRA, no channel masks).

Text quality is fine for monospaced numerals. For a more "arcade" look, swap `Consolas` for a bundled bitmap font drawn with `FillRect` — not needed now.

### 7.5 Colour conventions (from `AGENTS.md`, kept)

```lua
local COLOR_PLUS    = 0x00FF00   -- green: safe advantage
local COLOR_MINUS   = 0xFFFFFF   -- white: small disadvantage (-1 .. -9)
local COLOR_PUNISH  = 0xFF0000   -- red: punishable (<= -10)
local COLOR_PANEL   = 0x000000   -- panel fill, alpha ~190
local COLOR_LABEL   = 0xA0FFA0   -- secondary text
```

`AGENTS.md` writes these as `0x00FF00FF` etc. (RGBA byte order). The DIB wants `0x00RRGGBB`. Pick one convention in `config.lua` and convert at the boundary once; mixing them is why the "green" ends up blue.

---

## 8. Main loop: 60 Hz, pipelined, non-blocking

### 8.1 Time source

`os.clock()` on this LuaJIT build tracks **wall time**, not CPU time (measured: 2.84 s over a 3 s wall interval). That makes it useless as a scheduling clock — use `QueryPerformanceCounter`.

```lua
ffi.cdef[[ typedef long long LONGLONG;
  int QueryPerformanceCounter(LONGLONG *);
  int QueryPerformanceFrequency(LONGLONG *); ]]
local k32  = ffi.load("kernel32")
local freq = ffi.new("LONGLONG[1]"); k32.QueryPerformanceFrequency(freq)
local function now()
  local t = ffi.new("LONGLONG[1]"); k32.QueryPerformanceCounter(t)
  return tonumber(t[0]) / tonumber(freq[0])
end
```

`tonumber(t[0])` is required: `LONGLONG` is a cdata and arithmetic on it stays cdata.

### 8.2 Pacing

*Measured* over 300 frames with a small workload per frame:

| Strategy | avg frame | fps | jitter > 1.5× |
|---|---|---|---|
| `Sleep(1)` + `timeBeginPeriod(1)` | 16.67 ms | 60.0 | occasional 40–63 ms hitches under system load |
| `Sleep(1)`, default timer resolution | ~15.6 ms nominal, quantised bursts | unstable | frequent |

So: `timeBeginPeriod(1)` at startup, `timeEndPeriod(1)` at shutdown. Note it raises the **global** timer resolution for the machine (increases idle power draw). If you care, the modern replacement is `CreateWaitableTimerExW(..., CREATE_WAITABLE_TIMER_HIGH_RESOLUTION)` + `SetWaitableTimerEx`, available on Windows 10 1803+; it gives per-timer resolution without touching the global period. **Unverified in this project** — the `timeBeginPeriod` path is what was measured.

Never `while true do ... end` busy-wait for the deadline: it burns a core and fights the emulator thread for the CPU, which is exactly the thing whose frame timing you are measuring.

### 8.3 Loop shape

```lua
local PERIOD = 1 / 60
timeBeginPeriod(1)
local next_t = now()
local pending_read = nil          -- { ticket=, sent= }
local snapshot     = nil          -- bytes of the last completed read
local view         = nil

while running do
  -- 1. sleep until the next deadline, but wake early if the socket has data
  local left = next_t - now()
  if left > 0 then
    local ms = math.max(0, math.min(math.floor(left * 1000), 15))
    client:poll(ms)               -- select(read, ms); processes whatever arrived
  else
    client:poll(0)                -- drain without waiting
  end

  -- 2. read completion -> swap in the new snapshot
  local done = client:take_result(pending_read)
  if done then
    snapshot, pending_read = done, nil
    view = game.step(snapshot, now())
  end
  if pending_read and now() - pending_read.sent > 0.5 then
    pending_read = nil            -- stale: keep the old view, retry next tick
  end

  -- 3. frame deadline work
  if now() >= next_t then
    -- geometry
    local x, y, w, h, visible = win32.track(ppsspp)
    if visible then
      overlay:ensure_size(w, h)
      if not pending_read then    -- never more than one in flight
        pending_read = client:request(memory_read_event(next_ticket()), 0.5)
      end
      if view and hud.changed(view, overlay) then
        overlay:clear()
        hud.draw(overlay, view)
        overlay:present(x, y)
      end
      overlay:show()
    else
      overlay:hide()
    end

    next_t = next_t + PERIOD
    local late = now() - next_t
    if late > 0.1 then next_t = now() + PERIOD end   -- don't accumulate a backlog
  end

  win32.pump_messages()          -- PeekMessage loop; returns false on WM_QUIT
end
```

Why "at most one `memory.read` in flight": each one breaks and resumes the CPU thread. Two overlapping requests queue on the CPU thread and add latency without adding information. Since a reply takes ~15 ms, you get ~55–60 snapshots/s and render at 60 Hz — the pipeline self-regulates.

> `client:request()` here **returns a pending handle** instead of waiting. The blocking variant used in the experiment scripts (`while not done do poll() end`) is only for one-off probes; using it in the loop costs you the frame deadline every time the emulator is slow.

`hud.changed(view, overlay)` is a dirty check: hash the formatted strings (or compare a version counter bumped by `game.step`). Re-presenting an identical surface every frame is pure waste, and at 960×544 it is 2 MB of PCIe/memory traffic per frame.

### 8.4 Latency budget (measured + reasoned)

| Stage | Cost |
|---|---|
| `select` wake-up | ~0.1–1 ms (Sleep granularity when idle) |
| recv + frame parse (512 B reply, 5 fragments) | < 0.1 ms |
| base64 decode + `ffi.copy` into a struct view | < 0.05 ms |
| `json.decode` of ~700 B | < 0.2 ms |
| render (panel + ~10 text runs, 420×150) | ~1–2 ms *(estimate, not benchmarked)* |
| `memory.read` round trip on the CPU thread | **15.3 ms avg, 17.4 ms p95** |

So the HUD shows state that is **1–2 emulated frames old**. That is acceptable for startup/recovery/advantage numbers and must be stated in the UI (or silently accepted — it is below human reaction time). It is *not* acceptable to pretend the HUD is frame-exact for punish validation; design the punish alert around the debounced state machine, not around wall-clock alignment.

---

## 9. Frame-data pipeline (mapping `AGENTS.md` §2 onto the protocol)

`AGENTS.md` describes four phases. Here is how they map to code, and where the protocol imposes changes.

### 9.1 Watch block

One contiguous read per frame. Build `config.watch = { address = 0x08800000 + 0x…, size = 256 }` covering both player structs if they are close, otherwise use two `memory.read`s **alternating** frames (never two in one frame).

Addresses come from `02-tekken6-memory.md`; nothing in that report is repeated here as fact. The HUD must boot with `config.watch = nil` and show a `NO WATCH BLOCK` banner rather than guessing an address — a wrong address read from `0x08800000` returns zeros, which looks like valid data and will silently produce garbage advantages.

### 9.2 Decode

```lua
function game.decode(bytes)
  local buf = ffi.cast("const uint8_t *", bytes)
  local function u8(o)  return buf[o] end
  local function u16(o) return bit.bor(buf[o], bit.lshift(buf[o+1], 8)) end     -- LE
  local function u32(o) return bit.bor(buf[o], bit.lshift(buf[o+1], 8),
                                       bit.lshift(buf[o+2], 16),
                                       bit.lshift(buf[o+3], 24)) % 4294967296 end
  return { p1_move = u32(0x00), p1_anim = u16(0x04), ... }
end
```

`ffi.cast` on a Lua string is zero-copy and bounds-unchecked: a short read (server error, truncated buffer) reads past the end of the string. Always check `#bytes >= expected` first. This is the most likely source of a silent crash in this design.

### 9.3 Debounce (the `AGENTS.md` state filter)

The debounce is not an optimisation, it is a correctness requirement: `memory.read` samples at ~60 Hz against a ~60 Hz emulator, so consecutive frames are not aligned. Move IDs can be seen once, twice, or never across a state change.

```lua
function game.step(snap, t)
  local s = decode(snap)
  -- state change => reset logical counters; otherwise increment
  if s.p1_move ~= prev.p1_move or s.p1_state ~= prev.p1_state then
    counters.startup, counters.active, counters.recovery = 0, 0, 0
    frame0 = t
  else
    counters.total = counters.total + 1
  end
  ...
end
```

Because a 1-frame move can be missed, do not assert "startup = 7" from one observation. Require the move to be present in ≥2 consecutive samples, or measure startup from the *first* sample in which it appears (which is off by up to 1 frame). Document the ±1 frame uncertainty in the HUD, e.g. `i12` instead of `i12.0`. **Do not** present computed frame data with more precision than the sampling allows — that is the difference between a tool and a toy.

### 9.4 Advantage math

`P2_guardstun - P1_recovery` needs the guard-stun timer from P2 and the remaining recovery of P1, both in the same snapshot. With one bulk read that is automatic; with two reads on different frames it is a race. This is the strongest argument for a single watch block.

Punish alert: only fire on a **transition** into a punishable state (`prev_adv > -10 and adv <= -10`), not while it stays punishable. Otherwise the alert latches on for the whole recovery.

---

## 10. Pitfalls (each one either bit me or is one mistake away)

### FFI / Win32

| Pitfall | Symptom | Fix |
|---|---|---|
| `SOCKET` declared as `int` | `WSAENOTSOCK` on every call | `uintptr_t` |
| `WNDPROC` Lua callback not referenced | crash after the first message, looks random | module-level `wndproc_ref` |
| `cbSize` not set on `WNDCLASSEXW` | `RegisterClassExW` 0, error 87 | `ffi.sizeof("WNDCLASSEXW")` |
| `BOOL` declared as `uint8_t` | struct layout off, `PAINTSTRUCT` 76 instead of 72 | `BOOL` = `int` |
| `biHeight` positive | surface appears vertically flipped | negative |
| `bits` read before `GdiFlush()` | text lags one frame or is blank | flush after `DrawTextW` |
| `SelectObject` return ignored on cleanup | GDI leak, handle exhaustion after ~10k restarts | save and restore |
| `ffi.load("user32")` for `MulDiv` | nil symbol at call time | it lives in `kernel32` on this build |
| `Sleep(1)` without `timeBeginPeriod` | 60 Hz becomes bursty 45/75 fps | `timeBeginPeriod(1)` |
| `os.clock()` for scheduling | drift, nonsense fps | `QueryPerformanceCounter` |
| Lua string cast to `uint8_t*` then used past its length | silent OOB read, rare crash | check `#bytes` |

### WebSocket / protocol

| Pitfall | Symptom | Fix |
|---|---|---|
| Unmasked client frames | server closes / 400 | mask bit + 4 mask bytes |
| Partial JSON fed to `json.decode` | intermittent `expected value` errors | reassemble by `FIN` |
| One `memory.read` per field | game stutters, fps drops | one bulk read per frame |
| Two reads in flight | latency creeps to 30–45 ms | drop new requests while pending |
| Blocking on a ticket | HUD freezes if PPSSPP is paused | 0.5 s timeout, reuse last snapshot |
| `input.buttons.press` for HUD input | reply never arrives while paused | `input.buttons.send` |
| Matching the status line as `Switching Protocols` | handshake rejected by your own client | check code `101` only |
| Assuming `version` returns `pid` | `nil` index error | `FindWindowW("PPSSPPWnd")` |
| Forgetting the subprotocol header | server may not accept the upgrade | `debugger.ppsspp.org` |

### Emulator interaction

* **Every `memory.read` pauses emulation.** Not visible at 1/frame; very visible at 40/frame. Treat the read budget as a first-class design constraint.
* **Paused/stepping emulation ⇒ no replies.** If the user pauses via the debugger, the HUD stops updating; show `PAUSED` (from `game.status` or `cpu.status`) rather than a frozen value that looks live.
* **Observed once during this work:** the debugger accepted the WebSocket handshake but stopped answering JSON on an already-running instance (HTTP `GET /debugger/` still returned 200; an independent Python client also timed out). Not reproduced deliberately, and not diagnosed. Mitigation: watchdog on "no reply for 1 s while we have a pending ticket" → close, reconnect with backoff, and show `DEBUGGER RECONNECTING` in the HUD. **Unverified** whether the server itself wedges or whether the emulator was in a blocking state.
* Do not reconnect in a tight loop. Backoff 1 s, 2 s, 5 s, 10 s, then 30 s.

### Rendering

* `WS_EX_TRANSPARENT` alone is not a guarantee of click-through on every driver; the `HTTRANSPARENT` return is the actual mechanism. Implement both. An actual mouse-through test was **not** performed (PPSSPP was in use by another session).
* Layered windows are not composited over **exclusive fullscreen** flip-model swapchains. In windowed/borderless PPSSPP it works (verified, screenshots). Assume the overlay disappears or tears in exclusive fullscreen, and recommend borderless.
* Re-assert `HWND_TOPMOST` at ~1 Hz, not per frame: if another topmost window takes the z-order, `SetWindowPos` per frame is a needless syscall storm.
* `UpdateLayeredWindow` with `ULW_ALPHA` requires the DIB to be **32bpp top-down with premultiplied BGRA**. Any other combination "works" but looks wrong (dark fringes, ghosting).

---

## 11. Verification log (what was actually run)

| Check | Result |
|---|---|
| LuaJIT available, FFI/bit present | 2.1 ROLLING, Win64 |
| Struct sizes vs SDK | 408 / 16 / 520 / 8 / 80 / 72 / 44 / 4 / 48 — all correct |
| base64 + SHA-1 vs RFC vectors | pass |
| Frame codec, every recv split 0–70000 B, fragmentation, interleaved ping | pass |
| Handshake vs local Python WS server | pass, accept key validated |
| Handshake vs live PPSSPP 1.19.3 | `HTTP/1.1 101 OK`, subprotocol ok |
| `version` | `{"event":"version","version":"v1.19.3","name":"PPSSPP","ticket":1}` |
| `game.status` | `ULES01376 TEKKEN 6` |
| `memory.read` | base64 decoded, correct bytes |
| RTT | `version` 0.94 ms; `memory.read` 15.3 ms avg / 17.4 ms p95 |
| Window discovery | `FindWindowW("PPSSPPWnd")` → pid 16452, client 240,115 960×544 |
| Layered overlay | rendered, moved, text updated, translucent panel + AA text, no focus steal |
| 60 Hz pacing | 60.0 fps avg with `Sleep(1)` + `timeBeginPeriod(1)`; 40–63 ms hitches under load |

Not verified (see §12): long-duration stability, mixed-DPI multi-monitor, exclusive fullscreen, real mouse click-through, Windows 7/8, elevated PPSSPP, debugger wedge root cause.

---

## 12. Open questions / unverified

1. **Stability over minutes-to-hours.** Only short sessions were run. Watch for GDI handle leaks (`GetGuiResources`) and ticket-map growth.
2. **Mixed-DPI multi-monitor.** PPSSPP is system-DPI aware; the overlay matches it, so both stretch together on the secondary monitor but with *different* filters. Alignment on a 125 %/150 % secondary monitor is likely off by a few pixels. Needs a real dual-DPI setup.
3. **Exclusive fullscreen.** Expected not to composite. Borderless is the recommendation.
4. **Click-through.** Implemented (`WS_EX_TRANSPARENT` + `HTTRANSPARENT`), not exercised with a real mouse.
5. **Windows 7/8.** `SetProcessDPIAware`, layered windows and Winsock2 are all available; the DIB mask technique too. `GetDpiForWindow` (used only for diagnostics) is Windows 10+, so guard it. Not tested.
6. **Elevated PPSSPP.** A non-elevated overlay cannot be topmost over an elevated window (UIPI). If PPSSPP runs as admin, the HUD must too.
7. **The observed debugger wedge** (§10, "Emulator interaction"). Unknown cause; watchdog + reconnect is the mitigation.
8. **`memory.read` maximum size** in 1.19.3. 512 B was measured, 4 KB is used in `01`'s examples. Do not jump to 64 KB without testing; chunk and scan offline instead.
9. **Whether the server answers client pings.** The code path exists but was never observed. Do not use ping/pong as the liveness mechanism; a `memory.read` reply is the heartbeat.
10. **Render cost at full 960×544** with many text runs. Estimates only.

---

## 13. Build order (shortest path to a useful HUD)

1. `wslib` + `wsproto` + `wsclient`; connect, `version`, `game.status`, print. (No window.)
2. `overlay` static panel at a hardcoded rect, `hud.draw` with fake data. Verify translucency, AA, no focus steal, click-through.
3. `win32.track` — attach the panel to the client origin; drag/resize PPSSPP while it runs.
4. Pipeline: one `memory.read` per frame, decode into a struct view, render raw hex in the panel. This proves addresses and endianness before any frame-data math exists.
5. Debounce + move state machine + advantage math; colour the numbers.
6. Watchdog, reconnect, clean shutdown, `timeEndPeriod(1)`.
7. Punish transition alert last — it is the feature most sensitive to sampling error.

---

## 14. References

* `tasks/01-ppsspp-debugger.md` — handshake, tickets, fragmentation, `memory.read` semantics, 1.19.3 corrections.
* `tasks/02-tekken6-memory.md` — candidate addresses, translation, discovery protocol.
* RFC 6455 §4.1 (handshake), §5.2 (framing), §5.5 (control frames).
* Microsoft: `UpdateLayeredWindow`, `CreateDIBSection`, `BLENDFUNCTION`, `SetProcessDPIAware`, `select` (Winsock).
* `https://github.com/rxi/json.lua` — MIT, v0.1.2.
