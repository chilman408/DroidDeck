// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), path = require('node:path'), { createHash } = require('node:crypto');
const { readRuntimeConfig } = require('./runtime-config');
const { configurationDigest } = require('./configuration-generation');
const { readBoundedRegular } = require('./read-bounded');
const sha = b => createHash('sha256').update(b).digest('hex');
function createPidPlan(configuration) {
  const generation = configurationDigest(configuration);
  const unit = '[Service]\nEnvironment=DROIDDECK_CONFIGURATION_SHA256=' + generation + '\n' +
    'ExecStartPre=/usr/bin/node /opt/droiddeck/pid-compat/portable/pid-startup.js\n';
  const files = {
    'portable/pid-startup.js': readBoundedRegular(path.join(__dirname, 'pid-startup.js'), 16384).toString().replace(/\r\n/g, '\n'),
    'systemd/40-droiddeck-pid-compat.conf': unit,
    'waydroid-display/pid-compat.js': readBoundedRegular(path.join(__dirname, '../waydroid-display/pid-compat.js'), 16384).toString().replace(/\r\n/g, '\n'),
  };
  return { configurationDigest: generation, files };
}
function createPidBundlePlan(configuration) {
  const { configurationDigest: generation, files } = createPidPlan(configuration);
  const records = Object.entries(files).sort().map(([name, text]) => {
    const bytes = Buffer.from(text);
    return { path: name, size: bytes.length, sha256: sha(bytes) };
  });
  const manifest = { version: 1, product: 'DroidDeck', kind: 'pid-startup-component', installable: false,
    configurationDigest: generation, files: records };
  return { files, manifest };
}
function buildPidBundle(configuration, output) {
  const { files, manifest } = createPidBundlePlan(configuration);
  fs.mkdirSync(output, { mode: 0o700 });
  for (const [name, text] of Object.entries(files).sort()) {
    const p = path.join(output, name);
    fs.mkdirSync(path.dirname(p), { recursive: true, mode: 0o700 });
    fs.writeFileSync(p, Buffer.from(text), { mode: 0o600, flag: 'wx' });
  }
  fs.writeFileSync(path.join(output, 'bundle-manifest.json'), JSON.stringify(manifest, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
  return manifest;
}
if (require.main === module) {
  try {
    if (process.argv.length !== 5 || process.argv[2] !== 'build') throw new Error('Use build CONFIG NEW_OUTPUT');
    const m = buildPidBundle(readRuntimeConfig(process.argv[3]), process.argv[4]);
    console.log(JSON.stringify({ kind: m.kind, files: m.files.length, configurationDigest: m.configurationDigest, installable: false }));
  } catch (e) { console.error(e.message); process.exitCode = 1; }
}
module.exports = { createPidPlan, createPidBundlePlan, buildPidBundle };
