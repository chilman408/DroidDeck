// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const { assertHeadlessSessionReady, connectSocket } = require("./headless-session-preflight");
function fixture() {
  const calls = [];
  const io = { async lstat(filename) {
    calls.push(filename);
    return { uid: 1000, mode: 0o700, isDirectory: () => filename === "/run/user/1000",
      isSocket: () => filename !== "/run/user/1000" };
  } };
  const run = async (file, args, options) => {
    calls.push([file, args, options]);
    if (file === "/usr/bin/systemctl") return Buffer.from("LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled\n");
    return Buffer.from('{"type":"b","data":[false]}');
  };
  return { io, run, calls, connect: async () => {} };
}
test("headless session validates private sockets and disabled desktop before vacant bus", async () => {
  const f = fixture(); await assertHeadlessSessionReady(f);
  assert.deepEqual(f.calls.slice(0, 3), ["/run/user/1000", "/run/user/1000/bus", "/run/user/1000/wayland-teslaandroid"]);
  const command = f.calls.find(call => Array.isArray(call) && call[0] === "/usr/bin/systemctl");
  assert.deepEqual(command[1], ["--user", "show", "tesla-waydroid-session.service", "--property=LoadState,ActiveState,UnitFileState"]);
});
test("headless session refuses active, enabled, masked and malformed desktop states", async () => {
  for (const state of ["LoadState=loaded\nActiveState=active\nUnitFileState=disabled\n",
    "LoadState=loaded\nActiveState=inactive\nUnitFileState=enabled\n",
    "LoadState=masked\nActiveState=inactive\nUnitFileState=masked\n", "unexpected"]) {
    const f = fixture(); f.run = async () => Buffer.from(state);
    await assert.rejects(assertHeadlessSessionReady(f));
  }
});
test("headless session refuses foreign or unsafe runtime sockets", async () => {
  for (const patch of [{ uid: 0 }, { mode: 0o777 }, { isDirectory: () => false }]) {
    const f = fixture(), original = f.io.lstat;
    f.io.lstat = async name => ({ ...await original(name), ...(name === "/run/user/1000" ? patch : {}) });
    await assert.rejects(assertHeadlessSessionReady(f));
  }
  for (const patch of [{ uid: 0 }, { isSocket: () => false }]) {
    const f = fixture(), original = f.io.lstat;
    f.io.lstat = async name => ({ ...await original(name), ...(name.endsWith("wayland-teslaandroid") ? patch : {}) });
    await assert.rejects(assertHeadlessSessionReady(f));
  }
});
test("compositor readiness retries only missing socket, is bounded and cancellation-aware", async () => {
  const f = fixture(), original = f.io.lstat; let attempts = 0, sleeps = 0;
  f.io.lstat = async name => {
    if (name.endsWith("wayland-teslaandroid") && attempts++ < 2) throw Object.assign(new Error("missing"), { code: "ENOENT" });
    return original(name);
  };
  f.sleep = async ms => { assert.equal(ms, 250); sleeps++; };
  await assertHeadlessSessionReady(f); assert.equal(sleeps, 2);
  f.io.lstat = async name => { if (name.endsWith("wayland-teslaandroid")) throw Object.assign(new Error("missing"), { code: "ENOENT" }); return original(name); };
  sleeps = 0; await assert.rejects(assertHeadlessSessionReady(f)); assert.equal(sleeps, 39);
  const controller = new AbortController(); controller.abort(new Error("cancelled"));
  await assert.rejects(assertHeadlessSessionReady({ ...fixture(), signal: controller.signal }), /cancelled/);
});
test("unregistered bus must remain vacant and compositor must actually accept connections", async () => {
  const f = fixture(), original = f.run;
  f.run = async (file, ...args) => file === "/usr/bin/busctl" ? Buffer.from('{"type":"b","data":[true]}') : original(file, ...args);
  await assert.rejects(assertHeadlessSessionReady(f));
  const g = fixture(); g.connect = async () => { throw new Error("not listening"); };
  await assert.rejects(assertHeadlessSessionReady(g), /not listening/);
});
test("system-managed headless units need no OS login, TCP exposure or root Waydroid", () => {
  const read = name => fs.readFileSync(path.join(__dirname, name), "utf8");
  const compositor = read("tesla-waydroid-compositor.service"), session = read("tesla-waydroid-headless-session.service");
  for (const unit of [compositor, session]) {
    assert.match(unit, /^User=hilman$/m); assert.match(unit, /^Requires=.*user@1000.service/m);
    assert.match(unit, /^Environment=XDG_RUNTIME_DIR=\/run\/user\/1000$/m);
    assert.match(unit, /^WantedBy=multi-user.target$/m);
    assert.doesNotMatch(unit, /autologin|enable-linger|User=root|--prepare|--backend=(vnc|rdp)|Restart=always/);
  }
  assert.match(compositor, /--backend=headless-backend.so --renderer=pixman --socket=wayland-teslaandroid/);
  assert.match(compositor, /--no-config --shell=kiosk-shell.so/);
  assert.match(compositor, /^PAMName=tesla-waydroid$/m);
  const pam = read("tesla-waydroid.pam");
  assert.match(pam, /^account required pam_unix.so$/m);
  assert.match(pam, /^session required pam_systemd.so class=background$/m);
  assert.doesNotMatch(pam, /pam_permit|auth|password/);
  assert.doesNotMatch(pam, /\r/, "PAM module names must not contain Windows line endings");
  assert.match(session, /^PartOf=waydroid-container.service tesla-waydroid-compositor.service$/m);
  assert.match(session, /^Environment=WAYLAND_DISPLAY=wayland-teslaandroid$/m);
  assert.match(session, /^ExecStart=\/usr\/bin\/waydroid show-full-ui$/m);
  assert.match(session, /^KillSignal=SIGUSR1$/m);
  assert.match(session, /headless-session-preflight.js/);
});
test("real private compositor probe connects, closes and rejects absent or cancelled sockets", { skip: process.platform !== "linux" }, async () => {
  const io = require("node:fs/promises"), os = require("node:os"), net = require("node:net");
  const directory = await io.mkdtemp(path.join(os.tmpdir(), "ta-headless-")), filename = path.join(directory, "wayland-test");
  const server = net.createServer(socket => socket.end());
  try {
    await new Promise((resolve, reject) => { server.once("error", reject); server.listen(filename, resolve); });
    await connectSocket(filename);
    const controller = new AbortController(); controller.abort(new Error("cancelled"));
    await assert.rejects(connectSocket(filename, controller.signal), /cancelled/);
    await assert.rejects(connectSocket(path.join(directory, "absent")));
  } finally {
    await new Promise(resolve => server.close(resolve));
    await io.rm(directory, { recursive: true, force: true });
  }
});
