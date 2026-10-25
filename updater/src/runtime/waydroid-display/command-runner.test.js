// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { setTimeout: delay } = require("node:timers/promises");
const { execute } = require("./host-adapter");

test("explicit command deadline terminates a slow real child instead of ignoring the caller's budget", async () => {
  await assert.rejects(execute(process.execPath, ["-e", "setTimeout(()=>process.stdout.write('late'),250)"],
    { env: process.env, timeoutMs: 25 }), /Command timed out/);
});

test("invalid or unbounded command deadlines cannot launch a child", async () => {
  for (const timeoutMs of [0, -1, 1.5, NaN, Infinity, 65001, "1000"]) {
    await assert.rejects(execute(process.execPath, ["-e", "process.exit(99)"],
      { env: process.env, timeoutMs }), /Invalid command timeout/);
  }
});

test("bounded command input travels through stdin, not argv or shell interpolation", async () => {
  const input = JSON.stringify({ latitude: 0, longitude: -180, accuracy: 5 });
  const output = await execute(process.execPath, ["-e",
    "let body='';process.stdin.on('data',c=>body+=c);process.stdin.on('end',()=>process.stdout.write(body))"],
    { env: process.env, input });
  assert.equal(output.toString(), input);
});

test("invalid or oversized stdin is rejected before a command is launched", async () => {
  for (const input of [Buffer.alloc(4097), "x".repeat(4097), "\u00e9".repeat(2049), {}, 1]) {
    await assert.rejects(execute(process.execPath, ["-e", "process.exit(99)"], { input }), /Invalid command input/);
  }
});

test("aborting a launcher stops its descendant writer before recovery can begin", { skip: process.platform === "win32" }, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ta-command-cancel-"));
  const filename = path.join(root, "late-mutation");
  const child = `setTimeout(()=>require('fs').writeFileSync(${JSON.stringify(filename)},'late'),500)`;
  const parent = `require('child_process').spawn(process.execPath,['-e',${JSON.stringify(child)}],{stdio:'inherit'});setInterval(()=>{},1000)`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error("test cancelled")), 200);
  try {
    await assert.rejects(execute(process.execPath, ["-e", parent], { shell: false, signal: controller.signal }));
    await delay(700);
    assert.equal(fs.existsSync(filename), false);
  } finally { clearTimeout(timer); fs.rmSync(root, { recursive: true, force: true }); }
});
