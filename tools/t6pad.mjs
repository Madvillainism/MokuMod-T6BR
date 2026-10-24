// Persistent PPSSPP debugger pad/session client (raw RFC6455, no deps).
//
// Reads commands from stdin, one per line:
//   press <button> [frames]     hold a button for N vblanks (default 2)
//   send  <json>                raw request, e.g. {"event":"game.status"}
//   status                      game.status
//   quit
//
// Prints one line per event so a parent process can synchronise:
//   READY                       handshake complete
//   TX <json>                   request written
//   RX <json>                   server message
//
// Usage: node tools/t6pad.mjs [--port 8099]
import net from "node:net";
import crypto from "node:crypto";
import readline from "node:readline";

const args = process.argv.slice(2);
const port = Number(args[args.indexOf("--port") + 1]) || 8099;
const host = "127.0.0.1";

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
  const text = JSON.stringify(msg);
  console.log("TX " + text);
  sendFrame(text);
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
      console.log("RX " + payload.toString("utf8"));
    } else if (opcode === 0x8) {
      console.log("RX <close>");
      process.exit(0);
    } else if (opcode === 0x9) {
      sock.write(Buffer.from([0x8a, 0x80, 0, 0, 0, 0, ...payload]));
    }
  }
}

sock.on("connect", () => {
  sock.write(
    [
      "GET /debugger HTTP/1.1",
      `Host: ${host}:${port}`,
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
      console.log("HANDSHAKE_FAILED");
      process.exit(2);
    }
    handshaken = true;
    frameBuf = headerBuf.subarray(end + 4);
    console.log("READY");
    handleFrames();
    return;
  }
  frameBuf = Buffer.concat([frameBuf, chunk]);
  handleFrames();
});

sock.on("error", (e) => {
  console.log("ERROR " + e.message);
  process.exit(1);
});

sock.on("close", () => {
  console.log("CLOSED");
  process.exit(0);
});

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const t = line.trim();
  if (!t) return;
  const [cmd, ...rest] = t.split(/\s+/);
  if (cmd === "quit") process.exit(0);
  if (cmd === "press" && rest[0]) {
    sendJson({
      event: "input.buttons.press",
      button: rest[0],
      duration: Number(rest[1] || 2),
    });
    return;
  }
  if (cmd === "status") {
    sendJson({ event: "game.status" });
    return;
  }
  if (cmd === "raw") {
    sendJson(JSON.parse(rest.join(" ")));
    return;
  }
  console.log("ERR unknown command " + cmd);
});
