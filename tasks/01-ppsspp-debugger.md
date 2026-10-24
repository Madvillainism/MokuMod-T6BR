# PPSSPP 1.19.3 — WebSocket debugger: exact integration notes

**Target:** `C:\Program Files\PPSSPP\PPSSPPWindows64.exe` (v1.19.3)
**Method:** read upstream source at tag `v1.19.3` (the tag that produced this build — the binary embeds `v1.19.3` and build paths `D:\Temp\BuildTemp\1.19.3_Windows_BUILD\ppsspp\...`), cross-checked against strings extracted from the installed EXE. **PPSSPP was not launched**, nothing was installed, no project files other than this report were touched.

---

## 0. TL;DR

```ini
[General]
RemoteDebuggerOnStartup = True
RemoteISOPort = 8099
```
```
ws://127.0.0.1:8099/debugger
Sec-WebSocket-Protocol: debugger.ppsspp.org
```
Request: `{"event":"version","name":"framemod","version":"0.1","ticket":1}`
Response: `{"event":"version","name":"PPSSPP","version":"v1.19.3","ticket":1}`

Three corrections to assumptions common in this project (see §9):
1. **`memory.search` does not exist in 1.19.3** (only in current master). Use `memory.read` chunks + client-side scan.
2. **`input.buttons.release` does not exist** (not even in master). Use `input.buttons.send` with `false`, or `input.buttons.press` + `duration`.
3. **`version` does not return `pid`/`path`** — only `name` + `version`.

---

## 1. Enabling the debugger at launch

### Config keys (both live in `[General]`)
`Core/Config.cpp` (v1.19.3), lines 296–303:

```cpp
ConfigSetting("RemoteISOPort", &g_Config.iRemoteISOPort, 0, CfgFlag::DEFAULT),
ConfigSetting("RemoteShareOnStartup", &g_Config.bRemoteShareOnStartup, false, CfgFlag::DEFAULT),
ConfigSetting("RemoteISOSubdir", &g_Config.sRemoteISOSubdir, "/", CfgFlag::DEFAULT),
ConfigSetting("RemoteDebuggerOnStartup", &g_Config.bRemoteDebuggerOnStartup, false, CfgFlag::DEFAULT),
```

* `RemoteDebuggerOnStartup` (bool) — starts the web server with the DEBUGGER flag.
* `RemoteISOPort` (int, default **0**) — port for the shared HTTP/WebSocket server. `0` = let the OS pick.
* Section is `[General]` (confirmed against the live `...\PSP\SYSTEM\ppsspp.ini`, where both keys sit under `[General]`).
* This machine's ini **already has** `RemoteISOPort = 8099` and `RemoteDebuggerOnStartup = True`.

### Where it is consumed
`UI/NativeApp.cpp`, end of `NativeInit`:

```cpp
WebServerFlags flags = (WebServerFlags)0;
if (g_Config.bRemoteShareOnStartup) flags |= WebServerFlags::DISCS;
if (g_Config.bRemoteDebuggerOnStartup) flags |= WebServerFlags::DEBUGGER;
if (flags != WebServerFlags::NONE) {
    StartWebServer(WebServerFlags::ALL);      // <-- note: ALL, not `flags`
}
```

Started in `NativeInit`, before the emulation main loop and before the game boots, so the socket is reachable from process start.

> ⚠️ **1.19.3 quirk:** the computed `flags` is discarded — `WebServerFlags::ALL` is passed. Enabling *either* Remote ISO sharing *or* the debugger exposes **both** `/` (disc listing/serving) and `/debugger` on the same port. Sources: `Core/WebServer.cpp` `ExecuteWebServer()` + `HandleFallback()`.

### `--appendconfig` — works, but it is not ephemeral
`UI/NativeApp.cpp` (arg loop, `case '-'`):

```cpp
if (!strncmp(argv[i], "--appendconfig=", ...)) {
    g_Config.SetAppendedConfigIni(Path(...));
    g_Config.LoadAppendedConfig();
}
```
Parsing happens **after** `g_Config.Load()` (L522 vs L542), so appended values override the ini.

> ⚠️ **`LoadAppendedConfig()` calls `Save("Loaded appended config")`** (`Core/Config.cpp` L1161–1176) — the appended values are **written back into the real `ppsspp.ini`**. `--appendconfig` is not a read-only overlay. If a clean profile matters, back up the ini first. (It is re-applied per-game too, `loadGameConfig` → `LoadAppendedConfig()`.)

