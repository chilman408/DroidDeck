// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const api = require('./artifact-build');
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
const roles = ['touch', 'api', 'display', 'gps', 'logical', 'binder'];
const component = {
  touch: 'touch-native-preview', api: 'api-native-preview', display: 'diagnostic-display-native-preview',
  gps: 'gps-java-dex-preview', logical: 'logical-capture-java-dex-preview', binder: 'binder-shared-library-preview'
};
const files = {
  touch: ['tesla-android-x86-touch'], api: ['tesla-android-x86-api'], display: ['tesla-android-x86-display-latest'],
  gps: ['location-bridge.jar'], logical: ['logical-capture.jar'], binder: ['libs/libglibutil.so.1.0.80', 'libs/libgbinder.so.1.1.45']
};
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'artifact-build-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const inputs = Object.fromEntries(['libwsRepo', 'apiRepo', 'gbinderRepo', 'glibutilRepo', 'd8Jar', 'androidJar', 'scrcpyServer']
    .map(k => [k, path.join(root, k)]));
  const publisher = Buffer.from('generated publisher fixture');
  fs.writeFileSync(inputs.scrcpyServer, publisher);
  const publisherPin = { size: publisher.length, sha256: sha(publisher) }, calls = [];
  const builders = Object.fromEntries(roles.map(role => [role, options => {
    calls.push({ role, options }); fs.mkdirSync(options.output);
    const records = files[role].map(p => {
      const target = path.join(options.output, p); fs.mkdirSync(path.dirname(target), { recursive: true });
      const bytes = Buffer.from('generated artifact ' + p); fs.writeFileSync(target, bytes);
      return { path: p, name: p, size: bytes.length, sha256: sha(bytes) };
    });
    const receipt = { format: 1, component: component[role], installable: false, nativeTestsPassed: true,
      elfStaticX86_64: true, serverExecuted: false, androidOperationsExecuted: false, browserPngFallback: false,
      hostTestsPassed: true, dexReparsePassed: true, symbolSmokePassed: true, binderOperationsExecuted: false };
    if (['touch', 'api', 'display'].includes(role)) receipt.artifact = records[0];
    else if (role === 'binder') receipt.artifacts = { glibutil: records[0], gbinder: records[1] };
    else receipt.artifacts = { jar: records[0] };
    return receipt;
  }]));
  return { root, inputs, calls, options: { builders, publisherPin }, output: path.join(root, 'output') };
}
test('builds six components in order and collects only eight artifacts', t => {
  const f = fixture(t), receipt = api._buildArtifacts(f.inputs, f.output, f.options);
  assert.deepEqual(f.calls.map(c => c.role), roles);
  assert.equal(f.calls[0].options.libwsRepo, f.inputs.libwsRepo);
  assert.equal(f.calls[1].options.upstreamRepo, f.inputs.apiRepo);
  assert.equal(f.calls[4].options.androidJar, f.inputs.androidJar);
  assert.equal(f.calls[5].options.glibutilRepo, f.inputs.glibutilRepo);
  assert.equal(receipt.installable, false); assert.equal(receipt.servicesActivated, false);
  assert.equal(receipt.androidOperationsExecuted, false); assert.equal(receipt.files.length, 8);
  assert.equal(receipt.buildReceipts.length, 6);
  assert.deepEqual(api._verifyArtifacts(path.join(f.output, 'artifacts'), f.options.publisherPin), receipt);
  assert.equal(fs.readdirSync(path.join(f.output, 'artifacts')).length, 9);
  assert.throws(() => api._buildArtifacts(f.inputs, f.output, f.options));
});
test('identical fixture builds have identical path-free manifests', t => {
  const f = fixture(t), a = api._buildArtifacts(f.inputs, f.output, f.options);
  const b = api._buildArtifacts(f.inputs, path.join(f.root, 'second'), f.options);
  assert.deepEqual(a, b); assert(!JSON.stringify(a).includes(f.root));
});

function pair(t) {
  const f = fixture(t);
  api._buildArtifacts(f.inputs, f.output, f.options);
  api._buildArtifacts(f.inputs, path.join(f.root, 'second'), f.options);
  return { ...f, left: path.join(f.output, 'artifacts'), right: path.join(f.root, 'second/artifacts') };
}

