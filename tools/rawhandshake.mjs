// Raw RFC6455 handshake probe (no WebSocket library) to see PPSSPP's exact HTTP response.
import net from "node:net";
import crypto from "node:crypto";

const host = process.argv[2] || "127.0.0.1";
const port = Number(process.argv[3] || 8099);
const key = crypto.randomBytes(16).toString("base64");

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

const sock = net.connect({ host, port }, () => {
  console.log("TCP connected; sending handshake");
  sock.write(req);
});

let buf = Buffer.alloc(0);
sock.on("data", (d) => {
  buf = Buffer.concat([buf, d]);
  console.log("RAW:", JSON.stringify(buf.toString("latin1")));
  if (buf.includes("\r\n\r\n")) {
    console.log("--- headers ---");
    console.log(buf.toString("latin1").split("\r\n\r\n")[0]);
    sock.end();
  }
});
sock.on("error", (e) => console.error("ERR", e.message));
sock.on("close", () => { console.log("closed"); process.exit(0); });
setTimeout(() => { console.log("timeout"); sock.destroy(); }, 3000);