### `--debugger` / `--debugger-run`: **do not exist in 1.19.3**
The complete Windows arg parser (`UI/NativeApp.cpp`) handles only:
`-d`, `-v`, `-j`, `-i`, `-r`, `-J`, `--loglevel=`, `--log=`, `--state=`, `--escape-exit`, `--pause-menu-exit`, `--fullscreen`, `--windowed`, `--touchscreentest`, `--gamesettings`, `--developertools`, `--appendconfig=`.
Confirmed both by source and by the EXE string table (no `--debugger` token). Those flags exist only in 1.20.x+.

### Port selection and logging
`Core/WebServer.cpp` L417–427:

```cpp
if (!http->Listen(g_Config.iRemoteISOPort, "debugger-webserver")) {
    if (!http->Listen(0, "debugger-webserver")) { ERROR_LOG(...); return; }
}
UpdateStatus(ServerStatus::RUNNING);
g_Config.iRemoteISOPort = http->Port();
```

* Tries the configured port; on failure falls back to **ephemeral** (`0`).
* Actual port from `getsockname()` (`Common/Net/HTTPServer.cpp` L207–211) and logged at **INFO** level:
  `HTTP server started on port %d: %s` → `HTTP server started on port 8099: debugger-webserver`
  (`reason` is the literal `"debugger-webserver"`).
* Binding is `INADDR_ANY` (IPv6 first, then IPv4), **not** loopback-only.
* To *read* that log line from a script you must enable `FileLogging=True` or start with `--log=<file>`; otherwise it goes to PPSSPP's normal log sinks (debug output). Practical answer: **pin the port in config and don't parse logs**.
* Because `g_Config.iRemoteISOPort` is overwritten in memory with the real port and config gets saved later, an ephemeral fallback can be **persisted** into the ini (unverified, but the code path is there).

---

## 2. Transport: URL, handshake, framing

### URL and route
* Exact path: `ws://<host>:<port>/debugger` (handler registered as `http->RegisterHandler("/debugger", &ForwardDebuggerRequest)`).
* `/debugger` without `Upgrade: websocket` → `301` to `/debugger/index.html` (the built-in web UI).
* `/debugger/` → redirect; `/debugger/...` serves the bundled HTML/JS debugger UI from VFS.
* The server sees only the raw path — a query string is stripped before dispatch, but don't rely on it.

### Required handshake headers
`Common/Net/WebsocketServer.cpp` L61–115 (`CreateAsUpgrade`):

```cpp
if (!requireHeader("upgrade", "websocket") || !requireHeaderContains("connection", "upgrade")) { ... 400 ... }
if (!requireHeader("sec-websocket-version", "13")) { ... 400 ... }
...
if (!protocol.empty() && request.GetHeader("sec-websocket-protocol", &requestedProtocols)) {
    if (ListContainsNoCase(requestedProtocols, protocol))
        obtainedProtocolHeader = "Sec-WebSocket-Protocol: " + protocol + "\r\n";
}
if (!request.GetHeader("sec-websocket-key", &key)) { ... 400 ... }
key += WEBSOCKET_GUID;  sha1(...);  Base64Encode(accept, 20);
request.WriteHttpResponseHeader("1.1", 101, -1, "websocket", otherHeaders.c_str());
```

* `Upgrade: websocket` (exact, case-insensitive).
* `Connection:` must **contain the token** `upgrade` (comma-split, case-insensitive).
* `Sec-WebSocket-Version: 13` (exact match; server replies with `Sec-WebSocket-Version: 13` on failure).
* `Sec-WebSocket-Key:` required (any base64 string; not length-validated).
* `Sec-WebSocket-Protocol: debugger.ppsspp.org` — **optional but recommended**; the server only *echoes* it if requested. A raw client can omit it and still connect; a browser client must request it (or nothing).
* Server also needs ≥2 HTTP header lines (`ok = line_count > 1 && resource != nullptr` in `Common/Net/HTTPHeaders.cpp`).

### Response quirks
```
HTTP/1.1 101 OK
Server: PPSSPPServer v0.1
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Accept: <base64(sha1(key+GUID))>
Sec-WebSocket-Protocol: debugger.ppsspp.org
<blank>
```
`WriteHttpResponseHeader` has **no `101` case** in its status switch, so the reason phrase is `OK`, not `Switching Protocols`. **Check the numeric code only.**

### Framing rules (server side)
* **Client frames MUST be masked** — `if ((header[0] & 0x70) != 0 || (header[1] & 0x80) == 0) Close(PROTOCOL_ERROR)`.
* RSV bits must be 0; control frames ≤125 bytes and FIN=1.
* Opcodes accepted: `0x0` CONTINUE, `0x1` TEXT, `0x2` BINARY, `0x8` CLOSE, `0x9` PING, `0xA` PONG. Binary payloads are rejected by the debugger layer (`"Bad message"`).
* **Server never masks.** Length encodings 7/16/64-bit, big-endian.
* Server replies PING with PONG automatically; it never pings on its own.
* **Server sends fragmented text** — `memory.read` is emitted as several TEXT/CONTINUE frames (`Respond(); Flush(); AddFragment(false,"\""); AddFragment(false,<b64>); ...; Finish()`). Reassembly by FIN bit is mandatory.

