// SPDX-License-Identifier: GPL-3.0-only
// Bind inside the disposable Android namespace; access only through an SSH tunnel.
const { createGateway } = require("./server");
const { createDisplayControl } = require("./display-control");
const { createHelperClient } = require("./display-helper-client");
async function main() {
  const control = createDisplayControl({ helper: createHelperClient("/run/teslaandroid-waydroid/display.sock") });
  await control.initialize();
  const video = process.env.GATEWAY_H264_STREAM === "1";
  const audio = process.env.GATEWAY_AUDIO_STREAM === "1";
  const audioSettings = audio ? require("./audio-settings").createAudioSettings("/home/hilman/.config/teslaandroid-waydroid/browser-audio-test.json") : undefined;
  const server = createGateway({ frontendDir: "/home/hilman/teslaandroid-waydroid/" + (audio ? "frontend-audio-test" : video ? "frontend-h264-test" : "frontend"), displayControl: control, audioSettings,
    targets: { api: "http://127.0.0.1:8081", display: "ws://127.0.0.1:9091", touchscreen: "ws://127.0.0.1:9999",
      ...(video ? { h264: "ws://127.0.0.1:9092" } : {}), ...(audio ? { audio: "ws://127.0.0.1:9093" } : {}) } });
  server.listen(18444, "192.168.240.112");
  process.once("SIGTERM", () => { control.close(); server.close(() => process.exit(0)); server.closeAllConnections(); });
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
