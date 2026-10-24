// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs/promises'), os = require('node:os'), path = require('node:path');
const { buildPidBundle } = require('./pid-bundle');
const config = { version: 1, user: 'hilman', uid: 1000, gid: 1000, home: '/home/hilman', lanAddress: '192.168.2.215', port: 8443, hostname: 'device.teslaandroid.com' };
test('PID component is a closed three-file plan with no automatic session startup', async t => {
  const base = await fs.mkdtemp(path.join(os.tmpdir(), 'dd-pid-bundle-')); t.after(() => fs.rm(base, { recursive: true, force: true }));
  const target = path.join(base, 'new'); const m = buildPidBundle(config, target);
  assert.equal(m.installable, false);
  assert.deepEqual(m.files.map(r => r.path), ['portable/pid-startup.js', 'systemd/40-droiddeck-pid-compat.conf', 'waydroid-display/pid-compat.js']);
  const text = await fs.readFile(path.join(target, m.files[1].path), 'utf8');
  assert.match(text, /ExecStartPre=\/usr\/bin\/node \/opt\/droiddeck\/pid-compat\/portable\/pid-startup.js/);
  assert.doesNotMatch(text, /Wants=|Requires=|ExecStart=|sysctl.kernel.pid_max/);
  assert.throws(() => buildPidBundle(config, target), /exist/i);
  assert.throws(() => buildPidBundle({ ...config, password: 'generated-fixture' }, path.join(base, 'invalid')), /schema/);
});