test('fresh pair invokes all six builders twice and compares both closed outputs', t => {
  const f = fixture(t), result = api._reproduceArtifacts(f.inputs, f.output, f.options);
  assert.deepEqual(f.calls.map(c => c.role), [...roles, ...roles]);
  assert.deepEqual(fs.readdirSync(f.output).sort(), ['first', 'second']);
  assert.equal(result.freshBuildInvocationsCompleted, 2);
  assert.equal(result.sameHostBuildComparisonVerified, true);
  assert.equal(result.artifactBytesMatched, true); assert.equal(result.buildReceiptDigestsMatched, true);
  assert.equal(result.payloadFiles, 8); assert.equal(result.buildComponents, 6); assert.equal(result.filesWritten, true);
  for (const key of ['independentBuildExecutionVerified', 'installable', 'servicesActivated', 'androidOperationsExecuted']) {
    assert.equal(result[key], false);
  }
  assert(!JSON.stringify(result).includes(f.root)); assert.match(result.comparisonSha256, /^[a-f0-9]{64}$/);
  assert.equal(new Set(f.calls.map(c => c.options.output)).size, 12);
});

test('fresh pair validates input schema and publisher before creating output', t => {
  const f = fixture(t);
  assert.throws(() => api._reproduceArtifacts({ ...f.inputs, foreign: 'private-sentinel' }, f.output, f.options), /metadata/);
  fs.writeFileSync(f.inputs.scrcpyServer, 'generated mismatch');
  assert.throws(() => api._reproduceArtifacts(f.inputs, f.output, f.options), /Artifact differs/);
  assert.equal(fs.existsSync(f.output), false); assert.equal(f.calls.length, 0);
});

test('fresh pair preserves existing targets without adopting or running builders', t => {
  const f = fixture(t); fs.mkdirSync(f.output); fs.writeFileSync(path.join(f.output, 'sentinel'), 'generated foreign');
  assert.throws(() => api._reproduceArtifacts(f.inputs, f.output, f.options), /EEXIST/);
  assert.equal(fs.readFileSync(path.join(f.output, 'sentinel'), 'utf8'), 'generated foreign');
  assert.equal(f.calls.length, 0);
});

test('fresh pair stops on either build failure and preserves partial outputs without retry', t => {
  for (const attempt of [1, 2]) {
    const f = fixture(t), original = f.options.builders.touch; let calls = 0;
    f.options.builders.touch = options => {
      if (++calls === attempt) throw Error('generated build failure');
      return original(options);
    };
    assert.throws(() => api._reproduceArtifacts(f.inputs, f.output, f.options), /generated build failure/);
    assert.equal(calls, attempt); assert.equal(fs.existsSync(path.join(f.output, 'first')), true);
    assert.equal(fs.existsSync(path.join(f.output, 'second')), attempt === 2);
    assert.throws(() => api._reproduceArtifacts(f.inputs, f.output, f.options), /EEXIST/);
    assert.equal(calls, attempt);
  }
});

test('fresh pair rejects different self-consistent second build artifacts', t => {
  const f = fixture(t), original = f.options.builders.touch; let calls = 0;
  f.options.builders.touch = options => {
    const receipt = original(options);
    if (++calls === 2) {
      const data = Buffer.from('generated nondeterministic binary');
      fs.writeFileSync(path.join(options.output, files.touch[0]), data);
      receipt.artifact = { ...receipt.artifact, size: data.length, sha256: sha(data) };
    }
    return receipt;
  };
  assert.throws(() => api._reproduceArtifacts(f.inputs, f.output, f.options), /reproducibility comparison mismatch/);
  assert.equal(calls, 2); assert.equal(fs.existsSync(path.join(f.output, 'first/artifacts/bundle-manifest.json')), true);
  assert.equal(fs.existsSync(path.join(f.output, 'second/artifacts/bundle-manifest.json')), true);
});

test('fresh pair rejects byte-identical first artifact directory replacement during second build', t => {
  const f = fixture(t), original = f.options.builders.touch; let calls = 0;
  f.options.builders.touch = options => {
    if (++calls === 2) {
      const first = path.join(f.output, 'first/artifacts'), saved = path.join(f.root, 'generated-saved');
      fs.renameSync(first, saved); fs.cpSync(saved, first, { recursive: true });
    }
    return original(options);
  };
  assert.throws(() => api._reproduceArtifacts(f.inputs, f.output, f.options), /Original fresh build directory/);
});

