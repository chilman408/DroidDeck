// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os');
const { inspectDependencyStage } = require('./dependency-receipt');
const integrity = 'sha512-' + Buffer.alloc(64, 1).toString('base64');
async function fixture() {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'droiddeck-dependency-receipt-'));
  const manifest = { name: 'teslaandroid-waydroid-display', version: '0.1.0', private: true, license: 'GPL-3.0-only', dependencies: { pngjs: '7.0.0' } };
  const lock = { name: manifest.name, version: manifest.version, lockfileVersion: 3, packages: {
    '': { name: manifest.name, version: manifest.version, dependencies: manifest.dependencies },
    'node_modules/pngjs': { version: '7.0.0', integrity, resolved: 'https://registry.npmjs.org/pngjs/-/pngjs-7.0.0.tgz', license: 'MIT' },
    'node_modules/development-only': { version: '1.0.0', dev: true },
  } };
  await fs.mkdir(path.join(directory, 'node_modules/pngjs'), { recursive: true });
  const write = (file, data) => fs.writeFile(path.join(directory, file), typeof data === 'object' ? JSON.stringify(data) : data);
  await write('package.json', manifest); await write('package-lock.json', lock);
  await write('node_modules/pngjs/package.json', { name: 'pngjs', version: '7.0.0', license: 'MIT' });
  await write('node_modules/pngjs/LICENSE', 'fixture license text\n'); await write('node_modules/pngjs/index.js', 'module.exports = {};\n');
  return { directory, manifest, lock, write, close: () => fs.rm(directory, { recursive: true, force: true }) };
}

test('dependency receipt records exact production bytes, source lock/SRI and license files without installing', async () => {
  const f = await fixture();
  try {
    const receipt = inspectDependencyStage('display', f.directory);
    assert.equal(receipt.installable, false); assert.equal(receipt.kind, 'dependency-receipt');
    assert.equal(receipt.packages.length, 1); assert.equal(receipt.fileCount, 3);
    assert.equal(receipt.packages[0].name, 'pngjs'); assert.equal(receipt.packages[0].integrity, integrity);
    assert.deepEqual(receipt.packages[0].licenseFiles, ['LICENSE']);
    assert.match(receipt.lockSha256, /^[a-f0-9]{64}$/); assert.match(receipt.filesSha256, /^[a-f0-9]{64}$/);
    assert.equal(Object.isFrozen(receipt.packages[0]), true); assert.doesNotMatch(JSON.stringify(receipt), /fixture license text|module.exports|development-only/);
    assert.equal(inspectDependencyStage('display', f.directory).filesSha256, receipt.filesSha256);
    await f.write('node_modules/pngjs/index.js', 'module.exports = 1;\n');
    assert.notEqual(inspectDependencyStage('display', f.directory).filesSha256, receipt.filesSha256);
  } finally { await f.close(); }
});

test('wrong stage identity, lock mismatch, missing/extra/development packages or missing license texts fail closed', async () => {
  for (const mutate of [async f => { f.manifest.name = 'other'; await f.write('package.json', f.manifest); },
    async f => { f.lock.packages[''].dependencies = { pngjs: '8.0.0' }; await f.write('package-lock.json', f.lock); },
    async f => f.write('node_modules/pngjs/package.json', { name: 'pngjs', version: '8.0.0' }),
    async f => fs.rm(path.join(f.directory, 'node_modules/pngjs/LICENSE')),
    async f => f.write('node_modules/pngjs/LICENSE', ''),
    async f => fs.mkdir(path.join(f.directory, 'node_modules/development-only')),
    async f => fs.rm(path.join(f.directory, 'node_modules/pngjs'), { recursive: true }),
    async f => f.write('node_modules/extra-secret-file', 'not part of dependencies')]) {
    const f = await fixture(); try { await mutate(f); assert.throws(() => inspectDependencyStage('display', f.directory)); } finally { await f.close(); }
  }
  assert.throws(() => inspectDependencyStage('../gateway', '/missing'));
});

test('noncanonical source URL, weak/malformed integrity and link declarations cannot become receipts', async () => {
  for (const change of [{ resolved: 'https://other.example/pngjs.tgz' }, { resolved: 'https://user:secret@registry.npmjs.org/pngjs/-/pngjs-7.0.0.tgz' },
    { integrity: 'sha1-weak' }, { integrity: 'sha512-broken' }, { link: true }]) {
    const f = await fixture(); try { Object.assign(f.lock.packages['node_modules/pngjs'], change); await f.write('package-lock.json', f.lock);
      assert.throws(() => inspectDependencyStage('display', f.directory)); } finally { await f.close(); }
  }
});

test('dependency inventory rejects links instead of reading outside a staged package', { skip: process.platform !== 'linux' }, async () => {
  const f = await fixture(); try { await fs.symlink('/etc/passwd', path.join(f.directory, 'node_modules/pngjs/foreign'));
    assert.throws(() => inspectDependencyStage('display', f.directory), /Unsafe/); } finally { await f.close(); }
});

test('dependency inventory rejects hardlinks, oversized artifacts and oversized lock inventories', async () => {
  for (const mutate of [async f => fs.link(path.join(f.directory, 'node_modules/pngjs/index.js'), path.join(f.directory, 'node_modules/pngjs/duplicate.js')),
    async f => fs.truncate(path.join(f.directory, 'node_modules/pngjs/index.js'), 8 * 1024 * 1024 + 1),
    async f => { for (let i = 0; i < 129; i++) f.lock.packages['node_modules/fixture-' + i] = {
      version: '1.0.0', integrity, resolved: 'https://registry.npmjs.org/fixture-' + i + '/-/fixture-' + i + '-1.0.0.tgz' };
      await f.write('package-lock.json', f.lock); }]) {
    const f = await fixture(); try { await mutate(f); assert.throws(() => inspectDependencyStage('display', f.directory)); } finally { await f.close(); }
  }
});

test('case-colliding dependency files cannot form a portable inventory', { skip: process.platform !== 'linux' }, async () => {
  const f = await fixture(); try { await f.write('node_modules/pngjs/COPY.js', '1'); await f.write('node_modules/pngjs/copy.js', '2');
    assert.throws(() => inspectDependencyStage('display', f.directory), /Case-colliding/); } finally { await f.close(); }
});
