// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), cp = require('node:child_process');
const { buildFrontend, verifyBundle, validateManifest } = require('./frontend-bundle');
function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-bundle-')), repo = path.join(root, 'upstream');
  fs.mkdirSync(repo);
  const write = (name, data) => { const target = path.join(repo, name); fs.mkdirSync(path.dirname(target), { recursive: true }); fs.writeFileSync(target, data); };
  write('LICENSE', 'Fixture license\r\n'); write('index.html', '<title>Fixture</title>\r\n');
  write('js/app.js', '\uFEFFconst original = true;\r\n'); write('assets/font.ttf', Buffer.from([0, 255, 128, 13, 10]));
  write('private.pem', 'DO NOT EXPORT');
  const git = args => cp.execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8' }).trim();
  git(['init', '-q']); git(['add', '.']);
  git(['-c', 'user.name=Bundle Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Fixture']);
  const revision = git(['rev-parse', 'HEAD']);
  write('index.html', 'Different HEAD'); git(['add', 'index.html']);
  git(['-c', 'user.name=Bundle Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Not the pinned revision']);
  write('index.html', 'Dirty working copy'); write('js/app.js', 'Dirty script');
  const patchFile = path.join(root, 'project.patch');
  fs.writeFileSync(patchFile, 'diff --git a/index.html b/index.html\n--- a/index.html\n+++ b/index.html\n@@ -1 +1 @@\n-<title>Fixture</title>\n+<title>DroidDeck</title>\n');
  const logo = path.join(root, 'logo.png'), qr = path.join(root, 'qr.svg');
  fs.writeFileSync(logo, Buffer.from([0, 255, 128, 13, 10])); fs.writeFileSync(qr, '<svg/>\r\n');
  return { root, repo, revision, patchFile, assets: { 'assets/droiddeck-logo.png': logo, 'assets/donations-qr.svg': qr },
    build: output => buildFrontend({ upstreamRepo: repo, revision, output, patchFile, assets: { 'assets/droiddeck-logo.png': logo, 'assets/donations-qr.svg': qr } }) };
}
function cleanup(root) {
  const resolved = path.resolve(root);
  assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
  assert.match(path.basename(resolved), /^droiddeck-bundle-/);
  assert.equal(fs.lstatSync(resolved).isDirectory(), true);
  fs.rmSync(resolved, { recursive: true, force: true });
}
test('pinned Git objects, not dirty checkout or private files, produce deterministic component bytes', () => {
  const f = fixture(), one = path.join(f.root, 'one'), two = path.join(f.root, 'two');
  try {
    f.build(one); f.build(two);
    const manifest = verifyBundle(one);
    assert.equal(manifest.installable, false); assert.equal(manifest.kind, 'frontend-component');
    assert.equal(fs.readFileSync(path.join(one, 'index.html'), 'utf8'), '<title>DroidDeck</title>\n');
    assert.equal(fs.readFileSync(path.join(one, 'js/app.js'), 'utf8'), '\uFEFFconst original = true;\n');
    assert.equal(fs.readFileSync(path.join(one, 'LICENSE'), 'utf8'), 'Fixture license\n');
    assert.deepEqual(fs.readFileSync(path.join(one, 'assets/font.ttf')), Buffer.from([0, 255, 128, 13, 10]));
    assert.equal(fs.existsSync(path.join(one, 'private.pem')), false);
    assert.deepEqual(fs.readFileSync(path.join(one, 'bundle-manifest.json')), fs.readFileSync(path.join(two, 'bundle-manifest.json')));
  } finally { cleanup(f.root); }
});
test('Git replacement refs cannot substitute bytes for pinned source objects', () => {
  const f = fixture(), out = path.join(f.root, 'out');
  try {
    const git = args => cp.execFileSync('git', ['-C', f.repo, ...args], { encoding: 'utf8' }).trim();
    const object = git(['rev-parse', f.revision + ':assets/font.ttf']);
    const secret = path.join(f.root, 'private-fixture.txt'); fs.writeFileSync(secret, 'PRIVATE FIXTURE');
    const replacement = git(['hash-object', '-w', secret]); git(['replace', object, replacement]);
    f.build(out);
    assert.deepEqual(fs.readFileSync(path.join(out, 'assets/font.ttf')), Buffer.from([0, 255, 128, 13, 10]));
    assert.equal(fs.readFileSync(path.join(out, 'assets/font.ttf')).includes(Buffer.from('PRIVATE FIXTURE')), false);
  } finally { cleanup(f.root); }
});