### Message shape
`Core/Debugger/WebSocket.cpp` L25–45 (upstream comment):

```cpp
// Messages to and from PPSSPP follow the same basic format:
//    { "event": "NAME", ... }
// At start, please send a "version" event.
```
* Request: `{"event":"<name>", <params>, "ticket":<any JSON>}`
* Response: same `event` name, params, and the `ticket` echoed **verbatim** (`Core/Debugger/WebSocket/WebSocketUtils.cpp` `DebuggerJsonAddTicket` → `writer.writeRaw("ticket", json_stringify(value))`). Ticket works on *every* responder, and on errors.
* Error: `{"event":"error","message":"...","level":<int>,"ticket":<echo>}`
  `level` = `LogLevel` enum: **1 NOTICE, 2 ERROR, 3 WARN, 4 INFO, 5 DEBUG, 6 VERBOSE**. `DebuggerRequest::Fail()` always uses `2` (LERROR).
* Unknown event → `error` `"Bad message: unknown event"`; bad JSON → `"Bad message: invalid JSON"`; missing `event` → `"Bad message: no event property"`.
* `version` is *not* enforced — nothing gates other events on it. It only records client name/version server-side.

---

## 3. Event schemas (1.19.3, exact)

### Complete event set in 1.19.3
| Subscriber | Events |
| --- | --- |
| GameSubscriber | `version`, `game.status`, `game.reset` |
| MemorySubscriber | `memory.read_u8`, `memory.read_u16`, `memory.read_u32`, `memory.read`, `memory.readString`, `memory.write_u8`, `memory.write_u16`, `memory.write_u32`, `memory.write` |
| MemoryInfoSubscriber | `memory.mapping`, `memory.info.config`, `memory.info.set`, `memory.info.list`, `memory.info.search` |
| InputSubscriber | `input.buttons.send`, `input.buttons.press`, `input.analog.send` |
| CPUCoreSubscriber | `cpu.stepping`, `cpu.resume`, `cpu.status`, `cpu.getAllRegs`, `cpu.getReg`, `cpu.setReg`, `cpu.evaluate` |
| SteppingSubscriber | `cpu.stepInto`, `cpu.stepOver`, `cpu.stepOut`, `cpu.runUntil`, `cpu.nextHLE` |
| Breakpoint / Disasm / HLE / GPU / Replay / ClientConfig | breakpoints, `memory.disasm`, `memory.assemble`, `memory.searchDisasm`, `hle.*`, `gpu.*`, `replay.*`, `broadcast.config.*` |
| Broadcasters (server→client) | `game.start`, `game.quit`, `game.pause`, `game.resume`, `input.buttons`, `input.analog`, `cpu.stepping`, `log` |

**Absent in 1.19.3:** `memory.search`, `game.speed.*`, `game.state.*`, `game.umd.*`, `client.config.*`, `input.buttons.release`. (`memory.search` maps to `WebSocketMemorySearch` only in master.)

---

### `version`
Request params: `name` (string, optional-loose), `version` (string, optional-loose).
```json
→ {"event":"version","name":"framemod","version":"0.1","ticket":1}
← {"event":"version","name":"PPSSPP","version":"v1.19.3","ticket":1}
```
`name` is always `"PPSSPP"`; `version` is `PPSSPP_GIT_VERSION` (here exactly `v1.19.3`). No `pid`, no `path`.

### `game.status`
No params.
```json
→ {"event":"game.status","ticket":2}
← {"event":"game.status","game":{"id":"ULES01523","version":"1.00","title":"Tekken 6"},"paused":false,"ticket":2}
```
`game` is `null` until `PSP_GetBootState() == BootState::Complete`. `paused` = UI state is the pause menu (not stepping).

### `memory.read_u8 / read_u16 / read_u32`
Params: `address` (uint32: JSON integer, or string parsed by `strtoul(...,0)` → `"0x08800000"` works).
```json
→ {"event":"memory.read_u32","address":142606336,"ticket":3}
← {"event":"memory.read_u32","value":305419896,"ticket":3}
```
Errors: `"CPU not started"`, `"Invalid address"`. Non-integer numbers are rejected (`"integer required"`).

