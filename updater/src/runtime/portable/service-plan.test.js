// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { createServicePlan } = require('./service-plan');
const { configurationDigest } = require('./configuration-generation');
const configuration = { user: 'deck', uid: 1200, gid: 1201, home: '/home/stream', lanAddress: '10.20.30.40' };

test('portable startup plan is immutable metadata and fixed files, not an installer or an update', () => {
  const plan = createServicePlan(configuration);
  assert.equal(plan.installable, false); assert.equal(plan.kind, 'service-plan');
  assert.equal(Object.isFrozen(plan), true); assert.equal(Object.isFrozen(plan.files), true);
  assert.deepEqual(Object.keys(plan.files).sort(), ['pam.d/droiddeck', 'systemd/system/droiddeck-browser-connection.service',
    'systemd/system/tesla-waydroid-compositor.service', 'systemd/system/tesla-waydroid-headless-session.service',
    'systemd/system/tesla-waydroid-runtime.service', 'systemd/system/waydroid-container.service.d/50-droiddeck-session.conf', 'tmpfiles.d/droiddeck.conf']);
  assert.equal(plan.configurationDigest, configurationDigest(configuration));
  assert.doesNotMatch(JSON.stringify(plan), /hilman|192\.168\.2\.90|\/opt\/teslaandroid-waydroid|\/run\/user\/1000|\/home\/hilman/);
  for (const [name, content] of Object.entries(plan.files)) {
    assert.doesNotMatch(name, /\.\.|^\//); assert.doesNotMatch(content, /\r|password|TOKEN|autologin|enable-linger|sh -c/i);
  }
});

test('unprivileged compositor and session pin metadata and preserve private PipeWire and vacancy checks', () => {
  const { files, configurationDigest: digest } = createServicePlan(configuration);
  for (const name of ['tesla-waydroid-compositor.service', 'tesla-waydroid-headless-session.service']) {
    const unit = files['systemd/system/' + name];
    for (const value of ['User=deck', 'Group=1201', 'Environment=HOME=/home/stream', 'Environment=XDG_RUNTIME_DIR=/run/user/1200',
      'Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1200/bus', 'Environment=WAYLAND_DISPLAY=wayland-teslaandroid',
      'Environment=DROIDDECK_CONFIGURATION_SHA256=' + digest, 'KillMode=control-group', 'WantedBy=multi-user.target']) assert.ok(unit.includes(value), value);
    assert.match(unit, /^Requires=.*user@1200.service/m);
    assert.doesNotMatch(unit, /User=root|--backend=(vnc|rdp)|Restart=always/);
  }
  const compositor = files['systemd/system/tesla-waydroid-compositor.service'];
  assert.match(compositor, /^PAMName=droiddeck$/m);
  assert.match(compositor, /^ExecStartPre=\/usr\/bin\/node \/opt\/droiddeck\/runtime\/portable\/worker-entry.js compositor-check$/m);
  assert.match(compositor, /^ExecStart=\/usr\/bin\/weston --backend=pipewire --renderer=gl --socket=wayland-teslaandroid --width=1280 --height=720 --idle-time=0 --no-config --shell=kiosk-shell.so$/m);
  const session = files['systemd/system/tesla-waydroid-headless-session.service'];
  assert.match(session, /^ExecStartPre=\/usr\/bin\/node \/opt\/droiddeck\/runtime\/portable\/worker-entry.js headless-check$/m);
  assert.match(session, /^ExecStart=\/usr\/bin\/waydroid show-full-ui$/m);
  assert.match(session, /^KillSignal=SIGUSR1$/m);
  assert.match(session, /^PartOf=waydroid-container.service tesla-waydroid-compositor.service$/m);
  assert.equal(files['systemd/system/waydroid-container.service.d/50-droiddeck-session.conf'], '[Unit]\nWants=tesla-waydroid-headless-session.service\n');
  assert.equal(files['pam.d/droiddeck'], '#%PAM-1.0\naccount required pam_unix.so\nsession required pam_systemd.so class=background\n');
});

test('runtime and connection use closed entry and shared sockets never belong to one service lifecycle', () => {
  const { files } = createServicePlan(configuration);
  const runtime = files['systemd/system/tesla-waydroid-runtime.service'];
  assert.match(runtime, /^User=root$/m); assert.match(runtime, /^Environment=TESLA_WAYDROID_CAPTURE_BACKEND=compositor$/m);
  assert.match(runtime, /^ExecStart=\/usr\/bin\/node \/opt\/droiddeck\/runtime\/portable\/worker-entry.js runtime$/m);
  assert.match(runtime, /^After=.*tesla-waydroid-headless-session.service.*droiddeck-browser-connection.service/m);
  assert.match(runtime, /^TimeoutStopSec=900s$/m);
  const connection = files['systemd/system/droiddeck-browser-connection.service'];
  assert.match(connection, /^User=root$/m); assert.match(connection, /^Group=1201$/m);
  assert.match(connection, /^ExecStart=\/usr\/bin\/node \/opt\/droiddeck\/runtime\/portable\/worker-entry.js connection$/m);
  assert.match(connection, /^ProtectSystem=strict$/m); assert.match(connection, /^RestrictAddressFamilies=AF_UNIX$/m);
  assert.match(connection, /^ReadWritePaths=\/var\/lib\/droiddeck\/browser-connection \/run\/droiddeck$/m);
  for (const value of Object.values(files)) assert.doesNotMatch(value, /^RuntimeDirectory=/m);
  assert.equal(files['tmpfiles.d/droiddeck.conf'], 'd /var/lib/droiddeck 0700 root root -\nd /var/lib/droiddeck/browser-connection 0700 root root -\nd /run/droiddeck 0750 root 1201 -\n');
});

test('only unprivileged GPU compositor/session receive fixed supplemental device groups', () => {
  const { files } = createServicePlan(configuration);
  for (const [name, content] of Object.entries(files)) {
    const gpuService = name === 'systemd/system/tesla-waydroid-compositor.service' ||
      name === 'systemd/system/tesla-waydroid-headless-session.service';
    const directives = content.split('\n').filter(line => line.startsWith('SupplementaryGroups='));
    assert.deepEqual(directives, gpuService ? ['SupplementaryGroups=render video'] : [], name);
    assert.doesNotMatch(content, /usermod|chmod|Group=render|Group=video|SupplementaryGroups=root/);
  }
});

test('service plan rejects unvalidated or executable/path-injection configuration', () => {
  for (const change of [{ user: 'root' }, { user: 'deck\nExecStart=/bin/false' }, { home: '/home/%u' },
    { hostname: 'deck.example\nExecStart=x' }, { uid: 0 }, { gid: '1201' }, { renderer: 'custom' }, { command: '/bin/sh' }]) {
    assert.throws(() => createServicePlan({ ...configuration, ...change }));
  }
});

test('Ubuntu systemd verifies generated units without installing, enabling or starting them', { skip: process.platform !== 'linux' }, async () => {
  const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os');
  const { spawnSync } = require('node:child_process');
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'droiddeck-service-verify-'));
  try {
    const { files } = createServicePlan({ user: 'hilman', uid: 1000, gid: 1000, lanAddress: '192.168.2.90' });
    const units = [];
    for (const [name, content] of Object.entries(files)) {
      if (!name.startsWith('systemd/system/')) continue;
      const filename = path.join(directory, name.slice('systemd/system/'.length));
      await fs.mkdir(path.dirname(filename), { recursive: true }); await fs.writeFile(filename, content);
      if (name.endsWith('.service')) units.push(filename);
    }
    const container = path.join(directory, 'waydroid-container.service');
    await fs.copyFile('/usr/lib/systemd/system/waydroid-container.service', container); units.push(container);
    const result = spawnSync('/usr/bin/systemd-analyze', ['verify', '--man=no', ...units], {
      encoding: 'utf8', timeout: 10000, maxBuffer: 65536,
      env: { ...process.env, SYSTEMD_UNIT_PATH: directory + ':/usr/lib/systemd/system:/lib/systemd/system', SYSTEMD_COLORS: '0' },
    });
    assert.equal(result.error, undefined); assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stderr.trim(), '', 'Generated unit verification should have no warnings');
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});
