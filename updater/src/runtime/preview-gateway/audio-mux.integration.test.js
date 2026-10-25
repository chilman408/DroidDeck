// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const { encoderArgs } = require("./audio-relay");
const { Mp4Fragments } = require("./audio-stream");
test("real-time silence produces browser initialization and media within two seconds", {
  skip: !fs.existsSync("/usr/bin/ffmpeg"), timeout: 6000,
}, async () => {
  const child = spawn("/usr/bin/ffmpeg", encoderArgs(), { stdio: ["pipe", "pipe", "pipe"] });
  const closed = once(child, "close"), started = Date.now();
  let init, fragment, errorText = "";
  const parser = new Mp4Fragments((type) => { if (type === "init") init = Date.now() - started; else fragment ??= Date.now() - started; });
  child.stderr.on("data", bytes => { errorText = (errorText + bytes).slice(-4096); });
  child.stdin.on("error", () => {});
  child.stdout.on("data", bytes => parser.push(bytes));
  const writer = setInterval(() => child.stdin.write(Buffer.alloc(3840)), 20);
  try {
    await new Promise(resolve => setTimeout(resolve, 2000));
    assert.ok(init !== undefined && fragment !== undefined, `Startup exceeded budget: ${JSON.stringify({ init, fragment, errorText })}`);
    console.log(JSON.stringify({ silentMuxInitMs: init, silentMuxFragmentMs: fragment }));
  } finally {
    clearInterval(writer); child.stdin.destroy(); child.kill("SIGKILL"); await closed;
  }
});
