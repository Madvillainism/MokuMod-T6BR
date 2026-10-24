// t6padd.mjs — persistent PPSSPP telemetry daemon.
//
// PPSSPP 1.19.3's debugger crashes (0xc0000005) when clients connect and
// disconnect around game-state transitions, so this daemon owns exactly one
// long-lived WebSocket and re-exposes it as a simple line-delimited JSON TCP
// service on 127.0.0.1:8098.  Python tooling and the LuaJIT HUD both talk to
// this port instead of re-implementing WebSocket.
//
//   client -> {"cmd":"press","button":"cross","frames":3}
//   client -> {"cmd":"raw","event":"game.status"}
//   client -> {"cmd":"ping"}
//   daemon -> {"ok":true,"ticket":5}
//   daemon -> {"rx":<any PPSSPP event>}
//
// Usage: node tools/t6padd.mjs [--port 8099] [--tcp 8098] [--verbose]
import net from "node:net";
import crypto from "node:crypto";

const argv = process.argv.slice(2);
function arg(name, def) {
  const i = argv.indexOf("--" + name);
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : def;
}
const WS_PORT = Number(arg("port", "8099"));
const TCP_PORT = Number(arg("tcp", "8098"));
const VERBOSE = argv.includes("--verbose");
const HOST = "127.0.0.1";

function log(...a) {
  if (VERBOSE) console.log(new Date().toISOString().slice(11, 23), ...a);
}

// ---------------------------------------------------------------- local bus
const clients = new Set();
function broadcast(obj) {
  const line = JSON.stringify(obj) + "\n";
  for (const c of clients) {
    if (!c.destroyed) c.write(line);
  }
}

// ------------------------------------------------------------- ws -> ppsspp
let sock = null;
let handshaken = false;
let frameBuf = Buffer.alloc(0);
let headerBuf = Buffer.alloc(0);
let ticket = 1;
let reconnectDelay = 1000;
let closedByUs = false;

function wsFrame(opcode, payload) {
  const mask = crypto.randomBytes(4);
  let header;
  if (payload.length < 126) {
    header = Buffer.from([0x80 | opcode, 0x80 | payload.length]);
  } else if (payload.length < 65536) {
    header = Buffer.alloc(4);
    header[0] = 0x80 | opcode;
    header[1] = 0x80 | 126;
    header.writeUInt16BE(payload.length, 2);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x80 | opcode;
    header[1] = 0x80 | 127;
    header.writeBigUInt64BE(BigInt(payload.length), 2);
  }
  const masked = Buffer.alloc(payload.length);
  for (let i = 0; i < payload.length; i++) masked[i] = payload[i] ^ mask[i % 4];
  return Buffer.concat([header, mask, masked]);
}

function sendJson(obj) {
  if (!sock || !handshaken) throw new Error("debugger not connected");
  const msg = { ticket: obj.ticket ?? ticket++, ...obj };
  const text = JSON.stringify(msg);
  log("TX", text);
  sock.write(wsFrame(0x1, Buffer.from(text, "utf8")));
  return msg.ticket;
}

function handleFrames() {
  for (;;) {
    if (frameBuf.length < 2) return;
    const b1 = frameBuf[1];
    const opcode = frameBuf[0] & 0x0f;
    const masked = !!(b1 & 0x80);
    let len = b1 & 0x7f;
    let off = 2;
    if (len === 126) {
      if (frameBuf.length < 4) return;
      len = frameBuf.readUInt16BE(2);
      off = 4;
    } else if (len === 127) {
      if (frameBuf.length < 10) return;
      len = Number(frameBuf.readBigUInt64BE(2));
      off = 10;
    }
    if (masked) off += 4;
    if (frameBuf.length < off + len) return;
    const payload = frameBuf.subarray(off, off + len);
    frameBuf = frameBuf.subarray(off + len);

    if (opcode === 0x1 || opcode === 0x2) {
      const text = payload.toString("utf8");
      log("RX", text);
      let obj;
      try {
        obj = JSON.parse(text);
      } catch {
        obj = { raw: text };
      }
      broadcast({ rx: obj });
    } else if (opcode === 0x8) {
      log("server close frame");
      sock.end();
      return;
    } else if (opcode === 0x9) {
      sock.write(wsFrame(0xa, payload));
    }
  }
}