test('approved branding pin is enforced at the copy boundary and failure never publishes a manifest', () => {
  const f = fixture(), out = path.join(f.root, 'pinned'), refused = path.join(f.root, 'refused');
  try {
    const pin = require('node:crypto').createHash('sha256').update(fs.readFileSync(f.assets['assets/droiddeck-logo.png'])).digest('hex');
    const options = { upstreamRepo: f.repo, revision: f.revision, patchFile: f.patchFile, assets: f.assets };
    const manifest = buildFrontend({ ...options, output: out, assetPins: { 'assets/droiddeck-logo.png': pin } });
    assert.equal(manifest.files.find(file => file.path === 'assets/droiddeck-logo.png').sha256, pin);
    assert.throws(() => buildFrontend({ ...options, output: refused, assetPins: { 'assets/droiddeck-logo.png': '0'.repeat(64) } }), /asset bytes/);
    assert.equal(fs.existsSync(path.join(refused, 'bundle-manifest.json')), false);
    const invalid = path.join(f.root, 'invalid-pin');
    assert.throws(() => buildFrontend({ ...options, output: invalid, assetPins: { 'js/app.js': pin } }), /asset pins/);
    assert.equal(fs.existsSync(invalid), false);
  } finally { cleanup(f.root); }
});

test('missing frontend Git objects reject without fetching or changing the cache', () => {
  const f = fixture(), out = path.join(f.root, 'out');
  try {
    const { missingPublicObject } = require('../../tests/fixtures/git-promisor');
    const cache = missingPublicObject({ source: f.repo, revision: f.revision, missingPath: 'assets/font.ttf', root: f.root });
    assert.equal(cache.hasObject(), false); const before = cache.packNames();
    assert.throws(() => buildFrontend({ upstreamRepo: cache.repository, revision: f.revision, output: out, patchFile: f.patchFile, assets: f.assets }));
    assert.equal(cache.hasObject(), false); assert.deepEqual(cache.packNames(), before);
    assert.equal(fs.existsSync(cache.fetchHead), false);
    assert.equal(fs.existsSync(path.join(out, 'bundle-manifest.json')), false);
  } finally { cleanup(f.root); }
});
test('file and directory paths share one case-folded namespace', () => {
  const f = fixture(), out = path.join(f.root, 'out');
  try {
    const base = f.build(out), modified = structuredClone(base);
    modified.files.push(...['js/A.js/b.js', 'js/a.js'].map(file => ({ ...base.files[0], path: file })));
    modified.files.sort((a, b) => a.path < b.path ? -1 : 1);
    assert.throws(() => validateManifest(modified), /collid/i);
    const prefix = structuredClone(base);
    prefix.files.push(...['js/A.js', 'js/A.js/b.js'].map(file => ({ ...base.files[0], path: file })));
    prefix.files.sort((a, b) => a.path < b.path ? -1 : 1);
    assert.throws(() => validateManifest(prefix), /collid/i);
  } finally { cleanup(f.root); }
});
test('existing output is never overwritten and a bad patch leaves only a non-installable inspection directory', () => {
  const f = fixture(), out = path.join(f.root, 'output');
  try {
    f.build(out); const before = fs.readFileSync(path.join(out, 'bundle-manifest.json'));
    assert.throws(() => f.build(out), /exist/i);
    assert.deepEqual(fs.readFileSync(path.join(out, 'bundle-manifest.json')), before);
    fs.writeFileSync(f.patchFile, 'diff --git a/index.html b/index.html\n--- a/index.html\n+++ b/index.html\n@@ -1 +1 @@\n-NOT THE SOURCE\n+Invalid candidate\n');
    assert.throws(() => f.build(path.join(f.root, 'bad')), /patch|path/i);
    assert.equal(fs.existsSync(path.join(f.root, 'bad')), true);
    assert.equal(fs.existsSync(path.join(f.root, 'bad/bundle-manifest.json')), false);
  } finally { cleanup(f.root); }
});
test('unsafe patch paths, binary data, modes and license edits cannot reach output', () => {
  const f = fixture();
  try {
    for (const [index, patch] of [
      'diff --git a/../private.pem b/../private.pem\n',
      'diff --git a/js/CON/app.js b/js/CON/app.js\n',
      'diff --git a/LICENSE b/LICENSE\n',
      'diff --git a/index.html b/index.html\nnew mode 120000\n',
      'diff --git a/index.html b/index.html\nGIT binary patch\n',
      'diff --git a/index.html b/js/other.js\n',
    ].entries()) {
      fs.writeFileSync(f.patchFile, patch); const out = path.join(f.root, 'unsafe-' + index);
      assert.throws(() => f.build(out)); assert.equal(fs.existsSync(out), false);
    }
  } finally { cleanup(f.root); }
});
test('changed, missing, extra and oversized files cannot pass integrity verification', () => {
  const f = fixture();
  try {
    for (const mutation of ['changed', 'missing', 'extra', 'oversized']) {
      const out = path.join(f.root, mutation); f.build(out);
      if (mutation === 'changed') fs.appendFileSync(path.join(out, 'index.html'), 'changed');
      if (mutation === 'missing') fs.unlinkSync(path.join(out, 'index.html'));
      if (mutation === 'extra') fs.writeFileSync(path.join(out, 'secret.key'), 'secret');
      if (mutation === 'oversized') fs.writeFileSync(path.join(out, 'js/extra.js'), Buffer.alloc(8 * 1024 * 1024 + 1));
      assert.throws(() => verifyBundle(out));
    }
  } finally { cleanup(f.root); }
});
test('manifest rejects unsafe paths, duplicates, case collisions, unsorted records and false release metadata', () => {
  const f = fixture(), out = path.join(f.root, 'out');
  try {
    const base = f.build(out);
    for (const value of ['../x.js', 'js\\x.js', '/js/x.js', 'js/.secret.js', 'js/a/../x.js', 'assets/private.pem', 'js/CON/app.js', 'js/dir./app.js']) {
      const m = structuredClone(base); m.files[0].path = value; assert.throws(() => validateManifest(m));
    }
    for (const change of [m => m.files.push(m.files[0]), m => m.files.reverse(), m => m.installable = true,
      m => m.sourceRevision = 'HEAD', m => m.sourceRevision = [m.sourceRevision], m => m.patchSha256 = [m.patchSha256],
      m => m.files[0].sha256 = [m.files[0].sha256], m => m.files[0].size = -1, m => m.extra = true,
      m => m.files.push({ ...m.files[0], path: m.files[0].path.toUpperCase() })]) {
      const m = structuredClone(base); change(m); assert.throws(() => validateManifest(m));
    }
    const mixedDirectories = structuredClone(base);
    mixedDirectories.files.push(...['js/Dir/a.js', 'js/dir/b.js'].map(file => ({ ...base.files[0], path: file })));
    mixedDirectories.files.sort((a, b) => a.path < b.path ? -1 : 1);
    assert.throws(() => validateManifest(mixedDirectories), /Case-colliding/);
  } finally { cleanup(f.root); }
});
test('hard links and empty extra directories are rejected', () => {
  const f = fixture(), out = path.join(f.root, 'out');
  try {
    f.build(out); fs.linkSync(path.join(out, 'index.html'), path.join(f.root, 'alias'));
    assert.throws(() => verifyBundle(out)); fs.unlinkSync(path.join(f.root, 'alias'));
    fs.mkdirSync(path.join(out, 'js/unused')); assert.throws(() => verifyBundle(out));
  } finally { cleanup(f.root); }
});
test('symbolic links cannot be shipped or verified', { skip: process.platform === 'win32' }, () => {
  const f = fixture(), out = path.join(f.root, 'out');
  try {
    f.build(out); fs.unlinkSync(path.join(out, 'index.html'));
    fs.symlinkSync(path.join(f.repo, 'index.html'), path.join(out, 'index.html')); assert.throws(() => verifyBundle(out));
  } finally { cleanup(f.root); }
});