test('fresh pair rejects unexpected outer files added by a builder', t => {
  const f = fixture(t), original = f.options.builders.touch;
  f.options.builders.touch = options => {
    fs.writeFileSync(path.join(f.output, 'unexpected'), 'generated');
    return original(options);
  };
  assert.throws(() => api._reproduceArtifacts(f.inputs, f.output, f.options), /Closed build-pair inventory/);
  assert.equal(f.calls.length, 6);
});

test('production fresh-pair CLI cannot override publisher pins or expose input paths', t => {
  const f = fixture(t), input = path.join(f.root, 'inputs.json'); fs.writeFileSync(input, JSON.stringify(f.inputs));
  for (const args of [['reproduce', input, f.output], ['reproduce', input, f.output, '--publisher-pin', 'private-sentinel']]) {
    const result = spawnSync(process.execPath, [path.join(__dirname, 'artifact-build.js'), ...args], { encoding: 'utf8', timeout: 10000 });
    assert.equal(result.status, 1); assert.equal(result.stdout, '');
    assert.equal(result.stderr.trim(), 'Artifact build rejected; partial output is not installable');
    assert.equal(fs.existsSync(f.output), false);
  }
});

test('comparison verifies eight artifacts and six receipts without executing builders or writing files', t => {
  const f = pair(t), calls = f.calls.length;
  const snapshot = root => Object.fromEntries(fs.readdirSync(root).map(name => [name, sha(fs.readFileSync(path.join(root, name)))]));
  const before = [snapshot(f.left), snapshot(f.right)];
  const result = api._compareArtifacts(f.left, f.right, f.options.publisherPin);
  assert.equal(result.artifactBytesMatched, true); assert.equal(result.buildReceiptDigestsMatched, true);
  assert.equal(result.payloadFiles, 8); assert.equal(result.buildComponents, 6);
  for (const key of ['installable', 'servicesActivated', 'androidOperationsExecuted', 'filesWritten', 'independentBuildExecutionVerified']) {
    assert.equal(result[key], false);
  }
  assert.match(result.comparisonSha256, /^[a-f0-9]{64}$/);
  assert(!JSON.stringify(result).includes(f.root)); assert.equal(f.calls.length, calls);
  assert.deepEqual([snapshot(f.left), snapshot(f.right)], before);
});

test('comparison rejects one directory twice, overlapping paths and aliases', t => {
  const f = pair(t);
  assert.throws(() => api._compareArtifacts(f.left, f.left, f.options.publisherPin), /Disjoint/);
  assert.throws(() => api._compareArtifacts(f.left, path.dirname(f.left), f.options.publisherPin), /Disjoint/);
  const alias = path.join(f.root, 'artifact-alias');
  fs.symlinkSync(f.left, alias, process.platform === 'win32' ? 'junction' : 'dir');
  assert.throws(() => api._compareArtifacts(f.left, alias, f.options.publisherPin), /Unsafe build directory/);
});

test('comparison rejects different but individually valid native Java library and publisher bytes', t => {
  const f = pair(t), manifest = path.join(f.right, 'bundle-manifest.json');
  const original = fs.readFileSync(manifest);
  for (const name of ['tesla-android-x86-touch', 'location-bridge.jar', 'libgbinder.so.1.1.45', 'scrcpy-server-v4.0']) {
    const target = path.join(f.right, name), previous = fs.readFileSync(target), changed = Buffer.from('changed generated artifact');
    fs.writeFileSync(target, changed);
    const value = JSON.parse(original), row = value.files.find(f => f.path === name);
    row.size = changed.length; row.sha256 = sha(changed); fs.writeFileSync(manifest, JSON.stringify(value));
    assert.throws(() => api._compareArtifacts(f.left, f.right, f.options.publisherPin), /reproducibility|Publisher artifact mismatch/);
    fs.writeFileSync(target, previous); fs.writeFileSync(manifest, original);
  }
});

test('matching payloads with different component receipt digests cannot pass comparison', t => {
  const f = pair(t), filename = path.join(f.right, 'bundle-manifest.json');
  const manifest = JSON.parse(fs.readFileSync(filename)); manifest.buildReceipts[0].sha256 = '0'.repeat(64);
  fs.writeFileSync(filename, JSON.stringify(manifest));
  api._verifyArtifacts(f.right, f.options.publisherPin);
  assert.throws(() => api._compareArtifacts(f.left, f.right, f.options.publisherPin), /reproducibility/);
});