function connect() {
  closedByUs = false;
  handshaken = false;
  headerBuf = Buffer.alloc(0);
  frameBuf = Buffer.alloc(0);
  const key = crypto.randomBytes(16).toString("base64");
  sock = net.connect({ host: HOST, port: WS_PORT });

  sock.on("connect", () => {
    sock.write(
      [
        "GET /debugger HTTP/1.1",
        `Host: ${HOST}:${WS_PORT}`,
        "Upgrade: websocket",
        "Connection: Upgrade",
        `Sec-WebSocket-Key: ${key}`,
        "Sec-WebSocket-Version: 13",
        "Sec-WebSocket-Protocol: debugger.ppsspp.org",
        "",
        "",
      ].join("\r\n")
    );
  });

  sock.on("data", (chunk) => {
    if (!handshaken) {
      headerBuf = Buffer.concat([headerBuf, chunk]);
      const end = headerBuf.indexOf("\r\n\r\n");
      if (end < 0) return;
      if (!headerBuf.subarray(0, end).toString("latin1").startsWith("HTTP/1.1 101")) {
        log("handshake rejected");
        sock.destroy();
        return;
      }
      handshaken = true;
      reconnectDelay = 1000;
      frameBuf = headerBuf.subarray(end + 4);
      log("debugger connected");
      broadcast({ connected: true });
      handleFrames();
      return;
    }
    frameBuf = Buffer.concat([frameBuf, chunk]);
    handleFrames();
  });

  sock.on("error", (e) => {
    log("ws error", e.message);
  });

  sock.on("close", () => {
    handshaken = false;
    broadcast({ connected: false });
    if (closedByUs) return;
    const delay = reconnectDelay;
    reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    log(`ws closed, retry in ${delay}ms`);
    setTimeout(connect, delay);
  });
}

function closeGracefully() {
  closedByUs = true;
  if (sock && handshaken) {
    const status = Buffer.from([0x03, 0xe8]); // 1000 normal closure
    sock.write(wsFrame(0x8, status));
    setTimeout(() => sock.end(), 120);
  } else if (sock) {
    sock.destroy();
  }
}

// ------------------------------------------------------------- local server
const server = net.createServer((c) => {
  clients.add(c);
  c.setEncoding("utf8");
  let buf = "";
  c.on("data", (d) => {
    buf += d;
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      let msg;
      try {
        msg = JSON.parse(line);
      } catch {
        c.write(JSON.stringify({ ok: false, error: "bad json" }) + "\n");
        continue;
      }
      try {
        if (msg.cmd === "ping") {
          c.write(JSON.stringify({ ok: true, pong: Date.now() }) + "\n");
        } else if (msg.cmd === "status") {
          c.write(JSON.stringify({ connected: handshaken }) + "\n");
        } else if (msg.cmd === "press") {
          const t = sendJson({
            event: "input.buttons.press",
            button: msg.button,
            duration: msg.frames ?? 2,
          });
          c.write(JSON.stringify({ ok: true, ticket: t }) + "\n");
        } else if (msg.cmd === "buttons") {
          const t = sendJson({
            event: "input.buttons.send",
            buttons: msg.buttons || {},
          });
          c.write(JSON.stringify({ ok: true, ticket: t }) + "\n");
        } else if (msg.cmd === "raw") {
          const { cmd, ...rest } = msg;
          const t = sendJson(rest);
          c.write(JSON.stringify({ ok: true, ticket: t }) + "\n");
        } else {
          c.write(JSON.stringify({ ok: false, error: "unknown cmd" }) + "\n");
        }
      } catch (e) {
        c.write(JSON.stringify({ ok: false, error: e.message }) + "\n");
      }
    }
  });
  c.on("close", () => clients.delete(c));
  c.on("error", () => clients.delete(c));
});

server.listen(TCP_PORT, HOST, () => log(`local bus on ${HOST}:${TCP_PORT}`));
connect();

for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    closeGracefully();
    setTimeout(() => process.exit(0), 250);
  });
}
