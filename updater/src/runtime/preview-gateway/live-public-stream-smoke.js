// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const { WebSocket } = require("ws");

async function main() {
  const origin = "https://teslavm.mondriandsm.synology.me";
  const access = await fetch(origin + "/api/browserAccess", { signal: AbortSignal.timeout(10000) });
  assert.equal(access.status, 200, "Anonymous browser settings unavailable");
  assert.equal((await access.json()).enabled, false, "This probe requires user-selected login-off");
  const response = await fetch(origin + "/api/displaySession", {
    method: "POST", headers: { Origin: origin, "Content-Type": "application/json" },
    body: "{}", signal: AbortSignal.timeout(10000),
  });
  assert.equal(response.status, 200, "Anonymous display session unavailable");
  const session = await response.json();
  assert.equal(typeof session.token, "string");
  const url = new URL("/sockets/display", origin.replace("https:", "wss:"));
  url.searchParams.set("session", session.token);
  url.searchParams.set("format", "h264");
  const stream = new WebSocket(url, { origin, handshakeTimeout: 10000 });
  await new Promise((resolve, reject) => {
    let dimensions, configured = false, settled = false;
    const timer = setTimeout(() => finish(new Error("Public H.264 keyframe timed out")), 15000);
    function finish(error) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      stream.terminate();
      if (error) reject(error); else resolve();
    }
    stream.on("error", finish);
    stream.on("close", () => finish(new Error("Public stream closed before keyframe")));
    stream.on("message", (packet, binary) => {
      try {
        if (!binary) {
          const metadata = JSON.parse(packet.toString());
          if (metadata.type === "scrcpy-session") dimensions = [metadata.width, metadata.height];
        } else if (packet.length > 12) {
          if (packet[0] & 0x40) configured = true;
          else if ((packet[0] & 0x20) && configured && dimensions) {
            console.log(JSON.stringify({ publicHttps: 200, loginEnabled: false,
              publicWebSocket: 101, h264Keyframe: true, dimensions }));
            finish();
          }
        }
      } catch (error) { finish(error); }
    });
  });
}

if (require.main === module) main().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