test('manifest whitespace and object key order do not change comparison evidence', t => {
  const f = pair(t), original = api._compareArtifacts(f.left, f.right, f.options.publisherPin);
  const filename = path.join(f.right, 'bundle-manifest.json'), manifest = JSON.parse(fs.readFileSync(filename));
  manifest.files = manifest.files.map(row => ({ sha256: row.sha256, size: row.size, path: row.path }));
  manifest.buildReceipts = manifest.buildReceipts.map(row => ({ sha256: row.sha256, component: row.component }));
  fs.writeFileSync(filename, JSON.stringify(Object.fromEntries(Object.entries(manifest).reverse())));
  assert.deepEqual(api._compareArtifacts(f.left, f.right, f.options.publisherPin), original);
});

test('comparison rechecks the first bundle after observing the second', t => {
  const f = pair(t), original = fs.readdirSync; let changed = false;
  fs.readdirSync = (...args) => {
    if (args[0] === f.right && !changed) {
      changed = true; fs.writeFileSync(path.join(f.left, 'location-bridge.jar'), 'changed during comparison');
    }
    return original(...args);
  };
  try { assert.throws(() => api._compareArtifacts(f.left, f.right, f.options.publisherPin)); }
  finally { fs.readdirSync = original; }
  assert.equal(changed, true);
});

test('production comparison rejects fixture publisher pins and CLI overrides without leaking paths', t => {
  const f = pair(t);
  assert.throws(() => api.compareArtifacts(f.left, f.right));
  for (const args of [['compare', f.left, f.right], ['compare', f.left, f.right, '--publisher-pin', 'private-sentinel']]) {
    const result = spawnSync(process.execPath, [path.join(__dirname, 'artifact-build.js'), ...args], { encoding: 'utf8', timeout: 10000 });
    assert.equal(result.status, 1); assert.equal(result.stdout, '');
    assert.equal(result.stderr.trim(), 'Artifact build rejected; partial output is not installable');
  }
});