### `memory.read` (bulk, base64, fragmented)
Params: `address` (uint), `size` (uint), `replacements` (bool, optional, default `true`).
```json
→ {"event":"memory.read","address":142606336,"size":4096,"ticket":4}
← frame 1 (TEXT, FIN=0): {"event":"memory.read","ticket":4,"base64":
← frame 2 (CONTINUE):    "
← frame 3 (CONTINUE):    <base64 chars, 65535 per chunk>
← frame 4 (CONTINUE):    "
← frame 5 (CONTINUE,FIN=1):}
```
Ticket is emitted with the header fragment, the base64 body is streamed afterwards.
**Reassemble all CONTINUE frames before parsing — never parse a single frame.**
Errors: `"Invalid address"`, `"Invalid size"` (range must satisfy `Memory::IsValidRange`).
Practical read sizes: 4–64 KB. PSP user RAM starts at `0x08800000`; size up to ~24 MB is legal but a single giant read stalls the CPU thread and produces ~500 frames.

### `memory.write_u8 / u16 / u32` / `memory.write`
`{"event":"memory.write_u32","address":...,"value":...}` → `{"event":"memory.write_u32","value":<new value>}`
`{"event":"memory.write","address":...,"base64":"..."}` → `{"event":"memory.write"}` (no extra data).

### `memory.readString`
`address`, optional `type`: `"utf-8"` (default) → `value`; `"base64"` → `base64` (NUL excluded).

### `memory.search` — **NOT AVAILABLE**
There is no value/pattern scanner in 1.19.3. Available lookalikes:
* `memory.info.search` — searches **allocation tags**, not values: `{address,end,match,type}` → `{extent:{type,address,size,ticks,pc,tag,allocated}}`.
* `memory.searchDisasm` — searches **disassembly text**/symbols.
* For value scanning: loop `memory.read` over `0x08800000 + 24 MB` in 32–64 KB chunks and scan in Lua (≈400–700 round trips; run it once, offline, never per-frame).

### `input.buttons.press` (deferred reply!)
Params: `button` (string, required), `duration` (uint frames, optional, default **1**; negative rejected), `ticket`.
```json
→ {"event":"input.buttons.press","button":"cross","duration":2,"ticket":7}
   ... no immediate reply ...
← {"event":"input.buttons.press","ticket":7}      // sent once the button is released
```
Mechanics (`InputSubscriber::ButtonsPress` + `Broadcast`): the button is set immediately via `__CtrlUpdateButtons(button,0)`; a pending entry stores `duration`; each **VBlank** decrements it; at `-1` the button is cleared and the reply is sent. `duration:1` ⇒ pressed during frame N, released at the next vblank.
**If the emulator is paused or stepping, no vblanks occur and the reply never arrives.**

### `input.buttons.send` (immediate, state-based) — the real "release"
Param: `buttons` — object of `<name>: true|false` (all keys must be known; `null` ignored).
```json
→ {"event":"input.buttons.send","buttons":{"cross":true,"down":false},"ticket":8}
← {"event":"input.buttons.send","ticket":8}
```
`__CtrlUpdateButtons(down, up)` masks with `CTRL_MASK_USER`. This is the closest thing to `input.buttons.release`: `{"buttons":{"cross":false}}`.

### `input.analog.send`
`x`, `y` numbers in [-1,1]; `stick` optional `"left"` (default) / `"right"`.

### `game.reset`
Param: `break` (bool, optional) — sets `PSP_CoreParameter().startBreak` so you can catch the CPU at boot.
```json
→ {"event":"game.reset","break":true,"ticket":9}
← {"event":"game.reset","ticket":9}     // after PSP_Reboot() returns
```
Fails with `"Game not running"` unless `BootState::Complete`. Synchronous and slow (full reboot); memory reads during it will fail.

### `cpu.status`
No params.
```json
← {"event":"cpu.status","stepping":false,"paused":false,"pc":2164260864,"ticks":123456789.0,"ticket":10}
```
`stepping` = `Core_IsStepping() && coreState != CORE_POWERDOWN`; `paused` = UI state is not in-game (pause menu); `pc` is only meaningful while stepping; `ticks` is a **double** (CoreTiming ticks) — the best monotonic clock available.

### Frame-stepping / frame-tick events
* `cpu.stepping` (request `{"event":"cpu.stepping"}`) → CPU breaks, and a **`cpu.stepping` event is broadcast** when it stops. `cpu.resume` reverses it and also broadcasts.
* `cpu.stepInto` / `stepOver` / `stepOut` / `runUntil {address}` / `nextHLE` — no immediate response; a `cpu.stepping` event arrives when the step completes (instruction-level, not frame-level).
* `input.buttons` broadcast — sent once per **vblank** when the button state changes; effectively a 60 Hz tick you get for free.
* `gpu.stats.feed {"enable":true}` → a `gpu.stats.get` event **every frame** (`fps`, `vblanksPerSecond`, `timing.frames[]`, `timing.pos`). Heaviest but the only true per-frame event.
* There is **no** "frame advanced" notification tied to the game's own frame counter; derive frame identity from `cpu.status.ticks` or the `input.buttons`/`gpu.stats` broadcasts.

