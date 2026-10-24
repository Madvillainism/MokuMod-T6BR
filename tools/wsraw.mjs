// Raw RFC6455 client for PPSSPP's debugger (no external deps).
// Usage:
//   node tools/wsraw.mjs --port 8099 --json '{"event":"version"}' --wait 1500
//   node tools/wsraw.mjs --port 8099 --json '{"event":"input.buttons.press","button":"start","duration":2}' --wait 2000
//   node tools/wsraw.mjs --port 8099 --read 0x08800000 64
import net from "node:net";
import crypto from "node:crypto";

const args = process.argv.slice(2);
function arg(name, def) {
  const i = args.indexOf("--" + name);
  return i >= 0 && i + 1 < args.length ? args[i + 1] : def;
}

const port = Number(arg("port", "8099"));
const host = arg("host", "127.0.0.1");
const waitMs = Number(arg("wait", "1500"));
const requests = [];

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--json") requests.push(JSON.parse(args[i + 1]));
  if (args[i] === "--read") {
    requests.push({
      event: "memory.read",
      address: Number(args[i + 1]),
      size: Number(args[i + 2] || 4),
    });
  }
}
if (!requests.length) {
  requests.push({ event: "version", name: "framemod-wsraw", version: "0.1.0" });
  requests.push({ event: "game.status" });
}

const key = crypto.randomBytes(16).toString("base64");
const sock = net.connect({ host, port });
let handshaken = false;
let headerBuf = Buffer.alloc(0);
let frameBuf = Buffer.alloc(0);
let ticket = 1;

function sendFrame(text) {
  const payload = Buffer.from(text, "utf8");
  const mask = crypto.randomBytes(4);
  let header;
  if (payload.length < 126) {
    header = Buffer.from([0x81, 0x80 | payload.length]);
  } else if (payload.length < 65536) {
    header = Buffer.alloc(4);
    header[0] = 0x81;
    header[1] = 0x80 | 126;
    header.writeUInt16BE(payload.length, 2);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x81;
    header[1] = 0x80 | 127;
    header.writeBigUInt64BE(BigInt(payload.length), 2);
  }
  const masked = Buffer.alloc(payload.length);
  for (let i = 0; i < payload.length; i++) masked[i] = payload[i] ^ mask[i % 4];
  sock.write(Buffer.concat([header, mask, masked]));
}

function sendJson(obj) {
  const msg = { ticket: obj.ticket ?? ticket++, ...obj };
  if (obj.ticket === undefined) msg.ticket = msg.ticket;
  console.log("->", JSON.stringify(msg));
  sendFrame(JSON.stringify(msg));
}

function handleMessage(text) {
  console.log("<-", text);
}

function handleFrames() {
  for (;;) {
    if (frameBuf.length < 2) return;
    const b0 = frameBuf[0];
    const b1 = frameBuf[1];
    const opcode = b0 & 0x0f;
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
    let mask;
    if (masked) {
      if (frameBuf.length < off + 4) return;
      mask = frameBuf.subarray(off, off + 4);
      off += 4;
    }
    if (frameBuf.length < off + len) return;
    let payload = frameBuf.subarray(off, off + len);
    if (masked) {
      payload = Buffer.from(payload);
      for (let i = 0; i < payload.length; i++) payload[i] ^= mask[i % 4];
    }
    frameBuf = frameBuf.subarray(off + len);

    if (opcode === 0x1 || opcode === 0x2) {
      handleMessage(payload.toString("utf8"));
    } else if (opcode === 0x8) {
      console.log("server close");
      sock.end();
      return;
    } else if (opcode === 0x9) {
      // pong
      const pong = Buffer.from([0x8a, 0x80, 0, 0, 0, 0, ...payload]);
      sock.write(pong);
    }
  }
}

sock.on("connect", () => {
  const req = [
    "GET /debugger HTTP/1.1",
    `Host: ${host}:${port}`,
    "Upgrade: websocket",
    "Connection: Upgrade",
    `Sec-WebSocket-Key: ${key}`,
    "Sec-WebSocket-Version: 13",
    "Sec-WebSocket-Protocol: debugger.ppsspp.org",
    "",
    "",
  ].join("\r\n");
  sock.write(req);
});

sock.on("data", (chunk) => {
  if (!handshaken) {
    headerBuf = Buffer.concat([headerBuf, chunk]);
    const end = headerBuf.indexOf("\r\n\r\n");
    if (end < 0) return;
    const head = headerBuf.subarray(0, end).toString("latin1");
    if (!head.startsWith("HTTP/1.1 101")) {
      console.error("handshake failed:\n" + head);
      process.exit(2);
    }
    console.log("handshake 101 ok");
    handshaken = true;
    frameBuf = headerBuf.subarray(end + 4);
    handleFrames();
    for (const r of requests) sendJson(r);
    setTimeout(() => {
      sock.end();
      setTimeout(() => process.exit(0), 100);
    }, waitMs);
    return;
  }
  frameBuf = Buffer.concat([frameBuf, chunk]);
  handleFrames();
});

sock.on("error", (e) => {
  console.error("socket error:", e.message);
  process.exit(1);
});
sock.on("close", () => {
  console.log("closed");
  process.exit(0);
});
setTimeout(() => {
  console.error("overall timeout");
  sock.destroy();
  process.exit(3);
}, waitMs + 8000);
