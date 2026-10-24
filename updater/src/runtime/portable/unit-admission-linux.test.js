// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), { spawnSync } = require('node:child_process');
const { buildInstallProfile } = require('./install-profile');
const { readSnapshot, compareUnits } = require('./unit-admission');
const supported = process.platform === 'linux' && process.getuid?.() >= 1000 && process.getgid?.() >= 1000 &&
  fs.existsSync('/run/systemd/system') && fs.existsSync('/etc/droiddeck/runtime.json');
test('native system-bus snapshot projects five loaded services without command or environment plaintext', { skip: !supported }, async () => {
  const snapshot = await readSnapshot();
  assert.equal(Object.keys(snapshot).length, 5);
  const config = { user: os.userInfo().username, uid: process.getuid(), gid: process.getgid(), lanAddress: '192.168.50.5' };
  const mismatches = compareUnits(config, snapshot); assert(mismatches.length > 0);
  assert(!JSON.stringify(snapshot).includes('DROIDDECK_CONFIGURATION_SHA256='));
  assert(!JSON.stringify(snapshot).includes('NODE_PATH='));
  for (const record of Object.values(snapshot)) assert.match(record.ExecStartSha256, /^[a-f0-9]{64}$/);
});
test('native inspect rejects a generated different profile without deployment, and root refusal never acquires root', { skip: !supported }, t => {
  const root = fs.mkdtempSync(path.join(os.homedir(), '.droiddeck-unit-gate-')); fs.chmodSync(root, 0o700);
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const output = path.join(root, 'profile');
  buildInstallProfile({ user: os.userInfo().username, uid: process.getuid(), gid: process.getgid(), lanAddress: '192.168.50.5' }, output, 'a'.repeat(64));
  const cli = path.join(__dirname, 'unit-admission.js');
  const run = (args, preload = []) => spawnSync(process.execPath, [...preload, cli, ...args], {
    encoding: 'utf8', timeout: 60000, env: { ...process.env, NODE_OPTIONS: '', NODE_PATH: '' }
  });
  const result = run(['inspect', output]); assert.equal(result.status, 2, result.stderr);
  const receipt = JSON.parse(result.stdout); assert.equal(receipt.baseSessionUnitsMatched, false);
  assert.equal(receipt.accountValidated, true); assert.equal(receipt.readyForActivation, false);
  assert(receipt.mismatches.some(r => r.fields.includes('EnvironmentSha256')));
  assert.equal(JSON.parse(fs.readFileSync(path.join(output, 'bundle-manifest.json'))).accountValidated, false);
  const fixture = path.join(root, 'root-guard.cjs'); fs.writeFileSync(fixture, 'process.getuid=()=>0;\n');
  assert.equal(run(['inspect', output], ['--require', fixture]).status, 1);
  for (const args of [['--activate'], ['inspect', output, '--force']]) {
    const refused = run(args); assert.equal(refused.status, 1); assert.equal(refused.stdout, '');
    assert.equal(refused.stderr.trim(), 'Effective unit admission rejected; no activation performed');
  }
});