---

## 4. Button names for `input.buttons.press`

`Core/Debugger/WebSocket/InputSubscriber.cpp` L28–59 (the authoritative map, also used by the broadcaster):

`cross, circle, triangle, square, up, down, left, right, start, select, home, screen, note, ltrigger, rtrigger, hold, wlan, remote_hold, vol_up, vol_down, disc, memstick, forward, back, playpause, l2, l3, r2, r3`

Notes:
* Values are the raw `CTRL_*` bitmasks; `__CtrlUpdateButtons` masks with `CTRL_MASK_USER`, so `home`, `vol_*`, `screen`, `note`, `disc`, `memstick`, `wlan`, `hold`, `remote_hold` and the headset keys are accepted by the parser but may have no effect.
* Wrong name → `{"event":"error","message":"Unsupported button value 'x'","level":2}`.

### Can it navigate menus?
* **Yes for the game's own menus** (Tekken 6 character select, pause menu inside the game, training menu): these read `sceCtrl` state, which is exactly what `__CtrlUpdateButtons` modifies.
* **No for PPSSPP's UI** (the emulator pause menu, settings, the built-in debugger UI): those are ImGui/`ScreenManager` states and ignore emulated PSP input.
* Timing: `duration` is counted in **vblanks**, so it is frame-accurate regardless of your host loop rate, but it requires the emulation to be running (not the pause menu).

---

## 5. `--state=<file>` in 1.19.3

**Yes, it exists and works.** `UI/NativeApp.cpp`:

```cpp
if (!strncmp(argv[i], "--state=", strlen("--state=")) && strlen(argv[i]) > strlen("--state="))
    stateToLoad = Path(std::string(argv[i] + strlen("--state=")));
...
if (!boot_filename.empty() && stateToLoad.Valid()) {
    SaveState::Load(stateToLoad, -1, [](SaveState::Status status, std::string_view message) { ...OSD... });
}
```

Behaviour:
* Syntax: `PPSSPPWindows64.exe "--state=C:\path\to\file.ppst" "C:\...\game.iso"` — the whole flag must be one argv token, so quote it; **no space around `=`**.
* Requires a boot filename in the same command line; `--state` alone does nothing.
* `slot = -1` ⇒ load **from that file**, not from a numbered slot. The extension is not required but `.ppst` is conventional.
* It is *queued*: `SaveState::Load` calls `Core_Break` + `Enqueue(SAVESTATE_LOAD, ...)` (`Core/SaveState.cpp` L429–437), so the load is executed by the CPU thread once emulation starts, i.e. **shortly after boot, not before window creation**.
* Failure is reported only through an OSD message (`Failed to load state…`), not via exit code. Game-ID/version mismatch = no load, game stays at boot.
* State path may be absolute or relative to the process CWD. Keep it under the project directory.
* Nothing in the websocket reports "state loaded"; poll `game.status` / `cpu.status.ticks` and verify a known memory value.

---

## 6. Limits, threads, and gotchas

