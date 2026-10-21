// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
test("desktop session service recovers Waydroid without changing OS login or container data", () => {
  const filename = path.join(__dirname, "tesla-waydroid-session.service");
  assert.ok(fs.existsSync(filename), "persistent user session unit missing");
  const unit = fs.readFileSync(filename, "utf8");
  for (const value of ["PartOf=graphical-session.target", "WantedBy=graphical-session.target", "Restart=always",
    "RestartSec=5s", "ExecStartPre=/usr/bin/test -S %t/wayland-0", "ExecStart=/usr/bin/waydroid show-full-ui",
    "ExecStartPre=/usr/bin/node /opt/teslaandroid-waydroid/runtime/waydroid-display/session-preflight.js",
    "Environment=LD_LIBRARY_PATH=/opt/teslaandroid-waydroid/libgbinder", "Environment=WAYLAND_DISPLAY=wayland-0",
    "Conflicts=waydroid-session-software-final.service", "KillMode=control-group"]) assert.ok(unit.includes(value), value);
  assert.equal(/autologin|User=root|--prepare|container start|container stop|rm -rf|loginctl enable-linger/i.test(unit), false);
});

test("session preflight refuses an existing or malformed D-Bus owner without starting Android", async () => {
  const { assertSessionVacant } = require("./session-preflight");
  const run = value => async (file, args) => {
    assert.equal(file, "/usr/bin/busctl");
    assert.deepEqual(args.slice(-4), ["org.freedesktop.DBus", "NameHasOwner", "s", "id.waydro.Session"]);
    return Buffer.from(value);
  };
  await assertSessionVacant({ run: run('{"type":"b","data":[false]}') });
  for (const value of ['{"type":"b","data":[true]}', '{"type":"s","data":[false]}',
    '{"type":"b","data":[]}', "not-json"]) await assert.rejects(assertSessionVacant({ run: run(value) }));
});
