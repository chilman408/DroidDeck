// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), path = require('node:path'), { createHash } = require('node:crypto');
const { inspectDependencyStage } = require('./dependency-receipt');
const { readBoundedRegular } = require('./read-bounded');
const PINS = Object.freeze({
  gateway: Object.freeze({ manifestSha256: '8fcaef9f5a9e9a0391b04a831aae75269f495a9eac662268d591891081a1af3f',
    lockSha256: 'f901bd3bbde7880f468e1e02df179551d84f041dba0fe0789c196e63ec26560f',
    filesSha256: '36a31e5d8046a012b1ff78716102347144af36523f4463db2669e8122b554d98' }),
  display: Object.freeze({ manifestSha256: '411ec4535eb1eda92b0218d735b02aaca8210adb15936775bfdd5b0334fc3337',
    lockSha256: 'b694e109e850916294c12fd0850f69f0a0733e9d40b287e8b7380d0c3506e679',
    filesSha256: 'b4a6884fea6fd759584daeefa1316d25e9ea539b0a0f6be0bd04610fa6a3be88' })
});
const sha = b => createHash('sha256').update(b).digest('hex');
function matched(receipt, pins) {
  if (!pins || ['manifestSha256', 'lockSha256', 'filesSha256'].some(k => receipt[k] !== pins[k])) throw new Error('Dependency policy pins mismatch');
}
function inventory(directory, manifestAllowed = false) {
  if (!fs.lstatSync(directory).isDirectory()) throw new Error('Unsafe dependency component root');
  const records = [], dirs = [], names = new Set(); let total = 0, entries = 0;
  function walk(relative) {
    for (const name of fs.readdirSync(path.join(directory, relative)).sort()) {
      if (++entries > 10000 || !/^[A-Za-z0-9._-]+$/.test(name) || ['.', '..'].includes(name) || name.endsWith('.')) throw new Error('Unsafe dependency path');
      const file = relative ? relative + '/' + name : name, filename = path.join(directory, file), st = fs.lstatSync(filename);
      if (file.length > 240 || file.split('/').length > 20 || names.has(file.toLowerCase())) throw new Error('Dependency namespace collision');
      names.add(file.toLowerCase());
      if (st.isDirectory()) { dirs.push(file); walk(file); }
      else if (manifestAllowed && file === 'bundle-manifest.json') readBoundedRegular(filename, 1024 * 1024);
      else if (!relative && name === '.package-lock.json' && !manifestAllowed) readBoundedRegular(filename, 1024 * 1024);
      else {
        const bytes = readBoundedRegular(filename, 8 * 1024 * 1024); total += bytes.length;
        if (total > 64 * 1024 * 1024 || records.length >= 1000) throw new Error('Dependency component too large');
        records.push({ path: file, size: bytes.length, sha256: sha(bytes) });
      }
    }
  }
  walk(''); records.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  if (dirs.some(d => !records.some(r => r.path.startsWith(d + '/')))) throw new Error('Empty dependency directory');
  return records;
}
function verifyDependencyBundle(directory, { pins = PINS } = {}) {
  const m = JSON.parse(readBoundedRegular(path.join(directory, 'bundle-manifest.json'), 1024 * 1024));
  if (!m || Object.keys(m).sort().join(',') !== 'files,filesSha256,installable,kind,lockSha256,manifestSha256,product,role,version' ||
      m.version !== 1 || m.product !== 'DroidDeck' || m.kind !== 'runtime-dependency-component' || m.installable !== false ||
      !Object.hasOwn(PINS, m.role)) throw new Error('Invalid dependency component manifest');
  matched(m, pins[m.role]);
  const records = inventory(directory, true);
  if (JSON.stringify(records) !== JSON.stringify(m.files) || sha(JSON.stringify(records)) !== m.filesSha256) throw new Error('Dependency component bytes changed');
  return m;
}
function buildDependencyBundle({ role, source, output, pins = PINS }) {
  if (!Object.hasOwn(PINS, role)) throw new Error('Unknown dependency role');
  const receipt = inspectDependencyStage(role, source);
  matched(receipt, pins[role]);
  const input = path.join(source, 'node_modules'), records = inventory(input);
  if (sha(JSON.stringify(records)) !== receipt.filesSha256) throw new Error('Dependency inventory changed');
  fs.mkdirSync(output, { mode: 0o700 });
  for (const r of records) {
    const bytes = readBoundedRegular(path.join(input, r.path), 8 * 1024 * 1024);
    if (bytes.length !== r.size || sha(bytes) !== r.sha256) throw new Error('Dependency source changed');
    const filename = path.join(output, r.path);
    fs.mkdirSync(path.dirname(filename), { recursive: true, mode: 0o700 });
    fs.writeFileSync(filename, bytes, { flag: 'wx', mode: 0o600 });
  }
  matched(inspectDependencyStage(role, source), pins[role]);
  const m = { version: 1, product: 'DroidDeck', kind: 'runtime-dependency-component', installable: false,
    role, ...pins[role], files: records };
  fs.writeFileSync(path.join(output, 'bundle-manifest.json'), JSON.stringify(m, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  return verifyDependencyBundle(output, { pins });
}
function main(args) {
  let m;
  if (args.length === 4 && args[0] === 'build') m = buildDependencyBundle({ role: args[1], source: args[2], output: args[3] });
  else if (args.length === 2 && args[0] === 'verify') m = verifyDependencyBundle(args[1]);
  else throw new Error('Use build ROLE SOURCE NEW_OUTPUT or verify OUTPUT');
  console.log(JSON.stringify({ kind: m.kind, role: m.role, files: m.files.length, filesSha256: m.filesSha256, installable: false }));
}
if (require.main === module) { try { main(process.argv.slice(2)); } catch (error) { console.error(error.message); process.exitCode = 1; } }
module.exports = { PINS, buildDependencyBundle, verifyDependencyBundle };