1. **Every memory access pauses emulation.** `MemorySubscriber::LockMemoryAndCPU` does `Core_Break(BreakReason::MemoryAccess, addr); Core_WaitInactive();` unless the CPU is already stepping, then resumes in the destructor. Reads are coherent-at-frame-boundary but *expensive* — this is a full stop/start handshake with the CPU thread. One 4 KB `memory.read` per frame is fine; 40 × `read_u32` per frame is not.
2. **`memory.read` replies are fragmented** (TEXT then CONTINUE frames). A client that reads one frame gets truncated JSON.
3. **`input.buttons.press` has a deferred response.** Never block the whole socket on it; correlate with `ticket` and keep processing. No vblank ⇒ no reply ⇒ hung client.
4. **`broadcast.config.set` is racy in 1.19.3.** `ClientConfigSubscriber.cpp` L81–85 looks the key up with `disallowed_config.find(key)` and fails with `"Unsupported 'disallowed' object key 'logger'"` if it is absent. The map is only populated lazily, by `WebSocket.cpp` L193–200 using `operator[]` during the broadcast pass that runs *after* the first `Process()` iteration. So a `broadcast.config.set` sent as the **first** frame on a fresh socket usually fails; a retry after one frame (~16 ms) succeeds for exactly `logger|game|stepping|input`. Treat it as flaky and filter broadcasts client-side anyway.
5. **Default broadcast volume:** the connector registers a log callback immediately (`LogBroadcaster`), so you will receive a burst of up to 1024 buffered `log` events on connect, and more as the game runs. Budget for it.
6. **Ordering:** one thread per connection (`NewThreadExecutor` spawns a `std::thread` per accepted socket), requests are handled **sequentially** in that thread; responses are ordered **except** deferred ones (`input.buttons.press`, stepping events). Pipelining N requests gains nothing except latency hiding.
7. **Poll cadence:** the loop is `ws->Process(highActivity ? 1ms : 1/60s)`; it wakes immediately on incoming data, so round-trip latency is dominated by the CPU pause/resume, not by the 16.7 ms timeout. After any non-immediate response it polls at 1 kHz for 1 s.
8. **`lifecycleLock`:** the debugger holds a global mutex while dispatching each event (`WebSocket.cpp` L172–181), and the emulation thread takes the same mutex during `STARTING`/`STOPPING`/`MEMORY_REINITING`. Avoid memory ops during boot / `game.reset` / state load / shutdown. Shutdown also blocks until all debugger connections close (`StopAllDebuggers`), and `game.reset` will stall your socket while the core reboots.
9. **No auth, no TLS, binds `INADDR_ANY`.** Anyone on the LAN can read/write PSP memory and inject input. Firewall the port or keep it on a trusted network.
10. **Multiple clients are allowed** but each has its own state (press queue, log cursor, client info). Don't split reads and input across sockets.
11. **Backlog 1024**, no keepalive/ping from the server, close code on shutdown = `GOING_AWAY`.
12. **No rate limit as such** — the limit is the CPU pause cost and the 60 Hz vblank cadence.
13. **`game.status` is cheap** (no CPU pause) — poll that freely; `cpu.status` is also cheap.
14. Address space (`memory.mapping` if you need it): user RAM `0x08800000`, scratchpad `0x00010000`, VRAM `0x04000000`, kernel `0x08000000`/`0x88000000` mirrors.

---

## 7. LuaJIT FFI plan (raw WebSocket over `ws2_32`) — design only

**Critical constraint:** PPSSPP's built-in Lua console cannot do this. `Core/LuaContext.cpp` opens only `sol::lib::base/table/bit32/string/math` — no `io`, no `os`, no `package`, no `ffi`, and only `r32`/`w32`/`print/debug/info/warn/error`/`ver` are exposed. The FFI client must run in a **separate host process** (standalone LuaJIT, or a LuaJIT embedded in the HUD app) that talks to PPSSPP over the socket.

**A. FFI declarations (`ffi.cdef`)**
- Types: `SOCKET`/`uintptr_t` (Windows SOCKET is `UINT_PTR`, 64-bit here — never `int`), `sockaddr_in` (`short sin_family; ushort sin_port; uint32 sin_addr; char pad[8]`), `WSADATA` (opaque 400-byte struct is enough).
- Functions: `WSAStartup(0x202, &wsadata)`, `WSACleanup`, `socket(AF_INET=2, SOCK_STREAM=1, IPPROTO_TCP=6)`, `connect`, `send`, `recv`, `closesocket`, `setsockopt`, `ioctlsocket`, `htons`, `inet_addr`.
- Constants that bite on Windows: `SOL_SOCKET = 0xFFFF` (not 1), `SO_RCVTIMEO = 0x1006`, `TCP_NODELAY = 1` (`IPPROTO_TCP`), `FIONBIO = 0x8004667E`.
- Prefer `SO_RCVTIMEO` (a `DWORD` of milliseconds) over `select()`: `fd_set` layout on Win64 (`u_int fd_count; SOCKET fd_array[64];`) is easy to get wrong in FFI.

**B. Handshake**
1. 16 random bytes → base64 (pure-Lua base64 encode; ~20 lines) → `Sec-WebSocket-Key`.
2. `send()`:
   `GET /debugger HTTP/1.1\r\nHost: 127.0.0.1:<port>\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: <key>\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: debugger.ppsspp.org\r\n\r\n`
3. `recv()` in a loop until `\r\n\r\n`; assert the status line matches `^HTTP/1.1 101` (reason phrase is `OK`).
4. Optional: verify `Sec-WebSocket-Accept == base64(sha1(key .. GUID))`; SHA-1 must be implemented in Lua (~60 lines) since LuaJIT has no digest. Skipping is acceptable on loopback.
5. `setsockopt(TCP_NODELAY)` right after connect, otherwise Nagle can add ~40 ms to small request bursts.

