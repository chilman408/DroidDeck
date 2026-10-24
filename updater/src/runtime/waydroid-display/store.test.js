// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createStore } = require("./store");
const state = { width: 960, height: 640, density: 180, resolutionPreset: 0, isResponsive: 1 };
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "tesla-display-store-"));
  fs.chmodSync(dir, 0o700);
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return { dir, filename: path.join(dir, "display.json") };
}
test("store defaults only on absence and atomically round trips validated private state", async t => {
  const { filename } = fixture(t);
  const store = createStore(filename);
  assert.equal(await store.load(), null);
  await store.save(state);
  assert.deepEqual(await store.load(), state);
  await assert.rejects(store.save({ ...state, renderer: 1 }));
  assert.deepEqual(await store.load(), state);
  if (process.platform !== "win32") assert.equal(fs.statSync(filename).mode & 0o777, 0o600);
});
test("corrupt saved preferences are not overwritten as fresh defaults", async t => {
  const { filename } = fixture(t);
  fs.writeFileSync(filename, "not json", { mode: 0o600 });
  await assert.rejects(createStore(filename).load());
  assert.equal(fs.readFileSync(filename, "utf8"), "not json");
});
test("symlink or public state directory is rejected", { skip: process.platform === "win32" }, async t => {
  const { dir, filename } = fixture(t);
  const target = path.join(dir, "target");
  fs.writeFileSync(target, JSON.stringify(state), { mode: 0o600 });
  fs.symlinkSync(target, filename);
  await assert.rejects(createStore(filename).load());
  fs.unlinkSync(filename);
  fs.chmodSync(dir, 0o777);
  await assert.rejects(createStore(filename).save(state));
});
