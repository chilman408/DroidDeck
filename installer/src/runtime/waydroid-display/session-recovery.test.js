// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const { recoverDesktopSession } = require("./session-recovery");
const template = fs.readFileSync(path.join(__dirname, "tesla-waydroid-session.service"));
const unit = "/home/hilman/.config/systemd/user/tesla-waydroid-session.service";
const loaded = "LoadState=loaded\nActiveState=active\nFragmentPath=" + unit + "\nDropInPaths=\nTransient=no\n";
function fixture({ state = loaded, bus = true, changed = false, unsafe = false } = {}) {
  const calls = [];
  const run = async (file, args, options) => {
    calls.push([file, args, options]);
    assert.equal(options.shell, false);
    if (file === "/usr/bin/busctl") return Buffer.from(JSON.stringify({ type: "b", data: [bus] }));
    assert.equal(file, "/usr/sbin/runuser");
    assert.deepEqual(args.slice(0, 7), ["-u", "hilman", "--", "/usr/bin/env", "XDG_RUNTIME_DIR=/run/user/1000",
      "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus", "/usr/bin/systemctl"]);
    return Buffer.from(args.includes("show") ? state : "");
  };
  const io = {
    lstat: async filename => ({ uid: filename.startsWith("/run/user/1000") || filename === unit ? 1000 : 0,
      mode: unsafe ? 0o666 : 0o700, nlink: 1, size: template.length,
      isDirectory: () => filename === "/run/user/1000",
      isSocket: () => /\/(?:bus|wayland-0)$/.test(filename), isFile: () => filename.endsWith(".service") }),
    readFile: async filename => filename === unit && changed ? Buffer.from("foreign unit") : template,
  };
  return { run, io, calls, sleep: async () => {} };
}
test("container recovery queues only the existing owned desktop session restart", async () => {
  const f = fixture();
  assert.equal(await recoverDesktopSession(f), "queued");
  const final = f.calls.at(-1);
  assert.deepEqual(final[1].slice(7), ["--user", "--no-block", "try-restart", "tesla-waydroid-session.service"]);
  assert.equal(f.calls.filter(([, args]) => args.includes("try-restart")).length, 1);
});
test("inactive, missing or absent desktop sessions are never started", async () => {
  for (const state of [loaded.replace("ActiveState=active", "ActiveState=inactive"), "LoadState=not-found\n"]) {
    const f = fixture({ state });
    assert.equal(await recoverDesktopSession(f), "skipped");
    assert.equal(f.calls.some(([, args]) => args.includes("try-restart")), false);
  }
  const f = fixture(); f.io.lstat = async () => { throw Object.assign(new Error("missing"), { code: "ENOENT" }); };
  assert.equal(await recoverDesktopSession(f), "skipped");
  assert.equal(f.calls.length, 0);
});
test("foreign units, drop-ins and changed unit contents refuse recovery", async () => {
  for (const options of [{ state: loaded.replace(unit, "/tmp/foreign.service") },
    { state: loaded.replace("DropInPaths=", "DropInPaths=/tmp/change.conf") },
    { state: loaded.replace("Transient=no", "Transient=yes") }, { changed: true }, { unsafe: true }]) {
    const f = fixture(options);
    await assert.rejects(recoverDesktopSession(f));
    assert.equal(f.calls.some(([, args]) => args.includes("try-restart")), false);
  }
});
test("recovery waits boundedly for container D-Bus readiness and propagates cancellation", async () => {
  const f = fixture({ bus: false });
  await assert.rejects(recoverDesktopSession(f), /Container bus/);
  assert.equal(f.calls.filter(([file]) => file === "/usr/bin/busctl").length, 10);
  assert.equal(f.calls.some(([, args]) => args.includes("try-restart")), false);
  const controller = new AbortController(); controller.abort(new Error("cancelled"));
  const cancelled = fixture();
  await assert.rejects(recoverDesktopSession({ ...cancelled, signal: controller.signal }), /cancelled/);
  assert.equal(cancelled.calls.length, 0);
});
test("optional container hook cannot stop Android or change login settings", () => {
  const dropin = fs.readFileSync(path.join(__dirname, "session-recovery.conf"), "utf8");
  assert.match(dropin, /^ExecStartPost=-\/usr\/bin\/node \/opt\/teslaandroid-waydroid\/runtime\/waydroid-display\/session-recovery.js$/m);
  assert.equal(/autologin|enable-linger|ExecStop|container stop|rm /i.test(dropin), false);
});
test("malformed D-Bus and changed session state never queue restart", async () => {
  for (const value of ['{"type":"s","data":[true]}', '{"type":"b","data":[]}',
    '{"type":"b","data":[1]}', "not-json"]) {
    const f = fixture(), original = f.run;
    f.run = async (file, args, options) => file === "/usr/bin/busctl" ? Buffer.from(value) : original(file, args, options);
    await assert.rejects(recoverDesktopSession(f));
    assert.equal(f.calls.some(([, args]) => args.includes("try-restart")), false);
  }
  const f = fixture(), original = f.run;
  let inspected = 0;
  f.run = async (file, args, options) => {
    const result = await original(file, args, options);
    if (args.includes("show") && ++inspected === 2) return Buffer.from(loaded.replace("Transient=no", "Transient=yes"));
    return result;
  };
  await assert.rejects(recoverDesktopSession(f), /changed/);
  assert.equal(f.calls.some(([, args]) => args.includes("try-restart")), false);
});
test("unit properties reject duplicates and oversized input", () => {
  const { parseState } = require("./session-recovery");
  for (const value of ["LoadState=loaded\nLoadState=loaded", "broken", "x".repeat(8193)]) {
    assert.throws(() => parseState(value));
  }
});
test("unit ownership comparison permits only line-ending normalization", async () => {
  const f = fixture();
  f.io.readFile = async filename => filename === unit ? Buffer.from(template.toString().replace(/\r?\n/g, "\n")) :
    Buffer.from(template.toString().replace(/\r?\n/g, "\r\n"));
  assert.equal(await recoverDesktopSession(f), "queued");
  for (const invalid of [Buffer.from([0xff]), Buffer.concat([template, Buffer.from("\nEnvironment=FOREIGN=1\n")])]) {
    const changed = fixture();
    changed.io.readFile = async filename => filename === unit ? invalid : template;
    await assert.rejects(recoverDesktopSession(changed));
    assert.equal(changed.calls.some(([, args]) => args.includes("try-restart")), false);
  }
});
test("an owned session already restarting is left to systemd, never restarted twice", async () => {
  for (const phase of ["activating", "deactivating", "reloading"]) {
    const f = fixture({ state: loaded.replace("ActiveState=active", "ActiveState=" + phase) });
    assert.equal(await recoverDesktopSession(f), "already-restarting");
    assert.equal(f.calls.some(([, args]) => args.includes("try-restart")), false);
    const foreign = fixture({ state: loaded.replace("ActiveState=active", "ActiveState=" + phase).replace(unit, "/tmp/foreign.service") });
    await assert.rejects(recoverDesktopSession(foreign));
  }
});
