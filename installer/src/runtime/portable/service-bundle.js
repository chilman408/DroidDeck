// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), path = require('node:path'), { createHash } = require('node:crypto');
const { createServicePlan } = require('./service-plan');
const { readRuntimeConfig } = require('./runtime-config');
const { readBoundedRegular } = require('./read-bounded');
const PLAN_SHA256 = '0bc59d1db855eed1112e3ea12ae034899dcd4117a00d1f0824187fb039c9e621';
const FILES = Object.freeze(['pam.d/droiddeck', 'systemd/system/droiddeck-browser-connection.service',
  'systemd/system/tesla-waydroid-compositor.service', 'systemd/system/tesla-waydroid-headless-session.service',
  'systemd/system/tesla-waydroid-runtime.service', 'systemd/system/waydroid-container.service.d/50-droiddeck-session.conf',
  'tmpfiles.d/droiddeck.conf']);
const sha = b => createHash('sha256').update(b).digest('hex');
function verifyServiceBundle(directory) {
  if (!fs.lstatSync(directory).isDirectory()) throw new Error('Unsafe service bundle root');
  const m = JSON.parse(readBoundedRegular(path.join(directory, 'bundle-manifest.json'), 16384));
  if (!m || Object.keys(m).sort().join(',') !== 'configurationDigest,files,installable,kind,product,sourcePlanSha256,version' ||
      m.version !== 1 || m.product !== 'DroidDeck' || m.kind !== 'service-component' || m.installable !== false ||
      m.sourcePlanSha256 !== PLAN_SHA256 || !/^[a-f0-9]{64}$/.test(m.configurationDigest) ||
      !Array.isArray(m.files) || m.files.length !== FILES.length) throw new Error('Invalid service manifest');
  const directories = new Map([['', new Set(['bundle-manifest.json'])]]);
  for (const name of FILES) {
    const parts = name.split('/');
    parts.forEach((part, i) => { const parent = parts.slice(0, i).join('/');
      if (!directories.has(parent)) directories.set(parent, new Set()); directories.get(parent).add(part); });
  }
  for (const [name, entries] of directories) {
    const filename = path.join(directory, name);
    if (!fs.lstatSync(filename).isDirectory() || fs.readdirSync(filename).sort().join('\n') !== [...entries].sort().join('\n')) throw new Error('Unexpected service entries');
  }
  m.files.forEach((f, index) => {
    if (!f || Object.keys(f).sort().join(',') !== 'path,sha256,size' || f.path !== FILES[index] ||
        !Number.isSafeInteger(f.size) || f.size < 1 || f.size > 16384 || !/^[a-f0-9]{64}$/.test(f.sha256)) throw new Error('Invalid service file record');
    const b = readBoundedRegular(path.join(directory, f.path), 16384);
    if (b.length !== f.size || sha(b) !== f.sha256) throw new Error('Service file bytes changed');
  });
  return m;
}
function buildServiceBundle({ configuration, output }) {
  const source = readBoundedRegular(path.join(__dirname, 'service-plan.js'), 16384);
  if (sha(Buffer.from(source.toString('utf8').replace(/\r\n/g, '\n'))) !== PLAN_SHA256) throw new Error('Service plan source pin changed');
  const plan = createServicePlan(configuration);
  if (Object.keys(plan.files).sort().join('\n') !== FILES.join('\n')) throw new Error('Service plan inventory changed');
  fs.mkdirSync(output, { mode: 0o700 });
  const records = FILES.map(name => {
    const bytes = Buffer.from(plan.files[name]), filename = path.join(output, name);
    if (!bytes.length || bytes.length > 16384) throw new Error('Service file bound');
    fs.mkdirSync(path.dirname(filename), { recursive: true, mode: 0o700 });
    fs.writeFileSync(filename, bytes, { flag: 'wx', mode: 0o600 });
    return { path: name, size: bytes.length, sha256: sha(bytes) };
  });
  const m = { version: 1, product: 'DroidDeck', kind: 'service-component', installable: false,
    sourcePlanSha256: PLAN_SHA256, configurationDigest: plan.configurationDigest, files: records };
  fs.writeFileSync(path.join(output, 'bundle-manifest.json'), JSON.stringify(m, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  return verifyServiceBundle(output);
}
function main(args) {
  let m;
  if (args.length === 3 && args[0] === 'build') m = buildServiceBundle({ configuration: readRuntimeConfig(args[1]), output: args[2] });
  else if (args.length === 2 && args[0] === 'verify') m = verifyServiceBundle(args[1]);
  else throw new Error('Use build CONFIG NEW_OUTPUT or verify OUTPUT');
  console.log(JSON.stringify({ kind: m.kind, files: m.files.length, configurationDigest: m.configurationDigest, installable: false }));
}
if (require.main === module) { try { main(process.argv.slice(2)); } catch (e) { console.error(e.message); process.exitCode = 1; } }
module.exports = { FILES, PLAN_SHA256, buildServiceBundle, verifyServiceBundle };