**C. Frame writer**
- `b1 = 0x80 | opcode` (0x1 text, 0x8 close, 0xA pong).
- `b2 = 0x80 | len` for len ≤125; `126` + 2-byte BE; `127` + 8-byte BE.
- 4 random mask bytes, then payload XOR `mask[i & 3]`.
- One `send()` per frame (payload assembled in a Lua string; avoid per-byte FFI calls).

**D. Frame reader (the hard part)**
- Maintain `rbuf`; loop `recv()` into a 64 KB scratch `uint8_t[65536]`, append with `ffi.string(buf, n)`.
- Parse: need ≥2 bytes; compute header length (2 + (0/2/8) + (0/4)); need full payload else wait.
- Server frames are **unmasked** (`b2 & 0x80 == 0`) — assert it.
- Accumulate `opcode 0x1/0x2` + `0x0` continuations until `FIN=1` into `msg`; only then hand `msg` to JSON.
- `0x9` PING → immediately send `0xA` PONG with the same payload; `0x8` CLOSE → send masked close and stop.
- Handle partial frames across `recv()` calls and multiple frames per `recv()`.
- Recommended: a blocking read loop with `SO_RCVTIMEO = 250 ms`, `WSAETIMEDOUT (10060)` treated as "no data".

**E. JSON**
- Encode with `string.format` and a strict string escaper (`\" \\ \n \r \t \uXXXX`), always including `"ticket"`.
- Decode with a small recursive-descent parser (or dkjson vendored as one file — **not** `require"cjson"`, which is not guaranteed present). Must tolerate `null`, integers, doubles and the `ticks` float.
- Dispatch by `msg.event`; keep `tickets[msg.ticket]` → callback so `input.buttons.press` and `cpu.stepping` can complete later; ignore `log`/`input.buttons`/`game.*` unless needed.
- Decode `base64` with a pure-Lua decoder into a Lua string; then `string.unpack`/`ffi.cast("uint32_t*", ...)` (little-endian on x86) or explicit byte math for the fields you need.

**F. Lifecycle**
- Connect → `version` → `game.status` (poll until `game` non-null) → optional `broadcast.config.set` (ignore the failure) → main loop at 60 Hz: one `memory.read` for the whole watch-block, plus `cpu.status` every N frames.
- On send failure / `recv` returning 0 → reconnect with backoff; PPSSPP allows reconnecting while it runs.
- Never call `cpu.stepping` in gameplay; it stops the emulator. `memory.read` already synchronizes.

---

## 8. References (tag `v1.19.3`)

| Topic | Path |
| --- | --- |
| Debugger entry, event dispatch, tickets | `Core/Debugger/WebSocket.cpp` L25–45, L130–187 |
| Param parsing, ticket echo | `Core/Debugger/WebSocket/WebSocketUtils.cpp` L26–58, L90–93 |
| Handshake, masking, framing | `Common/Net/WebsocketServer.cpp` L61–115, L267–300, L454–486 |
| HTTP server, port log, 101 response | `Common/Net/HTTPServer.cpp` L88–124, L207–211 |
| Request parser requirement | `Common/Net/HTTPHeaders.cpp` (`ParseHeaders`: `ok = line_count > 1 && resource != nullptr`) |
| Server startup, flags, `/debugger` route | `Core/WebServer.cpp` L304–307, L383–427, L446–469 |
| Config keys/defaults, append-config save | `Core/Config.cpp` L296–303, L1161–1176 |
| Arg parsing, `--state`, web server start | `UI/NativeApp.cpp` (`case '-'`, `stateToLoad`, `StartWebServer(WebServerFlags::ALL)`) |
| Memory events, CPU pause | `Core/Debugger/WebSocket/MemorySubscriber.cpp` L32–45, L61–104, L106–227 |
| Buttons | `Core/Debugger/WebSocket/InputSubscriber.cpp` L28–59, L169–217 |
| Game/version events | `Core/Debugger/WebSocket/GameSubscriber.cpp` L25–31, L41–112 |
| CPU status / stepping | `Core/Debugger/WebSocket/CPUCoreSubscriber.cpp` L30–41, L99–119; `.../SteppingSubscriber.cpp` L52–61 |
| Broadcasters | `GameBroadcaster.cpp` L76–94, `InputBroadcaster.cpp` L51–102, `LogBroadcaster.cpp` L93–123 |
| Broadcast config (racy) | `ClientConfigSubscriber.cpp` L69–96; `WebSocket.cpp` L193–200 |
| Input injection primitive | `Core/HLE/sceCtrl.cpp` L208–215 (`__CtrlUpdateButtons`) |
| Break/inactive semantics | `Core/Core.cpp` (`Core_Break`, `Core_WaitInactive`, `Core_IsStepping`) |
| Lua sandbox (no ffi/socket) | `Core/LuaContext.cpp` L63–84 |
| Savestate queue | `Core/SaveState.cpp` L429–437 |

