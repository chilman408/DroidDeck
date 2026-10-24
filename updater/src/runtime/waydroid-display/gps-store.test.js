// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { createGpsStore } = require("./gps-store");
async function fixture(t, kind = "preferences") {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "ta-gps-"));
  if (process.platform !== "win32") await fs.chmod(directory, 0o700);
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const file = path.join(directory, "state.json");
  return { directory, file, store: createGpsStore(file, kind) };
}
test("GPS stores only enabled preference or lease journal and atomically clears only a journal", async t => {
  const f = await fixture(t); assert.equal(await f.store.load(), null);
  await f.store.save({ version: 1, enabled: false });
  assert.deepEqual(await f.store.load(), { version: 1, enabled: false });
  await assert.rejects(f.store.save({ version: 1, enabled: true, latitude: 1 }));
  await assert.rejects(f.store.save(null));
  assert.deepEqual(await f.store.load(), { version: 1, enabled: false });
  const journal = await fixture(t, "journal");
  await journal.store.save({ version: 1, lease: "a".repeat(32) }); await journal.store.save(null);
  assert.equal(await journal.store.load(), null);
  await assert.rejects(journal.store.save({ version: 1, lease: ["a".repeat(32)] }));
});
test("GPS store refuses corrupt existing state instead of overwriting it", async t => {
  const f = await fixture(t); await fs.writeFile(f.file, "bad", { mode: 0o600 });
  await assert.rejects(f.store.save({ version: 1, enabled: true }));
  assert.equal(await fs.readFile(f.file, "utf8"), "bad");
});
test("GPS store rejects links and nonprivate files/directories on Linux", { skip: process.platform === "win32" }, async t => {
  const f = await fixture(t); const target = path.join(f.directory, "target");
  await fs.writeFile(target, JSON.stringify({ version: 1, enabled: true }), { mode: 0o600 });
  await fs.symlink(target, f.file); await assert.rejects(f.store.load()); await fs.unlink(f.file);
  await fs.copyFile(target, f.file); await fs.chmod(f.file, 0o644); await assert.rejects(f.store.load());
  await fs.chmod(f.file, 0o600); await fs.chmod(f.directory, 0o755); await assert.rejects(f.store.load());
});
