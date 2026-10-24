// Minimal PPSSPP WebSocket debugger probe (Node 22+)
// Usage: node tools/wsprobe.mjs [ws://127.0.0.1:8099/debugger]

const url = process.argv[2] || "ws://127.0.0.1:8099/debugger";
const ws = new WebSocket(url, "debugger.ppsspp.org");

let ticket = 1;
const pending = new Map();

function send(event, extra = {}) {
  const msg = { event, ticket: ticket++, ...extra };
  pending.set(msg.ticket, msg);
  console.log("->", JSON.stringify(msg));
  ws.send(JSON.stringify(msg));
}

ws.addEventListener("open", () => {
  console.log("open", url);
  send("version", { name: "framemod-probe", version: "0.1.0" });
  setTimeout(() => send("game.status"), 300);
  setTimeout(() => send("memory.read_u32", { address: 0x08800000 }), 600);
  setTimeout(() => send("memory.read_u8", { address: 0x08800000 }), 900);
  setTimeout(() => send("memory.read", { address: 0x08800000, size: 64 }), 1200);
  setTimeout(() => send("cpu.status"), 1500);
});

ws.addEventListener("message", (ev) => {
  const text = typeof ev.data === "string" ? ev.data : "(binary)";
  console.log("<-", text);
});

ws.addEventListener("error", (e) => console.error("error", e.message || e));
ws.addEventListener("close", (e) => {
  console.log("close", e.code, e.reason);
  process.exit(0);
});

setTimeout(() => {
  console.log("timeout, closing");
  ws.close();
}, 4000);
