// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), { createHash } = require('node:crypto');
const { readBoundedRegular } = require('./read-bounded');
const { normalizeRuntimeConfig, readRuntimeConfig } = require('./runtime-config');
const { configurationDigest } = require('./configuration-generation');
const PINS = Object.freeze({ 'headless-session.py': '744be141e0b26b507d70ac77275f2507f2e28a4659a0f0489206d5fd384334ba',
  'host-platform-compat.py': '6a1191680ed1380a1bcecbd9c6704cfa5ff9837935b24e33af9aae1fc51471b2' });
const FLAGS = ['installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted', 'accountValidated'];
const sha = bytes => createHash('sha256').update(bytes).digest('hex'), encode = value => Buffer.from(JSON.stringify(value, null, 2) + '\n');
function canonical(value) {
  if (typeof value !== 'string' || !path.isAbsolute(value) || path.resolve(value) !== value || value.includes('\0')) throw Error('Canonical component path required');
  return value;
}
function directories(filename, privateHome) {
  let current = path.parse(canonical(filename)).root;
  for (const part of filename.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part); const st = fs.lstatSync(current);
    if (!st.isDirectory() || st.isSymbolicLink() || (privateHome && (current === privateHome || current.startsWith(privateHome + path.sep)) &&
        (st.uid !== process.getuid() || st.mode & 0o022))) throw Error('Unsafe component parent');
  }
}
function plan(configuration, profile) {
  const config = normalizeRuntimeConfig(configuration);
  if (typeof profile !== 'string' || !/^[a-f0-9]{64}$/.test(profile)) throw Error('Invalid Waydroid profile hash');
  const generation = configurationDigest(config), files = {};
  for (const name of ['headless-runtime.py', ...Object.keys(PINS)]) {
    const bytes = Buffer.from(readBoundedRegular(path.join(__dirname, name), 32768).toString('utf8').replace(/\r\n/g, '\n'));
    if (PINS[name] && sha(bytes) !== PINS[name]) throw Error('Accepted session source pin changed');
    files[name] = bytes;
  }
  files['policy.json'] = encode({ version: 1, configurationDigest: generation, waydroidConfigurationSha256: profile });
  files['systemd/60-droiddeck-headless.conf'] = Buffer.from('[Service]\nType=notify\nNotifyAccess=main\nExecStart=\n' +
    'ExecStart=/usr/bin/python3 -I -B /opt/droiddeck/headless-runtime/headless-runtime.py\nTimeoutStartSec=60s\n');
  return { generation, files };
}
function manifest(generation, profile, files) {
  return { version: 1, product: 'DroidDeck', kind: 'headless-session-component', configurationDigest: generation,
    waydroidConfigurationSha256: profile, ...Object.fromEntries(FLAGS.map(k => [k, false])),
    files: Object.keys(files).sort().map(name => ({ path: name, size: files[name].length, sha256: sha(files[name]) })) };
}
function verifyHeadlessBundle(configuration, profile, directory) {
  const root = canonical(directory), p = plan(configuration, profile), expected = manifest(p.generation, profile, p.files);
  directories(root);
  if (fs.readdirSync(root).sort().join(',') !== 'bundle-manifest.json,headless-runtime.py,headless-session.py,host-platform-compat.py,policy.json,systemd' ||
      !fs.lstatSync(path.join(root, 'systemd')).isDirectory() || fs.lstatSync(path.join(root, 'systemd')).isSymbolicLink() ||
      fs.readdirSync(path.join(root, 'systemd')).join(',') !== '60-droiddeck-headless.conf') throw Error('Unexpected headless inventory');
  for (const [name, bytes] of Object.entries(p.files)) {
    if (!readBoundedRegular(path.join(root, name), 32768).equals(bytes)) throw Error('Headless recipe changed');
  }
  const stored = readBoundedRegular(path.join(root, 'bundle-manifest.json'), 32768);
  if (!stored.equals(encode(expected))) throw Error('Headless manifest changed');
  return expected;
}
function write(filename, bytes) {
  const fd = fs.openSync(filename, fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY, 0o600);
  try { fs.writeFileSync(fd, bytes); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function buildHeadlessBundle(configuration, profile, directory) {
  const root = canonical(directory), p = plan(configuration, profile);
  directories(path.dirname(root)); fs.mkdirSync(root, { mode: 0o700 });
  fs.mkdirSync(path.join(root, 'systemd'), { mode: 0o700 });
  for (const [name, bytes] of Object.entries(p.files)) write(path.join(root, name), bytes);
  write(path.join(root, 'bundle-manifest.json'), encode(manifest(p.generation, profile, p.files)));
  return verifyHeadlessBundle(configuration, profile, root);
}
function main(args) {
  if (args.length !== 4 || !['build', 'verify'].includes(args[0])) throw Error('Use build|verify CONFIG WAYDROID_CONFIG OUTPUT');
  const configuration = readRuntimeConfig(canonical(args[1]));
  const profile = sha(readBoundedRegular(canonical(args[2]), 65536)), output = canonical(args[3]);
  let m;
  if (args[0] === 'build') {
    if (process.platform !== 'linux' || !process.getuid || process.getuid() === 0) throw Error('Unprivileged Linux build required');
    const home = path.resolve(os.homedir());
    if (!output.startsWith(home + path.sep)) throw Error('Component output outside home');
    directories(path.dirname(output), home); m = buildHeadlessBundle(configuration, profile, output);
  } else m = verifyHeadlessBundle(configuration, profile, output);
  console.log(JSON.stringify({ kind: m.kind, configurationDigest: m.configurationDigest, files: m.files.length,
    installable: false, readyForActivation: false, servicesActivated: false, androidOperationsExecuted: false }));
}
if (require.main === module) { try { main(process.argv.slice(2)); } catch (_) { console.error('Headless component rejected; no activation performed'); process.exitCode = 1; } }
module.exports = { buildHeadlessBundle, verifyHeadlessBundle, main };