Raw URLs: `https://raw.githubusercontent.com/hrydgard/ppsspp/v1.19.3/<path>`
Binary cross-checks: `v1.19.3`, `debugger.ppsspp.org`, `--state=`, `--appendconfig=`, `HTTP server started on port %d`, `RemoteDebuggerOnStartup`, `RemoteISOPort`, `memory.read*` (no `memory.search`), `input.buttons.{press,send}`, `cpu.status`.

---

## 9. Verified vs. unverified

### Verified against v1.19.3 source **and** the installed binary
* Config keys `RemoteDebuggerOnStartup` / `RemoteISOPort`, `[General]` section, default port `0`.
* Web server starts at app init (before boot); `StartWebServer(WebServerFlags::ALL)` regardless of which flag was set.
* `--appendconfig=` exists, is parsed after `g_Config.Load()` (so it overrides), **and persists** via `Save()`.
* No `--debugger` / `--debugger-run` in 1.19.3.
* Port fallback to ephemeral, `g_Config.iRemoteISOPort` updated, log line `HTTP server started on port %d: debugger-webserver`.
* URL `ws://host:port/debugger`, subprotocol `debugger.ppsspp.org` (optional echo), handshake header requirements, `101` + reason `OK`.
* Client frames must be masked; server never masks; server fragments `memory.read`.
* Message shape `{"event":...}`, `ticket` echo, `error` shape and numeric `level`.
* Exact schemas for `version`, `game.status`, `memory.read*`, `memory.write*`, `input.buttons.press/send`, `input.analog.send`, `game.reset`, `cpu.status`, `cpu.*` stepping.
* Button name table (29 names) and vblank-based `duration` semantics; `__CtrlUpdateButtons` masks with `CTRL_MASK_USER`.
* **`memory.search` and `input.buttons.release` do not exist in 1.19.3.**
* `--state=` syntax, `slot=-1`, queued load, requires a boot filename, OSD-only error reporting.
* Memory ops break and resume the CPU; response fragmentation; `broadcast.config.set` race (map populated after first loop pass); one thread per connection; no auth/loopback restriction.

### Unverified / inferred (flagged)
* That an ephemeral fallback port gets **persisted** into `ppsspp.ini` (code path exists: in-memory value + later `Config::Save`).
* The `lifecycleLock` ↔ `Core_WaitInactive` **deadlock window** during boot/reset — reasoned from `WebSocket.cpp` L101–121/L172–181 plus `Core_WaitInactive`, never reproduced.
* Exact frame count for `memory.read` (code implies 4–5: header, `"`, base64 chunk(s), `"`, `}`).
* Real-world round-trip latency / max sustainable poll rate on this machine (not measured; PPSSPP not launched).
* Whether `home`/`vol_*`/`note`/`screen` injected bits have any in-game effect (they are masked to `CTRL_MASK_USER`; Tekken 6 likely ignores them).
* Tekken 6 frame data addresses, savestate IDs/versions — out of scope for this task.
* Whether the v1.19.3 tag's `ClientConfigSubscriber` bug was later fixed (master has a different `client.config.*` API; not compared line-by-line).

---

## 10. Top 3 risks

1. **Measurement perturbance.** Every `memory.read`/`read_u32` breaks the emulation thread and waits for it to go inactive, then resumes. A naive per-field polling design (10–40 reads/frame) will visibly stutter the game and can distort the very frame timings the HUD is supposed to measure. Mitigation: one bulk `memory.read` per frame over a contiguous watch block, cache, and debounce on state changes (as `AGENTS.md` already prescribes).
2. **Hang/deadlock windows around lifecycle transitions.** The debugger dispatches each event while holding the global `lifecycleLock`, which the emulation thread also takes during `STARTING`/`STOPPING`/`MEMORY_REINITING`; issue a memory op in that window and you can stall the socket or the emulator (worst case: read waiting for "inactive" while the core waits for the lock). Compound this with `input.buttons.press`, whose reply only arrives on a vblank — a paused emulator means a permanently pending request. Mitigation: gate all requests on `game.status`/`cpu.status`, add hard timeouts, treat missing replies as connection loss and reconnect.
3. **Assumption drift from the in-repo skill.** The project's `ppsspp` skill claims `memory.search`, `version` returning `pid`/`path`, and `--appendconfig` being side-effect free. All three are false for 1.19.3 (search exists only in master; version returns only `name`/`version`; append-config calls `Config::Save`). Building the scanner or the bootstrap on those assumptions produces silent failures or an overwritten `ppsspp.ini`. Mitigation: treat this report as the contract for 1.19.3, and pin the binary version before relying on any event list.
