// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { spawnSync } = require('node:child_process');
const supported = process.platform === 'linux' && process.getuid?.() >= 1000 && process.getgid?.() >= 1000;
test('Linux CLI prepares/verifies/checks real NSS and rejects root, wrong UID and outside-home output', {
  skip: supported ? false : 'Unprivileged Linux account with regular UID/GID required'
}, t => {
  const home = os.homedir(), root = fs.mkdtempSync(path.join(home, '.droiddeck-profile-cli-'));
  assert(root.startsWith(home + path.sep)); fs.chmodSync(root, 0o700);
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'profile-outside-'));
  t.after(() => { fs.rmSync(root, { recursive: true, force: true }); fs.rmSync(outside, { recursive: true, force: true }); });
  const config = { user: os.userInfo().username, uid: process.getuid(), gid: process.getgid(), home, lanAddress: '192.168.50.5' };
  const input = path.join(root, 'input.json'), output = path.join(root, 'profile');
  fs.writeFileSync(input, JSON.stringify(config), { mode: 0o600 });
  const script = path.join(__dirname, 'install-profile.js');
  const run = (args, preload = []) => spawnSync(process.execPath, [...preload, script, ...args], {
    encoding: 'utf8', timeout: 15000, env: { ...process.env, NODE_OPTIONS: '', NODE_PATH: '' }
  });
  for (const args of [['build', input, output], ['verify', output], ['check-host', output]]) {
    const result = run(args); assert.equal(result.status, 0, result.stderr);
    const report = JSON.parse(result.stdout); assert.equal(report.readyForActivation, false);
    assert.equal(report.accountValidated, args[0] === 'check-host');
  }
  const rejected = run(['build', input, path.join(outside, 'profile')]);
  assert.equal(rejected.status, 1); assert(!fs.existsSync(path.join(outside, 'profile')));
  // Force the refusal branch in an unprivileged child; this never acquires root.
  const rootFixture = path.join(root, 'root-guard.cjs');
  fs.writeFileSync(rootFixture, 'process.getuid=()=>0;\n', { mode: 0o600 });
  const rootOutput = path.join(root, 'root-refused');
  const rootRejected = run(['build', input, rootOutput], ['--require', rootFixture]);
  assert.equal(rootRejected.status, 1); assert(!fs.existsSync(rootOutput));
  fs.writeFileSync(input, JSON.stringify({ ...config, uid: config.uid + 1 }));
  const mismatch = path.join(root, 'mismatched-profile');
  assert.equal(run(['build', input, mismatch]).status, 0);
  const hostRejected = run(['check-host', mismatch]); assert.equal(hostRejected.status, 1);
  assert.equal(hostRejected.stderr.trim(), 'Install profile rejected; no activation performed');
  assert.equal(JSON.parse(fs.readFileSync(path.join(mismatch, 'bundle-manifest.json'))).accountValidated, false);
});

test('Linux headless profile CLI prepares private output and refuses root, linked input and outside-home output', {
  skip: supported ? false : 'Unprivileged Linux account with regular UID/GID required'
}, t => {
  const home = os.homedir(), root = fs.mkdtempSync(path.join(home, '.droiddeck-headless-profile-cli-'));
  fs.chmodSync(root, 0o700);
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'headless-profile-outside-'));
  t.after(() => { fs.rmSync(root, { recursive: true, force: true }); fs.rmSync(outside, { recursive: true, force: true }); });
  const input = path.join(root, 'input.json'), cfg = path.join(root, 'waydroid.cfg'), output = path.join(root, 'profile');
  fs.writeFileSync(input, JSON.stringify({ user: os.userInfo().username, uid: process.getuid(), gid: process.getgid(),
    home, lanAddress: '192.168.50.5' }), { mode: 0o600 });
  fs.writeFileSync(cfg, '[waydroid]\narch = x86_64\n', { mode: 0o600 });
  const run = (args, preload = []) => spawnSync(process.execPath,
    [...preload, path.join(__dirname, 'install-profile.js'), ...args], {
      encoding: 'utf8', timeout: 15000, env: { ...process.env, NODE_OPTIONS: '', NODE_PATH: '' }
    });
  for (const args of [['build-headless', input, cfg, output], ['verify', output], ['verify-headless', output, cfg], ['check-host', output]]) {
    const result = run(args); assert.equal(result.status, 0, result.stderr);
    const report = JSON.parse(result.stdout); assert.equal(report.readyForActivation, false);
    assert.equal(report.accountValidated, args[0] === 'check-host');
    assert.equal(report.waydroidProfileVerified, args[0] === 'verify-headless');
  }
  assert.equal(JSON.parse(fs.readFileSync(path.join(output, 'bundle-manifest.json'))).files.length, 19);
  const refused = path.join(outside, 'profile');
  assert.equal(run(['build-headless', input, cfg, refused]).status, 1); assert(!fs.existsSync(refused));
  const rootFixture = path.join(root, 'root-guard.cjs'), rootOutput = path.join(root, 'root-refused');
  fs.writeFileSync(rootFixture, 'process.getuid=()=>0;\n', { mode: 0o600 });
  assert.equal(run(['build-headless', input, cfg, rootOutput], ['--require', rootFixture]).status, 1);
  assert(!fs.existsSync(rootOutput));
  const linked = path.join(root, 'linked.cfg'), linkedOutput = path.join(root, 'linked-refused'); fs.symlinkSync(cfg, linked);
  assert.equal(run(['build-headless', input, linked, linkedOutput]).status, 1); assert(!fs.existsSync(linkedOutput));
  assert.equal(run(['build-headless', input, cfg, output]).status, 1);
  assert.equal(run(['verify-headless', output, cfg]).status, 0);
});