test('byte-identical replacement of a compared directory is not the original observation', t => {
  const f = pair(t), original = fs.readdirSync; let changed = false;
  fs.readdirSync = (...args) => {
    if (args[0] === f.right && !changed) {
      changed = true;
      const moved = path.join(f.root, 'previous-artifacts');
      fs.renameSync(f.left, moved); fs.cpSync(moved, f.left, { recursive: true });
    }
    return original(...args);
  };
  try { assert.throws(() => api._compareArtifacts(f.left, f.right, f.options.publisherPin), /directory changed/); }
  finally { fs.readdirSync = original; }
  assert.equal(changed, true);
});
test('invalid config and publisher bytes fail before any build or output', t => {
  const f = fixture(t);
  for (const input of [{ ...f.inputs, password: 'generated' }, { ...f.inputs, apiRepo: '../relative' },
    { ...f.inputs, androidJar: '' }, { ...f.inputs, d8Jar: null }]) {
    assert.throws(() => api._buildArtifacts(input, f.output, f.options));
  }
  fs.writeFileSync(f.inputs.scrcpyServer, 'bad');
  assert.throws(() => api._buildArtifacts(f.inputs, f.output, f.options));
  assert.equal(f.calls.length, 0); assert(!fs.existsSync(f.output));
});
test('a failed builder stops the sequence and publishes no artifact component', t => {
  const f = fixture(t); f.options.builders.api = () => { throw Error('generated failure'); };
  assert.throws(() => api._buildArtifacts(f.inputs, f.output, f.options));
  assert.deepEqual(f.calls.map(c => c.role), ['touch']);
  assert(!fs.existsSync(path.join(f.output, 'artifacts')));
});
test('false test evidence and mismatched artifact bytes cannot be collected', t => {
  for (const corrupt of [r => { r.nativeTestsPassed = false; }, r => { r.artifact.sha256 = '0'.repeat(64); },
    r => { r.artifact.name = '../outside'; r.artifact.path = '../outside'; }]) {
    const f = fixture(t), original = f.options.builders.touch;
    f.options.builders.touch = input => { const r = original(input); corrupt(r); return r; };
    assert.throws(() => api._buildArtifacts(f.inputs, f.output, f.options));
    assert(!fs.existsSync(path.join(f.output, 'artifacts/bundle-manifest.json')));
  }
});
test('verifier rejects changed, extra, linked and forged evidence', t => {
  const f = fixture(t); api._buildArtifacts(f.inputs, f.output, f.options);
  const root = path.join(f.output, 'artifacts'), manifest = path.join(root, 'bundle-manifest.json');
  const original = fs.readFileSync(manifest), value = JSON.parse(original);
  for (const mutate of [m => { m.installable = true; }, m => { m.servicesActivated = true; },
    m => { m.files[0].path = '../outside'; }, m => { m.buildReceipts.pop(); }]) {
    const copy = structuredClone(value); mutate(copy); fs.writeFileSync(manifest, JSON.stringify(copy));
    assert.throws(() => api._verifyArtifacts(root, f.options.publisherPin));
  }
  fs.writeFileSync(manifest, original);
  const file = path.join(root, 'location-bridge.jar'), bytes = fs.readFileSync(file);
  fs.writeFileSync(file, 'bad'); assert.throws(() => api._verifyArtifacts(root, f.options.publisherPin));
  fs.writeFileSync(file, bytes); fs.writeFileSync(path.join(root, 'secret.pem'), 'generated');
  assert.throws(() => api._verifyArtifacts(root, f.options.publisherPin)); fs.unlinkSync(path.join(root, 'secret.pem'));
  fs.linkSync(file, path.join(f.root, 'linked')); assert.throws(() => api._verifyArtifacts(root, f.options.publisherPin));
});
test('production publisher pin cannot be replaced by a fixture manifest', t => {
  const f = fixture(t); api._buildArtifacts(f.inputs, f.output, f.options);
  assert.throws(() => api.verifyArtifacts(path.join(f.output, 'artifacts')));
});
test('failed manifest sync leaves no verifiable component', t => {
  const f = fixture(t), original = fs.fsyncSync; let calls = 0;
  fs.fsyncSync = fd => {
    if (++calls === 9) throw Object.assign(Error('generated sync failure'), { code: 'EIO' });
    return original(fd);
  };
  try { assert.throws(() => api._buildArtifacts(f.inputs, f.output, f.options)); }
  finally { fs.fsyncSync = original; }
  assert.throws(() => api._verifyArtifacts(path.join(f.output, 'artifacts'), f.options.publisherPin));
  assert(!fs.existsSync(path.join(f.output, 'artifacts/bundle-manifest.json')));
});
test('each component must report its actual required test and no-operation flags', t => {
  const field = { touch: 'nativeTestsPassed', api: 'serverExecuted', display: 'browserPngFallback',
    gps: 'dexReparsePassed', logical: 'androidOperationsExecuted', binder: 'symbolSmokePassed' };
  for (const role of roles) {
    const f = fixture(t), original = f.options.builders[role];
    f.options.builders[role] = options => { const r = original(options); r[field[role]] = !r[field[role]]; return r; };
    assert.throws(() => api._buildArtifacts(f.inputs, f.output, f.options));
    assert(!fs.existsSync(path.join(f.output, 'artifacts')));
  }
});
test('linked source ancestors and existing output are never adopted', t => {
  const f = fixture(t), linked = path.join(f.root, 'linked-directory');
  fs.symlinkSync(f.root, linked, process.platform === 'win32' ? 'junction' : 'dir');
  assert.throws(() => api._buildArtifacts({ ...f.inputs, scrcpyServer: path.join(linked, 'scrcpyServer') }, f.output, f.options));
  fs.mkdirSync(f.output); fs.writeFileSync(path.join(f.output, 'keep'), 'generated sentinel');
  assert.throws(() => api._buildArtifacts(f.inputs, f.output, f.options));
  assert.equal(fs.readFileSync(path.join(f.output, 'keep'), 'utf8'), 'generated sentinel');
  assert.equal(f.calls.length, 0);
});
test('CLI rejects overrides and reports only a fixed error without config values', t => {
  const f = fixture(t), config = path.join(f.root, 'input.json');
  fs.writeFileSync(config, JSON.stringify({ ...f.inputs, password: 'GENERATED-PRIVATE-SENTINEL' }));
  for (const args of [['build', config, f.output], ['build', config, f.output, '--publisher-pin', 'generated'], ['--activate']]) {
    const result = spawnSync(process.execPath, [path.join(__dirname, 'artifact-build.js'), ...args], { encoding: 'utf8', timeout: 10000 });
    assert.equal(result.status, 1); assert.equal(result.stdout, '');
    assert.equal(result.stderr.trim(), 'Artifact build rejected; partial output is not installable');
  }
  assert(!fs.existsSync(f.output));
});
